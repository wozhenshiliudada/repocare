"""Three real Python controllers: actual red test, read-only restart, explicit green continuation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import test_task_planning as fixture
import test_task_planning_process as process_fixture
from repo_maintainer.demo import create_fixture

WORKER=process_fixture.WORKER.replace("'decision':'reject'","'decision':'approve'")
WORKER=WORKER.replace("print(json.dumps(result),flush=True)","""
    from repo_maintainer.task_feedback import KIND
    from repo_maintainer.task_session import fingerprint
    result['feedback_records_sha256']=fingerprint(session.registry.load(KIND))
    result['status']=next(r for r in session.state()['runs'] if r['run_id']==run_id)['status']
    print(json.dumps(result),flush=True)
""")

class FailureControllerProcessTests(unittest.TestCase):
    def test_sdk_rejections_checkpoint_restart_new_approval_red_to_green(self):
        with tempfile.TemporaryDirectory(prefix='pi-schema-process-') as temp:
            root=Path(temp);create_fixture(root/'source')
            original=(root/'source'/'calculator.py').read_bytes()
            model=fixture.SyntheticModel();model.scenario='revise'
            model.schema_calls=[('read_source',{'query':'def im('}),
                ('propose_repair',{'diagnosis':'format mistake','risk':'fixture','edits':['not an object']})]
            try:
                def phase(name,run_id=None):
                    data={'root':str(root),'url':model.url,'issue':fixture.ISSUE,'phase':name,'run_id':run_id}
                    proc=subprocess.run([sys.executable,'-X','utf8','-c',WORKER],input=json.dumps(data),
                        text=True,encoding='utf-8',capture_output=True,env=os.environ.copy(),timeout=100)
                    self.assertEqual(proc.returncode,0,proc.stderr)
                    return json.loads(proc.stdout)
                first=phase('create')
                self.assertEqual('rolled_back',first['status']);self.assertEqual(4,len(model.requests))
                native=list((root/'state'/'task-sessions').glob('*/sessions/*.jsonl'))
                # TaskSessionStore lives beside the original UI registry directory.
                if not native:native=list((root/'task-sessions').glob('*/sessions/*.jsonl'))
                self.assertEqual(1,len(native))
                entries=[json.loads(line) for line in native[0].read_bytes().splitlines()]
                rejected=[e['message'] for e in entries if e['type']=='message' and
                    e['message']['role']=='toolResult' and e['message']['isError']]
                self.assertEqual(2,len(rejected))
                self.assertTrue(all(m['details']=={} and m['content'][0]['text'].startswith('Validation failed for tool "')
                                    for m in rejected))
                opened=phase('open',first['run_id'])
                self.assertEqual(4,len(model.requests));self.assertEqual(first['file_sha256'],opened['file_sha256'])
                self.assertEqual(first['feedback_records_sha256'],opened['feedback_records_sha256'])
                self.assertEqual(opened['budget_before'],opened['budget_after'])
                second=phase('continue',first['run_id'])
                self.assertEqual('candidate_ready',second['status']);self.assertEqual(8,len(model.requests))
                self.assertEqual(first['task_id'],second['task_id']);self.assertEqual(first['session_id'],second['session_id'])
                self.assertEqual(2,second['revision']);self.assertEqual(3,len({first['pid'],opened['pid'],second['pid']}))
                self.assertEqual(8,second['budget_after']['requests_used'])
                self.assertEqual(0,second['budget_after']['requests_reserved_unknown'])
                self.assertEqual(50,second['budget_after']['request_budget'])
                self.assertEqual(original,(root/'source'/'calculator.py').read_bytes())
                user=[m for m in model.requests[4]['messages'] if m['role']=='user'][-1]
                content=json.loads(user['content'] if isinstance(user['content'],str) else user['content'][0]['text'])
                self.assertTrue(any(t['returncode'] for t in content['continuation_feedback']['tests']))
            finally:model.close()

    def test_red_exit_open_only_and_feedback_green_three_processes(self):
        with tempfile.TemporaryDirectory(prefix='pi-failure-process-') as temp:
            root=Path(temp);create_fixture(root/'source')
            model=fixture.SyntheticModel();model.scenario='revise'
            try:
                def phase(name,run_id=None):
                    data={'root':str(root),'url':model.url,'issue':fixture.ISSUE,'phase':name,'run_id':run_id}
                    process=subprocess.run([sys.executable,'-X','utf8','-c',WORKER],input=json.dumps(data),
                        text=True,encoding='utf-8',capture_output=True,env=os.environ.copy(),timeout=90)
                    self.assertEqual(process.returncode,0,process.stderr)
                    return json.loads(process.stdout)
                first=phase('create');self.assertEqual(first['status'],'rolled_back');self.assertEqual(2,len(model.requests))
                opened=phase('open',first['run_id']);self.assertEqual(2,len(model.requests))
                self.assertEqual(first['file_sha256'],opened['file_sha256'])
                self.assertEqual(first['feedback_records_sha256'],opened['feedback_records_sha256'])
                self.assertEqual(opened['budget_before'],opened['budget_after'])
                second=phase('continue',first['run_id'])
                self.assertEqual(second['status'],'candidate_ready')
                self.assertEqual(first['task_id'],second['task_id']);self.assertEqual(first['session_id'],second['session_id'])
                self.assertEqual(3,len({first['pid'],opened['pid'],second['pid']}))
                user=[m for m in model.requests[2]['messages'] if m['role']=='user'][-1]
                content=json.loads(user['content'] if isinstance(user['content'],str) else user['content'][0]['text'])
                self.assertTrue(any(t['returncode'] for t in content['continuation_feedback']['tests']))
                self.assertEqual(4,second['budget_after']['requests_used']);self.assertEqual(50,second['budget_after']['request_budget'])
                self.assertEqual(0,second['budget_after']['requests_reserved_unknown'])
            finally:model.close()
