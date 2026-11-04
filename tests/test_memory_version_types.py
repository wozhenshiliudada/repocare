"""Recorded memory versions must not use Python's bool-as-int equality."""
import copy
import unittest
from memory_catalog_fixture import fixture
from repo_maintainer.dashboard import memory_evidence
from repo_maintainer.memory_items import build_load,validate_load_view
from repo_maintainer.models import SafetyError
from repo_maintainer.task_session import fingerprint


class MemoryVersionTests(unittest.TestCase):
    def load(self):return build_load(**fixture())

    def test_boolean_schema_and_route_versions_are_not_integer_versions(self):
        original=self.load()
        for field in ('schema_version','route_version'):
            for invalid in (True,False):
                with self.subTest(field=field,value=invalid):
                    changed=copy.deepcopy(original);changed[field]=invalid
                    changed['context_sha256']=fingerprint({k:v for k,v in changed.items() if k!='context_sha256'})
                    with self.assertRaises(SafetyError):validate_load_view(changed,changed['context_sha256'])
        self.assertEqual(original,validate_load_view(original,original['context_sha256']))

    def test_dashboard_rejects_rehashed_boolean_versions_without_mutating_original_evidence(self):
        original=self.load()
        self.assertEqual(original,memory_evidence({'memory_load':original,'memory_load_sha256':original['context_sha256']})['memory_load'])
        for field in ('schema_version','route_version'):
            with self.subTest(field=field):
                changed=copy.deepcopy(original);changed[field]=True
                changed['context_sha256']=fingerprint({k:v for k,v in changed.items() if k!='context_sha256'})
                evidence={'memory_load':changed,'memory_load_sha256':changed['context_sha256']};before=copy.deepcopy(evidence)
                view=memory_evidence(evidence)
                self.assertNotIn('memory_load',view);self.assertIn('memory_load_error',view)
                self.assertEqual(before,evidence)
