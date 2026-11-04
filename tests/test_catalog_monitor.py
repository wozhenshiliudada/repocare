"""Actual shared-monitor ledger -> controlled commit route -> new approval, loopback model."""
import tempfile
import unittest
from pathlib import Path
from repo_maintainer.demo import create_fixture,DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.repository_workbench import RepositoryWorkbench,inspect_repository
from test_repository_workbench import FakeServer,FakeGitHub
from test_task_planning import SyntheticModel


class CatalogMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.addCleanup(self.temp.cleanup)
        self.repo=create_fixture(self.root/'source');repo=RepositoryGateway(self.repo)
        repo.command('config','user.name','Catalog Fixture');repo.command('config','user.email','fixture@example.invalid')
        self.model=SyntheticModel();self.addCleanup(self.model.close)
        self.config=self.root/'synthetic-config';self.config.write_text('not read')
        self.hub=self.new_hub();self.addCleanup(lambda:self.hub.close())

    def new_hub(self):
        hub=RepositoryWorkbench(self.root/'hub',self.config,request_budget=50,github=FakeGitHub(),
            provider_factory=lambda p:PiProvider(p.allowed_paths,self.model.url,p.readonly_paths,max_requests=50),server_factory=FakeServer)
        hub.monitor.stop();return hub

    def worker(self,fn,*args):return self.hub.executor.submit(fn,*args).result(timeout=60)

    def start(self):
        repo=inspect_repository(self.repo)
        identity=self.worker(self.hub.monitor.start,{'path':str(self.repo),'revision':repo['revision'],'allowed_paths':['calculator.py'],
            'readonly_paths':['tests/test_calculator.py'],'allowed_tests':[list(DEMO_TEST)],'timeout_seconds':30,'confirmed':True,
            'branch':repo['branch'],'github_url':'','interval_seconds':60})['monitor_id']
        self.worker(self.hub.monitor.poll,identity);event=self.hub.monitor.view()[0]['events'][-1]
        return identity,self.hub.monitor.servers[identity,event['id']].session

    def test_actual_commit_inspection_uses_owner_budget_and_does_not_invent_native_task(self):
        identity,session=self.start();plan=next(iter(session.plans.values()))['view'];e=plan['planning_evidence'];load=e['memory_load']
        self.assertEqual('commit_inspection',load['task_type']);self.assertIsNone(load['task_id'])
        self.assertEqual(e['attempt_id'],load['invocation_id'])
        self.assertEqual(load,self.hub.monitor.registries[identity].load('attempt')[e['attempt_id']]['evidence']['memory_load'])
        self.assertFalse((session.runs_dir/'task-sessions').exists());count=len(self.model.requests)
        result=self.worker(session._operate,'decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'approve'})
        self.assertEqual('candidate_ready',result['workflow_status']);self.assertEqual(count,len(self.model.requests))
        self.assertEqual(count,self.hub.monitor.registries[identity].budget()['requests_used'])
        self.hub.close();self.hub=self.new_hub();self.assertEqual(count,len(self.model.requests))
        self.assertEqual(count,self.hub.monitor.registries[identity].budget()['requests_used'])

    def test_new_commit_still_invalidates_old_route_approval_without_extra_model_request(self):
        identity,session=self.start();plan=next(iter(session.plans.values()))['view'];count=len(self.model.requests)
        budget=self.hub.monitor.registries[identity].budget();RepositoryGateway(self.repo).command('commit','--allow-empty','-m','new observed head')
        with self.assertRaises(SafetyError):self.worker(session._operate,'decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'approve'})
        self.assertEqual(count,len(self.model.requests));self.assertEqual(budget,self.hub.monitor.registries[identity].budget())
