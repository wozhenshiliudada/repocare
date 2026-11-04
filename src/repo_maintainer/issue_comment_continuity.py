"""Timestamp continuity only for a verified, explicitly approved own comment.

Original Issue tokens/claims/Native/approvals are never rewritten. Each witness
links exact before/after bindings in the original journal. Any unrelated change
retains the ordinary IssueReader token and therefore fails original guards.
"""
import copy
import json
import threading
import weakref
from contextlib import nullcontext

from .issue_intake import fingerprint, STAMP
from .models import SafetyError

KIND = 'issue_comment_continuity'
KEYS = ('repository', 'repository_id', 'issue', 'head_sha', 'readiness_events', 'events_complete', 'spec_sha256')
_locks, _lock = weakref.WeakKeyDictionary(), threading.Lock()


def require(ok, message):
    if not ok:
        raise SafetyError(message)


def guard(registry):
    if registry is None:
        return nullcontext()
    with _lock:
        return _locks.setdefault(registry, threading.RLock())


def binding(snapshot):
    require(all(key in snapshot for key in KEYS), 'Complete original Issue binding required')
    return {key: copy.deepcopy(snapshot[key]) for key in KEYS}


def at_timestamp(before, stamp):
    result = copy.deepcopy(before)
    result['issue']['updated_at'] = stamp
    return result


def normalize(snapshot, registry):
    if registry is None or 'token' not in snapshot:
        return snapshot
    with registry.transaction() as db:
        witnesses = db.execute('SELECT id,body FROM objects WHERE kind=?', (KIND,)).fetchall()
        require(len(witnesses) <= 100, 'Comment continuity history exceeds its bound')
        records = {}
        for identity, body in witnesses:
            witness = json.loads(body)
            row = db.execute("SELECT body FROM objects WHERE kind='issue_progress' AND id=?", (identity,)).fetchone()
            require(row, 'Comment continuity has no original intent')
            progress = json.loads(row[0])
            before = progress.get('continuity_before')
            core = {k: v for k, v in witness.items() if k != 'sha256'}
            require(witness.get('sha256') == fingerprint(core)
                    and witness.get('progress_id') == progress.get('id') == identity
                    and progress.get('status') in {'posted', 'posted-review-required'}
                    and progress.get('authorization_sha256') == progress.get('approval_sha256') == fingerprint(progress['core'])
                    and witness.get('approval_sha256') == progress['approval_sha256']
                    and witness.get('before_sha256') == fingerprint(before)
                    and witness.get('comment_id') == progress.get('receipt', {}).get('comment_id')
                    and witness.get('actor_id') == progress.get('actor_id')
                    and witness.get('body_sha256') == fingerprint(progress['core']['body'])
                    and before['repository'] == progress['core']['repository']
                    and before['issue']['number'] == progress['core']['issue_number']
                    and witness.get('after_sha256') == fingerprint(at_timestamp(before, witness['after_updated_at'])),
                    'Original comment continuity evidence changed')
            records[identity] = (witness, before)
    current, visited = binding(snapshot), set()
    for _ in range(len(records)+1):
        matches = [(identity, w, b) for identity, (w, b) in records.items()
                   if w['after_sha256'] == fingerprint(current)]
        if not matches:
            return {**snapshot, **current, 'token': fingerprint(current)}
        require(len(matches) == 1 and matches[0][0] not in visited, 'Ambiguous comment continuity chain')
        identity, witness, current = matches[0]
        visited.add(identity)
    raise SafetyError('Cyclic comment continuity chain')


class CommentContinuity:
    def __init__(self, registry, connection, spec):
        self.registry, self.connection, self.spec = registry, connection, spec

    def guard(self):
        return guard(self.registry)

    def capture(self, number):
        from .issue_intake import IssueReader
        return binding(IssueReader(self.connection, self.spec, registry=self.registry).raw_snapshot(number))

    def proof(self, progress, comment):
        before = progress['continuity_before']
        after = self.capture(progress['core']['issue_number'])
        require(at_timestamp(before, after['issue']['updated_at']) == after,
                'Issue meaning, status, trust, target or scope changed after comment')
        if before == after:
            return None  # No token-changing timestamp, no witness required.
        created, updated = comment.get('created_at'), comment.get('updated_at')
        require(isinstance(created, str) and STAMP.fullmatch(created)
                and updated == created == after['issue']['updated_at']
                and created > before['issue']['updated_at'], 'Comment-only timestamp change cannot be verified')
        core = {'progress_id': progress['id'], 'before_sha256': fingerprint(before), 'after_sha256': fingerprint(after),
                'after_updated_at': updated, 'comment_id': comment['id'], 'actor_id': progress['actor_id'],
                'body_sha256': fingerprint(progress['core']['body']), 'approval_sha256': progress['approval_sha256']}
        return {**core, 'sha256': fingerprint(core)}
