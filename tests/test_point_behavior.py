"""Supplementary checks and protocol isolation; synthetic modules, no host repo imports."""
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import point_behavior_probe as probe
import swebench_point_behavior as gate
import swebench_cl_evaluation as pilot


def receipt(group='preserve-imaginary'):
    rows = [{'case': case, 'passed': True, 'diagnostic': ''} for case in probe.GROUPS[group]]
    return {'kind': probe.KIND, 'base_commit': probe.BASES[pilot.IDS[1]], 'patch_sha256': gate.sha(b''),
            'group': group, 'cases': rows, 'passed': len(rows), 'total': len(rows),
            'model_requests': 0, 'official_score': False}


def images():
    return {identity: {'digest': 'swebench/sweb.eval.x86_64.sympy_1776_sympy-' + identity.rsplit('-', 1)[1]
                                   + '@sha256:' + 'a'*64, 'image_id': 'sha256:' + 'a'*64}
            for identity in probe.BASES}


@contextmanager
def fake_sympy(mode):
    state = [True]
    @contextmanager
    def evaluate(flag):
        before, state[0] = state[0], flag
        try:
            yield
        finally:
            state[0] = before
    def point(a, b, **kwargs):
        flag = kwargs.get('evaluate', state[0])
        if mode == 'attribute':
            return a.imaginary_part
        if mode == 'noncallable':
            evaluate = False
            return evaluate(a)
        if mode == 'baseline' and not state[0] and not isinstance(a, complex):
            raise ValueError('Imaginary coordinates are not permitted.')
        if isinstance(a, complex) and not (mode == 'regression' and not flag):
            raise ValueError('Imaginary coordinates are not permitted.')
        return a, b
    # The production probe runs on Linux. Keep the simulated Linux path literal
    # while testing its semantic cases on Windows; do not weaken its real guard.
    with patch.object(probe, 'Path', side_effect=lambda value: SimpleNamespace(resolve=lambda: value)), \
            patch.dict(sys.modules, {'sympy': SimpleNamespace(I=1j, Integer=int, Point=point, evaluate=evaluate,
                                                            __file__='/testbed/sympy/__init__.py')}):
        yield state


class ProbeTests(unittest.TestCase):
    def test_baseline_preserves_all_three_constraints_but_reproduces_real_issue(self):
        with fake_sympy('baseline') as state:
            self.assertTrue(all(r['passed'] for r in probe.run_cases('preserve-imaginary')))
            self.assertFalse(probe.run_cases('real-coordinates')[0]['passed'])
            self.assertTrue(state[0])

    def test_regressive_fix_passes_issue_but_fails_both_evaluate_false_constraints(self):
        with fake_sympy('regression'):
            self.assertTrue(probe.run_cases('real-coordinates')[0]['passed'])
            rows = probe.run_cases('preserve-imaginary')
            self.assertEqual([False, False, True], [r['passed'] for r in rows])
            self.assertIn('expected ValueError', rows[0]['diagnostic'])

    def test_good_candidate_passes_and_context_state_is_restored(self):
        with fake_sympy('correct') as state:
            self.assertTrue(all(r['passed'] for r in probe.run_cases('all')))
            self.assertTrue(state[0])

    def test_module_from_another_location_is_not_accepted_as_testbed_source(self):
        with fake_sympy('correct'):
            sys.modules['sympy'].__file__ = '/other/sympy/__init__.py'
            rows = probe.run_cases('preserve-imaginary')
            self.assertFalse(any(r['passed'] for r in rows))
            self.assertTrue(all('Wrong SymPy source origin' in r['diagnostic'] for r in rows))

    def test_attribute_and_noncallable_failures_are_actual_case_diagnostics(self):
        for mode, error in [('attribute', 'AttributeError'), ('noncallable', 'TypeError')]:
            with self.subTest(mode=mode), fake_sympy(mode):
                rows = probe.run_cases('preserve-imaginary')
                self.assertFalse(any(r['passed'] for r in rows))
                self.assertTrue(all(error in r['diagnostic'] for r in rows))


