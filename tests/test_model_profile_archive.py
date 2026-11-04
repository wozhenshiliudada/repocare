"""Synthetic-only archive compatibility: retained identities, bytes and old bindings."""
import json
import os
import time
import unittest
import uuid
from unittest.mock import patch

from repo_maintainer.assistant import AssistantControl
from repo_maintainer.model_profiles import ModelProfiles
from repo_maintainer.models import SafetyError
import test_model_profiles as storage
import test_model_profile_workspaces as workspaces


class ModelProfileArchiveTests(unittest.TestCase):
    setUp = storage.ModelProfilesTests.setUp
    payload = storage.ModelProfilesTests.payload
    create = storage.ModelProfilesTests.create
    legacy = storage.ModelProfilesTests.legacy
    set_default = storage.ModelProfilesTests.set_default

    def tearDown(self):
        if getattr(self, 'control', None): self.control.close()
        self.temp.cleanup()

    def change(self, action, profile_id, **extra):
        return getattr(self.store, action)({'revision': self.store.view()['revision'],
                                           'action': action, 'profile_id': profile_id, **extra})

    def test_old_catalog_read_and_resolution_do_not_migrate_bytes(self):
        profile = self.create()['created_profile_id']
        before = self.store.catalog_path.read_bytes()
        self.assertNotIn('archived_profile_ids', json.loads(before))
        reopened = ModelProfiles(self.hub)
        self.assertTrue(all(not row['archived'] for row in reopened.view()['profiles']))
        self.assertEqual(profile, reopened.resolve()['id'])
        self.assertEqual(profile, reopened.resolve_selectable()['id'])
        self.assertEqual(before, self.store.catalog_path.read_bytes())

    def test_archive_default_fallback_restore_and_legacy_preserve_exact_env_bytes(self):
        self.legacy()
        first = self.create()['created_profile_id']
        second = self.create(model='second')['created_profile_id']
        paths = [self.hub.provider_config, self.store.resolve(first)['path'], self.store.resolve(second)['path']]
        original = {path: path.read_bytes() for path in paths}
        for profile, expected in [('legacy', first), (first, second), (second, None)]:
            view = self.change('archive', profile)
            self.assertEqual(expected, view['default_profile_id'])
            row = next(row for row in view['profiles'] if row['id'] == profile)
            self.assertTrue(row['archived']); self.assertFalse(row['selectable'])
            self.assertEqual(profile, self.store.resolve(profile)['id'])
            with self.assertRaises(SafetyError):
                self.store.resolve_selectable(profile)
        with self.assertRaises(SafetyError):
            self.store.resolve()
        view = self.change('restore', first)
        self.assertIsNone(view['default_profile_id'])
        self.assertEqual(first, self.store.resolve_selectable(first)['id'])
        self.set_default(first)
        self.assertEqual(first, ModelProfiles(self.hub).resolve()['id'])
        self.assertEqual(original, {path: path.read_bytes() for path in paths})

    def test_archived_identity_cannot_be_selected_or_recreated_for_another_budget(self):
        first = self.create()['created_profile_id']
        self.change('archive', first)
        before = self.store.catalog_path.read_bytes()
        for operation in (lambda: self.set_default(first), self.create,
                          lambda: self.create(model='variant', api_key='', source_profile_id=first),
                          lambda: self.store.reasoning_variant({'revision': self.store.view()['revision'],
                              'action': 'reasoning_variant', 'profile_id': first, 'reasoning_effort': 'high'})):
            with self.assertRaises(SafetyError): operation()
        self.assertEqual(before, self.store.catalog_path.read_bytes())
        self.assertEqual(1, len(list(self.store.root.glob('*.env'))))
        self.change('restore', first)
        self.assertEqual(first, self.create()['created_profile_id'])

    def test_strict_payload_stale_unknown_closed_and_noop_semantics(self):
        first = self.create()['created_profile_id']
        old_revision = self.store.view()['revision']
        self.change('archive', first)
        before = self.store.catalog_path.read_bytes()
        for value in ({'revision': old_revision, 'action': 'restore', 'profile_id': first},
                      {'revision': self.store.view()['revision'], 'action': 'restore', 'profile_id': 'f'*32},
                      {'revision': self.store.view()['revision'], 'action': 'restore', 'profile_id': first, 'extra': True},
                      {'revision': self.store.view()['revision'], 'action': 'restore'},
                      {'revision': self.store.view()['revision'], 'action': 'restore', 'profile_id': []}):
            with self.assertRaises(SafetyError): self.store.restore(value)
        self.change('archive', first)
        self.assertEqual(before, self.store.catalog_path.read_bytes())
        self.hub.closed = True
        with self.assertRaises(SafetyError): self.change('restore', first)
        self.assertEqual(before, self.store.catalog_path.read_bytes())

    def test_catalog_archive_validation_and_failed_save_do_not_damage_env(self):
        profile = self.create()['created_profile_id']
        original = self.store.catalog_path.read_bytes()
        path = self.store.resolve(profile)['path']; env = path.read_bytes()
        for archived in ([profile, profile], ['f'*32], 'legacy', [None], [profile]):
            catalog = json.loads(original); catalog['archived_profile_ids'] = archived
            self.store.catalog_path.write_text(json.dumps(catalog), encoding='utf-8')
            with self.assertRaises(SafetyError): self.store.view()
        self.store.catalog_path.write_bytes(original)
        with patch('repo_maintainer.model_profiles.os.replace', side_effect=OSError('synthetic')):
            with self.assertRaises(SafetyError): self.change('archive', profile)
        self.assertEqual(original, self.store.catalog_path.read_bytes())
        self.assertEqual(env, path.read_bytes())
        request = {'revision': self.store.view()['revision'], 'action': 'archive', 'profile_id': profile}
        replace = os.replace
        def uncertain(source, target):
            replace(source, target)
            raise OSError('synthetic response loss after publish')
        with patch('repo_maintainer.model_profiles.os.replace', side_effect=uncertain):
            with self.assertRaises(SafetyError): self.store.archive(request)
        # An unknown response must not undo a published change or make stale replay succeed.
        self.assertTrue(next(row for row in self.store.view()['profiles'] if row['id']==profile)['archived'])
        with self.assertRaises(SafetyError): self.store.archive(request)
        self.assertEqual(env, path.read_bytes())

    def test_default_fallback_skips_invalid_remaining_profile(self):
        first = self.create()['created_profile_id']
        damaged = self.create(model='damaged')['created_profile_id']
        last = self.create(model='last')['created_profile_id']
        damaged_path = self.store.resolve(damaged)['path']
        damaged_path.write_bytes(damaged_path.read_bytes()+b'INVALID=1\n')
        self.assertEqual(last, self.change('archive', first)['default_profile_id'])
        self.change('archive', damaged)
        with self.assertRaises(SafetyError): self.change('restore', damaged)

    def test_existing_assistant_view_ledger_and_native_remain_readable_without_any_model_call(self):
        profile_id = self.create()['created_profile_id']
        self.hub.request_budget = 50
        self.hub.model_profiles = self.store
        control = self.control = AssistantControl(self.hub)
        def wait(action, **payload):
            result = control.submit(action, {'request_id': uuid.uuid4().hex, **payload})
            deadline = time.monotonic()+5
            while time.monotonic()<deadline:
                job = next(row for row in control.view()['jobs'] if row['id']==result['id'])
                if job['status']!='running' and not control.active: return job
                time.sleep(.01)
            self.fail('Assistant metadata operation timed out')
        with patch.object(control, '_converse', side_effect=AssertionError('No model call')):
            created = wait('create', profile_id=profile_id)
            self.assertEqual('finished', created['status'], created)
            session_id = created['session_id']
            ledger = control.executor.submit(control._ledger, self.store.resolve(profile_id)).result()
            attempt, _ = ledger.reserve(maximum=2); ledger.settle(attempt, {'requests': 2}, success=True)
            ledger.reserve(maximum=3)
            native = control.root/'retained-native.jsonl'; native.write_bytes(b'{"original":"native"}\n')
            files = {path: path.read_bytes() for path in control.root.rglob('*')
                     if path.is_file() and path.name != 'owner.lock'}
            budget = ledger.budget()
            self.change('archive', profile_id)
            self.assertEqual(session_id, control.view()['sessions'][0]['id'])
            self.assertEqual(files, {path: path.read_bytes() for path in files})
            self.assertEqual(budget, ledger.budget())
            rejected = wait('create', profile_id=profile_id)
            self.assertEqual('failed', rejected['status'])
            self.assertEqual([session_id], [row['id'] for row in control.view()['sessions']])
            target = self.create(model='unarchived-target')['created_profile_id']
            linked = wait('switch_model', session_id=session_id, profile_id=target)
            self.assertEqual('finished', linked['status'], linked)
            self.assertEqual(2, len(control.view()['sessions']))
            self.assertFalse((control.root/'models'/target).exists())
            self.assertEqual(budget, ledger.budget())
            self.change('restore', profile_id)
            self.assertEqual(budget, ledger.budget())


