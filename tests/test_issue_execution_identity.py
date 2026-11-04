"""Existing fixed-Policy journals must remain bound to their original repository."""
from dataclasses import asdict
import unittest

import test_issue_execution as fixtures
from repo_maintainer.demo import create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.issue_execution import IssueExecution
from repo_maintainer.issue_intake import IssueIntake
from repo_maintainer.maintenance_rules import compile_policy,maintenance_rules
from repo_maintainer.models import SafetyError
from repo_maintainer.ui_registry import UIRegistry


class IssueExecutionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.IssueExecutionTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        case=self.case
        policy,_=compile_policy(case.repo,case.job['snapshot'],case.job['report'],case.environment,case.rules)
        case.registry.close()
        self.binding={'repository':str(case.repo.root),'policy':asdict(policy),'provider':case.metadata['provider']}
        case.registry=UIRegistry(case.root/'fixed-policy-state',self.binding)

    def test_fixed_policy_cannot_spend_original_budget_on_another_repository(self):
        case=self.case
        other=RepositoryGateway(create_fixture(case.root/'other-source'))
        rules=maintenance_rules(other,read_roots=['.'],write_roots=['.'],
                                check_ids=case.rules['check_ids'])
        with self.assertRaisesRegex(SafetyError,'registry repository identity differs'):
            IssueExecution(other.root,case.registry,case.github,case.spec,rules,case.provider)
        self.assertFalse(case.model.requests)
        self.assertFalse(case.registry.load('issue_execution'))
        self.assertEqual(case.registry.budget()['requests_used'],0)

    def test_original_fixed_policy_journal_can_execute_with_its_existing_budget(self):
        case=self.case
        case.job=IssueIntake(case.github,case.registry,case.spec).investigate(7,case.repo)
        case.executor=IssueExecution(case.repo.root,case.registry,case.github,case.spec,case.rules,case.provider)
        value=case.run_issue()
        self.assertEqual(value['status'],'awaiting-review',value)
        self.assertEqual(case.registry.budget()['requests_used'],2)
        self.assertEqual(case.registry.budget()['request_budget'],50)
        self.assertEqual(case.executor.runs,case.registry.root.parent)