class ReceiptTests(unittest.TestCase):
    def validate(self, data, code=0):
        return gate.verified_result(data, code, probe.BASES[pilot.IDS[1]], gate.sha(b''), 'preserve-imaginary')

    def test_complete_success_and_consistent_failure_are_accepted_as_data(self):
        self.validate(receipt())
        failed = receipt(); failed['cases'][0].update(passed=False, diagnostic='AssertionError: expected rejection')
        failed['passed'] -= 1
        self.validate(failed, 1)

    def test_no_cases_duplicates_reordering_bad_types_and_false_green_are_rejected(self):
        mutations = [lambda r: r.update(cases=[]), lambda r: r['cases'].reverse(),
            lambda r: r['cases'].__setitem__(0, r['cases'][1]), lambda r: r.update(passed=True),
            lambda r: r.update(total=0), lambda r: r.update(base_commit='0'*40),
            lambda r: r.update(patch_sha256='0'*64), lambda r: r.update(group='all'),
            lambda r: r.update(official_score=True), lambda r: r.update(model_requests=1),
            lambda r: r['cases'][0].update(passed=1), lambda r: r['cases'][0].update(diagnostic=[]) ]
        for mutate in mutations:
            value = receipt(); mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(value)
        for code in (1, 2, True, None):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.validate(receipt(), code)

    def test_floating_foreign_or_incomplete_image_pin_fails_before_docker(self):
        value = images(); head = probe.BASES[pilot.IDS[1]]
        self.assertEqual(value[pilot.IDS[1]], gate.pinned_image(value, head))
        for image in ('swebench/example:latest', 'evil.example/repo@sha256:' + 'a'*64, None):
            bad = copy.deepcopy(value); bad[pilot.IDS[1]]['digest'] = image
            with self.assertRaises(ValueError): gate.pinned_image(bad, head)
        with self.assertRaises(ValueError): gate.pinned_image({}, head)

    def test_unknown_local_head_is_refused_without_container_or_repo_import(self):
        from repo_maintainer.demo import create_fixture
        with tempfile.TemporaryDirectory() as temp:
            source = create_fixture(Path(temp)/'repo')
            with self.assertRaisesRegex(ValueError, 'frozen SymPy base'):
                gate.snapshot(source)


