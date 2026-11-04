"""Actual readonly Pi phase and original ledger, synthetic provider replies only."""
from pathlib import Path
import tempfile
import unittest

from repo_maintainer.demo import create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.ui_registry import UIRegistry,provider_binding
from repo_maintainer.validation_proposal import source_packet,ValidationProposal
from test_task_planning import SyntheticModel


class ValidationProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=RepositoryGateway(create_fixture(self.root/'source'))
        self.model=SyntheticModel();self.addCleanup(self.model.close)
        self.provider=PiProvider((),self.model.url,('calculator.py',),max_requests=6)
        self.binding={'repository':str(self.repo.root),'provider':provider_binding(self.provider)}
        self.registry=UIRegistry(self.root/'original-ledger',self.binding);self.addCleanup(lambda:self.registry.close())
        self.issue='add(2,3) must return 5'
        self.source=source_packet(self.repo,self.issue,['.'],['calculator.py'],{'original_rule':'confirmed'})
        self.calls=0
        def guard():self.calls+=1
        self.guard=guard
        self.service=ValidationProposal(self.registry,self.repo,self.provider,self.source,guard,'session')
        self.proposal={'requirement':self.issue,'rationale':'Explicit expected sum','limitations':'Python module only',
            'clarification':False,'cases':[{'id':'sum','module':'calculator.py','callable':'add','args':[2,3],
                'kwargs':{},'expect':{'kind':'json','value':5},'missing_behavior':False}]}
        self.model.schema_calls=[('propose_validation',self.proposal)]

    def test_real_original_native_proposal_review_and_duplicate_without_execution(self):
        before=self.repo.snapshot();budget=self.registry.budget()
        preview=self.service.preview();self.assertEqual(budget,self.registry.budget());self.assertEqual([],self.model.requests)
        result=self.service.propose(preview['revision'])
        self.assertEqual('awaiting_validation_review',result['status'])
        self.assertFalse(result['runtime_authorized']);self.assertEqual(1,len(self.model.requests))
        self.assertEqual(['propose_validation','read_source'],sorted(t['function']['name'] for t in self.model.requests[0]['tools']))
        self.assertEqual(before,self.repo.snapshot())
        self.assertEqual(1,self.registry.load('pi_task')[result['task_id']]['revision'])
        current=self.registry.budget();self.assertEqual((1,0,5),tuple(current[k] for k in
            ('requests_used','requests_reserved_unknown','requests_remaining')))
        self.assertEqual(result,self.service.propose(preview['revision']));self.assertEqual(1,len(self.model.requests))
        approved=self.service.review(self.service.revision(result),'accept')
        self.assertEqual('accepted',approved['status']);self.assertEqual(result['contract'],self.service.accepted())
        self.assertEqual(before,self.repo.snapshot());self.assertEqual(current,self.registry.budget())

    def test_clarification_does_not_manufacture_contract_or_acceptance(self):
        self.model.schema_calls=[('propose_validation',{**self.proposal,'clarification':True,'cases':[],
            'limitations':'What result should unspecified behavior return?'})]
        result=self.service.propose(self.service.preview()['revision'])
        self.assertEqual('clarification_needed',result['status']);self.assertIsNone(result['contract'])
        with self.assertRaises(SafetyError):self.service.review(self.service.revision(result),'accept')
        self.assertIsNone(self.service.accepted())

    def test_raw_float_proposal_preserves_contract_and_original_native(self):
        import json
        raw=json.dumps([{**self.proposal['cases'][0],'args':[2.0,3.0],'expect':{'kind':'json','value':5.0}}])
        proposal={k:v for k,v in self.proposal.items() if k!='cases'};proposal['cases_json']=raw
        self.model.schema_calls=[('propose_validation',proposal)]
        result=self.service.propose(self.service.preview()['revision'])
        self.assertIs(type(result['contract']['cases'][0]['expect']['value']),float)
        self.assertEqual(raw,result['proposal']['cases_json'])

    def test_stale_preview_stops_before_model_reservation(self):
        preview=self.service.preview();attempt,_=self.registry.reserve(1)
        self.registry.settle(attempt,{'requests':1},success=True)
        with self.assertRaises(SafetyError):self.service.propose(preview['revision'])
        self.assertEqual([],self.model.requests);self.assertEqual({},self.registry.load('validation_proposal'))

    def test_partial_unknown_blocks_even_with_remaining_budget_before_intent_or_native(self):
        self.registry.reserve(1)
        preview=self.service.preview();before=self.registry.budget()
        self.assertEqual(5,before['requests_remaining'])
        with self.assertRaisesRegex(SafetyError,'原消费未知'):
            self.service.propose(preview['revision'])
        self.assertEqual(before,self.registry.budget());self.assertEqual([],self.model.requests)
        self.assertEqual({},self.registry.load('validation_proposal'));self.assertEqual({},self.registry.load('pi_task'))
