"""Real production UISession -> durable provider -> Pi subprocess, loopback SSE only."""
import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import time
import uuid
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.task_session import KIND

ISSUE='Fix addition and preserve negative/mixed operands.'


class SyntheticModel:
    def __init__(self):
        self.requests=[]
        self.scenario='correct'
        self.gate=None
        self.on_request=None
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(body)
                if owner.on_request:
                    owner.on_request(body,len(owner.requests))
                if owner.gate:
                    owner.gate[0].set();owner.gate[1].wait(15)
                if not body.get('tools') and getattr(owner,'summary_text',None) is not None:
                    mode=getattr(owner,'summary_mode','stop')
                    delta={'role':'assistant','content':owner.summary_text}
                    if mode=='tool':delta={'role':'assistant','tool_calls':[{'index':0,'id':'forbidden-summary-tool',
                        'type':'function','function':{'name':'read_source','arguments':'{"path":".env.secret"}'}}]}
                    frame={'id':'synthetic-summary','object':'chat.completion.chunk','created':1,'model':body['model'],
                        'choices':[{'index':0,'delta':delta,'finish_reason':'tool_calls' if mode=='tool' else mode}],
                        'usage':{'prompt_tokens':100,'completion_tokens':11,'total_tokens':111}}
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                    self.wfile.write(('data: '+json.dumps(frame)+'\n\ndata: [DONE]\n\n').encode());return
                users=[i for i,m in enumerate(body['messages']) if m['role']=='user']
                current=body['messages'][users[-1]+1:]
                read=next((m for m in current if m['role']=='tool' and 'before_sha256' in m.get('content','')),None)
                if ([t['function']['name'] for t in body['tools']]==['propose_memory']
                        and (getattr(owner,'reflection_provider_error',False) or
                             (getattr(owner,'reflection_prose_once',False) and not getattr(owner,'reflection_prose_sent',False)))):
                    owner.reflection_prose_sent=True
                    frame={'id':'synthetic-reflection-prose','object':'chat.completion.chunk','created':1,'model':body['model'],
                        'choices':[{'index':0,'delta':{'role':'assistant','content':'PRIVATE_PROVIDER_TEXT_OR_UNSUBMITTED_PROSE'},
                                    'finish_reason':'network_error' if getattr(owner,'reflection_provider_error',False) else 'stop'}],
                        'usage':{'prompt_tokens':100,'completion_tokens':11,'total_tokens':111}}
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                    self.wfile.write(('data: '+json.dumps(frame)+'\n\ndata: [DONE]\n\n').encode());return
                schema_calls=getattr(owner,'schema_calls',[])
                tool_count=sum(m['role']=='tool' for m in current)
                if tool_count<len(schema_calls):
                    name,args=schema_calls[tool_count]
                elif getattr(owner,'reflection_candidate',None) is not None and [t['function']['name'] for t in body['tools']]==['propose_memory']:
                    name,args='propose_memory',owner.reflection_candidate
                elif owner.scenario in {'denied-read','injection'} and not any(m['role']=='tool' for m in current):
                    name,args='read_source',{'path':'.env.secret' if owner.scenario=='injection' else 'not-authorized.py'}
                elif read is None:
                    name,args='read_source',{'path':'calculator.py'}
                elif owner.scenario=='injection' and not any(m['role']=='tool' and
                        'Edit path is read-only' in m.get('content','') for m in current):
                    name,args='propose_repair',{'diagnosis':'Untrusted log requested a test edit','risk':'synthetic attack',
                        'edits':[{'path':'tests/test_calculator.py','replacements':[{'oldText':'assert','newText':'pass'}]}]}
                else:
                    source=json.loads(read['content'])['content']
                    if owner.scenario=='feedback-revise' and len(users)==2:
                        new='return a * b'
                    else:
                        new='return abs(a) + abs(b)' if owner.scenario=='repeat-failure' or (owner.scenario in {'revise','feedback-revise'} and len(users)==1) else 'return a + b'
                    old=next(line.strip() for line in source.splitlines() if line.strip().startswith('return '))
                    name,args='propose_repair',{'diagnosis':'Synthetic production wiring acceptance','risk':'fixture only',
                        'edits':[{'path':'calculator.py','replacements':[{'oldText':old,'newText':new}]}]}
                delta={'role':'assistant','tool_calls':[{'index':0,'id':'call_'+str(len(owner.requests)),
                    'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
                frame={'id':'synthetic','object':'chat.completion.chunk','created':1,'model':body['model'],
                    'choices':[{'index':0,'delta':delta,'finish_reason':'tool_calls'}],
                    'usage':{'prompt_tokens':100,'completion_tokens':11,'total_tokens':111}}
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                self.wfile.write(('data: '+json.dumps(frame)+'\n\ndata: [DONE]\n\n').encode())
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}/v1'
    def close(self):
        if self.gate:self.gate[1].set()
        self.server.shutdown();self.server.server_close();self.thread.join()


class TaskPlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='production-pi-task-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=create_fixture(self.root/'source')
        self.model=SyntheticModel();self.addCleanup(self.model.close)
        self.session=None
        self.open()
        self.addCleanup(lambda:self.session.close())
    def open(self,auto=False,attempts=1):
        self.inner=PiProvider(('calculator.py',),self.model.url,readonly_paths=('tests/test_calculator.py',),max_requests=50)
        self.session=UISession(self.source,Policy(('calculator.py',),(DEMO_TEST,)),self.inner,self.root/'state',
                               allow_auto=auto,max_attempts=attempts)
    def restart(self,**kwargs):
        self.session.close();self.open(**kwargs)
    def plan(self):return self.session._operate('plan',{'issue':ISSUE})
    def decision(self,plan,choice='reject'):
        return self.session._operate('decision',{**plan,'decision':choice})
    def record(self):
        tasks=self.session.registry.load(KIND)
        return tasks[self.session.task_planner.identity]

    def test_rejected_read_then_valid_plan_checkpoint_and_approval(self):
        self.model.scenario='denied-read'
        plan=self.plan()
        self.assertEqual(self.record()['revision'],1)
        self.assertEqual(self.session.registry.budget()['requests_used'],3)
        self.assertEqual(self.session.registry.budget()['requests_reserved_unknown'],0)
        before=self.session.registry.budget()
        self.restart()
        self.assertEqual(before,self.session.registry.budget())
        # Restart requires a new claim, never replays the original model call.
        item=self.session.state()['plans'][0]
        self.session._operate('claim_plan',{'plan_id':item['id'],'revision':item['revision']})
        result=self.decision(plan,'approve')
        run=self.session.state()['runs'][0]
        self.assertEqual(run['status'],'candidate_ready',result)
        self.assertTrue(run['source_unchanged'])
        self.assertEqual(len(self.model.requests),3)
    def session_file(self,record=None):
        record=record or self.record()
        return self.session.task_planner.store.directory(record['task_id'])/'sessions'/record['checkpoint']['file']
    def resume(self,run):
        preview=self.session._resume_preview(run['run_id'])
        return self.session._operate('resume_run',{'run_id':run['run_id'],'revision':preview['revision'],'mode':'approval'})

    def test_manual_approval_restart_resume_keeps_task_and_native_history(self):
        first=self.plan();a=self.record();file=self.session_file(a);original=file.read_bytes()
        self.assertNotIn(self.session.token.encode(),original)
        self.assertNotIn(b'synthetic-not-a-real-key',original)
        self.assertEqual(1,a['revision']);self.assertEqual(2,self.session.registry.budget()['requests_used'])
        rejected=self.decision(first)
        self.assertEqual(2,len(self.model.requests),'SavedPlan execution must make zero model requests')
        self.assertEqual(a['task_id'],self.session.registry.load('pi_task_link')['run:'+rejected['run_id']]['task_id'])
        self.restart();self.assertEqual(2,len(self.model.requests));self.assertEqual(original,file.read_bytes())
        second=self.resume(rejected);b=self.record()
        self.assertEqual(a['task_id'],b['task_id']);self.assertEqual(a['checkpoint']['session_id'],b['checkpoint']['session_id'])
        self.assertEqual(2,b['revision']);self.assertEqual(file,self.session_file(b))
        self.assertEqual(4,self.session.registry.budget()['requests_used']);self.assertEqual(46,self.session.registry.budget()['requests_remaining'])
        self.assertEqual(2,len([m for m in self.model.requests[2]['messages'] if m['role']=='user']))
        evidence=self.session.plans[second['plan_id']]['view']['planning_evidence']
        self.assertEqual(2,len(evidence['usage']),'Historical assistant usage must not be counted again')
        self.assertNotEqual(first['plan_id'],second['plan_id'])
        accepted=self.decision(second,'approve')
        self.assertEqual('candidate_ready',accepted['workflow_status']);self.assertEqual(4,len(self.model.requests))

    def test_auto_real_failed_tests_revise_same_session_with_current_candidate(self):
        self.restart(auto=True,attempts=3);self.model.scenario='revise'
        result=self.session._operate('auto',{'issue':ISSUE})
        self.assertEqual('candidate_ready',result['workflow_status'])
        record=json.loads((self.root/'state'/result['run_id']/'run.json').read_text())
        self.assertEqual(2,len(record['rounds']))
        self.assertEqual('failed',record['rounds'][0]['verification']);self.assertEqual('passed',record['rounds'][1]['verification'])
        task=self.record();self.assertEqual(2,task['revision']);self.assertEqual(4,len(self.model.requests))
        last_user=[m for m in self.model.requests[2]['messages'] if m['role']=='user'][-1]
        content=last_user['content']
        current=json.loads(content if isinstance(content,str) else content[0]['text'])
        self.assertEqual(1,current['feedback']['round'])
        self.assertTrue(any(t['returncode']!=0 for t in current['feedback']['tests']))
        read_body=self.model.requests[3]['messages'][-1]['content']
        self.assertIn('return abs(a) + abs(b)',read_body)
        self.assertEqual(task['task_id'],record['planning_origin']['task_id'])
        self.assertTrue(record['source_unchanged'])
        self.assertEqual(2,len(record['rounds'][1]['provider_evidence']['usage']))

    def test_same_issue_new_task_never_inherits_history(self):
        self.plan();a=self.record();self.plan();b=self.record()
        self.assertNotEqual(a['task_id'],b['task_id']);self.assertNotEqual(a['checkpoint']['session_id'],b['checkpoint']['session_id'])
        self.assertEqual(1,len([m for m in self.model.requests[2]['messages'] if m['role']=='user']))

    def test_corrupt_or_missing_history_blocks_before_reservation(self):
        rejected=self.decision(self.plan());file=self.session_file();original=file.read_bytes()
        budget=self.session.registry.budget()
        for value in (b'',original+b'{bad}\n'):
            with self.subTest(value=len(value)):
                file.write_bytes(value)
                with self.assertRaises(SafetyError):self.session._resume_preview(rejected['run_id'])
                self.assertEqual(budget,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))
        file.write_bytes(original);file.rename(file.with_suffix('.preserved'))
        with self.assertRaises(SafetyError):self.session._resume_preview(rejected['run_id'])

    def test_legal_uncommitted_tail_is_preserved_but_not_used(self):
        rejected=self.decision(self.plan());file=self.session_file();entries=[json.loads(x) for x in file.read_text().splitlines()]
        tail={'type':'message','id':'abcdefab','parentId':entries[-1]['id'],'timestamp':'2026-10-13T00:00:00Z',
              'message':{'role':'user','timestamp':1,'content':[{'type':'text','text':'UNCOMMITTED_TAIL_DO_NOT_USE'}]}}
        with file.open('ab') as stream:stream.write(json.dumps(tail).encode()+b'\n')
        self.resume(rejected)
        self.assertIn('UNCOMMITTED_TAIL_DO_NOT_USE',file.read_text())
        self.assertNotIn('UNCOMMITTED_TAIL_DO_NOT_USE',json.dumps(self.model.requests[2]['messages']))
        self.assertEqual(2,self.record()['revision'])

    def test_unknown_allowance_survives_restart_and_no_model_is_replayed(self):
        rejected=self.decision(self.plan());identity,allowance=self.session.registry.reserve()
        self.assertEqual(48,allowance);self.restart()
        budget=self.session.registry.budget();self.assertEqual(48,budget['requests_reserved_unknown'])
        with self.assertRaises(SafetyError):self.resume(rejected)
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))
        self.assertEqual('unknown',self.session.registry.load('attempt')[identity]['status'])

    def test_failed_checkpoint_commit_keeps_settled_consumption(self):
        from repo_maintainer.task_session import TaskSessionLease
        with patch.object(TaskSessionLease,'commit',side_effect=SafetyError('synthetic CAS failure')):
            with self.assertRaises(SafetyError):self.plan()
        self.assertEqual(2,self.session.registry.budget()['requests_used'])
        self.assertEqual(0,self.session.registry.budget()['requests_reserved_unknown'])
        self.assertEqual(0,self.record()['revision']);self.assertEqual({},self.session.plans)
        self.assertEqual(1,len(self.session.registry.load('attempt')))
        self.assertEqual('finished',next(iter(self.session.registry.load('attempt').values()))['status'])

    def test_provider_or_source_scope_change_does_not_reserve(self):
        rejected=self.decision(self.plan());budget=self.session.registry.budget()
        self.inner.mock_url=self.model.url+'/other'
        with self.assertRaises(SafetyError):self.session._resume_preview(rejected['run_id'])
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(2,len(self.model.requests))

    def test_original_plan_link_cannot_be_replaced_by_another_task(self):
        first=self.plan();a=self.record();second=self.plan();b=self.record()
        self.session.registry.put('pi_task_link','plan:'+first['plan_id'],{'task_id':b['task_id']})
        with self.assertRaises(SafetyError):self.decision(first,'approve')
        self.assertEqual('awaiting_approval',self.session.plans[first['plan_id']]['view']['status'])
        self.assertEqual(4,len(self.model.requests))

    def test_single_writer_preflight_holds_through_subprocess(self):
        planner=self.session.task_planner;planner.begin(ISSUE)
        record=planner.store._load(planner.identity,planner.scope)
        entered,release=threading.Event(),threading.Event();self.model.gate=(entered,release)
        errors=[]
        def work():
            try:self.session.provider.plan(__import__('repo_maintainer.git',fromlist=['RepositoryGateway']).RepositoryGateway(self.source),ISSUE,(DEMO_TEST,))
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=work);thread.start()
        try:
            # Cold SDK/source preflight on WSL's mounted checkout can exceed 15s.
            # Wait for the actual request gate before asserting the same lock.
            self.assertTrue(entered.wait(60))
            with self.assertRaises(SafetyError):
                with planner.store.lease(planner.identity,planner.scope,record['revision']):pass
        finally:release.set();thread.join(60)
        self.assertFalse(thread.is_alive());self.assertEqual([],errors)

    def test_wrong_repository_turn_is_rejected_before_reservation(self):
        from repo_maintainer.git import RepositoryGateway
        planner=self.session.task_planner;planner.begin(ISSUE)
        other=create_fixture(self.root/'other-source')
        budget=self.session.registry.budget()
        with self.assertRaises(SafetyError):self.session.provider.plan(RepositoryGateway(other),ISSUE,(DEMO_TEST,))
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual([],self.model.requests)

    def test_forged_create_path_and_wrong_task_receipt_never_commit(self):
        rejected=self.decision(self.plan());planner=self.session.task_planner
        task=planner.reference('run',rejected['run_id'],ISSUE)
        planner.begin(ISSUE,task)
        with planner.turn(__import__('repo_maintainer.git',fromlist=['RepositoryGateway']).RepositoryGateway(self.source),ISSUE,(DEMO_TEST,)) as turn:
            self.inner._task_checkpoint={'task_id':'a'*32,'revision':task['revision'],'file':'../escape.jsonl',
                'leaf_id':'abcdefab','session_id':'forged','snapshot_sha256':'b'*64}
            with self.assertRaises(SafetyError):turn.finish('unsettled')
        self.assertEqual(1,self.record()['revision']);self.assertEqual(2,len(self.model.requests))

    def test_committed_history_cannot_grant_unknown_tools_or_expand_paths(self):
        rejected=self.decision(self.plan());file=self.session_file();original=file.read_bytes()
        lines=[json.loads(line) for line in original.splitlines()]
        assistant=next(entry for entry in lines if entry.get('message',{}).get('role')=='assistant')
        call=next(part for part in assistant['message']['content'] if part['type']=='toolCall')
        for name,args in [('execute_shell',{'command':'read secrets'}),('read_source',{'path':'.env'})]:
            with self.subTest(tool=name):
                changed=copy.deepcopy(lines)
                item=next(entry for entry in changed if entry.get('message',{}).get('role')=='assistant')
                part=next(part for part in item['message']['content'] if part['type']=='toolCall')
                part.update(name=name,arguments=args)
                file.write_bytes(b''.join(json.dumps(entry).encode()+b'\n' for entry in changed))
                with self.assertRaises(SafetyError):self.session._resume_preview(rejected['run_id'])
                self.assertEqual(2,len(self.model.requests));self.assertEqual(2,self.session.registry.budget()['requests_used'])
        file.write_bytes(original)

    def test_task_revision_change_invalidates_displayed_continuation(self):
        rejected=self.decision(self.plan());preview=self.session._resume_preview(rejected['run_id'])
        self.resume(rejected);before=len(self.model.requests)
        with self.assertRaises(SafetyError):self.session._operate('resume_run',{'run_id':rejected['run_id'],'revision':preview['revision'],'mode':'approval'})
        self.assertEqual(before,len(self.model.requests));self.assertEqual(2,self.record()['revision'])

    def test_native_job_replay_keeps_one_task_one_attempt_and_live_progress(self):
        identity=uuid.uuid4().hex
        first=self.session.submit(identity,'plan',{'issue':ISSUE})
        repeated=self.session.submit(identity,'plan',{'issue':ISSUE})
        self.assertEqual(first['id'],repeated['id'])
        deadline=time.monotonic()+40
        while self.session.active and time.monotonic()<deadline:time.sleep(.02)
        self.assertIsNone(self.session.active)
        job=self.session.jobs[identity]
        self.assertEqual('finished',job['status'],job.get('error'))
        self.assertEqual(1,len(self.session.registry.load(KIND)))
        self.assertEqual(1,len(self.session.registry.load('attempt')))
        self.assertEqual(2,len(self.model.requests))
        self.assertEqual(2,self.session.registry.budget()['requests_used'])
        replay=self.session.submit(identity,'plan',{'issue':ISSUE})
        self.assertEqual(job['result'],replay['result']);self.assertEqual(2,len(self.model.requests))
        self.assertEqual(2,job['progress']['requests_started'])
