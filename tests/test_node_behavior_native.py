"""Freeze the old Python SDK rejection meaning; Node Native is explicit v3."""
import copy
import unittest
from unittest.mock import patch

import test_validation_native as native
from repo_maintainer.models import SafetyError
from repo_maintainer.task_session import validate_transcript, VALIDATION_DATA_SCHEMA


class NodeBehaviorNativeTests(unittest.TestCase):
    def fixture(self):
        f=native.ValidationNativeTests();f.setUp();self.addCleanup(f.doCleanups)
        f.task=copy.deepcopy(f.task)
        f.task['context']['paths'].append('calculator.js')
        return f

    def test_original_in_scope_js_schema_rejection_remains_valid_and_cannot_relabel_protocol(self):
        f=self.fixture();rejected=native.proposal();rejected['cases'][0]['module']='calculator.js'
        result=f.create(rejected=rejected)
        kwargs={'cwd':f.directory/'workspace','leaf':result['leaf_id'],'session_id':result['session_id']}
        validate_transcript(result['raw'].encode(),context=f.task['context'],**kwargs)
        new={**f.task['context'],'schema_version':3,'validation_schema':VALIDATION_DATA_SCHEMA}
        with self.assertRaises(SafetyError):
            validate_transcript(result['raw'].encode(),context=new,**kwargs)

    def test_new_node_native_requires_version_three_and_cannot_downgrade_to_old_python(self):
        f=self.fixture()
        f.task['context'].update(schema_version=3,validation_schema=VALIDATION_DATA_SCHEMA)
        proposal=native.proposal();proposal['cases'][0]['module']='calculator.js'
        result=f.create(proposal)
        kwargs={'cwd':f.directory/'workspace','leaf':result['leaf_id'],'session_id':result['session_id']}
        validate_transcript(result['raw'].encode(),context=f.task['context'],**kwargs)
        old={k:v for k,v in f.task['context'].items() if k!='validation_schema'};old['schema_version']=2
        with self.assertRaises(SafetyError):
            validate_transcript(result['raw'].encode(),context=old,**kwargs)

    def test_old_proposal_task_keeps_its_original_context_for_reconciliation(self):
        # Use the same production planner and original source/identity. A Node
        # path in an old readonly scope must not silently upgrade its protocol.
        import test_validation_proposal as proposal_fixture
        from repo_maintainer.validation_proposal import source_packet, ValidationPlanner
        from repo_maintainer.pi_provider import PiProvider
        from repo_maintainer.task_session import fingerprint
        f=proposal_fixture.ValidationProposalTests();f.setUp();self.addCleanup(f.doCleanups)
        f.repo.command('config','user.name','Native Legacy Fixture')
        f.repo.command('config','user.email','native-legacy@example.invalid')
        (f.repo.root/'calculator.js').write_text('export const add=(a,b)=>a+b;')
        f.repo.command('add','calculator.js');f.repo.command('commit','-m','legacy readonly mixed source')
        provider=PiProvider((),f.model.url,('calculator.py','calculator.js'),max_requests=6)
        source=source_packet(f.repo,f.issue,['.'],['calculator.py','calculator.js'],{'scope':'old original source'})
        planner=ValidationPlanner(f.registry,f.repo,provider,source,lambda:None)
        _,fresh=planner.current_scope(f.issue)
        self.assertEqual(3,fresh['schema_version'])
        old={k:v for k,v in fresh.items() if k!='validation_schema'};old['schema_version']=2
        identity=fingerprint({'source':source,'provider_identity':provider.task_identity()[1]})
        # Allocate through the original store with the original legacy context.
        scope,_=planner.current_scope(f.issue);scope['context_sha256']=fingerprint(old)
        task=planner.store.allocate(scope,old)
        f.registry.put('validation_proposal',identity,{'source':source,'task_id':task['task_id']})
        self.assertEqual((scope,old),planner.current_scope(f.issue))
