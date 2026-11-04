"""Private foreground lifecycle adapter for the existing repository workbench.

Discovery records are capabilities, never ownership authorities. Authenticated
service identity, the original process creation time, and the original lock are
checked separately. There is deliberately no kill, service manager or replay.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import stat
import tempfile
import time
import webbrowser

from .models import SafetyError
from .path_safety import is_junction
from .runtime_limits import MAX_REQUESTS

CONFIG_FIELDS = {'schema_version', 'workspace_root', 'provider_config',
                 'request_budget', 'allow_auto', 'port', 'ui'}
RECEIPT_FIELDS = {'schema_version', 'instance_id', 'host', 'port', 'pid',
                  'process_created', 'token', 'workspace_root', 'provider_config',
                  'request_budget', 'allow_auto', 'build_sha256', 'started_at', 'phase'}
BUSY_FIELDS = ('active', 'workspace_active', 'history_active', 'monitor_enabled',
               'acceptance_watch_active', 'github_push_active')
HEX64 = re.compile(r'[a-f0-9]{64}')
HEX32 = re.compile(r'[a-f0-9]{32}')
MAX_JSON = 65536


class GatewayError(SafetyError):
    """Fixed, user-facing lifecycle explanations, without input interpolation."""


class GatewayStopRejected(GatewayError):
    """The original stop endpoint returned a request rejection."""


def local_path(value):
    """Reject network/device paths before probing any filesystem component."""
    if not isinstance(value, (str, Path)):
        raise SafetyError('请使用完整的本机路径')
    raw = str(value)
    path, windows = Path(raw), PureWindowsPath(raw)
    if (not raw or len(raw) > 4096 or any(ord(c) < 32 for c in raw)
            or raw.startswith(('\\\\', '//')) or not path.is_absolute()
            or windows.drive.startswith('\\') or '..' in path.parts):
        raise SafetyError('请使用完整的本机路径；不接受网络、设备或上级路径')
    if os.name == 'nt':
        reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)),
                    *(f'LPT{i}' for i in range(10))}
        if any(any(c in part for c in '<>:"|?*') or part.endswith(('.', ' '))
               or part.split('.')[0].upper() in reserved for part in windows.parts[1:]):
            raise SafetyError('本机路径包含不受支持的名称')
    for item in reversed((path, *path.parents)):
        if item.is_symlink() or is_junction(item):
            raise SafetyError('本机配置和状态路径不能经过链接或目录联接')
        if item.exists() and item != path and not item.is_dir():
            raise SafetyError('本机路径的父级必须是普通目录')
    return path.absolute()


def gateway_home(value=None):
    return local_path(value if value is not None else os.environ.get('REPOCARE_HOME', str(Path.home()/'.repocare')))


def _windows_security(path, *, protect=False, directory=False):
    """Set/verify an owner-only protected DACL, without subprocesses or shell."""
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer = ctypes.c_void_p
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), pointer]
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, pointer]
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer)]
    advapi.GetSecurityDescriptorControl.argtypes = [pointer, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    advapi.GetAclInformation.argtypes = [pointer, pointer, wintypes.DWORD, ctypes.c_int]
    advapi.GetAce.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [pointer]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise SafetyError('无法核对本机配置的私有权限')

    def sid_string(sid):
        output = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(output)):
            raise SafetyError('无法核对本机配置的私有权限')
        try:
            return output.value
        finally:
            kernel.LocalFree(ctypes.cast(output, pointer))

    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise SafetyError('无法核对本机配置的私有权限')
        sid = sid_string(ctypes.cast(buffer, ctypes.POINTER(pointer))[0])
    finally:
        kernel.CloseHandle(token)
    if protect:
        descriptor = pointer()
        inheritance = 'OICI' if directory else ''
        sddl = f'D:P(A;{inheritance};FA;;;SY)(A;{inheritance};FA;;;{sid})'
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise SafetyError('无法保护本机配置的私有权限')
        try:
            if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
                raise SafetyError('无法保护本机配置的私有权限')
        finally:
            kernel.LocalFree(descriptor)
    owner, dacl, descriptor = pointer(), pointer(), pointer()
    code = advapi.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None,
                                      ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if code:
        raise SafetyError('无法核对本机配置的私有权限')
    try:
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if (not advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision))
                or not control.value & 0x1000 or not dacl or sid_string(owner) != sid):
            raise SafetyError('本机配置必须仅当前用户拥有，并使用受保护的私有权限')

        class AclSize(ctypes.Structure):
            _fields_ = [('count', wintypes.DWORD), ('used', wintypes.DWORD), ('free', wintypes.DWORD)]

        info, allowed = AclSize(), set()
        if not advapi.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
            raise SafetyError('无法核对本机配置的私有权限')
        for index in range(info.count):
            ace = pointer()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                raise SafetyError('无法核对本机配置的私有权限')
            # Only simple allow ACEs for this owner and SYSTEM are accepted.
            if ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))[0] != 0:
                raise SafetyError('本机配置权限包含不受支持的授权')
            identity = sid_string(pointer(ace.value + 8))
            if identity not in {sid, 'S-1-5-18'}:
                raise SafetyError('本机配置不能授权其他用户读取')
            allowed.add(identity)
        if sid not in allowed:
            raise SafetyError('本机配置缺少当前用户私有权限')
    finally:
        kernel.LocalFree(descriptor)


def check_private(path, *, directory=False):
    path = local_path(path)
    metadata = path.stat()
    if not (stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)):
        raise SafetyError('本机配置必须是普通文件或目录')
    if os.name == 'nt':
        _windows_security(path, directory=directory)
    elif metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise SafetyError('本机配置必须仅当前用户可读写')
    return path


def _protect(path, *, directory=False):
    if os.name == 'nt':
        _windows_security(path, protect=True, directory=directory)
    else:
        os.chmod(path, 0o700 if directory else 0o600)
    check_private(path, directory=directory)


def _control_directory(home, *, create=False):
    home = gateway_home(home)
    target = local_path(home/'gateway')
    if create and not target.exists():
        home.mkdir(parents=True, exist_ok=True)
        target.mkdir(mode=0o700)
        _protect(target, directory=True)
    if target.exists():
        check_private(target, directory=True)
    return target


def _strict_object(pairs):
    value = {}
    for key, content in pairs:
        if key in value:
            raise SafetyError('本机配置不能包含重复字段')
        value[key] = content
    return value


def _read_json(path, *, private=False):
    path = check_private(path) if private else local_path(path)
    if not path.is_file() or path.stat().st_size > MAX_JSON:
        raise SafetyError('本机配置不是有效的有界普通文件')
    with path.open('rb') as stream:
        content = stream.read(MAX_JSON + 1)
    if len(content) > MAX_JSON:
        raise SafetyError('本机配置超过容量')
    try:
        value = json.loads(content.decode('utf-8'), object_pairs_hook=_strict_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError):
        raise SafetyError('本机配置内容无效') from None
    if not isinstance(value, dict):
        raise SafetyError('本机配置必须是对象')
    return value


def _write_private(path, value, *, replace=False):
    path = local_path(path)
    if path.exists():
        check_private(path)
        if not replace:
            raise SafetyError('已有本机配置保留，不自动覆盖')
    descriptor, temporary = tempfile.mkstemp(prefix='.gateway-', suffix='.tmp', dir=path.parent)
    temporary = Path(temporary)
    try:
        _protect(temporary)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            descriptor = None
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        local_path(path)
        if replace:
            os.replace(temporary, path)
        else:
            # A private directory and exclusive destination avoid clobbering a
            # concurrent setup. Hard-link publication is atomic on NTFS/POSIX.
            os.link(temporary, path)
        check_private(path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def validate_configuration(value):
    if (not isinstance(value, dict) or set(value) != CONFIG_FIELDS
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or type(value['request_budget']) is not int or not 1 <= value['request_budget'] <= MAX_REQUESTS
            or type(value['allow_auto']) is not bool or type(value['port']) is not int
            or not 0 <= value['port'] <= 65535 or not isinstance(value['ui'], str)
            or value['ui'] not in {'legacy', 'react'}):
        raise SafetyError('本机启动配置字段无效；保留原配置')
    result = dict(value)
    result['workspace_root'] = str(local_path(value['workspace_root']))
    if value['provider_config'] is not None:
        provider = local_path(value['provider_config'])
        if not provider.is_file():
            raise SafetyError('明确指定的模型配置必须是已有本机普通文件')
        result['provider_config'] = str(provider)
    return result


def _workbench_metadata(root):
    path = local_path(root/'workbench.json')
    if not path.exists():
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise SafetyError('已有状态缺少原工作台配置；不重建或替换原账')
        return None
    value = _read_json(path)
    if (set(value) != {'schema_version', 'provider_config', 'request_budget', 'allow_auto', 'layout'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['layout'] != 'compact-v1' or type(value['allow_auto']) is not bool
            or type(value['request_budget']) is not int or not 1 <= value['request_budget'] <= MAX_REQUESTS):
        raise SafetyError('原工作台配置无效；保留原路径、记录和额度')
    value['provider_config'] = str(local_path(value['provider_config']))
    return value


def _require_original(configuration):
    root = Path(configuration['workspace_root'])
    original = _workbench_metadata(root)
    if original is not None:
        expected = {'schema_version': 1, 'provider_config': configuration['provider_config'] or str(root/'provider.env'),
                    'request_budget': configuration['request_budget'], 'allow_auto': configuration['allow_auto'], 'layout': 'compact-v1'}
        if original != expected:
            raise SafetyError('启动设置与原工作台不一致；保留原路径、模型引用及额度')


def load_configuration(home=None, *, check_state=True):
    try:
        directory = _control_directory(home)
        configuration = validate_configuration(_read_json(directory/'config.json', private=True))
    except FileNotFoundError:
        raise GatewayError('未找到启动设置，请核对原 --home，或先为新工作台运行 repocare setup。') from None
    except (SafetyError, OSError, ValueError, TypeError):
        raise GatewayError('无法读取私有启动设置。请核对原 --home、配置格式及本机权限。') from None
    if check_state:
        try:
            _require_original(configuration)
        except (SafetyError, OSError, ValueError, TypeError):
            raise GatewayError('无法核对原工作台配置。请核对原状态目录、模型配置引用与额度。') from None
    return configuration


def setup_configuration(args):
    home = gateway_home(getattr(args, 'home', None))
    directory = _control_directory(home)
    saved = load_configuration(home) if (directory/'config.json').exists() else None
    root = local_path(getattr(args, 'workspace_root', None) or (saved and saved['workspace_root']) or home/'workbench')
    original = _workbench_metadata(root)
    defaults = saved or {'schema_version': 1, 'workspace_root': str(root), 'provider_config': None,
                        'request_budget': MAX_REQUESTS, 'allow_auto': False, 'port': 0, 'ui': 'react'}
    configuration = dict(defaults, workspace_root=str(root))
    if original is not None and saved is None:
        configuration.update(request_budget=original['request_budget'], allow_auto=original['allow_auto'],
                             provider_config=None if original['provider_config'] == str(root/'provider.env') else original['provider_config'])
    for field in ('provider_config', 'request_budget', 'allow_auto', 'port', 'ui'):
        explicit = getattr(args, field, None)
        if explicit is not None:
            configuration[field] = str(explicit) if field == 'provider_config' else explicit
    configuration = validate_configuration(configuration)
    _require_original(configuration)
    if saved is not None:
        if configuration != saved:
            raise GatewayError('已保存本机设置；不同设置不能自动覆盖，请核对原配置')
    else:
        directory = _control_directory(home, create=True)
        _write_private(directory/'config.json', configuration)
    return {'status': 'configured', 'foreground': True, 'model_calls': 0}


def validate_receipt(value, configuration):
    if (not isinstance(value, dict) or set(value) != RECEIPT_FIELDS
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value['instance_id'], str) or not HEX32.fullmatch(value['instance_id'])
            or value['host'] != '127.0.0.1' or type(value['port']) is not int or not 1 <= value['port'] <= 65535
            or type(value['pid']) is not int or value['pid'] <= 0
            or type(value['process_created']) not in {int, float} or not math.isfinite(value['process_created']) or value['process_created'] <= 0
            or not isinstance(value['token'], str) or not HEX64.fullmatch(value['token'])
            or not isinstance(value['build_sha256'], str) or not HEX64.fullmatch(value['build_sha256'])
            or not isinstance(value['started_at'], str) or not 1 <= len(value['started_at']) <= 100
            or not isinstance(value['phase'], str) or value['phase'] not in {'listening', 'stopped'}):
        raise SafetyError('本机实例回执无效；无法确认归属')
    expected_provider = configuration['provider_config'] or str(Path(configuration['workspace_root'])/'provider.env')
    if (value['workspace_root'] != configuration['workspace_root'] or value['provider_config'] != expected_provider
            or type(value['request_budget']) is not int or value['request_budget'] != configuration['request_budget']
            or type(value['allow_auto']) is not bool or value['allow_auto'] != configuration['allow_auto']):
        raise SafetyError('本机实例与原启动设置不一致；无法确认归属')
    return dict(value)


def _receipt(home, configuration):
    try:
        path = _control_directory(home)/'control.json'
        return None if not path.exists() else validate_receipt(_read_json(path, private=True), configuration)
    except (SafetyError, OSError, ValueError, TypeError):
        raise GatewayError('无法核对原实例回执。请核对原 --home 和工作台状态锁。') from None


def _same_process(receipt):
    import psutil
    try:
        process = psutil.Process(receipt['pid'])
        return abs(process.create_time() - receipt['process_created']) < .001 and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return None


def _lock_state(root):
    """Probe the original lock without creating, truncating, or deleting it."""
    path = local_path(Path(root)/'workbench.lock')
    if not path.exists():
        return 'absent'
    if not path.is_file():
        return 'unknown'
    descriptor = os.open(path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0))
    try:
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError:
                return 'held'
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 'held'
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        return 'released'
    finally:
        os.close(descriptor)


def _request(receipt, method='GET', payload=None, *, timeout=3.0):
    # http.client neither uses environment proxies nor follows redirects.
    connection = http.client.HTTPConnection('127.0.0.1', receipt['port'], timeout=timeout)
    path = '/api/service' if method == 'GET' else '/api/actions/stop_workbench'
    headers = {'Authorization': 'Bearer ' + receipt['token'],
               'Origin': f"http://127.0.0.1:{receipt['port']}"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        if method == 'POST' and response.status == 409:
            raise GatewayStopRejected('服务拒绝停止请求。请核对原工作台的活动任务、监控和停止条件。')
        content = response.read(MAX_JSON + 1)
        if len(content) > MAX_JSON or response.status not in {200, 202}:
            raise GatewayError('本机服务未返回可核对的认证回执。请核对原 --home 与服务实例。')
        value = json.loads(content.decode('utf-8'), object_pairs_hook=_strict_object)
        if not isinstance(value, dict):
            raise GatewayError('本机服务回执无法核对。请运行 gateway status 核对原实例。')
        return value
    finally:
        connection.close()


def _verified_service(receipt):
    if _same_process(receipt) is not True:
        raise GatewayError('无法确认原实例进程身份。请运行 gateway status 核对原实例。')
    try:
        value = _request(receipt)
    except (OSError, http.client.HTTPException):
        raise GatewayError('未能读取本机服务状态。请稍后运行 gateway status 核对原实例。') from None
    except (ValueError, TypeError):
        raise GatewayError('本机服务回执无法核对。请核对原 --home 与服务实例。') from None
    runtime = value.get('runtime')
    if (not isinstance(runtime, dict) or value.get('gateway_identity') != receipt['instance_id']
            or value.get('stop_revision') != hashlib.sha256(receipt['token'].encode()).hexdigest()
            or any(value.get(name) != receipt[name] for name in ('workspace_root', 'provider_config', 'request_budget', 'allow_auto'))
            or runtime.get('build_sha256') != receipt['build_sha256'] or runtime.get('started_at') != receipt['started_at']
            or type(runtime.get('code_changed')) is not bool
            or type(value.get('stopping', False)) is not bool
            or not set(BUSY_FIELDS).issubset(value)
            or any(type(value[name]) is not bool for name in BUSY_FIELDS if name not in {'active', 'history_active'})
            or any(value[name] is not None and not isinstance(value[name], str) for name in ('active', 'history_active'))):
        raise GatewayError('认证服务与原实例身份不一致。请核对原 --home 与服务实例。')
    if _same_process(receipt) is not True or _lock_state(receipt['workspace_root']) != 'held':
        raise GatewayError('原实例进程或状态锁已变化。请运行 gateway status 核对。')
    return value


def verified_gateway(home=None):
    """Internal connector handshake; never return this capability to a tool UI.

    Consumers must keep routes fixed in their own code, retain the original
    authorization gates, and never expose ``receipt['token']`` or launch links.
    """
    configuration = load_configuration(home)
    receipt = _receipt(home, configuration)
    if receipt is None:
        raise GatewayError('没有可核对的原实例；请先启动 gateway run')
    return receipt, _verified_service(receipt)


def gateway_status(home=None):
    home = gateway_home(home)
    directory = _control_directory(home)
    if not (directory/'config.json').exists():
        return {'status': 'not_configured', 'identity_verified': False}
    configuration = load_configuration(home, check_state=False)
    try:
        _require_original(configuration)
    except (SafetyError, OSError, ValueError, TypeError):
        return {'status': 'unknown', 'identity_verified': False, 'detail': '原状态配置未完整核验；不执行控制'}
    try:
        receipt = _receipt(home, configuration)
        lock = _lock_state(configuration['workspace_root'])
    except GatewayError as error:
        return {'status': 'unknown', 'identity_verified': False, 'detail': str(error)}
    except (SafetyError, OSError, ValueError, TypeError):
        return {'status': 'unknown', 'identity_verified': False, 'detail': '无法核对原状态锁。请核对本机文件权限。'}
    if receipt is None:
        return {'status': 'not_running' if lock in {'absent', 'released'} else 'unknown',
                'identity_verified': False, 'lock': lock,
                **({'detail': '原状态锁仍被持有或无法核验。请核对原工作台。'} if lock not in {'absent', 'released'} else {})}
    process = _same_process(receipt)
    if process is False and lock in {'absent', 'released'}:
        return {'status': 'stopped', 'identity_verified': False, 'process_running': False, 'lock': lock}
    try:
        service = _verified_service(receipt)
    except GatewayError as error:
        return {'status': 'unknown', 'identity_verified': False, 'detail': str(error),
                'process_running': process, 'lock': lock}
    except (SafetyError, OSError, ValueError, TypeError):
        return {'status': 'unknown', 'identity_verified': False,
                'process_running': process, 'lock': lock,
                'detail': '无法核对原服务回执或状态锁。请核对原 --home 与工作台。'}
    readiness = service.get('readiness', {})
    if not isinstance(readiness, dict):
        readiness = {}
    ready = readiness.get('status')
    if not isinstance(ready, str) or ready not in {'ready', 'blocked', 'unknown'} or readiness.get('stale') is not False:
        ready = 'unknown'
    busy = any(bool(service[name]) for name in BUSY_FIELDS)
    drift = service['runtime']['code_changed']
    stopping = service.get('stopping', False)
    return {'status': 'stopping' if stopping else 'code_changed' if drift else 'busy' if busy else 'ready' if ready == 'ready' else 'running',
            'identity_verified': True, 'process_running': True, 'reachable': True,
            'ready': ready, 'busy': busy, 'stopping': stopping, 'code_changed': drift, 'lock': lock,
            'model_connectivity': 'not_checked', 'foreground': True}


def run_gateway(home=None):
    from .repository_workbench import ReactBuildError, RepositoryWorkbench, serve_workbench
    import psutil
    home = gateway_home(home)
    configuration = load_configuration(home)
    # Only an observed dead instance with a released original lock may be
    # replaced. Authentication failure or unknown state never grants takeover.
    current = gateway_status(home)
    if current['identity_verified']:
        raise GatewayError('原实例正在运行。请用 gateway open 打开工作台，或核对停止条件后使用 gateway restart。')
    if current['status'] not in {'not_running', 'stopped'}:
        raise GatewayError(current.get('detail', '原实例归属未知。请先用 gateway status 核对原服务。'))
    directory = _control_directory(home)
    try:
        workbench = RepositoryWorkbench(Path(configuration['workspace_root']),
                    Path(configuration['provider_config']) if configuration['provider_config'] else None,
                    request_budget=configuration['request_budget'], allow_auto=configuration['allow_auto'], ui=configuration['ui'])
    except ReactBuildError as error:
        raise GatewayError(str(error)) from None
    instance_id, record = secrets.token_hex(16), None

    def listening(application, port):
        nonlocal record
        application.gateway_identity = instance_id
        runtime = application.runtime.view()
        record = {'schema_version': 1, 'instance_id': instance_id, 'host': '127.0.0.1', 'port': port,
                  'pid': os.getpid(), 'process_created': psutil.Process().create_time(), 'token': application.token,
                  'workspace_root': str(application.root), 'provider_config': str(application.provider_config),
                  'request_budget': application.request_budget, 'allow_auto': application.allow_auto,
                  'build_sha256': runtime['build_sha256'], 'started_at': runtime['started_at'], 'phase': 'listening'}
        validate_receipt(record, configuration)
        _write_private(directory/'control.json', record, replace=True)
        print(f'Gateway listening on http://127.0.0.1:{port}/; use repocare gateway open. Foreground process.', flush=True)

    try:
        serve_workbench(workbench, configuration['port'], on_listen=listening, print_url=False)
    except ReactBuildError as error:
        raise GatewayError(str(error)) from None
    finally:
        workbench.close()
        if record is not None:
            # Never overwrite a successor's receipt, including on startup error.
            try:
                current = _receipt(home, configuration)
                if current is not None and current['instance_id'] == instance_id:
                    _write_private(directory/'control.json', dict(record, phase='stopped'), replace=True)
            except (SafetyError, OSError, ValueError):
                pass  # The original record is retained for unknown-state diagnosis.
    return {'status': 'stopped', 'foreground': True}


def open_gateway(home=None):
    configuration = load_configuration(home)
    receipt = _receipt(home, configuration)
    if receipt is None:
        raise GatewayError('没有可核对的运行实例；请先启动 gateway run')
    _verified_service(receipt)
    if not webbrowser.open(f"http://127.0.0.1:{receipt['port']}/#session={receipt['token']}"):
        raise GatewayError('未能打开默认浏览器；请核对浏览器设置后重试 open')
    return {'status': 'opened', 'identity_verified': True}


def stop_gateway(home=None, *, wait_seconds=10):
    if type(wait_seconds) not in {int, float} or not math.isfinite(wait_seconds) or not .1 <= wait_seconds <= 60:
        raise GatewayError('停止等待时间须为0.1至60秒')
    configuration = load_configuration(home)
    receipt = _receipt(home, configuration)
    if receipt is None:
        raise GatewayError('没有可核对的原实例；不发送停止操作')
    if _same_process(receipt) is False:
        lock = _lock_state(configuration['workspace_root'])
        if lock in {'absent', 'released'}:
            return {'status': 'stopped', 'process_running': False, 'lock': lock, 'request_sent': False}
    service = _verified_service(receipt)
    if service.get('stopping'):
        raise GatewayError('原服务正在停止。请用 gateway status 等待原进程退出和状态锁释放。')
    if any(bool(service[name]) for name in BUSY_FIELDS):
        raise GatewayError('工作台正忙或监控仍开启；请先在原页面暂停并等待当前操作结束')
    # Send exactly once. An accepted queued action is not a shutdown receipt.
    request_id = secrets.token_hex(16)
    try:
        response = _request(receipt, 'POST', {'request_id': request_id, 'revision': service['stop_revision']})
    except GatewayStopRejected as error:
        return {'status': 'blocked', 'request_sent': True, 'automatic_retry': False, 'detail': str(error)}
    except (OSError, ValueError, TypeError, http.client.HTTPException, SafetyError):
        return {'status': 'unknown', 'request_sent': True, 'automatic_retry': False,
                'detail': '未收到可核对的停止回执。请用 gateway status 核对原实例与状态锁，勿重复提交。'}
    if (response.get('id') != request_id or response.get('action') != 'stop_workbench'
            or response.get('status') not in {'running', 'finished'}):
        return {'status': 'unknown', 'request_sent': True, 'automatic_retry': False,
                'detail': '停止回执与原请求不匹配或未完成。请用 gateway status 核对，勿重复提交。'}
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            if _same_process(receipt) is False:
                lock = _lock_state(configuration['workspace_root'])
                if lock in {'absent', 'released'}:
                    return {'status': 'stopped', 'process_running': False, 'lock': lock, 'request_sent': True}
        except (OSError, SafetyError):
            break
        time.sleep(.05)
    return {'status': 'unknown', 'request_sent': True, 'automatic_retry': False,
            'detail': '停止已受理，尚未确认原进程退出及状态锁释放。请用 gateway status 核对，勿重复提交。'}


def restart_gateway(home=None, *, wait_seconds=10):
    result = stop_gateway(home, wait_seconds=wait_seconds)
    if result['status'] != 'stopped':
        return result
    return run_gateway(home)


def dispatch(args):
    home = getattr(args, 'home', None)
    operation = 'setup' if args.command == 'setup' else args.gateway_command
    try:
        if operation == 'setup':
            result = setup_configuration(args)
        elif operation == 'run':
            result = run_gateway(home)
        elif operation == 'status':
            result = gateway_status(home)
        elif operation == 'open':
            result = open_gateway(home)
        elif operation == 'stop':
            result = stop_gateway(home, wait_seconds=args.wait_seconds)
        elif operation == 'restart':
            result = restart_gateway(home, wait_seconds=args.wait_seconds)
        else:
            raise SafetyError('未知 Gateway 命令')
        code = 1 if result['status'] in {'unknown', 'not_configured', 'blocked'} else 0
    except GatewayError as error:
        result, code = {'status': 'blocked', 'detail': str(error)}, 1
    except (SafetyError, OSError, ValueError, TypeError, http.client.HTTPException):
        # Input paths, exception details and authentication material are never
        # echoed. The actionable reason is kept in fixed text.
        detail = {
            'setup': '无法保存私有启动设置。请核对本机路径、配置格式及文件权限。',
            'run': '工作台启动未完成。请核对原 --home、状态目录权限和本机端口。',
            'status': '无法读取本机 Gateway 状态。请核对原 --home 和本机文件权限。',
            'open': '无法确认可打开的原实例。请先运行 gateway status 核对。',
            'stop': '无法核对停止前的原实例或状态锁。请先运行 gateway status 核对。',
            'restart': '重启未完成。请先运行 gateway status 核对原实例和状态锁。',
        }
        result, code = {'status': 'blocked', 'detail': detail.get(operation, '未知 Gateway 命令。')}, 1
    print(json.dumps(result, ensure_ascii=False) if getattr(args, 'json', False) else result['status'] +
          (': ' + result['detail'] if result.get('detail') else ''), flush=True)
    return code


def _common(parser):
    parser.add_argument('--home', type=Path, default=argparse.SUPPRESS,
                        help='Private RepoCare home (default: REPOCARE_HOME or ~/.repocare)')
    parser.add_argument('--json', action='store_true', default=argparse.SUPPRESS,
                        help='Print a redacted status object')
    parser.set_defaults(gateway_handler=dispatch)


def configure_parser(subparsers):
    setup = subparsers.add_parser('setup', help='Save private local launch settings once; model key is optional')
    _common(setup)
    setup.add_argument('--workspace-root', type=Path)
    setup.add_argument('--provider-config', type=Path)
    setup.add_argument('--request-budget', type=int)
    setup.add_argument('--allow-auto', action='store_true', default=None)
    setup.add_argument('--port', type=int)
    setup.add_argument('--ui', choices=['react', 'legacy'])
    gateway = subparsers.add_parser('gateway', help='Run and manage the existing foreground workbench')
    _common(gateway)
    operations = gateway.add_subparsers(dest='gateway_command', required=True)
    for name in ('run', 'restart', 'stop', 'status', 'open'):
        command = operations.add_parser(name)
        _common(command)
        if name in {'stop', 'restart'}:
            command.add_argument('--wait-seconds', type=float, default=10, help='Bounded original-process shutdown wait, at most 60 seconds')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='repocare')
    configure_parser(parser.add_subparsers(dest='command', required=True))
    return dispatch(parser.parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
