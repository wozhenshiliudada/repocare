import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from repo_maintainer.git import RepositoryGateway, git_env
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.task_investigation import TaskInvestigation
from repo_maintainer.task_session import repository_identity
from repo_maintainer.ui_registry import UIRegistry


class TaskInvestigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo_root = self.root / 'source'
        subprocess.run(['git', 'init', '-b', 'main', str(self.repo_root)], env=git_env(),
                       capture_output=True, check=True)
        (self.repo_root / 'src').mkdir()
        (self.repo_root / 'src/entry.py').write_text('from helper import hidden_dependency\n', encoding='utf-8')
        (self.repo_root / 'src/helper.py').write_text('def hidden_dependency(n):\n    return n * 2\n', encoding='utf-8')
        (self.repo_root / 'outside.py').write_text('not readable', encoding='utf-8')
        (self.repo_root / 'src/.env').write_text('PRIVATE_KEY=secret', encoding='utf-8')
        self.repo = RepositoryGateway(self.repo_root)
        self.repo.command('add', '.')
        self.repo.command('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture')
        self.head = self.repo.head
        self.binding = {'provider': {'request_budget': 4}}
        self.registry_root = self.root / 'original-task'
        self.registry = UIRegistry(self.registry_root, self.binding)
        self.addCleanup(lambda: self.registry.close())
        self.task_id = 'a' * 32
        self.broker = TaskInvestigation(self.registry, self.repo, self.task_id)
        self.scope = dict(repository_id=repository_identity(self.repo.root, self.repo.common_dir),
                          base_sha=self.head, read_roots=['src'], policy_sha256='b' * 64,
                          expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())

    def test_discovers_dependency_not_in_initial_context_without_write_authority(self):
        scope = self.broker.register(**self.scope)
        result = self.broker.search_sources('hidden_dependency')
        self.assertEqual({'src/entry.py', 'src/helper.py'}, {row['source']['path'] for row in result['hits']})
        self.assertTrue(result['complete_catalog_scan'])
        read = self.broker.read_source('src/helper.py', start_line=2, max_lines=1)
        self.assertEqual('    return n * 2', read['lines'][0]['text'])
        self.assertEqual(scope, read['scope_sha256'])
        self.assertEqual(self.head, read['source']['commit_sha'])
        self.assertFalse(read['write_authorized'])
        self.assertFalse(read['runtime_authorized'])
        self.assertEqual(0, self.registry.budget()['requests_used'])
        self.assertEqual(b'', self.repo.command('status', '--porcelain'))

    def test_reads_original_commit_when_working_copy_changes(self):
        self.broker.register(**self.scope)
        original = self.repo.command('show', self.head + ':src/helper.py')
        (self.repo_root / 'src/helper.py').write_text('new candidate content', encoding='utf-8')
        result = self.broker.read_source('src/helper.py')
        self.assertEqual(digest(original), result['source']['sha256'])
        self.assertEqual('original_fixed_commit', result['side'])

    def test_sensitive_and_outside_paths_rejected(self):
        self.broker.register(**self.scope)
        for path in ('outside.py', 'src/.env', '../outside.py', '/etc/passwd'):
            with self.subTest(path=path), self.assertRaises(SafetyError):
                self.broker.read_source(path)

    def test_repeat_registration_and_restart_cannot_refill_budget(self):
        self.broker.register(**self.scope, max_calls=1)
        self.broker.read_source('src/helper.py')
        self.registry.close()
        self.registry = UIRegistry(self.registry_root, self.binding)
        restored = TaskInvestigation(self.registry, self.repo, self.task_id)
        restored.register(**self.scope, max_calls=1)
        with self.assertRaisesRegex(SafetyError, 'budget exhausted'):
            restored.search_sources('dependency')
        self.assertEqual(1, self.registry.load('task_investigation')[self.task_id]['calls'])

    def test_scope_cannot_expand_after_registration(self):
        self.broker.register(**self.scope)
        with self.assertRaises(SafetyError):
            self.broker.register(**{**self.scope, 'read_roots': ['.']})

    def test_wrong_repository_and_modified_scope_rejected(self):
        with self.assertRaises(SafetyError):
            self.broker.register(**{**self.scope, 'repository_id': 'c' * 64})
        self.broker.register(**self.scope)
        row = self.registry.load('task_investigation')[self.task_id]
        row['scope']['read_roots'] = ['.']
        self.registry.put('task_investigation', self.task_id, row)
        with self.assertRaisesRegex(SafetyError, 'binding changed'):
            self.broker.search_sources('secret')

    def test_revocation_before_and_after_read_discards_result(self):
        self.broker.register(**self.scope)
        calls = []
        def guard():
            calls.append(True)
            if len(calls) >= 3:
                raise SafetyError('original grant paused')
        self.broker.guard = guard
        with self.assertRaisesRegex(SafetyError, 'paused'):
            self.broker.read_source('src/helper.py')
        row = self.registry.load('task_investigation')[self.task_id]
        self.assertEqual(1, row['calls'])
        self.assertEqual([], row['receipts'])

    def test_expired_scope_cannot_read(self):
        with self.assertRaisesRegex(SafetyError, 'expired'):
            self.broker.register(**{**self.scope, 'expires_at': '2000-01-01T00:00:00+00:00'})

    def test_search_is_literal_and_does_not_run_shell(self):
        self.broker.register(**self.scope)
        result = self.broker.search_sources('$(touch hacked) .*')
        self.assertEqual([], result['hits'])
        self.assertFalse((self.repo_root / 'hacked').exists())
        self.assertNotIn('$(touch', json.dumps(self.registry.load('task_investigation')))

    def test_bounded_search_reports_partial_and_records_pins(self):
        self.broker.register(**self.scope)
        result = self.broker.search_sources('hidden_dependency', max_hits=1)
        self.assertEqual(1, len(result['hits']))
        self.assertFalse(result['complete_catalog_scan'])
        self.assertEqual(self.head, result['hits'][0]['source']['commit_sha'])
        self.assertEqual(1, len(self.registry.load('task_investigation')[self.task_id]['receipts']))

    def test_search_and_read_share_original_unique_file_accounting(self):
        self.broker.register(**self.scope)
        self.broker.read_source('src/entry.py')
        self.broker.search_sources('hidden_dependency')
        row = self.registry.load('task_investigation')[self.task_id]
        self.assertEqual(['src/entry.py', 'src/helper.py'], row['read_paths'])

    def test_entire_line_redacted_before_long_line_clipping(self):
        secret = 'BoundarySecretWhichMustNeverEnterContext'
        (self.repo_root / 'src/boundary.py').write_bytes(('x' * 490 + secret + '\n').encode())
        self.repo.command('add', '.')
        self.repo.command('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'boundary')
        self.broker.register(**{**self.scope, 'base_sha': self.repo.head})
        with patch.dict('os.environ', {'PI_MAINTENANCE_API_KEY': secret}):
            value = self.broker.read_source('src/boundary.py')
            search = self.broker.search_sources('xxx', max_hits=1)
        self.assertNotIn('BoundarySe', json.dumps([value, search]))

    def test_corrupted_counters_rejected_without_reset(self):
        self.broker.register(**self.scope)
        row = self.registry.load('task_investigation')[self.task_id]
        for bad in (-100, True, '0', 501):
            self.registry.put('task_investigation', self.task_id, {**row, 'calls': bad})
            with self.subTest(bad=bad), self.assertRaisesRegex(SafetyError, 'counters'):
                self.broker.read_source('src/entry.py')

    def test_unicode_window_is_partial_without_exceeding_original_byte_budget(self):
        (self.repo_root / 'src/unicode.py').write_bytes((('\U0001f642' * 500 + '\n') * 40).encode())
        self.repo.command('add', '.')
        self.repo.command('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'unicode')
        self.broker.register(**{**self.scope, 'base_sha': self.repo.head}, max_output_bytes=4096)
        result = self.broker.read_source('src/unicode.py', max_lines=200)
        self.assertTrue(result['output_truncated'])
        self.assertTrue(result['truncated'])
        self.assertGreater(result['omitted_rows'], 0)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 4096)
        row = self.registry.load('task_investigation')[self.task_id]
        self.assertEqual(len(json.dumps(result, ensure_ascii=False).encode()), row['output_bytes'])


if __name__ == '__main__':
    unittest.main()
