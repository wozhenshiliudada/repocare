"""Clarification successors: original journal, immutable parent, exact request binding."""
import copy
import unittest
from unittest.mock import patch
from pathlib import Path
from repo_maintainer.models import SafetyError,digest
from repo_maintainer.validation_proposal import ValidationProposal
import test_validation_proposal as generic
import test_issue_workbench as issue_fixture
import test_monitor_validation as monitor_fixture

class ClarificationContracts(unittest.TestCase):
    def setUp(self):
        self.f=generic.ValidationProposalTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f
        self.parent={'id':f.service.identity,'source':f.source,'status':'clarification_needed',
            'proposal':{**f.proposal,'clarification':True,'cases':[]},'contract':None,'task_id':None,
            'preview_revision':'a'*64,'runtime_authorized':False}
        f.registry.put('validation_proposal',f.service.identity,self.parent)
    def test_new_request_same_scope_and_single_intent_duplicate_no_replay(self):
        f=self.f;before=f.registry.budget();parent=copy.deepcopy(self.parent)
        preview=f.service.clarification_preview(f.service.revision(parent),'add(2,3) must return 5')
        def submitted(child,revision):
            value={'id':child.identity,'source':child.source,'status':'clarification_needed','runtime_authorized':False}
            child.registry.put('validation_proposal',child.identity,value);return value
        with patch.object(ValidationProposal,'propose',autospec=True,side_effect=submitted) as submit:
            child=f.service.clarify(preview['preview'],preview['revision'])
            self.assertEqual(child,f.service.clarify(preview['preview'],preview['revision']))
            self.assertEqual(1,submit.call_count)
        self.assertEqual(parent,f.registry.load('validation_proposal')[f.service.identity])
        current=f.service.current();self.assertIn('用户补充业务期望',current.source['issue'])
        self.assertNotEqual(f.source['request_sha256'],current.source['request_sha256'])
        for k in ('authority','files','read_roots','base_sha'):self.assertEqual(f.source[k],current.source[k])
        self.assertEqual(before,f.registry.budget());self.assertEqual([],f.model.requests)
    def test_unknown_stale_source_and_parent_change_block(self):
        f=self.f
        with self.assertRaises(SafetyError):f.service.clarification_preview('a'*64,'明确期望')
        preview=f.service.clarification_preview(f.service.revision(self.parent),'add returns sum')
        attempt,_=f.registry.reserve(1)
        with self.assertRaises(SafetyError):f.service.clarify(preview['preview'],preview['revision'])
        self.assertEqual({},f.registry.load('validation_clarification'));self.assertEqual([],f.model.requests)
    def test_persisted_link_without_child_never_replays(self):
        f=self.f;preview=f.service.clarification_preview(f.service.revision(self.parent),'add returns sum')
        with patch.object(ValidationProposal,'propose',side_effect=RuntimeError('injected before native')):
            with self.assertRaises(RuntimeError):f.service.clarify(preview['preview'],preview['revision'])
        current=f.service.current()
        with self.assertRaises(SafetyError):current.propose(current.preview()['revision'])
        self.assertEqual('interrupted_no_replay',f.service.clarify(preview['preview'],preview['revision'])['status'])
        self.assertEqual([],f.model.requests)
        changed={**self.parent,'status':'accepted'};f.registry.put('validation_proposal',f.service.identity,changed)
        with self.assertRaises(SafetyError):f.service.current()

    def test_ninth_child_works_and_original_request_size_rejects_before_link(self):
        f=self.f
        def submitted(child,revision):
            row={'id':child.identity,'source':child.source,'status':'clarification_needed','runtime_authorized':False}
            child.registry.put('validation_proposal',child.identity,row);return row
        with patch.object(ValidationProposal,'propose',autospec=True,side_effect=submitted):
            for n in range(9):
                current=f.service.current();row=f.registry.load('validation_proposal')[current.identity]
                pre=current.clarification_preview(current.revision(row),'Expected behavior '+str(n))
                current.clarify(pre['preview'],pre['revision'])
        current=f.service.current();self.assertEqual(9,current.clarification_depth())
        saved=copy.deepcopy(f.registry.load('validation_clarification'));row=f.registry.load('validation_proposal')[current.identity]
        # The ninth child is valid; an oversized original request is refused before writes.
        from repo_maintainer.validation_proposal import source_packet
        large=source_packet(f.repo,'a'*65530,['.'],['calculator.py'],f.source['authority'])
        large_service=ValidationProposal(f.registry,f.repo,f.provider,large,lambda:None,'session')
        large_row={'id':large_service.identity,'source':large,'status':'clarification_needed','runtime_authorized':False}
        f.registry.put('validation_proposal',large_service.identity,large_row)
        with self.assertRaises(SafetyError):
            large_service.clarification_preview(large_service.revision(large_row),'over original byte bound')
        self.assertEqual(saved,f.registry.load('validation_clarification'))
        self.assertEqual(current.identity,f.service.current().identity)

