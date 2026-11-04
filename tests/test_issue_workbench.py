"""Issue HTTP queue, original ledger and genuine Pi/Native with synthetic SSE."""
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid

from starlette.testclient import TestClient

from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.issue_workbench import IssueWorkbench, authorized_rules
from repo_maintainer.local_ui import UISession, create_app
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.workflow import cleanup_run
from test_issue_intake import FakeGitHub
from test_task_planning import SyntheticModel


class IssueWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.repo=RepositoryGateway(create_fixture(self.root/'source'))
        self.repo.command('remote','add','origin','https://github.com/example/project.git')
        self.github=FakeGitHub(self.repo.head)
        self.github.issue.update(title='Fix calculator add subtraction bug',body='add(2,3) must be 5; preserve mixed operands.')
        self.model=SyntheticModel()
        self.addCleanup(self.model.close)
        self.policy=Policy(('calculator.py',),(DEMO_TEST,),30,('tests/test_calculator.py',))
        self.session=self.new_session()
        self.addCleanup(self.finish)

    def new_session(self):
        p=PiProvider(self.policy.allowed_paths,self.model.url,self.policy.readonly_paths,25)
        s=UISession(self.repo.root,self.policy,p,self.root/'state')
        s.issue_workbench=IssueWorkbench(s,self.github)
        self.client=TestClient(create_app(s,12345),base_url='http://127.0.0.1:12345')
        self.headers={'Authorization':'Bearer '+s.token}
        return s

    def finish(self):
        self.client.close()
        self.session.close()
        for row in self.session.registry.load('issue_execution').values():
            directory=self.root/'state'/row['run_id']
            if (directory/'workspace').exists(): cleanup_run(directory)

    def post(self,action,payload):
        response=self.client.post('/api/actions/'+action,headers=self.headers,
            json={'request_id':uuid.uuid4().hex,**payload})
        self.assertEqual(response.status_code,202,response.text)
        return response.json()

    def wait(self,job):
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            value=next(j for j in self.session.state()['jobs'] if j['id']==job['id'])
            if value['status']!='running':return value
            time.sleep(.02)
        self.fail('Issue job timed out')

    def execute(self,action,payload):
        value=self.wait(self.post(action,payload))
        self.assertEqual(value['status'],'finished',value)
        return value['result']

    def config(self,**updates):
        return {'read_roots':['.'],'write_roots':['.'],'trusted_actors':['maintainer'],
                'mode':'approval','allow_install':False,'allow_network':False,'workflow_ids':[1001],**updates}

    def configure(self):
        p=self.execute('issue_config_preview',self.config())
        return self.execute('issue_configure',{'config':p['configuration'],'revision':p['revision']})

    def prepare(self):
        self.configure()
        job=self.execute('issue_investigate',{'number':7})
        self.assertEqual(job['status'],'investigated',job)
        return self.execute('issue_environment',{'investigation_id':job['id']})

    def test_original_twenty_file_rule_remains_valid_after_capacity_increase(self):
        from unittest.mock import patch
        from repo_maintainer.maintenance_rules import maintenance_rules
        def legacy(*args,**kwargs):
            kwargs.setdefault('max_files',20)
            return maintenance_rules(*args,**kwargs)
        with patch('repo_maintainer.issue_workbench.maintenance_rules',side_effect=legacy):
            prepared=self.prepare()
        self.assertEqual(prepared['rules']['max_files'],20)
        self.assertTrue(authorized_rules(self.session.registry,self.repo,prepared['spec'],prepared['rules']))
        self.assertEqual(self.model.requests,[])

    def pending(self):
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            rows=self.session.state()['issue_workbench']['approvals']
            value=next((r for r in rows if r['status']=='pending'),None)
            if value:return value
            if not self.session.active:self.fail(self.session.state()['jobs'])
            time.sleep(.02)
        self.fail('No original pending approval')

    def test_configuration_has_no_calls_or_budget_reset_and_is_immutable(self):
        before=self.repo.snapshot()
        original=self.session.registry.budget()
        self.configure()
        self.assertEqual(original,self.session.registry.budget())
        self.assertEqual(before,self.repo.snapshot())
        self.assertEqual(len(self.model.requests),0)
        p=self.execute('issue_config_preview',self.config(write_roots=['other']))
        job=self.wait(self.post('issue_configure',{'config':p['configuration'],'revision':p['revision']}))
        self.assertEqual(job['status'],'failed')
        self.assertEqual(self.session.issue_workbench._config()['write_roots'],['.'])

    def test_environment_retry_http_uses_original_task_fresh_approval_and_no_refresh_replay(self):
        from unittest.mock import patch
        prepared=self.prepare()
        original_source=self.repo.snapshot()
        with patch('repo_maintainer.workflow_environment.GrantedEnvironment.__call__',
                   side_effect=SafetyError('Synthetic dependency preparation failure')):
            job=self.post('issue_execute',{'investigation_id':prepared['id'],'revision':prepared['revision']})
            approval=self.pending()
            self.post('issue_decide',{'approval_id':approval['id'],'revision':approval['revision'],'decision':'approve'})
            first=self.wait(job)['result']
        self.assertEqual(first['status'],'failed_no_replay',first)
        path=self.session.issue_workbench._execution(first['id'])[2].runs/first['run_id']/'run.json'
        old=path.read_bytes()
        preview=self.execute('issue_environment_retry_preview',{'execution_id':first['id']})
        self.assertEqual(preview['budget']['requests_used'],2)
        self.assertEqual(len(self.model.requests),2)
        job=self.post('issue_retry_environment',{'execution_id':first['id'],'revision':preview['revision']})
        second_approval=self.pending()
        self.assertNotEqual(second_approval['id'],approval['id'])
        self.post('issue_decide',{'approval_id':second_approval['id'],
                     'revision':second_approval['revision'],'decision':'approve'})
        result=self.wait(job)
        self.assertEqual(result['status'],'finished',result)
        second=result['result']
        self.assertEqual(second['status'],'awaiting-review',second)
        self.assertEqual(second['task_id'],first['task_id'])
        self.assertEqual(second['previous_execution'],first['id'])
        self.assertEqual(path.read_bytes(),old)
        self.assertEqual(self.session.registry.budget()['requests_used'],4)
        self.assertEqual(original_source,self.repo.snapshot(exclude_branch='maintenance/'+second['run_id']))
        for _ in range(2):self.session.state()
        duplicate=self.wait(self.post('issue_retry_environment',{'execution_id':first['id'],'revision':preview['revision']}))
        self.assertEqual(duplicate['status'],'failed',duplicate)
        self.assertEqual(len(self.model.requests),4)

    def test_controlled_enhancement_needs_new_red_acceptance_not_old_green_tests(self):
        implementation=self.repo.root/'calculator.py'
        original=implementation.read_text().replace('return a - b','return a + b')
        implementation.write_text(original,encoding='utf-8')
        tests=self.repo.root/'tests/test_calculator.py'
        tests.write_text(tests.read_text()+'''\nclass SubtractAcceptance(unittest.TestCase):
    def test_new_subtract_function(self):
        import calculator
        self.assertTrue(hasattr(calculator, 'subtract'), 'New function is required')
        self.assertEqual(calculator.subtract(5, 3), 2)
        self.assertEqual(calculator.subtract(-2, 3), -5)
''',encoding='utf-8')
        self.repo.command('add','calculator.py','tests/test_calculator.py')
        self.repo.command('-c','user.name=Acceptance Author','-c','user.email=fixture@example.invalid',
                          'commit','-m','specify new subtract acceptance; existing add is already correct')
        self.github.head=self.repo.head
        self.github.issue.update(title='Add calculator subtract function',body='subtract(a,b) returns signed a-b; preserve add.',
                                 labels=[{'name':'enhancement'},{'name':'ready-for-agent'}])
        original=self.repo.read('calculator.py').decode('utf-8')  # Exact CRLF/LF bytes, as read_source sees them.
        self.model.schema_calls=[('read_source',{'path':'calculator.py'}),('propose_repair',{
            'diagnosis':'Synthetic enhancement wiring','risk':'fixture only','edits':[{'path':'calculator.py',
            'replacements':[{'oldText':original,'newText':original+'\ndef subtract(a, b):\n    return a - b\n'}]}]})]
        self.session.issue_workbench.permit_auto=True
        preview=self.execute('issue_config_preview',self.config(mode='auto'))
        self.execute('issue_configure',{'config':preview['configuration'],'revision':preview['revision']})
        investigation=self.execute('issue_investigate',{'number':7})
        p=self.execute('issue_environment',{'investigation_id':investigation['id']})
        before=self.repo.snapshot()
        result=self.execute('issue_execute',{'investigation_id':p['id'],'revision':p['revision']})
        self.assertEqual(result['status'],'awaiting-review',result)
        run=self.execute('issue_result',{'execution_id':result['id']})
        self.assertEqual(run['status'],'candidate_ready')
        self.assertTrue(any(t['returncode']!=0 for t in run['baseline_tests']))
        self.assertTrue(all(t['returncode']==0 for t in run['candidate_tests']))
        self.assertEqual(tests.read_bytes(),self.repo.read('tests/test_calculator.py'))
        self.assertFalse(self.session.issue_workbench.view()['approvals'],'Explicit auto mode has no manual approval inheritance')
        self.assertEqual(self.session.registry.budget()['requests_used'],2)
        self.assertEqual(before,self.repo.snapshot(exclude_branch='maintenance/'+result['run_id']))

    def test_forged_configuration_and_untrusted_issue_cannot_register_authority(self):
        p=self.execute('issue_config_preview',self.config())
        p['configuration']['write_roots']=['other']
        self.assertEqual(self.wait(self.post('issue_configure',{'config':p['configuration'],'revision':p['revision']}))['status'],'failed')
        self.assertFalse(self.session.issue_workbench.view()['configured'])
        self.configure()
        self.github.events[0]['actor']['login']='intruder'
        value=self.execute('issue_investigate',{'number':7})
        self.assertEqual(value['status'],'untrusted_label')
        self.assertFalse(self.session.registry.load('issue_workbench_scope'))

    def test_dynamic_scope_requires_registered_controller_derivation(self):
        prepared=self.prepare()
        controller=self.session.issue_workbench
        self.assertTrue(authorized_rules(self.session.registry,self.repo,prepared['spec'],prepared['rules']))
        self.assertNotEqual(prepared['policy']['allowed_tests'],[list(DEMO_TEST)])
        rules={**prepared['rules'],'write_roots':['outside']}
        self.assertFalse(authorized_rules(self.session.registry,self.repo,prepared['spec'],rules))
        self.assertEqual(self.session.policy,self.policy)
        self.assertEqual(len(self.model.requests),0)
        # Duplicate investigation returns the existing record, no new model/claim.
        duplicate=self.execute('issue_investigate',{'number':7})
        self.assertEqual(duplicate['id'],prepared['id'])

    def test_new_remote_version_invalidates_environment_before_model(self):
        p=self.prepare()
        self.github.issue['updated_at']='2026-10-21T12:00:00Z'
        value=self.wait(self.post('issue_execute',{'investigation_id':p['id'],'revision':p['revision']}))
        self.assertEqual(value['status'],'failed',value)
        self.assertEqual(len(self.model.requests),0)
        self.assertFalse(self.session.registry.load('issue_execution'))

    def test_http_authority_and_input_boundaries(self):
        for action,body in [('issue_read',{'number':True}),('issue_read',{'number':7,'url':'https://evil.invalid'}),
            ('issue_execute',{'investigation_id':'x','revision':'f'*64})]:
            r=self.client.post('/api/actions/'+action,headers=self.headers,json={'request_id':uuid.uuid4().hex,**body})
            self.assertEqual(r.status_code,409,r.text)
        r=self.client.post('/api/actions/issue_read',json={'request_id':uuid.uuid4().hex,'number':7})
        self.assertEqual(r.status_code,401)
        r=self.client.post('/api/actions/issue_read',headers={**self.headers,'Origin':'https://evil.invalid'},
            json={'request_id':uuid.uuid4().hex,'number':7})
        self.assertEqual(r.status_code,403)

    def test_real_pi_queue_pending_approval_candidate_and_restart_keep_original_ledger(self):
        before=self.repo.snapshot()
        p=self.prepare()
        job=self.post('issue_execute',{'investigation_id':p['id'],'revision':p['revision']})
        pending=self.pending()
        self.assertTrue(self.session.active)
        self.assertEqual(len(self.model.requests),2)
        run=next(iter(self.session.registry.load('issue_execution').values()))
        self.assertFalse((self.root/'state'/run['run_id']/'workspace').exists(),'No edit before approval')
        decision=self.post('issue_decide',{'approval_id':pending['id'],'revision':pending['revision'],'decision':'approve'})
        self.assertEqual(decision['status'],'decision_recorded')
        result=self.wait(job)
        self.assertEqual(result['status'],'finished',result)
        self.assertEqual(result['result']['status'],'awaiting-review',result)
        execution=result['result']
        candidate=self.execute('issue_result',{'execution_id':execution['id']})
        self.assertEqual(candidate['status'],'candidate_ready',candidate)
        self.assertEqual(self.session.registry.budget()['requests_used'],2)
        self.assertEqual(self.session.registry.budget()['requests_reserved_unknown'],0)
        self.assertEqual(self.session.policy,self.policy)
        self.assertFalse(self.session.state()['runs'],'Issue scope cannot enter fixed manual controls')
        self.assertEqual(before,self.repo.snapshot(exclude_branch='maintenance/'+execution['run_id']))
        budget=self.session.registry.budget()
        self.client.close();self.session.close()
        self.session=self.new_session()
        self.assertEqual(self.session.registry.budget(),budget)
        self.assertEqual(self.session.state()['issue_workbench']['executions'][0]['id'],execution['id'])
        self.assertFalse(self.session.active)
        self.assertEqual(len(self.model.requests),2)

        # HTTP -> original sealed same-environment checks -> local branch ->
        # actual local-bare push -> simulated Draft PR/default-branch/close.
        review=self.execute('issue_prepare_local',{'execution_id':execution['id']})
        self.assertEqual(review['status'],'prepared',review)
        local=self.execute('issue_publish_local',{'execution_id':execution['id'],'revision':review['review_sha256']})
        self.assertEqual(local['status'],'published',local)
        from test_issue_delivery import FakePulls
        from test_issue_publication import LocalBranchTransport, prepare_bare
        from test_issue_closure import AcceptanceRemote
        from types import SimpleNamespace
        remote=FakePulls(self.github,review['review']['target_branch'],review['review']['commit'])
        bare=self.root/'remote.git'
        prepare_bare(self.repo,bare)
        controller=self.session.issue_workbench
        controller.remote=remote
        controller.transport=LocalBranchTransport(bare)
        native={str(p):p.read_bytes() for p in (self.root/'state/task-sessions').rglob('*.jsonl')}
        self.assertTrue(native)
        for preview,action,expected in [('issue_push_preview','issue_push','remote-ref-verified'),
                                        ('issue_pr_preview','issue_pr','awaiting-review')]:
            proposal=self.execute(preview,{'execution_id':execution['id']})
            result=self.execute(action,{'execution_id':execution['id'],'revision':proposal['challenge'],
                'approval_sha256':proposal['approval_sha256']})
            self.assertEqual(result['status'],expected,result)
        self.assertEqual(self.github.issue['state'],'open')
        case=SimpleNamespace(repo=self.repo,root=self.root,commit=review['review']['commit'],
            github=self.github,remote=remote,addCleanup=self.addCleanup)
        acceptance=AcceptanceRemote(case)
        controller.remote=acceptance
        blocked=self.wait(self.post('issue_close_preview',{'execution_id':execution['id']}))
        self.assertEqual(blocked['status'],'failed','Unmerged PR cannot close an Issue')
        self.assertEqual(self.github.issue['state'],'open')
        acceptance.merge()
        proposal=self.execute('issue_close_preview',{'execution_id':execution['id']})
        self.assertEqual(self.github.issue['state'],'open','Read-only acceptance is not close authority')
        closed=self.execute('issue_close',{'execution_id':execution['id'],'revision':proposal['challenge'],
            'approval_sha256':proposal['approval_sha256']})
        self.assertEqual(closed['status'],'closed-verified',closed)
        self.assertEqual(self.session.registry.load('issue_execution')[execution['id']]['status'],'delivered')
        self.assertEqual(self.session.registry.budget(),budget)
        self.assertEqual(len(self.model.requests),2)
        self.assertEqual({str(p):p.read_bytes() for p in (self.root/'state/task-sessions').rglob('*.jsonl')},native)

    def test_pause_waiting_approval_does_not_need_idle_queue_and_keeps_native(self):
        p=self.prepare()
        job=self.post('issue_execute',{'investigation_id':p['id'],'revision':p['revision']})
        approval=self.pending()
        execution=self.session.issue_workbench.view()['executions'][0]
        result=self.post('issue_pause',{'execution_id':execution['id'],'revision':execution['challenge']})
        self.assertTrue(result['pause_requested'])
        done=self.wait(job)
        self.assertEqual(done['result']['status'],'paused',done)
        self.assertEqual(self.session.registry.budget()['requests_used'],2)
        self.assertEqual(self.session.registry.budget()['requests_reserved_unknown'],0)
        self.assertEqual(self.session.registry.load('issue_approval')[approval['id']]['status'],'interrupted')
        self.assertTrue(self.session.registry.load('pi_task'))
        with self.assertRaises(SafetyError):
            self.session.issue_workbench.immediate('issue_decide',{'approval_id':approval['id'],
                'revision':approval['revision'],'decision':'approve'})

    def test_remote_edit_during_approval_rejects_click_without_edit_or_second_request(self):
        p=self.prepare()
        job=self.post('issue_execute',{'investigation_id':p['id'],'revision':p['revision']})
        approval=self.pending()
        self.github.issue['updated_at']='2026-10-21T13:00:00Z'
        r=self.client.post('/api/actions/issue_decide',headers=self.headers,json={'request_id':uuid.uuid4().hex,
            'approval_id':approval['id'],'revision':approval['revision'],'decision':'approve'})
        self.assertEqual(r.status_code,409,r.text)
        row=self.session.issue_workbench.view()['executions'][0]
        self.post('issue_pause',{'execution_id':row['id'],'revision':row['challenge']})
        self.assertEqual(self.wait(job)['result']['status'],'paused')
        self.assertEqual(len(self.model.requests),2)



if __name__=='__main__':unittest.main()
