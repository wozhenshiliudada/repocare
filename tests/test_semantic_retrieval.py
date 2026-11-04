"""Optional retrieval authority and settings boundaries; numerical encoder tested separately."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from repo_maintainer import semantic_retrieval as semantic
from repo_maintainer.local_ui import validate_payload
from repo_maintainer.models import SafetyError
from repo_maintainer.task_session import fingerprint


class Registry:
    def __init__(self):self.rows={}
    def load(self,kind):return copy.deepcopy(self.rows.get(kind,{}))
    def put(self,kind,key,value):self.rows.setdefault(kind,{})[key]=copy.deepcopy(value)


class SemanticControlTests(unittest.TestCase):
    def setUp(self):
        self.session=SimpleNamespace(registry=Registry(),token='synthetic-only')
        self.session.retrieval=semantic.SessionRetrieval(self.session)

    def test_default_off_does_not_import_encoder_read_model_or_write_registry(self):
        with patch.object(semantic.LocalEncoder,'_load',side_effect=AssertionError('must remain lazy')):
            result,cosines,strategy=semantic.session_rank(self.session,'锁',{'a':'会话锁'})
        self.assertEqual('a',result[0][1]);self.assertFalse(cosines);self.assertEqual(semantic.STRATEGY,strategy)
        self.assertEqual({},self.session.registry.rows);self.assertFalse(self.session.retrieval.view()['enabled'])

    def test_no_registration_or_stale_revision_cannot_enable(self):
        control=self.session.retrieval
        with self.assertRaises(SafetyError):control.configure(True,control.revision())
        with self.assertRaises(SafetyError):control.configure(False,'0'*64)
        self.assertFalse(self.session.registry.rows)

    def test_explicit_disable_is_allowed_when_registered_model_is_lost(self):
        self.session.registry.put('retrieval_settings','default',{'schema_version':1,'enabled':True,'model_manifest_sha256':'a'*64})
        with self.assertRaises(SafetyError):semantic.session_rank(self.session,'foo',{'a':'foo'})
        self.session.retrieval.configure(False,self.session.retrieval.revision())
        self.assertEqual('a',semantic.session_rank(self.session,'foo',{'a':'foo'})[0][0][1])

    def test_registered_model_stays_off_until_explicit_configuration(self):
        package=SimpleNamespace(sha='a'*64,checked=lambda:None)
        control=semantic.SessionRetrieval(self.session,package);self.session.retrieval=control
        self.assertFalse(control.settings()['enabled']);control.configure(True,control.revision())
        with patch.object(control.encoder,'rank',return_value=([(1,'a')],{'a':.7})):
            self.assertEqual(semantic.HYBRID,control.rank('foo',{'a':'bar'})[2])
        control.package=SimpleNamespace(sha='b'*64)
        with self.assertRaises(SafetyError):control.rank('foo',{'a':'bar'})

    def test_selection_receipt_requires_existing_task_and_cannot_be_replaced(self):
        task='1'*32;selection={'hits':[{'id':'e'}],'retrieval':semantic.STRATEGY}
        with self.assertRaises(SafetyError):semantic.record_selection(self.session,task,'issue','knowledge',selection)
        self.session.registry.put('pi_task',task,{'task_id':task})
        semantic.record_selection(self.session,task,'issue','knowledge',selection)
        receipt=self.session.registry.load('retrieval_task')[task]
        self.assertEqual(fingerprint(selection['hits']),receipt['channels']['knowledge']['selected_hits_sha256'])
        self.assertFalse(receipt['channels']['knowledge']['runtime_authorized'])
        with self.assertRaises(SafetyError):semantic.record_selection(self.session,task,'issue','knowledge',selection)
        with self.assertRaises(SafetyError):semantic.record_selection(self.session,task,'other','experience',selection)

    def test_fusion_retains_identifier_matches_and_negative_abstention(self):
        self.assertEqual([],semantic.fuse([],[]))
        fused=semantic.fuse([(1,'exact'),(.5,'both')],[(.9,'both'),(.8,'semantic')])
        self.assertEqual('both',fused[0][1]);self.assertEqual({'exact','both','semantic'},{i for _,i in fused})

    def test_only_pinned_data_manifest_accepted_and_no_arbitrary_model_loading(self):
        from repo_maintainer.ui_backup import encoded
        with tempfile.TemporaryDirectory() as root:
            file=Path(root)/'manifest.json'
            for value in ({},[],{'schema_version':1,'repository':'custom','files':[]},
                {'schema_version':1,'repository':semantic.MODEL,'revision':semantic.REVISION,'license':'Apache-2.0',
                 'files':[{'path':'../../evil.onnx','bytes':1,'sha256':'a'*64}]*2}):
                file.write_bytes(encoded(value))
                with self.assertRaises(SafetyError):semantic.ModelPackage(file)

    def test_retrieval_payload_cannot_grant_policy_or_change_budget(self):
        good={'request_id':'1'*32,'enabled':False,'revision':'a'*64}
        self.assertEqual({'enabled':False,'revision':'a'*64},validate_payload('retrieval_settings',good))
        for extra in ({'request_budget':999},{'allowed_paths':['*']},{'model_manifest':'other'}):
            with self.assertRaises(SafetyError):validate_payload('retrieval_settings',{**good,**extra})
