"""Read views must not acquire repair authority or mutate source Git state."""
import json
import os
import re
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from starlette.testclient import TestClient
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError
from repo_maintainer.provider import MockProvider
from repo_maintainer.repository_workbench import RepositoryWorkbench, create_workbench_app, inspect_repository
from repo_maintainer.workbench_read import GitHubBrowser, graph_records


class WorkbenchReadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=create_fixture(self.root/'repo')
        self.config=self.root/'config';self.config.write_text('not a credential')
        self.hub=RepositoryWorkbench(self.root/'state',self.config,provider_factory=lambda _:MockProvider(),ui='react')
        self.addCleanup(self.hub.close)
        self.client=TestClient(create_workbench_app(self.hub,12345),base_url='http://127.0.0.1:12345')
        self.addCleanup(self.client.close);self.headers={'Authorization':'Bearer '+self.hub.token}
        preview=inspect_repository(self.repo)
        response=self.client.post('/api/actions/connect',headers=self.headers,json={
            'request_id':uuid.uuid4().hex,'path':str(self.repo),'revision':preview['revision'],
            'allowed_paths':['calculator.py'],'readonly_paths':['tests/test_calculator.py'],
            'allowed_tests':[list(DEMO_TEST)],'timeout_seconds':30,'allow_auto':False,'confirmed':True})
        self.assertEqual(response.status_code,202,response.text)
        self.wait(lambda: self.hub.jobs[response.json()['id']]['status']!='running')
        self.record=self.hub.state()['workspaces'][0]

    def wait(self,predicate):
        deadline=time.monotonic()+30
        while not predicate():
            if time.monotonic()>deadline:self.fail('bounded worker did not finish')
            time.sleep(.02)

    def get(self,view,query=''):
        return self.client.get('/api/browse/'+self.record['id']+'/'+view+query,headers=self.headers)

    def test_react_assets_are_explicit_self_only_allowlist_and_legacy_available(self):
        response=self.client.get('/');self.assertEqual(response.status_code,200)
        self.assertIn('RepoCare',response.text)
        references=re.findall(r'(?:src|href)="(/assets/[^"<>]+)"',response.text)
        self.assertGreaterEqual(len(references),2)
        for path in references:self.assertEqual(self.client.get(path).status_code,200)
        for path in ['/assets/../../pyproject.toml','/assets/secret.json','/assets/source.js.map']:
            self.assertEqual(self.client.get(path).status_code,404)
        self.assertEqual(self.client.get('/legacy').status_code,200)
        self.assertIn("script-src 'self'",response.headers['content-security-policy'])
        self.assertNotIn('unsafe-inline',response.headers['content-security-policy'])

    def test_tree_file_graph_are_read_only_and_keep_original_authority_boundary(self):
        repository=RepositoryGateway(self.repo);before=repository.snapshot()
        tree=self.get('tree');self.assertEqual(tree.status_code,200,tree.text)
        self.assertIn('calculator.py',tree.json()['files'])
        file=self.get('file','?path=calculator.py');self.assertEqual(file.status_code,200,file.text)
        self.assertIn('return',file.json()['content'])
        graph=self.get('graph');self.assertEqual(graph.status_code,200,graph.text)
        self.assertEqual(graph.json()['commits'][0]['sha'],repository.head)
        self.assertEqual(before,repository.snapshot())
        self.assertIsNone(self.hub.active)
        self.assertFalse(self.hub.jobs[next(iter(self.hub.jobs))]['result'].get('plan_id'))
        path='/api/browse/'+self.record['id']+'/tree'
        self.assertEqual(self.client.get(path).status_code,401)
        self.assertEqual(self.client.get(path,headers={'Authorization':'Bearer '+self.record['token']}).status_code,401)
        self.assertEqual(self.client.get(path,headers={**self.headers,'Origin':'https://other.invalid'}).status_code,403)

    def test_paths_secrets_untracked_binary_large_and_duplicate_query_are_rejected(self):
        from repo_maintainer.maintenance_limits import LIMITS
        for name,content in [('credentials.json',b'secret'),('binary.dat',b'a\0b'),('large.txt',b'x'*(LIMITS['source_file_max_bytes']+1))]:
            (self.repo/name).write_bytes(content)
            RepositoryGateway(self.repo).command('add','--',name)
        (self.repo/'untracked.txt').write_text('not tracked')
        for name in ['../config','credentials.json','binary.dat','large.txt','untracked.txt']:
            self.assertIn(self.get('file','?path='+name).status_code,[404,415])
        self.assertEqual(self.get('file','?path=calculator.py&path=../config').status_code,400)
        self.assertEqual(self.get('unexpected').status_code,404)

    def test_history_request_is_explicit_bounded_idempotent_and_cache_is_separate(self):
        workspace=self.hub.workspaces[self.record['id']]
        workspace.github_url='https://github.com/example/repo.git'
        repository=RepositoryGateway(self.repo);before=repository.snapshot()
        original=self.hub.history.command
        def local_transport(job,directory,*args,**kwargs):
            # Only test transport changes. Production still refuses local/file remotes.
            if 'fetch' in args:
                args=tuple(str(self.repo) if a==workspace.github_url else a for a in args)
                args=('-c','protocol.file.allow=always',*args)
            return original(job,directory,*args,**kwargs)
        branch=repository.command('symbolic-ref','--short','HEAD').decode().strip()
        value={'request_id':uuid.uuid4().hex,'workspace_id':workspace.identity,'branches':[branch],'confirmed':True}
        with patch.object(self.hub.github,'branch_head',return_value=repository.head),patch.object(self.hub.history,'command',side_effect=local_transport):
            response=self.client.post('/api/history',headers=self.headers,json=value)
            self.assertEqual(response.status_code,202,response.text)
            self.wait(lambda:self.hub.history.jobs[value['request_id']]['status']!='running')
        job=self.client.get('/api/history/'+value['request_id'],headers=self.headers).json()
        self.assertEqual(job['status'],'finished',job)
        self.assertTrue(job['result']['cached']);self.assertEqual(before,repository.snapshot())
        self.assertEqual(self.client.post('/api/history',headers=self.headers,json=value).json()['id'],value['request_id'])
        value['branches']=['another'];self.assertEqual(self.client.post('/api/history',headers=self.headers,json=value).status_code,409)
        for bad in [[],['a']*9,['../bad'],['a','a']]:
            value.update(request_id=uuid.uuid4().hex,branches=bad)
            self.assertIn(self.client.post('/api/history',headers=self.headers,json=value).status_code,[400,409])
        self.assertEqual(before,repository.snapshot())

    def test_interrupted_history_is_read_only_and_old_request_cannot_replay(self):
        workspace=self.hub.workspaces[self.record['id']]
        workspace.github_url='https://github.com/example/repo.git'
        identity=uuid.uuid4().hex;directory=self.hub.history.root/identity
        directory.mkdir(parents=True)
        state={'id':identity,'signature':'a'*64,'status':'running','phase':'fetching','result':None}
        path=directory/'state.json';path.write_text(json.dumps(state),encoding='utf-8');before=path.read_bytes()
        with patch.object(self.hub.history.executor,'submit',side_effect=AssertionError('No replay')):
            response=self.client.get('/api/history/'+identity,headers=self.headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['status'],'interrupted')
            value={'request_id':identity,'workspace_id':workspace.identity,'branches':['main'],'confirmed':True}
            response=self.client.post('/api/history',headers=self.headers,json=value)
            self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(before,path.read_bytes())

    def test_remote_head_drift_keeps_failure_cache_without_retry_or_source_change(self):
        workspace=self.hub.workspaces[self.record['id']]
        workspace.github_url='https://github.com/example/repo.git'
        repository=RepositoryGateway(self.repo);before=repository.snapshot();commands=[]
        def changed_remote(job,directory,*args,**kwargs):
            commands.append(args)
            if args[0]=='init':Path(args[-1]).mkdir()
            return (repository.head+'\n').encode() if 'rev-parse' in args else b''
        identity=uuid.uuid4().hex
        value={'request_id':identity,'workspace_id':workspace.identity,'branches':['main'],'confirmed':True}
        with patch.object(self.hub.github,'branch_head',return_value='0'*40),patch.object(self.hub.history,'command',side_effect=changed_remote):
            response=self.client.post('/api/history',headers=self.headers,json=value)
            self.assertEqual(response.status_code,202,response.text)
            self.wait(lambda:self.hub.history.jobs[identity]['status']!='running')
            result=self.client.get('/api/history/'+identity,headers=self.headers).json()
            self.assertEqual(result['status'],'failed',result)
            self.assertIn('变化',result['error'])
            self.client.post('/api/history',headers=self.headers,json=value)
            self.assertEqual(1,sum('fetch' in args for args in commands))
        self.assertTrue((self.hub.history.root/identity/'objects.git').is_dir())
        self.assertTrue((self.hub.history.root/identity/'intent.json').is_file())
        self.assertEqual(before,repository.snapshot())

    def test_read_pool_capacity_does_not_block_maintenance_state(self):
        self.hub.read_pool.slots.acquire();self.hub.read_pool.slots.acquire()
        try:
            self.assertEqual(self.get('graph').status_code,429)
            response=self.client.get('/api/state',headers=self.headers)
            self.assertEqual(response.status_code,200)
            self.assertIsNone(response.json()['active'])
        finally:
            self.hub.read_pool.slots.release();self.hub.read_pool.slots.release()


