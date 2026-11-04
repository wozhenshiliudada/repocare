"""Explicit two-stage real-Pi SWE-Bench-CL development probe.

Preparation never reads credentials. Paid stages require a frozen manifest and
conversation authorization; an operator digest alone is not user authorization.
Only the fixed, owned SymPy pilot is supported. No model loop is implemented here.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
for path in (PROJECT / 'src', PROJECT.parent / 'ui-python-deps', PROJECT.parent / 'recovery-python-deps'):
    sys.path.insert(0, str(path))
from repo_maintainer.experience_memory import KIND
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession, json_digest
from repo_maintainer.models import Policy, digest
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.runtime_limits import MAX_OUTPUT_TOKENS, PLANNING_TIMEOUT_SECONDS
from repo_maintainer.task_session import fingerprint, require
from memory_temporal_controls import LimitedDurableProvider, RequestWindow

ARMS = ('A_existing_baseline', 'B_operator_selected_history', 'C_automatic_retrieval')
IDS = ('sympy__sympy-17655', 'sympy__sympy-22714')


def save(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def code_hashes():
    paths = [*PROJECT.glob('src/repo_maintainer/*.py'), *PROJECT.glob('runtime/pi/*.mjs'),
             PROJECT / 'runtime/pi/package-lock.json', PROJECT / 'src/repo_maintainer/pi_runtime_limits.json',
             Path(__file__).resolve(), Path(__file__).with_name('memory_temporal_controls.py')]
    paths += [Path(__file__).with_name(name) for name in ('swebench_point_behavior.py', 'point_behavior_probe.py')]
    return {str(path.relative_to(PROJECT)): digest(path.read_bytes()) for path in sorted(paths)}


def fixed_files(root, behavior_checks=False):
    paths = ['prepared/preparation.json', 'prepared/agent/tasks.json', 'prepared/judge/tasks.json',
             'judge.py', 'failure_output.py', 'image-lock.json', 'environment-ready.json', 'environment-controls.json']
    if behavior_checks:
        paths.append('behavior-controls.json')
    return {name: digest((root / name).read_bytes()) for name in paths}


def behavior_sources():
    return {name: digest((PROJECT/'scripts'/name).read_bytes())
            for name in ('point_behavior_probe.py', 'swebench_point_behavior.py')}


def prepare_behavior_controls(root, history_workspace, target_workspace):
    """New read-only, zero-model preparation; never opens a provider or old journal."""
    from swebench_point_behavior import evaluate, snapshot, verified_result, BASES
    require(not (root/'behavior-controls.json').exists() and not (root/'behavior-controls-intent.json').exists(),
            'Supplementary control intent exists; inspect it instead of replaying')
    inputs = dict(zip(IDS, (history_workspace, target_workspace)))
    for identity, workspace in inputs.items():
        current = snapshot(workspace)
        require(current['base_commit'] == BASES[identity] and current['patch'] == b'',
                'Supplementary control requires the clean matching original baseline')
        require(not root.resolve().is_relative_to(Path(current['workspace']))
                and not root.resolve().is_relative_to(Path(current['common_dir'])),
                'Control evidence must be outside source/Git metadata')
    sources = behavior_sources()
    save(root/'behavior-controls-intent.json', {'schema_version': 1, 'source_hashes': sources,
         'workspaces': {key: str(Path(value).resolve()) for key,value in inputs.items()}, 'model_requests': 0})
    rows = {}
    for identity, workspace in inputs.items():
        row = evaluate(workspace, root/'image-lock.json', root/'behavior-control-results', 'preserve-imaginary')
        verified_result(row, 0, BASES[identity], digest(b''), 'preserve-imaginary')
        require(row['source_unchanged'] and row['network_disabled'] and not row['infrastructure_error'],
                'Supplementary control did not safely pass')
        rows[identity] = row
    require(behavior_sources() == sources, 'Behavior checker changed during controls')
    save(root/'behavior-controls.json', {'schema_version': 1, 'kind': 'point-preservation-controls-v1',
         'source_hashes': sources, 'baselines': rows, 'model_requests': 0, 'official_score': False})
    return {'behavior_controls_ready': True, 'model_requests': 0, 'official_score': False,
            'controls_sha256': digest((root/'behavior-controls.json').read_bytes())}


def freeze(root, python, behavior_checks=False):
    require(type(behavior_checks) is bool, 'Behavior selection must be explicit')
    require(not (root / 'pilot-manifest.json').exists(), 'Pilot manifest already exists')
    prepared = json.loads((root / 'prepared/preparation.json').read_bytes())
    require((prepared['history'], prepared['target']) == IDS, 'Only the prepared owned SymPy pilot is supported')
    controls = json.loads((root / 'environment-controls.json').read_bytes())
    images = json.loads((root / 'image-lock.json').read_bytes())
    require(set(controls) == set(IDS), 'Both official control pairs are required')
    for identity in IDS:
        for mode in ('baseline', 'gold'):
            row = controls[identity][mode]
            require(not row['infrastructure_error'] and row['image'] == images[identity], 'Control environment changed')
            require(row['valid_red_baseline'] if mode == 'baseline' else row['resolved'], 'Official control failed')
    if behavior_checks:
        from swebench_point_behavior import verified_result, BASES
        pack = json.loads((root / 'behavior-controls.json').read_bytes())
        require(pack.get('schema_version') == 1 and pack.get('kind') == 'point-preservation-controls-v1'
                and pack.get('source_hashes') == behavior_sources() and pack.get('model_requests') == 0
                and pack.get('official_score') is False, 'Supplementary checker version changed or controls are unbound')
        behavior = pack.get('baselines', {})
        require(set(behavior) == set(IDS), 'Both supplementary baseline controls are required')
        for identity in IDS:
            row = behavior[identity]
            verified_result(row, 0, BASES[identity], digest(b''), 'preserve-imaginary')
            require(row.get('image') == images[identity] and row.get('source_unchanged') is True
                    and row.get('network_disabled') is True and row.get('infrastructure_error') is False,
                    'Supplementary baseline control changed or failed')
    config = PROJECT / '.env.mimo.local'
    require(config.is_file(), 'This project explicit MiMo configuration is required')
    manifest = {'schema_version': 1, 'kind': 'swebench-cl-sympy-development-probe',
                'root': str(root), 'provider_config': str(config), 'judge_python': str(python.resolve()),
                'history': IDS[0], 'target': IDS[1], 'arms': ARMS,
                'request_ceiling': 100, 'history_ceiling': 25, 'per_arm_ceiling': 25,
                'max_attempts': 3, 'max_output_tokens': MAX_OUTPUT_TOKENS,
                'planning_timeout_seconds': PLANNING_TIMEOUT_SECONDS,
                'files': fixed_files(root, behavior_checks), 'code': code_hashes(),
                'paid_requests': 0, 'credentials_read': False,
                'limitations': prepared['limitations'],
                'authorization': 'separate conversation approval required; no reuse of old remaining 67 requests'}
    # Normalize tuples exactly as the saved JSON will be read.
    manifest = json.loads(json.dumps(manifest))
    if behavior_checks:
        manifest['behavior_preservation'] = 'point-imaginary-preservation-v1'
        manifest['limitations'].append('Supplementary point checks derive from observed development failures, not a blind official score.')
    save(root / 'pilot-manifest.json', manifest)
    return {'manifest_sha256': fingerprint(manifest), 'paid_requests': 0, 'credentials_read': False}


def checked(root, approved):
    manifest = json.loads((root / 'pilot-manifest.json').read_bytes())
    require(fingerprint(manifest) == approved and manifest['root'] == str(root), 'Frozen operator confirmation differs')
    require(manifest['files'] == fixed_files(root, 'behavior_preservation' in manifest)
            and manifest['code'] == code_hashes(), 'Frozen pilot files or code changed')
    require(manifest['request_ceiling'] == 100 and manifest['history_ceiling'] == 25
            and manifest['per_arm_ceiling'] == 25 and tuple(manifest['arms']) == ARMS, 'Frozen allowances changed')
    policy_commands(root, manifest)  # Reject unknown supplementary selections before a session/credential read.
    return manifest


def policy_commands(root, manifest):
    command = (manifest['judge_python'], '-X', 'utf8', str(root / 'judge.py'),
               '--mode', 'workspace', '--workspace', '.')
    if 'behavior_preservation' not in manifest:
        return (command,)
    require(manifest['behavior_preservation'] == 'point-imaginary-preservation-v1', 'Unknown supplementary behavior selection')
    supplement = (manifest['judge_python'], '-X', 'utf8', str(PROJECT/'scripts/swebench_point_behavior.py'),
                  '--workspace', '.', '--images', str(root/'image-lock.json'),
                  '--output', str(root/'behavior-results'), '--group', 'preserve-imaginary')
    return (command, supplement)


def open_session(root, manifest):
    environment = json.loads((root / 'environment-ready.json').read_bytes())
    policy = Policy(tuple(environment['allowed_paths']), policy_commands(root, manifest), timeout_seconds=290,
                    readonly_paths=tuple(environment['readonly_paths']))
    provider = PiProvider(policy.allowed_paths, readonly_paths=policy.readonly_paths, max_requests=100)
    provider.config_file = Path(manifest['provider_config'])
    session = UISession(root / 'source', policy, provider, root / 'state', allow_auto=True, max_attempts=3,
                        source_guard=lambda: checked(root, fingerprint(manifest)))
    require(session.task_planner is not None, 'The original task journal is required')
    return session


def provider_pin(session, manifest):
    return json.loads(json.dumps({'identity': session.provider.inner.task_identity(),
                      'configuration_sha256': digest(Path(manifest['provider_config']).read_bytes())}))


def cleanup(session, result):
    if result['workflow_status'] == 'candidate_ready':
        session._operate('cleanup', {'run_id': result['run_id'],
                         'revision': json_digest(session._run_view(result['run_id']))})


def native_files(root):
    return {str(path.relative_to(root)): digest(path.read_bytes())
            for path in (root / 'state/task-sessions').glob('*/sessions/*.jsonl')}


def history(root, manifest):
    require(not (root / 'history-intent.json').exists() and not (root / 'state').exists(),
            'History intent/state exists; inspect it instead of replaying')
    agents = json.loads((root / 'prepared/agent/tasks.json').read_bytes())
    repository = RepositoryGateway(root / 'source')
    require(repository.head == agents[0]['base_commit'], 'Historical source base differs')
    save(root / 'history-intent.json', {'manifest_sha256': fingerprint(manifest), 'instance': IDS[0]})
    session = open_session(root, manifest)
    try:
        pin = provider_pin(session, manifest)
        session.provider = LimitedDurableProvider(session.provider, RequestWindow(session.registry, 25))
        result = session._operate('auto', {'issue': agents[0]['problem_statement']})
        require(not session.registry.budget()['requests_reserved_unknown'], 'Unknown consumption; stop the pilot')
        cleanup(session, result)
        episode = result['run_id']
        session.experience.capture(episode)
        entry = session.registry.load(KIND)[episode]
        save(root / 'history-ready.json', {'episode': episode, 'entry': entry,
             'review_sha256': fingerprint(entry), 'result': result,
             'provider_pin': pin, 'budget': session.registry.budget(), 'native_files': native_files(root)})
        return {'history_ready': True, 'review_sha256': fingerprint(entry), 'budget': session.registry.budget()}
    finally:
        session.close()


def selected_history(session, identity, entry):
    require(session.registry.load(KIND).get(identity) == entry and entry['review'] == 'accepted', 'Frozen history differs')
    payload = session.experience.validate(identity, entry, applicable=True)
    result = {'id': identity, 'payload_sha256': entry['payload_sha256'], 'review_version': entry['review_version'],
              'episode': payload, 'applicability': 'matching_original_source' if payload['source_files'] == session.experience.files()
              else 'requires_revalidation'}
    require(len(json.dumps(result, ensure_ascii=False).encode()) <= 40000, 'Selected history exceeds the same byte allowance')
    return {'hits': [result], 'excluded': [], 'runtime_authorized': False,
            'retrieval': {'name': 'operator-selected-pre-target-history', 'model_requests': 0}}


@contextmanager
def condition(session, arm, episode, entry):
    require(arm in ARMS, 'Unknown pilot condition')
    accepted = {identity: value for identity, value in session.registry.load(KIND).items() if value.get('review') == 'accepted'}
    require(accepted == {episode: entry}, 'Historical pool changed or measured results entered the pool')
    settings = session.state()['experience_memory']['settings_revision']
    session._operate('experience_settings', {'enabled': arm != ARMS[0], 'revision': settings})
    original = session.experience.search
    if arm == ARMS[1]: session.experience.search = lambda issue: selected_history(session, episode, entry)
    try:
        yield
    finally:
        session.experience.search = original


def measure(root, manifest, reviewed):
    ready = json.loads((root / 'history-ready.json').read_bytes())
    require(reviewed == ready['review_sha256'], 'Review the actual prior episode before measurement')
    require(not (root / 'measurement-intent.json').exists(), 'Measurement intent exists; do not replay automatically')
    session = open_session(root, manifest)
    try:
        require(session.registry.budget() == ready['budget'] and ready['budget']['requests_used'] <= 25,
                'The original historical journal or consumption changed')
        require(provider_pin(session, manifest) == ready['provider_pin'], 'Provider configuration changed')
        episode = ready['episode']
        require(session.registry.load(KIND)[episode] == ready['entry'], 'Historical episode changed before review')
        view = next(row for row in session.experience.view()['entries'] if row['id'] == episode)
        save(root / 'measurement-intent.json', {'review_sha256': reviewed, 'arms': ARMS})
        session._operate('experience_review', {'run_id': episode, 'revision': view['revision'], 'review_status': 'accepted'})
        entry = deepcopy(session.registry.load(KIND)[episode])
        agents = json.loads((root / 'prepared/agent/tasks.json').read_bytes())
        repository = RepositoryGateway(root / 'source')
        require(repository.common_dir == root / 'source/.git' and repository.head == agents[0]['base_commit'],
                'Only the original owned clean source may transition to the target')
        repository.require_clean(repository.snapshot())
        repository.command('checkout', '--detach', agents[1]['base_commit'])
        target = repository.snapshot()
        save(root / 'historical-cutoff.json', {'episode': entry, 'target_source': target,
             'history_pool_size': 1, 'target_problem_sha256': digest(agents[1]['problem_statement'].encode())})
        durable, rows = session.provider, []
        for arm in ARMS:
            checked(root, fingerprint(manifest))
            require(repository.snapshot() == target, 'Original target changed between conditions')
            require(provider_pin(session, manifest) == ready['provider_pin'], 'Provider changed between conditions')
            require(all((root / path).is_file() and digest((root / path).read_bytes()) == value
                        for path, value in ready['native_files'].items()), 'Historical Native changed')
            before, old_attempts = session.registry.budget(), set(session.registry.load('attempt'))
            old_tasks = set(session.registry.load('pi_task'))
            session.provider = LimitedDurableProvider(durable, RequestWindow(session.registry, 25))
            save(root / (arm + '-intent.json'), {'manifest_sha256': fingerprint(manifest), 'budget_before': before})
            with condition(session, arm, episode, entry):
                result = session._operate('auto', {'issue': agents[1]['problem_statement']})
                run = json.loads((root / 'state' / result['run_id'] / 'run.json').read_bytes())
                cleanup(session, result)
            row = {'arm': arm, 'result': result, 'budget_before': before, 'budget_after': session.registry.budget(),
                   'rounds': run['rounds'], 'provider_evidence': run.get('provider_evidence', {}),
                   'attempts': {i: value for i, value in session.registry.load('attempt').items() if i not in old_attempts},
                   'candidate_tests_passed': result['workflow_status'] == 'candidate_ready'}
            new_tasks = set(session.registry.load('pi_task')) - old_tasks
            row['memory_selections'] = {kind: {i: value for i, value in session.registry.load(kind).items() if i in new_tasks}
                                       for kind in ('experience_task', 'retrieval_task')}
            row['memory_loads'] = {i: value['evidence']['memory_load'] for i, value in row['attempts'].items()
                                   if value.get('evidence', {}).get('memory_load') is not None}
            save(root / ('result-' + arm + '.json'), row)
            rows.append(row)
            require(not row['budget_after']['requests_reserved_unknown'], 'Unknown consumption stops remaining conditions')
            require(repository.snapshot() == target, 'Original target changed during repair/cleanup')
        summary = {'completed': True, 'kind': 'one-pair-development-probe', 'results': rows,
                   'budget': session.registry.budget(), 'model_benefit_established': False,
                   'limitations': manifest['limitations']}
        save(root / 'summary.json', summary)
        return {'completed': True, 'passed_conditions': sum(row['candidate_tests_passed'] for row in rows),
                'budget': summary['budget'], 'general_benefit_established': False}
    finally:
        session.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('freeze', 'history', 'measure', 'behavior-controls'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--judge-python', type=Path)
    parser.add_argument('--approved-manifest')
    parser.add_argument('--review-history')
    parser.add_argument('--behavior-checks', action='store_true', help='Freeze independent development behavior checks for a NEW pilot only')
    parser.add_argument('--history-workspace', type=Path)
    parser.add_argument('--target-workspace', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == 'behavior-controls':
        require(args.history_workspace is not None and args.target_workspace is not None,
                'Both explicit clean baseline workspaces are required')
        result = prepare_behavior_controls(root, args.history_workspace, args.target_workspace)
    elif args.action == 'freeze':
        require(args.judge_python is not None, 'Explicit pinned judge Python is required')
        result = freeze(root, args.judge_python, behavior_checks=args.behavior_checks)
    else:
        require(not args.behavior_checks, 'Behavior checks must be selected when freezing a new pilot, never added to an existing one')
        manifest = checked(root, args.approved_manifest)
        result = history(root, manifest) if args.action == 'history' else measure(root, manifest, args.review_history)
    print(json.dumps(result), flush=True)
