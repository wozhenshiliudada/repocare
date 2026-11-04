import copy
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from repo_maintainer import pi_evidence
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.workflow import repair


def evidence(status="failed"):
    return {"schema_version":1,"status":status,"failure_category":"provider_error" if status=="failed" else None,
            "count_scope":"adapter_stream_invocations","consumption":"unknown" if status!="completed" else "reported",
            "requests":None if status!="completed" else 1,"requests_started":1,"model_cost_verified":False,
            "events_complete":status!="running","events":[{"type":"agent_end"}],"usage":[{"input":3}]}


def counted_evidence():
    return {**evidence(), 'failure_category':'no_plan', 'requests':1, 'consumption':'reported',
            'terminal_count':{'schema_version':1,'completed_streams':1}, 'pi_version':'1.0.4',
            'upstream_commit':'7c10bd4337495ee613f2224843ecdf349b80d1df',
            'source_snapshot_sha256':'a'*64, 'edit_protocol':'exact-replacements-v1'}


class EvidenceTests(unittest.TestCase):
    def test_counted_failure_is_a_terminal_protocol_not_an_inference_from_progress(self):
        value=counted_evidence()
        self.assertEqual(value,pi_evidence.validate_evidence(value,2))
        receipt=pi_evidence.CountedPlanningFailure(value,2)
        self.assertTrue(receipt.matches(value,2))
        changed=copy.deepcopy(value);changed['requests']=2
        self.assertFalse(receipt.matches(changed,2));self.assertFalse(receipt.matches(value,3))
        self.assertIsNone(pi_evidence.progress_evidence(pi_evidence.PREFIX+json.dumps(value).encode()+b'\n',2))
        for field, bad in [('status','running'),('status','completed'),('failure_category','provider_error'),
                           ('failure_category','runtime_timeout'),('failure_category','request_budget'),
                           ('requests',None),('requests',True),('requests_started',2),('events_complete',False),
                           ('consumption','unknown'),('terminal_count',{'schema_version':True,'completed_streams':1}),
                           ('terminal_count',{'schema_version':1,'completed_streams':2}),
                           ('terminal_count',{'schema_version':1,'completed_streams':True}),
                           ('terminal_count',{'schema_version':1,'completed_streams':1,'text':'private'}),
                           ('purpose','task_compaction')]:
            item=copy.deepcopy(value);item[field]=bad
            with self.subTest(field=field,bad=bad),self.assertRaises(SafetyError):
                pi_evidence.validate_evidence(item,2)
        with self.assertRaises(SafetyError):pi_evidence.CountedPlanningFailure(evidence(),2)

    def test_safe_evidence_has_unknown_consumption_despite_known_invocation(self):
        value=pi_evidence.validate_evidence(evidence(),2)
        self.assertIsNone(value["requests"])
        self.assertEqual(1,value["requests_started"])

    def test_metadata_cannot_carry_raw_message_tool_output_or_untrusted_fields(self):
        variants=[]
        for field,value in [("error","untrusted"),("schema_version",True),("usage",[{"cost":"untrusted"}]),
                            ("events",[{"type":"agent_end","text":"untrusted"}]),
                            ("events",[{"type":"tool_execution_end","tool":"bash"}]),
                            ("requests",1),("requests_started",3),("requests_started",True)]:
            item=evidence();item[field]=value;variants.append(item)
        for item in variants:
            with self.assertRaises((SafetyError,TypeError)):
                pi_evidence.validate_evidence(item,2)

    def test_unknown_schema_and_oversize_events_are_not_adopted(self):
        for field,value in [("schema_version",2),("events",[{"type":"agent_end"}]*129),
                            ("usage",[{"input":float("inf")}]),("source_snapshot_sha256","private")]:
            item=evidence();item[field]=value
            with self.assertRaises(SafetyError):pi_evidence.validate_evidence(item,2)

    def test_only_complete_valid_progress_frames_are_used(self):
        item=evidence("running")
        first=pi_evidence.PREFIX+json.dumps(item).encode()+b"\n"
        untrusted=copy.deepcopy(item);untrusted["error"]="private"
        raw=b"arbitrary stderr\n"+first+pi_evidence.PREFIX+json.dumps(untrusted).encode()+b"\n"+pi_evidence.PREFIX+b'{"schema_version":'
        self.assertEqual(item,pi_evidence.progress_evidence(raw,2))
        self.assertIsNone(pi_evidence.progress_evidence(first[:-1],2))
        self.assertIsNone(pi_evidence.progress_evidence(b"x"*2100000,2))


