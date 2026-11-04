"""Independent Python controller processes share the same original journal/native Pi file."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from test_task_planning import SyntheticModel, ISSUE
from repo_maintainer.demo import create_fixture

WORKER=r'''
import hashlib,json,sys
from pathlib import Path
from repo_maintainer.demo import DEMO_TEST
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import Policy
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.task_session import KIND
data=json.load(sys.stdin)
root=Path(data['root'])
provider=PiProvider(('calculator.py',),data['url'],readonly_paths=('tests/test_calculator.py',),max_requests=50)
session=UISession(root/'source',Policy(('calculator.py',),(DEMO_TEST,)),provider,root/'state')
try:
    before=session.registry.budget()
    if data['phase']=='create':
        plan=session._operate('plan',{'issue':data['issue']})
        run=session._operate('decision',{**plan,'decision':'reject'})
        run_id=run['run_id']
    else:
        run_id=data['run_id']
        preview=session._resume_preview(run_id)
        if data['phase']=='continue':
            plan=session._operate('resume_run',{'run_id':run_id,'revision':preview['revision'],'mode':'approval'})
            run_id=session._operate('decision',{**plan,'decision':'reject'})['run_id']
    task_id=session.registry.load('pi_task_link')['run:'+run_id]['task_id']
    task=session.registry.load(KIND)[task_id]
    file=session.task_planner.store.directory(task_id)/'sessions'/task['checkpoint']['file']
    result={'pid':__import__('os').getpid(),'run_id':run_id,'task_id':task_id,'revision':task['revision'],
            'session_id':task['checkpoint']['session_id'],'file_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
            'budget_before':before,'budget_after':session.registry.budget()}
    print(json.dumps(result),flush=True)
finally:session.close()
'''


class ControllerProcessTests(unittest.TestCase):
    def test_three_controller_processes_create_readonly_open_continue(self):
        with tempfile.TemporaryDirectory(prefix='pi-controller-process-') as temp:
            root=Path(temp);create_fixture(root/'source')
            model=SyntheticModel()
            try:
                def phase(name,run_id=None):
                    payload={'root':str(root),'url':model.url,'issue':ISSUE,'phase':name,'run_id':run_id}
                    proc=subprocess.run([sys.executable,'-X','utf8','-c',WORKER],input=json.dumps(payload),
                                         text=True,encoding='utf-8',capture_output=True,env=os.environ.copy(),timeout=65)
                    self.assertEqual(0,proc.returncode,proc.stderr)
                    return json.loads(proc.stdout)
                first=phase('create');self.assertEqual(2,len(model.requests))
                opened=phase('open',first['run_id'])
                self.assertEqual(2,len(model.requests));self.assertEqual(first['file_sha256'],opened['file_sha256'])
                self.assertEqual(opened['budget_before'],opened['budget_after'])
                second=phase('continue',first['run_id'])
                self.assertEqual(4,len(model.requests));self.assertEqual(3,len({first['pid'],opened['pid'],second['pid']}))
                self.assertEqual(first['task_id'],second['task_id']);self.assertEqual(first['session_id'],second['session_id'])
                self.assertEqual(2,second['revision']);self.assertEqual(4,second['budget_after']['requests_used'])
                self.assertEqual(50,second['budget_after']['request_budget']);self.assertEqual(0,second['budget_after']['requests_reserved_unknown'])
                self.assertEqual(2,len([m for m in model.requests[2]['messages'] if m['role']=='user']))
            finally:model.close()
