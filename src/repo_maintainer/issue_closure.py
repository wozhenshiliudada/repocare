"""One explicit completed-Issue close after actual default-branch acceptance.

No code merge, CI dispatch, reopening, model call or ledger reset. The final
remote state is observed evidence, not a cross-system atomic transaction.
"""
from __future__ import annotations

import json
import os
import re

from .issue_acceptance import observe, acceptance_policy, ReadWindow, require, stamp
from .issue_intake import fingerprint, project_issue
from .recovery import process_identity
from .store import now, redact

KIND = 'issue_closure'


def stable_proof(proof):
    """Expected close changes only state/reason/time/actor and a closing event."""
    issue = proof['issue']
    return {**proof, 'issue': {'meaning': {k: v for k, v in issue['issue'].items() if k not in {'state', 'updated_at'}},
                               'head_sha': issue['head_sha'], 'readiness': issue['readiness']}}


class IssueClosure:
    def __init__(self, delivery, remote, policy):
        require(policy == acceptance_policy(policy['workflow_ids'], policy['events']), 'Trusted acceptance policy required')
        self.delivery, self.registry, self.remote, self.policy = delivery, delivery.registry, remote, policy

    def _get(self, identity, db):
        row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (KIND, identity)).fetchone()
        require(row, 'Unknown Issue closure')
        return json.loads(row[0])

    def view(self, identity):
        with self.registry.transaction() as db:
            value = self._get(identity, db)
        return {**value, 'challenge': fingerprint(value)}

    def prepare(self, identity):
        require(self.registry.path.stat().st_size < 28*1024*1024, 'Original registry near capacity; retain history without a new close')
        proof = observe(self.delivery, self.remote, identity, self.policy)
        require(proof['issue']['issue']['state'] == 'open', 'Issue is already closed; never claim or reopen an external close')
        core = {'operation': 'close_completed_issue_only', 'proof': proof, 'acceptance_policy': self.policy,
                'payload': {'state': 'closed', 'state_reason': 'completed'},
                'race_semantics': 'preflight_and_postflight_observations_not_atomic'}
        require(len(json.dumps(core, ensure_ascii=False).encode()) <= 512*1024, 'Acceptance proposal exceeds evidence bound')
        with self.registry.transaction() as db:
            old = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (KIND, identity)).fetchone()
            if old:
                require(json.loads(old[0])['core'] == core, 'Closure proposal changed; preserve its existing version/intent')
            else:
                require(db.execute('SELECT count(*) FROM objects WHERE kind=?', (KIND,)).fetchone()[0] < 100, 'Closure registry capacity reached')
                self.registry.put(KIND, identity, {'id': identity, 'revision': 0, 'status': 'prepared', 'core': core,
                    'approval_sha256': fingerprint(core), 'created_at': now()}, db)
        return self.view(identity)

    def _finish(self, identity, raw):
        row = self.view(identity)
        approved = row['core']['proof']
        b = approved['binding']
        issue = project_issue(raw, b['repository'], b['issue_number'])
        require(issue.get('state') == 'closed' and issue.get('id') == approved['issue']['issue']['id'],
                'No matching closed Issue receipt; retain unknown intent')
        closing_actor = raw.get('closed_by') or {}
        receipt = {'issue_id': issue['id'], 'number': issue['number'], 'state': 'closed',
                   'state_reason': raw.get('state_reason'), 'closed_at': raw.get('closed_at'),
                   'updated_at': issue['updated_at'], 'raw_text_sha256': issue['raw_text_sha256'],
                   'closed_by': {'id': closing_actor.get('id'), 'login': str(closing_actor.get('login', ''))[:100]},
                   'response_sha256': fingerprint(raw)}
        try:
            proof = observe(self.delivery, self.remote, identity, self.policy)
            current = proof['issue']
            require(stable_proof(proof) == stable_proof(approved), 'Delivery/acceptance evidence changed after the close intent')
            require(current['issue'] == issue and current['state_reason'] == 'completed'
                    and current['closed_by'] == approved['github_actor']
                    and current['closed_at'] == stamp(raw.get('closed_at'))
                    and current['closed_at'] >= approved['pr']['merged_at']
                    and current['issue']['updated_at'] == current['closed_at'],
                    'Close was cancelled/changed/reopened or performed by another account; manual review required')
            status, error = 'closed-verified', None
        except Exception as caught:
            proof = None
            status, error = 'closed-review-required', redact(type(caught).__name__+': '+str(caught))
        with self.registry.transaction() as db:
            current = self._get(identity, db)
            require(current['status'] in {'close_intent', 'close_unknown'}, 'Closure advanced concurrently')
            if status == 'closed-verified':
                execution = self.delivery.execution
                original = execution._load('issue_execution', identity, db)
                head = execution._load('issue_execution_head', original['issue_key'], db)
                require(original['status'] == 'awaiting-review' and head and head['execution_id'] == identity,
                        'Original execution claim changed while closing')
                original.update(status='delivered', issue_closure_id=identity, delivery_pr=approved['pr']['url'],
                                closed_at=receipt['closed_at'], revision=original['revision']+1)
                self.registry.put('issue_execution', identity, original, db)
                run = execution._load('run', b['run_id'], db) or {'run_id': b['run_id']}
                self.registry.put('run', b['run_id'], {**run, 'issue_closure_id': identity,
                                                     'delivery_pr': approved['pr']['url']}, db)
            current.update(status=status, receipt=receipt, review_required_reason=error,
                           verified_proof_sha256=fingerprint(proof) if proof else None,
                           owner_process=None, revision=current['revision']+1, observed_at=now())
            self.registry.put(KIND, identity, current, db)
        return self.view(identity)

    def close(self, identity, challenge, *, approve_sha256, actor):
        require(isinstance(actor, str) and re.fullmatch('[A-Za-z0-9_.@-]{1,100}', actor), 'Explicit closure approval actor required')
        row = self.view(identity)
        require(row['status'] == 'prepared' and row['challenge'] == challenge, 'Refresh prepared closure; never replay an intent')
        require(approve_sha256 == row['approval_sha256'] == fingerprint(row['core']), 'Exact completed-Issue close approval required')
        require(self.policy == row['core']['acceptance_policy'], 'Acceptance rules changed')
        proof = observe(self.delivery, self.remote, identity, self.policy)
        require(proof == row['core']['proof'] and proof['issue']['issue']['state'] == 'open',
                'Issue/PR/default branch/CI/account changed after approval proposal')
        owner = process_identity(os.getpid())
        require(owner, 'Closure owner process identity unavailable')
        with self.registry.transaction() as db:
            current = self._get(identity, db)
            require(fingerprint(current) == challenge and current['status'] == 'prepared', 'Another closer advanced this intent')
            current.update(status='close_intent', owner_process=owner, revision=current['revision']+1,
                           authorization={'actor': actor, 'approval_sha256': approve_sha256, 'time': now()})
            self.registry.put(KIND, identity, current, db)
        try:
            b = proof['binding']
            raw = self.remote.close_completed(b['repository'], b['issue_number'], proof['github_actor'], proof['issue']['issue'])
            return self._finish(identity, raw)
        except BaseException:
            with self.registry.transaction() as db:
                current = self._get(identity, db)
                if current['status'] == 'close_intent':
                    current.update(status='close_unknown', owner_process=None, revision=current['revision']+1,
                                   error='Close outcome unknown; observe only, never repeat PATCH or reopen')
                    self.registry.put(KIND, identity, current, db)
            raise

    def reconcile(self, identity):
        row = self.view(identity)
        require(row['status'] in {'close_intent', 'close_unknown'}, 'Only an unresolved close intent can reconcile')
        owner = row.get('owner_process')
        require(not owner or process_identity(owner['pid']) != owner, 'Original Issue closer is still alive')
        require(row.get('authorization', {}).get('approval_sha256') == fingerprint(row['core']) == row['approval_sha256'],
                'Original closure authorization changed')
        require(row['core']['acceptance_policy'] == self.policy, 'Acceptance policy changed')
        b = row['core']['proof']['binding']
        raw = ReadWindow(self.remote).api('/repos/'+b['repository']+'/issues/'+str(b['issue_number']))
        return self._finish(identity, raw)
