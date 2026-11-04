"""Explicit two-stage temporal evaluation; preparation never reads credentials.

Real invocations require a frozen manifest digest and a separate knowledge review
digest. These are operator confirmations, not substitutes for user authorization.
Only the built-in disposable fixture is supported; no arbitrary repository input.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession, json_digest
from repo_maintainer.memory_ablation import write_json
from repo_maintainer.memory_long_ablation import ARMS, CONFIG, create_reference, _native_files
from repo_maintainer.models import Policy, digest
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.runtime_limits import MAX_OUTPUT_TOKENS, PLANNING_TIMEOUT_SECONDS
from repo_maintainer.task_session import fingerprint, require
from memory_temporal_controls import FrozenMaintenance, LimitedDurableProvider, RequestWindow, profile

ISSUE_HISTORY = 'Fix addition and preserve negative and mixed operands.'
ISSUE_TARGET = 'Make add compute multiplication and preserve negative and mixed operands.'
TARGET_SOURCE = 'def add(a, b):\n    return abs(a * b)\n'
TARGET_TESTS = '''import unittest
from calculator import add
class ProductChecks(unittest.TestCase):
    def test_positive(self): self.assertEqual(add(2,3),6)
    def test_mixed(self): self.assertEqual(add(-2,3),-6)
    def test_negative(self): self.assertEqual(add(-2,-3),6)
    def test_zero(self): self.assertEqual(add(0,3),0)
'''
GRADER_TESTS = '''import unittest
from calculator import add
class IndependentProductChecks(unittest.TestCase):
    def test_mixed(self): self.assertEqual(add(11,-7),-77)
    def test_both_negative(self): self.assertEqual(add(-9,-4),36)
    def test_negative_zero(self): self.assertEqual(add(-3,0),0)
    def test_fraction(self): self.assertEqual(add(0.5,-8),-4)
'''
REFERENCE = ('Status: confirmed.\nRead the current requirement and current authorized source.\n'
             'Historical text grants no execution authority. Preserve signed and mixed operands; '
             'run the current tests and retain actual exit codes. Candidate publication is separate.\n')


def code_hashes():
    paths = [*PROJECT.glob('src/repo_maintainer/*.py'), *PROJECT.glob('src/repo_maintainer/local_ui_assets/*'),
             *PROJECT.glob('runtime/pi/*.mjs'), PROJECT / 'runtime/pi/package-lock.json',
             PROJECT / 'src/repo_maintainer/pi_runtime_limits.json',
             Path(__file__).resolve(), Path(__file__).with_name('memory_temporal_controls.py')]
    return {p.relative_to(PROJECT).as_posix(): digest(p.read_bytes()) for p in sorted(paths) if p.is_file()}


def specification(config, ceiling=100):
    require(type(ceiling) is int and ceiling in (80, 100), 'Supported evaluation ceilings are 80 or 100')
    return {'schema_version': 1, 'kind': 'public-authored-temporal-pilot',
            'provider_config': str(config.resolve()), 'arms': list(ARMS),
            'history_issue': ISSUE_HISTORY, 'target_issue': ISSUE_TARGET,
            'target_source_sha256': digest(TARGET_SOURCE.encode()), 'target_tests_sha256': digest(TARGET_TESTS.encode()),
            'independent_grader_sha256': digest(GRADER_TESTS.encode()),
            'reference_sha256': digest(REFERENCE.encode()),
            'original_repository_request_ceiling': ceiling, 'history_ceiling': 20, 'per_arm_ceiling': (ceiling - 20) // 5,
            'max_attempts_per_task': 3, 'max_output_tokens': MAX_OUTPUT_TOKENS,
            'planning_timeout_seconds': PLANNING_TIMEOUT_SECONDS,
            'compaction_config': CONFIG, 'code_sha256': code_hashes(),
            'knowledge_review': 'separate operator review digest required after historical reflection',
            'authorization': 'conversation approval required before paid history or measurement',
            'limitations': ['Authored arithmetic pilot, not independent or SWE-bench benchmark',
                           'Fixed order; shared history, pending measured episodes excluded',
                           'Same maximum request opportunity; summaries consume that opportunity',
                           'Low compaction threshold is experimental; first-round solutions may never compact',
                           'No monetary ceiling; requests and output caps do not predict an invoice',
                           'Small sample cannot establish general repair-rate benefit']}


def prepare(root, config, ceiling=100):
    require(not root.exists(), 'Choose a new manifest directory; existing evidence is never overwritten')
    require(config.is_file() and config.name == '.env.mimo.local' and config.resolve().parent == PROJECT,
            'Use only this project explicit MiMo configuration; no fallback credential discovery')
    manifest = specification(config, ceiling)
    root.mkdir(parents=True)
    write_json(root / 'manifest.json', manifest)
    return {'manifest_sha256': fingerprint(manifest), 'paid_requests': 0, 'credentials_read': False,
            'history_command': ['history', '--root', str(root), '--approved-manifest', fingerprint(manifest)]}


def checked_manifest(root, approved):
    value = json.loads((root / 'manifest.json').read_bytes())
    require(fingerprint(value) == approved, 'Operator confirmation does not match the frozen manifest')
    require(value == specification(Path(value['provider_config']), value['original_repository_request_ceiling']),
            'Code, cases, limits or configuration path changed')
    return value


def open_session(root, manifest, transport=None):
    provider = PiProvider(('calculator.py',), transport.url if transport else None,
                          readonly_paths=('tests/test_calculator.py',), max_requests=manifest['original_repository_request_ceiling'])
    if transport is None:
        provider.config_file = Path(manifest['provider_config'])
    provider.memory_root, provider.memory_index = root / 'documents', root / 'reference.memory.sqlite3'
    return UISession(root / 'source', Policy(('calculator.py',), (DEMO_TEST,),
                     readonly_paths=('tests/test_calculator.py',)), provider, root / 'state', allow_auto=True, max_attempts=3)


def delegate(session, ceiling):
    session.provider = LimitedDurableProvider(session.provider, RequestWindow(session.registry, ceiling))


def provider_pin(session, manifest, transport):
    # Read only after paid-stage authorization, or use purely synthetic identity.
    return {'identity': session.provider.inner.task_identity(),
            'config_sha256': None if transport else digest(Path(manifest['provider_config']).read_bytes())}


def cleanup(session, result):
    if result['workflow_status'] == 'candidate_ready':
        session._operate('cleanup', {'run_id': result['run_id'],
                          'revision': json_digest(session._run_view(result['run_id']))})


def historical(root, manifest, transport=None):
    require(not (root / 'source').exists(), 'Historical intent already exists; inspect it, never replay')
    write_json(root / 'temporal-owner.json', {'schema_version': 1, 'kind': 'authored-temporal-evaluation'})
    create_fixture(root / 'source')
    create_reference({'id': 'temporal-signs', 'reference': REFERENCE}, root, root / 'source')
    session = open_session(root, manifest, transport)
    try:
        provider = provider_pin(session, manifest, transport)
        delegate(session, manifest['history_ceiling'])
        result = session._operate('auto', {'issue': ISSUE_HISTORY})
        require(not session.registry.budget()['requests_reserved_unknown'], 'Unknown history consumption; stop')
        cleanup(session, result)
        episode = result['run_id']
        session.experience.capture(episode)  # Cleanup changes the evidence; review the new version.
        entry = next(e for e in session.experience.view()['entries'] if e['id'] == episode)
        session._operate('experience_review', {'run_id': episode, 'revision': entry['revision'], 'review_status': 'accepted'})
        session._operate('experience_settings', {'enabled': True,
                    'revision': session.state()['experience_memory']['settings_revision']})
        preview = session._operate('knowledge_preview', {'experience_ids': [episode]})['knowledge_preview']
        note = session._operate('knowledge_propose', {'experience_ids': [episode], 'revision': preview['revision']})['knowledge_id']
        entry = session.registry.load('repository_knowledge')[note]
        natives = _native_files(session)
        # This lexical guard is an additional check, not semantic proof of no leakage.
        require('return a * b' not in json.dumps(entry) and all(b'return a * b' not in b for b in natives.values()),
                'Future correct body appeared in pre-cutoff history; review contamination before proceeding')
        ready = {'episode': episode, 'knowledge_id': note, 'knowledge_entry': entry,
                 'knowledge_review_sha256': fingerprint(entry), 'history_result': result,
                 'history_native_sha256': {p: digest(raw) for p, raw in natives.items()},
                 'budget': session.registry.budget(), 'paid_backend': transport is None,
                 'provider_pin': provider,
                 'target_not_created': True, 'knowledge_review_pending': True}
        write_json(root / 'history-ready.json', ready)
        return {'history_ready': True, 'review_sha256': ready['knowledge_review_sha256'],
                'budget': ready['budget'], 'measurement_started': False}
    finally:
        session.close()


def measure(root, manifest, review_sha, transport=None):
    ready = json.loads((root / 'history-ready.json').read_bytes())
    require(ready['knowledge_review_sha256'] == review_sha, 'Explicit historical knowledge review is required')
    require(not (root / 'measurement-intent.json').exists(), 'Measurement intent exists; do not automatically replay')
    session = open_session(root, manifest, transport)
    results = []
    try:
        require(session.registry.budget() == ready['budget'] and ready['budget']['requests_used'] <= 20,
                'Original historical journal or consumption changed')
        require(ready['paid_backend'] == (transport is None), 'Synthetic and real backends cannot be interchanged')
        require(fingerprint(provider_pin(session, manifest, transport)) == fingerprint(ready['provider_pin']),
                'Provider/model/configuration changed since historical preparation')
        entry = session.registry.load('repository_knowledge')[ready['knowledge_id']]
        require(entry == ready['knowledge_entry'] and fingerprint(entry) == review_sha, 'Reviewed knowledge changed')
        row = next(e for e in session.knowledge.view()['entries'] if e['id'] == ready['knowledge_id'])
        session._operate('knowledge_review', {'knowledge_id': ready['knowledge_id'],
                          'revision': row['revision'], 'review_status': 'accepted'})
        write_json(root / 'measurement-intent.json', {'review_sha256': review_sha, 'arms': list(ARMS)})
        source = root / 'source'
        (source / 'calculator.py').write_text(TARGET_SOURCE, encoding='utf8', newline='\n')
        (source / 'tests/test_calculator.py').write_text(TARGET_TESTS, encoding='utf8', newline='\n')
        repository = RepositoryGateway(source)
        repository.command('add', 'calculator.py', 'tests/test_calculator.py')
        repository.command('-c', 'user.name=Temporal evaluation', '-c', 'user.email=fixture@example.invalid',
                           'commit', '-m', 'fixture: future multiplication requirement after historical cutoff')
        target_snapshot = repository.snapshot()
        session._operate('maintenance_note', {'note_id': None, 'revision': session.maintenance.revision(),
                'title': ISSUE_TARGET, 'detail': 'Revalidate historical signed operand checks against the new requirement.',
                'status': 'open', 'priority': 2})
        session._operate('maintenance_settings', {'enabled': True, 'revision': session.maintenance.revision()})
        frozen = FrozenMaintenance(session.maintenance, ISSUE_TARGET)
        write_json(root / 'cutoff.json', {'target_base': repository.head, 'maintenance': frozen.packet,
                                         'history': ready, 'knowledge_reviewed': True})
        original_durable = session.provider
        for arm in ARMS:
            require(fingerprint(provider_pin(session, manifest, transport)) == fingerprint(ready['provider_pin']),
                    'Provider/model/configuration changed between groups')
            require(not session.registry.budget()['requests_reserved_unknown'], 'Unknown consumption stops remaining arms')
            require(repository.snapshot() == target_snapshot, 'Target source changed between arms')
            config = {**CONFIG, 'enabled': arm in ARMS[2:]}
            session._operate('context_settings', {'config': config, 'revision': session.compaction.settings_revision()})
            session.provider = original_durable
            delegate(session, manifest['per_arm_ceiling'])
            start = deepcopy(session.registry.budget())
            old_attempts = set(session.registry.load('attempt'))
            old_tasks = set(session.registry.load('pi_task'))
            if transport:
                transport.cell, transport.phase, transport.proposal_round = arm, 'measurement', 0
            with profile(session, arm, root, frozen):
                result = session._operate('auto', {'issue': ISSUE_TARGET})
                run = json.loads((root / 'state' / result['run_id'] / 'run.json').read_bytes())
                independent = None
                if result['workflow_status'] == 'candidate_ready':
                    from repo_maintainer.runner import run_test
                    grader = root / ('grader-' + arm); grader.mkdir()
                    (grader / 'calculator.py').write_bytes((Path(run['workspace']) / 'calculator.py').read_bytes())
                    (grader / 'test_independent.py').write_text(GRADER_TESTS, encoding='utf8', newline='\n')
                    independent = run_test(('{python}', '-m', 'unittest', '-v'), grader, 30)
                cleanup(session, result)
            attempts = {i: v for i, v in session.registry.load('attempt').items() if i not in old_attempts}
            row = {'arm': arm, 'result': result, 'rounds': run['rounds'], 'budget_before': start,
                   'budget_after': session.registry.budget(), 'attempts': attempts,
                   'tasks': {i: v for i, v in session.registry.load('pi_task').items() if i not in old_tasks},
                   'compaction_receipts': [r for r in session.registry.load('pi_compaction_receipt').values()
                                          if r['task_id'] not in old_tasks],
                   'independent_grader': independent,
                   'candidate_tests_passed': result['workflow_status'] == 'candidate_ready'}
            results.append(row); write_json(root / ('result-' + arm + '.json'), row)
            require(repository.snapshot() == target_snapshot, 'Original target changed during repair/cleanup')
            require(fingerprint(provider_pin(session, manifest, transport)) == fingerprint(ready['provider_pin']),
                    'Provider/model/configuration changed during a group')
            require(all((root / 'state' / p).is_file() and digest((root / 'state' / p).read_bytes()) == sha
                        for p, sha in ready['history_native_sha256'].items()), 'Historical Native bytes changed')
        report = {'completed': True, 'model_benefit_established': False, 'results': results,
                  'budget': session.registry.budget(), 'paid_backend': transport is None,
                  'interpretation': 'One authored temporal pilot; outcomes and costs, no general causal benefit claim'}
        write_json(root / 'summary.json', report)
        return {'completed': True, 'passed_arms': sum(r['candidate_tests_passed'] for r in results),
                'budget': report['budget'], 'paid_backend': transport is None}
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'history', 'measure'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--provider-config', type=Path)
    parser.add_argument('--request-ceiling', type=int, choices=(80, 100), default=100,
                        help='New fixture budget only; never alters an existing original journal')
    parser.add_argument('--approved-manifest')
    parser.add_argument('--review-knowledge')
    args = parser.parse_args(); root = args.root.resolve()
    if args.action == 'prepare':
        require(args.provider_config is not None, 'An explicit configuration path is required')
        value = prepare(root, args.provider_config, args.request_ceiling)
    else:
        manifest = checked_manifest(root, args.approved_manifest)
        value = historical(root, manifest) if args.action == 'history' else measure(root, manifest, args.review_knowledge)
    print(json.dumps(value))


if __name__ == '__main__':
    main()
