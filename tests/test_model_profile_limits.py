"""Selected immutable limits, historical compatibility and real synthetic Pi transport."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from repo_maintainer.model_profiles import ModelProfiles
from repo_maintainer.models import SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.provider_config import MODEL_LIMIT_FIELDS, load_provider_config, resolve_model_limits
from repo_maintainer.pi_evidence import matches_model_limits


class ModelProfileLimitsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hub = SimpleNamespace(root=self.root, provider_config=self.root / 'legacy.env',
                                   lock=threading.RLock(), closed=False)
        self.profiles = ModelProfiles(self.hub)

    def create(self, **changes):
        return self.profiles.create({'revision': self.profiles.view()['revision'], 'action': 'create',
            'name': 'selected', 'api_key': 'synthetic-profile-limit-key',
            'base_url': 'https://provider.example.invalid/v1', 'model': 'synthetic-model', **changes})

    def test_explicit_limits_persist_in_pinned_file_and_reopen_without_credentials_in_view(self):
        view = self.create(context_window_tokens=65536, max_output_tokens=2048)
        selected = self.profiles.resolve(view['created_profile_id'])
        self.assertEqual((65536, 2048, True), tuple(selected[k] for k in
            ('context_window_tokens', 'max_output_tokens', 'limits_explicit')))
        raw = selected['path'].read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), selected['config_sha256'])
        loaded = load_provider_config(selected['path'])
        self.assertEqual('2048', loaded['PI_MAINTENANCE_MAX_OUTPUT_TOKENS'])
        self.assertEqual({key: value for key, value in view.items() if key != 'created_profile_id'}, ModelProfiles(self.hub).view())
        self.assertNotIn('synthetic-profile-limit-key', json.dumps(view))
        self.assertEqual(raw, selected['path'].read_bytes())

    def test_effective_lengths_participate_in_dedup_and_edit_is_new_immutable_profile(self):
        first = self.create(max_output_tokens=2048)
        path = self.profiles.resolve(first['created_profile_id'])['path']
        raw = path.read_bytes()
        same = self.create(name='renamed', context_window_tokens=32768, max_output_tokens=2048)
        self.assertEqual(first['created_profile_id'], same['created_profile_id'])
        self.assertEqual(first['revision'], same['revision'])
        edited = self.create(context_window_tokens=65536, max_output_tokens=2048)
        self.assertNotEqual(first['created_profile_id'], edited['created_profile_id'])
        self.assertEqual(raw, path.read_bytes())
        self.assertEqual(2, len(list(self.profiles.root.glob('*.env'))))

    def test_legacy_and_old_saved_profiles_are_not_migrated_and_keep_historical_defaults(self):
        old = self.create()
        before = {p: p.read_bytes() for p in self.profiles.root.iterdir()}
        row = ModelProfiles(self.hub).resolve(old['created_profile_id'])
        self.assertEqual((32768, 128000, False), tuple(row[k] for k in
            ('context_window_tokens', 'max_output_tokens', 'limits_explicit')))
        self.assertEqual(before, {p: p.read_bytes() for p in self.profiles.root.iterdir()})
        self.hub.provider_config.write_bytes(before[row['path']])
        original = self.hub.provider_config.read_bytes()
        self.assertFalse(self.profiles.resolve('legacy')['limits_explicit'])
        self.create(context_window_tokens=65536, max_output_tokens=2048)
        self.assertEqual(original, self.hub.provider_config.read_bytes())

    def test_nonintegers_unknown_fields_and_unsupported_combinations_write_no_state(self):
        invalid = [True, False, 4096.0, 1.2, '65536', None, float('nan'), float('inf'), 0, -1, 2**53]
        for value in invalid:
            with self.subTest(context=value), self.assertRaises(SafetyError):
                self.create(context_window_tokens=value, max_output_tokens=2048)
            self.assertFalse(self.profiles.root.exists())
        for changes in ({'context_window_tokens': 65536, 'max_output_tokens': 65536},
                        {'context_window_tokens': 3000000, 'max_output_tokens': 2**53},
                        {'max_output_tokens': True}, {'max_output_tokens': 0},
                        {'context_window_tokens': 65536}, {'unexpected': 1}):
            with self.subTest(changes=changes), self.assertRaises(SafetyError):
                self.create(**changes)
            self.assertFalse(self.profiles.root.exists())

    def test_optional_individual_values_and_decimal_file_validation(self):
        self.assertEqual(2048, self.profiles.resolve(self.create(max_output_tokens=2048)['created_profile_id'])['max_output_tokens'])
        self.assertEqual(128000, self.profiles.resolve(self.create(context_window_tokens=262144)['created_profile_id'])['max_output_tokens'])
        for raw in ('NaN', '2048.0', 'true', '01', '+2048', '1e3', ' 2048', '999999999999999999999999'):
            with self.subTest(raw=raw), self.assertRaises(SafetyError):
                resolve_model_limits({'PI_MAINTENANCE_MAX_OUTPUT_TOKENS': raw})
        partial = {'PI_MAINTENANCE_MAX_OUTPUT_TOKENS': '2048'}
        self.assertEqual(32768, resolve_model_limits(partial, context_policy={'context_window_tokens': 1048576},
            legacy_context_window_tokens=1048576)['context_window_tokens'])
        self.assertEqual(128000, resolve_model_limits({'PI_MAINTENANCE_CONTEXT_WINDOW_TOKENS': '262144'},
            legacy_max_output_tokens=64)['max_output_tokens'])

    def test_between_turn_budget_uses_selected_limits_without_rebinding_old_policy(self):
        from repo_maintainer.task_compaction import TaskCompaction
        compaction = TaskCompaction.__new__(TaskCompaction)
        selected = {'context_window_tokens': 65536, 'max_output_tokens': 2048, 'limits_explicit': True}
        compaction.planner = SimpleNamespace(provider=SimpleNamespace(effective_model_limits=lambda **_: selected))
        original = {'schema_version': 1, 'enabled': False, 'context_window_tokens': 1048576,
                    'trigger_tokens': 720000, 'keep_recent_tokens': 16000, 'summary_max_output_tokens': 128000}
        compaction.checked = lambda *_: {'revision': 0}
        compaction.policy = lambda _: original
        compaction.next_input = lambda *_: 1000
        self.assertIs(original, compaction.before_turn('id', 'issue', None))
        compaction.next_input = lambda *_: 65000
        with self.assertRaises(SafetyError):
            compaction.before_turn('id', 'issue', None)
        self.assertEqual(1048576, original['context_window_tokens'])
        self.assertEqual(128000, original['summary_max_output_tokens'])

    def test_unchanged_task_identity_and_exact_selected_receipts(self):
        old = self.profiles.resolve(self.create()['created_profile_id'])
        new = self.profiles.resolve(self.create(context_window_tokens=65536, max_output_tokens=2048)['created_profile_id'])
        provider = PiProvider(('calculator.py',))
        provider.config_file = old['path']; identity = provider.task_identity()
        provider.config_file = new['path']
        self.assertEqual(identity, provider.task_identity())
        limits = provider.effective_model_limits()
        self.assertTrue(matches_model_limits({'max_output_tokens': 2048}, limits))
        self.assertFalse(matches_model_limits({'max_output_tokens': 128000}, limits))
        self.assertFalse(matches_model_limits({}, limits))
        provider.config_file = old['path']
        self.assertTrue(matches_model_limits({}, provider.effective_model_limits()))

    def test_selected_file_is_isolated_from_ambient_lengths_before_launch(self):
        selected = self.profiles.resolve(self.create()['created_profile_id'])
        provider = PiProvider(('calculator.py',)); provider.config_file = selected['path']
        repo = SimpleNamespace(head='a' * 40, common_dir=self.root, read=lambda _: b'def add(a,b):\n return a-b\n')
        with patch.dict(os.environ, {field: '4096' for field in MODEL_LIMIT_FIELDS}), \
             patch('repo_maintainer.pi_provider.shutil.which', return_value='node'), \
             patch('repo_maintainer.pi_provider.subprocess.Popen', side_effect=RuntimeError('captured launch')) as spawn:
            with self.assertRaisesRegex(RuntimeError, 'captured launch'):
                provider.plan(repo, 'repair addition', ())
            self.assertTrue(MODEL_LIMIT_FIELDS.isdisjoint(spawn.call_args.kwargs['env']))
            self.assertFalse(provider.effective_model_limits()['limits_explicit'])

    @unittest.skipUnless(shutil.which('node'), 'Node required for real pinned Pi synthetic transport')
    def test_selected_config_reaches_real_pi_sdk_and_python_receipt_boundary(self):
        fixture = Path(__file__).resolve().parents[1] / 'runtime' / 'pi' / 'test' / 'selected-limits-fixture.mjs'
        if not (fixture.parents[1] / 'node_modules').exists():
            self.skipTest('Pinned Pi dependencies unavailable')
        repo = SimpleNamespace(head='a' * 40, common_dir=self.root,
            read=lambda _: b'def add(a,b):\n    return a - b\n')
        for context, output in ((65536, 2048), (131072, 4096)):
            selected = self.profiles.resolve(self.create(context_window_tokens=context, max_output_tokens=output)['created_profile_id'])
            provider = PiProvider(('calculator.py',), max_requests=3); provider.config_file = selected['path']
            with self.subTest(output=output), patch('repo_maintainer.pi_provider.runtime_script', return_value=fixture):
                plan = provider.plan(repo, 'Repair addition', (('python', '-m', 'unittest'),))
                self.assertIn('a + b', plan.edits[0].content)
                self.assertEqual(output, provider.evidence['max_output_tokens'])
                self.assertEqual(1, provider.requests_used)


if __name__ == '__main__':
    unittest.main()