class ContainerOwnershipTests(unittest.TestCase):
    def test_timeout_removes_only_owned_container_and_closes_client_without_mounts_or_host_env(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config = root/'images.json'; config.write_text(json.dumps(images()))
            snap = {'workspace': str(root/'repo'), 'common_dir': str(root/'repo/.git'),
                    'base_commit': probe.BASES[pilot.IDS[1]], 'patch': b''}
            owned = Mock(); owned.wait.side_effect = TimeoutError('synthetic timeout')
            client = Mock(); client.images.get.return_value.id = images()[pilot.IDS[1]]['image_id']
            client.containers.create.return_value = owned
            with patch.object(gate, 'snapshot', return_value=snap), \
                    patch.dict(sys.modules, {'docker': SimpleNamespace(DockerClient=lambda **kw: client)}):
                with self.assertRaises(TimeoutError): gate.evaluate(root/'repo', config, root/'out')
            owned.remove.assert_called_once_with(force=True); client.close.assert_called_once()
            args = client.containers.create.call_args.kwargs
            self.assertTrue(args['network_disabled']); self.assertEqual(['ALL'], args['cap_drop'])
            self.assertNotIn('volumes', args); self.assertNotIn('environment', args); self.assertNotIn('privileged', args)

    def test_mismatched_local_image_never_creates_container(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config = root/'images.json'; config.write_text(json.dumps(images()))
            snap = {'workspace': str(root/'repo'), 'common_dir': str(root/'repo/.git'),
                    'base_commit': probe.BASES[pilot.IDS[1]], 'patch': b''}
            client = Mock(); client.images.get.return_value.id = 'sha256:' + 'b'*64
            with patch.object(gate, 'snapshot', return_value=snap), \
                    patch.dict(sys.modules, {'docker': SimpleNamespace(DockerClient=lambda **kw: client)}):
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    gate.evaluate(root/'repo', config, root/'out')
            client.containers.create.assert_not_called(); client.close.assert_called_once()


class PilotBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'pilot'; self.root.mkdir()
        self.project = Path(self.temp.name)/'project'; self.project.mkdir()
        (self.project/'.env.mimo.local').write_text('synthetic placeholder, never read')
        (self.project/'scripts').mkdir()
        for name in ('point_behavior_probe.py', 'swebench_point_behavior.py'):
            (self.project/'scripts'/name).write_bytes(Path(gate.__file__).with_name(name).read_bytes())
        def save(name, data):
            path = self.root/name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
        self.save = save
        save('prepared/preparation.json', {'history': pilot.IDS[0], 'target': pilot.IDS[1], 'limitations': []})
        for name in ('prepared/agent/tasks.json', 'prepared/judge/tasks.json', 'judge.py', 'failure_output.py', 'environment-ready.json'):
            save(name, {})
        self.pins = images(); save('image-lock.json', self.pins)
        save('environment-controls.json', {identity: {mode: {'image': self.pins[identity], 'infrastructure_error': False,
             'valid_red_baseline': True, 'resolved': True} for mode in ('baseline', 'gold')} for identity in pilot.IDS})
        self.controls = {}
        for identity in pilot.IDS:
            self.controls[identity] = {**receipt(), 'base_commit': probe.BASES[identity], 'image': self.pins[identity],
                'source_unchanged': True, 'network_disabled': True, 'infrastructure_error': False}
        self.enterContext(patch.object(pilot, 'PROJECT', self.project))
        self.enterContext(patch.object(pilot, 'code_hashes', return_value={'synthetic': 'fixed'}))
        self.save_controls()

    def save_controls(self):
        self.save('behavior-controls.json', {'schema_version': 1, 'kind': 'point-preservation-controls-v1',
                  'source_hashes': pilot.behavior_sources(), 'baselines': self.controls,
                  'model_requests': 0, 'official_score': False})

    def test_old_default_policy_stays_official_only(self):
        result = pilot.freeze(self.root, Path(sys.executable))
        value = pilot.checked(self.root, result['manifest_sha256'])
        self.assertNotIn('behavior_preservation', value)
        self.assertNotIn('behavior-controls.json', value['files'])
        self.assertEqual(1, len(pilot.policy_commands(self.root, value)))

    def test_new_selection_binds_both_commands_controls_and_supplementary_label(self):
        result = pilot.freeze(self.root, Path(sys.executable), behavior_checks=True)
        value = pilot.checked(self.root, result['manifest_sha256'])
        commands = pilot.policy_commands(self.root, value)
        self.assertEqual(2, len(commands)); self.assertEqual(commands[0][-1], '.')
        self.assertEqual(('preserve-imaginary',), commands[1][-1:])
        self.assertIn('behavior-controls.json', value['files'])
        self.assertTrue(any('not a blind' in text for text in value['limitations']))
        self.assertFalse(value['credentials_read'])

    def test_failing_or_missing_supplementary_control_prevents_freeze(self):
        self.controls[pilot.IDS[1]]['cases'][0]['passed'] = False
        self.controls[pilot.IDS[1]]['passed'] -= 1
        self.save_controls()
        with self.assertRaises(ValueError): pilot.freeze(self.root, Path(sys.executable), behavior_checks=True)
        self.assertFalse((self.root/'pilot-manifest.json').exists())

    def test_mutated_control_is_not_silently_resealed_or_replayed(self):
        result = pilot.freeze(self.root, Path(sys.executable), behavior_checks=True)
        self.controls[pilot.IDS[0]]['network_disabled'] = False
        self.save_controls()
        with self.assertRaisesRegex(RuntimeError, 'Frozen pilot files or code changed'):
            pilot.checked(self.root, result['manifest_sha256'])

    def test_unknown_supplementary_profile_cannot_add_arbitrary_command(self):
        with self.assertRaisesRegex(RuntimeError, 'Unknown supplementary'):
            pilot.policy_commands(self.root, {'judge_python': sys.executable, 'behavior_preservation': 'run anything'})

    def test_stale_checker_cannot_reuse_earlier_green_baselines(self):
        (self.project/'scripts/point_behavior_probe.py').write_text('changed checker')
        with self.assertRaisesRegex(RuntimeError, 'checker version changed'):
            pilot.freeze(self.root, Path(sys.executable), behavior_checks=True)
        self.assertFalse((self.root/'pilot-manifest.json').exists())

    def test_control_preparation_never_opens_provider_and_binds_actual_checker_bytes(self):
        (self.root/'behavior-controls.json').unlink()
        def snap(path):
            identity = pilot.IDS[0] if str(path) == 'history' else pilot.IDS[1]
            return {'workspace': str(Path(self.temp.name)/str(path)), 'common_dir': str(Path(self.temp.name)/str(path)/'.git'),
                    'base_commit': probe.BASES[identity], 'patch': b''}
        with patch.object(gate, 'snapshot', side_effect=snap), \
                patch.object(gate, 'evaluate', side_effect=[self.controls[i] for i in pilot.IDS]) as evaluate, \
                patch.object(pilot, 'PiProvider', side_effect=AssertionError('No provider in preparation')), \
                patch.object(pilot, 'UISession', side_effect=AssertionError('No task journal in preparation')):
            value = pilot.prepare_behavior_controls(self.root, Path('history'), Path('target'))
        self.assertTrue(value['behavior_controls_ready']); self.assertEqual(2, evaluate.call_count)
        pack = json.loads((self.root/'behavior-controls.json').read_bytes())
        self.assertEqual(pilot.behavior_sources(), pack['source_hashes'])
        self.assertEqual(self.controls, pack['baselines'])
        with self.assertRaisesRegex(RuntimeError, 'intent exists'):
            pilot.prepare_behavior_controls(self.root, Path('history'), Path('target'))


if __name__ == '__main__': unittest.main()
