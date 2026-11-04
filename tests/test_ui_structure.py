"""Fixed structural provenance with unchanged durable approvals and budgets."""
import contextlib
from dataclasses import asdict
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

from repo_maintainer.cli import main
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession, create_app
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.provider import MockProvider
from repo_maintainer.source_memory import canonical
from repo_maintainer import structure_index as structure
from repo_maintainer.workflow import cleanup_run
from starlette.testclient import TestClient
from source_protocol_fixture import source_protocol_only


class UIStructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = create_fixture(self.base / 'repository')
        subprocess.run(['git', '-C', str(self.repo), 'config', 'core.autocrlf', 'false'], check=True)
        # Re-use the selected tracked bytes; no model or extraction in binding tests.
        RepositoryGateway(self.repo).command('checkout', '--', 'calculator.py')
        self.policy = Policy(('calculator.py',), (DEMO_TEST,), readonly_paths=('tests/test_calculator.py',))
        self.index = self.base / 'structure-indexes' / 'sample'
        spec = self.base / 'spec.json'
        spec.write_bytes(canonical({'schema_version': 1, 'paths': ['calculator.py']}))
        def engine(snapshot, selected, output):
            output.write_bytes(canonical({'schema_version': 1, 'engine': structure.ENGINE, 'nodes': [
                dict(id='add', kind='function', name='add', qualified_name='add', path='calculator.py',
                     language='python', start_line=1, end_line=2)], 'edges': [], 'omitted_external_edges': 0}))
        with patch.object(structure, 'run_engine', side_effect=engine):
            structure.build_structure(spec, self.repo, self.index)
        self.session = None; self.addCleanup(self.finish)

    def provider(self):
        return PiProvider(self.policy.allowed_paths, 'http://127.0.0.1:12345/v1', self.policy.readonly_paths, 4)

    def open(self, enabled=True, index=None, runs=None):
        if self.session: self.session.close()
        self.session = UISession(self.repo, self.policy, self.provider(), runs or self.base / 'runs',
            allow_auto=True, structure_index=(index or self.index) if enabled else None)
        source_protocol_only(self.session)
        return self.session

    def finish(self):
        if self.session:
            self.session.close()
            for identity in self.session.runs:
                path = self.session.runs_dir / identity / 'run.json'
                if path.exists(): cleanup_run(path.parent)

    def generate(self):
        if not self.session: self.open()
        provider = self.session.provider.inner
        def fake(repo, issue, tests):
            provider.requests_used += 2; provider.evidence = {'requests': 2}
            return MockProvider().plan(repo, issue, tests)
        with patch.object(provider, 'plan', side_effect=fake):
            self.session._operate('plan', {'issue': 'addition'})
        return self.session.state()['plans'][0]

    def db(self): return (self.base / 'runs' / 'ui-state' / 'registry.sqlite3').read_bytes()

    def test_preview_only_provenance_zero_model_and_budget_and_no_private_index_path(self):
        self.open(); before = self.db(); source = RepositoryGateway(self.repo).snapshot()
        with patch.object(self.session.provider.inner, 'plan', side_effect=AssertionError('model')):
            result = self.session._operate('structure_preview', {'query': 'add'})['structure_preview']
        self.assertEqual('calculator.py', result['hits'][0]['path'])
        self.assertFalse(result['runtime_authorized']); self.assertTrue(result['structural_claims_only'])
        self.assertFalse(self.session.state()['structure_index']['model_context'])
        self.assertNotIn(str(self.index), json.dumps(self.session.state()))
        self.assertEqual([], self.session.state()['planning_attempts'])
        self.assertEqual(before, self.db()); self.assertEqual(source, RepositoryGateway(self.repo).snapshot())

    def test_manifest_whitespace_is_pinned_by_gui_even_when_cli_semantic_receipt_is_unchanged(self):
        plan = self.generate(); before = self.db()
        path = self.index / structure.MANIFEST; path.write_bytes(path.read_bytes() + b' ')
        structure.verify_structure(self.index, self.repo)
        for action, payload in [('plan', {'issue': 'addition'}), ('auto', {'issue': 'addition'}),
                ('claim_plan', {'plan_id': plan['id'], 'revision': plan['revision']}),
                ('decision', {'plan_id': plan['id'], 'fingerprint': plan['fingerprint'], 'decision': 'approve'})]:
            with self.assertRaisesRegex(SafetyError, 'structure files changed'): self.session._operate(action, payload)
        self.assertEqual('invalid', self.session.state()['structure_index']['status'])
        self.assertEqual('awaiting_approval', self.session.state()['plans'][0]['status'])
        self.assertEqual(before, self.db()); self.assertEqual(2, self.session.state()['requests_used'])

    def test_original_source_copy_and_graph_changes_invalidate_even_empty_queries(self):
        self.open()
        for path in [self.repo/'calculator.py', self.index/'snapshot'/'calculator.py', self.index/structure.GRAPH]:
            original = path.read_bytes(); path.write_bytes(original + b' ')
            with self.assertRaises(SafetyError): self.session._operate('structure_preview', {'query': 'no_matching_symbol'})
            self.assertEqual('invalid', self.session.state()['structure_index']['status'])
            path.write_bytes(original)
        self.assertEqual('verified', self.session.state()['structure_index']['status'])
        self.assertEqual([], self.session.state()['planning_attempts'])

    def test_restart_requires_new_claim_then_saved_approval_no_requests_and_consumed_remains(self):
        old = self.generate(); self.open(); current = self.session.state()['plans'][0]
        self.assertEqual(old['structure_binding'], current['structure_binding']); self.assertFalse(current['claimed'])
        with self.assertRaisesRegex(SafetyError, 'claim challenge changed'):
            self.session._operate('claim_plan', {'plan_id': old['id'], 'revision': old['revision']})
        self.session._operate('claim_plan', {'plan_id': current['id'], 'revision': current['revision']})
        self.session._operate('decision', {'plan_id': current['id'], 'fingerprint': current['fingerprint'], 'decision': 'approve'})
        self.assertEqual(2, self.session.state()['requests_used']); self.open()
        self.assertEqual('consumed', self.session.state()['plans'][0]['status'])
        with self.assertRaises(SafetyError): self.session._operate('claim_plan', {'plan_id': old['id'], 'revision': old['revision']})

    def test_disable_enable_or_same_bytes_different_directory_cannot_rebind_journal(self):
        self.generate(); self.session.close(); before = self.db()
        copy = self.base / 'another-index'; shutil.copytree(self.index, copy)
        for args in [dict(enabled=False), dict(index=copy)]:
            with self.assertRaisesRegex(SafetyError, 'scope changed'): self.open(**args)
            self.assertEqual(before, self.db())
        self.open(); self.assertEqual(2, self.session.state()['requests_used'])
        legacy = self.base / 'legacy'; self.open(enabled=False, runs=legacy); self.session.close()
        old = (legacy/'ui-state'/'registry.sqlite3').read_bytes()
        with self.assertRaisesRegex(SafetyError, 'scope changed'): self.open(runs=legacy)
        self.assertEqual(old, (legacy/'ui-state'/'registry.sqlite3').read_bytes())

    def test_stale_original_source_same_binding_reopens_history_but_cannot_claim(self):
        old = self.generate(); self.session.close()
        source = self.repo/'calculator.py'; source.write_bytes(source.read_bytes()+b'\n# later\n')
        gateway = RepositoryGateway(self.repo); gateway.command('add', 'calculator.py')
        gateway.command('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','changed')
        self.open(); self.assertEqual(2, self.session.state()['requests_used'])
        self.assertEqual('invalid', self.session.state()['structure_index']['status'])
        with self.assertRaises(SafetyError): self.session._operate('claim_plan', {'plan_id':old['id'],'revision':old['revision']})
        with self.assertRaises(SafetyError): self.open(runs=self.base/'new-journal')
        self.assertFalse((self.base/'new-journal'/'ui-state'/'registry.sqlite3').exists())

    def test_revision_guard_precedes_credentials(self):
        self.open(); value = self.session.provider.inner; value.config_file = self.base/'unused.env.local'
        path = self.index/structure.GRAPH; path.write_bytes(path.read_bytes()+b' ')
        with patch('repo_maintainer.provider_config.load_provider_config', side_effect=AssertionError('credentials')):
            with self.assertRaisesRegex(SafetyError, 'structure files changed'):
                value.revise(RepositoryGateway(self.repo), 'addition', self.policy.allowed_tests, {})

    def test_sdk_response_change_retains_unknown_allowance_and_no_saved_plan_after_restart(self):
        self.open(); issue='addition'; plan=asdict(MockProvider().plan(RepositoryGateway(self.repo), issue, self.policy.allowed_tests))
        data={'protocol_version':1,'status':'completed','plan':plan,'evidence':{'schema_version':1,
            'status':'completed','failure_category':None,'requests':2,'requests_started':2,
            'count_scope':'adapter_stream_invocations','consumption':'reported','model_cost_verified':False,
            'events_complete':True,'events':[],'usage':[]}}
        path=self.index/structure.GRAPH; original=path.read_bytes(); popen=subprocess.Popen; outer=self
        class Process:
            returncode=0
            def __init__(self):
                self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
            def communicate(self, raw, timeout):
                request=json.loads(raw)
                data['evidence']['edit_protocol']='exact-replacements-v1'
                data['patches']=[{'path':edit['path'], 'replacements':[{
                    'oldText':next(f['content'] for f in request['files'] if f['path']==edit['path']),
                    'newText':edit['content']}]} for edit in data['plan']['edits']]
                outer.assertNotIn('structure_index',request); outer.assertNotIn('structure_context',request)
                path.write_bytes(original+b' ')
                return json.dumps(data).encode(),b''
        def factory(argv,*args,**kwargs):
            return Process() if len(argv)>1 and Path(argv[1]).name=='plan.mjs' else popen(argv,*args,**kwargs)
        with patch('repo_maintainer.pi_provider.subprocess.Popen', side_effect=factory):
            with self.assertRaisesRegex(SafetyError, 'structure files changed'): self.session._operate('plan',{'issue':issue})
        state=self.session.state(); self.assertEqual([],state['plans']); self.assertEqual(4,state['requests_reserved_unknown'])
        path.write_bytes(original); self.open(); self.assertEqual(4,self.session.state()['requests_reserved_unknown'])
        self.assertEqual(0,self.session.state()['requests_remaining'])

    def test_http_rejects_paths_limits_controls_and_sql_fields_before_job_or_model(self):
        self.open()
        with TestClient(create_app(self.session,12345),base_url='http://127.0.0.1:12345') as client:
            for query,extra in [('add',{'index':'other'}),('add',{'sql':'select *'}),('x'*129,{}),('add\n',{}),('',{})]:
                response=client.post('/api/actions/structure_preview',headers={'Authorization':'Bearer '+self.session.token},
                    json={'request_id':uuid.uuid4().hex,'query':query,**extra})
                self.assertEqual(409,response.status_code)
        self.assertEqual([],self.session.state()['jobs']); self.assertEqual([],self.session.state()['planning_attempts'])

    def test_disabled_scope_keeps_original_binding_and_guard(self):
        self.open(enabled=False)
        self.assertFalse(self.session.state()['structure_index']['enabled'])
        self.assertIsNone(self.session.provider.inner._structure_guard)
        with contextlib.closing(sqlite3.connect(self.base/'runs'/'ui-state'/'registry.sqlite3')) as db:
            row=db.execute('SELECT body FROM metadata').fetchone()
        self.assertNotIn('structure_index',json.loads(row[0]))
        with self.assertRaises(SafetyError): self.session._operate('structure_preview',{'query':'add'})

    def test_cli_demo_structure_rejected_before_fixture_or_provider(self):
        dest=self.base/'unused'
        with contextlib.redirect_stderr(io.StringIO()),patch('repo_maintainer.cli.PiProvider',side_effect=AssertionError('provider')):
            self.assertEqual(1,main(['serve','--demo',str(dest),'--structure-index',str(self.index)]))
        self.assertFalse(dest.exists())

    def test_index_runs_overlap_and_non_pi_rejected(self):
        for index,runs,provider in [(self.index,self.index/'runs',self.provider()),(self.index,self.base, self.provider()),
                                  (self.index,self.base/'runs',MockProvider())]:
            with self.assertRaises(SafetyError):
                UISession(self.repo,self.policy,provider,runs,structure_index=index)


if __name__=='__main__': unittest.main()
