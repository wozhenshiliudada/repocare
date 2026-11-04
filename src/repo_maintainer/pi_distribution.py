"""Locate, verify, stage and explicitly install the pinned Pi Node runtime.

Standard library only: the installed-package checker imports this before any
optional Python dependency exists. Nothing here reads provider files, calls a
model, starts a server or installs packages implicitly.

Two layouts are supported and never inferred from the working directory:

* source checkout  -> ``<repo>/runtime/pi`` (the original development layout)
* Python wheel     -> ``repo_maintainer/pi_runtime`` (verified by MANIFEST.json)

The wheel manifest contains a SHA-256 for every runtime file and for the two
limit JSON files, so a missing or tampered resource is rejected before Node is
started. Vendor tarballs and third-party notices are recorded separately; an
incomplete wheel is reported as incomplete instead of pretending to be a full
offline distribution.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .models import SafetyError
from .path_safety import is_junction

PACKAGE_RUNTIME = 'pi_runtime'
MANIFEST_NAME = 'MANIFEST.json'
PACKAGE_NAME = 'package.json'
LOCK_NAME = 'package-lock.json'
PI_PACKAGE = '@earendil-works/pi-coding-agent'
REQUIRED_DEPENDENCIES = {PI_PACKAGE: '1.0.4', 'typebox': '1.3.27'}
REQUIRED_RUNTIME_FILES = (
    PACKAGE_NAME, LOCK_NAME,
    'runtime-limits.mjs', 'maintenance-limits.mjs',
    'plan.mjs', 'model-transport.mjs', 'connectivity.mjs', 'validation-plan.mjs',
    'schema-rejection.mjs', 'reflect.mjs', 'compact.mjs', 'task-session.mjs',
)
LIMIT_FILES = ('pi_runtime_limits.json', 'maintenance_limits.json')
MINIMUM_NODE = (22, 19, 0)
SCRIPT_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*\.mjs')
SAFE_PART = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
SENSITIVE = re.compile(
    r'(?:^|/)(?:\.env[^/]*|credentials[^/]*|secrets[^/]*|[^/]*\.(?:pem|key))$',
    re.IGNORECASE,
)
FORBIDDEN_RUNTIME = {'node_modules', 'test', 'tests', '__pycache__'}
EXCLUDED_SCRIPT = re.compile(
    r'(?:^|[-.])(?:browser|replay|smoke|test)(?:[-.]|$)', re.IGNORECASE,
)
INSTALL_TIMEOUT_SECONDS = 1800
VENDOR_TIMEOUT_SECONDS = 900
VENDOR_BATCH = 20
_WINDOWS_RESERVED = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(10)),
    *(f'LPT{i}' for i in range(10)),
}


class PiDistributionError(SafetyError):
    """A Pi runtime precondition failed; no Node process may start."""

    def __init__(self, message: str, *, reason: str = 'invalid'):
        super().__init__(message)
        if reason not in {'missing', 'tampered', 'invalid'}:
            reason = 'invalid'
        self.reason = reason


@dataclass(frozen=True)
class PiRuntime:
    root: Path
    kind: str  # 'source' | 'wheel'
    version: str
    package: dict
    lock_sha256: str
    node_modules: Path
    distribution_complete: bool
    vendor_missing: tuple[str, ...] = ()
    manifest_sha256: str | None = None
    vendor_cache_complete: bool = False

    def script(self, name: str) -> Path:
        if not isinstance(name, str) or not SCRIPT_NAME.fullmatch(name):
            raise PiDistributionError('Pi 运行脚本名无效', reason='invalid')
        return _require_regular(self.root, name)

    def view(self) -> dict:
        installed = installed_dependencies(self)
        return {
            'present': True,
            'kind': self.kind,
            'version': self.version,
            'distribution_complete': self.distribution_complete,
            'vendor_cache_complete': self.vendor_cache_complete,
            'vendor_missing': len(self.vendor_missing),
            'node_modules_installed': installed == REQUIRED_DEPENDENCIES,
            'dependencies': installed,
        }


@dataclass(frozen=True)
class InstallPlan:
    runtime: PiRuntime
    command: tuple[str, ...]
    approval: str
    node: str
    node_version: str
    npm: str
    action: str  # 'install' | 'none'
    package_sha256: str
    lock_sha256: str

    def view(self) -> dict:
        return {
            'schema_version': 1,
            'action': self.action,
            'runtime': self.runtime.view(),
            'runtime_root': str(self.runtime.root),
            'node': self.node_version,
            'npm': self.npm,
            'command': list(self.command),
            'package_sha256': self.package_sha256,
            'lock_sha256': self.lock_sha256,
            'confirmation_sha256': self.approval,
            'requires_network': self.action == 'install',
            'auto_install': False,
            'model_calls': 0,
        }


@dataclass(frozen=True)
class VendorSpec:
    name: str
    version: str
    filename: str
    integrity: str


@dataclass(frozen=True)
class VendorPlan:
    runtime: PiRuntime
    vendor_dir: Path
    specs: tuple[VendorSpec, ...]
    missing: tuple[str, ...]
    approval: str
    npm: str


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_json(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeError, ValueError) as error:
        raise PiDistributionError(f'{label} 不是有效 JSON', reason='tampered') from error
    if not isinstance(value, dict):
        raise PiDistributionError(f'{label} 必须是 JSON 对象', reason='tampered')
    return value


def _safe_relative(value: str, *, label: str) -> PurePosixPath:
    if (not isinstance(value, str) or not value or len(value) > 4096
            or '\\' in value or '\x00' in value or re.match(r'^[A-Za-z]:', value)):
        raise PiDistributionError(f'{label} 路径无效', reason='tampered')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {'', '.', '..'} for part in path.parts):
        raise PiDistributionError(f'{label} 路径越界', reason='tampered')
    for part in path.parts:
        if (not SAFE_PART.fullmatch(part) or part.endswith(('.', ' '))
                or part.split('.')[0].upper() in _WINDOWS_RESERVED):
            raise PiDistributionError(f'{label} 路径含不安全名称', reason='tampered')
    return path


def _check_components(root: Path, path: Path) -> None:
    """Reject a symlink/junction at the root or anywhere below it."""
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise PiDistributionError('Pi 资源路径越界', reason='tampered') from error
    if root.is_symlink() or is_junction(root):
        raise PiDistributionError('Pi 运行目录是链接，拒绝使用', reason='tampered')
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or is_junction(current):
            raise PiDistributionError('Pi 资源路径含链接，拒绝使用', reason='tampered')


def _require_regular(root: Path, relative: str) -> Path:
    safe = _safe_relative(relative, label='Pi 资源')
    path = root.joinpath(*safe.parts)
    _check_components(root, path)
    if not path.is_file():
        raise PiDistributionError(f'缺少 Pi 资源：{safe.as_posix()}', reason='tampered')
    return path


def _read_regular(root: Path, relative: str) -> bytes:
    return _require_regular(root, relative).read_bytes()


def _manifest_keys(value: dict) -> None:
    required = {'schema_version', 'pi_version', 'source_tree', 'lock_sha256',
                'files', 'limits', 'vendor', 'licenses'}
    if set(value) != required:
        raise PiDistributionError('Pi MANIFEST.json 字段不完整', reason='tampered')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise PiDistributionError('Pi MANIFEST.json 版本不受支持', reason='tampered')
    if not isinstance(value['files'], dict) or not isinstance(value['limits'], dict):
        raise PiDistributionError('Pi MANIFEST.json 资源表无效', reason='tampered')
    vendor = value['vendor']
    licenses = value['licenses']
    if (not isinstance(vendor, dict) or set(vendor) != {'complete', 'expected', 'files', 'missing'}
            or not isinstance(licenses, dict) or set(licenses) != {'complete', 'files'}):
        raise PiDistributionError('Pi MANIFEST.json 分发字段无效', reason='tampered')


def _validate_dependencies(package: dict, lock: dict, *, label: str) -> str:
    dependencies = package.get('dependencies')
    if dependencies != REQUIRED_DEPENDENCIES:
        raise PiDistributionError(
            f'{label} Pi 依赖声明与固定锁不一致', reason='invalid')
    packages = lock.get('packages')
    root = packages.get('') if isinstance(packages, dict) else None
    if (not isinstance(root, dict) or root.get('dependencies') != REQUIRED_DEPENDENCIES
            or lock.get('lockfileVersion') != 3):
        raise PiDistributionError(f'{label} Pi 锁文件格式或依赖不一致', reason='invalid')
    return dependencies[PI_PACKAGE]


def _source_runtime(root: Path) -> PiRuntime:
    if not root.is_dir():
        raise PiDistributionError('未找到源码 Pi 运行目录 runtime/pi', reason='missing')
    _check_components(root, root / PACKAGE_NAME)
    package = _parse_json(_read_regular(root, PACKAGE_NAME), 'Pi package.json')
    lock_bytes = _read_regular(root, LOCK_NAME)
    lock = _parse_json(lock_bytes, 'Pi package-lock.json')
    version = _validate_dependencies(package, lock, label='源码')
    return PiRuntime(
        root=root,
        kind='source',
        version=version,
        package=package,
        lock_sha256=_sha256_bytes(lock_bytes),
        node_modules=root / 'node_modules',
        distribution_complete=True,
    )


def _wheel_runtime(root: Path) -> PiRuntime:
    if not root.is_dir():
        raise PiDistributionError('当前安装包没有内置 Pi 运行目录', reason='missing')
    _check_components(root, root / MANIFEST_NAME)
    manifest_bytes = _read_regular(root, MANIFEST_NAME)
    manifest = _parse_json(manifest_bytes, 'Pi MANIFEST.json')
    _manifest_keys(manifest)
    files = manifest['files']
    if not files:
        raise PiDistributionError('Pi MANIFEST.json 没有运行文件', reason='tampered')
    for relative, expected in files.items():
        safe = _safe_relative(relative, label='Pi 运行文件')
        if any(part.casefold() in FORBIDDEN_RUNTIME for part in safe.parts):
            raise PiDistributionError('Pi 发行包不得包含测试或 node_modules', reason='tampered')
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise PiDistributionError('Pi 运行文件哈希无效', reason='tampered')
        path = _require_regular(root, safe.as_posix())
        if _sha256_file(path) != expected:
            raise PiDistributionError(
                f'Pi 运行文件完整性校验失败：{safe.as_posix()}', reason='tampered')
    for required in REQUIRED_RUNTIME_FILES:
        if required not in files:
            raise PiDistributionError(f'Pi 发行包缺少必需文件：{required}', reason='tampered')
    package = _parse_json(_read_regular(root, PACKAGE_NAME), 'Pi package.json')
    lock_bytes = _read_regular(root, LOCK_NAME)
    lock = _parse_json(lock_bytes, 'Pi package-lock.json')
    version = _validate_dependencies(package, lock, label='安装包')
    if manifest['pi_version'] != version or manifest['lock_sha256'] != _sha256_bytes(lock_bytes):
        raise PiDistributionError('Pi MANIFEST.json 与包内版本或锁不一致', reason='tampered')

    limits = manifest['limits']
    if set(limits) != set(LIMIT_FILES):
        raise PiDistributionError('Pi MANIFEST.json 限制文件表无效', reason='tampered')
    package_root = root.parent
    for name, expected in limits.items():
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise PiDistributionError('Pi 限制文件哈希无效', reason='tampered')
        path = package_root / name
        if path.is_symlink() or is_junction(path):
            raise PiDistributionError('Pi 限制文件是链接，拒绝使用', reason='tampered')
        if not path.is_file():
            raise PiDistributionError(f'缺少 Pi 限制文件：{name}', reason='tampered')
        if _sha256_file(path) != expected:
            raise PiDistributionError(f'Pi 限制文件完整性校验失败：{name}', reason='tampered')

    vendor = manifest['vendor']
    vendor_files = vendor['files']
    vendor_missing = vendor['missing']
    if (type(vendor['complete']) is not bool or type(vendor['expected']) is not int
            or vendor['expected'] < 0 or not isinstance(vendor_files, dict)
            or not isinstance(vendor_missing, list)
            or not all(isinstance(name, str) for name in vendor_missing)):
        raise PiDistributionError('Pi vendor 清单无效', reason='tampered')
    if len(vendor_files) + len(vendor_missing) != vendor['expected']:
        raise PiDistributionError('Pi vendor 清单计数不一致', reason='tampered')
    if vendor['complete'] and vendor_missing:
        raise PiDistributionError('Pi vendor 清单自相矛盾', reason='tampered')
    absent = []
    for name, expected in vendor_files.items():
        if not isinstance(name, str) or not name.endswith('.tgz') or '/' in name or '\\' in name:
            raise PiDistributionError('Pi vendor 文件名无效', reason='tampered')
        path = root / 'vendor' / name
        if path.is_symlink() or is_junction(path):
            raise PiDistributionError('Pi vendor 文件是链接，拒绝使用', reason='tampered')
        if not path.is_file():
            absent.append(name)
            continue
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise PiDistributionError('Pi vendor 哈希无效', reason='tampered')
        if _sha256_file(path) != expected:
            raise PiDistributionError(f'Pi vendor 完整性校验失败：{name}', reason='tampered')

    licenses = manifest['licenses']
    license_files = licenses['files']
    if (type(licenses['complete']) is not bool or not isinstance(license_files, dict)
            or not all(isinstance(name, str) for name in license_files)):
        raise PiDistributionError('Pi 许可证清单无效', reason='tampered')
    for name, expected in license_files.items():
        path = _require_regular(root, 'LICENSES/' + name)
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise PiDistributionError('Pi 许可证哈希无效', reason='tampered')
        if _sha256_file(path) != expected:
            raise PiDistributionError(f'Pi 许可证完整性校验失败：{name}', reason='tampered')

    # A locked online install needs the adapter, lock and notices, not a second
    # copy of every registry package. Cache completeness is a separate fact and
    # never promises that npm ci can operate offline.
    complete = bool(licenses['complete'])
    missing = tuple(vendor_missing + absent)
    return PiRuntime(
        root=root,
        kind='wheel',
        version=version,
        package=package,
        lock_sha256=_sha256_bytes(lock_bytes),
        node_modules=root / 'node_modules',
        distribution_complete=complete,
        vendor_cache_complete=bool(vendor['complete'] and not absent),
        vendor_missing=missing,
        manifest_sha256=_sha256_bytes(manifest_bytes),
    )


def _default_package_root() -> Path:
    return Path(__file__).resolve().parent


def locate_runtime(*, package_root: Path | None = None, repo_root: Path | None = None) -> PiRuntime:
    """Return the one trusted Pi runtime for this installation.

    Source checkouts keep using ``runtime/pi``. Installed wheels use their own
    ``pi_runtime`` and verify the manifest before returning. The working
    directory is never searched.
    """
    package_root = Path(package_root) if package_root is not None else _default_package_root()
    if repo_root is not None:
        source = Path(repo_root)
    elif package_root.parent.name == 'src':
        source = package_root.parent.parent
    else:
        source = None
    if source is not None:
        candidate = source / 'runtime' / 'pi'
        if candidate.is_dir():
            return _source_runtime(candidate)
    wheel = package_root / PACKAGE_RUNTIME
    if wheel.is_dir():
        return _wheel_runtime(wheel)
    raise PiDistributionError(
        '未找到 Pi 运行目录：源码安装需要 runtime/pi，wheel 安装需要内置 pi_runtime。'
        '请使用包含 Pi 的完整发行包，或按 SETUP.md 使用源码安装。',
        reason='missing',
    )


def runtime_script(name: str, *, package_root: Path | None = None) -> Path:
    """Resolve one production ``.mjs`` for the five existing call sites."""
    return locate_runtime(package_root=package_root).script(name)


def minimum_node(runtime: PiRuntime) -> tuple[int, int, int]:
    declared = runtime.package.get('engines', {}).get('node')
    match = re.fullmatch(r'>=(\d+)\.(\d+)\.(\d+)', declared or '')
    if not match:
        raise PiDistributionError('Pi package.json 的 Node 版本声明不受支持', reason='invalid')
    return tuple(map(int, match.groups()))


def installed_dependencies(runtime: PiRuntime) -> dict[str, str]:
    result: dict[str, str] = {}
    if runtime.node_modules.is_symlink() or is_junction(runtime.node_modules):
        return {}
    for name in REQUIRED_DEPENDENCIES:
        path = runtime.node_modules / name / 'package.json'
        try:
            if path.is_symlink() or is_junction(path) or not path.is_file():
                return {}
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError):
            return {}
        version = value.get('version') if isinstance(value, dict) else None
        if not isinstance(version, str):
            return {}
        result[name] = version
    return result


def _tool_version(execute, node: str) -> str:
    try:
        result = execute([node, '--version'], stdin=subprocess.DEVNULL,
                         capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PiDistributionError('无法读取 Node.js 版本；请确认安装完整。', reason='invalid') from error
    value = result.stdout.decode('utf-8', errors='replace').strip()
    if result.returncode or not re.fullmatch(r'v\d+\.\d+\.\d+', value):
        raise PiDistributionError('Node.js 版本输出无效；请安装 Node.js >=22.19.0。', reason='invalid')
    return value


def _runtime_hashes(runtime: PiRuntime) -> tuple[str, str]:
    try:
        package_bytes = _read_regular(runtime.root, PACKAGE_NAME)
        lock_bytes = _read_regular(runtime.root, LOCK_NAME)
    except OSError as error:
        raise PiDistributionError('Pi 依赖声明或锁文件不可读，拒绝继续', reason='tampered') from error
    return _sha256_bytes(package_bytes), _sha256_bytes(lock_bytes)


def plan_install(runtime: PiRuntime, *, which=None, execute=None) -> InstallPlan:
    """Build the read-only plan shown before the explicit confirmation."""
    which = shutil.which if which is None else which
    execute = subprocess.run if execute is None else execute
    if not runtime.root.is_dir():
        raise PiDistributionError('Pi 运行目录不存在，无法安装依赖', reason='missing')
    package_sha, lock_sha = _runtime_hashes(runtime)
    if lock_sha != runtime.lock_sha256:
        raise PiDistributionError('Pi 锁文件在定位后发生变化，拒绝安装', reason='tampered')
    package = _parse_json(_read_regular(runtime.root, PACKAGE_NAME), 'Pi package.json')
    lock = _parse_json(_read_regular(runtime.root, LOCK_NAME), 'Pi package-lock.json')
    _validate_dependencies(package, lock, label='运行时')
    installed = installed_dependencies(runtime)
    action = 'none' if installed == REQUIRED_DEPENDENCIES else 'install'
    node = which('node')
    npm = which('npm') or which('npm.cmd')
    if action == 'install':
        if not node:
            raise PiDistributionError(
                '未找到 Node.js。请先安装 Node.js >=22.19.0，然后重新运行 '
                '`repo-maintainer runtime install`。', reason='missing')
        node_version = _tool_version(execute, node)
        actual = tuple(map(int, node_version[1:].split('.')))
        if actual < minimum_node(runtime):
            raise PiDistributionError(
                f'Node.js 版本过低（{node_version}，需要 >=22.19.0）。请升级后重试。',
                reason='invalid')
        if not npm:
            raise PiDistributionError(
                '未找到 npm。请安装随 Node.js 提供的 npm，然后重新运行 '
                '`repo-maintainer runtime install`。', reason='missing')
    else:
        # Already installed: this diagnostic must not spawn Node or npm.
        node_version = ''
    command = (npm or 'npm', 'ci', '--prefix', str(runtime.root),
               '--ignore-scripts', '--no-audit', '--no-fund')
    if action=='install' and Path(npm).suffix.lower() in {'.cmd','.bat'}:
        entries=[Path(npm).parent/'node_modules/npm/bin/npm-cli.js',
                 Path(node).parent/'node_modules/npm/bin/npm-cli.js']
        entry=next((p for p in entries if p.is_file()),None)
        if entry is None:
            raise PiDistributionError('无法找到 npm-cli.js，请修复 Node.js/npm 安装。',reason='missing')
        command=(node,str(entry),*command[1:])
    payload = {
        'schema_version': 1,
        'action': action,
        'kind': runtime.kind,
        'root': str(runtime.root.absolute()),
        'package_sha256': package_sha,
        'lock_sha256': lock_sha,
        'node': node_version,
        'npm': npm or '',
        'command': list(command),
    }
    approval = _sha256_bytes(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode())
    return InstallPlan(runtime=runtime, command=command, approval=approval, node=node or '',
                       node_version=node_version, npm=npm or '', action=action,
                       package_sha256=package_sha, lock_sha256=lock_sha)


def _npm_environment() -> dict[str, str]:
    allowed = {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME', 'USERPROFILE',
        'APPDATA', 'LOCALAPPDATA', 'COMSPEC', 'PATHEXT', 'PROGRAMFILES',
        'PROGRAMFILES(X86)', 'PROGRAMDATA',
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    # Explicitly keep lifecycle scripts, audit, funding and update notices off.
    unused=Path(__file__).resolve().with_name('.repocare-no-global-npmrc')
    if unused.exists():
        raise PiDistributionError('安装配置隔离路径被占用，拒绝读取额外 npm 配置。',reason='tampered')
    env.update({
        'npm_config_ignore_scripts': 'true',
        'npm_config_audit': 'false',
        'npm_config_fund': 'false',
        'npm_config_update_notifier': 'false',
        'npm_config_userconfig': os.devnull,
        'npm_config_globalconfig': str(unused),
        'npm_config_registry': 'https://registry.npmjs.org',
    })
    return env


def _clean_output(raw: bytes) -> str:
    text = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else str(raw)
    text = re.sub(r'(?i)(_authToken|_password|token)\s*=\s*\S+', r'\1=[redacted]', text)
    text = re.sub(r'https?://[^\s/@]+:[^\s/@]+@', 'https://[redacted]@', text)
    text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)
    return text[-1600:]


def execute_install(plan: InstallPlan, approval: str, *, runner=None) -> dict:
    """Run only the pre-approved fixed ``npm ci`` command, then verify bytes."""
    runner = subprocess.run if runner is None else runner
    if not isinstance(approval, str) or not hmac.compare_digest(approval, plan.approval):
        raise PiDistributionError('确认码不一致；请重新运行准备命令并核对后再确认。', reason='invalid')
    package_sha, lock_sha = _runtime_hashes(plan.runtime)
    if (package_sha != plan.package_sha256 or lock_sha != plan.lock_sha256):
        raise PiDistributionError('Pi 锁文件或依赖声明在确认后变化，拒绝执行。', reason='tampered')
    if plan.action == 'none':
        return {'action': 'none', 'executed': False, 'runtime': plan.runtime.view(),
                'model_calls': 0}
    try:
        result = runner(list(plan.command), cwd=str(plan.runtime.root), env=_npm_environment(),
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, timeout=INSTALL_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as error:
        raise PiDistributionError(
            'Pi 依赖安装超时，未重试；请检查网络后重新运行准备命令。', reason='invalid') from error
    except OSError as error:
        raise PiDistributionError(
            '无法启动 npm；请确认 Node.js/npm 安装完整。', reason='invalid') from error
    if result.returncode:
        detail = _clean_output(getattr(result, 'stdout', b'') or b'')
        raise PiDistributionError(
            f'Pi 依赖安装失败（npm 退出码 {result.returncode}）。未自动重试或改用其他锁文件。'
            + (f'\n{detail}' if detail else ''), reason='invalid')
    after_package, after_lock = _runtime_hashes(plan.runtime)
    if (after_package != plan.package_sha256 or after_lock != plan.lock_sha256):
        raise PiDistributionError('npm ci 改动了 package.json 或锁文件，拒绝接受。', reason='tampered')
    installed = installed_dependencies(plan.runtime)
    if installed != REQUIRED_DEPENDENCIES:
        raise PiDistributionError(
            'npm ci 完成但直接依赖版本与锁不一致；未自动重试。', reason='invalid')
    return {'action': 'install', 'executed': True, 'runtime': plan.runtime.view(),
            'installed_dependencies': installed, 'model_calls': 0}


def _pack_filename(name: str, version: str) -> str:
    base = name[1:] if name.startswith('@') else name
    base = base.replace('/', '-')
    if not SAFE_PART.fullmatch(base) or not re.fullmatch(r'[A-Za-z0-9._+-]+', version):
        raise PiDistributionError('Pi 锁文件中的包名或版本无效', reason='invalid')
    return f'{base}-{version}.tgz'


def _integrity_matches(path: Path, integrity: str) -> bool:
    parsed = []
    for token in integrity.split():
        algorithm, separator, encoded = token.partition('-')
        if not separator or algorithm not in {'sha512', 'sha256', 'sha1'}:
            raise PiDistributionError('Pi 锁文件 integrity 格式无效', reason='invalid')
        parsed.append((algorithm, encoded))
    if not parsed:
        raise PiDistributionError('Pi 锁文件 integrity 格式无效', reason='invalid')
    digests = {algorithm: hashlib.new(algorithm) for algorithm, _ in parsed}
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            for digest in digests.values():
                digest.update(chunk)
    for algorithm, encoded in parsed:
        actual = base64.b64encode(digests[algorithm].digest()).decode('ascii')
        if hmac.compare_digest(actual, encoded):
            return True
    return False


def _lock_specs(lock: dict) -> list[VendorSpec]:
    packages = lock.get('packages')
    if not isinstance(packages, dict):
        raise PiDistributionError('Pi 锁文件缺少 packages', reason='invalid')
    result: dict[tuple[str, str], VendorSpec] = {}
    for key, value in packages.items():
        if not key or not isinstance(value, dict):
            continue
        if 'resolved' not in value or 'integrity' not in value:
            continue
        name = key.rsplit('node_modules/', 1)[-1]
        version = value.get('version')
        integrity = value.get('integrity')
        if not isinstance(name, str) or not name or 'node_modules' in name:
            continue
        if not isinstance(version, str) or not isinstance(integrity, str):
            raise PiDistributionError('Pi 锁文件条目缺少版本或 integrity', reason='invalid')
        filename = _pack_filename(name, version)
        spec = VendorSpec(name=name, version=version, filename=filename, integrity=integrity)
        previous = result.get((name, version))
        if previous is not None and previous.integrity != integrity:
            raise PiDistributionError('Pi 锁文件同一版本出现不同 integrity', reason='invalid')
        for other in result.values():
            if other.filename == filename and (other.name, other.version) != (name, version):
                raise PiDistributionError('Pi 锁文件出现同名 vendor tarball', reason='invalid')
        result[(name, version)] = spec
    if not result:
        raise PiDistributionError('Pi 锁文件没有可 vendoring 的包', reason='invalid')
    return sorted(result.values(), key=lambda item: (item.name, item.version))


def vendor_plan(runtime: PiRuntime, *, which=None) -> VendorPlan:
    """Read-only plan for the explicit vendoring helper (never auto-run)."""
    which = shutil.which if which is None else which
    vendor_dir = runtime.root / 'vendor'
    if vendor_dir.is_symlink() or is_junction(vendor_dir):
        raise PiDistributionError('Pi vendor 目录是链接，拒绝使用', reason='tampered')
    try:
        lock_bytes = (runtime.root / LOCK_NAME).read_bytes()
    except OSError as error:
        raise PiDistributionError('Pi 锁文件不可读，无法生成 vendor 计划', reason='tampered') from error
    specs = _lock_specs(_parse_json(lock_bytes, 'Pi package-lock.json'))
    missing: list[str] = []
    for spec in specs:
        path = vendor_dir / spec.filename
        if path.is_symlink() or is_junction(path):
            raise PiDistributionError('Pi vendor 文件是链接，拒绝使用', reason='tampered')
        if not path.is_file():
            missing.append(spec.filename)
            continue
        if not _integrity_matches(path, spec.integrity):
            raise PiDistributionError(f'Pi vendor 完整性校验失败：{spec.filename}', reason='tampered')
    npm = which('npm') or which('npm.cmd') or ''
    payload = {'schema_version': 1, 'runtime': str(runtime.root.absolute()),
               'specs': [[spec.name, spec.version, spec.filename] for spec in specs],
               'missing': missing, 'npm': npm}
    approval = _sha256_bytes(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode())
    return VendorPlan(runtime=runtime, vendor_dir=vendor_dir, specs=tuple(specs),
                      missing=tuple(missing), approval=approval, npm=npm)


def vendor_runtime(plan: VendorPlan, approval: str, *, runner=None) -> dict:
    """Explicitly download the exact locked tarballs with fixed ``npm pack`` argv."""
    runner = subprocess.run if runner is None else runner
    if not isinstance(approval, str) or not hmac.compare_digest(approval, plan.approval):
        raise PiDistributionError('vendor 确认码不一致；请重新运行准备命令。', reason='invalid')
    if not plan.missing:
        return {'vendor': 'complete', 'downloaded': 0, 'model_calls': 0}
    if not plan.npm:
        raise PiDistributionError('未找到 npm；无法生成 vendor tarball。', reason='missing')
    if plan.vendor_dir.is_symlink() or is_junction(plan.vendor_dir):
        raise PiDistributionError('vendor 目录是链接，拒绝写入', reason='tampered')
    plan.vendor_dir.mkdir(parents=True, exist_ok=True)
    by_name = {spec.filename: spec for spec in plan.specs}
    for start in range(0, len(plan.missing), VENDOR_BATCH):
        names = plan.missing[start:start + VENDOR_BATCH]
        specs = [f'{by_name[name].name}@{by_name[name].version}' for name in names]
        command = [plan.npm, 'pack', *specs, '--pack-destination', str(plan.vendor_dir),
                   '--ignore-scripts', '--no-audit', '--no-fund']
        try:
            result = runner(command, cwd=str(plan.runtime.root), env=_npm_environment(),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=VENDOR_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PiDistributionError('npm pack 超时或无法启动；未重试。', reason='invalid') from error
        if result.returncode:
            raise PiDistributionError(
                f'npm pack 失败（退出码 {result.returncode}）；未重试。\n'
                + _clean_output(getattr(result, 'stdout', b'') or b''), reason='invalid')
    rechecked = vendor_plan(plan.runtime, which=lambda name: plan.npm if name in {'npm', 'npm.cmd'} else None)
    if rechecked.missing:
        raise PiDistributionError(
            f'仍有 {len(rechecked.missing)} 个 vendor tarball 缺失；未自动重试。', reason='invalid')
    return {'vendor': 'complete', 'downloaded': len(plan.missing),
            'tarballs': len(plan.specs), 'model_calls': 0}


def _copy_bytes(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def _reset_destination(source: Path, destination: Path) -> None:
    if destination.name != PACKAGE_RUNTIME:
        raise PiDistributionError('Pi 构建输出目录名必须是 pi_runtime', reason='invalid')
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise PiDistributionError('Pi 构建输出不能位于源码目录内', reason='invalid')
    if destination.exists():
        if destination.is_symlink() or is_junction(destination):
            raise PiDistributionError('Pi 构建输出目录是链接，拒绝清理', reason='tampered')
        if not destination.is_dir():
            raise PiDistributionError('Pi 构建输出路径不是目录，拒绝覆盖', reason='tampered')
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)


def _runtime_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.iterdir()):
        if path.name in FORBIDDEN_RUNTIME:
            continue
        if path.is_symlink() or is_junction(path):
            raise PiDistributionError(f'Pi 源码含链接：{path.name}', reason='tampered')
        if path.name in FORBIDDEN_RUNTIME:
            continue
        if path.is_dir():
            continue
        if path.name in {PACKAGE_NAME, LOCK_NAME, 'README.md'}:
            files.append(path)
        elif path.suffix == '.mjs' and not EXCLUDED_SCRIPT.search(path.stem):
            files.append(path)
    names = {path.name for path in files}
    for required in REQUIRED_RUNTIME_FILES:
        if required not in names:
            raise PiDistributionError(f'Pi 源码缺少必需运行文件：{required}', reason='missing')
    return files


def _license_files(root: Path) -> list[Path]:
    if root.is_symlink() or is_junction(root):
        raise PiDistributionError('第三方许可证目录是链接，拒绝打包', reason='tampered')
    if not root.is_dir():
        return []
    result: list[Path] = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or is_junction(path):
            raise PiDistributionError('第三方许可证路径含链接，拒绝打包', reason='tampered')
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if SENSITIVE.search(relative):
            continue
        result.append(path)
    return result


def _dependency_notices(lock: dict) -> list[dict]:
    notices = []
    for key, value in (lock.get('packages') or {}).items():
        if not key or not isinstance(value, dict):
            continue
        name = key.rsplit('node_modules/', 1)[-1]
        notices.append({
            'name': name,
            'version': value.get('version'),
            'license': value.get('license', 'UNKNOWN'),
            'integrity': value.get('integrity'),
            'resolved': value.get('resolved'),
        })
    return sorted(notices, key=lambda item: (str(item['name']), str(item['version'])))


def stage_pi_runtime(repo_root: Path, destination: Path, *,
                     third_party: Path | None = None) -> list[Path]:
    """Copy the tracked Pi runtime into a wheel build directory and write MANIFEST.

    This is called by ``setup.py`` only. It never reads the working directory,
    never copies ``node_modules``/``test``/secrets, and never downloads anything.
    """
    repo_root = Path(repo_root)
    source = repo_root / 'runtime' / 'pi'
    destination = Path(destination)
    if not source.is_dir():
        raise PiDistributionError('缺少 runtime/pi，无法构建 Pi 发行包', reason='missing')
    if source.is_symlink() or is_junction(source):
        raise PiDistributionError('Pi 源码目录是链接，拒绝打包', reason='tampered')
    _reset_destination(source, destination)
    written: list[Path] = []
    copied: dict[str, str] = {}
    for path in _runtime_source_files(source):
        data = path.read_bytes()
        target = destination / path.name
        _copy_bytes(target, data)
        written.append(target)
        copied[path.name] = _sha256_bytes(data)

    package = _parse_json((source / PACKAGE_NAME).read_bytes(), 'Pi package.json')
    lock_bytes = (source / LOCK_NAME).read_bytes()
    lock = _parse_json(lock_bytes, 'Pi package-lock.json')
    version = _validate_dependencies(package, lock, label='源码')

    specs = _lock_specs(lock)
    vendor_dir = source / 'vendor'
    actual: dict[str, Path] = {}
    if vendor_dir.is_dir():
        if vendor_dir.is_symlink() or is_junction(vendor_dir):
            raise PiDistributionError('Pi vendor 目录是链接，拒绝打包', reason='tampered')
        for path in sorted(vendor_dir.glob('*.tgz')):
            if path.is_symlink() or is_junction(path):
                raise PiDistributionError('Pi vendor 文件是链接，拒绝打包', reason='tampered')
            if not path.is_file():
                continue
            actual[path.name] = path
    expected_names = {spec.filename for spec in specs}
    unexpected = sorted(set(actual) - expected_names)
    if unexpected:
        raise PiDistributionError(
            f'Pi vendor 含锁文件之外的 tarball：{unexpected[0]}', reason='tampered')
    vendor_files: dict[str, str] = {}
    missing: list[str] = []
    for spec in specs:
        path = actual.get(spec.filename)
        if path is None:
            missing.append(spec.filename)
            continue
        if not _integrity_matches(path, spec.integrity):
            raise PiDistributionError(
                f'Pi vendor 完整性校验失败：{spec.filename}', reason='tampered')
        data = path.read_bytes()
        target = destination / 'vendor' / spec.filename
        _copy_bytes(target, data)
        written.append(target)
        vendor_files[spec.filename] = _sha256_bytes(data)
    if missing:
        print(f'notice: {len(missing)} registry tarballs are not cached; explicit online npm ci is required',
              file=sys.stderr)

    license_root = Path(third_party) if third_party is not None else repo_root / 'third_party' / 'pi'
    license_files: dict[str, str] = {}
    license_complete = False
    copies = _license_files(license_root)
    for path in copies:
        relative = path.relative_to(license_root).as_posix()
        data = path.read_bytes()
        target = destination / 'LICENSES' / 'third_party' / 'pi' / relative
        _copy_bytes(target, data)
        written.append(target)
        license_files['third_party/pi/' + relative] = _sha256_bytes(data)
        if path.name.lower().startswith(('license', 'licence')) or path.name.lower().endswith(('-license','-licence')):
            license_complete = True
    notices = _dependency_notices(lock)
    notices_bytes = json.dumps(notices, ensure_ascii=False, indent=2, sort_keys=True).encode() + b'\n'
    target = destination / 'LICENSES' / 'DEPENDENCY-LICENSES.json'
    _copy_bytes(target, notices_bytes)
    written.append(target)
    license_files['DEPENDENCY-LICENSES.json'] = _sha256_bytes(notices_bytes)
    if not license_complete:
        print('warning: third_party/pi upstream license text is missing; wheel will be marked incomplete',
              file=sys.stderr)

    limits = {}
    package_dir = repo_root / 'src' / 'repo_maintainer'
    for name in LIMIT_FILES:
        path = package_dir / name
        if not path.is_file():
            raise PiDistributionError(f'缺少共享限制文件：{name}', reason='missing')
        limits[name] = _sha256_bytes(path.read_bytes())

    manifest = {
        'schema_version': 1,
        'pi_version': version,
        'source_tree': 'runtime/pi',
        'lock_sha256': _sha256_bytes(lock_bytes),
        'files': dict(sorted(copied.items())),
        'limits': limits,
        'vendor': {'complete': not missing, 'expected': len(specs),
                   'files': dict(sorted(vendor_files.items())),
                   'missing': sorted(missing)},
        'licenses': {'complete': license_complete, 'files': dict(sorted(license_files.items()))},
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode() + b'\n'
    target = destination / MANIFEST_NAME
    _copy_bytes(target, manifest_bytes)
    written.append(target)
    return written


def runtime_command(args) -> int:
    """Implementation of the single new CLI subcommand ``runtime install``."""
    as_json = bool(getattr(args, 'json', False))
    try:
        runtime = locate_runtime()
        plan = plan_install(runtime)
    except PiDistributionError as problem:
        if as_json:
            print(json.dumps({'ok': False, 'error': str(problem), 'model_calls': 0},
                             ensure_ascii=False, indent=2))
        else:
            print(f'无法准备 Pi 运行时：{problem}')
        return 1
    if getattr(args, 'approve', None) is None:
        if as_json:
            print(json.dumps({'ok': True, **plan.view()}, ensure_ascii=False, indent=2))
        else:
            print(_plan_text(plan))
        return 0 if plan.action == 'none' else 1
    try:
        result = execute_install(plan, args.approve)
    except PiDistributionError as problem:
        if as_json:
            print(json.dumps({'ok': False, 'error': str(problem), 'model_calls': 0},
                             ensure_ascii=False, indent=2))
        else:
            print(f'Pi 依赖安装未完成：{problem}')
        return 1
    if as_json:
        print(json.dumps({'ok': True, **result}, ensure_ascii=False, indent=2))
    else:
        if result['action'] == 'none':
            print('Pi 锁定依赖已安装且版本一致；未执行 npm。')
        else:
            deps = result['installed_dependencies']
            print('Pi 锁定依赖安装完成：'
                  + '，'.join(f'{name}=={version}' for name, version in sorted(deps.items())))
            print('本次只按本项目锁文件执行 npm ci；没有调用模型，也没有读取模型配置。')
    return 0


def _plan_text(plan: InstallPlan) -> str:
    lines = [
        'Pi 运行时准备（默认只诊断，不自动安装）',
        f'  类型：{"源码目录" if plan.runtime.kind == "source" else "安装包内置"}',
        f'  位置：{plan.runtime.root}',
        f'  Pi 版本：{plan.runtime.version}',
        f'  Node.js：{plan.node_version or "未找到或无需安装"}',
        f'  npm：{plan.npm or "未找到"}',
        f'  锁文件 SHA256：{plan.lock_sha256}',
        f'  已安装依赖：{plan.runtime.view()["dependencies"] or "无"}',
        f'  固定安装命令：{" ".join(plan.command)}',
        '  该命令只执行锁定 npm ci，写入 node_modules，可能访问 npm registry；',
        '  不会调用模型、不会读取 provider 配置，也不会接受任意 npm 参数。',
        f'  确认码：{plan.approval}',
    ]
    if plan.action == 'none':
        lines.append('结论：锁定依赖已齐备，无需执行安装。')
    else:
        lines.append('未执行安装。要执行，请重新运行并加 --approve ' + plan.approval)
    if not plan.runtime.distribution_complete and plan.runtime.kind == 'wheel':
        lines.append('注意：当前 wheel 的 vendor tarball 或第三方许可证不完整；')
        lines.append('      npm ci 仍可使用网络，但该安装包不能声称为完整离线发行版。')
    return '\n'.join(lines)
