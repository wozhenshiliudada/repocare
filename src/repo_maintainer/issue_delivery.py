"""Explicit draft-PR delivery in the original Issue execution registry.

An exact remote head must already exist. This slice does not push, merge, close
Issues, mutate labels, reset model budgets, or grant execution from Issue text.
"""
from __future__ import annotations

import json
import os
import re

from .github_connection import github_url, branch_name
from .integration import verified_publication
from .issue_intake import IssueReader, fingerprint
from .maintenance_rules import validate_rules
from .models import SafetyError
from .recovery import process_identity
from .store import now, redact

KIND = 'issue_delivery'


def require(condition, message):
    if not condition:
        raise SafetyError(message)


class CandidateDelivery:
    """Shared single-intent PR algorithm; concrete origins supply their own authority."""
    kind = None

    def __init__(self, registry, remote):
        require(self.kind in {'issue_delivery','monitor_delivery'}, 'Unknown delivery origin')
        self.registry, self.remote = registry, remote

    def _local(self, identity):
        raise NotImplementedError

    def _fresh(self, binding):
        raise NotImplementedError

    def _payload(self, identity, binding):
        raise NotImplementedError

    def _publication_paths(self, binding):
        raise NotImplementedError

    def _get(self, identity, db):
        row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (self.kind, identity)).fetchone()
        require(row, 'Unknown Issue delivery')
        return json.loads(row[0])


    def view(self, identity):
        with self.registry.transaction() as db:
            row = self._get(identity, db)
        return {**row, 'challenge': fingerprint(row)}


    def _remote_heads(self, binding):
        slug = binding['repository']
        meta = self.remote.api('/repos/'+slug)
        require(isinstance(meta, dict) and meta.get('id') == binding['repository_id']
                and str(meta.get('full_name', '')).casefold() == slug, 'Remote repository identity differs')
        default = branch_name(meta.get('default_branch'))
        url = 'https://github.com/'+slug
        require(self.remote.branch_head(url, binding['base']) == binding['base_sha'], 'Remote base advanced')
        require(self.remote.branch_head(url, binding['head']) == binding['commit'],
                'Exact approved branch must already exist remotely; this API never pushes')
        return default


    def prepare(self, execution_id):
        _, binding = self._local(execution_id)
        self._fresh(binding)
        default = self._remote_heads(binding)
        require(not self.remote.pulls_for_head(binding['repository'], binding['head']),
                'PR already exists for this head; never adopt/reopen without a recorded intent')
        payload = self._payload(execution_id, binding)
        core = {'binding': binding, 'payload': payload, 'default_branch': default,
                'operation': 'create_draft_pr_only', 'issue_close_authorized': False}
        identity = execution_id  # One original execution, at most one creation intent.
        with self.registry.transaction() as db:
            old = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (self.kind, identity)).fetchone()
            if old:
                require(json.loads(old[0])['core'] == core, 'Existing delivery differs; retain it without replay')
            else:
                require(db.execute('SELECT count(*) FROM objects WHERE kind=?', (self.kind,)).fetchone()[0] < 100,
                        'Delivery registry capacity reached')
                self.registry.put(self.kind, identity, {'id': identity, 'revision': 0, 'status': 'prepared',
                    'created_at': now(), 'core': core, 'approval_sha256': fingerprint(core)}, db)
        return self.view(identity)


    def _receipt(self, value, core):
        binding, payload = core['binding'], core['payload']
        require(isinstance(value, dict), 'PR response is not an object')
        number = value.get('number')
        require(type(number) is int and 1 <= number <= 9999999999
                and type(value.get('id')) is int and value['id'] > 0
                and isinstance(value.get('html_url'), str)
                and value['html_url'].casefold() == f"https://github.com/{binding['repository']}/pull/{number}".casefold()
                and value.get('title') == payload['title'] and value.get('body') == payload['body']
                and value.get('draft') is True and value.get('state') in {'open', 'closed'},
                'PR identity/payload changed; retain intent without replay')
        for side, sha in (('head', binding['commit']), ('base', binding['base_sha'])):
            tip = value.get(side) or {}
            repo = tip.get('repo') or {}
            require(tip.get('ref') == payload[side] and tip.get('sha') == sha
                    and repo.get('id') == binding['repository_id']
                    and str(repo.get('full_name', '')).casefold() == binding['repository'],
                    'PR repository/ref/SHA differs; retain for human inspection')
        return {'id': value['id'], 'number': number, 'url': value['html_url'],
                'state': value['state'], 'draft': True, 'response_sha256': fingerprint(value),
                'merged_at': value.get('merged_at'), 'head_sha': binding['commit'],
                'base_sha': binding['base_sha'], 'payload_sha256': fingerprint(payload)}


    def _finish(self, identity, value):
        row = self.view(identity)
        receipt = self._receipt(value, row['core'])
        # PR creation itself may change Issue.updated_at. Do not pretend that a
        # changed token is harmless or reuse it as a new repair authorization.
        try:
            self._fresh(row['core']['binding'])
            _, local = self._local(identity)
            require(local == row['core']['binding'], 'Local publication changed during remote operation')
            status = 'awaiting-review' if receipt['state'] == 'open' and not receipt['merged_at'] else 'remote-closed-review'
            reason = None
        except Exception as error:
            status = 'remote-created-review-required'
            reason = redact(type(error).__name__+': '+str(error))
        with self.registry.transaction() as db:
            current = self._get(identity, db)
            require(current['status'] in {'create_intent', 'creation_unknown'}, 'Delivery advanced concurrently')
            current.update(receipt=receipt, status=status, owner_process=None,
                           revision=current['revision']+1, observed_at=now(), review_required_reason=reason)
            self.registry.put(self.kind, identity, current, db)
        return self.view(identity)


    def create(self, identity, challenge, *, approve_sha256, actor):
        require(isinstance(actor, str) and re.fullmatch(r'[A-Za-z0-9_.@-]{1,100}', actor), 'Explicit approval actor required')
        row = self.view(identity)
        require(row['challenge'] == challenge and row['status'] == 'prepared', 'Refresh prepared delivery; never replay an intent')
        require(approve_sha256 == row['approval_sha256'] == fingerprint(row['core']), 'Exact PR payload approval required')
        _, binding = self._local(identity)
        require(binding == row['core']['binding'], 'Local delivery binding changed')
        self._fresh(binding)
        require(self._remote_heads(binding) == row['core']['default_branch'], 'Default branch changed')
        require(not self.remote.pulls_for_head(binding['repository'], binding['head']), 'Existing PR blocks creation')
        _, local = self._local(identity)
        require(local == binding, 'Local delivery changed during remote preflight')
        self._fresh(binding)  # Human edit/closure during preflight is not authorization.
        owner = process_identity(os.getpid())
        require(owner, 'Controller process identity unavailable')
        with self.registry.transaction() as db:
            current = self._get(identity, db)
            require(fingerprint(current) == challenge and current['status'] == 'prepared', 'Another worker advanced delivery')
            current.update(status='create_intent', revision=current['revision']+1, owner_process=owner,
                           authorization={'actor': actor, 'approval_sha256': approve_sha256, 'time': now()})
            self.registry.put(self.kind, identity, current, db)  # COMMIT before the only POST.
        try:
            value = self.remote.create_draft(binding['repository'], row['core']['payload'])
            return self._finish(identity, value)
        except BaseException:
            with self.registry.transaction() as db:
                current = self._get(identity, db)
                if current['status'] == 'create_intent':
                    current.update(status='creation_unknown', owner_process=None, revision=current['revision']+1,
                                   error='PR write outcome unknown; read-only reconciliation required')
                    self.registry.put(self.kind, identity, current, db)
            raise


    def reconcile(self, identity):
        row = self.view(identity)
        require(row['status'] in {'create_intent', 'creation_unknown'}, 'Only an unresolved recorded intent can reconcile')
        owner = row.get('owner_process')
        require(not owner or process_identity(owner['pid']) != owner, 'Original PR creator is still alive')
        require(row.get('authorization', {}).get('approval_sha256') == fingerprint(row['core']) == row['approval_sha256'],
                'Original PR authorization changed')
        binding = row['core']['binding']
        values = self.remote.pulls_for_head(binding['repository'], binding['head'])
        require(len(values) == 1, 'No unique PR found; preserve unknown intent, never repeat creation')
        return self._finish(identity, values[0])



