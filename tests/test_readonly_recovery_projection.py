"""Readonly recovery classification; pinned fixtures only, no Pi/model process."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from repo_maintainer.demo import create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError,digest
from repo_maintainer.task_session import TaskSessionStore,fingerprint
from repo_maintainer.task_recovery_view import recovery_cards
from repo_maintainer.ui_registry import UIRegistry
from repo_maintainer.validation_proposal import source_packet,proposal_contract

class ReadonlyRecoveryProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=RepositoryGateway(create_fixture(self.root/'source'))
        (self.repo.root/'calc.mjs').write_text('export function add(a,b){return a-b}\n')
        self.repo.command('add','calc.mjs');self.repo.command('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','node fixture')
        self.registry=UIRegistry(self.root/'runs',{'provider':{'request_budget':8}});self.addCleanup(self.registry.close)
        self.store=TaskSessionStore(self.registry,self.repo.root,self.repo.common_dir)
        def manual_scope(_):raise SafetyError('This fixture never supplies manual repair authority')
        self.session=SimpleNamespace(registry=self.registry,task_planner=SimpleNamespace(store=self.store,current_scope=manual_scope),failure_feedback=None)
        self.state={'active':None,'plans':[],'runs':[],'runtime':{'code_changed':False},'source_memory':{},'structure_index':{},**self.registry.budget()}
    def make(self,schema=2,path='calculator.py',status='accepted',issue='Please correct add behavior'):
        binding={'provider':'pi-loopback','model':'mock-maintenance','endpoint':['http','127.0.0.1',1,'/v1'],'credential_source':None}
        paths=['calculator.py',path] if schema==2 and path.endswith('.mjs') else [path]
        source=source_packet(self.repo,issue,['.'],paths,{'issue_token':'a'*64,'provider':binding})
        context={'schema_version':schema,'purpose':'validation-proposal-v1','provider':binding['provider'],'model':binding['model'],'paths':paths}
        if schema==3:context['validation_schema']='data-behavior-v2'
        scope=dict(repository_sha256=source['repository_id'],base_sha=source['base_sha'],issue_sha256=fingerprint(issue),
            policy_sha256=fingerprint({'purpose':'validation-proposal-v1','readonly':paths}),provider_sha256=fingerprint(binding),
            source_sha256=fingerprint({'validation_source':source['source_sha256']}),
            tests_sha256=fingerprint({'purpose':'validation-proposal-v1','execution_checks':False}),context_sha256=fingerprint(context))
        task=self.store.allocate(scope,context)["task_id"];identity=fingerprint({'source':source,'provider_identity':binding})
        proposal={'requirement':issue,'rationale':'fixture business requirement','limitations':'What result?' if status=='clarification_needed' else 'stated case',
            'clarification':status=='clarification_needed','cases':[] if status=='clarification_needed' else [{'id':'sum','module':paths[0],'callable':'add','args':[2,3],'kwargs':{},'expect':{'kind':'json','value':5},'missing_behavior':False}]}
        row={'id':identity,'task_id':task,'source':source,'status':status,'runtime_authorized':False,'proposal':proposal,'contract':proposal_contract(source,proposal)}
        self.registry.put('validation_proposal',identity,row)
        if status not in {'started','interrupted_no_replay'}:
            # Bytes are solely a hash-integrity fixture, not a claimed SDK transcript.
            raw=b'{"fixture":"pinned prefix"}\n';directory=self.store.directory(task)/'sessions';directory.mkdir()
            (directory/'fixture.jsonl').write_bytes(raw);record=self.registry.load('pi_task')[task]
            record.update(revision=1,checkpoint={'file':'fixture.jsonl','session_id':'0'*36,'leaf_id':'1'*8,'prefix_bytes':len(raw),
                'prefix_sha256':digest(raw),'rounds':1,'snapshot_sha256':'a'*64,'attempt_id':'2'*32})
            self.registry.put('pi_task',task,record)
        return identity,task,row
    def cards(self):
        with patch('subprocess.Popen',side_effect=AssertionError('No child process in readonly projection')):
            return recovery_cards(self.session,self.state)
    def snapshot(self):return (self.registry.path.read_bytes(),{str(p):p.read_bytes() for p in self.root.rglob('*.jsonl')})
    def test_python2_node3_and_frozen_node2_do_not_become_unfinished_repairs(self):
        for schema,path in ((2,'calculator.py'),(3,'calc.mjs'),(2,'calc.mjs')):
            with self.subTest(schema=schema,path=path):
                self.make(schema,path,issue=str(schema)+path);before=self.snapshot()
                self.assertEqual([],self.cards());self.assertEqual(before,self.snapshot())
    def test_unknown_stopped_readonly_is_retained_in_original_row_and_budget_without_repair_actions(self):
        identity,task,row=self.make(status='interrupted_no_replay');self.registry.reserve(3)
        self.state.update(self.registry.budget());before=self.snapshot()
        self.assertEqual([],self.cards());self.assertEqual(row,self.registry.load('validation_proposal')[identity])
        self.assertEqual(3,self.state['requests_reserved_unknown']);self.assertEqual(before,self.snapshot())
    def test_historical_readonly_classification_does_not_require_current_head_sources_or_model(self):
        self.make()
        (self.repo.root/'calculator.py').write_text('def add(a,b): return a+b\n')
        self.repo.command('add','calculator.py')
        self.repo.command('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','later source')
        self.state['runtime']['code_changed']=True;self.state['source_memory']['status']='invalid'
        self.session.provider=SimpleNamespace(task_identity=lambda:(_ for _ in ()).throw(AssertionError('Do not read current model configuration')))
        before=self.snapshot();self.assertEqual([],self.cards());self.assertEqual(before,self.snapshot())
    def test_scope_context_source_binding_and_missing_native_corruption_remain_visible(self):
        identity,task,row=self.make();original=copy.deepcopy(self.registry.load('pi_task')[task]);prefix=self.store.directory(task)/'sessions/fixture.jsonl';raw=prefix.read_bytes()
        mutations=[lambda r:r['scope'].__setitem__('provider_sha256','f'*64),lambda r:r['scope'].__setitem__('source_sha256','f'*64),
            lambda r:r['context'].__setitem__('model','other'),lambda r:r.__setitem__('budget_scope_sha256','f'*64)]
        for change in mutations:
            changed=copy.deepcopy(original);change(changed);self.registry.put('pi_task',task,changed)
            self.assertEqual([task],[c['id'] for c in self.cards()]);self.assertFalse(self.cards()[0]['actions'])
        self.registry.put('pi_task',task,original);prefix.write_bytes(raw+b'changed')
        # Appended uncommitted tails do not damage the original committed prefix.
        self.assertEqual([],self.cards());prefix.write_bytes(b'bad prefix')
        self.assertEqual([task],[c['id'] for c in self.cards()]);prefix.unlink()
        self.assertEqual([task],[c['id'] for c in self.cards()])
    def test_missing_duplicate_or_tampered_validation_source_cannot_hide_task(self):
        identity,task,row=self.make()
        for change in (lambda r:r['source'].__setitem__('issue','changed'),lambda r:r.__setitem__('id','f'*64),
                       lambda r:r.__setitem__('runtime_authorized',True),lambda r:r.__setitem__('contract',{'schema_version':'wrong'})):
            changed=copy.deepcopy(row);change(changed);self.registry.put('validation_proposal',identity,changed)
            self.assertEqual([task],[c['id'] for c in self.cards()])
        self.registry.put('validation_proposal',identity,row);self.registry.put('validation_proposal','e'*64,row)
        self.assertEqual([task],[c['id'] for c in self.cards()])
        with self.registry.transaction() as db:db.execute("DELETE FROM objects WHERE kind='validation_proposal'")
        self.assertEqual([task],[c['id'] for c in self.cards()])
    def test_original_chain_is_checked_and_no_arbitrary_depth_limit_is_added(self):
        identity,task,parent=self.make(status='clarification_needed');root=identity
        for n in range(10):
            child_id,child_task,child=self.make(status='clarification_needed',issue=parent['source']['issue']+'\n\n用户补充业务期望：\n'+str(n))
            self.registry.put('validation_clarification',identity,{'parent_id':identity,'parent_sha256':fingerprint(parent),
                'child_id':child_id,'answer':str(n),'source':child['source']})
            identity,task,parent=child_id,child_task,child
        self.assertEqual([],self.cards())
        link=self.registry.load('validation_clarification')[root];link['parent_sha256']='f'*64
        self.registry.put('validation_clarification',root,link)
        self.assertEqual(11,len(self.cards()));self.assertTrue(all(not c['actions'] for c in self.cards()))
    def test_genuine_repair_and_unknown_or_unrecognised_purpose_keep_original_recovery_cards(self):
        identity,task,row=self.make(status='started');record=self.registry.load('pi_task')[task]
        for context in ({'provider':'pi-loopback','model':'mock-maintenance','paths':['calculator.py']},
                        {**record['context'],'purpose':'repair-investigation-v1'},
                        {**record['context'],'purpose':'unknown-purpose'}):
            changed=copy.deepcopy(record);changed['context']=context;changed['scope']['context_sha256']=fingerprint(context)
            self.registry.put('pi_task',task,changed);self.state['requests_reserved_unknown']=2
            card=self.cards()[0];self.assertEqual(task,card['id']);self.assertEqual('unknown',card['status']);self.assertFalse(card['actions'])

if __name__=='__main__':unittest.main(verbosity=2)
