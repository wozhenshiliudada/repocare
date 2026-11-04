import copy
import json
import unittest

import test_issue_delivery as fixtures
import test_issue_closure as closure_fixtures
from test_issue_progress import Remote
from test_issue_comment_continuity import CommentRemote
from repo_maintainer.issue_progress import IssueProgress, IssueProgressSource, verified_check_counts
from repo_maintainer.models import SafetyError
from repo_maintainer.repository_diagnosis import build_report


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.IssueDeliveryTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        f = self.fixture
        self.source = IssueProgressSource(f.execution, delivery=f.service)
        self.original_budget = f.registry.budget()
        self.original_report = (f.run/'run.json').read_bytes()

    def test_original_sealed_candidate_to_comment_then_original_pr_to_review(self):
        f = self.fixture
        facts = self.source.facts(f.identity, 'candidate_passed')
        self.assertEqual(facts['candidate_sha'], f.commit)
        self.assertEqual(facts['branch'], f.head)
        self.assertEqual(facts['checks'][0]['passed'], 4)
        self.assertEqual(facts['checks'][0]['skipped'], 0)
        self.assertEqual(self.source.facts(f.identity, 'candidate_passed'), facts)
        remote = CommentRemote(f.github)
        service = IssueProgress(f.registry, remote, self.source.observer(f.identity, 'candidate_passed'))
        proposal = service.prepare(facts['repository'], facts['issue_number'], facts['run_id'], facts['event_id'])
        service.send(proposal['id'], approve_sha256=proposal['approval_sha256'], expected_actor_id=42)
        self.assertIn('尚未交付', remote.rows[0]['body'])
        f.service._fresh(f.service._local(f.identity)[1])
        self.assertEqual(self.source.facts(f.identity, 'candidate_passed'), facts)
        with self.assertRaises(SafetyError):
            self.source.facts(f.identity, 'awaiting_review')
        f.create()
        review = self.source.facts(f.identity, 'awaiting_review')
        self.assertEqual(review['links'], [f.remote.rows[0]['html_url']])
        self.assertNotEqual(review['event_id'], facts['event_id'])
        self.assertEqual(f.registry.budget(), self.original_budget)
        self.assertEqual((f.run/'run.json').read_bytes(), self.original_report)
        self.assertEqual(f.execution.view(f.identity)['status'], 'awaiting-review')
        self.assertEqual(f.github.issue['state'], 'open')

    def test_changed_original_report_rejects_candidate_without_repair_or_post(self):
        f = self.fixture
        record = json.loads(self.original_report)
        record['candidate_tests'][0]['stdout'] = 'Ran 0 tests\nOK'
        try:
            (f.run/'run.json').write_text(json.dumps(record), encoding='utf-8')
            with self.assertRaises(SafetyError):
                self.source.facts(f.identity, 'candidate_passed')
        finally:
            (f.run/'run.json').write_bytes(self.original_report)
        self.assertEqual(f.registry.budget(), self.original_budget)
        self.assertEqual(f.remote.writes, 0)

    def test_unverified_comment_timestamp_keeps_original_source_guard_closed(self):
        f = self.fixture
        facts = self.source.facts(f.identity, 'candidate_passed')
        _, binding = f.service._local(f.identity)
        remote = Remote()
        original_create = remote.create_comment
        timestamp = f.github.issue['updated_at']
        def comment_with_metadata_change(*args, **kwargs):
            receipt = original_create(*args, **kwargs)
            f.github.issue['updated_at'] = '2026-10-28T03:00:00Z'
            return receipt
        remote.create_comment = comment_with_metadata_change
        service = IssueProgress(f.registry, remote, self.source.observer(f.identity, 'candidate_passed'))
        proposal = service.prepare(facts['repository'], facts['issue_number'], facts['run_id'], facts['event_id'])
        try:
            self.assertEqual(service.send(proposal['id'], approve_sha256=proposal['approval_sha256'],
                                          expected_actor_id=42)['status'], 'posted-review-required')
            # Deliberate reproduction of an unresolved shared dependency. This is
            # not an integration success and must not relax original guards here.
            with self.assertRaises(SafetyError):
                f.service._fresh(binding)
            with self.assertRaises(SafetyError):
                self.source.facts(f.identity, 'candidate_passed')
        finally:
            f.github.issue['updated_at'] = timestamp
        self.assertEqual(f.registry.budget(), self.original_budget)

    def test_changed_issue_or_claim_cannot_supply_source_booleans(self):
        f = self.fixture
        f.github.issue['updated_at'] = '2026-10-28T02:00:00Z'
        with self.assertRaises(SafetyError):
            self.source.facts(f.identity, 'candidate_passed')
        with f.registry.transaction() as db:
            f.registry.put('issue_execution_head', 'fixture', {'execution_id': 'another'}, db)
        with self.assertRaises(SafetyError):
            self.source.facts(f.identity, 'candidate_passed')
        with self.assertRaises(SafetyError):
            self.source.facts(f.identity, 'delivered')
        self.assertEqual(f.remote.writes, 0)

    def test_accepted_investigation_and_blocker_are_read_from_original_objects(self):
        f = self.fixture
        with f.registry.transaction() as db:
            row = f.execution._load('issue_execution', f.identity, db)
            row.update(status='running')
            f.registry.put('issue_execution', f.identity, row, db)
            diagnosis = f.execution._load('issue_diagnosis', 'diagnosis', db)
            diagnosis.update(status='investigated', report=build_report(f.repo, head_sha=f.repo.head, read_roots=['.']))
            f.registry.put('issue_diagnosis', 'diagnosis', diagnosis, db)
        accepted = self.source.facts(f.identity, 'accepted')
        investigated = self.source.facts(f.identity, 'investigated')
        self.assertIn(f.identity, accepted['summary'])
        self.assertIn('报告指纹', investigated['summary'])
        with f.registry.transaction() as db:
            row.update(status='failed_no_replay', error='Missing dependency; token=private')
            f.registry.put('issue_execution', f.identity, row, db)
        blocked = self.source.facts(f.identity, 'blocked')
        self.assertIn('Missing dependency', blocked['summary'])
        remote = Remote()
        service = IssueProgress(f.registry, remote, self.source.observer(f.identity, 'blocked'))
        proposal = service.prepare(blocked['repository'], blocked['issue_number'], blocked['run_id'], blocked['event_id'])
        self.assertNotIn('token=private', proposal['core']['body'])
        self.assertEqual(f.registry.budget(), self.original_budget)


