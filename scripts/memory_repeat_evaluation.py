"""Prepare first-candidate anti-repeat evaluation; paid run needs new conversation approval.

Uses original source-document memory, Pi AgentSession, UISession and durable ledgers.
Historical native episodes are not migrated or rebound to the authored repositories.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
for directory in (PROJECT / 'src', PROJECT.parent / 'ui-python-deps', PROJECT.parent / 'recovery-python-deps'):
    sys.path.insert(0, str(directory))
from repo_maintainer.models import Policy, digest
from repo_maintainer.task_session import fingerprint, require
from repo_maintainer.source_memory import build_memory, planning_context
from memory_repeat_cases import ARMS, CASES, KIND, RELATED, REFERENCE_NOTES, feature_tests, fixture
from memory_repeat_judge import run_probe, verified

OLD = PROJECT / 'evidence/2026-10-19/swebench-cl-multipair-run'
SCRIPTS = ('memory_repeat_cases.py', 'memory_repeat_probe.py', 'memory_repeat_judge.py', 'memory_repeat_evaluation.py')

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')

def code_hashes():
    from memory_temporal_evaluation import code_hashes as original
    result = original()
    for name in (*SCRIPTS, 'swebench_point_behavior.py'):
        result['scripts/' + name] = digest((PROJECT / 'scripts' / name).read_bytes())
    return result

def historical_notes():
    pins = json.loads((OLD / 'artifact-hashes.json').read_bytes())
    notes, proof = {}, {}
    for pair, identity in (('printing', RELATED), ('evalf', 'evalf-implemented-function')):
        relative = 'pairs/' + pair + '/history-ready.json'
        raw = (OLD / relative).read_bytes()
        require(digest(raw) == pins[relative], 'Original historical snapshot changed')
        ready = json.loads(raw); payload = ready['entry']['payload']
        require(fingerprint(ready['entry']) == ready['review_sha256']
                and fingerprint(payload) == ready['entry']['payload_sha256']
                and payload['classification'] == 'historical' and payload['runtime_authorized'] is False,
                'Historical projection differs')
        for name, expected in payload['evidence'].items():
            require(digest((OLD / 'pairs' / pair / 'state' / ready['episode'] / name).read_bytes()) == expected,
                    'Original historical report changed')
        if pair == 'printing':
            require(len(payload['rounds']) == 2 and payload['rounds'][0]['verification'] == 'failed'
                    and payload['rounds'][1]['verification'] == 'passed', 'Expected real failed-then-passed history')
        lines = ['# Historical case: ' + identity, '', 'Derived from a real MiMo run, not a new instruction.',
                 'Run: ' + ready['episode'], 'Payload SHA256: ' + ready['entry']['payload_sha256'],
                 'Issue: ' + payload['issue'][:300]]
        for row in payload['rounds']:
            lines.extend(['', 'Round %s: %s' % (row['number'], row['verification']), row['diagnosis']])
            if row.get('failure_excerpt'):
                lines.extend(['Actual failure excerpt:', row['failure_excerpt']])
            lines.append('Actual selected command exits: ' + json.dumps(row['test_exits']))
        lines.extend(['', 'Historical reference only. Revalidate the current source; no execution authority.'])
        text = '\n'.join(lines) + '\n'
        require(len(text.encode()) < 6000 and len(text.splitlines()) < 100, 'Historical note exceeds original channel bounds')
        notes[identity] = text
        proof[identity] = {'source': str(OLD / relative), 'source_sha256': digest(raw),
                           'payload_sha256': ready['entry']['payload_sha256'], 'run_id': ready['episode'],
                           'projection_sha256': digest(text.encode()), 'classification': 'historical',
                           'native_episode_imported': False}
    return notes, proof

def corpus(root, notes):
    root.mkdir(parents=True)
    rows = []
    for identity, text in notes.items():
        historical = identity not in REFERENCE_NOTES
        path = root / (identity + '.md'); path.write_text(text, encoding='utf-8', newline='\n')
        rows.append({'id': identity, 'topic': identity, 'path': path.name, 'start_line': 1,
                     'end_line': len(text.splitlines()), 'kind': 'case' if historical else 'document',
                     'classification': 'historical' if historical else 'reference'})
    save(root / 'memory-catalog.json', {'schema_version': 1, 'entries': rows})

def git(path, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0',
               GIT_AUTHOR_DATE='2026-10-19T00:00:00+00:00', GIT_COMMITTER_DATE='2026-10-19T00:00:00+00:00')
    return subprocess.check_output(['git', '-c', 'core.autocrlf=false', '-c', 'core.hooksPath=' + os.devnull,
        '-c', 'core.fsmonitor=false', '-c', 'user.name=RepoCare Evaluation',
        '-c', 'user.email=repocare-evaluation@example.invalid', '-C', str(path), *args], env=env, stderr=subprocess.PIPE)

def control_rows(root, pin):
    results = {}
    for case in CASES:
        results[case] = {}
        for variant in ('base', 'bad', 'good'):
            receipt = run_probe(fixture(case, variant).encode(), case, pin, root / 'controls' / case / variant)
            value = receipt['result']; groups = value['tests_status']
            require(not groups['FEATURE']['failure'] if variant != 'base' else bool(groups['FEATURE']['failure']),
                    'Feature positive/negative control differs')
            require(bool(groups['PRESERVATION']['failure']) == (variant == 'bad')
                    and value['repeat_error'] == (variant == 'bad') and value['passed'] == (variant == 'good'),
                    'Old-error control did not distinguish the fixture')
            results[case][variant] = receipt
    return results

def prepare(root, python):
    require(not root.exists(), 'Use a new directory; preserve previous intents')
    require(python.is_file(), 'Explicit existing judge Python required')
    root.mkdir(parents=True)
    save(root / 'preparation-intent.json', {'kind': KIND, 'model_requests': 0, 'credentials_read': False})
    notes, proof = historical_notes()
    save(root / 'historical-sources.json', proof)
    corpus(root / 'selected', {RELATED: notes[RELATED]})
    corpus(root / 'pool', {**notes, **{key: '# Reference: ' + key + '\n\n' + text + '\n'
                                    for key, text in REFERENCE_NOTES.items()}})
    cells, queries = [], {}
    for case, spec in CASES.items():
        for arm in ARMS:
            identifier = case + '--' + arm; cell = root / 'cells' / identifier; source = cell / 'source'
            source.mkdir(parents=True)
            (source / 'printer.py').write_text(fixture(case), encoding='utf-8', newline='\n')
            (source / 'test_feature.py').write_text(feature_tests(case), encoding='utf-8', newline='\n')
            git(source, 'init', '-b', 'fixture'); git(source, 'add', '--', 'printer.py', 'test_feature.py')
            git(source, 'commit', '-m', 'Fixed authored task ' + case)
            row = {'id': identifier, 'case': case, 'arm': arm, 'source': str(source),
                   'base_sha': git(source, 'rev-parse', 'HEAD').decode().strip(), 'issue': spec['issue'],
                   'memory_root': None, 'memory_index': None}
            if arm != ARMS[0]:
                collection = root / ('selected' if arm == ARMS[1] else 'pool')
                index = cell / 'case.memory.sqlite3'; build_memory(collection, source, index)
                row.update(memory_root=str(collection), memory_index=str(index))
                packet = planning_context(collection, source, index, spec['issue'])
                queries[identifier] = {'hits': [item['id'] for item in packet['hits']],
                    'related_rank': next((i + 1 for i, item in enumerate(packet['hits']) if item['id'] == RELATED), None),
                    'context': packet, 'maximum_results': 5, 'model_requests': 0}
            cells.append(row)
    save(root / 'retrieval-preflight.json', queries)
    pin = json.loads((PROJECT.parent / 'swebench-cl-multipair-20261019/pairs/printing/image-lock.json').read_bytes())['sympy__sympy-14531']
    save(root / 'image-lock.json', pin)
    controls = control_rows(root, pin); save(root / 'controls.json', controls)
    manifest = {'schema_version': 1, 'kind': KIND, 'root': str(root), 'cells': cells,
                'order': [case + '--' + arm for case, spec in CASES.items() for arm in spec['order']],
                'provider_config': str(PROJECT / '.env.mimo.local'), 'judge_python': str(python.resolve()),
                'request_ceiling': 225, 'per_cell_ceiling': 25, 'max_attempts': 3,
                'max_output_tokens': 128000, 'planning_timeout_seconds': 1800,
                'metric': 'first_candidate_before_any_candidate_test_feedback', 'memory_channel': 'source_case_documents',
                'native_episode_imported': False, 'pool_size': 6, 'retrieval_limit': 5,
                'paid_authorized': False, 'model_requests': 0, 'code': code_hashes()}
    paths = [root / name for name in ('historical-sources.json', 'retrieval-preflight.json', 'image-lock.json', 'controls.json')]
    paths += [path for directory in ('selected', 'pool') for path in (root / directory).iterdir()]
    for row in cells:
        paths += [Path(row['source']) / name for name in ('printer.py', 'test_feature.py')]
        if row['memory_index']: paths.append(Path(row['memory_index']))
    manifest['files'] = {str(path.relative_to(root)).replace('\\', '/'): digest(path.read_bytes()) for path in paths}
    save(root / 'manifest.json', manifest)
    checked(root, fingerprint(manifest))
    return {'manifest_sha256': fingerprint(manifest), 'cells': 9, 'container_controls': 9,
            'model_requests': 0, 'credentials_read': False, 'paid_authorized': False,
            'retrieval_hits': {key: row['hits'] for key, row in queries.items()}}

def checked(root, approved):
    manifest = json.loads((root / 'manifest.json').read_bytes())
    require(fingerprint(manifest) == approved and manifest['root'] == str(root)
            and manifest['kind'] == KIND and manifest['code'] == code_hashes(), 'Frozen suite or code changed')
    require(manifest['request_ceiling'] == 225 and manifest['per_cell_ceiling'] == 25
            and manifest['max_attempts'] == 3 and manifest['max_output_tokens'] == 128000
            and manifest['planning_timeout_seconds'] == 1800, 'Frozen limits changed')
    require(manifest['provider_config'] == str(PROJECT / '.env.mimo.local'), 'No alternate credentials')
    for name, expected in manifest['files'].items():
        require(digest((root / name).read_bytes()) == expected, 'Frozen input changed: ' + name)
    notes, proof = historical_notes()
    require(proof == json.loads((root / 'historical-sources.json').read_bytes()), 'Original historical source changed')
    for identity, note in notes.items():
        require((root / 'pool' / (identity + '.md')).read_bytes() == note.encode(), 'Historical note changed')
    expected = [(case, arm) for case in CASES for arm in ARMS]
    require([(row['case'], row['arm']) for row in manifest['cells']] == expected, 'Frozen conditions changed')
    require(manifest['order'] == [case + '--' + arm for case, spec in CASES.items() for arm in spec['order']], 'Order changed')
    for row in manifest['cells']:
        require(row['id'] == row['case'] + '--' + row['arm']
                and Path(row['source']) == root / 'cells' / row['id'] / 'source'
                and row['issue'] == CASES[row['case']]['issue'], 'Cell scope changed')
        collection = None if row['arm'] == ARMS[0] else root / ('selected' if row['arm'] == ARMS[1] else 'pool')
        require(row['memory_root'] == (str(collection) if collection else None)
                and row['memory_index'] == (str(root / 'cells' / row['id'] / 'case.memory.sqlite3') if collection else None),
                'Memory scope changed')
    return manifest

def score_run(record):
    require(record['source_unchanged'] is True, 'Safely completed run required')
    if not record['rounds']:
        return {'first_candidate_available': False, 'first_repeat_error': None, 'first_feature_passed': None,
                'first_all_checks_passed': None, 'final_all_checks_passed': False, 'rounds': 0,
                'metric': 'first_candidate_before_any_candidate_test_feedback',
                'first_loaded_citations': [], 'first_memory_load': None}
    def result(row):
        require(len(row['tests']) == 1 and len(row['plan']['edits']) == 1, 'Unexpected selected scope')
        test = row['tests'][0]; require(not test['timed_out'], 'Inconclusive timed-out check')
        payload = json.loads(test['stdout'].strip())
        verified({key: payload[key] for key in ('kind', 'case', 'source_sha256', 'tests_status', 'repeat_error',
                  'model_requests', 'official_score', 'passed')}, test['returncode'], payload['case'],
                 digest(row['plan']['edits'][0]['content'].encode()))
        return payload
    first, final = result(record['rounds'][0]), result(record['rounds'][-1])
    observable = 'test_unique_declarations' not in first['tests_status']['PRESERVATION']['errors']
    return {'first_candidate_available': True, 'first_repeat_error': first['repeat_error'] if observable else None,
            'first_feature_passed': not first['tests_status']['FEATURE']['failure'],
            'first_all_checks_passed': first['passed'], 'final_all_checks_passed': final['passed'],
            'rounds': len(record['rounds']), 'metric': 'first_candidate_before_any_candidate_test_feedback',
            'first_loaded_citations': record['rounds'][0]['provider_evidence'].get('memory_citations', []),
            'first_memory_load': record['rounds'][0]['provider_evidence'].get('memory_load')}

def open_session(root, manifest, cell, transport=None, state=None):
    from repo_maintainer.local_ui import UISession
    from repo_maintainer.pi_provider import PiProvider
    provider = PiProvider(('printer.py',), transport.url if transport else None,
                          readonly_paths=('test_feature.py',), max_requests=25)
    if transport is None:
        provider.config_file = Path(manifest['provider_config'])
    if cell['memory_root']:
        provider.memory_root, provider.memory_index = Path(cell['memory_root']), Path(cell['memory_index'])
    command = (manifest['judge_python'], '-X', 'utf8', str(PROJECT / 'scripts/memory_repeat_judge.py'),
               '--root', str(root), '--case', cell['case'], '--workspace', '.')
    policy = Policy(('printer.py',), (command,), timeout_seconds=150, readonly_paths=('test_feature.py',))
    return UISession(Path(cell['source']), policy, provider, state or root / 'cells' / cell['id'] / 'state',
                     allow_auto=True, max_attempts=3, source_guard=lambda: checked(root, fingerprint(manifest)))

def run(root, approved):
    manifest = checked(root, approved)
    authorization = json.loads((root / 'paid-authorization.json').read_bytes())
    require(authorization.get('approved') is True and authorization.get('manifest_sha256') == approved
            and authorization.get('request_ceiling') == 225, 'Record new user authorization first')
    require(not (root / 'run-intent.json').exists()
            and all(not (root / 'cells' / row['id'] / 'state').exists() for row in manifest['cells']),
            'Existing paid intent/state; inspect instead of replaying')
    from repo_maintainer.local_ui import json_digest
    save(root / 'run-intent.json', {'manifest_sha256': approved, 'request_ceiling': 225, 'cells': manifest['order']})
    rows = []; provider_pin = digest(Path(manifest['provider_config']).read_bytes())
    for identity in manifest['order']:
        checked(root, approved)
        require(digest(Path(manifest['provider_config']).read_bytes()) == provider_pin, 'Provider changed')
        cell = next(row for row in manifest['cells'] if row['id'] == identity)
        directory = root / 'cells' / identity
        require(git(Path(cell['source']), 'rev-parse', 'HEAD').decode().strip() == cell['base_sha'], 'Cell base changed')
        save(directory / 'intent.json', {'manifest_sha256': approved, 'cell': identity})
        session = open_session(root, manifest, cell)
        try:
            outcome = session._operate('auto', {'issue': cell['issue']})
            budget = session.registry.budget(); require(budget['requests_reserved_unknown'] == 0, 'Unknown consumption stops batch')
            record = json.loads((directory / 'state' / outcome['run_id'] / 'run.json').read_bytes())
            score = score_run(record)
            expected = [] if cell['arm'] == ARMS[0] else [item['id'] for item in
                planning_context(Path(cell['memory_root']), Path(cell['source']), Path(cell['memory_index']), cell['issue'])['hits']]
            if score['first_candidate_available']:
                require([item['id'] for item in score['first_loaded_citations']] == expected, 'Actual source memory differs')
            if outcome['workflow_status'] == 'candidate_ready':
                session._operate('cleanup', {'run_id': outcome['run_id'], 'revision': json_digest(session._run_view(outcome['run_id']))})
            require(not git(Path(cell['source']), 'status', '--porcelain').strip(), 'Original fixture changed')
            row = {'id': identity, 'result': outcome, 'score': score, 'budget': budget,
                   'attempts': session.registry.load('attempt'), 'source_clean': True}
            save(directory / 'result.json', row); rows.append(row)
        finally:
            session.close()
    summary = {'completed': True, 'kind': KIND, 'results': rows, 'model_requests': sum(row['budget']['requests_used'] for row in rows),
               'general_memory_benefit_established': False, 'official_score': False}
    require(summary['model_requests'] <= 225, 'Aggregate ceiling exceeded')
    save(root / 'summary.json', summary)
    return {'completed': True, 'model_requests': summary['model_requests'], 'cells': 9}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check', 'run'))
    parser.add_argument('--root', type=Path, required=True); parser.add_argument('--judge-python', type=Path)
    parser.add_argument('--approved-manifest')
    args = parser.parse_args(); root = args.root.resolve()
    if args.action == 'prepare':
        require(args.judge_python is not None, 'Explicit existing judge Python required'); value = prepare(root, args.judge_python)
    elif args.action == 'check':
        checked(root, args.approved_manifest); value = {'checked': True, 'model_requests': 0, 'credentials_read': False}
    else:
        value = run(root, args.approved_manifest)
    print(json.dumps(value), flush=True)