class ModelProfileArchiveHTTPTests(unittest.TestCase):
    setUp = workspaces.ModelProfileWorkspaceTests.setUp
    tearDown = workspaces.ModelProfileWorkspaceTests.tearDown
    start = workspaces.ModelProfileWorkspaceTests.start
    restart = workspaces.ModelProfileWorkspaceTests.restart
    profiles = workspaces.ModelProfileWorkspaceTests.profiles
    create = workspaces.ModelProfileWorkspaceTests.create
    default = workspaces.ModelProfileWorkspaceTests.default
    scope = workspaces.ModelProfileWorkspaceTests.scope
    action = workspaces.ModelProfileWorkspaceTests.action
    connect = workspaces.ModelProfileWorkspaceTests.connect
    project = workspaces.ModelProfileWorkspaceTests.project
    checkpoint = workspaces.ModelProfileWorkspaceTests.checkpoint
    assert_history = workspaces.ModelProfileWorkspaceTests.assert_history

    def post(self, action, profile_id, **extra):
        return self.client.post('/api/model-profiles', headers=self.headers,
            json={'revision': self.profiles()['revision'], 'action': action, 'profile_id': profile_id, **extra})

    def test_archive_http_security_strict_revisions_and_restore_retained_id(self):
        profile = self.create()
        original = self.hub.model_profiles.resolve(profile)['path'].read_bytes()
        body = {'revision': self.profiles()['revision'], 'action': 'archive', 'profile_id': profile}
        self.assertEqual(401, self.client.post('/api/model-profiles', json=body).status_code)
        for extra in ({'Origin':'https://foreign.invalid'}, {'Host':'foreign.invalid'}, {'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(403, self.client.post('/api/model-profiles', headers={**self.headers, **extra}, json=body).status_code)
        self.assertEqual(200, self.post('archive', profile).status_code)
        duplicate = self.client.post('/api/model-profiles', headers=self.headers,
            json={'revision': self.profiles()['revision'], 'action': 'create', 'name': 'same archived',
                  'base_url': 'https://b.example.invalid/v1', 'model': 'model-b', 'api_key': self.secret_b})
        self.assertEqual(409, duplicate.status_code)
        self.assertNotIn(self.secret_b, duplicate.text)
        self.assertEqual(409, self.client.post('/api/model-profiles', headers=self.headers, json=body).status_code)
        self.assertEqual(409, self.post('restore', profile, unexpected=True).status_code)
        self.assertEqual(409, self.post('archive', 'f'*32).status_code)
        self.assertEqual(409, self.client.post('/api/provider-models', headers=self.headers, json={'profile_id':profile}).status_code)
        restored = self.post('restore', profile)
        self.assertEqual(200, restored.status_code, restored.text)
        self.assertEqual(profile, self.hub.model_profiles.resolve_selectable(profile)['id'])
        self.assertEqual(original, self.hub.model_profiles.resolve(profile)['path'].read_bytes())
        self.assertFalse(self.hub.jobs)

    def test_legacy_archive_preserves_original_scope_task_native_and_ledger_bytes_through_restart(self):
        workspace = self.connect()
        saved = self.checkpoint(workspace)
        profile = self.create()
        project_file = self.project()/'project.json'
        originals = {path:path.read_bytes() for path in [self.config, project_file, saved['journal'], saved['native']]}
        policy = workspace.session.policy
        monitor_identity = self.hub.monitor._provider(policy).task_identity()
        self.assertEqual(200, self.post('archive', 'legacy').status_code)
        self.assertEqual(profile, self.profiles()['default_profile_id'])
        self.assert_history(workspace, saved)
        self.assertEqual(originals, {path:path.read_bytes() for path in originals})
        self.assertEqual(monitor_identity, self.hub.monitor._provider(policy).task_identity())
        failed = self.action('task_model_workspace', {'workspace_id':workspace.identity, 'profile_id':'legacy', 'confirmed':True})
        self.assertEqual('failed', failed['status'])
        with self.assertRaises(SafetyError): self.hub.connect(self.scope(profile_id='legacy'))
        self.restart()
        workspace = self.connect()  # Omitted selection resumes original archived primary.
        self.assert_history(workspace, saved)
        self.assertEqual(originals, {path:path.read_bytes() for path in originals})
        self.assertEqual(200, self.post('restore', 'legacy').status_code)
        self.assert_history(workspace, saved)

    def test_archived_saved_primary_reopens_old_bound_tasks_without_rebinding(self):
        profile = self.create()
        workspace = self.connect(profile_id=profile)
        saved = self.checkpoint(workspace)
        project = self.project()/'project.json'; original = project.read_bytes()
        self.assertEqual(200, self.post('archive', profile).status_code)
        self.assert_history(workspace, saved)
        self.restart()
        workspace = self.connect()
        self.assertEqual(profile, workspace.view()['model_profile_id'])
        self.assert_history(workspace, saved)
        self.assertEqual(original, project.read_bytes())
