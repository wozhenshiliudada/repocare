"""Real Windows Git regression for the worktree child's explicit GIT_DIR limit.

Only a deterministic MockProvider is used; no real model/network. Failed fixtures are retained, including a failed
original discard; no fallback deletion or relaxed ownership guard is attempted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.integration import prepare_integration, verification_registration
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.provider import MockProvider
from repo_maintainer.workflow import cleanup_run, repair


@unittest.skipUnless(os.name == 'nt', 'Windows explicit GIT_DIR length regression')
class GitLongPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='repocare-git-longpaths-')
        # Suppress automatic finalization so a failed original discard keeps its
        # fixture. Explicit successful cleanup below uses tempfile's standard
        # Windows read-only Git-object handling without ignoring cleanup errors.
        self.temporary._finalizer.detach()
        self.owned_root = Path(self.temporary.name).resolve()
        self.completed = False
        # Common ancestor180 + /source7 =187; /<run27>/workspace10 =218.
        padding = 180 - len(str(self.owned_root)) - 1
        self.assertGreater(padding, 0, 'Fixture temporary prefix exceeds the exact regression geometry')
        self.assertLessEqual(padding, 200)
        self.shared = self.owned_root / ('p' * padding)
        self.source = self.shared / 'source'
        self.owner = self.shared / ('r' * 27)
        self.target = self.owner / 'workspace'
        self.assertEqual(len(str(self.source)), 187)
        self.assertEqual(len(str(self.target)), 218)
        self.assertEqual(len(str(self.target / '.git')), 223)
        self.owner.mkdir(parents=True)
        create_fixture(self.source)
        self.audit = []
        self.repository = RepositoryGateway(self.source, self.audit)
        self.before = self.repository.snapshot()
        self.config_before = (self.repository.common_dir / 'config').read_bytes()
        self.branch = 'maintenance/longpath-regression'
        self.version = self.repository.command('--version').decode().strip()
        print(json.dumps({'case': self.id(), 'owned_fixture': str(self.owned_root),
            'source_length': len(str(self.source)), 'target_length': len(str(self.target)),
            'target_git_length': len(str(self.target / '.git')), 'git_version': self.version}), flush=True)

    def tearDown(self):
        if self.completed and not self.target.exists():
            # Only the exact newly owned fixture is removed, never a computed
            # parent or a failed candidate. The original discard already ran.
            resolved = self.owned_root.resolve()
            if (resolved != self.owned_root or resolved.parent != Path(tempfile.gettempdir()).resolve()
                    or not resolved.name.startswith('repocare-git-longpaths-')):
                raise AssertionError('Fixture cleanup target changed; preserve it')
            self.temporary.cleanup()
            self.assertFalse(resolved.exists())
            print('fixture_cleanup=removed_owned_successful_fixture', flush=True)
        else:
            print('fixture_cleanup=retained_for_inspection ' + str(self.owned_root), flush=True)

    def test_exact_218_workspace_read_write_diff_and_original_owned_discard(self):
        evidence = self.owner / 'keep-evidence.txt'
        evidence.write_text('Original run-owned evidence must survive candidate discard.\n', encoding='utf-8')
        candidate = self.repository.create_workspace(self.target, self.branch, self.before['head'])
        self.assertEqual(candidate.root, self.target.resolve())
        self.assertEqual(candidate.common_dir, self.repository.common_dir)
        self.assertEqual(candidate.head, self.before['head'])
        original = self.repository.read('calculator.py')
        self.assertEqual(candidate.read('calculator.py'), original)
        edited = original.replace(b'return a - b', b'return a + b')
        self.assertNotEqual(edited, original)
        candidate.safe_file('calculator.py').write_bytes(edited)
        self.assertEqual(candidate.read('calculator.py'), edited)
        self.assertIn('+    return a + b', candidate.diff())
        self.assertIn('-    return a - b', candidate.diff())
        self.assertEqual(self.repository.read('calculator.py'), original)
        self.assertEqual(self.before, self.repository.snapshot(exclude_branch=self.branch))
        # Relative command transport must not weaken the run/ancestor owner check.
        for wrong_owner in (self.owner.parent, self.owner / 'unrelated'):
            with self.subTest(owner=str(wrong_owner)), self.assertRaisesRegex(SafetyError, 'does not belong'):
                self.repository.discard_candidate(self.target, self.branch, self.before['head'], wrong_owner)
            self.assertTrue(self.target.is_dir())
            self.assertEqual(candidate.read('calculator.py'), edited)
            self.assertEqual(candidate.head, self.before['head'])
        self.repository.discard_candidate(self.target, self.branch, self.before['head'], self.owner)
        self.assertFalse(self.target.exists())
        self.assertTrue(evidence.is_file())
        self.assertEqual(self.before, self.repository.snapshot())
        self.assertEqual(self.config_before, (self.repository.common_dir / 'config').read_bytes())
        additions = [row['command'] for row in self.audit if 'worktree' in row['command']
                     and row['command'][row['command'].index('worktree') + 1] == 'add']
        self.assertEqual(len(additions), 1)
        argument = additions[0][additions[0].index('add') + 3]
        self.assertFalse(Path(argument).is_absolute())
        self.assertEqual((self.source / argument).resolve(), self.target.resolve())
        self.assertTrue(all('core.longpaths=true' in row['command'] for row in self.audit))
        print('original_discard=completed; candidate_absent=true; source_before_equals_after=true; '
              'source_config_unchanged=true; ancestor_evidence_preserved=true', flush=True)
        self.completed = True

    def test_existing_target_is_refused_without_touching_it_or_creating_branch(self):
        self.target.mkdir()
        marker = self.target / 'keep.txt'
        marker.write_bytes(b'pre-existing owned fixture, never a new candidate')
        with self.assertRaisesRegex(SafetyError, 'new directory outside source'):
            self.repository.create_workspace(self.target, self.branch, self.before['head'])
        self.assertEqual(marker.read_bytes(), b'pre-existing owned fixture, never a new candidate')
        self.assertEqual(self.before, self.repository.snapshot())
        self.assertEqual(self.config_before, (self.repository.common_dir / 'config').read_bytes())
        # This was never a worktree; remove only the exact marker and empty
        # test-owned leaf so successful fixture cleanup does not mimic discard.
        marker.unlink()
        self.target.rmdir()
        self.completed = True

    def test_exact_233_integration_verification_seals_and_discards_original_candidate(self):
        padding = 175 - len(str(self.owned_root)) - 1
        self.assertGreater(padding, 0)
        runs_root = self.owned_root / ('s' * padding)
        self.assertEqual(len(str(runs_root)), 175)
        policy = Policy(('calculator.py',), (DEMO_TEST,),
                        readonly_paths=('tests/test_calculator.py',))
        record = repair(self.source, 'addition', MockProvider(), policy, 'auto', runs_root)
        self.assertEqual(record['status'], 'candidate_ready', record.get('error'))
        self.assertEqual(record['source_repo'], str(self.source))
        self.assertEqual(record['source_before'], self.before)
        self.target = Path(record['workspace'])
        self.owner = self.target.parent
        verification = self.owner / 'integration-verification'
        self.assertEqual(len(self.owner.name), 32)
        self.assertEqual(len(str(self.target)), 218)
        self.assertEqual(self.target.name, 'workspace')
        self.assertEqual(len(str(verification)), 233)
        self.assertEqual(len(str(verification / '.git')), 238)
        self.assertFalse(self.owner.is_relative_to(self.source))
        candidate = RepositoryGateway(self.target)
        self.assertEqual(candidate.common_dir, self.repository.common_dir)
        self.assertEqual(candidate.head, self.before['head'])
        candidate_before = candidate.snapshot()
        branch = record['branch']
        self.assertEqual(branch, 'maintenance/' + self.owner.name)
        baseline = record['baseline_tests'][0]
        checked = record['candidate_tests'][0]
        self.assertNotEqual(baseline['returncode'], 0)
        self.assertFalse(baseline['timed_out'])
        self.assertRegex(baseline['stderr'], r'Ran 4 tests in ')
        self.assertIn('FAILED', baseline['stderr'])
        self.assertEqual(checked['returncode'], 0)
        self.assertFalse(checked['timed_out'])
        self.assertRegex(checked['stderr'], r'Ran 4 tests in ')
        self.assertIn('OK', checked['stderr'])
        self.assertEqual(checked['command'][1:], list(DEMO_TEST[1:]))
        with self.assertRaisesRegex(SafetyError, 'does not belong'):
            self.repository.discard_candidate(self.target, branch, self.before['head'], self.owner.parent)
        self.assertEqual(candidate.snapshot(), candidate_before)
        review = prepare_integration(self.owner)
        self.assertEqual(review['status'], 'prepared', review)
        self.assertTrue(review['review_sha256'])
        self.assertEqual(review['preparation']['source_repo'], str(self.source))
        self.assertEqual(review['preparation']['common_dir'], str(self.repository.common_dir))
        self.assertEqual(review['preparation']['verification_dir'], str(verification))
        self.assertEqual(review['review']['candidate_snapshot'], candidate_before)
        self.assertTrue(review['review']['tests'])
        for checked in review['review']['tests']:
            self.assertEqual(checked['returncode'], 0)
            self.assertFalse(checked['timed_out'])
            self.assertRegex(checked['stderr'], r'Ran 4 tests in ')
            self.assertIn('OK', checked['stderr'])
            self.assertEqual(checked['command'][1:], list(DEMO_TEST[1:]))
        self.assertEqual(review['cleanup']['result'], 'verification_removed')
        self.assertFalse(verification.exists())
        self.assertEqual(verification_registration(self.repository, verification), [])
        self.assertEqual(candidate.snapshot(), candidate_before)
        self.assertEqual(self.before, self.repository.snapshot(exclude_branch=branch))
        self.assertEqual(self.config_before, (self.repository.common_dir / 'config').read_bytes())
        # Sealing keeps the successful candidate; only the original explicit
        # cleanup route may discard that candidate and its run-owned branch.
        self.assertTrue(self.target.is_dir())
        result = cleanup_run(self.owner)
        self.assertEqual(result['status'], 'discarded')
        self.assertTrue(result['source_unchanged'])
        self.assertFalse(self.target.exists())
        self.assertFalse(verification.exists())
        self.assertTrue((self.owner / 'integration-review.json').is_file())
        self.assertEqual(self.before, self.repository.snapshot())
        self.assertEqual(self.config_before, (self.repository.common_dir / 'config').read_bytes())
        print('second_entry=source187/runs175/run32/workspace218/verification233; '
              'seal=prepared; sealed_tests=passed_nonempty; verification_removed=true; '
              'candidate_owned_cleanup=discarded; source_before_equals_after=true; '
              'source_config_unchanged=true; mock_only=true', flush=True)
        self.completed = True

    def test_source_contained_target_is_refused_even_with_relative_parent_segments(self):
        inside = self.source / '..' / 'source' / 'nested-candidate'
        with self.assertRaisesRegex(SafetyError, 'new directory outside source'):
            self.repository.create_workspace(inside, self.branch, self.before['head'])
        self.assertFalse(inside.exists())
        self.assertEqual(self.before, self.repository.snapshot())
        self.assertEqual(self.config_before, (self.repository.common_dir / 'config').read_bytes())
        self.completed = True


if __name__ == '__main__':
    unittest.main(verbosity=2)
