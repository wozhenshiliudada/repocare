"""Package-owned read-only setup checks for source and installed layouts.

No provider files, installs, project tests or model calls. A source checkout uses
its original ``runtime/pi``; a wheel may carry a verified ``pi_runtime`` and is
reported as an incomplete offline distribution when vendor assets are absent.
"""
from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, requires, version
import json
from pathlib import Path
import re
import shutil
import subprocess
import os
import sys
import tomllib
from urllib.parse import unquote

from .path_safety import is_junction
from .pi_distribution import REQUIRED_DEPENDENCIES, PiDistributionError, locate_runtime

PACKAGE = Path(__file__).resolve().parent
SOURCE_LAYOUT = PACKAGE.parent.name == 'src'
ROOT = PACKAGE.parents[1] if SOURCE_LAYOUT else PACKAGE.parent
MINIMUM_NODE = (22, 19, 0)


BUILD_STEP = 'npm --prefix frontend ci && npm --prefix frontend run build'
UI_ROOT = Path('src/repo_maintainer/workbench_ui')
REFERENCE = re.compile(r'''\b(?:src|href)\s*=\s*["']([^"']+)["']''', re.IGNORECASE)
# Exact backend URLs, not an additional filesystem search path. The contract
# test compares these with actual production HTTP responses.
SERVER_ASSETS = {
    'maintenance.css': 'workbench_assets/maintenance.css',
    'diff2html.min.css': 'dashboard_assets/vendor/diff2html.min.css',
    'diff2html.min.js': 'dashboard_assets/vendor/diff2html.min.js',
    'session-access.js': 'local_ui_assets/session-access.js',
    'maintenance.js': 'local_ui_assets/app.js',
    'onboarding.js': 'workbench_assets/app.js',
}


def _linked(path):
    """True when any component of path is a symlink or Windows junction."""
    absolute = path.absolute()
    for item in reversed((absolute, *absolute.parents)):
        if item.is_symlink() or is_junction(item):
            return True
    return False


def _local_asset(ui, reference):
    """Map a same-origin reference to a file inside ui, or None when it is not local.

    Percent-decode fully before validating, so an encoded separator or traversal can
    never slip past the checks. Never follows symlinks, never leaves ui, and never
    fetches or executes anything.
    """
    value = reference.strip()
    if not value or value.startswith('//'):  # protocol-relative: external, no fetch
        return None
    if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', value):  # http:, data:, javascript:, ...
        return None
    value = value.split('#', 1)[0].split('?', 1)[0]
    for _ in range(8):  # fully decode; bounded, no unbounded recursion
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    if '\\' in value or '\x00' in value or re.match(r'^[A-Za-z]:', value):
        return ui  # rejected marker: Windows separator, drive letter or NUL
    if value.startswith('/'):
        value = value[1:]
    parts = [p for p in value.split('/') if p not in ('', '.')]
    if not parts or any(p == '..' for p in parts):
        return ui  # rejected marker: traversal segment
    relative = '/'.join(parts)
    if relative in SERVER_ASSETS:
        target = ui.parent/SERVER_ASSETS[relative]
        return ui if _linked(target) else target
    target = ui.joinpath(*parts)
    try:
        target.relative_to(ui)
        if os.path.commonpath([str(target.absolute()), str(ui.absolute())]) != str(ui.absolute()):
            return ui  # rejected marker: resolved outside the UI root
    except (ValueError, OSError):
        return ui  # rejected marker: unsafe reference
    for item in (target, *target.parents):  # every component below the UI root
        if item == ui:
            break
        if item.is_symlink() or is_junction(item):
            return ui  # rejected marker: symlinked component
    return target


