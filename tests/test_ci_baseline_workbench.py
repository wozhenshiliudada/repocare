import copy
import unittest
from unittest.mock import patch

import test_ci_workbench as workbench_fixtures
import test_ci_success_receipts as receipt_fixtures
from repo_maintainer.ci_evidence import CiEvidenceReader
from repo_maintainer.ci_success_baseline import baseline_for_packet


class BaselineWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.case = workbench_fixtures.CiWorkbenchTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.receipts = receipt_fixtures.ReceiptSourceTests()
        self.addCleanup(self.receipts.doCleanups)
        self.receipts.setUp()
        r = self.receipts
        r.reader = CiEvidenceReader(r.transport, synthetic=False)
        r.write_receipt(r.current); r.write_receipt(r.history)
        self.source = r.source_with()
        self.case.params = {'url':'https://github.com/example/project','run_id':'20','attempt':'1'}

    def get(self, **extra):
        with patch('repo_maintainer.ci_http.CiHttpTransport', return_value=self.receipts.transport):
            return self.case.get({**self.case.params, **extra})

    def configure_source(self):
        receipts = self.receipts
        class Remote:
            def run_attempt(inner, repository, run_id, attempt): return receipt_fixtures.discovered(receipts.current)
            def successful_runs(inner, *args): return {'runs':[receipt_fixtures.discovered(receipts.history)], 'complete':True}
            def compare(inner, repository, base, head):
                return {'base_commit':{'sha':base},'merge_base_commit':{'sha':base},'status':'ahead',
                        'total_commits':1,'commits':[{'sha':head}],
                        'files':[{'filename':'src/app.py','status':'modified','patch':'-old\n+new'}]}
        self.case.hub.ci_baseline_sources = {('example/project',1,receipts.current['job']['name']):self.source}
        self.case.hub.ci_baseline_remote = Remote()

    def test_shared_authenticated_http_returns_comparable_scope_and_diff_without_actions(self):
        self.configure_source()
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()['comparable_baseline']
        self.assertEqual(result['status'], 'comparable', result)
        self.assertEqual(result['baseline']['run_id'], 10)
        self.assertEqual(result['current']['run_id'], 20)
        self.assertEqual(result['baseline']['original_receipt_proof']['sha256'], self.source.pins['10-1-100.json'])
        self.assertEqual(result['diff']['relevant_files'][0]['patch'], '-old\n+new')
        self.assertFalse(self.case.hub.jobs or self.case.hub.workspaces)

    def test_no_reviewed_receipts_shows_unconfirmed_not_any_old_green(self):
        result = self.get().json()['comparable_baseline']
        self.assertEqual(result['message'], '无法确认可比较基线')
        self.assertEqual(result['rejections'][0]['reason'], 'missing_reviewed_commands_environment_dependency_receipt')
        self.assertIsNone(result['baseline'])

    def test_no_runner_steps_and_cancelled_are_not_code_failure_baselines(self):
        r = self.receipts
        jobs = r.transport.pages['/repos/example/project/actions/runs/20/attempts/1/jobs?per_page=100&page=1']
        jobs['jobs'][0]['steps'] = []
        result = self.get().json()['comparable_baseline']
        self.assertEqual(result['rejections'][0]['reason'], 'current_job_has_no_executed_steps')
        jobs['jobs'][0]['conclusion'] = 'cancelled'
        result = self.get().json()['comparable_baseline']
        self.assertEqual(result['status'], 'unconfirmed')

    def test_client_cannot_register_receipt_path_pins_or_alter_scope(self):
        for extra in [{'root':'C:/private'}, {'pins':'x'}, {'workflow_id':'1'}]:
            response = self.get(**extra)
            self.assertEqual(response.status_code, 400)
        self.assertFalse(self.case.hub.ci_baseline_sources)

    def test_log_unavailability_does_not_erase_complete_metadata_and_reviewed_receipts(self):
        self.configure_source()
        packet = self.receipts.reader.observe('https://github.com/example/project',20,1)
        packet['evidence_status'] = 'partial'
        packet['errors'] = [{'stage':'log','code':'logs_unavailable'}]
        result = self.case.hub.ci_baseline(packet)
        self.assertEqual(result['status'], 'comparable', result)
        self.assertEqual(packet['evidence_status'], 'partial')
        packet['errors'] = [{'stage':'jobs','code':'size_limit'}]
        result = self.case.hub.ci_baseline(packet)
        self.assertEqual(result['status'], 'unconfirmed')


if __name__ == '__main__': unittest.main()
