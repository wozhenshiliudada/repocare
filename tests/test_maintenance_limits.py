"""Resource changes must precede paid calls and preserve source/legacy evidence."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.maintenance_limits import LIMITS, validate_limits, capacity_view
from repo_maintainer.models import Policy, digest, file_digest
from repo_maintainer.provider import MockProvider
from repo_maintainer.workspace_inventory import tracked_capacity, inventory
from repo_maintainer.workflow import repair, plan_repair
from repo_maintainer import pi_evidence
from test_pi_evidence import evidence


class MaintenanceLimitsTests(unittest.TestCase):
    def test_strict_operator_config_and_no_mutable_view_alias(self):
        self.assertEqual(LIMITS,validate_limits(LIMITS))
        values=capacity_view();values['workspace_max_bytes']=1
        self.assertNotEqual(values,LIMITS)
        for invalid in [None,[],{**LIMITS,'unknown':1},{**LIMITS,'workspace_max_bytes':True},
                        {**LIMITS,'source_context_max_files':129},{**LIMITS,'source_file_max_bytes':0},
                        {**LIMITS,'planning_packet_max_bytes':1},{**LIMITS,'discovery_catalog_max_entries':1}]:
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):validate_limits(invalid)

    def test_old_time_receipt_stays_readable_without_rewriting_it(self):
        value=evidence();value.update(planning_timeout_seconds=1800,max_output_tokens=128000)
        original=copy.deepcopy(value)
        self.assertEqual(original,pi_evidence.validate_evidence(value,6))
        self.assertEqual(value,original)

    def test_actual_sparse_workspace_above_old_byte_and_entry_caps_is_inventoried(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            large=root/'large.bin'
            with large.open('wb') as stream:stream.truncate(64*1024**2+1)
            for i in range(5001):(root/f'd{i}').mkdir()
            manifest=inventory(root)
            self.assertEqual(len(manifest),5002)
            self.assertEqual(manifest['large.bin']['sha256'],file_digest(large))
            self.assertEqual(file_digest(large),digest(large.read_bytes()))

    def test_capacity_overflow_stops_before_provider_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);repo=create_fixture(root/'source');gateway=RepositoryGateway(repo)
            before=gateway.snapshot();provider=MockProvider('fix')
            with patch.dict(LIMITS,workspace_max_bytes=1),patch.object(provider,'plan',wraps=provider.plan) as plan:
                from repo_maintainer.models import SafetyError
                with self.assertRaises(SafetyError):
                    plan_repair(repo,'Addition bug',provider,Policy(('calculator.py',),(DEMO_TEST,)))
                record=repair(repo,'Addition bug',provider,Policy(('calculator.py',),(DEMO_TEST,)),
                              'auto',root/'runs')
                plan.assert_not_called()
            self.assertEqual(record['status'],'failed',record)
            self.assertEqual(record['candidate_preflight']['status'],'capacity_blocked')
            self.assertEqual(record['candidate_preflight']['model_calls'],0)
            self.assertEqual(record['baseline_tests'],[])
            self.assertFalse(Path(record['workspace']).exists())
            self.assertTrue(record['source_unchanged'])
            self.assertEqual(before,gateway.snapshot())

    def test_tracked_preflight_excludes_ignored_runtime_and_counts_parent_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=create_fixture(Path(temporary)/'source');gateway=RepositoryGateway(root)
            # The tracked fixture ignores cache files; source worktree need not copy them.
            (root/'.git'/'ignored-runtime.bin').write_bytes(b'x'*8000)
            result=tracked_capacity(gateway)
            self.assertEqual(result['status'],'fits');self.assertTrue(result['complete'])
            files=[raw for raw in gateway.command('ls-files','-z').split(b'\0') if raw]
            self.assertEqual(result['tracked_files'],len(files))
            self.assertEqual(result['bytes_observed'],4096+sum((root/name.decode()).stat().st_size for name in files))
            with patch.dict(LIMITS,workspace_max_entries=1):
                self.assertEqual(tracked_capacity(gateway)['status'],'capacity_blocked')

    def test_even_empty_tree_reserves_worktree_identity_bytes(self):
        from types import SimpleNamespace
        gateway=SimpleNamespace(command=lambda *args:b'')
        with patch.dict(LIMITS,workspace_max_bytes=4095):
            self.assertEqual(tracked_capacity(gateway)['status'],'capacity_blocked')

if __name__=='__main__':unittest.main()
