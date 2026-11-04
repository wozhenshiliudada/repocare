"""A read-only UI snapshot must revalidate drift and never persist cached trust."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import contextmanager
import uuid
import test_local_ui as ui_fixture
import test_catalog_composite as composite_fixture
from repo_maintainer.models import SafetyError
from repo_maintainer import source_memory
from repo_maintainer.source_view import verified_source_view


class SourceViewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.document=self.root/'rule.md'
        self.original='状态：确认\nPreserve signed addition.\n';self.document.write_text(self.original,encoding='utf-8')
        (self.root/source_memory.CATALOG).write_text(json.dumps({'schema_version':1,'entries':[{
          'id':'rule','topic':'addition','path':'rule.md','start_line':1,'end_line':2,'kind':'document','classification':'confirmed'}]}),encoding='utf-8')

    def test_repeated_view_reads_are_shared_then_fully_revalidated_and_discarded(self):
        with patch.object(source_memory,'read_bytes',wraps=source_memory.read_bytes) as reads:
            with verified_source_view():
                expected=source_memory.sources(self.root);first=reads.call_count
                for _ in range(40):self.assertEqual(expected,source_memory.sources(self.root))
                self.assertEqual(first,reads.call_count)
            self.assertGreater(reads.call_count,first);after=reads.call_count
            source_memory.sources(self.root);self.assertGreater(reads.call_count,after)

    def test_source_content_drift_rejects_whole_view_before_returning(self):
        with self.assertRaises(SafetyError):
            with verified_source_view():
                expected=source_memory.sources(self.root)
                self.document.write_text(self.original+'New independently changed material.\n',encoding='utf-8')
                self.assertEqual(expected,source_memory.sources(self.root))
        self.assertNotEqual(expected,source_memory.sources(self.root))

    def test_invalid_marker_drift_is_not_cached_as_valid_after_scope(self):
        with self.assertRaises(SafetyError):
            with verified_source_view():
                source_memory.sources(self.root);self.document.write_text('状态：提议\nDifferent rule.\n',encoding='utf-8')
        with self.assertRaises(SafetyError):source_memory.sources(self.root)

    def test_result_mutation_does_not_change_cached_trust(self):
        with verified_source_view():
            first=source_memory.sources(self.root);first[1][0]['classification']='proposed'
            self.assertEqual('confirmed',source_memory.sources(self.root)[1][0]['classification'])

    def test_exception_and_nested_views_do_not_leave_cache_in_next_operation(self):
        with self.assertRaisesRegex(ValueError,'stop'):
            with verified_source_view():
                first=source_memory.sources(self.root)
                with verified_source_view():self.assertEqual(first,source_memory.sources(self.root))
                raise ValueError('stop')
        self.document.write_text(self.original+'Changed after failed rendering.\n',encoding='utf-8')
        self.assertNotEqual(first,source_memory.sources(self.root))

    def test_parallel_thread_does_not_inherit_another_views_cached_result(self):
        with self.assertRaises(SafetyError):
            with verified_source_view():
                first=source_memory.sources(self.root)
                self.document.write_text(self.original+'Changed while the first view is pending.\n',encoding='utf-8')
                with ThreadPoolExecutor(max_workers=1) as pool:
                    second=pool.submit(source_memory.sources,self.root).result()
                self.assertNotEqual(first,second)


class SourceViewBoundaryTests(unittest.TestCase):
    setUp=ui_fixture.LocalUITests.setUp
    finish=ui_fixture.LocalUITests.finish

    def test_api_source_drift_is_structured_conflict_and_does_not_change_budget(self):
        before=self.session.registry.budget()
        with patch.object(self.session,'state',side_effect=SafetyError('Memory source changed while rendering; refresh the view')):
            response=self.client.get('/api/state',headers=self.headers)
        self.assertEqual(409,response.status_code);self.assertIn('refresh the view',response.json()['error'])
        self.assertEqual(before,self.session.registry.budget())

    def test_late_display_drift_keeps_business_outcome_and_does_not_publish_partial_views(self):
        identity=uuid.uuid4().hex;run=uuid.uuid4().hex
        self.session.jobs[identity]={'id':identity,'status':'running'};self.session.active=identity
        self.session.runs[run]={'run_id':run,'status':'old'};before=self.session.registry.budget()
        @contextmanager
        def changed():
            yield
            raise SafetyError('Selected archive receipt changed')
        with patch('repo_maintainer.source_view.verified_source_view',changed), \
             patch.object(self.session,'_operate',return_value={'business_result':'recorded'}), \
             patch.object(self.session,'_run_view',return_value={'run_id':run,'status':'candidate_ready'}), \
             patch.object(self.session,'_unavailable_run_view',return_value={'run_id':run,'status':'unavailable'}):
            self.session._execute(identity,'test_only',{})
        job=self.session.registry.load('job')[identity]
        self.assertEqual('finished',job['status']);self.assertEqual({'business_result':'recorded'},job['result'])
        self.assertIn('refresh_warning',job);self.assertEqual('unavailable',self.session.runs[run]['status'])
        self.assertIsNone(self.session.active);self.assertEqual(before,self.session.registry.budget())


class CatalogSourceViewTests(unittest.TestCase):
    setUp=composite_fixture.CompositeCatalogTests.setUp
    open=composite_fixture.CompositeCatalogTests.open

    def test_query_remains_readonly_and_rejects_source_drift_before_return(self):
        before=self.session.registry.path.read_bytes();budget=self.session.registry.budget()
        result=self.session.catalog.query('addition add',50)
        self.assertTrue(result['items']);self.assertEqual(before,self.session.registry.path.read_bytes())
        source=self.documents/'new.md';raw=source.read_bytes();original=self.session.memory.query_rows
        def change_after_read():
            rows=original();source.write_bytes(raw+b'late source change\n');return rows
        try:
            with patch.object(self.session.memory,'query_rows',side_effect=change_after_read):
                with self.assertRaises(SafetyError):self.session.catalog.query('addition add',50)
        finally:source.write_bytes(raw)
        self.assertEqual(before,self.session.registry.path.read_bytes());self.assertEqual(budget,self.session.registry.budget())
        self.assertEqual(0,len(self.model.requests))
