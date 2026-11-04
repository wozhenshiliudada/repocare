"""One approved create-only remote branch attempt for an original Issue execution."""
from __future__ import annotations

import json
import os
import re

from .github_branch import GitHubBranchTransport
from .github_connection import github_url, branch_name
from .issue_intake import fingerprint
from .models import SafetyError
from .recovery import process_identity, unlinked
from .store import now, redact

KIND = 'issue_publication'


def require(condition, message):
    if not condition:
        raise SafetyError(message)


class CandidatePublication:
    """Shared create-only branch transport; origin authority lives in delivery."""
    kind = None
    def __init__(self, delivery, transport=None):
        self.delivery, self.registry = delivery, delivery.registry
        require(self.kind in {'issue_publication','monitor_publication'}, 'Unknown publication origin')
        self.transport = transport if transport is not None else GitHubBranchTransport()

    def _get(self, identity, db):
        row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (self.kind, identity)).fetchone()
        require(row, 'Unknown remote publication')
        return json.loads(row[0])

    def view(self, identity):
        with self.registry.transaction() as db:
            row = self._get(identity, db)
        return {**row, 'challenge': fingerprint(row)}

    def _remote(self, binding):
        meta = self.delivery.remote.api('/repos/'+binding['repository'])
        require(isinstance(meta, dict) and meta.get('id') == binding['repository_id']
                and str(meta.get('full_name', '')).casefold() == binding['repository'],
                'Remote repository identity changed')
        default = branch_name(meta.get('default_branch'))
        require(binding['head'] != default, 'Publication may never target the default branch')
        refs = self.transport.refs(binding['repository'], binding['base'], binding['head'], self.registry.root)
        return default, refs

    def _core(self, binding, default):
        _, url = github_url('https://github.com/'+binding['repository'])
        objects, directory = self.delivery._publication_paths(binding)
        return {'binding': binding, 'url': url, 'default_branch': default,
                'operation': 'create_remote_branch_only', 'expected_old_sha': None,
                'remote_ref': 'refs/heads/'+binding['head'], 'objects': str(objects),
                'carrier_directory': str(directory), 'transport': 'sterile_git_gcm_v1',
                'merge_authorized': False, 'pr_authorized': False, 'issue_close_authorized': False}

    def prepare(self, identity):
        _, binding = self.delivery._local(identity)
        self.delivery._fresh(binding)
        default, refs = self._remote(binding)
        require(refs['base'] == binding['base_sha'] and refs['head'] is None,
                'Base changed or target already exists; do not overwrite/adopt it')
        core = self._core(binding, default)
        _, directory = self.delivery._publication_paths(binding)
        with self.registry.transaction() as db:
            old = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (self.kind, identity)).fetchone()
            if old:
                require(json.loads(old[0])['core'] == core, 'Existing publication differs; retain its intent')
            else:
                require(not directory.exists(), 'Unrecorded publication carrier exists; retain it')
                require(db.execute('SELECT count(*) FROM objects WHERE kind=?', (self.kind,)).fetchone()[0] < 100,
                        'Publication registry capacity reached')
                self.registry.put(self.kind, identity, {'id': identity, 'revision': 0, 'status': 'prepared',
                    'core': core, 'approval_sha256': fingerprint(core), 'created_at': now(), 'process_events': []}, db)
        return self.view(identity)

    def _event(self, identity, stage, detail):
        require(stage in {s+'_'+p for s in ('init', 'push') for p in ('starting', 'running', 'done')}, 'Unexpected Git phase')
        with self.registry.transaction() as db:
            row = self._get(identity, db)
            require(row['status'] == 'push_intent' and len(row['process_events']) < 6, 'Publication process journal changed')
            row['process_events'].append({'stage': stage, 'time': now(), 'detail': detail})
            row.update(stage=stage, revision=row['revision']+1)
            if stage.endswith('_starting'):
                row['child_process'] = None
            elif stage.endswith('_running'):
                row['child_process'] = None if detail.get('completed_before_identity') else detail
            else:
                row['child_process'] = None
            self.registry.put(self.kind, identity, row, db)

    def _finish(self, identity, refs, default):
        row = self.view(identity)
        binding = row['core']['binding']
        require(refs.get('head') == binding['commit'], 'Remote result is absent or changed; retain unknown intent')
        try:
            require(default == row['core']['default_branch'] and refs['base'] == binding['base_sha'], 'Remote target changed after push')
            self.delivery._fresh(binding)
            _, current = self.delivery._local(identity)
            require(current == binding, 'Local publication changed during push')
            status, reason = 'remote-ref-verified', None
        except Exception as error:
            status, reason = 'remote-ref-review-required', redact(type(error).__name__+': '+str(error))
        receipt = {'repository_id': binding['repository_id'], 'repository': binding['repository'],
                   'ref': row['core']['remote_ref'], 'sha': refs['head'], 'base_sha_observed': refs['base'],
                   'intent_sha256': row['approval_sha256']}
        with self.registry.transaction() as db:
            current = self._get(identity, db)
            require(current['status'] in {'push_intent', 'push_unknown'}, 'Publication advanced concurrently')
            current.update(receipt=receipt, status=status, review_required_reason=reason,
                           revision=current['revision']+1, owner_process=None, observed_at=now())
            self.registry.put(self.kind, identity, current, db)
        return self.view(identity)

    def push(self, identity, challenge, *, approve_sha256, actor):
        require(isinstance(actor, str) and re.fullmatch('[A-Za-z0-9_.@-]{1,100}', actor), 'Explicit publication actor required')
        row = self.view(identity)
        require(row['challenge'] == challenge and row['status'] == 'prepared', 'Refresh prepared publication; never replay intent')
        require(approve_sha256 == row['approval_sha256'] == fingerprint(row['core']), 'Exact remote branch approval required')
        require(row['core'] == self._core(row['core']['binding'], row['core']['default_branch']), 'Publication transport/path scope changed')
        _, binding = self.delivery._local(identity)
        require(binding == row['core']['binding'], 'Original publication binding changed')
        self.delivery._fresh(binding)
        default, refs = self._remote(binding)
        require(default == row['core']['default_branch'] and refs['base'] == binding['base_sha'] and refs['head'] is None,
                'Remote base/default changed or branch already exists')
        _, current = self.delivery._local(identity)
        require(current == binding, 'Original publication changed during preflight')
        self.delivery._fresh(binding)
        owner = process_identity(os.getpid())
        require(owner, 'Publisher process identity unavailable')
        with self.registry.transaction() as db:
            current = self._get(identity, db)
            require(fingerprint(current) == challenge and current['status'] == 'prepared', 'Another publisher advanced intent')
            current.update(status='push_intent', owner_process=owner, revision=current['revision']+1,
                           authorization={'actor': actor, 'approval_sha256': approve_sha256, 'time': now()})
            self.registry.put(self.kind, identity, current, db)
        try:
            self.transport.push(binding['repository'], binding['base'], binding['head'], binding['commit'],
                                row['core']['objects'], row['core']['carrier_directory'],
                                lambda stage, detail: self._event(identity, stage, detail))
            default, refs = self._remote(binding)
            return self._finish(identity, refs, default)
        except BaseException:
            with self.registry.transaction() as db:
                current = self._get(identity, db)
                if current['status'] == 'push_intent':
                    current.update(status='push_unknown', revision=current['revision']+1, owner_process=None,
                                   error='Push outcome unknown; retain carrier and reconcile without another push')
                    self.registry.put(self.kind, identity, current, db)
            raise

    def reconcile(self, identity):
        row = self.view(identity)
        require(row['status'] in {'push_intent', 'push_unknown'}, 'Only an unresolved recorded push can reconcile')
        owner, child = row.get('owner_process'), row.get('child_process')
        require(not owner or process_identity(owner['pid']) != owner, 'Original publisher is still alive')
        require(not row.get('stage', '').endswith('_starting'), 'Interrupted PID launch gap requires manual process verification')
        require(not child or process_identity(child['pid']) != child, 'Original Git child is still alive')
        require(row.get('stage') != 'push_running', 'Interrupted transport has no completion receipt; manually verify Git helpers stopped')
        require(row.get('authorization', {}).get('approval_sha256') == fingerprint(row['core']) == row['approval_sha256'],
                'Original publication approval changed')
        require(row['core'] == self._core(row['core']['binding'], row['core']['default_branch']), 'Publication transport/path scope changed')
        default, refs = self._remote(row['core']['binding'])
        return self._finish(identity, refs, default)


class IssuePublication(CandidatePublication):
    kind = KIND