class FakeProcess:
    stdin=stdout=stderr=None
    returncode=1
    def communicate(self,*args,**kwargs):
        return b'{"protocol_version":999,"secret":"syntheticNotARealKey123456"}',pi_evidence.PREFIX+json.dumps(evidence("running")).encode()+b"\n"


class PiFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);create_fixture(self.root/"repository")
        self.repo=RepositoryGateway(self.root/"repository");self.before=self.repo.snapshot()
        self.policy=Policy(("calculator.py",),(DEMO_TEST,),readonly_paths=("tests/test_calculator.py",))
        self.requests=0;self.release=threading.Event();self.mode="failure"
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]));owner.requests+=1
                if owner.mode=="stall":
                    self.send_response(200);self.send_header("Content-Type","text/event-stream");self.end_headers()
                    owner.release.wait(10);return
                if owner.mode in {'no_plan','budget','coalesce'}:
                    delta={'role':'assistant','content':'No structured proposal.'};finish='stop'
                    if owner.mode in {'budget','coalesce'}:
                        args={'path':'calculator.py'};name='read_source'
                        if owner.mode=='coalesce':
                            name='propose_repair'
                            args={'diagnosis':'Two disjoint fixture edits','risk':'synthetic', 'edits':[
                                {'path':'calculator.py','replacements':[{'oldText':'a - b','newText':'a + b'}]},
                                {'path':'calculator.py','replacements':[{'oldText':'def add(a, b):','newText':'def add(a, b):  # checked'}]}]}
                        delta={'role':'assistant','tool_calls':[{'index':0,'id':'call_'+str(owner.requests),
                            'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]};finish='tool_calls'
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                    chunk={'id':'mock','object':'chat.completion.chunk','created':1,'model':'mock-maintenance',
                           'choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
                    self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode());return
                self.send_response(500);self.send_header("Content-Type","application/json");self.end_headers()
                self.wfile.write(json.dumps({"error":{"message":"syntheticNotARealKey123456 Bearer syntheticCredentialNotReal"}}).encode())
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.url="http://127.0.0.1:"+str(self.server.server_port)+"/v1"
        self.provider=PiProvider(self.policy.allowed_paths,self.url,self.policy.readonly_paths,max_requests=2)

    def stop(self):
        self.release.set();self.server.shutdown();self.server.server_close();self.thread.join(5)

    def test_actual_node_failure_keeps_safe_final_events_and_never_accepts_plan(self):
        with self.assertRaisesRegex(SafetyError,"Pi planning failed"):
            self.provider.plan(self.repo,"addition bug",self.policy.allowed_tests)
        value=self.provider.evidence
        self.assertEqual("provider_error",value["failure_category"])
        self.assertEqual(1,value["requests_started"]);self.assertIsNone(value["requests"])
        self.assertTrue(value["events_complete"])
        self.assertIn({"type":"agent_end"},value["events"])
        self.assertNotIn("syntheticNotARealKey",json.dumps(value))
        self.assertEqual(1,self.requests);self.assertEqual(self.before,self.repo.snapshot())

    def test_real_pi_coalesced_proposal_passes_independent_python_verification_without_writing_source(self):
        self.mode='coalesce'
        plan=self.provider.plan(self.repo,'addition bug',self.policy.allowed_tests)
        self.assertEqual(1,len(plan.edits));self.assertIn('return a + b',plan.edits[0].content)
        self.assertIn('# checked',plan.edits[0].content)
        self.assertEqual(1,self.requests);self.assertEqual(1,self.provider.requests_used)
        self.assertEqual(self.before,self.repo.snapshot())

    def test_real_pi_clean_failure_settles_count_and_keeps_failed_task_after_restart(self):
        from repo_maintainer.ui_registry import UIRegistry,DurableProvider
        self.mode='no_plan'
        registry=UIRegistry(self.root/'journal',{'provider':{'request_budget':2}})
        try:
            durable=DurableProvider(self.provider,registry)
            with self.assertRaises(pi_evidence.CountedPlanningFailure):
                durable.plan(self.repo,'addition bug',self.policy.allowed_tests)
            self.assertEqual(2,self.provider.requests_used)
            self.assertEqual((2,0,0),tuple(registry.budget()[k] for k in
                ('requests_used','requests_reserved_unknown','requests_remaining')))
            attempt=next(iter(registry.load('attempt').values()))
            self.assertEqual(('finished','failed'),(attempt['status'],attempt['outcome']))
            registry.close();registry=UIRegistry(self.root/'journal',{'provider':{'request_budget':2}})
            self.assertEqual(2,registry.budget()['requests_used'])
            self.assertEqual(1,len(registry.load('attempt')));self.assertEqual(2,self.requests)
        finally:registry.close()
        self.assertEqual(self.before,self.repo.snapshot())

    def test_real_pi_request_exhaustion_counts_only_actual_calls_and_direct_reuse_cannot_reset_budget(self):
        self.mode='budget'
        with self.assertRaises(pi_evidence.CountedPlanningFailure):
            self.provider.plan(self.repo,'addition bug',self.policy.allowed_tests)
        self.assertEqual('request_budget',self.provider.evidence['failure_category'])
        self.assertEqual(2,self.provider.evidence['requests']);self.assertEqual(2,self.requests)
        with self.assertRaisesRegex(SafetyError,'budget exhausted'):
            self.provider.plan(self.repo,'again',self.policy.allowed_tests)
        self.assertEqual(2,self.requests)

    def test_ui_counted_failure_has_no_approved_plan_or_committed_native_checkpoint(self):
        from repo_maintainer.task_session import KIND
        self.mode='no_plan'
        session=UISession(self.repo.root,self.policy,self.provider,self.root/'runs')
        try:
            with self.assertRaises(pi_evidence.CountedPlanningFailure):
                session._operate('plan',{'issue':'addition bug'})
            state=session.state()
            self.assertEqual((2,0,0),tuple(state[k] for k in
                ('requests_used','requests_reserved_unknown','requests_remaining')))
            self.assertEqual([],state['plans']);self.assertEqual([],state['runs'])
            task=next(iter(session.registry.load(KIND).values()))
            self.assertEqual(0,task['revision']);self.assertIsNone(task.get('checkpoint'))
            self.assertEqual('failed',state['planning_attempts'][0]['outcome'])
        finally:session.close()
        self.assertEqual(self.before,self.repo.snapshot());self.assertEqual(2,self.requests)

    def test_terminal_receipt_exit_snapshot_or_status_contradiction_stays_unknown(self):
        self.mode='no_plan'
        original=self.provider._generate
        # Obtain a real terminal receipt, then corrupt only its parent boundary.
        with self.assertRaises(pi_evidence.CountedPlanningFailure):
            original(self.repo,'addition bug',self.policy.allowed_tests,None)
        safe={k:v for k,v in self.provider.evidence.items() if k not in {'command','returncode','total_requests_used','total_request_budget'}}
        snapshot=SimpleNamespace(head=self.repo.head,read=self.repo.read)
        for variant in ('exit','snapshot','status','extra'):
            body={'protocol_version':1,'status':'failed','evidence':copy.deepcopy(safe)}
            code=1
            if variant=='exit':code=0
            if variant=='snapshot':body['evidence']['source_snapshot_sha256']='0'*64
            if variant=='status':body['status']='completed'
            if variant=='extra':body['plan']={}
            process=SimpleNamespace(returncode=code,stdin=None,stdout=None,stderr=None,
                communicate=lambda *a,**kw:(json.dumps(body).encode(),b''))
            self.provider.requests_used=0
            with self.subTest(variant=variant),patch('repo_maintainer.pi_provider.subprocess.Popen',return_value=process):
                with self.assertRaises(SafetyError) as caught:
                    self.provider.plan(snapshot,'addition bug',self.policy.allowed_tests)
                self.assertNotIsInstance(caught.exception,pi_evidence.CountedPlanningFailure)
                self.assertIsNone(self.provider.evidence['requests']);self.assertEqual('unknown',self.provider.evidence['consumption'])
                self.assertEqual(0,self.provider.requests_used)

    def test_parent_timeout_keeps_partial_progress_and_unknown_usage(self):
        self.mode="stall"
        with patch.object(pi_evidence,"PROCESS_TIMEOUT_SECONDS",3):
            with self.assertRaisesRegex(SafetyError,"timed out"):
                self.provider.plan(self.repo,"addition bug",self.policy.allowed_tests)
        value=self.provider.evidence
        self.assertEqual("parent_timeout",value["failure_category"])
        self.assertFalse(value["events_complete"]);self.assertEqual(1,value["requests_started"])
        self.assertIsNone(value["requests"]);self.assertEqual("unknown",value["consumption"])
        self.assertEqual(1,self.requests);self.assertEqual(self.before,self.repo.snapshot())

    def test_unknown_result_schema_retains_valid_partial_evidence_without_raw_text(self):
        snapshot = SimpleNamespace(head=self.repo.head, read=self.repo.read)
        with patch("repo_maintainer.pi_provider.subprocess.Popen",return_value=FakeProcess()):
            with self.assertRaises(SafetyError):self.provider.plan(snapshot,"bug",self.policy.allowed_tests)
        self.assertEqual("protocol_error",self.provider.evidence["failure_category"])
        self.assertEqual(1,self.provider.evidence["requests_started"])
        self.assertFalse(self.provider.evidence["events_complete"])
        self.assertNotIn("syntheticNotARealKey",json.dumps(self.provider.evidence))
        self.assertEqual(0,self.requests)

    def test_workflow_failure_writes_real_report_and_no_candidate_or_test_runs(self):
        record=repair(self.repo.root,"addition bug",self.provider,self.policy,"auto",self.root/"runs")
        self.assertEqual("failed",record["status"]);self.assertTrue(record["source_unchanged"])
        self.assertEqual([],record["baseline_tests"]);self.assertFalse(Path(record["workspace"]).exists())
        report=(self.root/"runs"/record["run_id"]/"report.md").read_text()
        self.assertIn("provider_error",report);self.assertNotIn("syntheticNotARealKey",report)
        self.assertEqual(1,record["provider_evidence"]["requests_started"])
        self.assertEqual(self.before,self.repo.snapshot())

    def test_ui_failure_persists_events_and_unknown_reservation_across_restart(self):
        session=UISession(self.repo.root,self.policy,self.provider,self.root/"runs")
        try:
            with self.assertRaises(SafetyError):session._operate("plan",{"issue":"addition bug"})
        finally:session.close()
        session=UISession(self.repo.root,self.policy,self.provider,self.root/"runs");self.addCleanup(session.close)
        state=session.state();self.assertEqual(2,state["requests_reserved_unknown"])
        self.assertEqual(0,state["requests_remaining"]);self.assertEqual([],state["plans"]);self.assertEqual([],state["runs"])
        attempt=state["planning_attempts"][0]
        self.assertEqual("unknown",attempt["status"]);self.assertTrue(attempt["evidence"]["events_complete"])
        self.assertIsNone(attempt["evidence"]["requests"]);self.assertEqual(1,attempt["evidence"]["requests_started"])
        with self.assertRaisesRegex(SafetyError,"reserved"):
            session._operate("plan",{"issue":"another try"})
        self.assertEqual(1,self.requests);self.assertNotIn("syntheticNotARealKey",json.dumps(state))
        self.assertEqual(self.before,self.repo.snapshot())


if __name__=="__main__":unittest.main()
