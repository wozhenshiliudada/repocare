"""Combined core-six and usable-delivery contracts; no provider/model requests."""
import copy
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient
from repo_maintainer.models import SafetyError
from repo_maintainer.provider_config import validate_provider_config,load_provider_config
from repo_maintainer.issue_workbench import validate_action
from repo_maintainer.issue_validation import service as validation_service
from repo_maintainer.issue_followup import IssueFollowup
from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.demo import create_fixture
from repo_maintainer.repository_workbench import RepositoryWorkbench,create_workbench_app
from test_issue_comment_continuity import CommentRemote
from test_repository_workbench import FakeGitHub,FakeServer
import test_issue_workbench as issue_fixture
import test_ci_cross_source_correlation as ci_fixture
import test_issue_comment_continuity as continuity_fixture


class MergeBackendContracts(unittest.TestCase):
    def test_cli_aliases_keep_original_request_ceiling_default(self):
        from repo_maintainer.cli import main
        from repo_maintainer.runtime_limits import MAX_REQUESTS
        for command in ('web','workbench'):
            with self.subTest(command=command),\
                 patch('repo_maintainer.repository_workbench.RepositoryWorkbench') as hub,\
                 patch('repo_maintainer.repository_workbench.serve_workbench') as serve:
                self.assertEqual(0,main([command]))
                self.assertEqual(MAX_REQUESTS,hub.call_args.kwargs['request_budget'])
                self.assertIsNone(hub.call_args.args[1]);serve.assert_called_once()

    def test_managed_provider_readiness_tools_and_original_diagnostic_routes_coexist(self):
        with ExitStack() as stack:
            directory=stack.enter_context(tempfile.TemporaryDirectory())
            hub=RepositoryWorkbench(Path(directory)/'state',request_budget=7,ui='legacy',
                github=FakeGitHub(),server_factory=FakeServer)
            stack.callback(hub.close)
            with TestClient(create_workbench_app(hub,12345),base_url='http://127.0.0.1:12345') as client:
                headers={'Authorization':'Bearer '+hub.token}
                self.assertEqual([],hub.state()['correlation']['links'])
                current=client.get('/api/provider-config',headers=headers).json()
                secret='synthetic-merge-only'
                saved=client.post('/api/provider-config',headers=headers,json={
                    'revision':current['revision'],'api_key':secret,
                    'base_url':'https://provider.example.test/v1','model':'merge-test'})
                self.assertEqual(200,saved.status_code,saved.text)
                readiness=client.get('/api/readiness',headers=headers)
                self.assertEqual(200,readiness.status_code,readiness.text)
                provider=next(c for c in readiness.json()['checks'] if c['group']=='provider')
                self.assertEqual('ready',provider['status'])
                for route in ('/api/capabilities','/api/delivery','/api/state','/api/service','/api/provider-config'):
                    result=client.get(route,headers=headers)
                    self.assertEqual(200,result.status_code,(route,result.text))
                    self.assertNotIn(secret,result.text)
                self.assertEqual({},hub.jobs);self.assertEqual({},hub.workspaces)
                self.assertEqual(secret,load_provider_config(hub.provider_config)['PI_MAINTENANCE_API_KEY'])

    def test_shared_provider_validation_keeps_browser_fields_and_strict_endpoint_ports(self):
        value={'PI_MAINTENANCE_API_KEY':'synthetic-merge-only',
               'PI_MAINTENANCE_BASE_URL':'https://provider.example.test/v1','PI_MAINTENANCE_MODEL':'merge'}
        self.assertEqual(value,validate_provider_config(value.copy()))
        for url in ('https://provider.example.test:0','https://provider.example.test:invalid',
                    'https://provider.example.test:65536','https://[broken-ipv6'):
            with self.subTest(url=url),self.assertRaises(SafetyError):
                validate_provider_config({**value,'PI_MAINTENANCE_BASE_URL':url})
        for name in value:
            with self.subTest(field=name),self.assertRaises(SafetyError):
                validate_provider_config({**value,name:'bad\x7fvalue'})

    def test_issue_review_acceptance_and_explicit_progress_actor_fields_coexist(self):
        base={'request_id':'a'*32,'investigation_id':'b'*64,'revision':'c'*64}
        self.assertEqual('accept',validate_action('issue_validation_review',
            {**base,'decision':'accept'})['decision'])
        progress={'request_id':'a'*32,'investigation_id':'b'*64,'stage':'accepted'}
        self.assertEqual('accepted',validate_action('issue_progress_investigation_preview',progress)['stage'])
        send={'request_id':'a'*32,'investigation_id':'b'*64,'progress_id':'c'*64,
              'approval_sha256':'d'*64,'expected_actor_id':42}
        self.assertEqual(42,validate_action('issue_progress_investigation_send',send)['expected_actor_id'])
        with self.assertRaises(SafetyError):
            validate_action('issue_progress_investigation_send',{**send,'expected_actor_id':True})

    def test_verified_own_progress_comment_does_not_stale_original_clarification_preview(self):
        f=issue_fixture.IssueWorkbenchTests();f.setUp();self.addCleanup(f.doCleanups)
        f.configure();job=f.execute('issue_investigate',{'number':7})
        w=f.session.issue_workbench;root=validation_service(w,job['id'],root=True)
        parent={'id':root.identity,'source':root.source,'status':'clarification_needed',
            'runtime_authorized':False,'contract':None,'task_id':None,'preview_revision':'e'*64,
            'proposal':{'requirement':'Clarify add behavior','rationale':'fixture','limitations':'expected result?',
                        'clarification':True,'cases':[]}}
        root.registry.put('validation_proposal',root.identity,parent)
        budget=root.registry.budget();w.comment_remote=remote=CommentRemote(f.github)
        preview=f.execute('issue_progress_investigation_preview',{'investigation_id':job['id'],'stage':'accepted'})
        f.execute('issue_progress_investigation_send',{'investigation_id':job['id'],
            'progress_id':preview['id'],'approval_sha256':preview['approval_sha256'],'expected_actor_id':42})
        self.assertEqual(1,remote.posts)
        clarified=root.clarification_preview(root.revision(parent),'add(2,3) must return 5')
        self.assertIn('revision',clarified)
        self.assertEqual(parent,root.registry.load('validation_proposal')[root.identity])
        self.assertEqual(budget,root.registry.budget());self.assertEqual([],f.model.requests)
        f.github.issue['body']+=' Human changed the requirement'
        with self.assertRaises(SafetyError):
            root.clarification_preview(root.revision(parent),'add(2,3) must return 5')
        self.assertEqual(budget,root.registry.budget());self.assertEqual([],f.model.requests)

    def test_original_root_ci_preparation_uses_workbench_historical_baseline_reader(self):
        f=ci_fixture.CiCrossSourceCorrelationTests();f.setUp();self.addCleanup(f.doCleanups)
        observed=[]
        def baseline(packet):
            observed.append(copy.deepcopy(packet))
            return {'status':'unconfirmed','message':'Original hub reader was invoked',
                    'baseline':None,'diff':None,'rejections':[]}
        f.hub.ci_baseline=baseline
        _prepared,mid,event,pin=f.prepare_sources()
        self.assertEqual(1,len(observed),'Modern root preparation bypassed the existing hub baseline reader')
        diagnosis=f.hub.monitor.registries[mid].load('event')[event['id']]['root_preparation']['ci_diagnosis']
        self.assertEqual('Original hub reader was invoked',diagnosis['comparable_baseline']['message'])
        self.assertEqual(pin['head_sha'],observed[0]['run']['head_sha'])
        self.assertEqual([],f.model.requests)

    def test_verified_own_comment_is_not_a_new_issue_version_successor(self):
        f=continuity_fixture.ContinuityTests();f.setUp();self.addCleanup(f.doCleanups)
        repo=RepositoryGateway(create_fixture(f.root/'source'))
        f.github.head=repo.head;f.original=f.reader().snapshot(7)
        key=fingerprint([f.original['repository_id'],7]);parent_id='a'*32
        parent={'id':parent_id,'issue_key':key,'issue_number':7,'repository_id':f.original['repository_id'],
                'status':'awaiting-review','issue_token':f.original['token']}
        f.registry.put('issue_execution_head',key,{'execution_id':parent_id})
        f.registry.put('issue_head',key,{'token':parent['issue_token']})
        f.registry.put('issue_execution',parent_id,parent)
        f.post()
        self.assertNotEqual(f.original['token'],f.reader().raw_snapshot(7)['token'])
        before=f.registry.budget()
        with self.assertRaisesRegex(SafetyError,'token 未变化'):
            IssueFollowup(f.registry,repo,f.github,f.spec).preview(7)
        self.assertEqual(parent,f.registry.load('issue_execution')[parent_id])
        self.assertEqual({},f.registry.load('issue_successor'))
        self.assertEqual(before,f.registry.budget());self.assertEqual(1,f.remote.posts)


if __name__=='__main__':unittest.main(verbosity=2)
