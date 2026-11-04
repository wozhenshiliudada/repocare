"""All adapters with actual guarded sources and real Pi. Extraction fixture is synthetic."""
import copy
import json
import unittest
from unittest.mock import patch
import test_task_planning as fixture
import test_experience_memory as experience_fixture
import test_knowledge_memory as knowledge_fixture
import test_maintenance_memory as maintenance_fixture
from repo_maintainer.demo import DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import Policy,SafetyError,digest
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.source_memory import CATALOG,build_memory,canonical
from repo_maintainer import decision_relations as relations,structure_index as structure
from repo_maintainer.memory_items import ADAPTERS


class CompositeCatalogTests(unittest.TestCase):
    decision=fixture.TaskPlanningTests.decision
    record=fixture.TaskPlanningTests.record
    episode=experience_fixture.ExperienceTests.episode
    accept=experience_fixture.ExperienceTests.accept
    enable_experience=experience_fixture.ExperienceTests.enable
    proposal=knowledge_fixture.KnowledgeTests.proposal
    review=knowledge_fixture.KnowledgeTests.review
    note=maintenance_fixture.MaintenanceMemoryTests.note
    enable_notes=maintenance_fixture.MaintenanceMemoryTests.enable

    def open(self):
        if not hasattr(self,'documents'):return fixture.TaskPlanningTests.open(self)
        self.inner=PiProvider(('calculator.py',),self.model.url,('tests/test_calculator.py',),max_requests=50)
        self.inner.memory_root,self.inner.memory_index=self.documents,self.index
        self.inner.memory_relations,self.inner.memory_relations_context=self.relations,True
        self.session=UISession(self.source,Policy(('calculator.py',),(DEMO_TEST,),readonly_paths=('tests/test_calculator.py',)),
                              self.inner,self.root/'composite-state',structure_index=self.structure,structure_context_query='add')

    def setUp(self):
        fixture.TaskPlanningTests.setUp(self);self.session.close()
        self.documents=self.root/'documents';self.documents.mkdir();self.index=self.root/'rules.memory.sqlite3'
        classification='proposed' if self._testMethodName=='test_mixed_source_labels_are_visible_but_model_loading_still_hard_stops' else 'confirmed'
        texts={'new':'状态：确认\naddition add signed values.\n\nSupersedes: new -> old\n\n',
               'old':('状态：提议' if classification=='proposed' else '状态：确认')+'\naddition add earlier rule.\n',
               'case':'Historical addition add report; old approvals are not inherited.\n'}
        entries=[]
        for identity,text in texts.items():
            (self.documents/(identity+'.md')).write_text(text,encoding='utf-8')
            entries.append({'id':identity,'topic':'addition','path':identity+'.md','start_line':1,'end_line':len(text.splitlines()),
                            'kind':'case' if identity=='case' else 'document',
                            'classification':'historical' if identity=='case' else classification if identity=='old' else 'confirmed'})
        (self.documents/CATALOG).write_bytes(canonical({'schema_version':1,'entries':entries}));build_memory(self.documents,self.source,self.index)
        spec=self.root/'relations-spec.json';self.relations=self.root/'rules.memory-relations.json'
        spec.write_bytes(canonical({'schema_version':1,'relations':[{'id':'claim','newer_id':'new','older_id':'old','statement_line':4,
            'newer_sha256':digest((self.documents/'new.md').read_bytes()),'older_sha256':digest((self.documents/'old.md').read_bytes()),
            'statement_sha256':digest(b'Supersedes: new -> old')}]}))
        relations.register_relations(spec,self.documents,self.source,self.index,self.relations)
        self.structure=self.root/'structure-index';scope=self.root/'structure-spec.json'
        scope.write_bytes(canonical({'schema_version':1,'paths':['calculator.py']}))
        def engine(snapshot,selected,output):
            output.write_bytes(canonical({'schema_version':1,'engine':structure.ENGINE,'nodes':[{
                'id':'add','kind':'function','name':'add','qualified_name':'calculator.add','path':'calculator.py',
                'language':'python','start_line':1,'end_line':2}],'edges':[],'omitted_external_edges':0}))
        with patch.object(structure,'run_engine',side_effect=engine):structure.build_structure(scope,self.source,self.structure)
        self.open()

    def plan(self):return self.session._operate('plan',{'issue':'addition'})

    def test_all_six_sources_flow_to_actual_pi_same_budget_new_approval_and_report(self):
        run,_=self.episode();self.accept(run);self.enable_experience()
        self.model.reflection_candidate=copy.deepcopy(knowledge_fixture.NOTE);proposal=self.proposal(run);self.review(proposal['knowledge_id'])
        self.note(title='Follow addition',detail='Review add behavior against the current tests.');self.enable_notes()
        query=self.session.catalog.query('addition add',50)
        self.assertEqual(set(ADAPTERS),{r['adapter'] for r in query['items']})
        self.model.scenario='correct';plan=self.plan();e=self.session.plans[plan['plan_id']]['view']['planning_evidence'];load=e['memory_load']
        self.assertEqual(set(ADAPTERS),{r['adapter'] for r in load['items']});self.assertEqual('hard_stop',load['failure_policy'])
        old=next(r for r in load['items'] if r['object_id']=='SourceDocumentAdapter:old')
        self.assertEqual('confirmed',old['classification']);self.assertIsNone(old['review_state'])
        method=next(r for r in load['items'] if r['adapter']==ADAPTERS[5]);self.assertEqual('proposed',method['classification'])
        self.assertEqual('accepted',method['review_state']);self.assertEqual('procedural',method['knowledge_type'])
        count=len(self.model.requests);self.assertEqual('candidate_ready',self.decision(plan,'approve')['workflow_status'])
        self.assertEqual(count,len(self.model.requests));self.assertEqual(count,self.session.registry.budget()['requests_used'])
        report=self.session.state()['runs'][-1]['planning_origin']['provider_evidence']['memory_load']
        self.assertEqual(load,report);self.assertTrue(self.session.state()['runs'][-1]['source_unchanged'])

    def test_mixed_source_labels_are_visible_but_model_loading_still_hard_stops(self):
        query=self.session.catalog.query('addition',50);docs=[r for r in query['items'] if r['adapter']==ADAPTERS[0]]
        self.assertEqual({'confirmed','proposed'},{r['classification'] for r in docs})
        self.assertTrue(all(r['topic_group']['mixed_decisions'] for r in docs));budget=self.session.registry.budget()
        with self.assertRaises(SafetyError):self.plan()
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(0,len(self.model.requests))
