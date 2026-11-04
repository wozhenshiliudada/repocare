"""Real local-library and HTTP wiring; no model calls or remote writes."""
import json
from pathlib import Path
import sys
import uuid
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, Mock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import test_repository_workbench as fixture
import test_decision_relations as relations_fixture
import test_memory_migration as migration_fixture
from repo_maintainer.workbench_tools import DEFINITIONS
from repo_maintainer.models import digest, Policy
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.workbench_read import local_view, ReadError
from repo_maintainer.maintenance_limits import LIMITS


class AlignmentTests(unittest.TestCase):
    setUp = fixture.WorkbenchTests.setUp
    new_hub = fixture.WorkbenchTests.new_hub
    finish = fixture.WorkbenchTests.finish
    execute = fixture.WorkbenchTests.execute
    scope = fixture.WorkbenchTests.scope
    connect = fixture.WorkbenchTests.connect

    def params(self, kind, **overrides):
        return {f['key']: overrides.get(f['key'], f['default']) for f in DEFINITIONS[kind][1]}

    def preview(self, kind, **overrides):
        job = self.execute('capability_preview', {'capability': kind, 'params': self.params(kind, **overrides)})
        self.assertEqual(job['status'], 'finished', job)
        return job['result']

    def run_preview(self, view, *, expected='finished'):
        job = self.execute('capability_execute', {'preview_id': view['preview_id'], 'revision': view['revision'], 'confirmed': True})
        self.assertEqual(job['status'], expected, job)
        return job['result']

    def tool(self, kind, **overrides):
        return self.run_preview(self.preview(kind, **overrides))

    def documents(self):
        item = relations_fixture.DecisionRelationsTests()
        item.setUp()
        self.addCleanup(item.doCleanups)
        item.repo = self.repo
        item.build()
        item.save_spec()
        return item

    def report(self):
        run = self.root / 'old-runs' / ('a' * 32)
        run.mkdir(parents=True)
        (run / 'run.json').write_text(json.dumps({'schema_version': 2, 'run_id': run.name,
            'mode': 'approval', 'status': 'discarded', 'finished_at': 'historical', 'source_repo': str(self.repo)}))
        (run / 'report.md').write_text('# Historical report\nOld approval cannot authorize a new task.\n', encoding='utf-8')
        return run

    def test_definitions_are_authenticated_and_exactly_cover_existing_cli_routes(self):
        self.assertEqual(self.client.get('/api/capabilities').status_code, 401)
        response = self.client.get('/api/capabilities', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        rows = response.json()['capabilities']
        self.assertEqual({row['id'] for row in rows}, set(DEFINITIONS))
        self.assertEqual(len(rows), 14)
        for row in rows:
            self.assertTrue(row['fields'])
            self.assertTrue(row['impact'])
        for payload in ({'capability':'shell','params':{}}, {'capability':'backup','params':{'extra':'bad'}}):
            self.assertEqual(self.client.post('/api/actions/capability_preview', headers=self.headers,
                json={'request_id':uuid.uuid4().hex, **payload}).status_code,409)

    def test_git_refs_are_fixed_and_confirmation_is_one_use_without_source_changes(self):
        before = RepositoryGateway(self.repo).snapshot()
        view = self.preview('git-compare', repository=str(self.repo))
        self.assertEqual(view['params']['head'], before['head'])
        self.assertFalse((self.hub.root / 'tool-outputs').exists())
        result = self.run_preview(view)
        self.assertEqual(result['result']['head_sha'], before['head'])
        self.assertEqual(self.run_preview(view, expected='failed')['error'], '预览已变化、已消费或尚未确认；请核对原记录')
        self.assertEqual(RepositoryGateway(self.repo).snapshot(), before)
        artifact = result['artifacts'][0]
        response = self.client.get(artifact['url'], headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(digest(response.content), artifact['sha256'])
        self.assertEqual(self.client.get(artifact['url']).status_code, 401)

    def test_source_build_relations_and_explicit_new_session_binding_use_original_libraries(self):
        docs = self.documents()
        result = self.tool('source-build', repository=str(self.repo), source_root=str(docs.root))
        self.assertEqual(result['result']['entries'], 3)
        relation = self.tool('relations', repository=str(self.repo), source_root=str(docs.root),
            index=str(docs.index), spec=str(docs.spec))
        self.assertTrue((Path(relation['directory']) / 'claims.memory-relations.json').is_file())
        registration = self.tool('source-bind', repository=str(self.repo), source_root=str(docs.root), index=str(docs.index),
            relations=str(Path(relation['directory']) / 'claims.memory-relations.json'))
        self.assertFalse(registration['result']['existing_tasks_changed'])
        server = self.connect()
        state = server.session.state()
        self.assertTrue(state['source_memory']['enabled'])
        before = server.session.registry.budget()
        job = self.execute('capability_preview', {'capability':'source-bind','params':self.params('source-bind',
            repository=str(self.repo),source_root=str(docs.root),index=str(docs.index))})
        self.assertEqual(job['status'],'failed')
        self.assertEqual(server.session.registry.budget(),before)

    def test_case_export_compose_reports_and_archives_keep_history_readonly(self):
        run = self.report()
        before = {p.name:p.read_bytes() for p in run.iterdir()}
        case = self.tool('case-export', repository=str(self.repo),run_dir=str(run),case_id='old-run',topic='repair',start_line=1,end_line=2)
        self.assertFalse(case['result']['runtime_authorized'])
        selection = self.root / 'compose.json'
        selection.write_text(json.dumps({'schema_version':1,'members':[{'id':'case','source_root':str(Path(case['directory'])/'corpus')}],
            'selections':[{'id':'history','member_id':'case','entry_id':'old-run'}]}))
        composed = self.tool('source-compose',repository=str(self.repo),selection=str(selection))
        self.assertTrue((Path(composed['directory'])/'sources.memory.sqlite3').is_file())
        report = self.tool('report-export',run_dir=str(run),format='Markdown')
        self.assertEqual({a['name'] for a in report['artifacts']},{'tool-result.json','tool-report.md'})
        imported = self.tool('archive-import',run_dir=str(run))
        verified = self.tool('archive-import',operation='verify',archive_root=str(Path(imported['directory'])/'archive'),archive_id=imported['result']['archive_id'])
        self.assertFalse(verified['result']['runtime_authorized'])
        self.assertEqual({name:(run/name).read_bytes() for name in before},before)
        self.assertLessEqual({p.name for p in run.iterdir()}-set(before),{'integration-operation.lock'})

    def test_preflight_only_runs_original_policy_in_isolation_without_model_budget_change(self):
        server = self.connect()
        workspace = next(iter(self.hub.workspaces.values()),None)
        # FakeServer deliberately has no embedded router: register only its original session view for this adapter test.
        identity = uuid.uuid4().hex
        self.hub.workspaces[identity] = SimpleNamespace(session=server.session,view=lambda:{'id':identity})
        self.addCleanup(lambda:self.hub.workspaces.pop(identity,None))
        before = server.session.registry.budget()
        view = self.preview('preflight',workspace_id=identity,issue='Check existing addition baseline')
        self.assertTrue(view['executes_project_tests'])
        result = self.run_preview(view)['result']
        self.assertTrue(result['source_unchanged'])
        self.assertEqual(len(result['tests']),1)
        self.assertNotEqual(result['tests'][0]['returncode'],0)  # Original fixture bug stays red.
        self.assertEqual(server.session.registry.budget(),before)

    def test_backup_verify_and_migration_reuse_original_journal_and_sources(self):
        item = migration_fixture.MigrationTests()
        item.setUp()
        self.addCleanup(item.doCleanups)
        original = item.db_path.read_bytes()
        result = self.tool('backup', runs_dir=str(item.runs))
        self.assertIn('backup_id',result['result'])
        checked = self.tool('backup', runs_dir=str(item.runs),operation='verify',backup_id=result['result']['backup_id'])
        self.assertFalse(checked['result']['runtime_authorized'])
        migrated = self.tool('memory-migration',repository=str(self.repo),selection=str(item.selection))
        self.assertTrue((Path(migrated['directory'])/'sources.memory.sqlite3').is_file())
        self.assertEqual(item.db_path.read_bytes(),original)

    def test_changed_input_or_foreign_output_overlap_cannot_execute_old_preview(self):
        docs = self.documents()
        view = self.preview('relations',repository=str(self.repo),source_root=str(docs.root),index=str(docs.index),spec=str(docs.spec))
        docs.spec.write_text('{}')
        self.assertIn('已变化',self.run_preview(view,expected='failed')['error'])
        job = self.execute('capability_preview', {'capability':'source-build','params':self.params('source-build',
            source_root=str(self.root),repository=str(self.repo))})
        self.assertEqual(job['status'],'failed')

    def test_optional_runtime_registration_and_failures_are_explicit_not_fake_success(self):
        definition = self.hub.tools.definitions()
        row = next(r for r in definition['capabilities'] if r['id']=='python-binding')
        if row['unavailable_reason']:
            job = self.execute('capability_preview', {'capability':'python-binding','params':self.params('python-binding',
                repository=str(self.repo),sources='calculator.py',checks='calculator.py')})
            self.assertEqual(job['status'],'failed')
            self.assertIn('Pyright',job['result']['error'])
        manifest = self.root / 'package.json'
        manifest.write_text('{"schema_version":1}')
        package = SimpleNamespace(path=manifest,sha='1'*64)
        with patch('repo_maintainer.semantic_retrieval.ModelPackage',return_value=package):
            result = self.tool('semantic-package',manifest=str(manifest))
        self.assertFalse(result['result']['existing_sessions_changed'])
        self.assertFalse(result['result']['retrieval_enabled'])
        self.assertEqual(self.hub.semantic_model_manifest,manifest)

    def test_preflight_uses_original_session_executor_not_a_parallel_writer(self):
        server=self.connect()
        identity=uuid.uuid4().hex
        self.hub.workspaces[identity]=SimpleNamespace(session=server.session,view=lambda:{'id':identity})
        self.addCleanup(lambda:self.hub.workspaces.pop(identity,None))
        entered,released=threading.Event(),threading.Event()
        def baseline(*args,**kw):
            entered.set()
            self.assertTrue(released.wait(5))
            return {'events':[{'stage':'baseline_passes'}],'run_id':'a'*32,'baseline_tests':[],'source_unchanged':True}
        with patch('repo_maintainer.baseline_probe.collect_baseline',side_effect=baseline),ThreadPoolExecutor(max_workers=1) as caller:
            task=caller.submit(self.hub.tools.dispatch,'preflight',{'workspace_id':identity,'issue':'fixed'},self.root/'out',{'scope':'a'*64})
            try:
                self.assertTrue(entered.wait(5))
                later=server.session.executor.submit(lambda:'after')
                self.assertFalse(later.done())
            finally:
                released.set()
            self.assertTrue(task.result(5)['source_unchanged'])
            self.assertEqual(later.result(5),'after')

    def test_structure_build_reaches_original_optional_engine_and_keeps_source(self):
        if self.hub.tools.prerequisite('structure-build'):
            self.skipTest('Original optional CodeGraph runtime not installed')
        before=RepositoryGateway(self.repo).snapshot()
        view=self.preview('structure-build',repository=str(self.repo),paths='calculator.py')
        result=self.run_preview(view)
        self.assertTrue((Path(result['directory'])/'structure/structure-manifest.json').is_file())
        self.assertEqual(RepositoryGateway(self.repo).snapshot(),before)


class CatalogCapacityTests(unittest.TestCase):
    def test_more_than_ten_thousand_tracked_files_share_capacity_and_page_without_loss(self):
        gateway = Mock()
        gateway.head = 'a'*40
        gateway.command.return_value = b'\0'.join(f'f{i:05}.py'.encode() for i in range(10005))+b'\0'
        gateway.safe_file.return_value.stat.return_value.st_size=100001
        workspace = SimpleNamespace(session=SimpleNamespace(repo=Path.cwd()))
        with patch('repo_maintainer.workbench_read.RepositoryGateway',return_value=gateway):
            first=local_view(workspace,'tree')
            second=local_view(workspace,'tree',page=2,revision=first['revision'])
            final=local_view(workspace,'tree',page=51)
            self.assertEqual([len(first['files']),len(second['files']),len(final['files'])],[200,200,5])
            self.assertFalse(set(first['files'])&set(second['files']))
            self.assertEqual(first['catalog_limit'],LIMITS['catalog_max_files'])
            self.assertEqual(first['limit'],LIMITS['source_file_max_bytes'])
            self.assertEqual(local_view(workspace,'tree',query='f10004')['files'],['f10004.py'])
            with self.assertRaises(ReadError):local_view(workspace,'tree',revision='old')
