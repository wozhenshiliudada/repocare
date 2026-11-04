import copy
import json
import sys
import unittest
import uuid
import test_task_planning as fixture
from repo_maintainer.maintenance_memory import monitor_projection
from repo_maintainer.local_ui import validate_payload
from repo_maintainer.models import SafetyError,digest
from repo_maintainer.task_session import fingerprint


class MaintenanceMemoryTests(unittest.TestCase):
    setUp=fixture.TaskPlanningTests.setUp
    open=fixture.TaskPlanningTests.open
    restart=fixture.TaskPlanningTests.restart
    plan=fixture.TaskPlanningTests.plan
    decision=fixture.TaskPlanningTests.decision
    record=fixture.TaskPlanningTests.record
    session_file=fixture.TaskPlanningTests.session_file

    def note(self,identity=None,status='open',priority=2,title='Preserve signed addition',detail='Check negative and mixed operands before any new repair.'):
        return self.session._operate('maintenance_note',{'note_id':identity,'revision':self.session.maintenance.revision(),
                         'title':title,'detail':detail,'status':status,'priority':priority})

    def enable(self):
        return self.session._operate('maintenance_settings',{'enabled':True,'revision':self.session.maintenance.revision()})

    def test_note_versions_cas_closed_is_not_a_verified_fix_and_get_is_readonly(self):
        first=self.note();original=copy.deepcopy(self.session.registry.load('maintenance_note_version'))
        revision=self.session.maintenance.revision();self.note(first['note_id'],status='closed_by_user')
        with self.assertRaises(SafetyError):self.session._operate('maintenance_note',{'note_id':first['note_id'],'revision':revision,
            'title':'Old draft','detail':'Must not overwrite a newer declaration.','status':'open','priority':1})
        versions=self.session.registry.load('maintenance_note_version');self.assertEqual(2,len(versions))
        self.assertTrue(all(versions[i]==v for i,v in original.items()))
        value=self.session.state()['maintenance_memory'];self.assertEqual([],value['completed_actions']);self.assertEqual([],value['fixes'])
        self.assertEqual([],value['issues']);self.assertEqual('proposed',value['observations'][0]['classification'])
        before=self.session.registry.path.read_bytes();self.session.state();self.session.state()
        self.assertEqual(before,self.session.registry.path.read_bytes());self.assertEqual(0,len(self.model.requests))

    def test_new_task_loads_fixed_note_restart_and_new_approval_green(self):
        self.note();self.enable();plan=self.plan();record=copy.deepcopy(self.record());count=len(self.model.requests)
        request=self.model.requests[0];user=next(m for m in reversed(request['messages']) if m['role']=='user')
        text=user['content'] if isinstance(user['content'],str) else user['content'][0]['text']
        context=json.loads(text)['repository_maintenance'];self.assertEqual(record['task_id'],context['task_id'])
        self.assertEqual(1,len(context['hits']));self.assertEqual('proposed',context['hits'][0]['classification'])
        evidence=self.session.registry.load('attempt')[next(iter(self.session.registry.load('attempt')))]['evidence']
        self.assertEqual(context['context_sha256'],evidence['maintenance_context_sha256'])
        self.assertEqual(context['selection_sha256'],evidence['maintenance_selection_sha256'])
        raw=self.session_file().read_bytes();self.restart();self.assertEqual(count,len(self.model.requests));self.assertEqual(raw,self.session_file(record).read_bytes())
        item=self.session.state()['plans'][0]
        self.session._operate('claim_plan',{'plan_id':item['id'],'revision':item['revision']})
        result=self.decision(plan,'approve');self.assertEqual('candidate_ready',result['workflow_status'])
        memory=self.session.state()['maintenance_memory'];self.assertEqual(1,len(memory['checks']))
        self.assertEqual('candidate_test_passed',memory['fixes'][0]['body']['stage']);self.assertEqual([0],memory['checks'][0]['body']['test_exits'])
        self.assertEqual(count,len(self.model.requests));self.assertTrue(self.session.state()['runs'][0]['source_unchanged'])

    def test_existing_task_never_acquires_new_maintenance_selection(self):
        plan=self.plan();identity=self.record()['task_id'];self.note();self.enable()
        self.assertNotIn(identity,self.session.registry.load('maintenance_task'))
        self.assertEqual('candidate_ready',self.decision(plan,'approve')['workflow_status'])

    def test_changed_note_blocks_saved_plan_without_refund_model_or_tests(self):
        note=self.note();self.enable();plan=self.plan();budget=self.session.registry.budget();count=len(self.model.requests)
        self.note(note['note_id'],detail='Changed after the plan was generated.')
        with self.assertRaises(SafetyError):self.decision(plan,'approve')
        self.assertEqual([],self.session.state()['runs']);self.assertEqual(budget,self.session.registry.budget());self.assertEqual(count,len(self.model.requests))

    def test_lost_or_rehashed_selection_cannot_replace_original_metered_choice(self):
        self.note();self.enable();plan=self.plan();identity=self.record()['task_id'];pin=self.session.registry.load('maintenance_task')[identity]
        budget=self.session.registry.budget();count=len(self.model.requests)
        changed=copy.deepcopy(pin);changed['payload']['hits']=[];changed['payload_sha256']=fingerprint(changed['payload'])
        self.session.registry.put('maintenance_task',identity,changed)
        with self.assertRaises(SafetyError):self.decision(plan,'approve')
        with self.session.registry.transaction() as db:db.execute('DELETE FROM objects WHERE kind=? AND id=?',('maintenance_task',identity))
        with self.assertRaises(SafetyError):self.decision(plan,'approve')
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(count,len(self.model.requests))

    def test_note_drift_during_inference_preserves_unknown_and_no_completed_checkpoint(self):
        note=self.note();self.enable()
        def drift(body,count):
            if count==1:self.note(note['note_id'],detail='External concurrent note change while model is running.')
        self.model.on_request=drift
        with self.assertRaises(SafetyError):self.plan()
        self.assertEqual(0,self.record()['revision']);self.assertEqual(0,self.session.registry.budget()['requests_used'])
        self.assertEqual(50,self.session.registry.budget()['requests_reserved_unknown']);self.assertEqual({},self.session.plans)

    def test_pending_plan_reference_is_historical_and_corruption_blocks_its_dependant(self):
        first=self.plan();self.enable();second=self.plan();pin=self.session.registry.load('maintenance_task')[self.record()['task_id']]
        hit=next(r for r in pin['payload']['hits'] if r['source_ref']['kind']=='plan')
        cursor=next(r for r in pin['payload']['hits'] if r['source_ref']['kind']=='checkpoint')
        self.assertEqual('registration',cursor['body']['proof']);self.assertEqual(1,cursor['body']['rounds'])
        self.assertEqual(first['plan_id'],hit['source_ref']['id']);self.assertEqual('historical',hit['classification'])
        file=self.session.plans[first['plan_id']]['path'];raw=file.read_bytes();file.write_bytes(b'{broken')
        try:
            with self.assertRaises(SafetyError):self.decision(second,'approve')
        finally:file.write_bytes(raw)

    def test_missing_note_history_or_recognized_secret_is_rejected(self):
        note=self.note();self.note(note['note_id']);versions=self.session.registry.load('maintenance_note_version')
        old=next(i for i,v in versions.items() if v['version']==1)
        with self.session.registry.transaction() as db:db.execute('DELETE FROM objects WHERE kind=? AND id=?',('maintenance_note_version',old))
        with self.assertRaises(SafetyError):self.session.maintenance.resolve('note',note['note_id'])
        with self.assertRaises(SafetyError):self.note(title='Bearer syntheticPrivateToken123456789')

    def test_changed_retained_note_version_cannot_hide_behind_an_unchanged_head(self):
        note=self.note();self.note(note['note_id']);versions=self.session.registry.load('maintenance_note_version')
        old=next(i for i,v in versions.items() if v['version']==1)
        self.session.registry.put('maintenance_note_version',old,{**versions[old],'detail':'Tampered historical declaration'})
        with self.assertRaises(SafetyError):self.session.maintenance.resolve('note',note['note_id'])

    def test_native_compaction_keeps_selected_notes_same_task_and_same_original_budget(self):
        from test_task_compaction import CONFIG
        self.note();self.enable()
        self.session.compaction.configure(CONFIG,self.session.compaction.settings_revision())
        self.model.summary_text='Preserve signed operands, selected priority and original failed evidence. Verify current tests.'
        self.model.scenario='repeat-failure';first=self.plan();run=self.decision(first,'approve')
        def resume():
            preview=self.session._resume_preview(run['run_id'])
            return self.session._operate('resume_run',{'run_id':run['run_id'],'revision':preview['revision'],'mode':'approval'})
        self.model.scenario='correct';resume();identity=self.record()['task_id'];pin=self.session.registry.load('maintenance_task')[identity]
        preview=self.session.compaction.detail(identity,fixture.ISSUE)
        self.session._operate('compact_task',{'task_id':identity,'revision':preview['revision']})
        self.assertEqual(2,self.record()['revision']);third=resume()
        self.assertEqual(pin,self.session.registry.load('maintenance_task')[identity])
        self.assertEqual('candidate_ready',self.decision(third,'approve')['workflow_status'])
        self.assertEqual(len(self.model.requests),self.session.registry.budget()['requests_used'])
        self.assertEqual(0,self.session.registry.budget()['requests_reserved_unknown'])

    def test_request_schema_never_allows_reported_fix_commands_or_budget_in_notes(self):
        body={'request_id':uuid.uuid4().hex,'note_id':None,'revision':'a'*64,'title':'Check signed addition',
              'detail':'Follow up on the original failure.','status':'open','priority':2}
        self.assertEqual(2,validate_payload('maintenance_note',body)['priority'])
        for patch in [{'status':'fixed'},{'status':[]},{'priority':True},{'commands':['git']},{'runtime_authorized':True}]:
            with self.subTest(patch=patch):
                with self.assertRaises(SafetyError):validate_payload('maintenance_note',{**body,**patch})

    def test_http_note_action_requires_original_capability_and_is_idempotent_without_model(self):
        from starlette.testclient import TestClient
        from repo_maintainer.local_ui import create_app
        client=TestClient(create_app(self.session,12345),base_url='http://127.0.0.1:12345');self.addCleanup(client.close)
        body={'request_id':uuid.uuid4().hex,'note_id':None,'revision':self.session.maintenance.revision(),
              'title':'Check signed addition','detail':'Keep negative operands in the regression scope.','status':'open','priority':2}
        self.assertEqual(401,client.post('/api/actions/maintenance_note',json=body).status_code)
        headers={'Authorization':'Bearer '+self.session.token}
        self.assertEqual(202,client.post('/api/actions/maintenance_note',json=body,headers=headers).status_code)
        self.session.executor.submit(lambda:None).result(timeout=30)
        self.assertEqual('finished',self.session.jobs[body['request_id']]['status'])
        self.assertEqual(202,client.post('/api/actions/maintenance_note',json=body,headers=headers).status_code)
        self.session.executor.submit(lambda:None).result(timeout=30)
        self.assertEqual(1,len(self.session.registry.load('maintenance_note_version')))
        self.assertEqual(0,len(self.model.requests));self.assertEqual(0,self.session.registry.budget()['requests_used'])
        invalid=client.post('/api/actions/maintenance_note',json={**body,'request_id':uuid.uuid4().hex,'commands':['git']},headers=headers)
        self.assertEqual(409,invalid.status_code);self.assertIn('Unexpected operation fields',invalid.json()['error'])

    def test_unbound_historical_run_is_explained_and_never_sent_as_a_bound_native_task(self):
        self.model.scenario='repeat-failure';first=self.plan();run=self.decision(first,'approve')
        with self.session.registry.transaction() as db:db.execute('DELETE FROM objects WHERE kind=? AND id=?',('pi_task_link','run:'+run['run_id']))
        self.enable();preview=self.session.maintenance.search(fixture.ISSUE)
        self.assertTrue(any(r['id']==run['run_id'] and 'binding' in r['reason'] for r in preview['excluded']))
        self.assertFalse(any(r['source_ref']['kind']=='run' and r['source_ref']['id']==run['run_id'] for r in preview['hits']))
        self.model.scenario='correct';self.plan()
        self.assertEqual(0,self.session.registry.budget()['requests_reserved_unknown'])

    def test_note_version_limit_stops_before_writing_an_unreadable_new_head(self):
        from unittest.mock import patch
        with patch('repo_maintainer.maintenance_memory.MAX_NOTE_VERSIONS',2):
            note=self.note();self.note(note['note_id']);head=copy.deepcopy(self.session.registry.load('maintenance_note'))
            with self.assertRaises(SafetyError):self.note(note['note_id'])
            self.assertEqual(head,self.session.registry.load('maintenance_note'))
            self.assertEqual(2,len(self.session.registry.load('maintenance_note_version')))
            self.assertEqual('open',self.session.maintenance.resolve('note',note['note_id'])['body']['stage'])

    def test_boolean_version_rehashed_as_original_is_still_invalid(self):
        note=self.note();head=self.session.registry.load('maintenance_note')[note['note_id']]
        version=self.session.registry.load('maintenance_note_version')[head['version_id']];version['version']=True
        self.session.registry.put('maintenance_note_version',head['version_id'],version)
        self.session.registry.put('maintenance_note',note['note_id'],{**head,'payload_sha256':fingerprint(version)})
        with self.assertRaises(SafetyError):self.session.maintenance.resolve('note',note['note_id'])