class ClarificationEnvironmentFailures(unittest.TestCase):
    def test_missing_child_never_prepares_old_green_or_red_checks_and_malformed_preview_stops(self):
        from repo_maintainer.issue_validation import service
        f=issue_fixture.IssueWorkbenchTests();f.setUp();self.addCleanup(f.doCleanups)
        f.configure();job=f.execute('issue_investigate',{'number':7});origin={'investigation_id':job['id']}
        root=service(f.session.issue_workbench,job['id'],root=True)
        parent={'id':root.identity,'source':root.source,'status':'clarification_needed',
            'runtime_authorized':False,'contract':None,'preview_revision':'a'*64}
        root.registry.put('validation_proposal',root.identity,parent)
        self.assertEqual('validation_review_required',f.execute('issue_environment',origin)['status'])
        pre=f.execute('issue_validation_clarification_preview',{**origin,'revision':root.revision(parent),'answer':'Return sum'})
        with patch.object(ValidationProposal,'propose',side_effect=RuntimeError('before Native')):
            outcome=f.wait(f.post('issue_validation_clarify',{**origin,**pre,'confirmed':True}))
        self.assertEqual('failed',outcome['status'])
        visible=f.session.state()['issue_workbench']['validations']
        self.assertEqual('interrupted_no_replay',visible[0]['status'])
        self.assertEqual('validation_review_required',f.execute('issue_environment',origin)['status'])
        self.assertEqual({},root.registry.load('issue_workbench_environment'))
        child=root.current()
        root.registry.put('validation_proposal',child.identity,{'id':child.identity,'source':child.source,
            'status':'rejected','runtime_authorized':False})
        self.assertEqual('validation_review_required',f.execute('issue_environment',origin)['status'])
        bad=f.wait(f.post('issue_validation_clarify',{**origin,'preview':[], 'revision':'a'*64,'confirmed':True}))
        self.assertEqual('failed',bad['status']);self.assertEqual([],f.model.requests)


