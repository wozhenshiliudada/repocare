"""A real original journal keeps competing reference summaries visible read-only."""
import json
import unittest
from test_knowledge_memory import KnowledgeTests, NOTE


class PeerRuntimeTests(unittest.TestCase):
    setUp=KnowledgeTests.setUp
    open=KnowledgeTests.open
    restart=KnowledgeTests.restart
    plan=KnowledgeTests.plan
    decision=KnowledgeTests.decision
    record=KnowledgeTests.record
    episode=KnowledgeTests.episode
    accept=KnowledgeTests.accept
    seed=KnowledgeTests.seed
    proposal=KnowledgeTests.proposal

    def test_top_one_exposes_other_type_summary_without_loading_or_approving_it(self):
        run,_=self.seed();note=self.proposal(run)['knowledge_id']
        self.session._operate('maintenance_note',{'note_id':None,'revision':self.session.maintenance.revision(),
            'title':NOTE['title'],'detail':'Use absolute values for every operand. This is an unverified operator claim.',
            'status':'open','priority':2})
        before=self.session.registry.path.read_bytes();budget=self.session.registry.budget()
        tasks=self.session.registry.load('pi_task');count=len(self.model.requests)
        query=self.session.catalog.query(NOTE['title'],limit=1)
        self.assertEqual(1,query['returned_items'])
        group=next(g for g in query['topic_peers']['groups'] if g['topic']==NOTE['title'])
        combined=query['items']+group['peers'];text=json.dumps(combined)
        self.assertIn('Use absolute values for every operand',text)
        self.assertIn('failed original negative operand tests',text)
        learned=next(r for r in combined if r['adapter']=='KnowledgeEntryAdapter')
        self.assertEqual('awaiting_review',learned['review_state']);self.assertEqual('proposed',learned['classification'])
        self.assertTrue(group['cross_type']);self.assertFalse(query['topic_peers']['semantic_conflict_detection'])
        self.assertTrue(all(not r['in_context'] and not r['runtime_authorized'] for r in combined))
        self.assertEqual(before,self.session.registry.path.read_bytes());self.assertEqual(tasks,self.session.registry.load('pi_task'))
        self.assertEqual(budget,self.session.registry.budget());self.assertEqual(count,len(self.model.requests))
        self.assertEqual('pending',self.session.registry.load('repository_knowledge')[note]['review'])
