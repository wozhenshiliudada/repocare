"""Deterministic projection controls plus actual Pi/journal acceptance below."""
import copy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from repo_maintainer.experience_memory import ExperienceMemory, KIND
from repo_maintainer.models import Edit, Plan, Policy, SafetyError
from repo_maintainer.task_session import fingerprint

RUN='b'*32
TASK='c'*32


class Registry:
    def __init__(self):
        self.values={'pi_task_link':{'run:'+RUN:{'task_id':TASK}}}
    def load(self,kind):return copy.deepcopy(self.values.get(kind,{}))
    def put(self,kind,identity,value):self.values.setdefault(kind,{})[identity]=copy.deepcopy(value)


def projection_fixture(root, passed=True):
    """Synthetic report input for the projection only; not controller authenticity proof."""
    policy=Policy(('calculator.py',),(('fixed-test',),))
    plan=Plan('修复加法','e'*40,'Preserve signed addition','fixture',
              (Edit('calculator.py','a'*64,'return a + b'),),policy.allowed_tests,'projection-fixture')
    check={'command':['fixed-test'],'returncode':0 if passed else 1,'timed_out':False,
           'stdout':json.dumps({'metadata':'REPORT_METADATA_ONLY_'*150,'success':['test_add'],
                               'failure_excerpt':'AssertionError: -3 != 5' if not passed else ''}),
           'stderr':''}
    row={'number':1,'plan':asdict(plan),'plan_sha256':plan.fingerprint,
         'verification':'passed' if passed else 'failed','diff':'- return a - b\n+ return a + b', 'tests':[check]}
    source=Path('projection-fixture-source')
    record={'run_id':RUN,'finished_at':'synthetic-completion','source_unchanged':True,
            'source_repo':str(source),'source_before':{'files':{'calculator.py':'a'*64}},
            'issue':plan.issue,'base_sha':plan.base_sha,'status':'candidate_ready' if passed else 'rolled_back',
            'rounds':[row]}
    def save():
        (root/'run.json').write_bytes(json.dumps(record,ensure_ascii=False).encode())
    save()
    (root/'repair-execution.json').write_bytes(b'{"inflight": null}')
    (root/'report.md').write_bytes(b'# synthetic projection control\n')
    registry=Registry()
    session=SimpleNamespace(repo=source,policy=policy,provider=SimpleNamespace(inner=SimpleNamespace(readonly_paths=())),
        _run_dir=lambda identity:root,registry=registry,task_planner=object(),
        budget_registry=SimpleNamespace(budget=lambda:{'requests_reserved_unknown':0}))
    # This fixture tests only projection/reconstruction. Real construction, capture,
    # review, native history and approval are covered by the production tests below.
    memory=object.__new__(ExperienceMemory)
    memory.session=session;memory.repository='d'*64
    return memory,record,save


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.memory,self.record,self.save=projection_fixture(self.root)

    def test_new_success_is_compact_counts_with_original_method_and_provenance(self):
        payload=self.memory.original(RUN)
        self.assertEqual(4,payload['schema_version'])
        row=payload['rounds'][0]
        self.assertNotIn('failure_excerpt',row)
        self.assertEqual({'scope':'selected_commands','passed':1,'total':1,
                          'semantic_correctness_proven':False},row['validation_summary'])
        self.assertEqual('Preserve signed addition',row['diagnosis'])
        self.assertIn('return a + b',row['change_excerpt'])
        self.assertEqual({'calculator.py':'a'*64},payload['source_files'])
        self.assertEqual({'run.json','repair-execution.json','report.md'},set(payload['evidence']))
        self.assertFalse(payload['runtime_authorized'])
        self.assertEqual('historical',payload['classification'])

    def test_legacy_v1_v2_v3_reconstruction_matches_prechange_golden_hashes(self):
        # Frozen from the actual pre-change implementation on this deterministic input.
        expected={1:'c665288a6e4148b6987e79029cb8b57b653540fda88f01885ff25ddc8322784c',
                  2:'c660bb016b93967e7b6f26a11ad3a8d47fbe1694a91058085c75977e8aca9fde',
                  3:'84327af0b90b31783c85a8db94df7df686cca1eafef7422ecd6efa942b13b21c'}
        for version,sha in expected.items():
            self.assertEqual(sha,fingerprint(self.memory.original(RUN,version=version)))

    def test_recapture_of_accepted_legacy_success_does_not_upgrade_or_reset_review(self):
        for version in (1,2,3):
            payload=self.memory.original(RUN,version=version)
            entry={'payload':payload,'payload_sha256':fingerprint(payload),'review':'accepted','review_version':7}
            self.memory.session.registry.put(KIND,RUN,entry)
            before=self.memory.session.registry.load(KIND)
            self.assertTrue(self.memory.capture(RUN)['unchanged'])
            self.assertEqual(before,self.memory.session.registry.load(KIND))

    def test_metadata_heavy_success_is_smaller_but_no_real_token_or_model_gain_claim(self):
        # Some successful JSON reports have no failure_excerpt field. Legacy v3
        # already unwraps an existing empty field; do not misstate that case.
        check=self.record['rounds'][0]['tests'][0]
        report=json.loads(check['stdout']);report.pop('failure_excerpt')
        check['stdout']=json.dumps(report);self.save()
        old=self.memory.original(RUN,version=3);new=self.memory.original(RUN)
        self.assertIn('REPORT_METADATA_ONLY_',old['rounds'][0]['failure_excerpt'])
        self.assertNotIn('REPORT_METADATA_ONLY_',json.dumps(new))
        self.assertLess(len(json.dumps(new).encode()),len(json.dumps(old).encode()))

    def test_failed_attempts_remain_version3_with_original_failure_excerpt(self):
        memory,_,_=projection_fixture(self.root,passed=False)
        payload=memory.original(RUN)
        self.assertEqual(3,payload['schema_version'])
        self.assertIn('AssertionError',payload['rounds'][0]['failure_excerpt'])
        with self.assertRaises(SafetyError):memory.original(RUN,version=4)

    def test_success_after_failure_preserves_earlier_errors_without_success_report_noise(self):
        failed=copy.deepcopy(self.record['rounds'][0])
        failed['verification']='failed';failed['tests'][0]['returncode']=1
        failed['tests'][0]['stdout']='AssertionError: previous wrong attempt'
        self.record['rounds'][0]['number']=2
        self.record['rounds'].insert(0,failed);self.save()
        rows=self.memory.original(RUN)['rounds']
        self.assertIn('AssertionError',rows[0]['failure_excerpt'])
        self.assertNotIn('validation_summary',rows[0])
        self.assertNotIn('failure_excerpt',rows[1])
        self.assertEqual(1,rows[1]['validation_summary']['total'])

    def test_false_green_timeout_or_incomplete_commands_cannot_make_success_summary(self):
        for change in ({'returncode':1},{'timed_out':True},{'command':['unapproved']}):
            original=copy.deepcopy(self.record)
            self.record['rounds'][0]['tests'][0].update(change);self.save()
            with self.subTest(change=change),self.assertRaises(SafetyError):self.memory.original(RUN)
            self.record=original
            # Save closure captures the original dictionary; restore it in-place below.
            self.memory,self.record,self.save=projection_fixture(self.root)
        # Even internally consistent fake counts and new hashes cannot replace
        # comparison with the actual original receipt (one selected command).
        payload=self.memory.original(RUN)
        entry={'payload':payload,'payload_sha256':fingerprint(payload),'review':'accepted','review_version':1}
        self.memory.validate(RUN,entry)
        row=payload['rounds'][0];row['test_exits']=[0,0];row['test_timeouts']=[False,False]
        row['validation_summary'].update(passed=2,total=2)
        entry['payload_sha256']=fingerprint(payload)
        with self.assertRaises(SafetyError):self.memory.validate(RUN,entry)

    def test_unknown_or_boolean_version_is_rejected(self):
        for version in (0,5,True,'4'):
            with self.subTest(version=version),self.assertRaises(SafetyError):
                self.memory.original(RUN,version=version)


