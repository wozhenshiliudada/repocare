"""Actual Pi AgentSession + Native + original ledger, synthetic loopback replies."""
from pathlib import Path
import io
import json
import unittest

import test_pi_evidence as fixtures
from repo_maintainer.behavior_validation import make_contract
from repo_maintainer.models import Policy, digest
from repo_maintainer.target_environment import discover_environment, make_grant
from repo_maintainer.task_planning import TaskPlanner
from repo_maintainer.task_session import repository_identity
from repo_maintainer.ui_registry import UIRegistry, DurableProvider
from repo_maintainer.workflow import repair, cleanup_run
from repo_maintainer.workflow_environment import GrantedEnvironment


class BehaviorPiReplayTests(unittest.TestCase):
    def test_real_pi_native_and_original_budget_accept_fixed_behavior_marker(self):
        self._trial()

    def test_failed_candidate_revises_in_worktree_with_original_contract_and_native(self):
        self._trial(revise=True)

    def _trial(self,revise=False):
        fixture = fixtures.PiFailureTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.mode = 'coalesce'
        payloads=[]
        handler=fixture.server.RequestHandlerClass
        original=handler.do_POST
        def capture(request):
            raw=request.rfile.read(int(request.headers['Content-Length']))
            payloads.append(json.loads(raw))
            stream=request.rfile
            request.rfile=io.BytesIO(raw)
            try:
                if revise:
                    fixture.requests+=1
                    if len(payloads)==2:
                        name,args='read_source',{'path':'calculator.py'}
                    else:
                        old,new=('a - b','a * b') if len(payloads)==1 else ('a * b','a + b')
                        name,args='propose_repair',{'diagnosis':'Actual candidate revision','risk':'synthetic',
                            'edits':[{'path':'calculator.py','replacements':[{'oldText':old,'newText':new}]}]}
                    delta={'role':'assistant','tool_calls':[{'index':0,'id':'call_'+str(fixture.requests),
                        'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
                    chunk={'id':'mock','object':'chat.completion.chunk','created':1,'model':'mock-maintenance',
                           'choices':[{'index':0,'delta':delta,'finish_reason':'tool_calls'}]}
                    request.send_response(200);request.send_header('Content-Type','text/event-stream');request.end_headers()
                    request.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())
                    return
                return original(request)
            finally:
                request.rfile=stream
        handler.do_POST=capture
        issue = 'addition bug'
        contract = make_contract(repository_id=repository_identity(fixture.repo.root,fixture.repo.common_dir),
            base_sha=fixture.repo.head,request_sha256=digest(issue.encode()),requirement='add(2,3) must equal5',
            cases=[{'id':'addition','module':'calculator.py','callable':'add','args':[2.0,3.0],
                    'kwargs':{},'expect':{'kind':'json','value':5.0},'missing_behavior':False}])
        plan = discover_environment(fixture.repo,fixture.repo.head,['.'],behavior_contract=contract)
        grant = make_grant(plan,allow_local_code=True)
        policy = Policy(('calculator.py',),tuple(tuple(row['argv']) for row in plan['checks']),
                        timeout_seconds=10,readonly_paths=('tests/test_calculator.py',))
        ceiling=4 if revise else 2
        fixture.provider.max_requests=ceiling
        registry = UIRegistry(fixture.root/'original-journal',{'provider':{'request_budget':ceiling}})
        self.addCleanup(registry.close)
        durable = DurableProvider(fixture.provider,registry)
        planner = TaskPlanner(registry,fixture.repo.root,policy,fixture.provider,
                              {'behavior_contract':contract['contract_sha256']})
        durable.task_planner = planner
        task_id = planner.begin(issue)
        record = repair(fixture.repo.root,issue,durable,policy,'auto',fixture.root/'runs',
                        environment_factory=GrantedEnvironment(plan,grant),max_attempts=2 if revise else 1)
        self.assertEqual('candidate_ready',record['status'],record.get('error'))
        self.addCleanup(cleanup_run,Path(record['workspace']).parent)
        self.assertTrue(record['rounds'][-1]['behavior_comparison']['candidate_verified'])
        self.assertEqual(2 if revise else 1,len(record['rounds']))
        if revise:
            self.assertTrue(any(row['returncode']!=0 for row in record['rounds'][0]['tests']))
        self.assertEqual(contract['contract_sha256'],record['provider_evidence']['behavior_contract_sha256'])
        self.assertIsNone(fixture.provider._behavior_contract)
        self.assertIsNone(fixture.provider._behavior_source)
        prompts=json.dumps(payloads[0]['messages'],ensure_ascii=False)
        self.assertIn('behavior_context',prompts)
        self.assertIn('addition',prompts)
        self.assertIn('expected-result examples',prompts)
        user=next(m for m in payloads[0]['messages'] if m['role']=='user')['content']
        packet=json.loads(user if isinstance(user,str) else user[0]['text'])
        context=packet['behavior_context']
        self.assertIn('"args":[2.0,3.0]',context['body_json'])
        self.assertIn('"value":5.0',context['body_json'])
        self.assertEqual(contract['contract_sha256'],digest(context['body_json'].encode()))
        self.assertEqual(['propose_repair','read_source'],sorted(t['function']['name'] for t in payloads[0]['tools']))
        used=3 if revise else 1
        self.assertEqual(used,fixture.requests)
        budget = registry.budget()
        self.assertEqual((used,0,1),tuple(budget[k] for k in
                         ('requests_used','requests_reserved_unknown','requests_remaining')))
        revision=2 if revise else 1
        self.assertEqual(revision,registry.load('pi_task')[task_id]['revision'])
        planner.link('run',record['run_id'])
        before = fixture.requests
        reference = planner.reference('run',record['run_id'],issue)
        self.assertEqual({'task_id':task_id,'revision':revision},reference)
        self.assertEqual(before,fixture.requests)
        self.assertEqual('finished',next(iter(registry.load('attempt').values()))['status'])


if __name__ == '__main__':
    unittest.main()