class ClarificationPiPaths(unittest.TestCase):
    def setUp(self):
        # A fresh checkout has no ignored evidence directories. Keep the
        # original post-run parent/Native assertions reachable there too.
        Path('.demo/r1-clarification').mkdir(parents=True, exist_ok=True)

    def test_issue_answer_bound_environment_and_new_approval_candidate(self):
        f=issue_fixture.IssueWorkbenchTests();f.setUp();self.addCleanup(f.doCleanups)
        f.repo.command('config','user.name','Fixture');f.repo.command('config','user.email','fixture@example.invalid')
        f.repo.command('rm','tests/test_calculator.py');f.repo.command('commit','-m','No old tests')
        f.github.head=f.repo.head;f.github.issue.update(title='Fix calculator',body='Please correct add behavior')
        f.configure();job=f.execute('issue_investigate',{'number':7});origin={'investigation_id':job['id']}
        proposal={'requirement':'Please correct add behavior','rationale':'User goal','limitations':'What should add return?',
            'clarification':True,'cases':[]}
        f.model.schema_calls=[('propose_validation',proposal)]
        pre=f.execute('issue_validation_preview',origin)
        parent=f.execute('issue_validation_propose',{**origin,'revision':pre['revision']})
        old=copy.deepcopy(f.session.registry.load('validation_proposal')[parent['id']])
        task=copy.deepcopy(f.session.registry.load('pi_task')[parent['task_id']])
        raw={str(p):p.read_bytes() for p in (f.session.registry.root.parent/'task-sessions'/parent['task_id']).rglob('*.jsonl')}
        self.assertTrue(raw,'Original Issue Native bytes must exist')
        answer='add(2,3) must return 5'
        pre=f.execute('issue_validation_clarification_preview',{**origin,'revision':parent['revision'],'answer':answer})
        f.model.schema_calls=[('propose_validation',{**proposal,'requirement':answer,'clarification':False,
            'cases':[{'id':'sum','module':'calculator.py','callable':'add','args':[2,3],'kwargs':{},
            'expect':{'kind':'json','value':5},'missing_behavior':False}]})]
        child=f.execute('issue_validation_clarify',{**origin,**pre,'confirmed':True})
        self.assertEqual('awaiting_validation_review',child['status'])
        self.assertEqual(child['id'],f.execute('issue_validation_clarify',{**origin,**pre,'confirmed':True})['id'])
        accepted=f.execute('issue_validation_review',{**origin,'revision':child['revision'],'decision':'accept'})
        env=f.execute('issue_environment',origin)
        self.assertIn(answer,env['maintenance_request'])
        self.assertEqual(digest(env['maintenance_request'].encode()),env['environment']['behavior_contract']['request_sha256'])
        # An accepted leaf changed after preparation must be rejected before planning/candidate.
        service=f.session.issue_workbench._service(env)
        current=f.session.registry.load('validation_proposal')[child['id']]
        f.session.registry.put('validation_proposal',child['id'],{**current,'extra':'changed'})
        with self.assertRaises(SafetyError):service.source_guard()
        self.assertEqual(2,len(f.model.requests));self.assertEqual({},f.session.registry.load('issue_execution'))
        f.session.registry.put('validation_proposal',child['id'],current)
        original_head=f.github.head;f.github.head='e'*40
        with self.assertRaises(SafetyError):service.source_guard()
        f.github.head=original_head
        self.assertEqual(2,len(f.model.requests))
        f.model.schema_calls=[]
        execution=f.post('issue_execute',{**origin,'revision':env['revision']});approval=f.pending()
        f.post('issue_decide',{'approval_id':approval['id'],'revision':approval['revision'],'decision':'approve'})
        outcome=f.wait(execution);self.assertEqual('finished',outcome['status'],outcome)
        self.assertEqual('awaiting-review',outcome['result']['status'],outcome)
        row=outcome['result'];run=f.execute('issue_result',{'execution_id':row['id']})
        self.assertEqual('candidate_ready',run['status'])
        from repo_maintainer.integration import prepare_integration
        from repo_maintainer.task_session import fingerprint
        import json
        execution_service=f.session.issue_workbench._execution(row['id'])[2]
        sealed=prepare_integration(execution_service.runs/row['run_id'])
        self.assertEqual('prepared',sealed['status']);self.assertTrue(all(c['category']=='passed' for c in sealed['review']['tests']))
        self.assertNotEqual(parent['task_id'],child['task_id'])
        Path('.demo/r1-clarification/issue-final-evidence.json').write_text(json.dumps({
            'candidate_status':run['status'],'seal_status':sealed['status'],'seal':sealed,
            'parent_proposal_sha256':fingerprint(old),'parent_task_sha256':fingerprint(task),
            'parent_native_files':{path:digest(data) for path,data in raw.items()},
            'parent_task_id':parent['task_id'],'child_task_id':child['task_id'],
            'budget':f.session.registry.budget()},ensure_ascii=False,indent=2),encoding='utf-8')
        self.assertEqual(old,f.session.registry.load('validation_proposal')[parent['id']])
        self.assertEqual(task,f.session.registry.load('pi_task')[parent['task_id']])
        for path,data in raw.items():self.assertEqual(data,Path(path).read_bytes())
        self.assertEqual(0,f.session.registry.budget()['requests_reserved_unknown'])
    def test_monitor_answer_bound_baseline_repair_and_original_parent(self):
        f=monitor_fixture.MonitorValidationTests();f.setUp();self.addCleanup(f.doCleanups)
        mid,event=f.register('auto',no_tests=True);origin=f.location(mid,event)
        proposal={**f.proposal(),'clarification':True,'cases':[],'limitations':'What is expected?'}
        f.model.schema_calls=[('propose_validation',proposal)]
        pre=f.action('preview',{**origin,'request_text':'Correct add behavior'})
        parent=f.action('propose',{**pre,'confirmed':True})
        registry=f.hub.monitor.registries[mid];old=copy.deepcopy(registry.load('validation_proposal')[parent['id']])
        task=copy.deepcopy(registry.load('pi_task')[parent['task_id']])
        raw={str(p):p.read_bytes() for p in (registry.root.parent/'task-sessions'/parent['task_id']).rglob('*.jsonl')}
        self.assertTrue(raw,'Original monitor Native bytes must exist')
        answer='add(2,3) must return 5'
        pre=f.action('clarification_preview',{**origin,'revision':parent['revision'],'answer':answer})
        f.model.schema_calls=[('propose_validation',f.proposal())]
        child=f.action('clarify',{**origin,**pre,'confirmed':True})
        f.action('review',{**origin,'revision':child['revision'],'decision':'accept'})
        accepted=f.event(event['id'])['validation']
        f.action('prepare',{**origin,'revision':accepted['revision']})
        current=f.event(event['id']);request=current['root_preparation']['maintenance_request']
        self.assertIn(answer,request)
        self.assertEqual(digest(request.encode()),accepted['contract']['request_sha256'])
        f.model.schema_calls=[];preview=f.repair_preview(mid,current)
        self.assertEqual(request,preview['preview']['binding']['canonical_request'])
        result=f.execute(preview);self.assertEqual('candidate_ready',result['status'],result)
        f.request=request
        session=f.session(mid,current)
        record=f.assert_candidate(mid,current,next(reversed(session.runs)),expected_tasks=3,expected_requests=4)
        import json
        Path('.demo/r1-clarification/monitor-final-record.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        for path,data in raw.items():self.assertEqual(data,Path(path).read_bytes())
        Path('.demo/r1-clarification/monitor-native-pins.json').write_text(json.dumps({path:digest(data) for path,data in raw.items()},indent=2),encoding='utf-8')
        from repo_maintainer.integration import prepare_integration
        from repo_maintainer.task_session import fingerprint
        sealed=prepare_integration(session.runs_dir/record['run_id'])
        self.assertEqual('prepared',sealed['status']);self.assertTrue(all(c['category']=='passed' for c in sealed['review']['tests']))
        self.assertNotEqual(parent['task_id'],child['task_id'])
        Path('.demo/r1-clarification/monitor-final-evidence.json').write_text(json.dumps({
            'candidate_status':record['status'],'seal_status':sealed['status'],'seal':sealed,
            'parent_proposal_sha256':fingerprint(old),'parent_task_sha256':fingerprint(task),
            'parent_native_files':{path:digest(data) for path,data in raw.items()},
            'parent_task_id':parent['task_id'],'child_task_id':child['task_id'],'budget':registry.budget()},
            ensure_ascii=False,indent=2),encoding='utf-8')
        self.assertEqual(old,registry.load('validation_proposal')[parent['id']])
        self.assertEqual(task,registry.load('pi_task')[parent['task_id']])
        self.assertEqual(0,registry.budget()['requests_reserved_unknown'])

    def test_issue_child_stop_uses_original_job_control_and_never_replays_unknown(self):
        import threading
        f=issue_fixture.IssueWorkbenchTests();f.setUp();self.addCleanup(f.doCleanups)
        f.configure();job=f.execute('issue_investigate',{'number':7});origin={'investigation_id':job['id']}
        proposal={'requirement':'Fix add','rationale':'User goal','limitations':'Which result?',
            'clarification':True,'cases':[]}
        f.model.schema_calls=[('propose_validation',proposal)]
        pre=f.execute('issue_validation_preview',origin)
        parent=f.execute('issue_validation_propose',{**origin,'revision':pre['revision']})
        pre=f.execute('issue_validation_clarification_preview',{**origin,'revision':parent['revision'],'answer':'Return sum'})
        entered,released=threading.Event(),threading.Event();f.model.gate=(entered,released)
        job=f.post('issue_validation_clarify',{**origin,**pre,'confirmed':True})
        try:
            self.assertTrue(entered.wait(60));self.assertIsNotNone(f.session.control)
            target=f.session.state()['task_control']
            f.post('stop_job',{'job_id':target['job_id'],'revision':target['revision']})
            result=f.wait(job)
        finally:released.set()
        self.assertIn(result['status'],{'stopped','failed'});self.assertIsNone(f.session.control)
        self.assertIsNone(f.session.provider.inner._cancel_event)
        self.assertGreater(f.session.registry.budget()['requests_reserved_unknown'],0)
        count=len(f.model.requests)
        old=f.execute('issue_validation_clarify',{**origin,**pre,'confirmed':True})
        self.assertEqual('interrupted_no_replay',old['status']);self.assertEqual(count,len(f.model.requests))

    def test_watch_uses_same_admission_after_clarification_and_pause_blocks_new_child(self):
        import test_issue_watch_validation as watch_fixture
        case=watch_fixture.IssueWatchValidationTests();case.setUp();self.addCleanup(case.doCleanups)
        f=case.f;f.model.schema_calls=[('propose_validation',{'requirement':'Fix add',
            'rationale':'User goal','limitations':'Which result?','clarification':True,'cases':[]})]
        admission=f.execute('issue_watch_check',{});origin={'investigation_id':admission['investigation_id']}
        parent=f.session.state()['issue_workbench']['validations'][0]
        pre=f.execute('issue_validation_clarification_preview',{**origin,'revision':parent['revision'],'answer':'add(2,3) must return 5'})
        f.model.schema_calls=[('propose_validation',{'requirement':'add(2,3) must return 5',
            'rationale':'User answer','limitations':'one sum','clarification':False,
            'cases':[{'id':'sum','module':'calculator.py','callable':'add','args':[2,3],'kwargs':{},
            'expect':{'kind':'json','value':5},'missing_behavior':False}]})]
        child=f.execute('issue_validation_clarify',{**origin,**pre,'confirmed':True})
        f.execute('issue_validation_review',{**origin,'revision':child['revision'],'decision':'accept'})
        f.model.schema_calls=[];continued=f.execute('issue_watch_check',{})
        self.assertEqual('finished',continued['status'],continued)
        self.assertEqual(admission['id'],continued['id']);self.assertEqual(admission['grant_id'],continued['grant_id'])
        self.assertEqual(1,len(f.session.registry.load('issue_watch_admission')))
        self.assertEqual(3,len(f.session.registry.load('pi_task')))
        self.assertEqual(0,f.session.registry.budget()['requests_reserved_unknown'])
        f.post('issue_watch_pause',{'grant_id':case.grant['id']})
        count=len(f.model.requests)
        result=f.wait(f.post('issue_validation_clarify',{**origin,**pre,'confirmed':True}))
        self.assertEqual('failed',result['status']);self.assertEqual(count,len(f.model.requests))
