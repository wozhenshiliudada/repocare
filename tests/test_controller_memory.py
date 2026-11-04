"""Original controllers + real Pi SDK, local synthetic SSE, no paid provider."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import test_issue_execution as issues
import test_issue_intake as intake
import test_monitor_execution as monitors
from repo_maintainer.issue_intake import IssueIntake
from repo_maintainer.models import SafetyError
from repo_maintainer.workflow import cleanup_run


class JournalViewTests(unittest.TestCase):
    def setUp(self):
        from repo_maintainer.controller_memory import JournalView
        from repo_maintainer.ui_registry import UIRegistry
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.registry = UIRegistry(Path(temporary.name)/'state', {'provider':{'request_budget':10}})
        self.addCleanup(self.registry.close)
        self.task, self.foreign = 'a'*32, 'b'*32
        self.registry.put('pi_task_link', 'run:one', {'task_id':self.task})
        self.registry.put('pi_task_link', 'run:two', {'task_id':self.foreign})
        self.view = JournalView(self.registry,self.registry,SimpleNamespace(identity=self.task),'one')

    def test_foreign_task_pins_and_compaction_intents_are_not_readable_or_writable(self):
        self.registry.put('pi_compaction_intent','other',{'task_id':self.foreign,'status':'pending'})
        self.assertEqual({},self.view.load('pi_compaction_intent'))
        self.assertEqual({'run:one':{'task_id':self.task}},self.view.load('pi_task_link'))
        with self.assertRaises(SafetyError):
            self.view.put('pi_compaction_intent','other',{'task_id':self.foreign,'status':'abandoned'})
        self.assertEqual('pending',self.registry.load('pi_compaction_intent')['other']['status'])

    def test_same_database_memory_transaction_does_not_open_a_nested_writer_or_change_budget(self):
        before = self.registry.budget()
        with self.view.transaction() as db:
            self.view.put('pi_compaction_intent','own',{'task_id':self.task,'status':'abandoned'},db)
        self.assertEqual('abandoned',self.view.load('pi_compaction_intent')['own']['status'])
        self.assertEqual(before,self.registry.budget())
        self.assertFalse(hasattr(self.view,'reserve'))
        with self.assertRaises(SafetyError):self.view.put('attempt','forged',{})

    def test_corrupt_historical_journal_is_rejected_without_creating_or_repairing_it(self):
        from repo_maintainer.controller_memory import ReadOnlyJournal
        root = self.registry.root.parent/'damaged'
        (root/'ui-state').mkdir(parents=True)
        path = root/'ui-state/registry.sqlite3'
        path.write_bytes(b'broken original sqlite journal')
        before = path.read_bytes()
        with self.assertRaisesRegex(SafetyError, 'without reconstruction'):
            ReadOnlyJournal(root).load('run')
        self.assertEqual(before,path.read_bytes())

    def test_malformed_links_and_pins_are_filtered_readonly_without_foreign_ownership(self):
        for link in ([], {'task_id':[]}, {'task_id':self.task,'extra':True}, {'task_id':'not-a-task'}):
            with self.subTest(link=link):
                self.registry.put('pi_task_link','run:one',link)
                self.registry.put('pi_context_policy',self.task,{'task_id':[]})
                before=self.registry.path.read_bytes();budget=self.registry.budget()
                self.assertEqual({},self.view.load('pi_task_link'))
                self.assertEqual({},self.view.load('pi_context_policy'))
                self.assertEqual(before,self.registry.path.read_bytes())
                self.assertEqual(budget,self.registry.budget())
                with self.assertRaises(SafetyError):
                    self.view.put('pi_compaction_intent','bad',{'task_id':[]})
                with self.assertRaises(SafetyError):
                    self.view.put('pi_compaction_intent','foreign',{'task_id':self.foreign})
                self.assertEqual(before,self.registry.path.read_bytes())

    def test_finished_or_planned_run_cannot_fall_back_to_preplan_baseline(self):
        from repo_maintainer.controller_memory import run_base
        pending = {'status':'inspecting','source_before':{'head':'a'*40},'rounds':[]}
        self.assertEqual('a'*40,run_base(pending))
        for extra in ({'finished_at':'finished'},{'plan':{}},{'rounds':[{}]},{'status':'failed'}):
            with self.subTest(extra=extra),self.assertRaises(SafetyError):run_base({**pending,**extra})


class StandaloneMemoryTests(unittest.TestCase):
    import test_experience_memory as fixture
    setUp = fixture.ExperienceTests.setUp
    open = fixture.ExperienceTests.open
    plan = fixture.ExperienceTests.plan
    decision = fixture.ExperienceTests.decision
    record = fixture.ExperienceTests.record
    episode = fixture.ExperienceTests.episode

    def test_original_native_readonly_context_is_preserved_and_tampering_blocks_without_charge(self):
        run, task_id = self.episode()
        identity = run['run_id']
        memory = self.session.experience
        entry = self.session.registry.load('repository_experience')[identity]
        self.assertEqual({'calculator.py','tests/test_calculator.py'},set(entry['payload']['source_files']))
        memory.validate(identity,entry,applicable=True)
        before = self.session.registry.budget()
        task = self.session.registry.load('pi_task')[task_id]
        forged = copy.deepcopy(task)
        forged['context']['paths'].append('unapproved.py')
        self.session.registry.put('pi_task',task_id,forged)
        try:
            with self.assertRaises(SafetyError):memory.validate(identity,entry,applicable=True)
        finally:self.session.registry.put('pi_task',task_id,task)
        self.assertEqual(before,self.session.registry.budget())


def envelope(model):
    for request in reversed(model.requests):
        for message in reversed(request['messages']):
            if message['role'] == 'user':
                text = message['content']
                value = json.loads(text if isinstance(text, str) else text[0]['text'])
                if 'repository_experience' in value: return value
    raise AssertionError('No actual memory-bearing model request')


class IssueMemoryTests(unittest.TestCase):
    setUp = issues.IssueExecutionTests.setUp
    provider = issues.IssueExecutionTests.provider
    service = issues.IssueExecutionTests.service
    run_issue = issues.IssueExecutionTests.run_issue

    def next_issue(self):
        self.github.issue['id'] += 1
        self.github.issue['number'] += 1
        self.github.issue['html_url'] = 'https://github.com/example/project/issues/'+str(self.github.issue['number'])
        api = self.github.api
        number = self.github.issue['number']
        self.github.api = lambda path:api(path.replace('/issues/'+str(number), '/issues/7'))
        self.job = IssueIntake(self.github, self.registry, self.spec).investigate(self.github.issue['number'], self.repo)
        self.assertEqual('investigated', self.job['status'], self.job)
        self.executor = self.service()

    def test_issue_captures_environment_run_and_next_issue_loads_reviewed_history_on_same_ledger(self):
        first = self.run_issue()
        self.assertEqual('awaiting-review', first['status'], first)
        memory = self.executor.memory
        saved = self.registry.load('repository_experience')
        self.assertIn(first['run_id'], saved, self.registry.load('experience_capture'))
        self.assertEqual('pending', saved[first['run_id']]['review'])
        self.assertFalse(envelope(self.model)['repository_experience']['hits'])
        # Cleanup is an original evidence change. Capture its final facts before
        # reviewing them; never keep an old acceptance over changed report bytes.
        cleanup_run(self.executor.runs/first['run_id'])
        memory.experience.capture(first['run_id'])
        saved = self.registry.load('repository_experience')
        budget = self.registry.budget()
        entry = saved[first['run_id']]
        memory.experience.review(first['run_id'], memory.experience.challenge(first['run_id'], entry), 'accepted')
        self.assertEqual(budget, self.registry.budget())
        self.next_issue()
        second = self.run_issue()
        self.assertEqual('awaiting-review', second['status'], second)
        value = envelope(self.model)
        self.assertEqual(first['run_id'], value['repository_experience']['hits'][0]['id'])
        self.assertFalse(value['repository_experience']['runtime_authorized'])
        self.assertNotEqual(first['task_id'], second['task_id'])
        record = json.loads((self.executor.runs/second['run_id']/'run.json').read_text(encoding='utf-8'))
        evidence = record['provider_evidence']
        self.assertTrue(evidence['experience_context_sha256'])
        self.assertTrue(evidence['knowledge_context_sha256'])
        self.assertTrue(evidence['memory_load_sha256'])
        self.assertEqual('bug_fix', evidence['memory_task_type'])
        self.assertEqual(4, self.registry.budget()['requests_used'])
        self.assertEqual(0, self.registry.budget()['requests_reserved_unknown'])
        self.assertEqual(self.registry.path, self.executor.memory.registry.path)
        before = self.registry.budget()
        memory = self.executor.memory
        entry = self.registry.load('repository_experience')[first['run_id']]
        memory.experience.review(first['run_id'], memory.experience.challenge(first['run_id'], entry), 'rejected')
        issue = self.github.issue['title']+'\n'+self.github.issue['body']
        with self.assertRaises(SafetyError): memory.experience.context(issue, self.repo)
        with self.registry.transaction() as db:
            db.execute("DELETE FROM objects WHERE kind='experience_task' AND id=?", (second['task_id'],))
        with self.assertRaisesRegex(SafetyError, 'selection is missing'):
            memory.experience.verify_task(second['task_id'], issue)
        self.assertEqual(before, self.registry.budget())

    def test_issue_pending_history_is_not_loaded_and_readonly_settings_do_not_charge(self):
        first = self.run_issue()
        self.assertEqual('awaiting-review', first['status'], first)
        memory = self.executor.memory
        self.assertFalse(memory.compaction.settings()['enabled'])
        before = self.registry.budget()
        self.assertEqual('pending', memory.experience.view()['entries'][0]['review'])
        self.assertFalse(memory.knowledge.search('calculator addition')['hits'])
        self.assertEqual(before, self.registry.budget())
        with self.assertRaises(SafetyError): memory.registry.put('run', first['run_id'], {})
        with self.assertRaises(SafetyError): memory.registry.put('pi_task', first['task_id'], {})
        self.assertFalse(hasattr(memory.registry, 'reserve'))
        with self.assertRaises(SafetyError):
            memory.registry.put('pi_compaction_intent', 'foreign', {'task_id':'f'*32})


class MonitorMemoryTests(unittest.TestCase):
    def setUp(self):
        from test_task_planning import SyntheticModel
        self.model = SyntheticModel()
        self.addCleanup(self.model.close)
        monitors.MonitorExecutionTests.setUp(self)
    finish = monitors.MonitorExecutionTests.finish
    scope = monitors.MonitorExecutionTests.scope
    view = monitors.MonitorExecutionTests.view
    payload = monitors.MonitorExecutionTests.payload
    preview = monitors.MonitorExecutionTests.preview
    configure = monitors.MonitorExecutionTests.configure
    def new_hub(self):
        from repo_maintainer.pi_provider import PiProvider
        from repo_maintainer.repository_workbench import RepositoryWorkbench
        from test_repository_workbench import FakeGitHub, FakeServer
        return RepositoryWorkbench(self.root/'hub',self.config,request_budget=12,allow_auto=True,
            github=FakeGitHub(),server_factory=FakeServer,
            provider_factory=lambda p:PiProvider(p.allowed_paths,self.model.url,readonly_paths=p.readonly_paths,max_requests=12))
    worker = monitors.MonitorExecutionTests.worker
    prepare = monitors.MonitorExecutionTests.prepare
    repair_preview = monitors.MonitorExecutionTests.repair_preview
    execute = monitors.MonitorExecutionTests.execute
    session = monitors.MonitorExecutionTests.session
    preserve_cleanup = monitors.MonitorExecutionTests.preserve_cleanup

    def test_monitor_memory_uses_original_budget_and_child_run_links(self):
        identity, event = self.prepare('auto')
        result = self.execute(self.repair_preview(identity, event))
        session = self.session(identity, event)
        self.preserve_cleanup(session)
        self.assertEqual('candidate_ready', result['status'], session.jobs)
        original = self.hub.monitor.registries[identity]
        entries = original.load('repository_experience')
        run_id = next(iter(session.runs))
        self.assertIn(run_id, entries, original.load('experience_capture'))
        self.assertEqual('pending', entries[run_id]['review'])
        self.assertTrue(original.load('experience_task'))
        self.assertTrue(original.load('knowledge_task'))
        self.assertTrue(original.load('pi_context_policy'))
        self.assertFalse(session.registry.load('repository_experience'))
        self.assertEqual(0, session.registry.budget()['requests_used'])
        self.assertEqual(2, original.budget()['requests_used'])
        record = json.loads((session.runs_dir/run_id/'run.json').read_text(encoding='utf-8'))
        self.assertTrue(record['provider_evidence']['memory_load_sha256'])
        self.assertEqual('commit_inspection', record['provider_evidence']['memory_task_type'])
        budget = original.budget()
        entry = entries[run_id]
        session.experience.review(run_id, session.experience.challenge(run_id, entry), 'accepted')
        self.assertEqual(budget, original.budget())
        self.assertEqual('accepted', session.experience.view()['entries'][0]['review'])
        from unittest.mock import patch
        with patch.object(session.experience,'capture',side_effect=SafetyError('synthetic capture blocked')):
            session._register(record)
        self.assertEqual('blocked',original.load('experience_capture')[run_id]['status'])
        self.assertFalse(session.registry.load('experience_capture'))
        with self.assertRaises(SafetyError): session.controller_memory.registry.put('attempt', 'x', {})
        with session.registry.transaction() as db:
            metadata = json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
            wrong = {**metadata,'shared_budget':str(self.root/'foreign/ui-state/registry.sqlite3')}
            db.execute('UPDATE metadata SET body=?',(json.dumps(wrong),))
        try:
            with self.assertRaisesRegex(SafetyError, 'ownership differs'):
                session.experience.validate(run_id, original.load('repository_experience')[run_id])
        finally:
            with session.registry.transaction() as db:db.execute('UPDATE metadata SET body=?',(json.dumps(metadata),))

    def test_next_commit_loads_reviewed_episode_and_knowledge_from_original_monitor_journal(self):
        from test_knowledge_memory import NOTE
        from repo_maintainer.git import RepositoryGateway
        identity, event = self.prepare('auto')
        self.execute(self.repair_preview(identity, event))
        first = self.session(identity, event)
        self.preserve_cleanup(first)
        run_id = next(iter(first.runs))
        original = self.hub.monitor.registries[identity]
        entry = original.load('repository_experience')[run_id]
        first.experience.review(run_id, first.experience.challenge(run_id, entry), 'accepted')
        self.model.reflection_candidate = copy.deepcopy(NOTE)
        preview = first._operate('knowledge_preview', {'experience_ids':[run_id]})['knowledge_preview']
        note = first._operate('knowledge_propose', {'experience_ids':[run_id], 'revision':preview['revision']})
        stored = original.load('repository_knowledge')[note['knowledge_id']]
        first.knowledge.review(note['knowledge_id'], first.knowledge.challenge(note['knowledge_id'], stored), 'accepted')
        self.model.reflection_candidate = None
        first.maintenance.configure(True, first.maintenance.revision())
        declared = first.maintenance.save_note(None, first.maintenance.revision(),
            'Preserve calculator addition behavior', 'Keep mixed and negative operands when fixing add.', 'open', 3)
        path = self.repo/'calculator.py'
        path.write_text(path.read_text(encoding='utf-8')+'\n# new observed version\n', encoding='utf-8')
        repository = RepositoryGateway(self.repo)
        repository.command('add', 'calculator.py'); repository.command('commit', '-m', 'new broken version')
        self.worker(self.hub.monitor.poll, identity)
        event2 = next(e for e in self.view()['events'] if e['sha'] == repository.head)
        self.assertNotEqual(event['id'], event2['id'])
        self.execute(self.repair_preview(identity, event2))
        second = self.session(identity, event2)
        self.preserve_cleanup(second)
        value = envelope(self.model)
        self.assertEqual(run_id, value['repository_experience']['hits'][0]['id'])
        self.assertEqual(note['knowledge_id'], value['repository_knowledge']['hits'][0]['id'])
        self.assertEqual('requires_revalidation', value['repository_experience']['hits'][0]['applicability'])
        self.assertFalse(value['repository_knowledge']['runtime_authorized'])
        self.assertIn(declared['note_id'],[h['source_ref']['id'] for h in value['repository_maintenance']['hits']])
        self.assertFalse(value['repository_maintenance']['runtime_authorized'])
        run2 = next(iter(second.runs))
        record = json.loads((second.runs_dir/run2/'run.json').read_text(encoding='utf-8'))
        evidence = record['provider_evidence']
        self.assertTrue(evidence['knowledge_context_sha256'])
        self.assertTrue(evidence['memory_load_sha256'])
        self.assertTrue(evidence['maintenance_context_sha256'])
        self.assertEqual(len(self.model.requests), original.budget()['requests_used'])
        self.assertEqual(0, second.registry.budget()['requests_used'])
        budget = original.budget()
        stored = original.load('repository_knowledge')[note['knowledge_id']]
        second.knowledge.review(note['knowledge_id'], second.knowledge.challenge(note['knowledge_id'], stored), 'rejected')
        with self.assertRaises(SafetyError): second.knowledge.verify_task(evidence['task_id'], record['issue'])
        self.assertEqual(budget, original.budget())

    def test_monitor_compaction_uses_same_native_and_budget_and_restart_does_not_replay(self):
        from test_task_compaction import CONFIG
        identity, event = self.prepare()
        original = self.hub.monitor.registries[identity]
        original.put('context_settings', 'default', {'schema_version':1, 'config':copy.deepcopy(CONFIG)})
        self.model.summary_text = 'Keep the original source, approval boundaries and failed attempt references.'
        self.execute(self.repair_preview(identity, event))
        session = self.session(identity, event)
        plan = next(iter(session.plans.values()))['view']
        session._operate('decision', {'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'reject'})
        preview = self.worker(self.hub.monitor.continuation.inspect,
            {'monitor_id':identity,'event_id':event['id'],'run_id':None,'mode':'approval'})
        self.worker(self.hub.monitor.continuation.start, {**preview,'confirmed':True})
        task_id = session.task_planner.identity
        task = original.load('pi_task')[task_id]
        self.assertEqual(2, task['revision'])
        file = session.task_planner.store.directory(task_id)/'sessions'/task['checkpoint']['file']
        prefix = file.read_bytes(); budget = original.budget()
        detail = session.compaction.detail(task_id, session.compaction.issue(task_id))
        self.assertTrue(detail['can_compact'], detail)
        result = session._operate('compact_task', {'task_id':task_id,'revision':detail['revision']})
        self.assertEqual(task_id, result['task_id'])
        self.assertEqual(2, original.load('pi_task')[task_id]['revision'])
        self.assertEqual(prefix, file.read_bytes()[:len(prefix)])
        self.assertGreater(original.budget()['requests_used'], budget['requests_used'])
        self.assertEqual(1, len(original.load('pi_compaction_receipt')))
        self.assertEqual(0, session.registry.budget()['requests_used'])
        before = original.budget(); calls = len(self.model.requests)
        self.hub.close(); self.hub = self.new_hub(); self.hub.monitor.stop()
        self.worker(self.hub.monitor.operate, 'monitor_open', {'monitor_id':identity,'event_id':event['id']})
        reopened = self.session(identity, event)
        self.assertEqual(calls, len(self.model.requests))
        self.assertEqual(before, self.hub.monitor.registries[identity].budget())
        self.assertEqual(1, len(reopened.controller_memory.registry.load('pi_compaction_receipt')))
