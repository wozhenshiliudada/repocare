"""Real static-route layout and cross-platform failures observed in #13/#14 CI."""
import errno
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from repo_maintainer import ui_backup
from repo_maintainer.knowledge_memory import KnowledgeMemory
from repo_maintainer.maintenance_memory import MaintenanceMemory
from repo_maintainer.models import SafetyError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('doctor_contract', ROOT/'scripts/doctor.py')
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)

# The HTTP test below verifies these URLs against the production router, so a
# wrong directory assumption cannot pass using only a simplified React fixture.
ROUTES = {
    '/maintenance.css': 'workbench_assets/maintenance.css',
    '/diff2html.min.css': 'dashboard_assets/vendor/diff2html.min.css',
    '/diff2html.min.js': 'dashboard_assets/vendor/diff2html.min.js',
    '/session-access.js': 'local_ui_assets/session-access.js',
    '/maintenance.js': 'local_ui_assets/app.js',
    '/onboarding.js': 'workbench_assets/app.js',
}


class AssetContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root/'src/repo_maintainer'
        self.ui = self.package/'workbench_ui'
        (self.ui/'assets').mkdir(parents=True)
        (self.ui/'assets/app.js').write_text('/* build */')
        self.index = '<script src="/assets/app.js"></script>' + ''.join(
            f'<script src="{url}"></script>' for url in ROUTES)
        (self.ui/'index.html').write_text(self.index, encoding='utf-8')
        for relative in ROUTES.values():
            target = self.package/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('/* backend route */')

    def check(self):
        rows = []
        doctor.check_workbench_assets(self.root, lambda name, ok, detail: rows.append((name, ok, detail)))
        return rows

    def test_real_multi_root_layout_passes_and_each_missing_route_is_reported(self):
        self.assertTrue(all(row[1] for row in self.check()), self.check())
        for url, relative in ROUTES.items():
            with self.subTest(url=url):
                target = self.package/relative
                raw = target.read_bytes()
                target.unlink()
                self.assertIn('React asset '+url, [name for name, ok, _ in self.check() if not ok])
                target.write_bytes(raw)

    def test_backend_routes_match_actual_http_responses(self):
        from starlette.testclient import TestClient
        from repo_maintainer.repository_workbench import RepositoryWorkbench, create_workbench_app
        config = self.root/'unused.provider.local'
        config.write_text('not read for static HTTP requests')
        hub = RepositoryWorkbench(self.root/'state', config, ui='legacy')
        self.addCleanup(hub.close)
        with TestClient(create_workbench_app(hub, 12345), base_url='http://127.0.0.1:12345') as client:
            for url, relative in ROUTES.items():
                with self.subTest(url=url):
                    response = client.get(url)
                    self.assertEqual(200, response.status_code)
                    self.assertEqual((ROOT/'src/repo_maintainer'/relative).read_bytes(), response.content)

    def test_mapped_resources_keep_link_and_traversal_boundaries(self):
        target = self.package/ROUTES['/maintenance.js']
        real = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda p: p == target or real(p)):
            self.assertIn('React asset /maintenance.js', [name for name, ok, _ in self.check() if not ok])
        unsafe = ['/../local_ui_assets/app.js', '/%2e%2e/local_ui_assets/app.js',
                  '/%5clocal_ui_assets/app.js', '/not-a-route.js']
        for url in unsafe:
            with self.subTest(url=url):
                (self.ui/'index.html').write_text(f'<script src="{url}"></script>')
                self.assertTrue(any(not ok for _, ok, _ in self.check()))


class PlatformContractTests(unittest.TestCase):
    def test_non_directory_probe_means_absent_but_permission_and_links_still_fail(self):
        # Linux returns ENOTDIR for file.sqlite3/.git; Windows may return ENOENT.
        with patch.object(ui_backup, 'unlinked', side_effect=NotADirectoryError(errno.ENOTDIR, 'not a directory')):
            self.assertFalse(ui_backup.present(Path('memory.sqlite3/.git')))
        for error in (PermissionError('denied'), SafetyError('link')):
            with self.subTest(error=type(error).__name__), patch.object(ui_backup, 'unlinked', side_effect=error):
                with self.assertRaises(type(error)):
                    ui_backup.present(Path('memory.sqlite3/.git'))

    def test_memory_without_task_identity_returns_none_without_reading_sources(self):
        for cls in (KnowledgeMemory, MaintenanceMemory):
            with self.subTest(memory=cls.__name__):
                memory = object.__new__(cls)
                memory.session = SimpleNamespace(task_planner=None)
                with patch.object(memory, 'verify_task', side_effect=AssertionError('unexpected source read')):
                    self.assertIsNone(memory.context('issue', None))
                # An explicit identity still passes through the original validation gate.
                with patch.object(memory, 'verify_task', side_effect=SafetyError('changed pin')):
                    with self.assertRaisesRegex(SafetyError, 'changed pin'):
                        memory.context('issue', None, 'a'*32)