def check_workbench_assets(root, add, *, package_root=None):
    """Read-only check that the React workbench build output exists and is complete."""
    ui = root/UI_ROOT if package_root is None else package_root/'workbench_ui'
    displayed = UI_ROOT if package_root is None else Path('repo_maintainer/workbench_ui')
    index = ui/'index.html'
    if _linked(ui) or _linked(index):
        add('React workbench index', False,
            f'{displayed} must be a real directory and index.html a real file; '
            f'rebuild the frontend: {BUILD_STEP}')
        return
    if not index.is_file():
        add('React workbench index', False,
            f'{displayed}/index.html is missing; build the frontend: {BUILD_STEP}')
        return
    try:
        text = index.read_text(encoding='utf-8', errors='replace')
    except OSError:
        add('React workbench index', False,
            f'{displayed}/index.html is unreadable; rebuild the frontend: {BUILD_STEP}')
        return
    add('React workbench index', True, f'{displayed}/index.html')
    references = []
    for match in REFERENCE.finditer(text):
        reference = match.group(1).strip()
        if re.search(r'\.(?:js|mjs|css)\s*$', reference.split('#', 1)[0].split('?', 1)[0], re.IGNORECASE):
            references.append(reference)
    for reference in list(dict.fromkeys(references)):
        target = _local_asset(ui, reference)
        if target is None:
            continue  # external reference: never fetched
        if target == ui or not target.is_file():
            add('React asset ' + reference, False,
                f'missing or unsafe local asset referenced by {displayed}/index.html; '
                f'build the frontend: {BUILD_STEP}')
            continue
        add('React asset ' + reference, True, target.relative_to(root).as_posix())


