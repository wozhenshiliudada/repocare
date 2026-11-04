"""Original Pi/Native/ledger, granted Node worktrees and seal; synthetic SSE only."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_target_environment as environment_fixtures
from test_task_planning import SyntheticModel
from test_workflow_environment import AuthoredProvider
from repo_maintainer.behavior_validation import make_contract
from repo_maintainer.integration import prepare_integration
from repo_maintainer.models import Policy, SafetyError, digest
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.target_environment import discover_environment, make_grant
from repo_maintainer.task_planning import TaskPlanner
from repo_maintainer.task_session import repository_identity
from repo_maintainer.ui_registry import UIRegistry, DurableProvider
from repo_maintainer.validation_proposal import source_packet, ValidationProposal
from repo_maintainer.workflow import repair, cleanup_run
from repo_maintainer.workflow_environment import GrantedEnvironment


class NodeBehaviorWorkflowTests(unittest.TestCase):
    def setUp(self):
        case=environment_fixtures.TargetEnvironmentTests();case.setUp();self.addCleanup(case.doCleanups)
        self.case,self.repo=case,case.repo
        self.issue='total(2,3) must return 5; existing() must still return 7'

    def setup_project(self, *, module='calc.mjs', old_tests=False, missing=False, syntax=False):
        initial=('export function existing(){return 7;}\n' if missing else
                 'export function total(a,b){return a-b;}\nexport function existing(){return 7;}\n')
        if module.endswith('.mts'):
            initial=initial.replace('total(a,b)','total(a:number,b:number):number')
        if syntax:initial='export function total( broken;'
        self.case.write(module,initial)
        if old_tests:
            self.case.write('test/existing.test.mjs',
                "import test from 'node:test';import assert from 'node:assert/strict';\n"
                "import {existing} from '../"+module+"';\n"
                "test('original existing behavior',()=>assert.equal(existing(),7));\n")
        head=self.case.commit()
        case={'id':'sum','module':module,'callable':'total','args':[2.0,3.0],
              'kwargs':{},'expect':{'kind':'json','value':5.0},'missing_behavior':missing}
        contract=make_contract(repository_id=repository_identity(self.repo.root,self.repo.common_dir),
            base_sha=head,request_sha256=digest(self.issue.encode()),requirement=self.issue,cases=[case])
        return module,contract

    def environment(self,module,contract):
        plan=discover_environment(self.repo,self.repo.head,['.'],behavior_contract=contract)
        self.assertEqual('node',plan['runtime']);self.assertEqual([],plan['blockers'])
        grant=make_grant(plan,allow_local_code=True,timeout=30)
        policy=Policy((module,),tuple(tuple(c['argv']) for c in plan['checks']),timeout_seconds=10,
                      readonly_paths=tuple(p for p in self.repo.command('ls-files').decode().splitlines() if p.startswith('test/')))
        return plan,grant,policy

    def test_actual_reviewed_proposal_original_pi_red_green_native_ledger_and_immutable_seal(self):
        self.actual_replay()

    def test_actual_native_typescript_no_tests_and_seal(self):
        self.actual_replay(module='calc.mts',old_tests=False)

    def actual_replay(self,module='calc.mjs',old_tests=True):
        module,seed=self.setup_project(module=module,old_tests=old_tests,missing=old_tests)
        before=self.repo.snapshot()
        model=SyntheticModel();self.addCleanup(model.close)
        provider=PiProvider((),model.url,(module,),max_requests=5)
        registry=UIRegistry(self.case.root/'original-ledger',{'provider':{'request_budget':5}})
        self.addCleanup(registry.close)
        proposal={'requirement':self.issue,'rationale':'Explicit business result independent of current output',
            'limitations':'Standalone export and original native Node subset only','clarification':False,
            'cases_json':json.dumps(seed['cases'],ensure_ascii=False)}
        model.schema_calls=[('propose_validation',proposal)]
        source=source_packet(self.repo,self.issue,['.'],[module],{'original_scope':'controller-confirmed'})
        service=ValidationProposal(registry,self.repo,provider,source,lambda:None,'local-session')
        preview=service.preview();self.assertEqual([],model.requests)
        proposed=service.propose(preview['revision'])
        self.assertEqual('awaiting_validation_review',proposed['status'])
        self.assertIsNone(service.accepted())
        service.review(service.revision(proposed),'accept')
        contract=service.accepted();self.assertEqual('node-behavior-v1',contract['kind'])
        self.assertEqual(1,len(model.requests))
        original_task=registry.load('pi_task')[proposed['task_id']]
        self.assertEqual(3,original_task['context']['schema_version'])
        self.assertEqual('data-behavior-v2',original_task['context']['validation_schema'])
        original_native=list((self.case.root/'original-ledger').rglob('*.jsonl'))
        native_pins={str(p):digest(p.read_bytes()) for p in original_native}
        self.assertTrue(native_pins)
        plan,grant,policy=self.environment(module,contract)
        repair_provider=PiProvider((module,),model.url,policy.readonly_paths,max_requests=5)
        durable=DurableProvider(repair_provider,registry)
        planner=TaskPlanner(registry,self.repo.root,policy,repair_provider,{'behavior_contract':contract['contract_sha256']})
        durable.task_planner=planner;task=planner.begin(self.issue)
        typed=module.endswith('.mts')
        replacement=('export function total(a:number,b:number):number{return a+b;}' if typed else
                     'export function total(a,b){return a+b;}')
        model.schema_calls=[('propose_repair',{'diagnosis':'Required addition export','risk':'Synthetic fixture only',
            'edits':[{'path':module,'replacements':[{'oldText':self.repo.read(module).decode(),
                'newText':replacement+'\nexport function existing(){return 7;}\n'}]}]})]
        with patch('repo_maintainer.workflow.run_test',side_effect=AssertionError('No host execution fallback')):
            record=repair(self.repo.root,self.issue,durable,policy,'auto',self.case.root/'runs',
                          environment_factory=GrantedEnvironment(plan,grant))
        self.assertEqual('candidate_ready',record['status'],record.get('error'))
        owner=Path(record['workspace']).parent;self.addCleanup(cleanup_run,owner)
        self.assertEqual(['passed','test_failed'] if old_tests else ['test_failed'],
                         [row['category'] for row in record['baseline_tests']])
        self.assertTrue(all(row['category']=='passed' for row in record['candidate_tests']))
        self.assertEqual(contract['contract_sha256'],record['provider_evidence']['behavior_contract_sha256'])
        self.assertTrue(record['rounds'][0]['behavior_comparison']['candidate_verified'])
        user=next(m['content'] for m in model.requests[-1]['messages'] if m['role']=='user')
        packet=json.loads(user if isinstance(user,str) else user[0]['text'])
        self.assertIn('2.0',packet['behavior_context']['body_json'])
        self.assertEqual(contract['contract_sha256'],digest(packet['behavior_context']['body_json'].encode()))
        self.assertEqual(1,registry.load('pi_task')[task]['revision'])
        self.assertEqual(original_task,registry.load('pi_task')[proposed['task_id']])
        self.assertEqual(native_pins,{name:digest(Path(name).read_bytes()) for name in native_pins})
        budget=registry.budget();self.assertEqual((2,0,3),tuple(budget[k] for k in
            ('requests_used','requests_reserved_unknown','requests_remaining')))
        with patch('repo_maintainer.integration.run_test',side_effect=AssertionError('No seal host fallback')):
            sealed=prepare_integration(owner)
        self.assertEqual('prepared',sealed['status'])
        checks=sealed['review']['tests'];self.assertTrue(all(c['category']=='passed' for c in checks))
        receipt=checks[-1]['behavior_receipt'];self.assertEqual('node-behavior-result-v1',receipt['kind'])
        self.assertEqual(record['candidate_tests'][-1]['behavior_receipt']['node_sha256'],receipt['node_sha256'])
        self.assertEqual(budget,registry.budget());self.assertEqual(2,len(model.requests))
        self.assertEqual(before,self.repo.snapshot(exclude_branch=record['branch']))
        if os.environ.get('R1_JS_EVIDENCE_DIR'):
            destination=Path(os.environ['R1_JS_EVIDENCE_DIR'])/module
            destination.mkdir(parents=True,exist_ok=False)
            for name,value in [('run.json',record),('review.json',sealed),('budget.json',budget),
                               ('proposal.json',proposed),('source-before.json',before),('sse-payloads.json',model.requests)]:
                (destination/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
            (destination/'sealed.patch').write_bytes(self.repo.command('show',sealed['review']['commit']))
            for number,path in enumerate(list((self.case.root/'original-ledger').rglob('*.jsonl'))):
                (destination/f'native-{number}.jsonl').write_bytes(path.read_bytes())
            for phase,receipt in [('baseline',record['baseline_tests'][-1]['behavior_receipt']),
                                  ('candidate',record['candidate_tests'][-1]['behavior_receipt']),('sealed',checks[-1]['behavior_receipt'])]:
                folder=destination/phase;folder.mkdir()
                for path in Path(receipt['artifact_directory']).iterdir():
                    if path.is_file():(folder/path.name).write_bytes(path.read_bytes())

    def test_new_behavior_cannot_regress_old_green_node_suite(self):
        module,contract=self.setup_project(old_tests=True,missing=True)
        plan,grant,policy=self.environment(module,contract)
        record=repair(self.repo.root,self.issue,
            AuthoredProvider(module,'export const total=(a,b)=>a+b;export const existing=()=>8;'),
            policy,'auto',self.case.root/'runs',environment_factory=GrantedEnvironment(plan,grant))
        self.assertEqual('rolled_back',record['status'],record.get('error'))
        self.assertEqual('regressed',record['rounds'][0]['check_comparison']['checks'][0]['transition'])

    def test_syntax_cannot_be_red_baseline_even_with_expected_missing_export(self):
        module,contract=self.setup_project(syntax=True,missing=True)
        plan,grant,policy=self.environment(module,contract)
        record=repair(self.repo.root,self.issue,AuthoredProvider(module,'export const total=(a,b)=>a+b;'),
            policy,'auto',self.case.root/'runs',environment_factory=GrantedEnvironment(plan,grant))
        self.assertEqual('rolled_back',record['status'],record.get('error'))
        self.assertEqual('inconclusive',record['baseline_tests'][0]['category'])
