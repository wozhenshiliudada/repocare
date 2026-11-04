"""Recorded summaries stay display-only and never dereference arbitrary artifacts."""
from pathlib import Path
import unittest
from unittest.mock import patch
from repo_maintainer.dashboard import tests as project_tests


class DashboardBehaviorProjectionTests(unittest.TestCase):
    def test_readonly_projection_retains_category_and_omits_artifact_paths_and_extra_fields(self):
        value={'command':['controller-marker'],'command_kind':'controller_behavior_contract',
            'returncode':0,'category':'passed','tests_run':1,'behavior_receipt':{
                'phase':'candidate','category':'passed','counts':{'total':1,'passed':1,'failed':0,'inconclusive':0},
                'cases':[{'id':'sum','category':'passed','actual':'private-large-value'}],
                'artifact_directory':'C:/outside/must-not-open','secret':'not-exported'}}
        with patch.object(Path,'open',side_effect=AssertionError('Display must not open artifacts')):
            result=project_tests([value])[0]
        self.assertEqual('passed',result['category']);self.assertEqual(1,result['tests_run'])
        receipt=result['behavior_receipt'];self.assertEqual('recorded_summary_only',receipt['scope'])
        self.assertEqual([{'id':'sum','category':'passed'}],receipt['cases'])
        self.assertNotIn('artifact_directory',receipt);self.assertNotIn('secret',receipt)

    def test_missing_behavior_receipt_is_inconclusive_despite_exit_zero(self):
        result=project_tests([{'command_kind':'controller_behavior_contract','returncode':0,'category':'passed'}])[0]
        self.assertEqual('inconclusive',result['category'])
