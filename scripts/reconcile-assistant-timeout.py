"""Offline repair for reviewed, single-request timeouts from the legacy assistant."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import sys
from contextlib import closing, nullcontext

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from filelock import FileLock
from repo_maintainer.dashboard import unlinked
from repo_maintainer.gateway import gateway_status, load_configuration
from repo_maintainer.models import SafetyError

OLD_ERROR = '模型未返回有效会话；保留原账，不自动重试'
TIMEOUT = 'Request timed out.'
LEGACY_IMPLEMENTATION = '49786dca2628f21c3462d7537ab868c88b7ac70b'


def require(condition, reason):
    if not condition:
        raise SafetyError(reason)


def fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def identifier(value):
    require(isinstance(value, str) and re.fullmatch('[a-f0-9]{32}', value), 'Invalid record identifier')
    return value


def stamp(value):
    return datetime.fromisoformat(value)


def object_row(db, schema, kind, identity):
    row = db.execute(f'SELECT body FROM {schema}.objects WHERE kind=? AND id=?', (kind, identity)).fetchone()
    require(row is not None, 'Missing linked record')
    return json.loads(row[0])


def journal(directory):
    state = unlinked(directory / 'ui-state')
    for name in ('registry.sqlite3', 'registry.sqlite3-journal', 'registry.sqlite3-wal', 'registry.sqlite3-shm'):
        unlinked(state / name)
    path = state / 'registry.sqlite3'
    require(path.is_file() and path.stat().st_size <= 32 * 1024**2, 'Missing or oversized journal')
    return path


def native_proof(root, job, session, attempt):
    directory = unlinked(root / 'conversations' / session['id'] / 'sessions')
    files = sorted(directory.glob('*.jsonl'))
    require(len(files) == 1, 'Expected a single Native file for a fresh conversation')
    path = unlinked(files[0])
    require(path.is_file() and path.stat().st_size <= 2 * 1024**2, 'Native evidence unavailable')
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    require(raw.endswith(b'\n') and [row.get('type') for row in rows] ==
            ['session', 'model_change', 'thinking_level_change', 'message', 'message', 'message'],
            'Native trace is not a complete single-request timeout')
    require([row.get('message', {}).get('role') for row in rows[-3:]] == ['system', 'user', 'assistant']
            and rows[1]['parentId'] is None
            and all(row['parentId'] == previous['id'] for previous, row in zip(rows[1:], rows[2:])),
            'Unexpected message roles or ancestry')
    assistant = rows[-1]['message']
    require(assistant['provider'] == 'repocare-assistant' and assistant['model'] == session['model']
            and assistant['stopReason'] == 'error' and assistant['errorMessage'] == TIMEOUT
            and not assistant['content']
            and all(assistant['usage'][key] == 0 for key in ('input', 'output', 'totalTokens')),
            'No terminal SDK timeout')
    content = rows[-2]['message']['content']
    require(len(content) == 1 and content[0]['type'] == 'text', 'Unexpected request packet')
    packet = json.loads(content[0]['text'])
    require(packet['operation_id'] == job['id'], 'Native request does not match job')
    payload = {'request_id': job['id'], 'session_id': session['id'], 'text': packet['user_message'],
               'form_context': packet['current_form']}
    require(any(fingerprint({'action': 'send', 'payload': {**payload, 'language': language}}) == job['signature']
                for language in ('zh-CN', 'en')), 'Native request fingerprint differs from original job')
    start, finish = stamp(attempt['started_at']), stamp(attempt['finished_at'])
    require(all(start <= stamp(row['timestamp']) <= finish for row in rows), 'Native timestamps escape attempt')
    return sha256(raw).hexdigest()


def inspect(db, schema, root, job, session):
    metadata = json.loads(db.execute(f'SELECT body FROM {schema}.metadata').fetchone()[0])
    binding = object_row(db, 'main', 'assistant_model', session['profile_id'])
    require(metadata['purpose'] == 'conversation-model-v1' and metadata['profile_id'] == session['profile_id']
            and metadata['profile_sha256'] == session['profile_sha256'] == binding['profile_sha256']
            and binding['directory'] == str(root / 'models' / session['profile_id'])
            and metadata['provider']['type'] == 'PiAssistant' and metadata['provider']['mock_endpoint'] is None,
            'Ledger does not belong to the original model')
    attempts = [json.loads(body) for (body,) in db.execute(f"SELECT body FROM {schema}.objects WHERE kind='attempt'")]
    budget = list(db.execute(f'SELECT ceiling,used,held FROM {schema}.budget WHERE id=1').fetchone())
    ceiling, used, held = budget
    require(0 <= used <= ceiling and 0 <= held <= ceiling-used
            and ceiling == metadata['provider']['request_budget']
            and all(a['status'] in {'finished', 'unknown'} for a in attempts)
            and sum(a['reserved'] for a in attempts if a['status'] == 'unknown') == held, 'Ledger totals disagree')
    matches = [a for a in attempts if stamp(job['created_at']) <= stamp(a['started_at'])
               <= stamp(a['finished_at']) <= stamp(job['finished_at'])]
    require(len(matches) == 1, 'Could not bind a unique attempt to the completed job')
    attempt = matches[0]
    require(attempt['status'] == 'unknown' and attempt['outcome'] == 'failed'
            and attempt['evidence'] == {'purpose': 'conversation-v1', 'requests': None}
            and type(attempt['reserved']) is int and attempt['reserved'] > 8 and held == attempt['reserved'],
            'Expected one unreconciled legacy full reservation')
    released = attempt['reserved'] - 1
    return {'job_id': job['id'], 'model': session['model'], 'profile_id': session['profile_id'],
            'legacy_implementation': LEGACY_IMPLEMENTATION, 'original_attempt': attempt,
            'native_sha256': native_proof(root, job, session, attempt),
            'before': budget, 'after': [ceiling, used, held-released]}


def reconcile(root, ids, *, apply_digest=None, backup=None):
    root = unlinked(root)
    assistant = unlinked(root / 'assistant')
    apply = apply_digest is not None
    require(1 <= len(ids) <= 10 and len(ids) == len(set(ids)), 'Choose 1 to 10 distinct jobs')
    guard = FileLock(str(unlinked(root / 'workbench.lock')), timeout=0) if apply else nullcontext()
    catalog = journal(assistant / 'catalog')
    with guard, closing(sqlite3.connect(catalog.as_uri() + '?mode=ro', uri=True)) as db:
        subjects = []
        for identity in sorted(ids):
            job = object_row(db, 'main', 'job', identifier(identity))
            require(job['id'] == identity and job['action'] == 'send' and job['status'] == 'failed'
                    and job['error'] == OLD_ERROR, 'Job is not a completed legacy failure')
            session = object_row(db, 'main', 'assistant_session', identifier(job['session_id']))
            require(session['id'] == job['session_id'] and session['native_file'] is None
                    and session['native_sha256'] is None and not session['messages'], 'Conversation has newer history')
            subjects.append((job, session, journal(assistant / 'models' / identifier(session['profile_id']))))
        require(len({path for _, _, path in subjects}) == len(subjects), 'Use at most one repair per model ledger')
        for index, (_, _, path) in enumerate(subjects):
            db.execute(f'ATTACH DATABASE ? AS ledger{index}', (path.as_uri() + ('?mode=rw' if apply else '?mode=ro'),))
        # Check all journals before taking the write transaction. The workbench lock
        # prevents normal writers for the full backup and apply window.
        for schema in ['main', *(f'ledger{i}' for i in range(len(subjects)))]:
            require(db.execute(f'PRAGMA {schema}.user_version').fetchone()[0] == 1
                    and db.execute(f'PRAGMA {schema}.quick_check').fetchall() == [('ok',)]
                    and db.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0] == 'delete',
                    'Unsupported or damaged journal')
        plan = [inspect(db, f'ledger{i}', assistant, job, session) for i, (job, session, _) in enumerate(subjects)]
        digest = fingerprint(plan)
        if apply:
            require(apply_digest == digest and backup is not None, 'Reviewed plan changed or no backup location')
            backup = unlinked(backup)
            require(not backup.is_relative_to(root) and not backup.exists(), 'Backup needs a new directory outside the workbench')
            backup.mkdir(mode=0o700)
            for row, (_, _, path) in zip(plan, subjects):
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source:
                    with closing(sqlite3.connect(backup / (row['profile_id'] + '.sqlite3'))) as saved:
                        source.backup(saved)
            (backup / 'plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
            db.execute('BEGIN IMMEDIATE')
            require(plan == [inspect(db, f'ledger{i}', assistant, job, session)
                             for i, (job, session, _) in enumerate(subjects)], 'Evidence changed during backup')
            for i, row in enumerate(plan):
                schema = f'ledger{i}'
                old = row['original_attempt']
                audit = {**row, 'plan_sha256': digest, 'applied_at': datetime.now(timezone.utc).isoformat()}
                db.execute(f'INSERT INTO {schema}.objects VALUES(?,?,?)',
                           ('assistant_timeout_reconciliation', old['id'], json.dumps(audit, sort_keys=True)))
                db.execute(f"UPDATE {schema}.objects SET body=? WHERE kind='attempt' AND id=?",
                           (json.dumps({**old, 'reserved': 1}, sort_keys=True), old['id']))
                db.execute(f'UPDATE {schema}.budget SET held=? WHERE id=1', (row['after'][2],))
            db.commit()
        return {'applied': apply, 'plan_sha256': digest,
                'models': [{key: row[key] for key in ('model', 'before', 'after')} for row in plan]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', action='append', required=True)
    parser.add_argument('--home', type=Path)
    parser.add_argument('--apply-plan-sha')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    if args.apply_plan_sha:
        require(gateway_status(args.home)['status'] == 'stopped', 'Stop the verified original Gateway first')
    config = load_configuration(args.home)
    print(json.dumps(reconcile(Path(config['workspace_root']), args.job,
                               apply_digest=args.apply_plan_sha, backup=args.backup), indent=2))
