"""Purpose isolation in actual pinned Pi JSONL and the original checkpoint lease/journal."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.task_session import (TaskSessionStore, validate_transcript, fingerprint, strict_json,
                                         _context, _verify_schema_rejections, VALIDATION_PURPOSE)
from repo_maintainer.ui_registry import UIRegistry

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'runtime/pi/test/validation-native-fixture.mjs'
CONTEXT={'schema_version':2,'purpose':VALIDATION_PURPOSE,'provider':'native-fixture','model':'synthetic',
         'paths':['calculator.py']}

def proposal():
    return {'requirement':'Return signed addition','rationale':'Explicit desired sum','limitations':'Python only',
            'clarification':False,'cases':[{'id':'negative','module':'calculator.py','callable':'add','args':[-2,1],
                'kwargs':{},'expect':{'kind':'json','value':-1},'missing_behavior':False}]}

def raw(entries):
    return b''.join(json.dumps(e,ensure_ascii=False).encode()+b'\n' for e in entries)

class ValidationNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='pi-validation-native-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir()
        self.common=self.source/'.git';self.common.mkdir()
        # A Native/ledger unit fixture needs no writable Policy or invented tests.
        self.binding={'repository':str(self.source),'provider':{'request_budget':8},'purpose':VALIDATION_PURPOSE}
        self.registry=UIRegistry(self.root/'state',self.binding);self.addCleanup(lambda:self.registry.close())
        self.store=TaskSessionStore(self.registry,self.source,self.common)
        self.scope={key:digest(key.encode()) for key in ('issue_sha256','policy_sha256','provider_sha256',
                                                      'source_sha256','tests_sha256')}
        self.scope.update(repository_sha256=self.store.repository,base_sha='a'*40,context_sha256=fingerprint(CONTEXT))
        self.record=self.store.allocate(self.scope,CONTEXT);self.identity=self.record['task_id']
        self.directory=self.store.directory(self.identity)
        (self.directory/'workspace').mkdir();(self.directory/'sessions').mkdir()
        self.task={'mode':'create','context':CONTEXT,'binding':{'schema_version':1,'task_id':self.identity,
            'revision':0,'cwd':str(self.directory/'workspace'),'sessions_dir':str(self.directory/'sessions')}}

    def node(self,task,**data):
        env={k:v for k,v in os.environ.items() if k.upper() in {
            'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','COMSPEC','PATHEXT'}}
        result=subprocess.run([shutil.which('node'),str(FIXTURE)],input=json.dumps({'task':task,**data}),
                              encoding='utf-8',capture_output=True,env=env,timeout=20)
        self.assertEqual(0,result.returncode,result.stderr)
        return json.loads(result.stdout)

    def create(self,value=None,**data):
        result=self.node(self.task,proposal=value if value is not None else proposal(),**data)
        self.file=Path(result['session_file'])
        self.entries=[strict_json(line) for line in result['raw'].splitlines()]
        return result

    def validate(self,entries=None,**kwargs):
        return validate_transcript(raw(entries or self.entries),cwd=self.directory/'workspace',context=CONTEXT,**kwargs)

    def attempt(self):
        identity,_=self.registry.reserve();self.registry.settle(identity,{'requests':1},success=True);return identity

    def test_original_lease_commits_and_reopens_real_sdk_native_without_extra_budget(self):
        first=self.create();attempt=self.attempt()
        with self.store.lease(self.identity,self.scope,0) as lease:
            saved=lease.commit(self.file.name,first['leaf_id'],'b'*64,attempt);receipt=lease.descriptor()
        before=self.registry.budget();self.registry.close()
        self.registry=UIRegistry(self.root/'state',self.binding)
        self.store=TaskSessionStore(self.registry,self.source,self.common)
        with self.store.lease(self.identity,self.scope,1) as lease:
            self.assertEqual(receipt,lease.descriptor())
            reopened=self.node({'mode':'open','context':CONTEXT,'binding':receipt})
        self.assertEqual(first['session_id'],reopened['session_id']);self.assertEqual(first['leaf_id'],reopened['leaf_id'])
        self.assertEqual(1,saved['revision']);self.assertEqual(before,self.registry.budget())
        self.assertEqual(1,before['requests_used']);self.assertEqual(0,before['requests_reserved_unknown'])

    def test_clarification_with_zero_cases_is_submitted_read_only(self):
        self.create({**proposal(),'clarification':True,'cases':[]})
        self.assertEqual(1,self.validate()['rounds'])
        result=next(e['message'] for e in self.entries if e.get('message',{}).get('role')=='toolResult')
        self.assertIs(False,result['details']['runtime_authorized'])

    def test_raw_float_cases_keep_original_spelling_and_native_receipt_hash(self):
        value=proposal();cases=value.pop('cases')
        value['cases_json']=json.dumps(cases).replace('[-2, 1]','[-0.0, 1.0]').replace('"value": -1','"value": 1.0')
        self.create(value);self.assertEqual(1,self.validate()['rounds'])
        parsed=strict_json(value['cases_json'])
        self.assertIs(type(parsed[0]['args'][1]),float);self.assertIs(type(parsed[0]['expect']['value']),float)

    def test_raw_exponents_and_large_array_use_original_python_numeric_tokens(self):
        value=proposal();cases=value.pop('cases')
        value['cases_json']=json.dumps(cases).replace('[-2, 1]','['+','.join(['1e20']*1500)+']') \
            .replace('"value": -1','"value": 1e20')
        self.create(value);self.assertEqual(1,self.validate()['rounds'])
        parsed=strict_json(value['cases_json'])
        self.assertEqual(1500,len(parsed[0]['args']));self.assertIs(type(parsed[0]['args'][0]),float)
        self.assertIs(type(parsed[0]['expect']['value']),float)

    def test_raw_true_large_integer_is_rejected_by_python_case_validation(self):
        value=proposal();cases=value.pop('cases')
        value['cases_json']=json.dumps(cases).replace('[-2, 1]','[100000000000000000000]')
        self.create(value)
        with self.assertRaisesRegex(SafetyError,'integer exceeds canonical range'):self.validate()

    def test_original_schema_rejection_then_success_preserves_complete_history(self):
        self.create(rejected={'requirement':'missing fields'})
        self.assertEqual(1,self.validate()['rounds'])
        self.entries[5]['message']['content'][0]['text']+='tampered'
        with self.assertRaises(SafetyError):self.validate()

    def test_schema_valid_raw_json_data_error_can_precede_correct_submission(self):
        value=proposal();value.pop('cases');value['cases_json']='malformed JSON'
        self.create(semantic_rejected=value)
        self.assertEqual(1,self.validate()['rounds'])

    def test_nonselected_branch_cannot_hide_a_forged_schema_rejection(self):
        self.create(rejected={'requirement':'missing fields'})
        self.entries[5]['message']['content'][0]['text']+='forged'
        # Current correct proposal branches directly from the original user; the rejected pair is unselected.
        self.entries[6]['parentId']=self.entries[3]['id']
        with self.assertRaisesRegex(SafetyError,'schema rejection receipt'):self.validate()

    def test_success_receipt_cannot_upgrade_purpose_authority_or_unbind_args(self):
        self.create()
        for mutation in ('purpose','hash','authority','details','arguments','extra'):
            entries=copy.deepcopy(self.entries)
            message=next(e['message'] for e in entries if e.get('message',{}).get('role')=='toolResult')
            receipt=json.loads(message['content'][0]['text'])
            if mutation=='purpose':receipt['purpose']='repair'
            elif mutation=='hash':receipt['proposal_sha256']='0'*64
            elif mutation=='authority':receipt['runtime_authorized']=True
            elif mutation=='details':message['details']={}
            elif mutation=='extra':receipt['approved']=True
            else:
                call=next(e['message']['content'][0] for e in entries
                          if e.get('message',{}).get('stopReason')=='toolUse')
                call['arguments']['limitations']='changed after receipt'
            message['content'][0]['text']=json.dumps(receipt)
            if mutation!='details':message['details']=receipt
            with self.subTest(mutation=mutation),self.assertRaises(SafetyError):self.validate(entries)

    def test_legacy_and_proposal_tools_cannot_cross_purpose(self):
        self.create()
        legacy={k:CONTEXT[k] for k in ('provider','model','paths')}
        with self.assertRaisesRegex(SafetyError,'Unauthorized Pi tool call'):
            validate_transcript(raw(self.entries),cwd=self.directory/'workspace',context=legacy)
        entries=copy.deepcopy(self.entries)
        next(e['message']['content'][0] for e in entries if e.get('message',{}).get('stopReason')=='toolUse')['name']='propose_repair'
        with self.assertRaisesRegex(SafetyError,'Unauthorized Pi tool call'):self.validate(entries)

    def test_case_scope_zero_cases_extra_fields_and_duplicate_raw_keys_rejected(self):
        self.create()
        for mutation in ('module','zero','extra','duplicate','both','malformed'):
            entries=copy.deepcopy(self.entries)
            value=next(e['message']['content'][0]['arguments'] for e in entries
                       if e.get('message',{}).get('stopReason')=='toolUse')
            if mutation=='module':value['cases'][0]['module']='outside.py'
            elif mutation=='zero':value['cases']=[]
            elif mutation=='extra':value['edits']=[]
            elif mutation=='both':value['cases_json']=json.dumps(value['cases'])
            else:
                cases=value.pop('cases')
                value['cases_json']=json.dumps(cases).replace('"id": "negative"','"id":"negative","id":"other"') \
                    if mutation=='duplicate' else '{bad JSON'
            with self.subTest(mutation=mutation),self.assertRaises(SafetyError):self.validate(entries)

    def test_unselected_failed_tail_is_checked_and_preserved(self):
        self.create();leaf=self.entries[-1]['id']
        tail={'type':'message','id':'aa000001','parentId':leaf,'timestamp':'now',
              'message':{'role':'user','content':'uncommitted investigation','timestamp':2}}
        self.entries.append(tail)
        self.assertTrue(self.validate(leaf=leaf)['tail_uncommitted'])
        tail['message']={'role':'assistant','provider':CONTEXT['provider'],'model':CONTEXT['model'],
            'timestamp':2,'stopReason':'error','content':[{'type':'text','text':'Synthetic interrupted response'}]}
        self.assertTrue(self.validate(leaf=leaf)['tail_uncommitted'])
        tail['message']={'role':'assistant','provider':CONTEXT['provider'],'model':CONTEXT['model'],
            'timestamp':2,'stopReason':'toolUse','content':[{'type':'toolCall','id':'bad','name':'propose_repair','arguments':{}}]}
        with self.assertRaises(SafetyError):self.validate(leaf=leaf)

    def test_context_exact_version_model_source_and_purpose_are_immutable(self):
        self.assertEqual(CONTEXT,_context(CONTEXT))
        for patch_value in ({'purpose':'repair'},{'schema_version':True},{'extra':1},{'paths':['../escape.py']}):
            with self.subTest(patch=patch_value),self.assertRaises(SafetyError):_context({**CONTEXT,**patch_value})
        changed={**self.scope,'context_sha256':fingerprint({k:CONTEXT[k] for k in ('provider','model','paths')})}
        with self.assertRaises(SafetyError):
            with self.store.lease(self.identity,changed,0):self.fail('purpose converted')

    def test_verifier_gets_stage_from_controller_argv_not_transcript(self):
        with patch('repo_maintainer.task_session.subprocess.run',return_value=subprocess.CompletedProcess([],0,'true','')) as called:
            _verify_schema_rejections([],VALIDATION_PURPOSE,proposals=[],paths=CONTEXT['paths'])
        self.assertEqual(['--purpose',VALIDATION_PURPOSE],called.call_args.args[0][-2:])
        self.assertNotIn('purpose',json.loads(called.call_args.kwargs['input']))

    def test_original_budget_scope_and_unknown_attempt_cannot_commit_proposal(self):
        first=self.create();attempt,_=self.registry.reserve()
        self.registry.settle(attempt,{'requests':None},success=False)
        before=self.registry.budget()
        with self.store.lease(self.identity,self.scope,0) as lease:
            with self.assertRaisesRegex(SafetyError,'settled original'):
                lease.commit(self.file.name,first['leaf_id'],'b'*64,attempt)
        self.assertEqual(before,self.registry.budget())
        with patch.object(self.registry,'path',self.root/'foreign.sqlite3'):
            with self.assertRaisesRegex(SafetyError,'budget binding'):
                with self.store.lease(self.identity,self.scope,0):self.fail('foreign ledger accepted')

if __name__=='__main__':unittest.main()
