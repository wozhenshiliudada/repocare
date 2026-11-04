import unittest
from repo_maintainer.models import SafetyError
import test_validation_proposal as fixture
class ModelIdentityGuard(unittest.TestCase):
    def test_changed_provider_identity_stops_before_any_model_or_budget_write(self):
        f=fixture.ValidationProposalTests();f.setUp();self.addCleanup(f.doCleanups)
        budget=f.registry.budget();f.provider.mock_url=f.model.url+'/changed-model-route'
        with self.assertRaises(SafetyError):f.service._fresh()
        self.assertEqual(budget,f.registry.budget());self.assertEqual([],f.model.requests)
if __name__=='__main__':unittest.main(verbosity=2)
