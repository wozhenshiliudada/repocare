import copy
import json
from types import SimpleNamespace
import unittest

from repo_maintainer.behavior_validation import make_contract
from repo_maintainer.models import SafetyError,digest
from repo_maintainer.pi_behavior import planning_context,bound_expectations
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.task_session import repository_identity


class BehaviorContextTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        self.repo=SimpleNamespace(root=Path.cwd(),common_dir=Path.cwd()/'.git',head='a'*40)
        self.issue='明确的加法需求'
        self.contract=make_contract(repository_id=repository_identity(self.repo.root,self.repo.common_dir),
            base_sha=self.repo.head,request_sha256=digest(self.issue.encode()),requirement='加法',
            cases=[{'id':'sum','module':'calc.py','callable':'add','args':[1.0,2],
                    'kwargs':{},'expect':{'kind':'json','value':3.0},'missing_behavior':False}])
        self.tests=(('repocare:behavior-v1',self.contract['contract_sha256']),)

    def context(self,contract=None,**changes):
        values=dict(repository=self.repo,issue=self.issue,tests=self.tests,paths={'calc.py'})
        values.update(changes)
        return planning_context(self.contract if contract is None else contract,**values)

    def test_exact_bytes_keep_unicode_and_float_pins(self):
        value=self.context()
        self.assertEqual(value['contract_sha256'],digest(value['body_json'].encode()))
        self.assertIn('1.0',value['body_json'])
        self.assertIn('加法',value['body_json'])
        self.assertFalse(json.loads(value['body_json'])['runtime_authorized'])

    def test_scope_request_command_and_path_drift_rejected(self):
        for changes in ({'issue':'changed'},{'paths':set()},{'tests':()},
                        {'tests':self.tests+self.tests},
                        {'tests':(('repocare:behavior-v1','f'*64),)}):
            with self.subTest(changes=changes),self.assertRaises(SafetyError):self.context(**changes)
        bad=copy.deepcopy(self.contract);bad['cases'][0]['expect']['value']=10
        with self.assertRaises(SafetyError):self.context(bad)

    def test_marker_without_expectations_rejected_and_legacy_unchanged(self):
        self.assertIsNone(planning_context(None,self.repo,self.issue,(('python','-m','unittest'),),set()))
        with self.assertRaises(SafetyError):planning_context(None,self.repo,self.issue,self.tests,{'calc.py'})

    def test_candidate_uses_trusted_original_identity_without_accepting_other_repository(self):
        candidate=SimpleNamespace(root=self.repo.root/'candidate',common_dir=self.repo.common_dir,head=self.repo.head)
        with self.assertRaises(SafetyError):self.context(repository=candidate)
        self.assertEqual(self.context(),self.context(repository=candidate,source=self.repo))
        for changed in (SimpleNamespace(root=candidate.root,common_dir=self.repo.root/'other.git',head=self.repo.head),
                        SimpleNamespace(root=candidate.root,common_dir=self.repo.common_dir,head='b'*40)):
            with self.assertRaises(SafetyError):self.context(repository=changed,source=self.repo)

    def test_binding_is_private_restored_and_cannot_be_overwritten(self):
        p=PiProvider(('calc.py',),'http://127.0.0.1:1')
        with self.assertRaisesRegex(RuntimeError,'stop'):
            with bound_expectations(p,self.contract):
                self.assertEqual(self.contract,p._behavior_contract)
                self.assertIsNot(self.contract,p._behavior_contract)
                with self.assertRaises(SafetyError):
                    with bound_expectations(p,self.contract):pass
                raise RuntimeError('stop')
        self.assertIsNone(p._behavior_contract)
        self.assertIsNone(p._behavior_source)
