"""Native committed-object search before bounded source materialization; no model."""
import unittest
from unittest.mock import patch

import test_source_discovery as fixtures
from repo_maintainer import source_discovery
from repo_maintainer.repository_diagnosis import build_report


class CommittedSearchTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.SourceDiscoveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture,self.repo=fixture,fixture.repo

    def large(self):
        for i in range(source_discovery.LIMITS['discovery_max_files']+5):
            self.fixture.write(f'tests/test_noise_{i:03}.py', 'import unittest\n'
                'class T(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n')
        self.fixture.write('src/z_component.py', 'def CriticalRepairSymbol(n): return n*2\n')
        self.fixture.write('tests/test_z_component.py', 'import unittest\n'
            'from src.z_component import CriticalRepairSymbol\nclass T(unittest.TestCase):\n'
            '    def test_total(self): self.assertEqual(CriticalRepairSymbol(2),2)\n')
        self.fixture.commit()
        self.fixture.write('README.md', 'A later unrelated documentation change.\n')
        return self.fixture.commit()

    def report(self, head, roots=None, query='CriticalRepairSymbol'):
        return build_report(self.repo,head_sha=head,read_roots=roots or ['src','tests','README.md'],
                            search=True,query=query)

    def test_symbol_query_finds_implementation_and_test_beyond_old_read_prefix(self):
        head=self.large()
        before=self.repo.snapshot()
        report=self.report(head)
        names=[r['path'] for r in report['file_candidates']]
        self.assertIn('src/z_component.py',names)
        self.assertIn('tests/test_z_component.py',names)
        coverage=report['discovery']['coverage']
        self.assertLessEqual(coverage['files_read'],source_discovery.LIMITS['discovery_max_files'])
        self.assertLessEqual(coverage['bytes_read'],source_discovery.LIMITS['discovery_max_bytes'])
        self.assertFalse(coverage['complete_repository_review'])
        self.assertEqual(before,self.repo.snapshot())

    def test_search_old_objects_not_current_index_dirty_tree_or_textconv(self):
        self.fixture.write('src/target.py','CriticalRepairSymbol = 1\n')
        self.fixture.write('.gitattributes','*.py diff=unsafe\n')
        self.fixture.write('converter.py','from pathlib import Path\nPath("CONVERTER_MARKER").touch()\n'
            'print("CriticalRepairSymbol")\n')
        old=self.fixture.commit()
        self.repo.command('config','diff.unsafe.textconv','python converter.py')
        self.fixture.write('src/target.py','new_committed = 2\n')
        self.fixture.commit()
        self.fixture.write('src/target.py','USER_DIRTY_BYTES = 3\n')
        self.repo.command('add','src/target.py')
        self.fixture.write('tests/untracked.py','CriticalRepairSymbol = 4\n')
        before=self.repo.snapshot()
        report=self.report(old,roots=['src'])
        row=next(r for r in report['file_candidates'] if r['path']=='src/target.py')
        self.assertEqual(row['commit_sha'],old)
        self.assertEqual(row['sha256'],source_discovery.digest(b'CriticalRepairSymbol = 1\n'))
        self.assertFalse((self.repo.root/'CONVERTER_MARKER').exists())
        self.assertEqual(before,self.repo.snapshot())

    def test_literal_paths_and_only_eligible_catalog_entries_reach_native_search(self):
        for name in ('src/[literal]/target.py','src/literal/target.py','private/outside.py',
                     'src/.env','src/secrets/credential.py'):
            self.fixture.write(name,'CriticalRepairSymbol = 1\n')
        self.fixture.write('src/huge.py','x'*(source_discovery.LIMITS['source_file_max_bytes']+1))
        head=self.fixture.commit()
        calls=[]
        original=source_discovery._read_git
        def record(repository,*args,**kwargs):
            if args[0]=='grep': calls.append(args)
            return original(repository,*args,**kwargs)
        with patch('repo_maintainer.source_discovery._read_git',side_effect=record):
            report=self.report(head,roots=['src/[literal]','src/huge.py'])
        self.assertTrue(calls)
        for args in calls:
            paths=args[args.index('--')+1:]
            self.assertEqual(paths,('src/[literal]/target.py',))
            self.assertIn('--no-textconv',args)
            self.assertIn('-F',args)
            self.assertIn(head,args)
        self.assertEqual([r['path'] for r in report['file_candidates']],['src/[literal]/target.py'])
        calls.clear()
        with patch('repo_maintainer.source_discovery._read_git',side_effect=record):
            self.report(head,roots=['src'])
        for args in calls:
            paths=args[args.index('--')+1:]
            self.assertTrue(all(n.startswith('src/') for n in paths))
            self.assertTrue(all(n not in paths for n in ('src/.env','src/secrets/credential.py','src/huge.py')))

    def test_truncated_native_output_is_explicitly_partial_and_not_a_verified_source(self):
        self.fixture.write('src/target.py','CriticalRepairSymbol = 1\n')
        head=self.fixture.commit()
        original=source_discovery._read_git
        def truncate(repository,*args,**kwargs):
            if args[0]=='grep':
                return {'stdout':(head+':src/target.py\0'+head+':src/unfinished').encode(),
                    'stderr':b'', 'returncode':137,'timed_out':False,'truncated':True,
                    'receipt':{'captured_bytes':10}}
            return original(repository,*args,**kwargs)
        with patch('repo_maintainer.source_discovery._read_git',side_effect=truncate):
            report=self.report(head)
        native=report['discovery']['coverage']['native_search']
        self.assertEqual(native['status'],'partial')
        self.assertFalse(native['complete_catalog_scan'])
        self.assertTrue(native['output_truncated'])
        self.assertNotIn('src/unfinished',[p['path'] for p in report['file_candidates']])

    def test_no_match_is_a_valid_scan_but_empty_query_or_scope_never_launches_it(self):
        report=self.report(self.repo.head,query='AbsentSpecificSymbol')
        native=report['discovery']['coverage']['native_search']
        self.assertEqual(native['status'],'complete_catalog')
        self.assertEqual(native['matched_paths'],[])
        self.assertTrue(all(c['returncode']==1 for c in native['calls']))
        for roots,query in [([], 'SpecificSymbol'), (['src'], '')]:
            report=build_report(self.repo,head_sha=self.repo.head,read_roots=roots,search=True,query=query)
            self.assertEqual(report['discovery']['coverage']['native_search']['calls'],[])

    def test_native_scan_has_a_separate_visible_input_budget_and_never_claims_full_coverage(self):
        self.fixture.write('src/large.py','CriticalRepairSymbol = 1\n'+'#' * 300)
        head=self.fixture.commit()
        tree=source_discovery.CommittedTree(self.repo,head,['src'],byte_limit=100)
        hits,info=source_discovery.committed_search(tree,{'CriticalRepairSymbol':'query'})
        self.assertEqual(info['status'],'partial')
        self.assertLessEqual(info['input_blob_bytes_upper_bound'],100)
        self.assertLessEqual(info['captured_output_bytes'],65536)
        self.assertFalse(info['complete_catalog_scan'])
        self.assertNotIn('src/large.py',hits)
        self.assertEqual(tree.cache,{})

    def test_expired_scan_does_not_start_git_or_claim_no_matches_as_complete(self):
        tree=source_discovery.CommittedTree(self.repo,self.repo.head,['src'])
        tree.deadline=0
        with patch('repo_maintainer.source_discovery._read_git',side_effect=AssertionError('late child')):
            hits,info=source_discovery.committed_search(tree,{'SpecificSymbol':'query'})
        self.assertEqual(hits,set())
        self.assertTrue(info['timed_out'])
        self.assertEqual(info['status'],'partial')
        self.assertEqual(info['calls'],[])


if __name__=='__main__':
    unittest.main()