class MonitorMemoryTests(unittest.TestCase):
    def fixture(self):
        sha='a'*40;record={'spec':{'allowed_tests':[['{python}','test.py']]},'latest_sha':sha,'last_checked':'2026-10-16'}
        event={'id':digest(sha.encode())[:16],'sha':sha,'status':'checking','tests':[]}
        return record,event

    def test_observed_commit_is_never_a_completed_check_cursor(self):
        record,event=self.fixture();value=monitor_projection(record,[event])
        self.assertEqual(record['latest_sha'],value['observations']['head_commit']);self.assertEqual([],value['cursors']['completed_local_checks'])
        self.assertEqual([],value['completed_actions'])

    def test_real_failed_check_is_completed_check_not_fixed_and_blocked_or_timeout_never_advances(self):
        record,event=self.fixture();event.update(status='awaiting_approval',tests=[{'command':[sys.executable,'test.py'],'returncode':1,'timed_out':False}])
        value=monitor_projection(record,[event]);self.assertEqual(1,len(value['checks']));self.assertEqual([1],value['checks'][0]['test_exits'])
        self.assertEqual('local_check',value['completed_actions'][0]['kind']);self.assertEqual('awaiting_approval',value['fixes'][0]['recorded_status'])
        for change in [{'status':'blocked'},{'status':'interrupted'},{'tests':[{'command':['other'],'returncode':0,'timed_out':False}]},
                       {'tests':[{'command':[sys.executable,'test.py'],'returncode':0,'timed_out':True}]}]:
            self.assertEqual([],monitor_projection(record,[{**event,**change}])['cursors']['completed_local_checks'])

    def test_changed_monitor_event_identity_is_rejected(self):
        record,event=self.fixture();event['id']='b'*16
        with self.assertRaises(SafetyError):monitor_projection(record,[event])
