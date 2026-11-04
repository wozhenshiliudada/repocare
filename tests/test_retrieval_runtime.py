"""Real Pi/Git/controller with local synthetic language-model transport."""
import copy
import unittest
import test_task_planning as planning
import test_experience_memory as experience
import test_knowledge_memory as knowledge
import test_catalog_composite as composite
from unittest.mock import patch
from repo_maintainer.models import SafetyError
from repo_maintainer import ui_backup


class RetrievalRuntimeTests(unittest.TestCase):
    setUp=planning.TaskPlanningTests.setUp
    open=planning.TaskPlanningTests.open
    restart=planning.TaskPlanningTests.restart
    plan=planning.TaskPlanningTests.plan
    decision=planning.TaskPlanningTests.decision
    record=planning.TaskPlanningTests.record
    episode=experience.ExperienceTests.episode
    accept=experience.ExperienceTests.accept
    enable=experience.ExperienceTests.enable
    envelope=experience.ExperienceTests.envelope
    proposal=knowledge.KnowledgeTests.proposal
    review=knowledge.KnowledgeTests.review

    def test_full_procedure_step_query_is_readonly_old_pins_survive_restart_new_approval_passes(self):
        run,_=self.episode();self.accept(run)
        self.model.reflection_candidate=copy.deepcopy(knowledge.NOTE)
        self.model.reflection_candidate['steps'].append('Check CircuitBreakerInvariant before selecting the candidate.')
        note=self.proposal(run)['knowledge_id'];self.review(note);self.enable()
        self.model.scenario='correct';plan=self.plan();task_id=self.record()['task_id']
        pins={kind:self.session.registry.load(kind) for kind in ('experience_task','knowledge_task','retrieval_task')}
        db=self.session.registry.path.read_bytes();budget=self.session.registry.budget();count=len(self.model.requests)
        result=self.session.catalog.query('circuit breaker invariant')
        row=next(r for r in result['items'] if r['object_id']=='KnowledgeEntryAdapter:'+note)
        self.assertEqual(['breaker','circuit','invariant'],row['matched_terms'])
        self.assertEqual('accepted',row['review_state']);self.assertFalse(row['runtime_authorized'])
        self.assertEqual(db,self.session.registry.path.read_bytes());self.assertEqual(budget,self.session.registry.budget())
        self.assertEqual(count,len(self.model.requests));self.assertEqual([],self.session.catalog.query('the and for this')['items'])
        self.restart()
        for kind,value in pins.items():self.assertEqual(value,self.session.registry.load(kind))
        old=self.session.state()['plans'][-1]
        self.session._operate('claim_plan',{'plan_id':old['id'],'revision':old['revision']})
        self.assertEqual('candidate_ready',self.decision(plan,'approve')['workflow_status'])
        self.assertEqual(count,len(self.model.requests));self.assertEqual(budget,self.session.registry.budget())
        receipt=self.session.registry.load('retrieval_task')[task_id]['channels']
        self.assertEqual({'experience','knowledge'},set(receipt));self.assertTrue(all(not v['runtime_authorized'] for v in receipt.values()))
        # The original archive supports optional settings/provenance as historical metadata only.
        self.session.retrieval.configure(False,self.session.retrieval.revision())
        from contextlib import closing
        with closing(ui_backup.readonly(self.session.registry.path)) as connection:
            snapshot=ui_backup.snapshot(connection)
        self.assertIn('retrieval_settings',{r['kind'] for r in snapshot['objects']})
        self.assertIn('retrieval_task',{r['kind'] for r in snapshot['objects']})

    def test_rejected_knowledge_not_selected_and_revocation_blocks_old_approval_without_refund(self):
        run,_=self.episode();self.accept(run);self.model.reflection_candidate=copy.deepcopy(knowledge.NOTE)
        note=self.proposal(run)['knowledge_id'];self.review(note);self.enable();self.model.scenario='correct';plan=self.plan()
        budget=self.session.registry.budget();count=len(self.model.requests);pin=self.session.registry.load('knowledge_task')
        self.review(note,'rejected');self.assertFalse(self.session.knowledge.search(planning.ISSUE)['hits'])
        self.assertEqual(pin,self.session.registry.load('knowledge_task'))
        with self.assertRaises(SafetyError):self.decision(plan,'approve')
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(count,len(self.model.requests))


class CatalogSourcePublicationTests(unittest.TestCase):
    setUp=composite.CompositeCatalogTests.setUp
    open=composite.CompositeCatalogTests.open

    def test_source_excerpts_are_searchable_after_fts_normalization_miss(self):
        before=self.session.registry.path.read_bytes()
        result=self.session.catalog.query('ｓｉｇｎｅｄ')
        row=next(r for r in result['items'] if r['object_id']=='SourceDocumentAdapter:new')
        self.assertEqual(['signed'],row['matched_terms']);self.assertEqual('verified_record_bm25',row['match_origin'])
        self.assertEqual(before,self.session.registry.path.read_bytes());self.assertEqual(0,len(self.model.requests))

    def test_late_source_failure_never_publishes_previously_collected_rows_as_valid(self):
        before=self.session.registry.path.read_bytes()
        with patch.object(self.session.memory,'query_rows',side_effect=SafetyError('source changed during query')):
            result=self.session.catalog.query('addition')
        published = [*result['items'], *(row for group in result['topic_peers']['groups'] for row in group['peers'])]
        self.assertFalse(any(r['adapter'] in {'SourceDocumentAdapter','RunCaseAdapter','DecisionClaimAdapter'} for r in published))
        self.assertTrue(any('source changed during query' in r['reason'] for r in result['excluded']))
        self.assertEqual(before,self.session.registry.path.read_bytes());self.assertEqual(0,len(self.model.requests))
