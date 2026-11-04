"""Small durable UI journal; never replays external model, Git or test operations."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from filelock import FileLock, Timeout

from .dashboard import unlinked
from .models import SafetyError
from .store import now, redact
from .runtime_limits import MAX_REQUESTS
from .models import digest


def provider_binding(provider) -> dict:
    endpoint = urlsplit(getattr(provider, "mock_url", None) or "")
    return {"type": type(provider).__name__, "credential_source": getattr(provider, "credential_source", None),
            "config_file": str(getattr(provider, "config_file", None)),
            "mock_endpoint": [endpoint.scheme, endpoint.hostname, endpoint.port, endpoint.path],
            "request_budget": getattr(provider, "max_requests", 0)}


class UIRegistry:
    def __init__(self, root: Path, binding: dict):
        self.root = unlinked(root / "ui-state")
        had_journal_directory = self.root.exists()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = unlinked(self.root / "registry.sqlite3")
        self.guard = FileLock(str(unlinked(self.root / "owner.lock")), timeout=0)
        try:
            self.guard.acquire()
        except Timeout as error:
            raise SafetyError("Another UI server owns this state directory") from error
        try:
            if self.path.exists() and self.path.stat().st_size > 32 * 1024 * 1024:
                raise SafetyError("UI journal exceeds its size limit")
            existing = self.path.exists()
            if not existing and (had_journal_directory or (root / "ui-sessions").exists()):
                raise SafetyError("Missing durable UI journal or legacy UI history; refuse to reset request consumption")
            with self.transaction() as db:
                if existing:
                    if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                        raise SafetyError("Unsupported UI journal schema; preserve it for inspection")
                    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise SafetyError("UI journal integrity check failed")
                    saved = db.execute("SELECT body FROM metadata").fetchone()
                    if not saved or json.loads(saved[0]) != binding:
                        raise SafetyError("UI journal repository, policy, provider budget or source memory scope changed")
                else:
                    db.execute("CREATE TABLE metadata(body TEXT NOT NULL)")
                    db.execute("INSERT INTO metadata VALUES (?)", (json.dumps(binding, sort_keys=True),))
                    db.execute("CREATE TABLE objects(kind TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY(kind,id))")
                    db.execute("CREATE TABLE budget(id INTEGER PRIMARY KEY CHECK(id=1), ceiling INTEGER, used INTEGER, held INTEGER)")
                    db.execute("INSERT INTO budget VALUES(1,?,0,0)", (binding["provider"]["request_budget"],))
                    db.execute("PRAGMA user_version=1")
                # Intent was durable, outcome was not: keep the request identity and never replay it.
                for identity, body in db.execute("SELECT id,body FROM objects WHERE kind='job'").fetchall():
                    job = json.loads(body)
                    if job["status"] == "running":
                        job.update(status="interrupted", error="Previous operation interrupted; inspect evidence before a new action", finished_at=now())
                        self.put("job", identity, job, db)
                for identity, body in db.execute("SELECT id,body FROM objects WHERE kind='attempt'").fetchall():
                    attempt = json.loads(body)
                    if attempt["status"] == "started":
                        attempt.update(status="unknown", finished_at=now())
                        self.put("attempt", identity, attempt, db)
        except BaseException:
            self.guard.release()
            raise

    @contextmanager
    def transaction(self):
        for name in ("registry.sqlite3", "registry.sqlite3-journal", "registry.sqlite3-wal", "registry.sqlite3-shm"):
            unlinked(self.root / name)
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def close(self):
        self.guard.release()

    def put(self, kind: str, identity: str, value: dict, db=None):
        if db is None:
            with self.transaction() as connection:
                self.put(kind, identity, value, connection)
            return
        body = redact(json.dumps(value, ensure_ascii=False, sort_keys=True))
        db.execute("INSERT INTO objects VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
                   (kind, identity, body))

    def load(self, kind: str) -> dict:
        with self.transaction() as db:
            return {identity: json.loads(body) for identity, body in
                    db.execute("SELECT id,body FROM objects WHERE kind=? ORDER BY rowid", (kind,))}

    def budget(self, *, include_usage=False) -> dict:
        with self.transaction() as db:
            ceiling, used, held = db.execute("SELECT ceiling,used,held FROM budget WHERE id=1").fetchone()
            if (any(type(n) is not int or n < 0 for n in (ceiling, used, held))
                    or used + held > ceiling):
                raise SafetyError("Invalid durable request budget")
            budget = {"request_budget": ceiling, "requests_used": used, "requests_reserved_unknown": held,
                      "requests_remaining": ceiling - used - held}
            if include_usage:
                # The legacy held counter still drives all gates and revisions. A started
                # attempt becomes unknown on settlement without a receipt or on startup
                # recovery; unattributed holds stay unknown in the display as well.
                active = 0
                for (body,) in db.execute("SELECT body FROM objects WHERE kind='attempt'"):
                    attempt = json.loads(body)
                    reserved = attempt.get("reserved")
                    if attempt.get("status") == "started" and type(reserved) is int and reserved > 0:
                        active += reserved
                active = active if active <= held else 0
                budget.update(requests_reserved_active=active, requests_ended_unknown=held-active)
            return budget

    def reserve(self, maximum=None) -> tuple[str, int]:
        if maximum is not None and (type(maximum) is not int or not 1 <= maximum <= MAX_REQUESTS):
            raise SafetyError('Invalid bounded request reservation')
        with self.transaction() as db:
            ceiling, used, held = db.execute("SELECT ceiling,used,held FROM budget WHERE id=1").fetchone()
            remaining = ceiling - used - held
            if remaining <= 0:
                raise SafetyError("Durable model request budget exhausted or reserved by an unknown outcome")
            reserved = min(remaining, maximum) if maximum is not None else remaining
            identity = uuid.uuid4().hex
            self.put("attempt", identity, {"id": identity, "status": "started", "reserved": reserved,
                                         "started_at": now()}, db)
            db.execute("UPDATE budget SET held=held+? WHERE id=1", (reserved,))
            return identity, reserved

    def settle(self, identity: str, evidence: dict, *, success: bool, failure_receipt=None):
        from .pi_evidence import CountedPlanningFailure
        with self.transaction() as db:
            row = db.execute("SELECT body FROM objects WHERE kind='attempt' AND id=?", (identity,)).fetchone()
            attempt = json.loads(row[0])
            if attempt["status"] != "started":
                raise SafetyError("Model attempt was already settled")
            count = evidence.get("requests")
            counted_failure = (not success and type(failure_receipt) is CountedPlanningFailure
                               and failure_receipt.matches(evidence, attempt['reserved']))
            known = (success or counted_failure) and type(count) is int and 0 <= count <= attempt["reserved"]
            attempt.update(status="finished" if known else "unknown", outcome='completed' if success else 'failed',
                           evidence=evidence, finished_at=now())
            if known:
                db.execute("UPDATE budget SET used=used+?,held=held-? WHERE id=1", (count, attempt["reserved"]))
            self.put("attempt", identity, attempt, db)
        if success and not known:
            raise SafetyError("Provider request consumption missing or invalid; keep the reservation")

    def progress(self, identity, evidence):
        from .pi_evidence import validate_evidence
        with self.transaction() as db:
            row = db.execute("SELECT body FROM objects WHERE kind='attempt' AND id=?", (identity,)).fetchone()
            attempt = json.loads(row[0]) if row else {}
            if attempt.get('status') != 'started':
                raise SafetyError('Progress cannot change a settled or foreign attempt')
            value = validate_evidence(evidence, attempt['reserved'])
            if value['status'] != 'running' or value['events_complete'] or value['failure_category'] is not None:
                raise SafetyError('Only incomplete running evidence can update progress')
            previous = attempt.get('progress', {})
            for field in ('requests_started', 'tool_calls'):
                if previous.get(field) is not None and (value.get(field) is None or value[field] < previous[field]):
                    raise SafetyError('Planning counters moved backwards')
            attempt.update(progress=value, progress_at=now())
            self.put('attempt', identity, attempt, db)  # Never settle/refund consumption here.

    def _extension_state(self, db, allowed_job=None):
        binding = json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
        row = db.execute('SELECT ceiling,used,held FROM budget WHERE id=1').fetchone()
        if (any(type(value) is not int or value < 0 for value in row) or row[1]+row[2] > row[0]
                or binding['provider']['request_budget'] != row[0]):
            raise SafetyError('Budget scope is inconsistent')
        known = held = 0
        for body, in db.execute("SELECT body FROM objects WHERE kind='attempt'"):
            attempt = json.loads(body)
            reserved = attempt.get('reserved')
            if type(reserved) is not int or not 0 < reserved <= row[0]:
                raise SafetyError('Invalid historical request reservation')
            if attempt['status'] == 'started':
                raise SafetyError('A model attempt is still active; do not expand its budget')
            if attempt['status'] == 'unknown':
                held += reserved
            elif attempt['status'] == 'finished':
                count = attempt.get('evidence', {}).get('requests')
                if type(count) is not int or not 0 <= count <= reserved:
                    raise SafetyError('Invalid historical request receipt')
                known += count
            else:
                raise SafetyError('Unknown historical request state')
        if (known, held) != row[1:]:
            raise SafetyError('Budget totals disagree with preserved request receipts')
        for identity, body in db.execute("SELECT id,body FROM objects WHERE kind='job'"):
            if json.loads(body)['status'] == 'running' and identity != allowed_job:
                raise SafetyError('Another operation is running')
        return binding, row

    @staticmethod
    def _extension_preview(binding, row, target, session):
        if type(target) is not int or not row[0] < target <= MAX_REQUESTS or row[0] == 0:
            raise SafetyError('New total budget must increase the existing model budget, up to the configured maximum')
        value = {'old_total': row[0], 'new_total': target, 'requests_used': row[1],
                 'requests_reserved_unknown': row[2], 'old_remaining': row[0]-row[1]-row[2],
                 'new_remaining': target-row[1]-row[2], 'added_requests': target-row[0]}
        value['revision'] = digest(json.dumps({'binding':binding, 'preview':value, 'session':session},
                                             sort_keys=True).encode())
        return value

    def preview_extension(self, target, session, allowed_job=None):
        with self.transaction() as db:
            binding, row = self._extension_state(db, allowed_job)
            return self._extension_preview(binding, row, target, session)

    def extend_budget(self, target, revision, session, operation):
        with self.transaction() as db:
            binding, row = self._extension_state(db, operation)
            preview = self._extension_preview(binding, row, target, session)
            if preview['revision'] != revision:
                raise SafetyError('Budget or service session changed; review the new budget again')
            binding['provider']['request_budget'] = target
            db.execute('UPDATE metadata SET body=?', (json.dumps(binding, sort_keys=True),))
            db.execute('UPDATE budget SET ceiling=? WHERE id=1', (target,))
            self.put('budget_change', operation, {**preview, 'id':operation, 'actor':'local-browser-operator',
                      'changed_at':now(), 'used_and_unknown_preserved':True}, db)
        return preview


class DurableProvider:
    """Reserve before invoking Pi; unknown outcomes conservatively retain the full allowance."""

    def __init__(self, provider, registry: UIRegistry):
        self.inner, self.registry = provider, registry
        self.evidence = {}
        self.task_planner = None
        self.before_model = None

    @property
    def requests_used(self):
        return self.registry.budget()["requests_used"]

    @property
    def max_requests(self):
        return self.registry.budget()["request_budget"]

    def plan(self, *args):
        return self._invoke("plan", *args)

    def revise(self, *args):
        return self._invoke("revise", *args)

    def propose_validation(self, *args):
        return self._invoke('propose_validation', *args)

    def reflect(self, *args):
        # Explicit memory-only call; same original consumption ledger, no repair task/checkpoint.
        return self._reserved_invoke('reflect', *args)

    def compact(self, *args):
        return self._reserved_invoke('compact', *args)

    def _invoke(self, method, *args):
        if self.task_planner is not None:
            with self.task_planner.turn(*args[:3], feedback=args[3] if method=='revise' else None) as turn:
                return self._reserved_invoke(method, *args, task_turn=turn)
        return self._reserved_invoke(method, *args)

    def _reserved_invoke(self, method, *args, task_turn=None):
        from .job_control import OperationStopped
        cancel = getattr(self.inner, '_cancel_event', None)
        if cancel is not None and cancel.is_set():
            raise OperationStopped('Planning stopped before model reservation')
        if self.before_model is not None and method in {'plan','revise','propose_validation'}:
            self.before_model()
        if not getattr(self.inner, "max_requests", 0):
            result = getattr(self.inner, method)(*args)
            self.evidence = {**getattr(self.inner, "evidence", {}), "requests": 0,
                             "transport": "deterministic-mock"}
            return result
        identity, allowance = self.registry.reserve()
        # Pi's process receives the remaining allowance, including prior unknown reservations.
        self.inner.requests_used = self.inner.max_requests - allowance
        self.inner.evidence = {}
        previous_callback = getattr(self.inner, '_progress_callback', None)
        previous_load_id = getattr(self.inner,'_load_attempt_id',None)
        if getattr(self.inner,'_memory_catalog',None) is not None and method in {'plan','revise'}:
            self.inner._load_attempt_id = identity
        if previous_callback is not None:
            def progress(value):
                paths = (*getattr(self.inner,'allowed_paths',()), *getattr(self.inner,'readonly_paths',()))
                if any(event.get('path') not in paths for event in value.get('events',[]) if 'path' in event):
                    raise SafetyError('Progress path is outside the current source authorization')
                self.registry.progress(identity, value)
                previous_callback(value)
            self.inner._progress_callback = progress
        try:
            result = getattr(self.inner, method)(*args)
            if task_turn is not None:
                task_turn.validate_sources()  # Necessary sources must pass before settle/commit.
        except BaseException as problem:
            from .pi_evidence import CountedPlanningFailure
            raw = getattr(self.inner, 'evidence', {})
            counted_failure = (type(problem) is CountedPlanningFailure and problem.matches(raw, allowance))
            self.evidence = {**raw, "attempt_id": identity, "reserved_requests": allowance}
            if not counted_failure:
                self.evidence.update(consumption='unknown', requests=None)
                self.evidence.pop('terminal_count', None)
            if task_turn is not None:
                self.evidence['task_id'] = task_turn.record['task_id']
            self.registry.settle(identity, self.evidence, success=False,
                                 failure_receipt=problem if counted_failure else None)
            raise
        finally:
            if getattr(self.inner,'_memory_catalog',None) is not None:
                self.inner._load_attempt_id = previous_load_id
            if previous_callback is not None:
                self.inner._progress_callback = previous_callback
        self.evidence = {**getattr(self.inner, "evidence", {}), "attempt_id": identity}
        if task_turn is not None:
            self.evidence['task_id'] = task_turn.record['task_id']
        self.registry.settle(identity, self.evidence, success=True)
        if task_turn is not None:
            try:
                self.evidence['task_session'] = task_turn.finish(identity)
            except BaseException:
                # Settlement already happened. Never reserve/settle/refund this attempt again.
                self.evidence['checkpoint_status'] = 'blocked_after_settlement'
                raise
        return result
