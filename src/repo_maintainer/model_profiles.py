"""Immutable local provider profiles; reads never create state or call a provider."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path

from .dashboard import unlinked
from .models import SafetyError
from .provider_config import PROVIDER_FIELDS, resolve_model_limits, resolve_model_reasoning, validate_provider_config


MAX_PROFILES = 32
MAX_CATALOG_BYTES = 65536
_ID = re.compile(r"[0-9a-f]{32}\Z")
_REVISION = re.compile(r"[0-9a-f]{48}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ERROR = "模型配置无法读取或保存，请刷新并检查本机配置。"


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise SafetyError(_ERROR)
        result[key] = value
    return result


def _name(value):
    return (isinstance(value, str) and value == value.strip() and 0 < len(value) <= 80
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _configuration_identity(config):
    fields = {key: value for key, value in config.items() if key not in
              {'PI_MAINTENANCE_CONTEXT_WINDOW_TOKENS', 'PI_MAINTENANCE_MAX_OUTPUT_TOKENS'}}
    if 'PI_MAINTENANCE_REASONING_LEVELS' in fields:
        # Object member order is not a capability or a new allowance identity.
        # Preserve the array's display order and never rewrite the pinned env.
        fields['PI_MAINTENANCE_REASONING_LEVELS'] = json.dumps(
            json.loads(fields['PI_MAINTENANCE_REASONING_LEVELS']),
            sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    return fields, {key: value for key, value in resolve_model_limits(config).items() if key != 'limits_explicit'}


class ModelProfiles:
    def __init__(self, hub):
        self.hub = hub
        self.root = Path(hub.root) / 'model-profiles'
        self.catalog_path = self.root / 'catalog.json'
        self._initial_revision = secrets.token_hex(24)

    def _file(self, path, limit):
        path = unlinked(path)
        initial = path.stat()
        if not stat.S_ISREG(initial.st_mode) or initial.st_size > limit:
            raise SafetyError(_ERROR)
        with path.open('rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise SafetyError(_ERROR)
            content = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        if (len(content) > limit or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns):
            raise SafetyError(_ERROR)
        unlinked(path)
        if path.stat().st_ino != after.st_ino:
            raise SafetyError(_ERROR)
        return content

    def _provider(self, path, expected=None):
        content = self._file(path, 65536)
        digest = hashlib.sha256(content).hexdigest()
        if expected is not None and digest != expected:
            raise SafetyError(_ERROR)
        # Parse the exact bytes that were hashed; reopening for parsing could use
        # different bytes even though the pinned read itself was valid.
        config = {}
        for line in content.decode('utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            name, separator, value = line.partition('=')
            name, value = name.strip(), value.strip()
            if not separator or name not in PROVIDER_FIELDS or name in config:
                raise SafetyError(_ERROR)
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]:
                    raise SafetyError(_ERROR)
                value = value[1:-1]
            config[name] = value
        return validate_provider_config(config), digest

    def _legacy(self):
        try:
            config, digest = self._provider(Path(self.hub.provider_config))
            return config, digest
        except (SafetyError, OSError, ValueError):
            return None, None

    def _catalog(self, pending_id=None):
        target = unlinked(self.catalog_path)
        if not target.exists():
            # A wrong file type at the directory boundary is also rejected on GET.
            root = unlinked(self.root)
            if root.exists() and not root.is_dir():
                raise SafetyError(_ERROR)
            if root.exists() and any(root.iterdir()):
                # Losing the catalog must not make saved credentials appear new and
                # mint another workspace identity (and therefore another ceiling).
                raise SafetyError(_ERROR)
            legacy, _ = self._legacy()
            return {'schema': 1, 'revision': self._initial_revision,
                    'default_profile_id': 'legacy' if legacy else None, 'profiles': []}
        try:
            data = json.loads(self._file(target, MAX_CATALOG_BYTES).decode('utf-8'), object_pairs_hook=_pairs)
        except RecursionError:
            raise SafetyError(_ERROR) from None
        required = {'schema', 'revision', 'default_profile_id', 'profiles'}
        if (not isinstance(data, dict) or not required <= set(data)
                or set(data) - required - {'archived_profile_ids'}
                or type(data['schema']) is not int or data['schema'] != 1
                or not isinstance(data['revision'], str) or not _REVISION.fullmatch(data['revision'])
                or not isinstance(data['profiles'], list) or len(data['profiles']) > MAX_PROFILES):
            raise SafetyError(_ERROR)
        ids = {'legacy'}
        fingerprints = set()
        for entry in data['profiles']:
            if (not isinstance(entry, dict) or set(entry) != {'id', 'name', 'config_sha256'}
                    or not isinstance(entry['id'], str) or not _ID.fullmatch(entry['id'])
                    or entry['id'] in ids or not _name(entry['name'])
                    or not isinstance(entry['config_sha256'], str) or not _SHA.fullmatch(entry['config_sha256'])
                    or entry['config_sha256'] in fingerprints):
                raise SafetyError(_ERROR)
            ids.add(entry['id'])
            fingerprints.add(entry['config_sha256'])
        if data['default_profile_id'] is not None and (
                not isinstance(data['default_profile_id'], str) or data['default_profile_id'] not in ids):
            raise SafetyError(_ERROR)
        archived = data.get('archived_profile_ids', [])
        if (not isinstance(archived, list) or any(not isinstance(item, str) or item not in ids for item in archived)
                or len(archived) != len(set(archived)) or data['default_profile_id'] in archived):
            raise SafetyError(_ERROR)
        known = {entry['id'] + '.env' for entry in data['profiles']}
        if pending_id is not None:
            # Only failed-create cleanup may recognize its own unpublished file.
            known.add(pending_id + '.env')
        if any(path.suffix.lower() == '.env' and path.name not in known
               for path in unlinked(self.root).iterdir()):
            raise SafetyError(_ERROR)
        return data

    def _resolve(self, catalog, profile_id):
        if profile_id is None:
            profile_id = catalog['default_profile_id']
        if not isinstance(profile_id, str):
            raise SafetyError('请先添加并选择有效模型配置。')
        if profile_id == 'legacy':
            path, name, expected = Path(self.hub.provider_config), '原模型配置', None
        else:
            if not _ID.fullmatch(profile_id):
                raise SafetyError('模型配置不存在或已失效。')
            entry = next((e for e in catalog['profiles'] if e['id'] == profile_id), None)
            if entry is None:
                raise SafetyError('模型配置不存在或已失效。')
            path, name, expected = self.root / (profile_id + '.env'), entry['name'], entry['config_sha256']
        config, digest = self._provider(path, expected)
        return {'id': profile_id, 'name': name, 'model': config['PI_MAINTENANCE_MODEL'],
                'base_url': config['PI_MAINTENANCE_BASE_URL'], 'path': unlinked(path),
                'legacy': profile_id == 'legacy', 'config_sha256': digest, **resolve_model_limits(config),
                **resolve_model_reasoning(config)}

    def _view(self, catalog):
        profiles = []
        for entry in [{'id': 'legacy', 'name': '原模型配置'}, *catalog['profiles']]:
            archived = entry['id'] in catalog.get('archived_profile_ids', [])
            row = {'id': entry['id'], 'name': entry['name'], 'model': '', 'base_url': '',
                   'key_set': False, 'legacy': entry['id'] == 'legacy', 'selectable': False,
                   'archived': archived,
                   'context_window_tokens': None, 'max_output_tokens': None, 'limits_explicit': False,
                   'reasoning_protocol': None, 'reasoning_effort': None, 'reasoning_explicit': False,
                   'reasoning_levels': None}
            try:
                resolved = self._resolve(catalog, entry['id'])
                row.update(model=resolved['model'], base_url=resolved['base_url'], key_set=True, selectable=not archived)
                row.update({key: resolved[key] for key in
                            ('context_window_tokens', 'max_output_tokens', 'limits_explicit',
                             'reasoning_protocol', 'reasoning_effort', 'reasoning_explicit', 'reasoning_levels')})
            except (SafetyError, OSError, ValueError):
                pass
            profiles.append(row)
        return {'revision': catalog['revision'], 'default_profile_id': catalog['default_profile_id'],
                'profiles': profiles, 'max_profiles': MAX_PROFILES}

    def view(self):
        with self.hub.lock:
            try:
                return self._view(self._catalog())
            except (SafetyError, OSError, ValueError):
                raise SafetyError(_ERROR) from None

    def resolve(self, profile_id=None):
        with self.hub.lock:
            try:
                return self._resolve(self._catalog(), profile_id)
            except (SafetyError, OSError, ValueError):
                raise SafetyError(_ERROR) from None

    def _selectable(self, catalog, profile_id):
        selected = catalog['default_profile_id'] if profile_id is None else profile_id
        if selected in catalog.get('archived_profile_ids', []):
            raise SafetyError('模型配置已删除，请先在模型设置中恢复。')
        return self._resolve(catalog, selected)

    def resolve_selectable(self, profile_id=None):
        """Validate a new selection; old task/session bindings must use resolve."""
        with self.hub.lock:
            try:
                return self._selectable(self._catalog(), profile_id)
            except (SafetyError, OSError, ValueError):
                raise SafetyError(_ERROR) from None

    def _request(self, value, action, fields, optional=()):
        required = {'revision', 'action', *fields}
        if (not isinstance(value, dict) or not required <= set(value) or not set(value) <= required | set(optional)
                or not all(isinstance(value[key], str) for key in required) or value['action'] != action):
            raise SafetyError('模型配置字段无效。')
        if getattr(self.hub, 'closed', False):
            raise SafetyError('工作台已关闭。')
        catalog = self._catalog()
        if value['revision'] != catalog['revision']:
            raise SafetyError('模型配置已变化，请刷新后重试。')
        return catalog

    def _persist(self, catalog):
        root = unlinked(self.root)
        root.mkdir(mode=0o700, parents=False, exist_ok=True)
        unlinked(root)
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.catalog-', suffix='.tmp', dir=root)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(json.dumps(catalog, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            unlinked(self.catalog_path)
            os.replace(unlinked(Path(temporary)), self.catalog_path)
        finally:
            if temporary and os.path.lexists(temporary):
                os.unlink(temporary)

    def create(self, value):
        with self.hub.lock:
            created = None
            try:
                catalog = self._request(value, 'create', {'name', 'base_url', 'model'},
                                        {'api_key', 'source_profile_id', 'context_window_tokens', 'max_output_tokens',
                                         'reasoning_protocol', 'reasoning_effort', 'reasoning_levels'})
                if not _name(value['name']):
                    raise SafetyError('模型配置名称无效。')
                key = value.get('api_key', '')
                if 'source_profile_id' in value:
                    source = self._selectable(catalog, value['source_profile_id'])
                    if value['base_url'] != source['base_url']:
                        raise SafetyError('复用服务商密钥时，地址必须与原配置完全一致。')
                    if key == '':
                        original, _ = self._provider(source['path'], source['config_sha256'])
                        key = original['PI_MAINTENANCE_API_KEY']
                config = {'PI_MAINTENANCE_API_KEY': key,
                    'PI_MAINTENANCE_BASE_URL': value['base_url'], 'PI_MAINTENANCE_MODEL': value['model']}
                for public, field in (('context_window_tokens', 'PI_MAINTENANCE_CONTEXT_WINDOW_TOKENS'),
                                      ('max_output_tokens', 'PI_MAINTENANCE_MAX_OUTPUT_TOKENS')):
                    if public in value:
                        if type(value[public]) is not int:
                            raise SafetyError('模型长度必须是整数。')
                        config[field] = str(value[public])
                for public, field in (('reasoning_protocol', 'PI_MAINTENANCE_REASONING_PROTOCOL'),
                                      ('reasoning_effort', 'PI_MAINTENANCE_REASONING_EFFORT')):
                    if public in value:
                        config[field] = value[public]
                if 'reasoning_levels' in value:
                    if not isinstance(value['reasoning_levels'], list):
                        raise SafetyError('思考档位必须是列表。')
                    config['PI_MAINTENANCE_REASONING_LEVELS'] = json.dumps(value['reasoning_levels'], ensure_ascii=True, separators=(',', ':'))
                config = validate_provider_config(config)
                # Compare actual complete configurations, never only endpoint/model or display name.
                for entry in [{'id': 'legacy'}, *catalog['profiles']]:
                    try:
                        resolved = self._resolve(catalog, entry['id'])
                        existing, _ = self._provider(resolved['path'], resolved['config_sha256'])
                    except (SafetyError, OSError, ValueError):
                        if entry['id'] == 'legacy':
                            continue
                        # A damaged saved configuration cannot be recreated for a fresh budget.
                        raise SafetyError(_ERROR) from None
                    # Explicitly spelling the same effective limits does not mint
                    # another identity/ceiling. No old profile is rewritten.
                    if _configuration_identity(existing) == _configuration_identity(config):
                        if entry['id'] in catalog.get('archived_profile_ids', []):
                            raise SafetyError('相同模型配置已删除，请先恢复原配置。')
                        return {**self._view(catalog), 'created_profile_id': entry['id']}
                if len(catalog['profiles']) >= MAX_PROFILES:
                    raise SafetyError('模型配置数量已达上限。')
                root = unlinked(self.root)
                root.mkdir(mode=0o700, parents=False, exist_ok=True)
                profile_id = secrets.token_hex(16)
                target = unlinked(root / (profile_id + '.env'))
                content = ''.join(f'{name}="{text}"\n' for name, text in config.items()).encode('utf-8')
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created = (profile_id, target)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._provider(target, hashlib.sha256(content).hexdigest())
                catalog['profiles'].append({'id': profile_id, 'name': value['name'],
                                            'config_sha256': hashlib.sha256(content).hexdigest()})
                if catalog['default_profile_id'] is None:
                    catalog['default_profile_id'] = profile_id
                catalog['revision'] = secrets.token_hex(24)
                self._persist(catalog)
                return {**self._view(catalog), 'created_profile_id': profile_id}
            except (SafetyError, OSError, ValueError):
                if created:
                    try:
                        # An uncertain replace may already have published the catalog. Never
                        # remove a file referenced by persisted state, even when reporting failure.
                        disk = self._catalog(pending_id=created[0]) if unlinked(self.catalog_path).exists() else {'profiles': []}
                        if not any(e['id'] == created[0] for e in disk['profiles']):
                            unlinked(created[1]).unlink()
                    except (SafetyError, OSError, ValueError):
                        pass
                raise SafetyError(_ERROR) from None

    def set_default(self, value):
        with self.hub.lock:
            try:
                catalog = self._request(value, 'set_default', {'profile_id'})
                self._selectable(catalog, value['profile_id'])
                catalog['default_profile_id'] = value['profile_id']
                catalog['revision'] = secrets.token_hex(24)
                self._persist(catalog)
                return self._view(catalog)
            except (SafetyError, OSError, ValueError):
                raise SafetyError(_ERROR) from None

    def _archive_action(self, value, action):
        with self.hub.lock:
            try:
                catalog = self._request(value, action, {'profile_id'})
                profile_id = value['profile_id']
                if profile_id not in {'legacy', *(entry['id'] for entry in catalog['profiles'])}:
                    raise SafetyError('模型配置不存在。')
                archived = list(catalog.get('archived_profile_ids', []))
                # A same-revision repeat is a no-op; stale requests still fail above.
                if (profile_id in archived) == (action == 'archive'):
                    return self._view(catalog)
                if action == 'archive':
                    archived.append(profile_id)
                else:
                    # A damaged/missing profile cannot be restored as a usable selection.
                    self._resolve(catalog, profile_id)
                    archived.remove(profile_id)
                catalog['archived_profile_ids'] = archived
                if catalog['default_profile_id'] == profile_id and action == 'archive':
                    catalog['default_profile_id'] = next((row['id'] for row in self._view(catalog)['profiles']
                                                          if row['selectable']), None)
                catalog['revision'] = secrets.token_hex(24)
                self._persist(catalog)
                return self._view(catalog)
            except (SafetyError, OSError, ValueError):
                raise SafetyError(_ERROR) from None

    def archive(self, value):
        """Hide from new choices while retaining immutable bytes and old bindings."""
        return self._archive_action(value, 'archive')

    def restore(self, value):
        return self._archive_action(value, 'restore')

    def reasoning_variant(self, value):
        """Create or reuse an immutable effort variant; never change the default."""
        with self.hub.lock:
            try:
                catalog = self._request(value, 'reasoning_variant', {'profile_id', 'reasoning_effort'})
                source = self._selectable(catalog, value['profile_id'])
                if source['reasoning_protocol'] not in {'openai', 'deepseek'}:
                    raise SafetyError('请先在模型设置中明确配置思考协议。')
                if value['reasoning_effort'] == source['reasoning_effort']:
                    return {**self._view(catalog), 'created_profile_id': source['id']}
                config, _ = self._provider(source['path'], source['config_sha256'])
                config = {**config, 'PI_MAINTENANCE_REASONING_EFFORT': value['reasoning_effort']}
                validate_provider_config(config)
                payload = {'action': 'create', 'revision': catalog['revision'],
                    'name': source['name'][:60] + ' · ' + value['reasoning_effort'],
                    'source_profile_id': source['id'], 'base_url': source['base_url'],
                    'model': source['model'], 'reasoning_protocol': source['reasoning_protocol'],
                    'reasoning_effort': value['reasoning_effort']}
                if source['reasoning_levels'] is not None:
                    payload['reasoning_levels'] = source['reasoning_levels']
                for public, field in (('context_window_tokens', 'PI_MAINTENANCE_CONTEXT_WINDOW_TOKENS'),
                                      ('max_output_tokens', 'PI_MAINTENANCE_MAX_OUTPUT_TOKENS')):
                    if field in config:
                        payload[public] = int(config[field])
                return self.create(payload)
            except (SafetyError, OSError, ValueError):
                raise SafetyError(_ERROR) from None