class CountTests(unittest.TestCase):
    def check(self, text):
        return verified_check_counts(dict(command=['python', '-m', 'unittest'], stdout=text, stderr='',
                                          returncode=0, timed_out=False))

    def test_unittest_skips_are_not_counted_as_passed(self):
        self.assertEqual(self.check('Ran 7 tests\nOK (skipped=2)'),
                         dict(name='python -m unittest', passed=5, failed=0, skipped=2))

    def test_pytest_and_node_summaries(self):
        self.assertEqual(self.check('4 passed, 2 skipped')['passed'], 4)
        self.assertEqual(self.check('# tests 7\n# pass 5\n# fail 0\n# skipped 2')['skipped'], 2)

    def test_unknown_zero_and_truncated_counts_never_claim_success(self):
        for text in ['OK', 'Ran 0 tests\nOK', 'Ran 2 tests\nRan 3 tests\nOK']:
            with self.subTest(text=text), self.assertRaises(SafetyError):
                self.check(text)


class DeliveredSourceTests(unittest.TestCase):
    def test_original_acceptance_receipt_to_closed_issue_comment_without_new_close(self):
        case = closure_fixtures.IssueClosureTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        original = case.close()
        self.assertEqual(original['status'], 'closed-verified')
        f = case.case
        source = IssueProgressSource(f.execution, delivery=f.service, closure=case.service)
        facts = source.facts(f.identity, 'delivered')
        self.assertEqual(facts['issue_state'], 'closed')
        self.assertEqual(facts['delivered_sha'], case.remote.case.github.head)
        remote = CommentRemote(f.github)
        remote.issue_state = 'closed'
        service = IssueProgress(f.registry, remote, source.observer(f.identity, 'delivered'))
        proposal = service.prepare(facts['repository'], facts['issue_number'], facts['run_id'], facts['event_id'])
        service.send(proposal['id'], approve_sha256=proposal['approval_sha256'], expected_actor_id=42)
        self.assertIn('已交付', remote.rows[0]['body'])
        self.assertEqual(case.remote.writes, 1)  # Original simulated close only; progress never repeats it.
        self.assertEqual(f.github.issue['state'], 'closed')
        self.assertEqual(f.registry.budget(), f.budget)
        self.assertEqual(source.facts(f.identity, 'delivered'), facts)


if __name__ == '__main__':
    unittest.main()
