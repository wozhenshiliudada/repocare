"""Real original repair/worktrees/journals with granted environments; no model calls."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_target_environment as fixtures
from repo_maintainer.integration import prepare_integration
from repo_maintainer.models import Edit,Plan,Policy,SafetyError,digest
from repo_maintainer.target_environment import discover_environment,make_grant
from repo_maintainer.workflow import repair,cleanup_run
from repo_maintainer.workflow_environment import GrantedEnvironment


class AuthoredProvider:
    max_requests=0
    evidence={}
    def __init__(self,name,content):
        self.name,self.content=name,content
    def plan(self,repo,issue,tests):
        return Plan(issue,repo.head,'Authored deterministic fixture','Fixture only',
                    (Edit(self.name,digest(repo.read(self.name)),self.content),),tests,'authored-no-model')


class WorkflowEnvironmentTests(unittest.TestCase):
    def setUp(self):
        case=fixtures.TargetEnvironmentTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.case,self.repo=case,case.repo

    def setup_environment(self,language='python',dependency=False):
        head=self.case.python_project(dependency) if language=='python' else self.case.node_project()
        if dependency:
            fixtures.wheel(self.case.root/'wheelhouse')
        plan=discover_environment(self.repo,head,['.'])
        grant=make_grant(plan,allow_local_code=True,allow_install=dependency or language=='node',
                         wheelhouse=self.case.root/'wheelhouse' if dependency else None)
        policy=Policy(('calc.py' if language=='python' else 'calc.cjs',),
                      tuple(tuple(c['argv']) for c in plan['checks']),timeout_seconds=5)
        return plan,grant,policy

    def run_repair(self,plan,grant,policy,*,mode='auto',approval=None,content=None):
        name=policy.allowed_paths[0]
        provider=AuthoredProvider(name,content or ('def total(n):\n    return n*2\n' if name.endswith('.py')
                                                 else 'exports.total=n=>n*2;\n'))
        return repair(self.repo.root,'Authored issue',provider,policy,mode,self.case.root/'runs',
                      approval=approval,environment_factory=GrantedEnvironment(plan,grant))

    def test_real_python_install_red_green_uses_original_workflow_journal_and_same_runtime(self):
        plan,grant,policy=self.setup_environment(dependency=True)
        before=self.repo.snapshot()
        with patch('repo_maintainer.workflow.run_test',side_effect=AssertionError('No legacy runtime fallback')):
            record=self.run_repair(plan,grant,policy)
        self.assertEqual(record['status'],'candidate_ready',record.get('error'))
        self.assertEqual(record['baseline_tests'][0]['category'],'test_failed')
        self.assertEqual(record['candidate_tests'][0]['category'],'passed')
        self.assertNotEqual(record['candidate_tests'][0]['command'][0],sys.executable)
        self.assertTrue(record['rounds'][0]['environment_comparison']['candidate_verified'])
        execution=json.loads((Path(record['workspace']).parent/'repair-execution.json').read_text(encoding='utf-8'))
        self.assertTrue(execution['process_facts'])
        self.assertIsNone(execution['inflight'])
        self.assertTrue(all(p['kind']=='environment_command' and p['status']=='finished' for p in execution['process_facts']))
        self.assertEqual(before,self.repo.snapshot(exclude_branch=record['branch']))
        cleanup_run(Path(record['workspace']).parent)
        self.assertEqual(before,self.repo.snapshot())

    def test_revision_projection_binds_actual_environment_receipts_without_changing_reports(self):
        import copy
        plan,grant,policy=self.setup_environment()
        captured=[]
        factory=GrantedEnvironment(plan,grant)
        def prepare(*args):
            environment=factory(*args);captured.append(environment);return environment
        record=repair(self.repo.root,'Authored issue',AuthoredProvider('calc.py','def total(n):\n    return n*2\n'),
            policy,'auto',self.case.root/'runs',environment_factory=prepare)
        self.assertEqual(record['status'],'candidate_ready',record.get('error'))
        self.addCleanup(cleanup_run,Path(record['workspace']).parent)
        environment=captured[0]
        actual=copy.deepcopy(record['candidate_tests'])
        projected=environment.revision_checks(actual,policy.allowed_tests)
        self.assertEqual(projected[0]['command'][0],sys.executable)
        self.assertEqual(projected[0]['execution_command'],actual[0]['command'])
        self.assertNotEqual(actual[0]['command'][0],sys.executable)
        self.assertEqual(actual,record['candidate_tests'])
        for field,value in [('command',[sys.executable,'-c','print(1)']),
                            ('environment_sha256','0'*64),('stdout','forged output')]:
            foreign=copy.deepcopy(actual);foreign[0][field]=value
            with self.assertRaises(SafetyError):environment.revision_checks(foreign,policy.allowed_tests)
        with self.assertRaises(SafetyError):environment.revision_checks(actual,[('node','other.js')])

    def test_real_node_install_is_checkpointed_and_candidate_uses_same_dependencies(self):
        plan,grant,policy=self.setup_environment('node')
        record=self.run_repair(plan,grant,policy)
        self.assertEqual(record['status'],'candidate_ready',record.get('error'))
        self.assertTrue(record['rounds'][0]['environment_comparison']['candidate_verified'])
        self.assertFalse(list(Path(record['workspace']).rglob('INSTALL_MARKER')))
        cleanup_run(Path(record['workspace']).parent)

    def test_approval_rejection_never_prepares_environment_or_installs(self):
        plan,grant,policy=self.setup_environment()
        record=self.run_repair(plan,grant,policy,mode='approval',approval=lambda _: 'reject')
        self.assertEqual(record['status'],'rejected')
        self.assertNotIn('environment',record)
        self.assertFalse((Path(record['workspace']).parent/'environment').exists())

    def test_missing_install_grant_is_rejected_before_model_planning_or_worktree_creation(self):
        plan,_,_=self.setup_environment(dependency=True)
        grant=make_grant(plan,allow_local_code=True)
        before=self.repo.snapshot()
        with self.assertRaisesRegex(SafetyError,'installation permission'):
            GrantedEnvironment(plan,grant)
        self.assertFalse((self.case.root/'runs').exists())
        self.assertEqual(before,self.repo.snapshot())

    def test_missing_baseline_dependency_stops_before_any_edit_or_candidate_test(self):
        self.case.python_project()
        self.case.write('calc.py','import fixture_missing_baseline\ndef total(n): return n+1\n')
        head=self.case.commit()
        plan=discover_environment(self.repo,head,['.'])
        grant=make_grant(plan,allow_local_code=True)
        policy=Policy(('calc.py',),tuple(tuple(c['argv']) for c in plan['checks']))
        before=self.repo.snapshot()
        record=self.run_repair(plan,grant,policy)
        self.assertEqual(record['status'],'rolled_back',record)
        self.assertEqual(record['baseline_tests'][0]['category'],'environment_blocked')
        self.assertFalse(record['candidate_tests'])
        self.assertIn('inconclusive',record['error'])
        self.assertEqual(before,self.repo.snapshot())

    def test_candidate_import_failure_cannot_pass_or_trigger_legacy_runtime(self):
        plan,grant,policy=self.setup_environment()
        record=self.run_repair(plan,grant,policy,content='import fixture_missing_candidate\ndef total(n): return n*2\n')
        self.assertEqual(record['status'],'rolled_back')
        self.assertEqual(record['candidate_tests'][0]['category'],'environment_blocked')
        self.assertTrue(record['source_unchanged'])

    def test_new_environment_candidate_never_uses_legacy_integration_runtime(self):
        plan,grant,policy=self.setup_environment()
        record=self.run_repair(plan,grant,policy)
        self.assertEqual(record['status'],'candidate_ready',record.get('error'))
        owner=Path(record['workspace']).parent
        with patch('repo_maintainer.integration.run_test',side_effect=AssertionError('No legacy runtime')):
            review=prepare_integration(owner)
        self.assertEqual(review['status'],'prepared',review)
        self.assertTrue(review['review']['environment_verification']['comparison']['candidate_verified'])
        self.assertFalse((owner/'integration-verification').exists())
        self.assertTrue((owner/'integration-review.json').exists())
        cleanup_run(owner)
