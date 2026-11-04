"""Actual archived Pi history transfers only as source context into an independent target journal."""
import json
import unittest
import test_task_planning as planning
import test_experience_memory as experience_fixture
import test_knowledge_memory as knowledge_fixture
import test_maintenance_memory as maintenance_fixture
from repo_maintainer.demo import create_fixture,DEMO_TEST
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import Policy,SafetyError,digest
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.ui_backup import backup_ui_history,encoded
from repo_maintainer.memory_migration import preview_migration,migrate_memory
from repo_maintainer.memory_collection import compose_memory
from repo_maintainer.source_memory import CATALOG,build_memory


class MigrationRuntimeTests(unittest.TestCase):
    setUp=planning.TaskPlanningTests.setUp
    open=planning.TaskPlanningTests.open
    plan=planning.TaskPlanningTests.plan
    decision=planning.TaskPlanningTests.decision
    record=planning.TaskPlanningTests.record
    episode=experience_fixture.ExperienceTests.episode
    accept=experience_fixture.ExperienceTests.accept
    seed=knowledge_fixture.KnowledgeTests.seed
    proposal=knowledge_fixture.KnowledgeTests.proposal
    review=knowledge_fixture.KnowledgeTests.review
    note=maintenance_fixture.MaintenanceMemoryTests.note

    def test_same_repository_binding_mismatch_stops_without_new_budget_or_native_replacement(self):
        run,old_task=self.episode();self.accept(run);budget=self.session.registry.budget();self.session.close()
        runs=self.root/'state';database=runs/'ui-state'/'registry.sqlite3';before=database.read_bytes()
        native={p:digest(p.read_bytes()) for p in (runs/'task-sessions').glob('*/sessions/*.jsonl')}
        receipt=backup_ui_history(runs);preview=preview_migration('ui_backup',runs,receipt['backup_id'])
        row=next(r for r in preview['items'] if r['kind']=='repository_experience')
        selection=self.root/'selection.json';selection.write_bytes(encoded({'schema_version':1,'source':preview['source'],
            'items':[{k:row[k] for k in ('kind','id','item_sha256')}|{'name':'history','topic':'addition'}]}))
        root=self.root/'corpus';index=self.root/'history.memory.sqlite3';migrate_memory(selection,root,self.source,index)
        self.assertEqual(before,database.read_bytes());count=len(self.model.requests)
        provider=PiProvider(('calculator.py',),self.model.url,('tests/test_calculator.py',),max_requests=50)
        provider.memory_root,provider.memory_index=root,index
        with self.assertRaises(SafetyError):UISession(self.source,self.session.policy,provider,runs)
        self.assertEqual(before,database.read_bytes());self.assertEqual(native,{p:digest(p.read_bytes()) for p in native})
        self.open();self.assertEqual(budget,self.session.registry.budget());self.assertEqual(count,len(self.model.requests))
        self.assertIn(old_task,self.session.registry.load('pi_task'))

    def test_real_archive_new_target_new_native_and_approval_green_without_transferring_budget_or_reviews(self):
        run,old_task=self.seed();knowledge=self.proposal(run)['knowledge_id'];self.review(knowledge);self.note()
        old_budget=self.session.registry.budget();self.session.close();runs=self.root/'state'
        database=runs/'ui-state'/'registry.sqlite3';original=database.read_bytes()
        native={p:digest(p.read_bytes()) for p in (runs/'task-sessions').glob('*/sessions/*.jsonl')}
        receipt=backup_ui_history(runs);preview=preview_migration('ui_backup',runs,receipt['backup_id'])
        selected=[r for r in preview['items'] if r['kind'] in {'repository_knowledge','repository_experience','maintenance_note'}]
        self.assertEqual(3,len(selected),preview)
        selection=self.root/'selection.json';selection.write_bytes(encoded({'schema_version':1,'source':preview['source'],
            'items':[{k:r[k] for k in ('kind','id','item_sha256')}|{'name':f'history{n}','topic':'addition'} for n,r in enumerate(selected)]}))
        target_repo=create_fixture(self.root/'new-target');root=self.root/'corpus';index=self.root/'imported.memory.sqlite3'
        migrate_memory(selection,root,target_repo,index);self.assertEqual(original,database.read_bytes())
        docs=self.root/'curated';docs.mkdir();(docs/'rule.md').write_text('状态：确认\nAddition must preserve negative operand signs.\n',encoding='utf-8')
        (docs/CATALOG).write_bytes(encoded({'schema_version':1,'entries':[{'id':'rule','topic':'addition','path':'rule.md',
            'start_line':1,'end_line':2,'kind':'document','classification':'confirmed'}]}))
        old_index=self.root/'old-doc.memory.sqlite3';build_memory(docs,self.source,old_index);old_index_bytes=old_index.read_bytes()
        compose_selection=self.root/'compose.json';migration_entries=json.loads((root/CATALOG).read_bytes())['entries']
        compose_selection.write_bytes(encoded({'schema_version':1,'members':[{'id':'history','source_root':str(root)},{'id':'docs','source_root':str(docs)}],
            'selections':[{'id':r['id'],'member_id':'history','entry_id':r['id']} for r in migration_entries]
                         +[{'id':'rule','member_id':'docs','entry_id':'rule'}]}))
        combined=self.root/'combined';combined_index=self.root/'combined.memory.sqlite3'
        compose_memory(compose_selection,combined,target_repo,combined_index)
        provider=PiProvider(('calculator.py',),self.model.url,('tests/test_calculator.py',),max_requests=50)
        provider.memory_root,provider.memory_index=combined,combined_index
        target=UISession(target_repo,Policy(('calculator.py',),(DEMO_TEST,),readonly_paths=('tests/test_calculator.py',)),provider,self.root/'new-state')
        try:
            for kind in ('pi_task','repository_knowledge','repository_experience','maintenance_note'):
                self.assertEqual({},target.registry.load(kind))
            self.assertEqual(0,target.registry.budget()['requests_used']);before=len(self.model.requests)
            self.model.scenario='correct';plan=target._operate('plan',{'issue':'Addition negative operands'})
            saved=target.plans[plan['plan_id']]['view'];load=saved['planning_evidence']['memory_load']
            self.assertNotEqual(old_task,load['task_id']);self.assertTrue(load['items'])
            self.assertEqual({'confirmed','historical'},{i['classification'] for i in load['items']})
            self.assertTrue(all(i['review_state'] is None for i in load['items']))
            self.assertEqual('awaiting_approval',saved['status']);self.assertFalse(target.state()['runs'])
            self.assertEqual(2,len(self.model.requests)-before);self.assertEqual(2,target.registry.budget()['requests_used'])
            manifest=runs/'ui-backups'/receipt['backup_id']/'manifest.json';raw=manifest.read_bytes();manifest.write_bytes(raw+b' ')
            with self.assertRaises(SafetyError):target._operate('decision',{**plan,'decision':'approve'})
            self.assertEqual(before+2,len(self.model.requests));self.assertFalse(target.state()['runs'])
            manifest.write_bytes(raw)
            result=target._operate('decision',{**plan,'decision':'approve'});self.assertEqual('candidate_ready',result['workflow_status'])
            self.assertEqual(before+2,len(self.model.requests));self.assertEqual(original,database.read_bytes())
            self.assertEqual(old_index_bytes,old_index.read_bytes())
            self.assertEqual(native,{p:digest(p.read_bytes()) for p in native})
            self.assertEqual(3,old_budget['requests_used']);self.assertNotIn(knowledge,target.registry.load('repository_knowledge'))
            self.assertNotIn(run['run_id'],target.registry.load('repository_experience'))
            self.assertTrue(target.state()['runs'][-1]['source_unchanged'])
        finally:target.close()
