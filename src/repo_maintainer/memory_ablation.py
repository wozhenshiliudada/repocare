"""Four memory configurations through production Pi/controller, synthetic SSE only.

This experiment audits transport, isolation and accounting. The same scripted
first/final patches are supplied in every arm: outcomes cannot establish model
benefit. There is deliberately no user-repository, credential or live-model input.
"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import subprocess
import threading

from .comparison import file_snapshot
from .demo import DEMO_TEST
from .git import RepositoryGateway, git_env
from .local_ui import UISession
from .memory_ablation_fixtures import cases, suite_sha256
from .models import Policy, SafetyError, digest
from .pi_provider import PiProvider
from .recovery import unlinked
from .runner import run_test
from .store import now
from .task_session import fingerprint

ARMS = ('no_memory', 'session', 'session_experience', 'session_experience_knowledge')
CANARY = 'PRIVATE_GRADER_MEMORY_ABLATION_NEVER_SEND'


def require(value, message):
    if not value:
        raise SafetyError(message)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class EvaluationLedger:
    """One independent ceiling across all cells and their original UI ledgers.

    A received request consumes a slot before a response. Interrupted slots stay
    consumed; reopening cannot refund or increase the ceiling. This is an
    experimental aggregate, never authority to replenish a production registry.
    """
    def __init__(self, path, ceiling):
        require(type(ceiling) is int and 1 <= ceiling <= 100, 'Invalid aggregate request ceiling')
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), ceiling INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO config VALUES (1,?)', (ceiling,))
            require(db.execute('SELECT ceiling FROM config').fetchone() == (ceiling,), 'Evaluation ceiling is immutable')
            db.execute('CREATE TABLE IF NOT EXISTS request (id INTEGER PRIMARY KEY, cell TEXT NOT NULL, phase TEXT NOT NULL, '
                       'body TEXT NOT NULL, sha TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL)')

    def receive(self, cell, phase, body):
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            ceiling = db.execute('SELECT ceiling FROM config').fetchone()[0]
            require(db.execute('SELECT count(*) FROM request').fetchone()[0] < ceiling,
                    'Aggregate synthetic evaluation ceiling exhausted; no retry or refund')
            cursor = db.execute('INSERT INTO request(cell,phase,body,sha,status,started_at) VALUES (?,?,?,?,?,?)',
                                (cell, phase, raw, digest(raw.encode()), 'received', now()))
            return cursor.lastrowid

    def sent(self, identity):
        with closing(sqlite3.connect(self.path)) as db, db:
            require(db.execute("UPDATE request SET status='sent' WHERE id=? AND status='received'", (identity,)).rowcount == 1,
                    'Request acknowledgement does not match original received slot')

    def rows(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM request ORDER BY id')]


def user_envelopes(body):
    result = []
    for message in body.get('messages', []):
        if message.get('role') != 'user':
            continue
        content = message['content']
        if isinstance(content, list):
            content = ''.join(r.get('text', '') for r in content)
        result.append(json.loads(content))
    return result


class ScriptTransport:
    """Loopback responses depend on fixed phase/source, never arm or grader data."""
    def __init__(self, ledger):
        self.ledger, self.cell, self.phase, self.case = ledger, None, None, None
        self.errors = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_POST(self):
                try:
                    require(self.path == '/v1/chat/completions', 'Unexpected synthetic endpoint')
                    length = int(self.headers.get('Content-Length', '0'))
                    require(0 < length <= 2 * 1024 * 1024, 'Synthetic request size invalid')
                    body = json.loads(self.rfile.read(length))
                    slot = owner.ledger.receive(owner.cell, owner.phase, body)
                    require(CANARY not in json.dumps(body), 'Independent grader leaked to model request')
                    delta, finish = owner.response_delta(body, slot)
                    frame = {'id': 'synthetic', 'object': 'chat.completion.chunk', 'created': 1, 'model': body['model'],
                             'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}],
                             'usage': {'prompt_tokens': 100, 'completion_tokens': 11, 'total_tokens': 111}}
                    self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
                    self.wfile.write(('data: ' + json.dumps(frame) + '\n\ndata: [DONE]\n\n').encode())
                    owner.ledger.sent(slot)
                except Exception as error:
                    owner.errors.append(f'{type(error).__name__}: {error}')
                    self.send_error(503, 'Synthetic evaluation stopped; preserve original ledger')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'

    def response_delta(self, body, slot):
        name, args = self.response(body)
        return {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'synthetic_{slot}',
            'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, 'tool_calls'

    def response(self, body):
        names = [t['function']['name'] for t in body.get('tools', [])]
        if names == ['propose_memory']:
            require(self.phase == 'teaching_reflection', 'Unexpected reflection during evaluation')
            return 'propose_memory', deepcopy(self.case['note'])
        require(sorted(names) == ['propose_repair', 'read_source'], 'Unexpected agent capabilities')
        users = [i for i, m in enumerate(body['messages']) if m['role'] == 'user']
        current = body['messages'][users[-1] + 1:]
        read = next((m for m in current if m['role'] == 'tool' and 'before_sha256' in m.get('content', '')), None)
        if read is None:
            return 'read_source', {'path': 'calculator.py'}
        source = json.loads(read['content'])['content']
        old = next(line.strip() for line in source.splitlines() if line.strip().startswith('return '))
        new = self.case['second'] if self.phase == 'measurement_second' else self.case['first']
        return 'propose_repair', {'diagnosis': 'Fixed synthetic engineering response, independent of memory arm',
                                  'risk': 'Disposable authored fixture only', 'edits': [{'path': 'calculator.py',
                                  'replacements': [{'oldText': old, 'newText': new}]}]}

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()


def create_source(case, path):
    path.mkdir(parents=True, exist_ok=False)
    (path / 'tests').mkdir()
    (path / 'calculator.py').write_text(case['source'], encoding='utf-8', newline='\n')
    (path / 'tests/test_calculator.py').write_text(case['visible_tests'], encoding='utf-8', newline='\n')
    subprocess.run(['git', 'init', '-b', 'main', str(path)], check=True, capture_output=True, env=git_env())
    repo = RepositoryGateway(path)
    repo.command('add', '.')
    repo.command('-c', 'user.name=Memory Evaluation', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-m', 'fixture: public authored memory experiment')
    return repo


def review(session, kind, identity):
    memory = 'experience_memory' if kind == 'experience' else 'knowledge_memory'
    row = next(r for r in session.state()[memory]['entries'] if r['id'] == identity)
    payload = {'run_id' if kind == 'experience' else 'knowledge_id': identity,
               'revision': row['revision'], 'review_status': 'accepted'}
    session._operate(kind + '_review', payload)


def native_bytes(session, task):
    return (session.task_planner.store.directory(task['task_id']) / 'sessions' / task['checkpoint']['file']).read_bytes()


def grade(case, destination, content):
    destination.mkdir(exist_ok=False)
    (destination / 'calculator.py').write_text(content, encoding='utf-8', newline='\n')
    (destination / 'test_independent.py').write_text('# ' + CANARY + '\n' + case['grader_tests'], encoding='utf-8', newline='\n')
    before = file_snapshot(destination)
    result = run_test(('{python}', '-m', 'unittest', 'discover', '-v'), destination, 30)
    require(before == file_snapshot(destination), 'Independent grading modified fixture')
    return result


def execute_cell(case, arm, root, transport, progress):
    cell = case['id'] + '/' + arm
    root.mkdir(parents=True)
    source = create_source(case, root / 'source')
    before = source.snapshot()
    baseline = grade(case, root / 'grader-baseline', case['source'])
    require(baseline['returncode'] != 0 and not baseline['timed_out'], 'Fixture needs reproducible failing baseline')
    def open_session():
        provider = PiProvider(('calculator.py',), transport.url, readonly_paths=('tests/test_calculator.py',), max_requests=50)
        return UISession(source.root, Policy(('calculator.py',), (DEMO_TEST,)), provider, root / 'state')
    session = open_session()
    transport.cell, transport.case, transport.phase = cell, case, 'teaching_failure'
    try:
        # Every arm pays for the exact same pre-measurement material, but only
        # explicit accepted/enabled references may enter a new production task.
        plan = session._operate('plan', {'issue': case['issue']})
        lesson = session._operate('decision', {**plan, 'decision': 'approve'})
        require(lesson['workflow_status'] == 'rolled_back', 'Teaching trial must really fail and roll back')
        review(session, 'experience', lesson['run_id'])
        transport.phase = 'teaching_reflection'
        preview = session._operate('knowledge_preview', {'experience_ids': [lesson['run_id']]})['knowledge_preview']
        note = session._operate('knowledge_propose', {'experience_ids': preview['experience_ids'],
                                                    'revision': preview['revision']})['knowledge_id']
        require(session.registry.budget()['requests_used'] == 3, 'Teaching requests differ across arms')
        if arm.endswith('_knowledge'):
            review(session, 'knowledge', note)
        enabled = arm in ARMS[2:]
        revision = session.state()['experience_memory']['settings_revision']
        session._operate('experience_settings', {'enabled': enabled, 'revision': revision})
        cutoff = {'at': now(), 'experience': lesson['run_id'], 'knowledge': note,
                  'budget': session.registry.budget(), 'experience_enabled': enabled,
                  'knowledge_review': 'accepted' if arm.endswith('_knowledge') else 'pending',
                  'experience_sha256': fingerprint(session.registry.load('repository_experience')),
                  'knowledge_sha256': fingerprint(session.registry.load('repository_knowledge'))}
        write_json(root / 'cutoff.json', cutoff)
        transport.phase = 'measurement_first'
        first = session._operate('plan', {'issue': case['issue']})
        task = deepcopy(session.registry.load('pi_task')[session.task_planner.identity])
        failed = session._operate('decision', {**first, 'decision': 'approve'})
        require(failed['workflow_status'] == 'rolled_back', 'First measurement must fail actual authorized tests')
        failed_record = json.loads((root / 'state' / failed['run_id'] / 'run.json').read_text())
        first_content = failed_record['rounds'][0]['plan']['edits'][0]['content']
        original_native = native_bytes(session, task)
        original_budget = session.registry.budget()
        session.close(); session = open_session()
        require(original_native == native_bytes(session, task), 'Restart rewrote original native history')
        require(original_budget == session.registry.budget(), 'Restart changed original budget')
        transport.phase = 'measurement_second'
        if arm == 'no_memory':
            final = session._operate('plan', {'issue': case['issue']})
        else:
            preview = session._resume_preview(failed['run_id'])
            final = session._operate('resume_run', {'run_id': failed['run_id'], 'revision': preview['revision'], 'mode': 'approval'})
        second = deepcopy(session.registry.load('pi_task')[session.task_planner.identity])
        budget_before_approval = session.registry.budget()
        result = session._operate('decision', {**final, 'decision': 'approve'})
        require(result['workflow_status'] == 'candidate_ready', 'Fixed final response must pass actual authorized tests')
        run = json.loads((root / 'state' / result['run_id'] / 'run.json').read_text())
        candidate = (Path(run['workspace']) / 'calculator.py').read_text(encoding='utf-8')
        independent = grade(case, root / 'grader-final', candidate)
        require(independent['returncode'] == 0 and not independent['timed_out'], 'Independent grader failed')
        require(budget_before_approval == session.registry.budget(), 'Approval or grading issued model requests')
        rows = [r for r in transport.ledger.rows() if r['cell'] == cell]
        measured = [r for r in rows if r['phase'].startswith('measurement_')]
        envelopes = [user_envelopes(json.loads(r['body'])) for r in measured]
        require(len(rows) == 7 and len(measured) == 4, 'Request counts differ from fixed equal opportunities')
        histories = [len(e) for e in envelopes]
        require(histories[:2] == [1, 1] and histories[2:] == ([1, 1] if arm == 'no_memory' else [2, 2]),
                'Actual model session history differs from declared arm')
        for envelope in (e[-1] for e in envelopes[:2]):
            exp = [h['id'] for h in envelope.get('repository_experience', {}).get('hits', [])]
            kn = [h['id'] for h in envelope.get('repository_knowledge', {}).get('hits', [])]
            require(exp == ([lesson['run_id']] if enabled else []), 'Experience reference leaked or was omitted')
            require(kn == ([note] if arm.endswith('_knowledge') else []), 'Knowledge reference leaked or was omitted')
        same_task = task['task_id'] == second['task_id']
        require(same_task == (arm != 'no_memory'), 'Task identity does not match memory arm')
        require((task['checkpoint']['session_id'] == second['checkpoint']['session_id']) == same_task,
                'Native session identity differs from task identity')
        require(session.registry.budget()['requests_used'] == len(rows) and
                session.registry.budget()['requests_reserved_unknown'] == 0, 'Original ledger does not reconcile')
        require(before == source.snapshot(exclude_branch=run['branch']),
                'Experiment altered its original source or published an extra branch')
        row = {'case': case['id'], 'arm': arm, 'cell': cell, 'passed': True,
               'fixture_sha256': fingerprint({'source': case['source'], 'tests': case['visible_tests'], 'issue': case['issue']}),
               'cutoff': cutoff, 'first_task': task['task_id'], 'final_task': second['task_id'],
               'native_session_preserved': same_task, 'restart_bytes_unchanged': True, 'user_message_counts': histories,
               'statuses': [failed['workflow_status'], result['workflow_status']], 'baseline_grader': baseline,
               'independent_grader': independent, 'workflow_tests': run['candidate_tests'],
               'original_budget': session.registry.budget(), 'total_synthetic_requests': len(rows),
               'measurement_requests': len(measured), 'source_unchanged': True, 'published': False,
               'first_patch_sha256': digest(first_content.encode()), 'final_patch_sha256': digest(candidate.encode()),
               'planning_origins': [failed_record['planning_origin'], run['planning_origin']],
               'memory_loads': [r['planning_origin']['provider_evidence']['memory_load'] for r in (failed_record, run)],
               'paid_requests': 0, 'model_benefit_claim': False}
        write_json(root / 'cell-result.json', row)
        if progress:
            progress({'cell': cell, 'status': 'passed', 'synthetic_requests': len(rows), 'paid_requests': 0})
        return row
    finally:
        session.close()


def run_ablation(destination, ceiling=100, progress=None):
    destination = unlinked(Path(destination))
    require(not destination.exists(), 'Ablation output must be new; never replace or reset an evaluation')
    require(type(ceiling) is int and 1 <= ceiling <= 100, 'Invalid aggregate request ceiling')
    destination.mkdir(parents=True)
    ledger = EvaluationLedger(destination / 'evaluation.sqlite3', ceiling)
    transport = ScriptTransport(ledger)
    summary = {'schema_version': 1, 'kind': 'public-authored-synthetic-engineering', 'started_at': now(),
               'dataset_sha256': suite_sha256(), 'aggregate_ceiling': ceiling, 'cells': [], 'passed': False,
               'paid_requests': 0, 'model_benefit_claim': False,
               'limitations': ['Not blind: public authored fixtures and scripted repair responses.',
                              'Same scripted patches in every arm; repair outcomes do not measure memory benefit.',
                              'No user repository, live provider, credentials, remote push or main merge accepted.']}
    write_json(destination / 'fixture.json', {'cases': cases(), 'sha256': suite_sha256()})
    try:
        for case in cases():
            for arm in ARMS:
                write_json(destination / 'summary.json', summary)
                summary['cells'].append(execute_cell(case, arm, destination / case['id'] / arm, transport, progress))
        rows = ledger.rows()
        require(not transport.errors and all(r['status'] == 'sent' for r in rows), 'Transport has incomplete requests')
        require(len(rows) == sum(r['original_budget']['requests_used'] for r in summary['cells']),
                'Aggregate ledger differs from original cell consumption')
        # Every model body may contain its own lesson refs, never any other cell's refs.
        for row in rows:
            others = [c for c in summary['cells'] if c['cell'] != row['cell']]
            require(all(c['cutoff']['experience'] not in row['body'] and c['cutoff']['knowledge'] not in row['body']
                        and c['first_task'] not in row['body'] and c['final_task'] not in row['body'] for c in others),
                    'Cross-cell memory contamination')
        for case in cases():
            group = [c for c in summary['cells'] if c['case'] == case['id']]
            for key in ('fixture_sha256', 'first_patch_sha256', 'final_patch_sha256'):
                require(len({r[key] for r in group}) == 1, 'Experimental inputs/responses are not matched')
        summary.update(passed=len(summary['cells']) == 8, aggregate_received=len(rows),
                       aggregate_sent=sum(r['status'] == 'sent' for r in rows), isolation_verified=True)
    except Exception as error:
        summary['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        transport.close()
        summary.update(finished_at=now(), aggregate_received=len(ledger.rows()), transport_errors=transport.errors)
        write_json(destination / 'summary.json', summary)
    return summary
