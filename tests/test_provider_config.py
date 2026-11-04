import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.provider_config import load_provider_config


class ProviderConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / ".env.test.local"
        self.secret = "synthetic-config-credential"
        self.body = (f"PI_MAINTENANCE_API_KEY={self.secret}\n"
                     "PI_MAINTENANCE_BASE_URL=https://api.xiaomimimo.com/v1\n"
                     "PI_MAINTENANCE_MODEL=mimo-v2.6-flash\n")
        self.path.write_text(self.body, encoding="utf-8")

    def test_bom_quoted_config_is_data_without_changing_parent_environment(self):
        original = os.environ.get("PI_MAINTENANCE_API_KEY")
        self.path.write_text(self.body.replace(self.secret, '"' + self.secret + '"'), encoding="utf-8-sig")
        data = load_provider_config(self.path)
        self.assertEqual(self.secret, data["PI_MAINTENANCE_API_KEY"])
        self.assertEqual(original, os.environ.get("PI_MAINTENANCE_API_KEY"))

    def test_bad_config_cannot_include_secret_in_errors(self):
        for body in (self.body + "OTHER=" + self.secret, self.body + "PI_MAINTENANCE_API_KEY=" + self.secret,
                     self.body.replace("https://", "http://"),
                     self.body.replace("api.xiaomimimo.com", "user:password@api.xiaomimimo.com"),
                     self.body.replace("/v1", "/v1?token=" + self.secret),
                     self.body.replace(self.secret, "")):
            self.path.write_text(body, encoding="utf-8")
            with self.subTest(body_kind=len(body)), self.assertRaises(SafetyError) as caught:
                load_provider_config(self.path)
            self.assertNotIn(self.secret, str(caught.exception))

    def test_shell_expressions_are_not_evaluated(self):
        value = "$(Write-Output-dangerous)"
        self.path.write_text(self.body.replace(self.secret, value), encoding="utf-8")
        self.assertEqual(value, load_provider_config(self.path)["PI_MAINTENANCE_API_KEY"])

    def test_invalid_endpoint_ports_are_rejected_before_model_call(self):
        for host in ('api.example.test:invalid', 'api.example.test:65536', 'api.example.test:0', '[broken-ipv6'):
            self.path.write_text(self.body.replace('api.xiaomimimo.com', host), encoding='utf-8')
            with self.subTest(host=host), self.assertRaises(SafetyError) as caught:
                load_provider_config(self.path)
            self.assertNotIn(self.secret, str(caught.exception))

    def test_config_conflicts_rejected_without_spawning_provider(self):
        provider = PiProvider(("calculator.py",), mock_url="http://127.0.0.1:1/v1")
        provider.config_file = self.path
        with patch("repo_maintainer.pi_provider.subprocess.Popen") as popen:
            with self.assertRaises(SafetyError):
                provider.plan(None, "issue", ())
            popen.assert_not_called()

    def test_key_only_reaches_model_process_and_echo_is_redacted(self):
        original = b"def add(a, b):\n    return a - b\n"
        class Repository:
            head = "a" * 40
            def __init__(self, common_dir):
                # Revision history binds to repository identity even in a mock.
                self.common_dir = common_dir
            def read(self, path):
                self.assert_path = path
                return original
        data = {"protocol_version": 1, "status": "completed",
                "patches": [{"path": "calculator.py", "replacements": [{"oldText": "a - b", "newText": "a + b"}]}],
                "plan": {"issue": "addition", "base_sha": "a" * 40,
                         "diagnosis": self.secret, "risk": "fixture",
                         "edits": [{"path": "calculator.py", "before_sha256": digest(original),
                                    "content": "def add(a, b):\n    return a + b\n"}],
                         "tests": [["python", "tests.py"]], "provider": "stub"},
                "evidence": {"schema_version": 1, "status": "completed", "failure_category": None,
                             "requests": 1, "requests_started": 1, "count_scope": "adapter_stream_invocations",
                             "consumption": "reported", "model_cost_verified": False,
                             "events_complete": True, "events": [], "usage": [],
                             "edit_protocol": "exact-replacements-v1"}}
        provider = PiProvider(("calculator.py",))
        provider.config_file = self.path
        with patch("repo_maintainer.pi_provider.shutil.which", return_value="node"), \
                patch("repo_maintainer.pi_provider.subprocess.Popen") as popen:
            popen.return_value.returncode = 0
            popen.return_value.communicate.return_value = (json.dumps(data).encode(), b"")
            plan = provider.plan(Repository(Path(self.temp.name) / "synthetic-git-common"),
                                 "addition", (("python", "tests.py"),))
            self.assertEqual(self.secret, popen.call_args.kwargs["env"]["PI_MAINTENANCE_API_KEY"])
            request = json.loads(popen.return_value.communicate.call_args.args[0])
            self.assertNotIn(self.secret, json.dumps(request))
        self.assertEqual("[REDACTED]", plan.diagnosis)
        self.assertNotIn(self.secret, json.dumps(provider.evidence))
        self.assertEqual("explicit-local-file", provider.evidence["credential_source"])
        self.assertEqual(1, provider.requests_used)
        self.assertEqual("reported", provider.evidence["consumption"])