def inspect_environment(root=None, profile='web', *, package_version=version,
                        which=shutil.which, execute=subprocess.run, python_version=None,
                        installation_kind=None, package_root=None, distribution_requirements=requires):
    if profile not in {'core', 'web', 'dev'}:
        raise ValueError('Unknown environment profile')
    kind = installation_kind or ('source' if SOURCE_LAYOUT else 'installed')
    if kind not in {'source', 'installed'}:
        raise ValueError('Unknown application installation kind')
    root = Path(root) if root is not None else ROOT
    package_root = Path(package_root) if package_root is not None else PACKAGE
    checks = []
    def add(name, ok, detail):
        checks.append({'name': name, 'status': 'ok' if ok else 'missing_or_incompatible', 'detail': detail})
    py = tuple(python_version or sys.version_info[:3])
    add('Python', py >= (3, 11), '.'.join(map(str, py)) + '; requires >=3.11')
    if kind == 'source':
        config = tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8'))
        requirements = list(config['project']['dependencies'])
        if profile in {'web', 'dev'}:
            requirements += config['project']['optional-dependencies']['ui']
        if profile == 'dev':
            requirements += config['project']['optional-dependencies']['test']
    else:
        # Wheel METADATA is emitted by the same pyproject. Never search the
        # working directory for a replacement source or dependency declaration.
        try:
            declarations = distribution_requirements('repo-maintainer')
            if not isinstance(declarations, list) or not declarations:
                raise ValueError('Missing package dependency metadata')
        except (PackageNotFoundError, ValueError):
            declarations = []
            add('Package dependency metadata', False, 'Installed distribution dependency metadata is unavailable')
        selected_extras = {'ui'} if profile in {'web', 'dev'} else set()
        if profile == 'dev':
            selected_extras.add('test')
            add('Source development checkout', False,
                'Development checks require the source checkout; installed runtime checks are separate')
        requirements = []
        for declaration in declarations:
            requirement, separator, marker = declaration.partition(';')
            if separator:
                extra = re.fullmatch(r'''\s*extra\s*==\s*['"]([A-Za-z0-9_-]+)['"]\s*''', marker)
                if extra is None:
                    add('Package dependency metadata', False, 'Unsupported installed dependency marker')
                    continue
                if extra.group(1) not in selected_extras:
                    continue
            requirements.append(requirement.strip())
    for requirement in requirements:
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)', requirement)
        if not match:
            add(requirement, False, 'Unsupported declaration; inspect pyproject.toml')
            continue
        name, required = match.groups()
        try:
            installed = package_version(name)
        except PackageNotFoundError:
            installed = None
        add(name, installed == required, f'installed={installed or "missing"}; declared={required}')
    def probe(name):
        executable = which(name)
        if not executable:
            add(name, False, 'Not found on PATH')
            return None
        try:
            result = execute([executable, '--version'], stdin=subprocess.DEVNULL,
                             capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            add(name, False, 'Version check unavailable or timed out')
            return None
        value = result.stdout.decode('utf-8', errors='replace').strip()
        if result.returncode or len(value) > 200 or not re.fullmatch(r'[A-Za-z0-9 ._+()/-]+', value):
            add(name, False, 'Unexpected version response; raw output omitted')
            return None
        return value
    git = probe('git')
    if git is not None:
        add('git', bool(re.fullmatch(r'git version \d+\.\d+[^\r\n]*', git)), git)
    runtime = None
    runtime_problem = None
    if profile in {'web', 'dev'}:
        try:
            runtime = locate_runtime(package_root=package_root,
                                     repo_root=root if kind == 'source' else None)
        except PiDistributionError as problem:
            runtime_problem = problem
        fallback = '>=' + '.'.join(map(str, MINIMUM_NODE))
        declared = (fallback if runtime is None
                    else runtime.package.get('engines', {}).get('node', fallback))
        match = re.fullmatch(r'>=(\d+)\.(\d+)\.(\d+)', declared)
        if not match:
            raise ValueError('Unsupported Node engine declaration')
        minimum = tuple(map(int, match.groups()))
        node = probe('node')
        if node is not None:
            actual = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', node)
            add('node', bool(actual and tuple(map(int, actual.groups())) >= minimum),
                node + '; declared=' + declared)
        if runtime is not None:
            add('Pi runtime', True,
                f'{runtime.kind} layout; version={runtime.version}')
            for name, expected in runtime.package['dependencies'].items():
                path = runtime.node_modules/name/'package.json'
                try:
                    installed = json.loads(path.read_text(encoding='utf-8'))['version']
                except (OSError, ValueError, KeyError, TypeError):
                    installed = None
                add('Pi dependency ' + name, installed == expected,
                    f'installed={installed or "missing"}; declared={expected}')
            lock = runtime.root/'package-lock.json'
            if kind == 'installed' and not runtime.distribution_complete:
                # Keep the legacy readiness layer blocked as well: it knows the
                # Pi lock row but not the newer offline-distribution row.
                add('Pi npm lock', False,
                    f'{runtime.kind} Pi lock is present, but package resources/notices are '
                    'incomplete; reinstall a complete trusted package')
            else:
                add('Pi npm lock', lock.is_file(), f'{runtime.kind} Pi lock: package-lock.json')
            if kind == 'installed':
                missing = (f'{len(runtime.vendor_missing)} locked tarball(s) missing; '
                           if runtime.vendor_missing else '')
                add('Pi package resources', runtime.distribution_complete,
                    'Bundled Pi source, lock and third-party notices verified; online dependency install is separate'
                    if runtime.distribution_complete else
                    'Pi distribution is incomplete: ' + missing
                    + 'rebuild with Pi adapter resources and the upstream license')
            check_workbench_assets(root, add,
                                   package_root=package_root if kind == 'installed' else None)
        else:
            tampered = runtime_problem is not None and runtime_problem.reason != 'missing'
            for name in REQUIRED_DEPENDENCIES:
                add('Pi dependency ' + name, False,
                    'Pi runtime failed integrity verification; reinstall a trusted wheel'
                    if tampered else
                    'External Pi Node runtime is not distributed in the Python wheel; use the supported source installation')
            add('Pi npm lock', False,
                'Pi runtime files failed integrity verification'
                if tampered else
                'External Pi lock and runtime are not distributed in the Python wheel')
            check_workbench_assets(root, add,
                                   package_root=package_root if kind == 'installed' else None)
    return {'profile': profile, 'installation_kind': kind, 'ready': all(r['status'] == 'ok' for r in checks),
            'pi_runtime': (runtime.view() if runtime is not None else
                           {'present': False, 'kind': kind,
                            'reason': (runtime_problem.reason if runtime_problem is not None
                                       else ('not_checked' if profile == 'core' else 'missing'))}),
            'checks': checks, 'model_calls': 0, 'provider_files_read': 0,
            'scope': 'Declared direct dependencies and tool versions only; not a workflow, authentication, transitive lock, benchmark or model test. '
                     'Built frontend assets are verified by existence only; nothing is installed, built, started or executed.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['core', 'web', 'dev'], default='web')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    report = inspect_environment(profile=args.profile)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for row in report['checks']:
            print(f"[{row['status']}] {row['name']}: {row['detail']}")
        print('READY' if report['ready'] else 'SETUP INCOMPLETE: see SETUP.md')
        print(report['scope'])
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
