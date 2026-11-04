"""W2 actual committed Git fixtures. Never execute source-supplied commands."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, PropertyMock

from repo_maintainer.git import RepositoryGateway, git_env
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.repository_diagnosis import build_report, failure_evidence, main
from repo_maintainer.source_discovery import CommittedTree, discover_sources


class SourceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        subprocess.run(['git', 'init', '-b', 'main', str(self.root)], check=True,
                       capture_output=True, env=git_env())
        self.repo = RepositoryGateway(self.root)
        self.write('src/checkout.py', 'from rules import adjusted_total\n\ndef checkout(n):\n    return adjusted_total(n)\n')
        self.write('src/rules.py', 'def adjusted_total(n):\n    return n * 2\n')
        self.write('tests/test_checkout.py', 'import unittest\nfrom src.checkout import checkout\nclass Check(unittest.TestCase):\n    def test_checkout(self):\n        self.assertEqual(checkout(2), 2)\n')
        self.head = self.commit()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode('utf-8'))

    def commit(self):
        self.repo.command('add', '.')
        self.repo.command('-c', 'user.name=W2 Fixture', '-c', 'user.email=w2@example.invalid',
                          'commit', '-m', 'fixture')
        return self.repo.head

    def checks(self, text=None):
        return [{'command': ['python', '-m', 'unittest'], 'returncode': 1,
                 'stderr': text or 'FAIL: test_checkout (tests.Check)\n  File "src/checkout.py", line 4\nAssertionError: 4 != 2\n'}]

    def report(self, **kwargs):
        return build_report(self.repo, head_sha=self.head, read_roots=['.'],
                            checks=self.checks(), search=True, **kwargs)

    def test_cross_file_search_without_user_naming_implementation_or_test_command(self):
        report = self.report()
        candidates = {r['path']: r for r in report['file_candidates']}
        self.assertIn('src/rules.py', candidates)
        self.assertIn('lexical_import_from_seed', candidates['src/rules.py']['reasons'])
        self.assertEqual(candidates['src/rules.py']['import_hints'][0]['from_path'], 'src/checkout.py')
        self.assertEqual(report['discovery']['verified_locations'][0]['status'], 'verified_location')
        test = report['test_candidates'][0]
        self.assertIn('unittest', test['argv'])
        self.assertEqual(test['verification'], 'not_run')
        self.assertFalse(test['runtime_authorized'])
        self.assertEqual(test['source_bindings'][0]['path'], 'tests/test_checkout.py')
        self.assertFalse(report['coverage']['semantic_review_performed'])

    def test_visible_diagnostic_literal_beats_many_prose_matches_without_file_hint(self):
        for i in range(18):
            self.write(f'src/noise_{i:02d}.py', 'stdout = "output report"\n' * 12)
        self.write('src/worker.py', 'def capture(process):\n    return process.communicate()\n')
        self.write('tests/test_capture.py', 'import unittest\nclass Capture(unittest.TestCase):\n'
                   '    def test_output(self):\n        self.assertTrue(True)\n')
        self.head = self.commit()
        report = build_report(self.repo, head_sha=self.head, read_roots=['src', 'tests'],
            search=True, query='stdout output report <!-- hidden_tracker_abc --> `communicate()` grows memory')
        row = next(r for r in report['file_candidates'] if r['path'] == 'src/worker.py')
        self.assertIn('visible_diagnostic_literal_match', row['reasons'])
        self.assertEqual(report['file_candidates'][0]['path'], 'src/worker.py')
        self.assertNotIn('hidden_tracker_abc', [r['text'] for r in report['discovery']['query_terms']])
        self.assertIn('communicate', report['discovery']['coverage']['native_search']['terms'])
        self.assertFalse(row.get('runtime_authorized', False))

    def test_hidden_comment_cannot_fill_diagnostic_vocabulary(self):
        from repo_maintainer.source_discovery import query_terms
        terms = query_terms([], '<!-- ' + ' '.join(f'noise_{n}' for n in range(40))
                            + ' --> `specific_signal` fails after update')
        self.assertEqual(terms['specific_signal'], 'query_code')
        self.assertFalse(any(t.startswith('noise_') for t in terms))

    def test_visible_existing_path_wins_over_generic_code_fields_but_hidden_path_does_not(self):
        for i in range(18):
            self.write(f'src/noise_{i:02d}.py', 'model_calls = 0\nprovider_files_read = 0\n')
        self.write('src/readiness.py', 'ready = False\n')
        self.head = self.commit()
        report = build_report(self.repo, head_sha=self.head, read_roots=['src'], search=True,
            query='Improve `src/readiness.py`; preserve `model_calls=0`, `provider_files_read=0`. '
                  '<!-- `src/noise_00.py` -->')
        first = report['file_candidates'][0]
        self.assertEqual(first['path'], 'src/readiness.py')
        self.assertIn('visible_diagnostic_path_exists_in_target', first['reasons'])
        hidden = next(r for r in report['file_candidates'] if r['path'] == 'src/noise_00.py')
        self.assertNotIn('visible_diagnostic_path_exists_in_target', hidden['reasons'])

    def test_old_commit_bytes_win_over_current_head_index_and_dirty_tree(self):
        old_raw = (self.root / 'src/checkout.py').read_bytes()
        self.write('src/checkout.py', 'NEWER_COMMITTED_CONTENT = 1\n')
        self.commit()
        self.write('src/checkout.py', 'USER_UNCOMMITTED_CONTENT = 2\n')
        self.repo.command('add', 'src/checkout.py')
        self.write('notes.txt', 'user note')
        before = self.repo.snapshot()
        report = self.report()
        location = report['discovery']['verified_locations'][0]
        self.assertEqual(location['sha256'], digest(old_raw))
        self.assertEqual(location['excerpt'], '    return adjusted_total(n)')
        self.assertEqual(location['commit_sha'], self.head)
        self.assertEqual(before, self.repo.snapshot())

    def test_sensitive_paths_links_and_out_of_scope_content_are_not_read(self):
        self.write('secret_dir/.env', 'PRIVATE_MARKER')
        self.write('secrets/config.py', 'PRIVATE_MARKER')
        self.write('private/extra.py', 'PRIVATE_MARKER')
        self.write('src/allowed.py', 'SAFE = 1\n')
        self.head = self.commit()
        blob = self.repo.command('hash-object', '-w', '--stdin', input_data=b'../../private/extra.py').decode().strip()
        self.repo.command('update-index', '--add', '--cacheinfo', '120000,' + blob + ',src/link.py')
        self.repo.command('-c', 'user.name=W2', '-c', 'user.email=w2@example.invalid', 'commit', '-m', 'link')
        self.head = self.repo.head
        tree = CommittedTree(self.repo, self.head, ['src'])
        self.assertNotIn('src/link.py', tree.entries)
        self.assertNotIn('private/extra.py', tree.entries)
        for name in ['secret_dir/.env', 'secrets/config.py', 'private/extra.py', 'src/link.py', '../outside']:
            with self.assertRaises(SafetyError):
                tree.read(name)
        self.assertEqual(tree.skipped['non_regular'], 1)
        full = CommittedTree(self.repo, self.head, ['.'])
        self.assertNotIn('secrets/config.py', full.entries)

    def test_invalid_or_unavailable_log_locations_do_not_become_verified(self):
        report = build_report(self.repo, head_sha=self.head, read_roots=['src'], search=True,
            checks=self.checks('File "src/checkout.py", line 999\nFile "src/missing.py", line 1\nFile "../outside.py", line 1\n'))
        rows = report['discovery']['verified_locations']
        self.assertEqual([r['status'] for r in rows],
                         ['line_out_of_range', 'not_read_or_not_eligible', 'not_read_or_not_eligible'])
        self.assertIsNone(rows[2]['path'])
        self.assertFalse(report['failures']['observations'][0]['source_path_verified'])

    def test_binary_oversize_invalid_utf8_do_not_become_search_or_check_input(self):
        self.write('src/binary.py', b'checkout\x00PRIVATE')
        self.write('src/encoding.py', b'checkout\xff')
        from repo_maintainer.maintenance_limits import LIMITS
        self.write('tests/test_huge.py', b'x' * (LIMITS['source_file_max_bytes'] + 1))
        self.head = self.commit()
        report = self.report()
        cov = report['discovery']['coverage']
        self.assertEqual(cov['skipped_counts']['oversized'], 1)
        self.assertEqual(cov['skipped_counts']['binary'], 1)
        self.assertEqual(cov['skipped_counts']['non_utf8'], 1)
        names = {r['path'] for r in report['file_candidates']}
        self.assertNotIn('src/binary.py', names)
        self.assertNotIn('tests/test_huge.py', report['discovery']['check_discovery']['inputs'])

    def test_read_and_byte_limits_are_shared_with_check_recommendations(self):
        git = {'head_sha': self.head, 'changes': None}
        failure = failure_evidence(self.checks(), target_sha=self.head,
                                  repository_root=self.root, read_roots=['.'])
        value = discover_sources(self.repo, git, failure, ['.'], file_limit=1)
        self.assertEqual(value['coverage']['files_read'], 1)
        self.assertTrue(value['coverage']['read_budget_exhausted'])
        self.assertGreater(value['coverage']['unsearched_eligible'], 0)
        self.assertEqual(value['test_candidates'], [])
        limited = discover_sources(self.repo, git, failure, ['.'], byte_limit=1)
        self.assertEqual(limited['coverage']['bytes_read'], 0)
        self.assertTrue(limited['coverage']['read_budget_exhausted'])

    def test_tree_reads_once_and_deadline_stops_further_blobs(self):
        tree = CommittedTree(self.repo, self.head, ['src'])
        raw = tree.read('src/checkout.py')
        with patch('repo_maintainer.source_discovery._read_git', side_effect=AssertionError('reread')):
            self.assertEqual(tree.read('src/checkout.py'), raw)
        tree.deadline = 0
        with self.assertRaisesRegex(SafetyError, 'budget exhausted'):
            tree.read('src/rules.py')

    def test_javascript_relative_import_and_package_checks_are_pinned_without_execution(self):
        self.write('src/entry.ts', "import {run} from './util';\nexport const result = run();\n")
        self.write('src/util.ts', 'export function run() { return 1; }\n')
        self.write('package.json', json.dumps({'scripts': {'test': 'echo NEVER_EXECUTE > marker.txt'}}))
        self.head = self.commit()
        report = build_report(self.repo, head_sha=self.head, read_roots=['.'], search=True,
                             checks=self.checks('src/entry.ts:2:3\nError: wrong result'))
        candidate = next(r for r in report['file_candidates'] if r['path'] == 'src/util.ts')
        self.assertIn('lexical_import_from_seed', candidate['reasons'])
        check = next(r for r in report['test_candidates'] if r['id'] == 'npm-test')
        self.assertEqual(check['source_bindings'][0]['sha256'], digest((self.root / 'package.json').read_bytes()))
        self.assertFalse((self.root / 'marker.txt').exists())

    def test_explicit_javascript_test_import_resolves_only_its_committed_relative_path(self):
        self.write('js/checks/smoke.test.mjs', "import '../logic/policy.mjs';\n"
                   "import '../../outside/private.mjs';\nimport '../logic/missing.mjs';\n"
                   "import '../logic/wrong.mjs';\nimport '../logic/absent';\n")
        for name in ('js/logic/policy.mjs', 'outside/private.mjs', 'js/mirror/js/logic/absent.mjs',
                     'js/logic/wrong.ts', 'js/logic/missing.mjs.js', 'js/logic/absent.py'):
            self.write(name, 'export const value = 1;\n')
        self.commit()
        self.repo.command('-c', 'user.name=W2 Fixture', '-c', 'user.email=w2@example.invalid',
                          'commit', '--allow-empty', '-m', 'observe unchanged scope')
        test_path = 'js/checks/smoke.test.mjs'
        report = build_report(self.repo, head_sha=self.repo.head, read_roots=['js'],
                              search=True, query=f'`{test_path}`')
        self.assertEqual([row['path'] for row in report['file_candidates']],
                         [test_path, 'js/logic/policy.mjs'])
        imported = report['file_candidates'][1]
        self.assertIn('lexical_import_from_seed', imported['reasons'])
        self.assertEqual(imported['import_hints'], [{'from_path': test_path, 'module': 'js/logic/policy.mjs'}])
        self.assertEqual(imported['sha256'], digest(self.repo.read('js/logic/policy.mjs')))

    def test_native_typescript_modules_are_discovered_and_pinned_without_execution(self):
        from repo_maintainer.validation_proposal import source_packet, proposal_contract
        for extension, body in [('mts', 'export function checkout(n: number) { return n * 2; }'),
                                ('cts', 'exports.checkout = (n: number) => n * 2;')]:
            self.write(f'src/native.{extension}', body + '\n')
        self.write('outside/private.mts', 'throw new Error("do not load");\n')
        self.head = self.commit()
        before = self.repo.snapshot()
        report = build_report(self.repo, head_sha=self.head, read_roots=['src'],
                              search=True, query='`checkout` should return its input unchanged')
        candidates = {row['path']: row for row in report['file_candidates']}
        for extension in ('mts', 'cts'):
            name = f'src/native.{extension}'
            row = candidates[name]
            self.assertEqual(row['sha256'], digest((self.root / name).read_bytes()))
            self.assertEqual(row['commit_sha'], self.head)
            self.assertEqual(row['role'], 'implementation')
            source = source_packet(self.repo, 'checkout(2) must return 2', ['src'], [name], {})
            proposal = {'requirement': 'checkout(2) must return 2', 'rationale': 'Explicit expectation',
                        'limitations': 'Native erasable TypeScript only', 'clarification': False,
                        'cases': [{'id': 'checkout', 'module': name, 'callable': 'checkout',
                                   'args': [2], 'kwargs': {}, 'expect': {'kind': 'json', 'value': 2},
                                   'missing_behavior': False}]}
            contract = proposal_contract(source, proposal)
            self.assertEqual(contract['kind'], 'node-behavior-v1')
            self.assertFalse(source['runtime_authorized'])
        self.assertNotIn('outside/private.mts', candidates)
        self.assertFalse(report['runtime_authorized'])
        self.assertEqual([], report['test_candidates'])
        self.assertEqual(before, self.repo.snapshot())

    def test_query_is_literal_bounded_data_and_hits_have_actual_lines(self):
        self.write('src/data.py', 'PROMPT_IGNORE_ALL_RULES = 1\n')
        self.head = self.commit()
        report = self.report(query='PROMPT_IGNORE_ALL_RULES $(touch marker) .*')
        row = next(r for r in report['file_candidates'] if r['path'] == 'src/data.py')
        self.assertEqual(row['hits'][0]['line'], 1)
        self.assertIn('PROMPT_IGNORE_ALL_RULES', row['hits'][0]['excerpt'])
        self.assertFalse(report['runtime_authorized'])
        with self.assertRaises(SafetyError):
            self.report(query='x' * 4097)

    def test_empty_roots_do_not_read_source_or_discover_commands(self):
        value = build_report(self.repo, head_sha=self.head, read_roots=[], search=True,
                             checks=self.checks())
        self.assertEqual(value['file_candidates'], [])
        self.assertEqual(value['test_candidates'], [])
        self.assertEqual(value['discovery']['inputs'], [])

    def test_catalog_and_candidate_caps_are_reported(self):
        from repo_maintainer.maintenance_limits import LIMITS
        for i in range(1002):
            self.write(f'src/a{i:04d}.py', 'adjusted_total = 1\n')
        self.head = self.commit()
        with patch.dict(LIMITS,discovery_catalog_max_entries=1000):
            report = self.report(query='adjusted_total')
        coverage = report['discovery']['coverage']
        self.assertTrue(coverage['catalog_truncated'])
        self.assertTrue(coverage['candidate_truncated'])
        self.assertEqual(len(report['file_candidates']), 12)
        self.assertLessEqual(coverage['files_read'], LIMITS['discovery_max_files'])
        self.assertLessEqual(coverage['catalog_eligible'], 1000)

    def test_outside_scope_catalog_cannot_hide_scoped_source_or_check_recommendations(self):
        for i in range(1002):
            self.write(f'evidence/a{i:04d}.txt','unrelated evidence\n')
        self.head=self.commit()
        before=self.repo.snapshot()
        tree=CommittedTree(self.repo,self.head,['src','tests'])
        self.assertEqual(set(tree.entries),{'src/checkout.py','src/rules.py','tests/test_checkout.py'})
        self.assertFalse(tree.tree_truncated)
        self.assertEqual(tree.read('src/rules.py'),b'def adjusted_total(n):\n    return n * 2\n')
        report=build_report(self.repo,head_sha=self.head,read_roots=['src','tests'],
                            checks=self.checks(),search=True,query='checkout adjusted_total')
        self.assertIn('src/rules.py',[row['path'] for row in report['file_candidates']])
        self.assertTrue(report['test_candidates'])
        from repo_maintainer.target_environment import discover_environment
        environment=discover_environment(self.repo,self.head,['src','tests'])
        self.assertEqual(environment['status'],'proposed',environment['blockers'])
        self.assertTrue(environment['checks'])
        self.assertEqual(before,self.repo.snapshot())

    def test_scope_pathspecs_keep_brackets_option_like_names_and_prefixes_literal(self):
        self.write('src/[literal]/unit.py','literal = 1\n')
        self.write('src/literal/unit.py','must_not_match_brackets = 1\n')
        self.write('src-other/private.py','must_not_match_prefix = 1\n')
        self.write('-metadata.py','option_like_name = 1\n')
        self.head=self.commit()
        before=self.repo.snapshot()
        tree=CommittedTree(self.repo,self.head,['src/[literal]','-metadata.py'])
        self.assertEqual(set(tree.entries),{'src/[literal]/unit.py','-metadata.py'})
        self.assertEqual(tree.read('-metadata.py'),b'option_like_name = 1\n')
        for path in ('src/literal/unit.py','src-other/private.py','src/checkout.py'):
            with self.subTest(path=path),self.assertRaises(SafetyError):
                tree.read(path)
        overlapping=CommittedTree(self.repo,self.head,['src','src/[literal]'])
        self.assertEqual(len(overlapping.entries),4)
        self.assertNotIn('src-other/private.py',overlapping.entries)
        self.assertEqual(before,self.repo.snapshot())

    def test_target_mismatch_and_head_drift_fail_closed(self):
        failure = failure_evidence([], target_sha='0' * 40, repository_root=self.root, read_roots=['.'])
        with self.assertRaisesRegex(SafetyError, 'target differ'):
            discover_sources(self.repo, {'head_sha': self.head}, failure, ['.'])
        failure['target_sha'] = self.head
        with patch.object(RepositoryGateway, 'head', new_callable=PropertyMock,
                          side_effect=[self.head, '0' * 40]):
            with self.assertRaisesRegex(SafetyError, 'HEAD changed'):
                discover_sources(self.repo, {'head_sha': self.head}, failure, ['.'])

    def test_python_relative_imports_find_sibling(self):
        self.write('src/pkg/caller.py', 'from .rules import price\nprice(2)\n')
        self.write('src/pkg/rules.py', 'def price(n):\n    return n\n')
        self.head = self.commit()
        report = build_report(self.repo, head_sha=self.head, read_roots=['src/pkg'], search=True,
                             checks=self.checks('File "src/pkg/caller.py", line 2\n'))
        row = next(r for r in report['file_candidates'] if r['path'] == 'src/pkg/rules.py')
        self.assertIn('lexical_import_from_seed', row['reasons'])

    def test_cli_opt_in_default_remains_w1_and_output_cannot_overwrite(self):
        path = Path(self.temp.name) / 'report.json'
        argv = ['--repo', str(self.root), '--head', self.head, '--read-root', '.', '--output', str(path)]
        main(argv + ['--discover', '--query', 'adjusted_total'])
        report = json.loads(path.read_text(encoding='utf-8'))
        self.assertTrue(report['coverage']['search_performed'])
        self.assertTrue(report['file_candidates'])
        self.assertFalse(build_report(self.repo, head_sha=self.head, read_roots=['.'])['coverage']['search_performed'])
        with self.assertRaises(SafetyError):
            main(argv + ['--discover'])


if __name__ == '__main__':
    unittest.main()
