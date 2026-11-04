"""Commit+typed CI+Issue: original controllers, separate ledgers, one repair owner.

The GitHub transport is synthetic. CI bindings are made only by the original
reader/investigation/preparation/context, never assigned by this fixture.
"""
import copy
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from repo_maintainer.ci_evidence import CiEvidenceReader as RealReader
from repo_maintainer.ci_transport import CiJsonPage
from repo_maintainer.demo import DEMO_TEST
from repo_maintainer.github_connection import GitHubConnection, fetch_snapshot
from repo_maintainer.models import digest
from repo_maintainer.repair_comparison import _environment_semantics, _grant_semantics
from repo_maintainer.repository_workbench import inspect_repository
from repo_maintainer.maintenance_correlation import CLAIMS
from repo_maintainer.workbench_correlation import PAIRS
from test_ci_evidence import FixtureTransport, HEAD
import test_workbench_correlation as fixtures


class CiCrossSourceCorrelationTests(unittest.TestCase):
    setUp=fixtures.WorkbenchCorrelationTests.setUp
    start_hub=fixtures.WorkbenchCorrelationTests.start_hub
    finish=fixtures.WorkbenchCorrelationTests.finish
    wait=fixtures.WorkbenchCorrelationTests.wait
    job=fixtures.WorkbenchCorrelationTests.job
    execute=fixtures.WorkbenchCorrelationTests.execute
    selector=fixtures.WorkbenchCorrelationTests.selector
    native=fixtures.WorkbenchCorrelationTests.native
    candidates=fixtures.WorkbenchCorrelationTests.candidates
    record=fixtures.WorkbenchCorrelationTests.record

    def save(self,name,value):
        target=os.environ.get('R3_CI_EVIDENCE_DIR')
        if target:
            root=Path(target);root.mkdir(parents=True,exist_ok=True)
            (root/(self._testMethodName+'-'+name+'.json')).write_text(
                json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

    def _ci_fixture(self):
        head=self.repo.head
        self.rows=[{'id':101,'run_attempt':1,'workflow_id':301,'head_sha':head,
            'head_branch':'main','status':'completed','conclusion':'failure',
            'repository':{'id':123,'full_name':'example/project'},
            'name':'tests/test_calculator.py add regression'}]
        original_api=self.github.api
        def api(path):
            if '/actions/runs?branch=' in path:
                self.github.calls.append(path)
                return {'workflow_runs':copy.deepcopy(self.rows)}
            return original_api(path)
        self.github.api=api
        self.github.workflow_runs=lambda *args:GitHubConnection.workflow_runs(self.github,*args)
        self.github.branch_head=lambda *args:self.repo.head
        self.github.snapshot=lambda url,branch,sha,dest:fetch_snapshot(self.repo.root,branch,sha,dest)
        self.transport=FixtureTransport()
        self.transport.policy=SimpleNamespace(hosts=('results-receiver.actions.githubusercontent.com',))
        pages={}
        for path,page in self.transport.pages.items():
            value=json.loads(json.dumps(page.data).replace(HEAD,head).replace('example/repo','example/project'))
            if path=='/repos/example/repo':value['id']=123
            if isinstance(value,dict) and 'repository' in value:value['repository']['id']=123
            jobs=value.get('jobs',[]) if isinstance(value,dict) else []
            if isinstance(value,dict) and 'steps' in value:jobs=[value]
            for job in jobs:
                job['name']='tests/test_calculator.py add regression'
                job['steps']=[{'number':1,'name':'tests/test_calculator.py add regression',
                    'status':'completed','conclusion':'failure'}]
            pages[path.replace('example/repo','example/project')]=CiJsonPage(value,page.has_next)
        self.transport.pages=pages
        self.transport.logs={'/repos/example/project/actions/jobs/201/logs':
            b'FAIL tests/test_calculator.py add positive operands\nAssertionError: add(2,3) expected 5, got -1\n'}

    @contextmanager
    def ci_transport(self):
        with patch('repo_maintainer.ci_http.CiHttpTransport',return_value=self.transport),\
             patch('repo_maintainer.ci_evidence.CiEvidenceReader',
                   side_effect=lambda transport,**kwargs:RealReader(transport,synthetic=True)):
            yield

    def prepare_sources(self):
        self.repo.command('-c','core.longpaths=true','config','user.name','Synthetic CI Correlation')
        self.repo.command('-c','core.longpaths=true','config','user.email','ci-correlation@example.invalid')
        self.github.head=self.repo.head
        source=inspect_repository(self.repo.root)
        self.execute('connect',{'path':str(self.repo.root),'revision':source['revision'],
            'allowed_paths':['calculator.py'],'readonly_paths':['tests/test_calculator.py'],
            'allowed_tests':[list(DEMO_TEST)],'timeout_seconds':120,'allow_auto':True,'confirmed':True})
        self.workspace=self.client.get('/api/state',headers=self.headers).json()['workspaces'][0]
        self.session=self.hub.workspaces[self.workspace['id']].session
        self.registry=self.session.registry
        configured=self.execute('issue_config_preview',{'read_roots':['.'],'write_roots':['.'],
            'trusted_actors':['maintainer'],'mode':'auto','allow_install':False,'allow_network':False,
            'workflow_ids':[301]},workspace=self.workspace)
        self.execute('issue_configure',{'config':configured['configuration'],'revision':configured['revision']},workspace=self.workspace)
        issue=self.execute('issue_investigate',{'number':7},workspace=self.workspace)
        prepared=self.execute('issue_environment',{'investigation_id':issue['id']},workspace=self.workspace)
        self.assertIn('environment',prepared)
        self._ci_fixture()
        payload={'path':str(self.repo.root),'revision':source['revision'],'branch':source['branch'],
            'github_url':'https://github.com/example/project.git','interval_seconds':60,'timeout_seconds':120,
            'read_roots':['.'],'write_roots':['.'],'check_families':['unittest'],'mode':'auto',
            'allow_local_code':True,'allow_install':False,'allow_network':False}
        preview=self.execute('monitor_rules_preview',payload)
        mid=self.execute('monitor_rules_configure',{**preview,'confirmed':True})['monitor_id']
        with self.ci_transport():self.hub.executor.submit(self.hub.monitor.poll,mid).result(timeout=180)
        registry=self.hub.monitor.registries[mid]
        events=registry.load('event');self.assertEqual(1,len(events))
        event=next(iter(events.values()))
        self.assertEqual(digest(self.repo.head.encode())[:16],event['id'])
        self.assertEqual(event['sha'],self.repo.head)
        self.assertEqual('reproduced',event['root_preparation']['status'],event)
        context=self.hub.monitor.execution.context(mid,event['id'])
        pin=context[4]['ci_failure']
        self.assertEqual([123,101,1,201],[pin[k] for k in ('repository_id','run_id','attempt','job_id')])
        self.assertEqual('example/project',pin['repository']);self.assertEqual(self.repo.head,pin['head_sha'])
        self.assertTrue(pin['synthetic']);self.assertEqual(pin,event['root_preparation']['ci_failure_binding'])
        self.assertTrue(any(path.endswith('/actions/jobs/201/logs') for path in self.transport.calls))
        self.assertEqual([],self.model.requests)
        selectors={'left':self.selector(prepared),'right':{'kind':'monitor','monitor_id':mid,'event_id':event['id']}}
        comparison=self.execute('maintenance_link_preview',selectors)
        a,b,_,_=self.hub.correlation._pair(selectors['left'],selectors['right'])
        self.save('comparison',{'preview':comparison,'pin':pin,'ci_calls':self.transport.calls,
            'contexts':[{'origin':c['origin'],'environment':_environment_semantics(c['environment']),
                'grant':_grant_semantics(c['grant']),'policy':asdict(c['policy']),
                'model_identity':c['model_identity'],'max_attempts':c['max_attempts']} for c in (a,b)],
            'paid_requests':0,'model_requests':len(self.model.requests)})
        self.assertTrue(comparison['preview']['comparison']['comparable'],comparison['preview']['comparison'])
        self.assertNotEqual(self.registry.path,registry.path)
        before=[self.registry.budget(),registry.budget()]
        self.assertEqual('linked',self.execute('maintenance_link_confirm',{**comparison,'confirmed':True})['status'])
        self.assertEqual(before,[self.registry.budget(),registry.budget()])
        self.assertEqual([],self.model.requests)
        self.assertEqual({},registry.load(CLAIMS));self.assertEqual({},self.registry.load('issue_execution'))
        return prepared,mid,event,pin

    def test_typed_ci_commit_issue_preparation_is_comparable_without_model(self):
        prepared,mid,event,pin=self.prepare_sources()
        registry=self.hub.monitor.registries[mid]
        self.assertEqual(1,len(registry.load(PAIRS)))
        self.assertEqual({},registry.load('pi_task'));self.assertEqual({},self.registry.load('pi_task'))
        self.save('prepared',{'typed_ci_pin':pin,'event_count':len(registry.load('event')),
            'issue_investigation':prepared['id'],'monitor_id':mid,'event_id':event['id'],
            'issue_budget':self.registry.budget(),'monitor_budget':registry.budget(),'model_requests':0})

    def test_original_ci_monitor_repair_owner_prevents_issue_second_dispatch(self):
        prepared,mid,event,pin=self.prepare_sources()
        registry=self.hub.monitor.registries[mid]
        location={'monitor_id':mid,'event_id':event['id']}
        preview=self.execute('monitor_repair_preview',{**location,'max_attempts':3})
        denied=self.job('monitor_repair_start',{**preview,'confirmed':False})
        self.assertEqual('failed',denied['status']);self.assertEqual([],self.model.requests)
        self.assertEqual({},registry.load(CLAIMS));self.assertEqual({},registry.load('pi_task'))
        first=self.execute('monitor_repair_start',{**preview,'confirmed':True})
        self.assertEqual('candidate_ready',first['status'],first)
        self.assertEqual(2,len(self.model.requests));self.assertEqual(1,len(registry.load('pi_task')))
        current=registry.load('event')[event['id']]
        self.assertEqual(pin,current['root_execution']['binding']['ci_failure'])
        self.assertTrue(current['root_execution']['ci_result']['local_candidate_verified'])
        self.assertFalse(current['root_execution']['ci_result']['remote_ci_verified'])
        claims=copy.deepcopy(registry.load(CLAIMS));self.assertEqual(1,len(claims))
        budgets=[self.registry.budget(),registry.budget()];native=self.native();reports=self.candidates()
        self.assertTrue(native)
        candidates=[p for p in self.root.rglob('run.json')
                    if json.loads(p.read_text(encoding='utf-8')).get('status')=='candidate_ready']
        self.assertEqual(1,len(candidates))
        second=self.execute('issue_execute',{'investigation_id':prepared['id'],'revision':prepared['revision']},workspace=self.workspace)
        self.assertEqual('related_no_dispatch',second['status'],second)
        self.assertEqual(2,len(self.model.requests));self.assertEqual(budgets,[self.registry.budget(),registry.budget()])
        self.assertEqual(native,self.native());self.assertEqual(reports,self.candidates())
        self.assertEqual(claims,registry.load(CLAIMS));self.assertEqual({},self.registry.load('pi_task'))
        self.assertEqual(0,budgets[0]['requests_used']);self.assertEqual(2,budgets[1]['requests_used'])
        self.assertEqual(0,budgets[0]['requests_reserved_unknown']);self.assertEqual(0,budgets[1]['requests_reserved_unknown'])
        links=self.hub.correlation.view()['links'];self.assertEqual(1,len(links))
        self.assertEqual({'issue','monitor'},{s['kind'] for s in links[0]['sources']})
        self.assertEqual('monitor',links[0]['owner_source']['kind'])
        self.assertEqual(self.repo.command('-c','core.longpaths=true','status','--porcelain'),b'')
        self.save('one-owner',{'pin':pin,'owner':links[0]['owner_source'],'claim_count':len(claims),
            'model_requests':len(self.model.requests),'issue_extra_model_requests':0,'budgets':budgets,
            'native_sha256':{p:hashlib.sha256(raw).hexdigest() for p,raw in native.items()},
            'candidate_count':len(candidates),'candidate_run_paths':sorted(reports),
            'issue_result':{k:second[k] for k in ('id','status','correlation')},
            'monitor_result':{k:first[k] for k in ('monitor_id','event_id','status')},
            'link_confirmation_grants_repair':False,'original_auto_authority_preserved':True,
            'paid_provider_requests':0,'remote_ci_verified':False})


if __name__=='__main__':unittest.main(verbosity=2)
