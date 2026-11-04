"""Explicit structure projection cannot substitute for source checks or approvals."""
import contextlib
from dataclasses import asdict
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import test_ui_structure as fixtures
from source_protocol_fixture import source_protocol_only
from repo_maintainer.cli import main
from repo_maintainer.dashboard import memory_evidence
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.provider import MockProvider
from repo_maintainer.source_memory import canonical
from repo_maintainer import structure_index as structure


class PiStructureContextTests(unittest.TestCase):
    setUp = fixtures.UIStructureTests.setUp
    finish = fixtures.UIStructureTests.finish
    db = fixtures.UIStructureTests.db

    def provider(self):
        value = fixtures.UIStructureTests.provider(self)
        if hasattr(self, 'memory_root'):
            value.memory_root, value.memory_index = self.memory_root, self.memory_index
        return value

    def memory(self, mixed=False):
        from repo_maintainer.source_memory import build_memory, CATALOG
        self.memory_root=self.base/'documents'; self.memory_root.mkdir()
        entries=[]
        for identity, classification in [('rule','confirmed'), ('alternative','proposed' if mixed else 'confirmed')]:
            marker='提议' if classification=='proposed' else '确认'
            (self.memory_root/(identity+'.md')).write_bytes(f'状态：{marker}\naddition requires approval.'.encode())
            entries.append(dict(id=identity,topic='addition',path=identity+'.md',start_line=1,end_line=2,
                                kind='document',classification=classification))
        (self.memory_root/CATALOG).write_bytes(canonical({'schema_version':1,'entries':entries}))
        self.memory_index=self.base/'source.memory.sqlite3'
        build_memory(self.memory_root,self.repo,self.memory_index)

    def open(self, query='add', enabled=True):
        if self.session: self.session.close()
        self.session = UISession(self.repo, self.policy, self.provider(), self.base/'runs',
            allow_auto=True, structure_index=self.index if enabled else None,
            structure_context_query=query)
        source_protocol_only(self.session)
        return self.session

    def generate(self):
        if not self.session: self.open()
        provider = self.session.provider.inner
        def fake(repo, issue, tests):
            context = self.session.structure.context(issue, repo.head)
            provider.requests_used += 2
            provider.evidence = {'requests': 2, 'structure_context_sha256': context['context_sha256']}
            return MockProvider().plan(repo, issue, tests)
        with patch.object(provider, 'plan', side_effect=fake):
            self.session._operate('plan', {'issue': 'addition'})
        return self.session.state()['plans'][0]

    def test_default_keeps_old_binding_and_absent_model_projection(self):
        self.open(query=None)
        self.assertNotIn('model_context', self.session.structure.binding)
        self.assertIsNone(self.session.provider.inner._structure_context)
        self.assertIsNone(self.session.structure.context('addition', RepositoryGateway(self.repo).head))
        self.assertFalse(self.session.state()['structure_index']['model_context'])

    def test_context_binds_original_source_query_issue_and_separate_candidate_base(self):
        self.open(); before = self.db(); head = RepositoryGateway(self.repo).head
        value = self.session.structure.context('addition', head)
        self.assertEqual(['add'], [n['name'] for n in value['hits']])
        self.assertFalse(value['runtime_authorized']); self.assertTrue(value['structural_claims_only'])
        self.assertNotIn(str(self.index), json.dumps(value))
        self.assertNotIn('excerpt', value['hits'][0])
        receipt = value.pop('context_sha256'); self.assertEqual(receipt, digest(canonical(value)))
        candidate = self.session.structure.context('addition', 'f'*40)
        self.assertEqual(head, candidate['source']['head']); self.assertEqual('f'*40, candidate['planning_base_sha'])
        self.assertNotEqual(receipt, candidate['context_sha256'])
        self.assertNotEqual(receipt, self.session.structure.context('other', head)['context_sha256'])
        class Candidate:
            def read(_, path): return RepositoryGateway(self.repo).read(path)+b'\n'
        revised=self.session.structure.context('addition', head, Candidate())
        self.assertEqual(head, revised['planning_base_sha'])
        self.assertNotEqual(value['planning_files_sha256'], revised['planning_files_sha256'])
        self.assertNotEqual(receipt, revised['context_sha256'])
        self.assertEqual(before, self.db()); self.assertEqual([], self.session.state()['planning_attempts'])

    def test_empty_query_results_still_verify_all_original_sources(self):
        self.open(query='no_matching_symbol'); head = RepositoryGateway(self.repo).head
        value = self.session.structure.context('addition', head)
        self.assertEqual(([], [], []), (value['hits'], value['edges'], value['related_nodes']))
        copy = self.index/'snapshot'/'calculator.py'; copy.write_bytes(copy.read_bytes()+b' ')
        with self.assertRaises(SafetyError): self.session.structure.context('addition', head)

    def test_query_enable_disable_cannot_rebind_or_reset_used_budget(self):
        self.generate(); self.session.close(); before = self.db()
        for query, enabled in [(None, True), ('other', True), (None, False)]:
            with self.assertRaisesRegex(SafetyError, 'scope changed'): self.open(query, enabled)
            self.assertEqual(before, self.db())
        self.open(); self.assertEqual(2, self.session.state()['requests_used'])

    def test_restart_requires_fresh_claim_and_saved_approval_has_no_new_requests(self):
        old = self.generate(); self.open(); current = self.session.state()['plans'][0]
        self.assertFalse(current['claimed'])
        with self.assertRaisesRegex(SafetyError, 'claim challenge changed'):
            self.session._operate('claim_plan', {'plan_id':old['id'], 'revision':old['revision']})
        self.session._operate('claim_plan', {'plan_id':current['id'], 'revision':current['revision']})
        with patch.object(self.session.provider.inner, 'plan', side_effect=AssertionError('new model request')):
            self.session._operate('decision', {'plan_id':current['id'], 'fingerprint':current['fingerprint'], 'decision':'approve'})
        self.assertEqual(2, self.session.state()['requests_used'])
        self.open(); self.assertEqual('consumed', self.session.state()['plans'][0]['status'])

    def simulate(self, mode):
        self.open(); issue='addition'; repository=RepositoryGateway(self.repo)
        expected=self.session.structure.context(issue, repository.head)
        data={'protocol_version':1, 'status':'completed',
              'plan':asdict(MockProvider().plan(repository, issue, self.policy.allowed_tests)),
              'evidence':{'schema_version':1,'status':'completed','failure_category':None,
                'requests':2,'requests_started':2,'count_scope':'adapter_stream_invocations',
                'consumption':'reported','model_cost_verified':False,'events_complete':True,'events':[],'usage':[],
                'structure_context_sha256':expected['context_sha256']}}
        memory=self.session.memory.require_current(issue)
        if memory:
            memory=memory['context']; data['evidence']['memory_context_sha256']=memory['context_sha256']
        source=self.index/structure.GRAPH; original=source.read_bytes(); popen=subprocess.Popen; outer=self
        calls=[]
        class Process:
            returncode=0
            def __init__(self):
                self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
            def communicate(self, raw, timeout):
                request=json.loads(raw); calls.append(request)
                data['evidence']['edit_protocol']='exact-replacements-v1'
                data['patches']=[{'path':edit['path'], 'replacements':[{
                    'oldText':next(f['content'] for f in request['files'] if f['path']==edit['path']),
                    'newText':edit['content']}]} for edit in data['plan']['edits']]
                outer.assertEqual(expected, request['structure_context'])
                if memory: outer.assertEqual(memory, request['source_memory'])
                outer.assertNotIn('structure_index', request)
                if mode=='changed': source.write_bytes(original+b' ')
                if mode=='wrong': data['evidence']['structure_context_sha256']='e'*64
                if mode=='missing': data['evidence'].pop('structure_context_sha256')
                return json.dumps(data).encode(), b''
        def factory(argv,*args,**kwargs):
            return Process() if len(argv)>1 and Path(argv[1]).name=='plan.mjs' else popen(argv,*args,**kwargs)
        with patch('repo_maintainer.pi_provider.subprocess.Popen', side_effect=factory):
            if mode=='valid': self.session._operate('plan', {'issue':issue})
            else:
                with self.assertRaises(SafetyError): self.session._operate('plan', {'issue':issue})
        self.assertEqual(1, len(calls)); source.write_bytes(original)
        self.open(); state=self.session.state()
        if mode=='valid':
            self.assertEqual(2, state['requests_used']); self.assertEqual(0, state['requests_reserved_unknown'])
            evidence=state['plans'][0]['planning_evidence']
            self.assertEqual(expected['context_sha256'], evidence['structure_context_sha256'])
            self.assertEqual(expected['hits'], evidence['structure_provenance']['hits'])
            if memory: self.assertEqual(memory['context_sha256'], evidence['memory_context_sha256'])
        else:
            self.assertEqual([], state['plans']); self.assertEqual(4, state['requests_reserved_unknown'])
            self.assertEqual(0, state['requests_remaining'])

    def test_valid_sdk_receipt_retains_bounded_provenance(self): self.simulate('valid')
    def test_source_memory_and_structure_remain_separate_packages_and_receipts(self):
        self.memory(); self.simulate('valid')
    def test_structure_does_not_override_mixed_document_gate_or_reserve_budget(self):
        self.memory(mixed=True); self.open(); before=self.db()
        with patch.object(self.session.provider.inner, 'plan', side_effect=AssertionError('SDK')):
            with self.assertRaisesRegex(SafetyError, 'Mixed'): self.session._operate('plan', {'issue':'addition'})
        self.assertEqual([], self.session.state()['planning_attempts']); self.assertEqual(before, self.db())
    def test_sdk_graph_change_blocks_plan_and_preserves_unknown_after_restart(self): self.simulate('changed')
    def test_sdk_wrong_receipt_blocks_plan_and_preserves_unknown_after_restart(self): self.simulate('wrong')
    def test_sdk_missing_receipt_blocks_plan_and_preserves_unknown_after_restart(self): self.simulate('missing')

    def test_revision_gate_and_unbound_callback_precede_credentials(self):
        self.open(); provider=self.session.provider.inner; provider.config_file=self.base/'unused.env.local'
        graph=self.index/structure.GRAPH; graph.write_bytes(graph.read_bytes()+b' ')
        with patch('repo_maintainer.provider_config.load_provider_config', side_effect=AssertionError('credentials')):
            with self.assertRaises(SafetyError): provider.revise(RepositoryGateway(self.repo), 'addition', self.policy.allowed_tests, {})
            orphan=PiProvider(('calculator.py',)); orphan._structure_context=lambda *args: {}
            with self.assertRaisesRegex(SafetyError, 'fixed UI structure binding'): orphan.plan(None, 'addition', ())

    def test_cli_optin_is_rejected_before_fixture_or_provider(self):
        dest=self.base/'unused'
        with contextlib.redirect_stderr(io.StringIO()), patch('repo_maintainer.cli.PiProvider',side_effect=AssertionError('provider')):
            for args in [['serve','--structure-context-query','add'],
                         ['serve','--demo',str(dest),'--structure-index',str(self.index),'--structure-context-query','add'],
                         ['serve','--structure-index',str(self.index),'--structure-context-query','a\n']]:
                self.assertEqual(1, main(args))
        self.assertFalse(dest.exists())

    def test_report_projection_excludes_unknown_fields_and_bounds_rows(self):
        row={'id':'add','path':'calculator.py','excerpt':'private','approved':True}
        result=memory_evidence({'structure_context_sha256':'a'*64,'secret':'private',
            'structure_provenance':{'hits':[row]*8,'edges':[{'source':'a','target':'b','kind':'calls','sql':'private'}]*60,
                                    'related_nodes':[row]*60,'private_root':'private'}})
        self.assertEqual(5, len(result['structure_provenance']['hits']))
        self.assertEqual(50, len(result['structure_provenance']['edges']))
        self.assertEqual(50, len(result['structure_provenance']['related_nodes']))
        self.assertNotIn('private', json.dumps(result)); self.assertNotIn('approved', json.dumps(result))


if __name__=='__main__': unittest.main()
