"""Synthetic-only immutable profile storage acceptance tests."""
import hashlib
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from repo_maintainer.model_profiles import MAX_PROFILES, ModelProfiles
from repo_maintainer.models import SafetyError
from repo_maintainer.provider_config import load_provider_config


class ModelProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.hub = SimpleNamespace(root=self.state, provider_config=self.root / 'external.env',
                                   lock=threading.RLock(), closed=False, sessions={'bound': object()},
                                   active='old-task')
        self.store = ModelProfiles(self.hub)
        self.secret = 'synthetic-private-key-do-not-echo'

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, **changes):
        return {'revision': self.store.view()['revision'], 'action': 'create', 'name': '模型 A',
                'base_url': 'https://provider.example.invalid/v1', 'model': 'fixture-model',
                'api_key': self.secret, **changes}

    def create(self, **changes):
        return self.store.create(self.payload(**changes))

    def legacy(self, key=None):
        self.hub.provider_config.write_text(
            f'PI_MAINTENANCE_API_KEY="{key or self.secret}"\n'
            'PI_MAINTENANCE_BASE_URL="https://legacy.example.invalid/v1"\n'
            'PI_MAINTENANCE_MODEL="legacy-fixture"\n', encoding='utf-8')

    def set_default(self, profile_id):
        return self.store.set_default({'revision': self.store.view()['revision'],
                                       'action': 'set_default', 'profile_id': profile_id})

    def test_constructor_get_and_failed_resolution_have_no_side_effects(self):
        before = list(self.root.rglob('*'))
        view = self.store.view()
        self.assertEqual(before, list(self.root.rglob('*')))
        self.assertIsNone(view['default_profile_id'])
        self.assertFalse(view['profiles'][0]['selectable'])
        with self.assertRaises(SafetyError):
            self.store.resolve()
        self.assertEqual(before, list(self.root.rglob('*')))

    def test_create_persist_reopen_and_public_secret_boundary(self):
        with patch('repo_maintainer.pi_provider.subprocess.Popen') as process:
            view = self.create()
            process.assert_not_called()
        profile_id = view['created_profile_id']
        self.assertRegex(profile_id, r'^[0-9a-f]{32}$')
        self.assertEqual(profile_id, view['default_profile_id'])
        resolved = self.store.resolve()
        self.assertEqual(self.secret, load_provider_config(resolved['path'])['PI_MAINTENANCE_API_KEY'])
        self.assertEqual(hashlib.sha256(resolved['path'].read_bytes()).hexdigest(), resolved['config_sha256'])
        public = json.dumps(view)
        for private in (self.secret, resolved['config_sha256'], str(resolved['path']), 'config_sha256'):
            self.assertNotIn(private, public)
        self.assertEqual({'id', 'name', 'model', 'base_url', 'key_set', 'legacy', 'selectable', 'archived',
                          'context_window_tokens', 'max_output_tokens', 'limits_explicit',
                          'reasoning_protocol', 'reasoning_effort', 'reasoning_explicit', 'reasoning_levels'},
                         set(view['profiles'][1]))
        reopened = ModelProfiles(self.hub)
        self.assertEqual(self.store.view(), reopened.view())
        self.assertEqual(resolved, reopened.resolve())
        self.assertFalse(self.hub.provider_config.exists())
        if os.name != 'nt':
            self.assertEqual(0o600, resolved['path'].stat().st_mode & 0o777)
            self.assertEqual(0o600, self.store.catalog_path.stat().st_mode & 0o777)
            self.assertEqual(0o700, self.store.root.stat().st_mode & 0o777)

    def test_explicit_legacy_is_referenced_never_copied_or_written_and_stays_default(self):
        self.legacy()
        before = self.hub.provider_config.read_bytes()
        self.assertEqual('legacy', self.store.view()['default_profile_id'])
        self.assertEqual(self.hub.provider_config, self.store.resolve()['path'])
        self.assertTrue(self.store.resolve()['legacy'])
        created = self.create()
        self.assertEqual('legacy', created['default_profile_id'])
        self.assertEqual(before, self.hub.provider_config.read_bytes())
        self.assertEqual(1, len(list(self.store.root.glob('*.env'))))

    def test_original_first_run_can_initialize_legacy_after_constructor(self):
        self.assertIsNone(self.store.view()['default_profile_id'])
        self.legacy()
        self.assertEqual('legacy', self.store.view()['default_profile_id'])
        self.assertFalse(self.store.root.exists())

    def test_invalid_legacy_allows_first_new_profile_to_become_default(self):
        self.hub.provider_config.write_text('bad-data', encoding='utf-8')
        view = self.create()
        self.assertEqual(view['created_profile_id'], view['default_profile_id'])
        self.assertEqual('bad-data', self.hub.provider_config.read_text())

    def test_pinned_snapshot_parser_retains_original_provider_format_and_validation(self):
        self.hub.provider_config.write_text(
            "\ufeff# synthetic legacy file\n\nPI_MAINTENANCE_API_KEY = 'quoted-key'\n"
            'PI_MAINTENANCE_BASE_URL = https://legacy.example.invalid/v1\n'
            'PI_MAINTENANCE_MODEL = "legacy-fixture"\n', encoding='utf-8')
        self.assertEqual(load_provider_config(self.hub.provider_config),
                         self.store._provider(self.hub.provider_config)[0])
        for invalid in ('PI_MAINTENANCE_API_KEY=second\n', 'UNEXPECTED_FIELD=secret\n'):
            original = self.hub.provider_config.read_bytes()
            self.hub.provider_config.write_bytes(original + invalid.encode())
            self.assertFalse(self.store.view()['profiles'][0]['selectable'])
            self.hub.provider_config.write_bytes(original)
        self.create(api_key='embedded"quote\'key')
        self.assertEqual('embedded"quote\'key', load_provider_config(self.store.resolve(
            self.store.view()['profiles'][1]['id'])['path'])['PI_MAINTENANCE_API_KEY'])

    def test_complete_duplicate_reuses_original_id_revision_and_immutable_bytes(self):
        first = self.create()
        path = self.store.resolve()['path']
        before = path.read_bytes()
        duplicate = self.create(name='another display name')
        self.assertEqual(first['created_profile_id'], duplicate['created_profile_id'])
        self.assertEqual(first['revision'], duplicate['revision'])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(1, len(list(self.store.root.glob('*.env'))))
        changed = self.create(api_key='different-synthetic-key')
        self.assertNotEqual(first['created_profile_id'], changed['created_profile_id'])

    def test_legacy_duplicate_does_not_create_storage_or_new_budget_identity(self):
        self.legacy()
        view = self.create(base_url='https://legacy.example.invalid/v1', model='legacy-fixture')
        self.assertEqual('legacy', view['created_profile_id'])
        self.assertFalse(self.store.root.exists())

    def test_default_switch_does_not_modify_configs_and_old_explicit_id_still_resolves(self):
        first = self.create()['created_profile_id']
        second = self.create(model='second-fixture')['created_profile_id']
        first_path, second_path = self.store.resolve(first)['path'], self.store.resolve(second)['path']
        before = [first_path.read_bytes(), second_path.read_bytes()]
        view = self.set_default(second)
        self.assertEqual(second, view['default_profile_id'])
        self.assertEqual(first, self.store.resolve(first)['id'])
        self.assertEqual(second, ModelProfiles(self.hub).resolve()['id'])
        self.assertEqual(before, [first_path.read_bytes(), second_path.read_bytes()])

    def test_unknown_ids_never_fallback_or_create_state(self):
        self.legacy()
        for value in ('unknown', 'a' * 32, '../external.env', '', 1):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                self.store.resolve(value)
        self.assertFalse(self.store.root.exists())

    def test_stale_revision_and_concurrent_create_only_one_write_wins(self):
        payload = self.payload()
        def save(model):
            try:
                self.store.create({**payload, 'model': model})
                return 'saved'
            except SafetyError:
                return 'stale'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(['saved', 'stale'], pool.map(save, ['A', 'B']))
        with self.assertRaises(SafetyError):
            self.store.create(payload)
        self.assertEqual(1, len(list(self.store.root.glob('*.env'))))

    def test_stale_set_default_refuses_and_closed_hub_refuses(self):
        first = self.create()['created_profile_id']
        stale = {'revision': self.store.view()['revision'], 'action': 'set_default', 'profile_id': first}
        second = self.create(model='second')['created_profile_id']
        with self.assertRaises(SafetyError):
            self.store.set_default(stale)
        self.hub.closed = True
        with self.assertRaises(SafetyError):
            self.set_default(second)

    def test_validator_and_payload_boundaries_never_echo_keys_or_write(self):
        invalid = [{'base_url': 'http://unsafe.invalid'}, {'base_url': 'https://u:p@host.invalid'},
                   {'base_url': 'https://host.invalid/?key='+self.secret}, {'base_url': 'https://host.invalid:bad'},
                   {'model': 'line\nsecret'}, {'api_key': ''}, {'api_key': 'x' * 8193},
                   {'name': ' '}, {'name': 'x' * 81}, {'name': 'name\x00'}, {'extra': self.secret},
                   {'action': 'delete'}, {'model': 42}]
        for changes in invalid:
            with self.subTest(changes=list(changes)), self.assertRaises(SafetyError) as error:
                self.store.create(self.payload(**changes))
            self.assertNotIn(self.secret, str(error.exception))
        self.assertFalse(self.store.root.exists())

    def test_tampered_immutable_config_is_unselectable_and_cannot_refresh_budget(self):
        first = self.create()['created_profile_id']
        path = self.store.resolve(first)['path']
        path.write_bytes(path.read_bytes().replace(b'fixture-model', b'tampered-model'))
        self.assertFalse(self.store.view()['profiles'][1]['selectable'])
        for operation in (lambda: self.store.resolve(first), lambda: self.set_default(first), self.create):
            with self.assertRaises(SafetyError):
                operation()
        self.assertEqual(1, len(list(self.store.root.glob('*.env'))))

    def test_missing_config_is_unselectable_and_does_not_fallback_to_legacy(self):
        self.legacy()
        profile_id = self.create()['created_profile_id']
        path = self.store.resolve(profile_id)['path']
        self.set_default(profile_id)
        path.unlink()
        self.assertEqual(profile_id, self.store.view()['default_profile_id'])
        self.assertFalse(self.store.view()['profiles'][1]['selectable'])
        with self.assertRaises(SafetyError):
            self.store.resolve()

    def test_lost_catalog_refuses_recreation_instead_of_minting_new_budget_identity(self):
        profile_id = self.create()['created_profile_id']
        config = self.store.resolve(profile_id)['path']
        original = config.read_bytes()
        payload = self.payload()
        self.store.catalog_path.unlink()
        reopened = ModelProfiles(self.hub)
        for operation in (self.store.view, reopened.view, lambda: reopened.resolve(profile_id),
                          lambda: reopened.create(payload)):
            with self.assertRaises(SafetyError):
                operation()
        self.assertEqual(original, config.read_bytes())
        self.assertEqual(1, len(list(self.store.root.glob('*.env'))))

    def test_removed_catalog_entry_and_orphan_config_refuse_new_identity(self):
        profile_id = self.create()['created_profile_id']
        path = self.store.resolve(profile_id)['path']
        original = path.read_bytes()
        payload = self.payload()
        catalog = json.loads(self.store.catalog_path.read_bytes())
        catalog.update(profiles=[], default_profile_id=None)
        self.store.catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
        for operation in (self.store.view, lambda: self.store.resolve(profile_id),
                          lambda: self.store.create(payload)):
            with self.assertRaises(SafetyError):
                operation()
        self.assertEqual(original, path.read_bytes())
        self.assertEqual(1, len(list(self.store.root.glob('*.env'))))

    def test_failed_catalog_replace_leaves_old_bytes_and_removes_unpublished_secret(self):
        self.create()
        before = self.store.catalog_path.read_bytes()
        with patch('repo_maintainer.model_profiles.os.replace', side_effect=OSError(self.secret)):
            with self.assertRaises(SafetyError) as error:
                self.create(model='changed')
        self.assertNotIn(self.secret, str(error.exception))
        self.assertEqual(before, self.store.catalog_path.read_bytes())
        self.assertFalse(list(self.store.root.glob('*.tmp')))
        self.assertEqual(1, len(list(self.store.root.glob('*.env'))))

    def test_first_write_failure_cleans_secret_and_allows_explicit_retry(self):
        payload = self.payload()
        with patch('repo_maintainer.model_profiles.os.replace', side_effect=OSError(self.secret)):
            with self.assertRaises(SafetyError):
                self.store.create(payload)
        self.assertFalse(list(self.store.root.iterdir()))
        self.assertEqual(payload['revision'], self.store.view()['revision'])
        self.assertTrue(self.store.create(payload)['created_profile_id'])

    def test_uncertain_replace_retains_published_config_instead_of_breaking_catalog(self):
        replace = os.replace
        def uncertain(source, target):
            replace(source, target)
            raise OSError(self.secret)
        with patch('repo_maintainer.model_profiles.os.replace', side_effect=uncertain):
            with self.assertRaises(SafetyError):
                self.create()
        self.assertTrue(self.store.resolve()['path'].is_file())
        self.assertFalse(list(self.store.root.glob('*.tmp')))

    def test_catalog_size_schema_duplicate_fields_and_traversal_are_rejected(self):
        self.create()
        before = self.store.catalog_path.read_bytes()
        malformed = [b'x' * 65537, b'{"schema":1,"schema":1}', b'[]', b'[' * 1500 + b']' * 1500]
        data = json.loads(before)
        data['profiles'][0]['id'] = '../external'
        malformed.append(json.dumps(data).encode())
        duplicate = json.loads(before)
        duplicate['profiles'].append({**duplicate['profiles'][0], 'id': 'f' * 32})
        malformed.append(json.dumps(duplicate).encode())
        for content in malformed:
            self.store.catalog_path.write_bytes(content)
            with self.subTest(size=len(content)), self.assertRaises(SafetyError):
                self.store.view()
        self.store.catalog_path.write_bytes(before)
        self.assertTrue(self.store.resolve()['path'].is_file())

    def test_regular_file_boundaries_reject_directory_provider_and_catalog(self):
        self.hub.provider_config.mkdir()
        self.assertFalse(self.store.view()['profiles'][0]['selectable'])
        self.store.root.mkdir()
        self.store.catalog_path.mkdir()
        with self.assertRaises(SafetyError):
            self.store.view()

    def test_junction_boundary_refuses_reads_and_writes_without_side_effects(self):
        payload = self.payload()
        with patch('repo_maintainer.dashboard.is_junction', side_effect=lambda p: p == self.store.root):
            for operation in (self.store.view, lambda: self.store.resolve('legacy'),
                              lambda: self.store.create(payload)):
                with self.assertRaises(SafetyError):
                    operation()
        self.assertFalse(self.store.root.exists())

    def test_symlink_config_is_unselectable_and_never_followed(self):
        first = self.create()['created_profile_id']
        path = self.store.resolve(first)['path']
        target = self.root / 'target.env'
        target.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest('OS does not permit creating a synthetic symlink')
        self.assertFalse(self.store.view()['profiles'][1]['selectable'])
        with self.assertRaises(SafetyError):
            self.store.resolve(first)

    def test_profile_limit_is_bounded_but_existing_duplicate_remains_reusable(self):
        original = None
        for number in range(MAX_PROFILES):
            view = self.create(model=f'fixture-{number}')
            original = original or view['created_profile_id']
        with self.assertRaises(SafetyError):
            self.create(model='one-too-many')
        self.assertEqual(original, self.create(model='fixture-0')['created_profile_id'])
        self.assertEqual(MAX_PROFILES, len(list(self.store.root.glob('*.env'))))


if __name__ == '__main__':
    unittest.main()
