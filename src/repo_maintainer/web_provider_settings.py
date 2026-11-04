"""Local first-run settings. Secrets never enter the job journal."""
from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

from .dashboard import unlinked
from .models import SafetyError
from .provider_config import load_provider_config, validate_provider_config


class WebProviderSettings:
    def __init__(self, hub, managed):
        self.hub, self.managed = hub, managed
        self.revision = secrets.token_hex(24)

    def bound(self):
        hub = self.hub
        if (bool(hub.sessions) or bool(hub.monitor.records)
                or any((hub.root/'projects').glob('*/project.json'))):
            return True
        from .assistant import _read_objects, _record, catalog_integrity
        import sqlite3
        try:
            catalog_integrity(hub.root/'assistant')
            # Ownership is independent of the most recent page shown in UI.
            chats=_read_objects(hub.root/'assistant/catalog','assistant_session',limit=1000)
            return any(_record(identity,record)['profile_id']=='legacy' for identity,record in chats.items())
        except (SafetyError,OSError,ValueError,TypeError,sqlite3.Error):
            # Damaged conversation state prevents overwriting credentials, but
            # must not disable an otherwise valid original repair provider.
            return True

    def view(self):
        hub = self.hub
        with hub.lock:
            result = {'configured': False, 'key_set': False, 'base_url': '', 'model': '',
                      'managed': self.managed, 'editable': self.managed and not self.bound(),
                      'revision': self.revision}
            if hub.provider_config.exists():
                try:
                    config = load_provider_config(unlinked(hub.provider_config))
                    result.update(configured=True, key_set=True,
                                  base_url=config['PI_MAINTENANCE_BASE_URL'], model=config['PI_MAINTENANCE_MODEL'])
                except (SafetyError, OSError, ValueError):
                    result['error'] = '模型配置无法读取，请检查本机配置文件。'
            if not self.managed:
                result['reason'] = '当前使用启动时指定的配置文件；省略 --provider-config 可使用网页配置。'
            elif not result['editable']:
                result['reason'] = '已有维护记录绑定此模型配置。如需更换，请使用 --workspace-root 指定另一个状态目录；原记录和额度保留。'
            return result

    def require_ready(self):
        if self.managed and not self.view()['configured']:
            raise SafetyError('请先在网页设置中保存 API Key、Base URL 和模型名')

    def save(self, value):
        hub = self.hub
        with hub.lock:
            if (not isinstance(value, dict)
                    or set(value) != {'revision', 'api_key', 'base_url', 'model'}
                    or not all(isinstance(v, str) for v in value.values())):
                raise SafetyError('模型设置字段无效')
            current = self.view()
            if (hub.closed or hub.active or hub.history.active or hub.monitor.queued
                    or not current['editable'] or value['revision'] != self.revision):
                raise SafetyError('设置已变化或已有维护记录，请刷新设置并核对状态目录')
            key = value['api_key']
            if not key and current['configured']:
                if value['base_url'] != current['base_url']:
                    raise SafetyError('更换 Base URL 时请重新填写对应的 API Key')
                key = load_provider_config(unlinked(hub.provider_config))['PI_MAINTENANCE_API_KEY']
            config = validate_provider_config({'PI_MAINTENANCE_API_KEY': key,
                       'PI_MAINTENANCE_BASE_URL': value['base_url'], 'PI_MAINTENANCE_MODEL': value['model']})
            target = unlinked(hub.provider_config)
            temporary = None
            try:
                fd, temporary = tempfile.mkstemp(prefix='.provider-', suffix='.tmp', dir=hub.root)
                with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
                    for name, content in config.items():
                        stream.write(f'{name}="{content}"\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                load_provider_config(unlinked(Path(temporary)))
                os.replace(temporary, target)
                self.revision = secrets.token_hex(24)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            return self.view()
