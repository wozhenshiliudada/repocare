import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from repo_maintainer.service_readiness import ServiceReadiness


def installed():
    names = ["Python", "git", "node", "Pi npm lock", "React workbench index",
             "filelock", "psutil", "junitparser", "starlette", "uvicorn",
             "Pi dependency @earendil-works/pi-coding-agent", "Pi dependency typebox",
             "React asset /assets/fixture.js", "React asset /assets/fixture.css"]
    return {"ready": True, "checks": [{"name": name, "status": "ok", "detail": "private raw detail"}
                                        for name in names]}


class ServiceReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "provider-fixture.local"
        self.key = "synthetic-local-readiness-key"
        self.body = (f"PI_MAINTENANCE_API_KEY={self.key}\n"
                     "PI_MAINTENANCE_BASE_URL=https://api.fixture.test/v1\n"
                     "PI_MAINTENANCE_MODEL=fixture-model\n")
        self.path.write_text(self.body, encoding="utf-8")
        self.tick = [0.0]
        self.calls = []

    def inspector(self, installer=None, path=None):
        def inspect(root):
            self.calls.append(root)
            return installed()
        return ServiceReadiness(path or self.path, root=self.root,
                                inspect_installation=installer or inspect,
                                monotonic=lambda: self.tick[0], utcnow=lambda: "2026-10-28T00:00:00+00:00")

    def provider(self, value):
        return next(row for row in value["checks"] if row["id"] == "provider_config")

    def assert_private(self, value):
        serialized = json.dumps(value, ensure_ascii=False)
        for private in (self.key, str(self.path), str(self.root), "private raw detail", "api.fixture.test"):
            self.assertNotIn(private, serialized)

    def test_snapshot_never_reads_provider_or_runs_installation(self):
        inspector = self.inspector()
        with patch("repo_maintainer.service_readiness.load_provider_config", side_effect=AssertionError("must not read")):
            value = inspector.snapshot()
        self.assertEqual("unknown", value["status"])
        self.assertIsNone(value["checked_at"])
        self.assertEqual([], self.calls)
        self.assertEqual([], value["checks"])
        self.assertEqual(30, value['valid_for_seconds'])

    def test_complete_local_report_never_claims_network_or_model_validation(self):
        value = self.inspector().inspect()
        self.assertEqual("ready", value["status"])
        self.assertEqual("local_only", value["scope"])
        self.assertIs(value["network_checked"], False)
        self.assertEqual(0, value["model_calls"])
        self.assertEqual(30, value['valid_for_seconds'])
        self.assertEqual({"provider", "installation", "assets", "runtime"}, {row["group"] for row in value["checks"]})
        self.assertIn("尚未验证", self.provider(value)["detail"])
        git = next(row for row in value["checks"] if row["id"] == "git")
        self.assertIn("api.github.com", git["detail"])
        self.assertIn("快照下载（github.com）", git["detail"])
        self.assert_private(value)

    def test_cache_expiry_and_explicit_force_preserve_read_only_snapshot(self):
        inspector = self.inspector()
        first = inspector.inspect()
        first["checks"][0]["status"] = "tampered"
        self.path.unlink()
        self.assertEqual("ready", inspector.inspect()["status"])
        self.assertEqual(1, len(self.calls))
        self.tick[0] = 30
        self.assertTrue(inspector.snapshot()["stale"])
        self.assertEqual("ready", inspector.snapshot()["status"])
        refreshed = inspector.inspect()
        self.assertEqual("blocked", refreshed["status"])
        self.assertEqual(2, len(self.calls))
        self.assertFalse(refreshed["stale"])
        inspector.inspect(force=True)
        self.assertEqual(3, len(self.calls))

    def test_placeholder_file_is_not_a_configured_model(self):
        example = Path(__file__).resolve().parents[1] / ".env.example"
        self.path.write_bytes(example.read_bytes())
        value = self.inspector().inspect()
        self.assertEqual("blocked", self.provider(value)["status"])
        self.assertIn("示例", self.provider(value)["detail"])
        for body in (self.body.replace(self.key, "REPLACE_WITH_YOUR_KEY"),
                     self.body.replace("fixture-model", "REPLACE_WITH_PROVIDER_MODEL"),
                     self.body.replace("api.fixture.test", "api.example.invalid")):
            with self.subTest(body_length=len(body)):
                self.path.write_text(body, encoding="utf-8")
                self.assertEqual("blocked", self.provider(self.inspector().inspect())["status"])

    def test_missing_bad_encoding_duplicate_and_https_errors_are_sanitized(self):
        for content in (b"\xff\xfe", self.body.replace("https://", "http://").encode(),
                        (self.body + f"OTHER={self.key}\n").encode(),
                        (self.body + f"PI_MAINTENANCE_API_KEY={self.key}\n").encode(),
                        self.body.replace("api.fixture.test", f"user:{self.key}@api.fixture.test").encode()):
            self.path.write_bytes(content)
            value = self.inspector().inspect()
            self.assertEqual("blocked", self.provider(value)["status"])
            self.assert_private(value)
        self.path.unlink()
        value = self.inspector().inspect()
        self.assertIn("不存在", self.provider(value)["detail"])
        self.assert_private(value)

    def test_bad_endpoint_ports_are_local_format_failures(self):
        for endpoint in ('https://api.fixture.test:notaport/v1',
                         'https://api.fixture.test:99999/v1', 'https://api.fixture.test:0/v1'):
            with self.subTest(endpoint=endpoint):
                self.path.write_text(self.body.replace('https://api.fixture.test/v1', endpoint), encoding='utf-8')
                value = self.inspector().inspect()
                self.assertEqual('blocked', self.provider(value)['status'])
                self.assert_private(value)
        self.path.write_text(self.body.replace('https://api.fixture.test/v1',
                                              'https://api.fixture.test:8443/v1'), encoding='utf-8')
        self.assertEqual('ready', self.provider(self.inspector().inspect())['status'])

    def test_network_device_and_relative_config_paths_refused_before_filesystem(self):
        candidates = [r'\\untrusted.invalid\share\provider.local',
                      r'\\?\UNC\untrusted.invalid\share\provider.local',
                      r'\\?\C:\provider.local', r'\\.\pipe\provider.local',
                      '//untrusted.invalid/share/provider.local', 'relative-provider.local']
        if os.name == 'nt':
            candidates += [r'C:\private\CON', r'C:\private\config.local:stream', r'C:\private\trailing.']
        for candidate in candidates:
            with self.subTest(kind=candidate[:10]), \
                    patch.object(Path, 'stat') as stat_call, patch.object(Path, 'is_symlink') as linked, \
                    patch('repo_maintainer.service_readiness.load_provider_config') as loader:
                inspector = ServiceReadiness(Path(candidate), root=self.root,
                                             inspect_installation=lambda root: installed())
                value = inspector.inspect()
                self.assertEqual('blocked', self.provider(value)['status'])
                stat_call.assert_not_called()
                linked.assert_not_called()
                loader.assert_not_called()
                self.assertNotIn('untrusted.invalid', json.dumps(value))

    def test_parent_junction_and_symlink_paths_are_refused_before_loader(self):
        inspector = self.inspector()
        with patch("repo_maintainer.service_readiness.is_junction", side_effect=lambda path: path == self.root), \
                patch("repo_maintainer.service_readiness.load_provider_config") as loader:
            value = inspector.inspect()
            loader.assert_not_called()
        self.assertEqual("blocked", self.provider(value)["status"])
        with patch.object(Path, "is_symlink", autospec=True, side_effect=lambda path: path == self.root), \
                patch("repo_maintainer.service_readiness.load_provider_config") as loader:
            value = self.inspector().inspect()
            loader.assert_not_called()
        self.assertEqual("blocked", self.provider(value)["status"])

    def test_loader_and_stat_failures_never_leak_private_errors(self):
        with patch("repo_maintainer.service_readiness.load_provider_config", side_effect=PermissionError(str(self.path) + self.key)):
            value = self.inspector().inspect()
        self.assertEqual("blocked", self.provider(value)["status"])
        self.assert_private(value)
        with patch.object(Path, "stat", side_effect=OSError(str(self.path) + self.key)):
            value = self.inspector().inspect()
        self.assertEqual("blocked", self.provider(value)["status"])
        self.assert_private(value)

    def test_environment_keys_are_never_a_replacement_for_missing_explicit_file(self):
        self.path.unlink()
        with patch.dict("os.environ", {"PI_MAINTENANCE_API_KEY": self.key,
                                       "PI_MAINTENANCE_MODEL": "fixture-model",
                                       "PI_MAINTENANCE_BASE_URL": "https://api.fixture.test/v1"}):
            value = self.inspector().inspect()
        self.assertEqual("blocked", self.provider(value)["status"])
        self.assert_private(value)

    def test_no_explicit_configuration_reports_unconfigured_without_discovery(self):
        inspector = ServiceReadiness(None, root=self.root, inspect_installation=lambda root: installed())
        with patch.dict("os.environ", {"PI_MAINTENANCE_API_KEY": self.key}), \
                patch("repo_maintainer.service_readiness.load_provider_config") as loader:
            value = inspector.inspect()
            loader.assert_not_called()
        self.assertEqual("unknown", value["status"])
        provider = self.provider(value)
        self.assertEqual("unknown", provider["status"])
        self.assertIn("尚未明确指定", provider["detail"])
        self.assertIn("明确指定", provider["next_action"])
        self.assert_private(value)

    def test_installation_failure_or_incomplete_report_cannot_be_ready(self):
        for name in ("node", "Pi dependency @earendil-works/pi-coding-agent", "React asset /assets/fixture.js", "uvicorn"):
            report = installed()
            next(row for row in report["checks"] if row["name"] == name)["status"] = "missing_or_incompatible"
            with self.subTest(name=name):
                value = self.inspector(lambda root: report).inspect()
                self.assertEqual("blocked", value["status"])
                self.assert_private(value)
        for report in (None, {}, {"checks": []}, {"checks": [{"name": "Python", "status": "ok"}]}):
            value = self.inspector(lambda root: report).inspect()
            self.assertNotEqual("ready", value["status"])

    def test_missing_source_inputs_are_unknown_instead_of_fake_ready(self):
        value = ServiceReadiness(self.path, root=self.root).inspect()
        self.assertEqual("unknown", value["status"])
        self.assertEqual("ready", self.provider(value)["status"])

    def test_package_checker_does_not_execute_project_script_or_change_sys_path(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        doctor = scripts / 'doctor.py'
        doctor.write_text('raise RuntimeError("must never execute")\n', encoding='utf-8')
        before = sys.path[:]
        with patch('repo_maintainer.installation_check.inspect_environment', return_value=installed()) as checker, \
                patch('importlib.util.spec_from_file_location', side_effect=AssertionError('no project script loading')):
            value = ServiceReadiness(self.path, root=self.root).inspect()
        self.assertEqual('ready', value['status'])
        checker.assert_called_once_with(root=self.root, profile='web')
        self.assertEqual(before, sys.path)

    def test_installed_wheel_explains_missing_pi_instead_of_source_dev_failures(self):
        report = installed()
        report['installation_kind'] = 'installed'
        for row in report['checks']:
            if row['name'].startswith('Pi '):
                row['status'] = 'missing_or_incompatible'
        value = self.inspector(lambda root: report).inspect()
        self.assertEqual('blocked', value['status'])
        by_id = {row['id']: row for row in value['checks']}
        self.assertEqual('ready', by_id['python_dependencies']['status'])
        self.assertIn('Python 安装包未携带', by_id['pi_lock']['detail'])
        self.assertIn('Python 安装包不包含', by_id['pi_dependencies']['next_action'])

    def test_parent_link_is_checked_before_child_metadata_can_traverse_it(self):
        visited = []
        def linked(item):
            visited.append(item)
            return item == self.root
        with patch.object(Path, 'is_symlink', autospec=True, side_effect=linked), \
                patch('repo_maintainer.service_readiness.is_junction', return_value=False), \
                patch('repo_maintainer.service_readiness.load_provider_config') as loader:
            value = self.inspector().inspect()
            self.assertEqual('blocked', self.provider(value)['status'])
            self.assertNotIn(self.path, visited)
            loader.assert_not_called()

    def test_packaged_runtime_requires_its_resource_integrity_row(self):
        report = installed()
        report['installation_kind'] = 'installed'
        report['pi_runtime'] = {'present': True, 'kind': 'wheel'}
        report['checks'].extend([
            {'name': 'Pi runtime', 'status': 'ok'},
            {'name': 'Pi package resources', 'status': 'ok'},
        ])
        value = self.inspector(lambda root: report).inspect()
        self.assertEqual('ready', value['status'])
        self.assert_private(value)
        resources = report['checks'].pop()
        self.assertNotEqual('ready', self.inspector(lambda root: report).inspect()['status'])
        report['checks'].append({**resources, 'status': 'missing_or_incompatible'})
        blocked = self.inspector(lambda root: report).inspect()
        self.assertEqual('blocked', blocked['status'])
        self.assertEqual('blocked', next(row for row in blocked['checks']
                                        if row['id'] == 'pi_resources')['status'])
        self.assertEqual(0, blocked['model_calls'])

    def test_concurrent_refresh_does_not_dispatch_duplicate_inspection(self):
        started, release = threading.Event(), threading.Event()
        def blocked(root):
            self.calls.append(root)
            started.set()
            self.assertTrue(release.wait(3))
            return installed()
        inspector = self.inspector(blocked)
        result = []
        worker = threading.Thread(target=lambda: result.append(inspector.inspect()))
        worker.start()
        try:
            self.assertTrue(started.wait(1))
            value = inspector.inspect(force=True)
            self.assertEqual("unknown", value["status"])
            self.assertEqual(1, len(self.calls))
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual("ready", result[0]["status"])


if __name__ == "__main__":
    unittest.main()
