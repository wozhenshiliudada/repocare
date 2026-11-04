"""Actual Pi correction uses new evidence, original budget and independent review."""
import copy
import json
import unittest
import uuid
from starlette.testclient import TestClient
import test_task_planning as planning
import test_knowledge_memory as knowledge_fixture
import test_experience_memory as experience_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.knowledge_memory import KIND
from repo_maintainer.local_ui import create_app,validate_payload
from repo_maintainer.models import SafetyError,digest


class CorrectionTests(unittest.TestCase):
    setUp=planning.TaskPlanningTests.setUp
    open=planning.TaskPlanningTests.open
    restart=planning.TaskPlanningTests.restart
    plan=planning.TaskPlanningTests.plan
    decision=planning.TaskPlanningTests.decision
    record=planning.TaskPlanningTests.record
    episode=experience_fixture.ExperienceTests.episode
    accept=experience_fixture.ExperienceTests.accept
    enable=experience_fixture.ExperienceTests.enable
    proposal=knowledge_fixture.KnowledgeTests.proposal
    review=knowledge_fixture.KnowledgeTests.review
    seed=knowledge_fixture.KnowledgeTests.seed
    envelope=experience_fixture.ExperienceTests.envelope

    def parent(self):
        run,_=self.seed();parent=self.proposal(run)['knowledge_id'];self.review(parent)
        return run,parent

    def preview(self,parent,runs):
        return self.session._operate('knowledge_refresh_preview',{'knowledge_id':parent,'experience_ids':runs})['knowledge_refresh_preview']

    def refresh(self,preview):
        return self.session._operate('knowledge_refresh_propose',
            {k:preview[k] for k in ('knowledge_id','experience_ids','revision')})

    def native(self):
        return {str(p):digest(p.read_bytes()) for p in (self.root/'state'/'task-sessions').glob('*/sessions/*.jsonl')}

    def test_new_sources_replace_revoked_support_only_after_new_review_restart_and_approval_green(self):
        old,parent=self.parent();old_entry=copy.deepcopy(self.session.registry.load(KIND)[parent])
        gateway=RepositoryGateway(self.source);gateway.command('config','user.name','Correction Fixture')
        gateway.command('config','user.email','correction@example.invalid')
        file=self.source/'calculator.py';file.write_bytes(file.read_bytes()+b'# new observed source revision\n')
        gateway.command('add','calculator.py');gateway.command('commit','-m','fixture source revision')
        new,_=self.episode();self.accept(new);self.accept(old,'rejected');native=self.native();before=len(self.model.requests)
        view=next(r for r in self.session.state()['knowledge_memory']['entries'] if r['id']==parent)
        self.assertEqual('invalid',view['freshness']);self.assertTrue(view['can_refresh'])
        preview=self.preview(parent,[new['run_id']]);self.assertEqual(before,len(self.model.requests))
        note=copy.deepcopy(knowledge_fixture.NOTE);note['summary']='Corrected using the newly observed source and its independently accepted failed run.'
        self.model.reflection_candidate=note;child=self.refresh(preview)['knowledge_id']
        self.assertEqual(before+1,len(self.model.requests));self.assertEqual(native,self.native())
        source=self.envelope()['source'];self.assertEqual(2,source['schema_version']);self.assertEqual(parent,source['previous']['id'])
        self.assertEqual([new['run_id']],[h['id'] for h in source['hits']]);self.assertEqual(old_entry['payload']['note'],source['previous']['note'])
        self.assertEqual(['propose_memory'],[t['function']['name'] for t in self.model.requests[-1]['tools']])
        rows=self.session.registry.load(KIND);self.assertEqual('pending',rows[child]['review'])
        self.assertEqual(old_entry,rows[parent]);self.assertEqual(parent,rows[child]['payload']['parent'])
        self.assertFalse(self.session.knowledge.search(planning.ISSUE)['hits'])
        self.review(child,'rejected');self.assertEqual('active',self.session.registry.load(KIND)[parent]['lifecycle'])
        self.review(child);self.assertEqual('superseded',self.session.registry.load(KIND)[parent]['lifecycle'])
        self.review(parent,'rejected')  # Old version review does not revoke independently reviewed new support.
        self.enable();budget=self.session.registry.budget();self.restart()
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(before+1,len(self.model.requests))
        self.assertEqual([child],[h['id'] for h in self.session.knowledge.search(planning.ISSUE)['hits']])
        self.model.scenario='correct';plan=self.plan();self.assertEqual(child,self.envelope()['repository_knowledge']['hits'][0]['id'])
        self.assertEqual('candidate_ready',self.decision(plan,'approve')['workflow_status'])
        self.assertEqual(len(self.model.requests),self.session.registry.budget()['requests_used'])
        self.assertEqual(0,self.session.registry.budget()['requests_reserved_unknown'])
        self.assertTrue(self.session.state()['runs'][-1]['source_unchanged'])

    def test_parent_or_new_source_review_change_invalidates_preview_before_reservation(self):
        run,parent=self.parent();preview=self.preview(parent,[run['run_id']]);budget=self.session.registry.budget();count=len(self.model.requests)
        self.review(parent,'rejected')
        with self.assertRaises(SafetyError):self.refresh(preview)
        preview=self.preview(parent,[run['run_id']]);self.accept(run,'rejected')
        with self.assertRaises(SafetyError):self.refresh(preview)
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(count,len(self.model.requests))

    def test_parent_change_during_reflection_retains_unknown_and_no_child_or_native_write(self):
        run,parent=self.parent();preview=self.preview(parent,[run['run_id']]);native=self.native();before=self.session.registry.budget()
        def drift(body,_):
            self.model.on_request=None
            row=self.session.registry.load(KIND)[parent];row['review']='rejected';row['review_version']+=1
            self.session.registry.put(KIND,parent,row)
        self.model.on_request=drift
        with self.assertRaises(SafetyError):self.refresh(preview)
        self.assertEqual([parent],list(self.session.registry.load(KIND)));self.assertEqual(native,self.native())
        budget=self.session.registry.budget();self.assertEqual(before['requests_used'],budget['requests_used'])
        self.assertEqual(before['requests_remaining'],budget['requests_reserved_unknown'])

    def test_missing_metered_parent_or_rehashed_parent_cannot_be_corrected_as_intact_history(self):
        run,parent=self.parent();entry=self.session.registry.load(KIND)[parent];count=len(self.model.requests);budget=self.session.registry.budget()
        attempt_id=entry['origin']['attempt_id'];attempt=self.session.registry.load('attempt')[attempt_id]
        with self.session.registry.transaction() as db:db.execute("DELETE FROM objects WHERE kind='attempt' AND id=?",(attempt_id,))
        try:
            with self.assertRaises(SafetyError):self.preview(parent,[run['run_id']])
        finally:self.session.registry.put('attempt',attempt_id,attempt)
        changed=copy.deepcopy(entry);changed['payload']['note']['summary']='Forged corrected history'
        from repo_maintainer.task_session import fingerprint
        changed['payload_sha256']=fingerprint(changed['payload']);changed['origin']['candidate']=changed['payload']['note']
        self.session.registry.put(KIND,parent,changed)
        with self.assertRaises(SafetyError):self.preview(parent,[run['run_id']])
        self.assertEqual(count,len(self.model.requests));self.assertEqual(budget,self.session.registry.budget())

    def test_http_refresh_uses_original_capability_job_idempotence_and_pending_review(self):
        run,parent=self.parent();count=len(self.model.requests)
        with TestClient(create_app(self.session,12345),base_url='http://127.0.0.1:12345') as client:
            headers={'Authorization':'Bearer '+self.session.token};body={'request_id':uuid.uuid4().hex,'knowledge_id':parent,'experience_ids':[run['run_id']]}
            self.assertEqual(401,client.post('/api/actions/knowledge_refresh_preview',json=body).status_code)
            self.assertEqual(202,client.post('/api/actions/knowledge_refresh_preview',json=body,headers=headers).status_code)
            self.session.executor.submit(lambda:None).result(timeout=60)
            preview=self.session.jobs[body['request_id']]['result']['knowledge_refresh_preview']
            self.assertEqual(count,len(self.model.requests));body={**body,'request_id':uuid.uuid4().hex,'revision':preview['revision']}
            self.assertEqual(202,client.post('/api/actions/knowledge_refresh_propose',json=body,headers=headers).status_code)
            self.session.executor.submit(lambda:None).result(timeout=90)
            child=self.session.jobs[body['request_id']]['result']['knowledge_id']
            self.assertEqual('pending',self.session.registry.load(KIND)[child]['review'])
            self.assertEqual(count+1,len(self.model.requests))
            self.assertEqual(202,client.post('/api/actions/knowledge_refresh_propose',json=body,headers=headers).status_code)
            self.assertEqual(count+1,len(self.model.requests))
        with self.assertRaises(SafetyError):validate_payload('knowledge_refresh_propose',{**body,'approved':True})
