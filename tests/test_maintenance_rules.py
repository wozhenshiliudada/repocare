"""Actual committed evidence to original Policy; simulated GitHub, no model calls."""
import copy
from dataclasses import asdict
import json
import unittest
from unittest.mock import patch

import test_issue_intake as fixtures
from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.maintenance_rules import compile_policy, load_rules, maintenance_rules, main
from repo_maintainer.models import SafetyError
from repo_maintainer.target_environment import discover_environment
from repo_maintainer.behavior_validation import make_contract
from repo_maintainer.models import digest
from repo_maintainer.task_session import repository_identity
from repo_maintainer.workflow_behavior import check_id, command_for


class MaintenanceRulesTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.IssueIntakeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture=fixture
        self.repo=fixture.repo
        self.job=fixture.intake.investigate(7,self.repo)
        self.assertEqual(self.job['status'],'investigated')
        self.env=discover_environment(self.repo,self.repo.head,['src','tests'])
        self.rules=maintenance_rules(self.repo,read_roots=['src','tests'],write_roots=['src'],
                                     check_ids=[self.env['checks'][0]['id']])

    def compile(self, **overrides):
        values={'snapshot':self.job['snapshot'],'report':self.job['report'],
                'environment_plan':self.env,'rules':self.rules}
        values.update(overrides)
        return compile_policy(self.repo,**values)

    def test_issue_investigation_selects_actual_files_tests_and_original_policy_without_execution(self):
        before,budget=self.repo.snapshot(),self.fixture.registry.budget()
        with patch('repo_maintainer.runner.run_test',side_effect=AssertionError('No tests during compilation')):
            policy,binding=self.compile()
        self.assertEqual(policy.allowed_paths,('src/calc.py',))
        self.assertIn('tests/test_calc.py',policy.readonly_paths)
        self.assertIn('unittest',policy.allowed_tests[0])
        self.assertEqual(binding['policy'],asdict(policy))
        self.assertEqual(binding['issue_number'],7)
        self.assertFalse(binding['runtime_authorized'])
        self.assertEqual(binding['provider_budget'],'original_registry_only')
        self.assertEqual(before,self.repo.snapshot())
        self.assertEqual(budget,self.fixture.registry.budget())

    def test_behavior_cases_need_explicit_rule_id_and_preserve_existing_regressions(self):
        contract = make_contract(repository_id=repository_identity(self.repo.root,self.repo.common_dir),
            base_sha=self.repo.head,request_sha256=digest(b'current issue'),requirement='Correct adjusted total',
            cases=[{'id':'adjusted','module':'src/calc.py','callable':'adjusted_total','args':[2],
                    'kwargs':{},'expect':{'kind':'json','value':2},'missing_behavior':False}])
        env = discover_environment(self.repo,self.repo.head,['src','tests'],behavior_contract=contract)
        with self.assertRaisesRegex(SafetyError,'explicitly include'):
            self.compile(environment_plan=env)
        rules = maintenance_rules(self.repo,read_roots=['src','tests'],write_roots=['src'],
                                  check_ids=[c['id'] for c in env['checks']])
        policy,_ = self.compile(environment_plan=env,rules=rules)
        self.assertEqual(self.env['checks'][0]['argv'],list(policy.allowed_tests[0]))
        self.assertEqual(command_for(contract),policy.allowed_tests[-1])
        self.assertNotIn('tests/test_calc.py',policy.allowed_paths)
        self.assertIn('tests/test_calc.py',policy.readonly_paths)

    def test_behavior_does_not_raise_general_check_capacity_or_drop_required_checks(self):
        ordinary=['check'+str(i) for i in range(6)]
        with self.assertRaises(SafetyError):
            maintenance_rules(self.repo,read_roots=['src','tests'],write_roots=['src'],check_ids=ordinary)
        values=ordinary[:5]+['behavior-'+'a'*64]
        rules=maintenance_rules(self.repo,read_roots=['src','tests'],write_roots=['src'],check_ids=values)
        self.assertEqual(values,rules['check_ids'])

    def test_arbitrary_model_command_cannot_be_authorized_by_rehashing_plan(self):
        fake=copy.deepcopy(self.env)
        fake['checks'][0]['argv']=['python','-c','raise RuntimeError("injected")']
        fake['plan_sha256']=fingerprint({k:v for k,v in fake.items() if k!='plan_sha256'})
        with self.assertRaisesRegex(SafetyError,'proposal'):
            self.compile(environment_plan=fake)

    def test_actual_candidate_bytes_win_over_claimed_hash_and_reject_substitution(self):
        report=copy.deepcopy(self.job['report'])
        report['file_candidates'][0]['sha256']='0'*64
        with self.assertRaisesRegex(SafetyError,'actual committed bytes'):
            self.compile(report=report)

    def test_issue_labels_and_content_cannot_turn_on_auto_mode(self):
        with self.assertRaisesRegex(SafetyError,'maintainer rule'):
            maintenance_rules(self.repo,read_roots=['src','tests'],write_roots=['src'],
                              check_ids=self.rules['check_ids'],mode='auto')
        auto=maintenance_rules(self.repo,read_roots=['src','tests'],write_roots=['src'],
                               check_ids=self.rules['check_ids'],mode='auto',allow_auto=True)
        _,binding=self.compile(rules=auto)
        self.assertEqual(binding['mode'],'auto')
        self.assertFalse(binding['runtime_authorized'])

    def test_out_of_scope_implementation_requires_scope_review_not_automatic_widening(self):
        rules=maintenance_rules(self.repo,read_roots=['src','tests'],write_roots=['tests'],
                                check_ids=self.rules['check_ids'])
        with self.assertRaisesRegex(SafetyError,'No editable implementation'):
            self.compile(rules=rules)

    def test_original_pi_context_capacity_is_not_silently_truncated(self):
        rules=maintenance_rules(self.repo,read_roots=['src','tests'],write_roots=['src'],
                                check_ids=self.rules['check_ids'],max_files=1)
        with self.assertRaisesRegex(SafetyError,'file limit'):
            self.compile(rules=rules)

    def test_closed_changed_issue_or_target_cannot_compile_as_current(self):
        for field,value in [('status','ignored_closed'),('head_sha','0'*40)]:
            snapshot=copy.deepcopy(self.job['snapshot'])
            snapshot[field]=value
            with self.subTest(field=field),self.assertRaises(SafetyError):
                self.compile(snapshot=snapshot)
        self.repo.command('remote','set-url','origin','https://github.com/example/other.git')
        with self.assertRaisesRegex(SafetyError,'origin'):
            self.compile()

    def test_repository_rules_cannot_load_from_untrusted_target_or_change_silently(self):
        local=self.repo.root/'rules.json'
        local.write_text(json.dumps(self.rules),encoding='utf-8')
        with self.assertRaisesRegex(SafetyError,'outside repository'):
            load_rules(local,self.repo)
        external=self.fixture.root/'rules.json'
        external.write_text(json.dumps(self.rules),encoding='utf-8')
        self.assertEqual(load_rules(external,self.repo),self.rules)
        wrong=copy.deepcopy(self.rules)
        wrong['write_roots']=['.']
        external.write_text(json.dumps(wrong),encoding='utf-8')
        with self.assertRaises(SafetyError):
            load_rules(external,self.repo)

    def test_check_selection_requires_explicit_maintainer_identifier(self):
        rules=maintenance_rules(self.repo,read_roots=['src','tests'],write_roots=['src'],
                                check_ids=['unknown-test'])
        with self.assertRaisesRegex(SafetyError,'outside maintainer rules'):
            self.compile(rules=rules)

    def test_source_head_drift_preserves_user_work_and_requires_reinvestigation(self):
        path=self.repo.root/'src/calc.py'
        path.write_text('def adjusted_total(n): return n\n',encoding='utf-8')
        self.repo.command('add','src/calc.py')
        self.repo.command('-c','user.name=Rules fixture','-c','user.email=fixture@example.invalid',
                          'commit','-m','later user work')
        before=self.repo.snapshot()
        with self.assertRaisesRegex(SafetyError,'target differs'):
            self.compile()
        self.assertEqual(before,self.repo.snapshot())

    def test_cli_writes_external_compilation_without_claiming_fresh_remote_authority(self):
        root=self.fixture.root
        paths={}
        for name,value in [('rules',self.rules),('snapshot',self.job['snapshot']),
                           ('report',self.job['report']),('environment',self.env)]:
            path=root/(name+'.json')
            path.write_text(json.dumps(value),encoding='utf-8')
            paths[name]=str(path)
        output=root/'policy.json'
        args=['--repo',str(self.repo.root),'--rules',paths['rules'],'--issue-snapshot',paths['snapshot'],
              '--investigation',paths['report'],'--environment-plan',paths['environment'],'--output',str(output)]
        before=self.repo.snapshot()
        main(args)
        value=json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(value['policy']['allowed_paths'],['src/calc.py'])
        self.assertFalse(value['remote_freshness_verified'])
        self.assertFalse(value['runtime_authorized'])
        with self.assertRaisesRegex(SafetyError,'new and outside'):
            main(args)
        self.assertEqual(before,self.repo.snapshot())

    def test_broad_write_root_still_keeps_original_checks_readonly(self):
        rules=maintenance_rules(self.repo,read_roots=['src','tests'],write_roots=['src','tests'],
                                check_ids=self.rules['check_ids'])
        policy,_=self.compile(rules=rules)
        self.assertNotIn('tests/test_calc.py',policy.allowed_paths)
        self.assertIn('tests/test_calc.py',policy.readonly_paths)

    def test_focused_plan_rebuilds_from_actual_task_report_and_rejects_rehashed_changes(self):
        from repo_maintainer.repository_diagnosis import build_report
        report=build_report(self.repo,head_sha=self.repo.head,read_roots=['src','tests'],
                            search=True,query='adjusted_total test_total')
        env=discover_environment(self.repo,self.repo.head,['src','tests'],investigation=report)
        policy,binding=self.compile(report=report,environment_plan=env)
        self.assertEqual(policy.allowed_paths,('src/calc.py',))
        self.assertIn('--pattern=test_calc.py',policy.allowed_tests[0])
        self.assertEqual(binding['environment_plan_sha256'],env['plan_sha256'])
        fake=copy.deepcopy(env)
        fake['selection']['selected_tests']=[]
        fake['plan_sha256']=fingerprint({k:v for k,v in fake.items() if k!='plan_sha256'})
        with self.assertRaisesRegex(SafetyError,'proposal'):
            self.compile(report=report,environment_plan=fake)
        fake=copy.deepcopy(env)
        fake['checks'][0]['argv']=['python','-c','raise RuntimeError("untrusted")']
        fake['plan_sha256']=fingerprint({k:v for k,v in fake.items() if k!='plan_sha256'})
        with self.assertRaisesRegex(SafetyError,'proposal'):
            self.compile(report=report,environment_plan=fake)

    def test_focused_required_checks_need_new_scope_instead_of_reusing_partial_permission(self):
        from repo_maintainer.repository_diagnosis import build_report
        (self.repo.root/'tests/test_more.py').write_text('import unittest\nclass T(unittest.TestCase):\n'
            '    def test_total(self): self.assertTrue(True)\n',encoding='utf-8')
        self.repo.command('add','tests/test_more.py')
        self.repo.command('-c','user.name=Rules fixture','-c','user.email=fixture@example.invalid',
                          'commit','-m','another test')
        self.fixture.github.head=self.repo.head
        job=self.fixture.intake.investigate(7,self.repo)
        report=build_report(self.repo,head_sha=self.repo.head,read_roots=['src','tests'],
                            search=True,query='test_total adjusted_total')
        env=discover_environment(self.repo,self.repo.head,['src','tests'],investigation=report)
        self.assertEqual(len(env['checks']),2)
        with self.assertRaisesRegex(SafetyError,'scope review'):
            self.compile(snapshot=job['snapshot'],report=report,environment_plan=env)
