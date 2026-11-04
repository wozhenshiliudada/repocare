"""Credential echoes are checked after JSON decoding, including escaped keys."""
import copy
import json
import unittest
import uuid

from repo_maintainer.assistant_limits import MAX_ASSISTANT_REQUESTS_PER_TURN

import test_assistant as fixture_module
from test_assistant_forms import snapshot


class AssistantCredentialTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture_module.AssistantTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f=self.fixture
        f.secret='synthetic-"quoted"-credential\\path'
        f.profile=f.hub.model_profiles.create({'revision':f.hub.model_profiles.view()['revision'],
            'action':'create','name':'escaped credential fixture','api_key':f.secret,
            'base_url':'https://provider.example.invalid/v1','model':'escaped-key-model',
            'context_window_tokens':65536,'max_output_tokens':2048})['created_profile_id']

    def test_recursive_detection_checks_decoded_string_values_and_dictionary_keys(self):
        from repo_maintainer.assistant_secrets import contains_credential
        key=self.fixture.secret
        for value in ({'changes':[{'value':key}]},{'outer':{key:'value'}},[None,False,{'nested':'prefix '+key+' suffix'}]):
            encoded=json.dumps(value,ensure_ascii=False)
            self.assertNotIn(key,encoded,'The adversarial key must actually be JSON escaped')
            self.assertTrue(contains_credential(json.loads(encoded),key))
        self.assertFalse(contains_credential({'safe':['ordinary discussion',None,False,42]},key))

    def test_escaped_credential_proposal_echo_is_private_and_keeps_unknown_count(self):
        f=self.fixture;identity=f.create();f.model.proposal_value=f.secret
        failed,_=f.send(identity,'请给当前表单一个草稿',snapshot())
        self.assertEqual('failed',failed['status'],failed)
        public=f.control.view()
        self.assertEqual([],f.session(identity)['messages'])
        from repo_maintainer.assistant_secrets import contains_credential
        self.assertFalse(contains_credential(public,f.secret))
        budget = public['budgets'][f.profile]
        self.assertEqual(0, budget['requests_used'])
        self.assertEqual(MAX_ASSISTANT_REQUESTS_PER_TURN, budget['requests_reserved_unknown'])
        self.assertEqual(f.hub.request_budget - MAX_ASSISTANT_REQUESTS_PER_TURN, budget['requests_remaining'])
        self.assertNotIn('requests_counted',failed)
        f.assert_no_business_calls()

    def test_new_suffix_echo_keeps_the_entire_prior_native_prefix_and_local_error_evidence(self):
        f=self.fixture;identity=f.create();finished,_=f.send(identity,'成功原文')
        self.assertEqual('finished',finished['status'],finished)
        record=copy.deepcopy(f.record(identity));native=f.root/'assistant/conversations'/identity/'sessions'/record['native_file']
        before=native.read_bytes();f.model.proposal_value=f.secret
        failed,_=f.send(identity,'请给当前表单一个草稿',snapshot())
        self.assertEqual('failed',failed['status'],failed)
        after=native.read_bytes()
        self.assertGreater(len(after),len(before),'Rejected suffix must remain local, never truncate the original Native')
        self.assertTrue(after.startswith(before));self.assertEqual(record,f.record(identity))
        from repo_maintainer.assistant_secrets import contains_credential
        self.assertFalse(contains_credential(f.control.view(),f.secret))
        f.assert_no_business_calls()

    def test_user_text_and_current_form_secrets_stop_before_reserving_or_sending(self):
        f=self.fixture;identity=f.create();before=copy.deepcopy(f.control.view()['budgets'])
        form=snapshot();form['fields'][0]['value']=f.secret
        for text,context in (('不要发送 '+f.secret,None),('这个表单里含秘密',form)):
            result,_=f.send(identity,text,context)
            self.assertEqual('failed',result['status'],result)
            self.assertEqual([],f.model.requests)
            self.assertEqual(before,f.control.view()['budgets'])
        self.assertEqual([],f.session(identity)['messages']);f.assert_no_business_calls()

    def test_linked_history_refuses_preexisting_escaped_credential_without_changing_source(self):
        f=self.fixture;identity=f.create();first,_=f.send(identity,'普通成功讨论')
        self.assertEqual('finished',first['status'],first)
        # Simulate a successful record saved by an older version whose serialized
        # substring check missed this JSON-escaped credential. No real account is used.
        source=f.record(identity);source['messages'][0]['content']='historical '+f.secret
        f.control.executor.submit(lambda:f.control._head().put('assistant_session',identity,source)).result()
        source_before=copy.deepcopy(f.record(identity));native_before=f._snapshot(f.root/'assistant/conversations'/identity)
        budget_before=copy.deepcopy(f.control.view()['budgets']);calls=len(f.model.requests)
        target=f.hub.model_profiles.create({'revision':f.hub.model_profiles.view()['revision'],
            'action':'create','name':'other provider','api_key':'synthetic-other-provider-key',
            'base_url':'https://other-provider.example.invalid/v1','model':'other-model'})['created_profile_id']
        result=f.wait(f.control.submit('switch_model',{'request_id':uuid.uuid4().hex,'session_id':identity,'profile_id':target}))
        self.assertEqual('failed',result['status'],result)
        self.assertEqual(calls,len(f.model.requests));self.assertEqual(source_before,f.record(identity))
        self.assertEqual(native_before,f._snapshot(f.root/'assistant/conversations'/identity))
        self.assertEqual(budget_before,f.control.view()['budgets'])
        self.assertEqual(1,len(f.control.view()['sessions']));f.assert_no_business_calls()

    def test_inherited_source_key_is_not_sent_to_another_profile_from_a_linked_snapshot(self):
        f=self.fixture;identity=f.create();first,_=f.send(identity,'成功历史')
        self.assertEqual('finished',first['status'],first)
        target_profile=f.hub.model_profiles.create({'revision':f.hub.model_profiles.view()['revision'],
            'action':'create','name':'linked target','api_key':'synthetic-target-only-key',
            'base_url':'https://other.example.invalid/v1','model':'target-model'})['created_profile_id']
        switched=f.wait(f.control.submit('switch_model',{'request_id':uuid.uuid4().hex,'session_id':identity,'profile_id':target_profile}))
        self.assertEqual('finished',switched['status'],switched);target=switched['session_id']
        history=f.control.executor.submit(lambda:f.control._head().load('assistant_history')[target]).result()
        history['messages'][0]['content']='old escaped source key '+f.secret
        body={key:value for key,value in history.items() if key!='context_sha256'}
        history['context_sha256']=fixture_module.digest(json.dumps(body,sort_keys=True,ensure_ascii=False).encode())
        f.control.executor.submit(lambda:f.control._head().put('assistant_history',target,history)).result()
        calls=len(f.model.requests);before=copy.deepcopy(f.control.view()['budgets'])
        rejected,_=f.send(target,'继续讨论')
        self.assertEqual('failed',rejected['status'],rejected)
        self.assertEqual(calls,len(f.model.requests));self.assertEqual(before,f.control.view()['budgets'])
        from repo_maintainer.assistant_secrets import contains_credential
        self.assertFalse(contains_credential(f.control.view(),f.secret));f.assert_no_business_calls()
