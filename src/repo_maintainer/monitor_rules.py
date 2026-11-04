"""Maintainer root rules in the original monitor journal, never repository content."""
import json
import os
from pathlib import Path

from .dashboard import unlinked
from .git import RepositoryGateway
from .github_connection import branch_name, github_url
from .issue_intake import fingerprint
from .maintenance_rules import maintenance_rules, require
from .models import digest


FAMILIES = ('unittest', 'pytest', 'node-native')
FIELDS = {'path', 'revision', 'branch', 'github_url', 'interval_seconds', 'timeout_seconds',
          'read_roots', 'write_roots', 'check_families', 'mode', 'allow_local_code',
          'allow_install', 'allow_network'}


def family(check):
    identity = check['id']
    return 'unittest' if identity.startswith('unittest-') else identity


def scope(spec):
    rules = spec.get('maintenance_roots')
    if rules:
        return rules['read_roots'], rules['write_roots']
    return list(dict.fromkeys([*spec['allowed_paths'], *spec['readonly_paths']])), spec['allowed_paths']


class MonitorRootRules:
    def __init__(self, monitor):
        self.monitor = monitor
        self.hub = monitor.hub

    def current(self, identity, *, required=False):
        registry = self.monitor.registries[identity]
        config = registry.load('monitor_root_rules').get('current')
        require(config is not None or not required, '目录维护规则缺失；保留原账本，不能自动恢复权限')
        if config is None:
            return None
        spec = self.monitor.records[identity]['spec']
        require(config['sha256'] == fingerprint({k:v for k,v in config.items() if k!='sha256'})
                and config['monitor_spec_sha256'] == fingerprint(spec)
                and registry.load('monitor_root_rule_version').get(config['sha256']) == config,
                '目录维护规则或原监控绑定已变化')
        return config

    def preview(self, payload):
        from .repository_workbench import inspect_repository
        require(isinstance(payload, dict) and set(payload) == FIELDS, '目录维护规则字段不匹配')
        source = inspect_repository(Path(payload['path']))
        require(source['clean'] and source['revision'] == payload['revision'], '仓库预览已变化或有未提交修改；保留现场')
        require(branch_name(payload['branch']) == source['branch'], '请在所选分支的当前干净版本预览维护规则')
        repository = RepositoryGateway(Path(source['path']))
        require(not self.hub.root.is_relative_to(repository.root), '工作台状态必须在目标仓库外')
        url = github_url(payload['github_url'])[1] if payload['github_url'] else ''
        require(not url or url == source['github_url'], 'GitHub 地址须与预览仓库 origin 一致')
        require(type(payload['interval_seconds']) is int and 60 <= payload['interval_seconds'] <= 3600,
                '监控间隔须为60至3600秒')
        rule = maintenance_rules(repository, read_roots=payload['read_roots'], write_roots=payload['write_roots'],
            check_ids=['root-scope-validation'], mode=payload['mode'], allow_auto=self.hub.allow_auto,
            timeout_seconds=payload['timeout_seconds'], actor='local-workbench')
        families = payload['check_families']
        require(isinstance(families, list) and 1 <= len(families) <= len(FAMILIES)
                and all(isinstance(f,str) and f in FAMILIES for f in families)
                and len(families) == len(set(families)), '请选择支持的固定检查方式')
        for key in ('allow_local_code', 'allow_install', 'allow_network'):
            require(type(payload[key]) is bool, '环境权限须明确选择')
        require(not payload['allow_install'] or payload['allow_local_code'], '安装依赖须同时允许执行可信项目代码')
        require(not payload['allow_network'] or payload['allow_install'], '联网安装须同时允许依赖安装')
        matches = [r for r in self.monitor.records.values() if r['spec']['path'] == source['path']
                   or (url and r['spec']['github_url'] == url)]
        require(len(matches) <= 1, '存在多份旧监控身份；请先核对，不能另外创建额度')
        identity = digest((url.casefold() if url else os.path.normcase(source['path'])).encode())[:16]
        old = None
        if matches:
            spec = matches[0]['spec']
            require(all(spec[k] == payload[k] for k in ('branch','interval_seconds','timeout_seconds'))
                    and spec['path'] == source['path'] and spec['github_url'] == url,
                    '已有监控的仓库、来源、分支、间隔或超时不可借配置规则改变')
            identity = spec['id']
            require(not any(server.session.active for (mid,_),server in self.monitor.servers.items() if mid==identity),
                    '原监控任务仍在执行；请先暂停并核验，不能在执行中更换规则')
            old = self.current(identity)
            budget = self.monitor.registries[identity].budget()
        else:
            require(identity not in self.monitor.records, '原监控归属不同，不能另建额度')
            spec = {'schema_version': 2, 'id': identity, 'path': source['path'], 'github_url': url,
                'branch': payload['branch'], 'interval_seconds': payload['interval_seconds'],
                'timeout_seconds': float(payload['timeout_seconds']), 'allowed_paths': [],
                'readonly_paths': [], 'allowed_tests': [], 'requires_root_rules': True}
            budget = {'request_budget': self.hub.request_budget, 'requests_used': 0,
                      'requests_reserved_unknown': 0, 'requests_remaining': self.hub.request_budget}
        settings = {**{k: payload[k] for k in ('mode','allow_local_code','allow_install','allow_network')},
            'read_roots': rule['read_roots'], 'write_roots': rule['write_roots'],
            'check_families': sorted(families), 'timeout_seconds': rule['timeout_seconds']}
        if old and old['settings'] == settings:
            config = old
        else:
            if old:
                require(not any(e.get('root_preparation',{}).get('status') in
                    {'started','baseline_started','interrupted_no_replay','blocked_no_replay'}
                    for e in self.monitor.registries[identity].load('event').values()),
                    '原目录检查存在未结清或中断意图；先核验原现场，不能换规则重放')
            require(not old or old['version'] < 5, '已达本仓库规则修订上限；保留全部历史和原账本')
            core = {'schema_version': 1, 'version': old['version']+1 if old else 1,
                'previous_sha256': old['sha256'] if old else None, 'settings': settings,
                'repository_id': rule['repository_id'], 'monitor_spec_sha256': fingerprint(spec),
                'initial_head': source['head'], 'candidate_execution_connected': False,
                'local_execution_is_os_sandbox': False, 'budget': 'original_monitor_registry_only'}
            config = {**core, 'sha256': fingerprint(core)}
        value = {'spec': spec, 'configuration': config, 'repository_revision': source['revision'],
                 'budget': budget, 'tests_executed': False, 'model_requests': 0}
        return {'preview': value, 'revision': self.challenge(value)}

    def challenge(self, value):
        # JSON in a browser round-trips 30.0 as 30. Bind its numeric value,
        # retaining all fields and exact non-integral values.
        def canonical(item):
            if isinstance(item, dict):
                return {key: canonical(part) for key, part in item.items()}
            if isinstance(item, list):
                return [canonical(part) for part in item]
            if isinstance(item, float) and item.is_integer():
                return int(item)
            return item
        return fingerprint({'preview': canonical(value), 'session': self.hub.token})

    def configure(self, payload):
        require(set(payload) == {'preview','revision','confirmed'} and payload['confirmed'] is True,
                '请先预览并明确确认目录维护规则')
        value = payload['preview']
        require(isinstance(value,dict) and len(json.dumps(value).encode()) <= 32768
                and payload['revision'] == self.challenge(value), '目录规则预览已失效')
        spec, config = value['spec'], value['configuration']
        expected = self.preview({**config['settings'], **{k:spec[k] for k in
            ('path','branch','github_url','interval_seconds')}, 'revision': value['repository_revision']})
        require(expected['preview'] == value, '目录规则与当前原账本、来源或预览不符')
        # Persist only the server-reconstructed objects, including their exact
        # numeric representation used by the existing journal fingerprints.
        spec, config = expected['preview']['spec'], expected['preview']['configuration']
        identity = spec['id']
        if identity not in self.monitor.records:
            require(len(self.monitor.records) < 10, '首版最多10个监控仓库')
            directory = unlinked(self.monitor.root / identity)
            directory.mkdir()
            with (directory/'spec.json').open('x', encoding='utf-8') as stream:
                json.dump(spec, stream, ensure_ascii=False)
            self.monitor._open(identity, spec)
        registry = self.monitor.registries[identity]
        with registry.transaction() as db:
            registry.put('monitor_root_rule_version', config['sha256'], config, db)
            registry.put('monitor_root_rules', 'current', config, db)
        record = self.monitor.records[identity]
        record.update(enabled=True, error=None)
        self.monitor._save(record)
        self.monitor.next_due[identity] = 0
        return {'monitor_id': identity, 'configuration': config, **registry.budget(),
                'candidate_execution_connected': False, 'model_requests': 0}
