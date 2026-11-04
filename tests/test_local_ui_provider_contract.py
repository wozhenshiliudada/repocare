"""State projection never grants a model probe to an unbound provider."""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.provider import MockProvider


class LocalUIProviderContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        repo = create_fixture(root / "repository")
        policy = Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",))
        self.session = UISession(repo, policy, MockProvider(), root / "runs")
        self.addCleanup(self.session.close)

    def test_unsupported_providers_keep_state_available_without_probe_authority(self):
        session = self.session
        pi = PiProvider(("calculator.py",), "http://127.0.0.1:1/v1", max_requests=3)
        providers = (session.provider, MockProvider("broken"), pi, SimpleNamespace(inner=pi))
        original_budget = session.registry.budget()
        with patch.object(session, "_model_connectivity", side_effect=AssertionError("unsupported probe")), \
                patch.object(pi, "plan", side_effect=AssertionError("model request")):
            for provider in providers:
                with self.subTest(provider=type(provider).__name__):
                    session.provider = provider
                    state = session.state()
                    self.assertEqual({"available": False, "checks": [],
                                      "proves": "model_response_only", "repair_verified": False},
                                     state["model_connectivity"])
                    self.assertEqual([], state["plans"])
                    self.assertEqual([], state["runs"])
                    with self.assertRaises(SafetyError):
                        UISession._model_connectivity(session)
        self.assertEqual(original_budget, session.registry.budget())
        self.assertEqual({}, session.registry.load("attempt"))
        self.assertEqual({}, session.registry.load("model_connectivity"))

    def test_original_durable_pi_keeps_readiness_without_calling_model(self):
        self.session.close()
        root = Path(self.temp.name)
        pi = PiProvider(("calculator.py",), "http://127.0.0.1:1/v1", max_requests=3)
        session = UISession(self.session.repo, self.session.policy, pi, root / "pi-runs")
        self.addCleanup(session.close)
        with patch("repo_maintainer.model_connectivity._invoke", side_effect=AssertionError("model request")), \
                patch("repo_maintainer.provider_config.load_provider_config", side_effect=AssertionError("config read")):
            self.assertTrue(session.state()["model_connectivity"]["available"])
            session.monitor_owned = True
            self.assertFalse(session.state()["model_connectivity"]["available"])
            session.monitor_owned = False
            pi.max_requests = 0
            self.assertFalse(session.state()["model_connectivity"]["available"])
        self.assertEqual({}, session.registry.load("attempt"))
        self.assertEqual({}, session.registry.load("model_connectivity"))


if __name__ == "__main__":
    unittest.main()