class GitHubProjectionTests(unittest.TestCase):
    def test_empty_issue_page_after_pr_filter_keeps_link_pagination(self):
        browser=GitHubBrowser(None)
        with patch.object(browser,'get',return_value=([{'id':1,'pull_request':{},'html_url':'https://github.com/o/r/pull/1'}],True)):
            result=browser.view('issues','https://github.com/o/r',1)
        self.assertEqual(result['items'],[]);self.assertTrue(result['has_next'])

    def test_links_and_unknown_operations_cannot_escape_fixed_host(self):
        browser=GitHubBrowser(None)
        with patch.object(browser,'get',return_value=([{'id':1,'html_url':'https://evil.invalid/o/r/issues/1'}],False)):
            self.assertEqual(browser.view('issues','https://github.com/o/r')['items'],[])
        with self.assertRaises(SafetyError):browser.view('delete','https://github.com/o/r')
        with self.assertRaises(SafetyError):browser.view('issues','https://evil.invalid/o/r')

    def test_commit_projection_preserves_topological_order_and_missing_parent(self):
        a,b='a'*40,'b'*40
        rows=graph_records((a+'\0'+b+'\0'+'1\0child\0'+b+'\0'+'c'*40+'\0'+'99\0parent\0').encode())
        self.assertEqual([r['sha'] for r in rows],[a,b]);self.assertEqual(rows[1]['missing_parents'],['c'*40])