class IssueDelivery(CandidateDelivery):
    kind = KIND

    def __init__(self, execution_service, pull_requests):
        super().__init__(execution_service.registry, pull_requests)
        self.execution = execution_service

    def _local(self, execution_id):
        service = self.execution
        execution = service.view(execution_id)
        require(execution['status'] == 'awaiting-review', 'Execution is not awaiting review')
        validate_rules(service.rules, service.source)
        require(execution['rules_sha256'] == service.rules['rules_sha256']
                and execution['spec_sha256'] == fingerprint(service.spec), 'Original maintenance scope changed')
        with self.registry.transaction() as db:
            head = service._load('issue_execution_head', execution['issue_key'], db)
            job = service._load('issue_diagnosis', execution['investigation_id'], db)
            observed = service._load('issue_head', execution['issue_key'], db)
        require(head and head['execution_id'] == execution_id and job
                and job['snapshot']['token'] == execution['issue_token'] and observed
                and observed['token'] == execution['issue_token'], 'Original Issue claim/version changed')
        proof = verified_publication(service.runs/execution['run_id'])
        record, review = proof['record'], proof['review']
        expected = {'execution_id': execution_id, 'issue_number': execution['issue_number'],
                    'issue_token': execution['issue_token'], 'task_id': execution['task_id'],
                    'rules_sha256': execution['rules_sha256'],
                    'policy_binding_sha256': execution['policy_binding']['binding_sha256']}
        require(record.get('issue_execution') == expected, 'Original run/Issue reverse binding differs')
        require(record['source_repo'] == str(service.source.root)
                and record['base_sha'] == job['snapshot']['head_sha'], 'Run/source/baseline differs')
        origin = service.source.command('config', '--get', 'remote.origin.url').decode().strip()
        require(github_url(origin)[0].casefold() == service.spec['repository'], 'Source origin changed')
        binding = {**expected, 'repository': service.spec['repository'],
                   'repository_id': execution['repository_id'], 'run_id': execution['run_id'],
                   'base_sha': record['base_sha'], 'commit': review['commit'],
                   'head': review['target_branch'], 'base': service.spec['branch'],
                   'review_sha256': proof['review_sha256'], 'diff_sha256': review['diff_sha256'],
                   'policy_sha256': fingerprint(record['policy']),
                   'candidate_tests_sha256': fingerprint(record['candidate_tests']),
                   'sealed_tests_sha256': fingerprint(review['tests']),
                   'test_count': len(review['tests']),
                   'environment_sha256': fingerprint(record.get('environment'))}
        return execution, binding


    def _fresh(self, binding):
        service = self.execution
        snapshot = IssueReader(service.connection, service.spec, registry=service.registry).snapshot(binding['issue_number'])
        require(snapshot.get('status') == 'ready' and snapshot.get('token') == binding['issue_token'],
                'Issue/readiness/target changed; no remote write or automatic replay')
        return snapshot


    def _payload(self, execution_id, binding):
        # No Issue title/body or model prose enters the outbound payload. In
        # particular, untrusted closing keywords cannot silently close an Issue.
        body = (f"Refs #{binding['issue_number']}\n\n"
                "Verified candidate; awaiting human review. No merge or Issue closure is authorized.\n\n"
                + '\n'.join(f'- {key}: `{binding[key]}`' for key in (
                    'execution_id', 'run_id', 'task_id', 'base_sha', 'commit', 'rules_sha256',
                    'policy_binding_sha256', 'review_sha256', 'candidate_tests_sha256',
                    'sealed_tests_sha256', 'environment_sha256', 'test_count'))
                + f"\n\n<!-- repocare-delivery:{execution_id}:{binding['review_sha256']} -->\n")
        payload = {'title': f"RepoCare: candidate for Issue #{binding['issue_number']}", 'body': body,
                   'head': binding['head'], 'base': binding['base'], 'draft': True}
        return payload

    def _publication_paths(self, binding):
        from .dashboard import unlinked
        return (unlinked(self.execution.source.common_dir/'objects'),
                unlinked(self.execution.runs/binding['run_id']/'remote-publication'))