import test_task_planning as fixture
import test_experience_memory as experience_fixture
import test_knowledge_memory as knowledge_fixture


class SuccessIntegrationTests(unittest.TestCase):
    """Production Pi AgentSession and controller, temporary Git, local synthetic SSE only."""
    setUp=fixture.TaskPlanningTests.setUp
    open=fixture.TaskPlanningTests.open
    restart=fixture.TaskPlanningTests.restart
    plan=fixture.TaskPlanningTests.plan
    decision=fixture.TaskPlanningTests.decision
    record=fixture.TaskPlanningTests.record
    accept=experience_fixture.ExperienceTests.accept
    enable=experience_fixture.ExperienceTests.enable
    envelope=experience_fixture.ExperienceTests.envelope

    def success(self):
        self.model.scenario='correct'
        run=self.decision(self.plan(),'approve')
        self.assertEqual('candidate_ready',run['workflow_status'])
        self.assertTrue(self.session.state()['runs'][-1]['source_unchanged'])
        return run

    def test_success_restart_native_read_new_approval_and_real_tests(self):
        raw=(self.source/'calculator.py').read_bytes();run=self.success()
        identity=run['run_id'];old=self.record()['task_id']
        entry=self.session.registry.load(KIND)[identity]
        self.assertEqual('pending',entry['review']);self.assertEqual(4,entry['payload']['schema_version'])
        before=self.session.registry.path.read_bytes()
        self.assertTrue(self.session.experience.capture(identity)['unchanged'])
        self.assertEqual(before,self.session.registry.path.read_bytes())
        self.accept(run);self.enable();budget=self.session.registry.budget();self.restart()
        self.assertEqual(budget,self.session.registry.budget())
        plan=self.plan();self.assertNotEqual(old,self.record()['task_id'])
        context=self.envelope()['repository_experience'];episode=context['hits'][0]['episode']
        self.assertEqual(4,episode['schema_version'])
        self.assertEqual(1,episode['rounds'][0]['validation_summary']['passed'])
        self.assertNotIn('failure_excerpt',episode['rounds'][0])
        self.assertFalse(episode['runtime_authorized'])
        view=self.session.state()['plans'][-1]
        self.assertEqual('awaiting_approval',view['status'])
        self.assertEqual(context['context_sha256'],view['planning_evidence']['experience_context_sha256'])
        self.assertEqual('candidate_ready',self.decision(plan,'approve')['workflow_status'])
        self.assertEqual(4,len(self.model.requests));self.assertEqual(4,self.session.registry.budget()['requests_used'])
        self.assertEqual(0,self.session.registry.budget()['requests_reserved_unknown'])
        self.assertEqual(raw,(self.source/'calculator.py').read_bytes())

    def test_legacy_success_keeps_original_version_review_and_native_selection(self):
        run=self.success();identity=run['run_id'];self.accept(run)
        for version in (1,2,3):
            # Reconstruct the old wire format in this owned fixture, not a migration of user data.
            entry=self.session.registry.load(KIND)[identity]
            entry['payload']=self.session.experience.original(identity,version=version)
            entry['payload_sha256']=fingerprint(entry['payload'])
            self.session.registry.put(KIND,identity,entry)
            before=self.session.registry.path.read_bytes()
            self.session.experience.validate(identity,entry)
            self.assertTrue(self.session.experience.capture(identity)['unchanged'])
            self.assertEqual(before,self.session.registry.path.read_bytes())
        self.enable();self.plan();task=self.record()['task_id']
        self.assertEqual(3,self.envelope()['repository_experience']['hits'][0]['episode']['schema_version'])
        self.restart()
        from repo_maintainer.git import RepositoryGateway
        self.assertEqual(3,self.session.experience.context(fixture.ISSUE,
            RepositoryGateway(self.source),task)['hits'][0]['episode']['schema_version'])

    def test_v4_original_report_drift_stops_approval_without_more_requests(self):
        run=self.success();self.accept(run);self.enable();plan=self.plan()
        path=self.root/'state'/run['run_id']/'report.md';raw=path.read_bytes()
        try:
            path.write_bytes(raw+b' changed')
            before=self.session.registry.budget()
            self.assertFalse(self.session.experience.search(fixture.ISSUE)['hits'])
            with self.assertRaises(SafetyError):self.decision(plan,'approve')
            self.assertEqual(before,self.session.registry.budget());self.assertEqual(4,len(self.model.requests))
        finally:path.write_bytes(raw)

    def test_v4_revocation_stops_new_approval_without_refund(self):
        run=self.success();self.accept(run);self.enable();plan=self.plan();self.accept(run,'rejected')
        before=self.session.registry.budget()
        with self.assertRaises(SafetyError):self.decision(plan,'approve')
        self.assertEqual(before,self.session.registry.budget());self.assertEqual(4,len(self.model.requests))

    def test_v4_success_after_actual_failed_round_keeps_failure_and_counts(self):
        self.restart(auto=True,attempts=3);self.model.scenario='revise'
        run=self.session._operate('auto',{'issue':fixture.ISSUE})
        self.assertEqual('candidate_ready',run['workflow_status'])
        entry=self.session.registry.load(KIND)[run['run_id']]
        self.assertEqual(4,entry['payload']['schema_version']);rows=entry['payload']['rounds']
        self.assertEqual(['failed','passed'],[r['verification'] for r in rows])
        self.assertIn('AssertionError',rows[0]['failure_excerpt'])
        self.assertEqual(1,rows[1]['validation_summary']['passed'])
        self.assertNotIn('failure_excerpt',rows[1]);self.assertEqual(4,len(self.model.requests))
        self.assertTrue(self.session.state()['runs'][-1]['source_unchanged'])

    def test_v4_reflection_uses_original_receipts_and_creates_only_pending_knowledge(self):
        run=self.success();self.accept(run)
        note=copy.deepcopy(knowledge_fixture.NOTE)
        note['summary']='Original selected addition checks passed; preserve signs and verify current behavior.'
        self.model.reflection_candidate=note
        result=knowledge_fixture.KnowledgeTests.proposal(self,run)
        entry=self.session.registry.load('repository_knowledge')[result['knowledge_id']]
        self.assertEqual('pending',entry['review']);self.assertFalse(entry['payload']['runtime_authorized'])
        self.assertEqual(3,len(self.model.requests));self.assertEqual(3,self.session.registry.budget()['requests_used'])
        self.assertEqual(0,self.session.registry.budget()['requests_reserved_unknown'])
        self.assertFalse(self.session.knowledge.search(fixture.ISSUE)['hits'])
