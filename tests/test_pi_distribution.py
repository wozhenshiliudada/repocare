"""Pi distribution locator, package staging and explicit install contract.

These are unit tests over temporary fixtures. They do not build a wheel, run
npm/Node, touch a real repository, read provider files or call a model.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from repo_maintainer import pi_distribution as dist

ROOT = Path(__file__).resolve().parents[1]
REAL_PACKAGE = ROOT / 'src/repo_maintainer'


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha512_integrity(data: bytes) -> str:
    return 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode('ascii')


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def tarball_map() -> dict[str, bytes]:
    return {
        dist._pack_filename(dist.PI_PACKAGE, '1.0.4'): b'pi-agent-tarball-bytes',
        dist._pack_filename('typebox', '1.3.27'): b'typebox-tarball-bytes',
    }


def make_lock(tarballs: dict[str, bytes]) -> dict:
    entries = {
        dist.PI_PACKAGE: ('1.0.4', 'https://registry.npmjs.org/@earendil-works/pi-coding-agent/-/pi-coding-agent-1.0.4.tgz'),
        'typebox': ('1.3.27', 'https://registry.npmjs.org/typebox/-/typebox-1.3.27.tgz'),
    }
    packages = {'': {'dependencies': dict(dist.REQUIRED_DEPENDENCIES)}}
    for name, (version, resolved) in entries.items():
        packages['node_modules/' + name] = {
            'version': version,
            'resolved': resolved,
            'integrity': sha512_integrity(tarballs[dist._pack_filename(name, version)]),
            'license': 'MIT',
        }
    return {'name': 'contest-pi-maintenance-core', 'version': '0.1.0',
            'lockfileVersion': 3, 'requires': True, 'packages': packages}


def make_repo(temp: Path, *, vendor: bool = True, license_text: str | None = 'MIT fixture') -> tuple[Path, dict[str, bytes]]:
    repo = temp / 'repo'
    runtime = repo / 'runtime/pi'
    runtime.mkdir(parents=True)
    for name in dist.REQUIRED_RUNTIME_FILES:
        if name not in {dist.PACKAGE_NAME, dist.LOCK_NAME}:
            (runtime / name).write_text(f'// {name}\n', encoding='utf-8')
    (runtime / 'provider-payload.mjs').write_text('// production module\n', encoding='utf-8')
    (runtime / 'ui-browser-smoke.mjs').write_text('// test harness\n', encoding='utf-8')
    (runtime / 'replay.mjs').write_text('// test harness\n', encoding='utf-8')
    (runtime / 'test').mkdir()
    (runtime / 'test/example.test.mjs').write_text('// never packaged\n', encoding='utf-8')
    (runtime / 'node_modules').mkdir()
    (runtime / 'node_modules/example').write_text('// never packaged\n', encoding='utf-8')
    (runtime / '.env.local').write_text('SECRET=should-not-be-packaged\n', encoding='utf-8')
    (runtime / 'README.md').write_text('# Pi runtime\n', encoding='utf-8')
    tarballs = tarball_map()
    lock = make_lock(tarballs)
    write_json(runtime / dist.PACKAGE_NAME, {
        'name': 'contest-pi-maintenance-core', 'version': '0.1.0',
        'private': True, 'type': 'module',
        'engines': {'node': '>=22.19.0'},
        'dependencies': dict(dist.REQUIRED_DEPENDENCIES),
    })
    write_json(runtime / dist.LOCK_NAME, lock)
    if vendor:
        (runtime / 'vendor').mkdir()
        for name, data in tarballs.items():
            (runtime / 'vendor' / name).write_bytes(data)
    package = repo / 'src/repo_maintainer'
    package.mkdir(parents=True)
    for name in dist.LIMIT_FILES:
        shutil.copyfile(REAL_PACKAGE / name, package / name)
    third = repo / 'third_party/pi'
    third.mkdir(parents=True)
    (third / 'PROVENANCE.md').write_text('Pi upstream provenance fixture\n', encoding='utf-8')
    if license_text is not None:
        (third / 'PI-LICENSE').write_text(license_text, encoding='utf-8')
    return repo, tarballs


def stage(temp: Path, **kwargs):
    repo, tarballs = make_repo(temp, **kwargs)
    package_root = temp / 'build/repo_maintainer'
    destination = package_root / 'pi_runtime'
    package_root.mkdir(parents=True)
    for name in dist.LIMIT_FILES:
        shutil.copyfile(REAL_PACKAGE / name, package_root / name)
    written = dist.stage_pi_runtime(repo, destination)
    return repo, tarballs, package_root, destination, written


def make_runtime(temp: Path, *, installed: bool) -> dist.PiRuntime:
    root = temp / 'runtime'
    root.mkdir()
    package = {'engines': {'node': '>=22.19.0'},
               'dependencies': dict(dist.REQUIRED_DEPENDENCIES)}
    write_json(root / dist.PACKAGE_NAME, package)
    lock = {'lockfileVersion': 3, 'packages': {'': {'dependencies': dict(dist.REQUIRED_DEPENDENCIES)}}}
    write_json(root / dist.LOCK_NAME, lock)
    node_modules = root / 'node_modules'
    if installed:
        for name, version in dist.REQUIRED_DEPENDENCIES.items():
            directory = node_modules / name
            directory.mkdir(parents=True)
            write_json(directory / 'package.json', {'version': version})
    return dist.PiRuntime(root=root, kind='source', version='1.0.4', package=package,
                          lock_sha256=sha256((root / dist.LOCK_NAME).read_bytes()),
                          node_modules=node_modules, distribution_complete=True)


class LocatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_source_layout_uses_the_original_runtime_directory(self):
        repo, _ = make_repo(self.root)
        runtime = dist.locate_runtime(package_root=repo / 'src/repo_maintainer')
        self.assertEqual('source', runtime.kind)
        self.assertEqual(repo / 'runtime/pi', runtime.root)
        self.assertEqual('1.0.4', runtime.version)
        self.assertEqual((repo / 'runtime/pi/plan.mjs').read_bytes(),
                         runtime.script('plan.mjs').read_bytes())
        with self.assertRaisesRegex(dist.PiDistributionError, '脚本名'):
            runtime.script('../plan.mjs')

    def test_wheel_layout_verifies_manifest_and_returns_verified_files(self):
        repo, tarballs, package_root, destination, written = stage(self.root)
        runtime = dist.locate_runtime(package_root=package_root)
        self.assertEqual('wheel', runtime.kind)
        self.assertTrue(runtime.distribution_complete)
        self.assertEqual('1.0.4', runtime.version)
        self.assertEqual(sha256((destination / 'plan.mjs').read_bytes()),
                         sha256(runtime.script('plan.mjs').read_bytes()))
        manifest = json.loads((destination / dist.MANIFEST_NAME).read_text(encoding='utf-8'))
        self.assertEqual(sha256((destination / 'plan.mjs').read_bytes()),
                         manifest['files']['plan.mjs'])
        self.assertEqual(2, manifest['vendor']['expected'])
        self.assertTrue(manifest['vendor']['complete'])
        self.assertTrue(manifest['licenses']['complete'])
        self.assertIn(dist.MANIFEST_NAME, {path.name for path in written})

    def test_tampered_runtime_file_is_rejected(self):
        _, _, package_root, destination, _ = stage(self.root)
        (destination / 'plan.mjs').write_text('// tampered\n', encoding='utf-8')
        with self.assertRaises(dist.PiDistributionError) as raised:
            dist.locate_runtime(package_root=package_root)
        self.assertEqual('tampered', raised.exception.reason)

    def test_tampered_limits_file_is_rejected(self):
        _, _, package_root, _, _ = stage(self.root)
        (package_root / 'pi_runtime_limits.json').write_text('{"max_requests": 1}', encoding='utf-8')
        with self.assertRaises(dist.PiDistributionError) as raised:
            dist.locate_runtime(package_root=package_root)
        self.assertEqual('tampered', raised.exception.reason)

    def test_manifest_path_escape_is_rejected(self):
        _, _, package_root, destination, _ = stage(self.root)
        manifest = json.loads((destination / dist.MANIFEST_NAME).read_text(encoding='utf-8'))
        manifest['files']['../escape.mjs'] = sha256(b'escape')
        (destination / dist.MANIFEST_NAME).write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(dist.PiDistributionError) as raised:
            dist.locate_runtime(package_root=package_root)
        self.assertEqual('tampered', raised.exception.reason)

    def test_missing_runtime_is_reported_as_missing_not_tampered(self):
        with self.assertRaises(dist.PiDistributionError) as raised:
            dist.locate_runtime(package_root=self.root / 'empty')
        self.assertEqual('missing', raised.exception.reason)

    def test_incomplete_vendor_and_license_are_visible_but_runtime_still_executes(self):
        _, _, package_root, _, _ = stage(self.root, vendor=False, license_text=None)
        runtime = dist.locate_runtime(package_root=package_root)
        self.assertFalse(runtime.distribution_complete)
        self.assertEqual(2, len(runtime.vendor_missing))
        self.assertTrue(runtime.script('plan.mjs').is_file())

    def test_online_distribution_does_not_require_a_complete_registry_cache(self):
        _,_,package_root,_,_=stage(self.root,vendor=False)
        runtime=dist.locate_runtime(package_root=package_root)
        self.assertTrue(runtime.distribution_complete)
        self.assertFalse(runtime.vendor_cache_complete)
        self.assertEqual(len(runtime.vendor_missing),2)

    @unittest.skipIf(os.name == 'nt', 'creating symlinks may require privileges')
    def test_symlinked_runtime_file_is_rejected(self):
        _, _, package_root, destination, _ = stage(self.root)
        target = destination / 'plan.mjs'
        backup = destination / 'plan.backup'
        target.rename(backup)
        target.symlink_to(backup)
        with self.assertRaises(dist.PiDistributionError) as raised:
            dist.locate_runtime(package_root=package_root)
        self.assertEqual('tampered', raised.exception.reason)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_staging_copies_runtime_and_excludes_tests_secrets_and_node_modules(self):
        _, _, _, destination, _ = stage(self.root)
        names = {path.name for path in destination.iterdir()}
        self.assertIn('plan.mjs', names)
        self.assertIn('provider-payload.mjs', names)
        self.assertIn(dist.PACKAGE_NAME, names)
        self.assertIn(dist.LOCK_NAME, names)
        self.assertIn('vendor', names)
        self.assertNotIn('ui-browser-smoke.mjs', names)
        self.assertNotIn('replay.mjs', names)
        self.assertNotIn('test', names)
        self.assertNotIn('node_modules', names)
        self.assertNotIn('.env.local', names)
        self.assertFalse((destination / '.env.local').exists())
        self.assertFalse((destination / 'test').exists())
        self.assertFalse((destination / 'node_modules').exists())

    def test_staging_records_exact_byte_hashes_and_dependency_notices(self):
        _, tarballs, _, destination, _ = stage(self.root)
        manifest = json.loads((destination / dist.MANIFEST_NAME).read_text(encoding='utf-8'))
        for name, expected in manifest['files'].items():
            self.assertEqual(sha256((destination / name).read_bytes()), expected)
        self.assertEqual(sha256((destination / dist.LOCK_NAME).read_bytes()), manifest['lock_sha256'])
        for name in dist.LIMIT_FILES:
            self.assertEqual(sha256((REAL_PACKAGE / name).read_bytes()), manifest['limits'][name])
        for name, data in tarballs.items():
            self.assertEqual(sha256(data), manifest['vendor']['files'][name])
        notices = json.loads((destination / 'LICENSES/DEPENDENCY-LICENSES.json').read_text(encoding='utf-8'))
        self.assertEqual({'@earendil-works/pi-coding-agent', 'typebox'},
                         {row['name'] for row in notices})
        self.assertEqual({'MIT'}, {row['license'] for row in notices})

    def test_staging_rejects_vendor_tarball_not_in_lockfile(self):
        repo, _, package_root, destination, _ = stage(self.root)
        (destination / 'vendor' / 'unexpected-9.9.9.tgz').write_bytes(b'unexpected')
        # Re-stage from a source tree containing the extra tarball.
        (repo / 'runtime/pi/vendor/unexpected-9.9.9.tgz').write_bytes(b'unexpected')
        with self.assertRaises(dist.PiDistributionError):
            dist.stage_pi_runtime(repo, destination)

    def test_staging_rejects_corrupted_vendor_tarball(self):
        repo, _, _, destination, _ = stage(self.root)
        (repo / 'runtime/pi/vendor' / dist._pack_filename('typebox', '1.3.27')).write_bytes(b'wrong')
        with self.assertRaises(dist.PiDistributionError) as raised:
            dist.stage_pi_runtime(repo, destination)
        self.assertEqual('tampered', raised.exception.reason)


class InstallPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_already_installed_plan_does_not_spawn_node_or_npm(self):
        runtime = make_runtime(self.root, installed=True)
        plan = dist.plan_install(runtime, which=lambda name: None,
                                 execute=lambda *args, **kwargs: self.fail('must not execute'))
        self.assertEqual('none', plan.action)
        self.assertEqual(64, len(plan.approval))
        self.assertEqual('ci', plan.command[1])
        result = dist.execute_install(plan, plan.approval, runner=lambda *a, **k: self.fail('must not run'))
        self.assertFalse(result['executed'])
        self.assertEqual(0, result['model_calls'])

    def test_install_plan_uses_one_fixed_npm_ci_command(self):
        runtime = make_runtime(self.root, installed=False)
        plan = dist.plan_install(runtime, which=lambda name: '/fake/' + name,
                                 execute=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, b'v24.16.0', b''))
        self.assertEqual('install', plan.action)
        self.assertEqual(('/fake/npm', 'ci', '--prefix', str(runtime.root),
                          '--ignore-scripts', '--no-audit', '--no-fund'), plan.command)
        self.assertEqual('v24.16.0', plan.node_version)
        self.assertEqual(64, len(plan.approval))

    def test_missing_node_and_npm_are_explicit_and_stop_before_install(self):
        runtime = make_runtime(self.root, installed=False)
        with self.assertRaisesRegex(dist.PiDistributionError, '未找到 Node.js'):
            dist.plan_install(runtime, which=lambda name: None)
        with self.assertRaisesRegex(dist.PiDistributionError, '未找到 npm'):
            dist.plan_install(runtime, which=lambda name: None if name in {'npm', 'npm.cmd'} else '/fake/node',
                              execute=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, b'v24.16.0', b''))

    def test_old_node_is_rejected(self):
        runtime = make_runtime(self.root, installed=False)
        with self.assertRaisesRegex(dist.PiDistributionError, '版本过低'):
            dist.plan_install(runtime, which=lambda name: '/fake/' + name,
                              execute=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, b'v22.18.0', b''))

    def test_approval_is_required_and_lock_changes_are_rejected(self):
        runtime = make_runtime(self.root, installed=False)
        plan = dist.plan_install(runtime, which=lambda name: '/fake/' + name,
                                 execute=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, b'v24.16.0', b''))
        with self.assertRaisesRegex(dist.PiDistributionError, '确认码不一致'):
            dist.execute_install(plan, '0' * 64, runner=lambda *a, **k: self.fail('must not run'))
        (runtime.root / dist.LOCK_NAME).write_text('{"lockfileVersion": 3}', encoding='utf-8')
        with self.assertRaisesRegex(dist.PiDistributionError, '确认后变化'):
            dist.execute_install(plan, plan.approval, runner=lambda *a, **k: self.fail('must not run'))

    def test_approved_install_runs_fixed_command_and_rechecks_lock(self):
        runtime = make_runtime(self.root, installed=False)
        plan = dist.plan_install(runtime, which=lambda name: '/fake/' + name,
                                 execute=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, b'v24.16.0', b''))
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            for name, version in dist.REQUIRED_DEPENDENCIES.items():
                directory = runtime.node_modules / name
                directory.mkdir(parents=True, exist_ok=True)
                write_json(directory / 'package.json', {'version': version})
            return subprocess.CompletedProcess(argv, 0, b'ok', b'')

        result = dist.execute_install(plan, plan.approval, runner=runner)
        self.assertTrue(result['executed'])
        self.assertEqual(1, len(calls))
        self.assertEqual(list(plan.command), calls[0][0])
        self.assertEqual(str(runtime.root), calls[0][1]['cwd'])
        self.assertNotIn('PI_MAINTENANCE_API_KEY', calls[0][1]['env'])

    def test_npm_failure_keeps_error_and_does_not_retry(self):
        runtime = make_runtime(self.root, installed=False)
        plan = dist.plan_install(runtime, which=lambda name: '/fake/' + name,
                                 execute=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, b'v24.16.0', b''))
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 1, b'npm failed _authToken=secret', b'')

        with self.assertRaises(dist.PiDistributionError) as raised:
            dist.execute_install(plan, plan.approval, runner=runner)
        self.assertIn('退出码 1', str(raised.exception))
        self.assertNotIn('secret', str(raised.exception))
        self.assertEqual(1, len(calls))


class VendorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_complete_vendor_directory_produces_no_downloads(self):
        _, tarballs, package_root, _, _ = stage(self.root)
        runtime = dist.locate_runtime(package_root=package_root)
        plan = dist.vendor_plan(runtime, which=lambda name: '/fake/npm')
        self.assertEqual((), plan.missing)
        self.assertEqual(2, len(plan.specs))
        self.assertEqual('complete', dist.vendor_runtime(plan, plan.approval,
                                                          runner=lambda *a, **k: self.fail('must not run'))['vendor'])

    def test_missing_tarball_plan_downloads_only_locked_specs(self):
        _, tarballs, package_root, destination, _ = stage(self.root)
        runtime = dist.locate_runtime(package_root=package_root)
        missing = dist._pack_filename('typebox', '1.3.27')
        (destination / 'vendor' / missing).unlink()
        plan = dist.vendor_plan(runtime, which=lambda name: '/fake/npm')
        self.assertEqual((missing,), plan.missing)
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            (destination / 'vendor' / missing).write_bytes(tarballs[missing])
            return subprocess.CompletedProcess(argv, 0, b'', b'')

        result = dist.vendor_runtime(plan, plan.approval, runner=runner)
        self.assertEqual(1, result['downloaded'])
        self.assertEqual('pack', calls[0][1])
        self.assertIn('typebox@1.3.27', calls[0])
        self.assertIn('--pack-destination', calls[0])
        self.assertIn('--ignore-scripts', calls[0])

    def test_corrupted_existing_tarball_blocks_vendor_plan(self):
        _, _, package_root, destination, _ = stage(self.root)
        runtime = dist.locate_runtime(package_root=package_root)
        (destination / 'vendor' / dist._pack_filename('typebox', '1.3.27')).write_bytes(b'wrong')
        with self.assertRaises(dist.PiDistributionError) as raised:
            dist.vendor_plan(runtime, which=lambda name: '/fake/npm')
        self.assertEqual('tampered', raised.exception.reason)

    def test_vendor_approval_is_required(self):
        repo, _, package_root, destination, _ = stage(self.root)
        runtime = dist.locate_runtime(package_root=package_root)
        missing = dist._pack_filename('typebox', '1.3.27')
        (destination / 'vendor' / missing).unlink()
        plan = dist.vendor_plan(runtime, which=lambda name: '/fake/npm')
        with self.assertRaisesRegex(dist.PiDistributionError, 'vendor 确认码'):
            dist.vendor_runtime(plan, '0' * 64, runner=lambda *a, **k: self.fail('must not run'))


class CliTests(unittest.TestCase):
    def test_runtime_install_is_wired_and_defaults_to_diagnosis(self):
        from repo_maintainer.cli import main
        with patch('repo_maintainer.pi_distribution.locate_runtime',
                   side_effect=dist.PiDistributionError('missing fixture', reason='missing')):
            with patch('builtins.print'):
                self.assertEqual(1, main(['runtime', 'install', '--json']))


class InstallationCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_installed_checker_marks_a_complete_packaged_runtime(self):
        from repo_maintainer import installation_check
        _, _, package_root, destination, _ = stage(self.root)
        for name, version in dist.REQUIRED_DEPENDENCIES.items():
            directory = destination / 'node_modules' / name
            directory.mkdir(parents=True)
            write_json(directory / 'package.json', {'version': version})
        ui = package_root / 'workbench_ui'
        (ui / 'assets').mkdir(parents=True)
        (ui / 'index.html').write_text('<script src="/assets/app.js"></script>'
                                       '<link href="/assets/app.css" rel="stylesheet">', encoding='utf-8')
        (ui / 'assets/app.js').write_text('', encoding='utf-8')
        (ui / 'assets/app.css').write_text('', encoding='utf-8')
        versions = {'filelock': '4.0.12', 'psutil': '7.2.2', 'junitparser': '5.0.3',
                    'starlette': '1.7.0', 'uvicorn': '0.53.0',
                    dist.PI_PACKAGE: '1.0.4', 'typebox': '1.3.27'}

        def package_version(name):
            from importlib.metadata import PackageNotFoundError
            if name not in versions:
                raise PackageNotFoundError(name)
            return versions[name]

        result = installation_check.inspect_environment(
            self.root, 'web', installation_kind='installed', package_root=package_root,
            package_version=package_version, which=lambda name: name,
            execute=lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 0, b'git version 2.53.0' if argv[0] == 'git' else b'v24.16.0', b''),
            distribution_requirements=lambda name: [
                'filelock==4.0.12', 'psutil==7.2.2', 'junitparser==5.0.3',
                'starlette==1.7.0; extra == "ui"', 'uvicorn==0.53.0; extra == "ui"'],
            python_version=(3, 13, 3))
        checks = {row['name']: row for row in result['checks']}
        self.assertTrue(result['ready'])
        self.assertTrue(checks['Pi runtime']['status'] == 'ok')
        self.assertTrue(checks['Pi package resources']['status'] == 'ok')
        self.assertEqual('ok', checks['Pi dependency @earendil-works/pi-coding-agent']['status'])
        self.assertEqual('ok', checks['Pi dependency typebox']['status'])
        self.assertEqual(0, result['model_calls'])
        self.assertEqual(0, result['provider_files_read'])


if __name__ == '__main__':
    unittest.main()
