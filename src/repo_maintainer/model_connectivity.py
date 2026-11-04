"""Explicit one-response Pi check in the caller's ORIGINAL durable request ledger.

The UI controller owns queueing, stop control and model permission. No repository
contents, executable project command, free budget, or transport retry is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import uuid
from urllib.parse import urlsplit

from .job_control import OperationStopped
from .models import SafetyError, digest
from .pi_distribution import runtime_script
from .pi_provider import PiProvider
from .provider_config import load_provider_config
from .store import now
from .ui_registry import DurableProvider, provider_binding


def _hash(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def _require(condition, text):
    if not condition:
        raise SafetyError(text)


class ModelConnectivity:
    def __init__(self, session, *, before_model):
        self.session = session
        _require(isinstance(session.provider, DurableProvider)
                 and isinstance(session.provider.inner, PiProvider)
                 and session.provider.registry is session.budget_registry,
                 'Connectivity requires the original Pi provider and request ledger')
        _require(callable(before_model), 'Connectivity requires original model permission guard')
        self.before_model = before_model
        self.registry, self.provider = session.budget_registry, session.provider.inner

    def _guard(self):
        self.before_model()
        existing = self.session.provider.before_model
        if existing is not None and existing is not self.before_model:
            existing()

    def _configuration(self):
        p = self.provider
        _require(p.max_requests > 0, 'Original model permission is disabled')
        if p.mock_url:
            _require(p.config_file is None and p.credential_source == 'environment',
                     'Connectivity configuration sources cannot be mixed')
            url = urlsplit(p.mock_url)
            _require(url.scheme == 'http' and url.hostname == '127.0.0.1'
                     and not (url.username or url.password or url.query or url.fragment),
                     'Synthetic connectivity requires explicit loopback HTTP')
            config, identity = {}, {'kind': 'explicit-loopback', 'endpoint': p.mock_url,
                                    'model': 'mock-maintenance'}
        else:
            _require(p.config_file is not None and p.credential_source == 'environment',
                     'Connectivity requires an explicitly configured provider file; no credential discovery')
            path = Path(p.config_file)
            config = load_provider_config(path)
            raw = path.read_bytes()
            # Reparse after reading raw bytes so a changed file never gets a mixed pin.
            _require(load_provider_config(path) == config and path.read_bytes() == raw,
                     'Provider configuration changed during preview')
            identity = {'kind': 'explicit-file', 'endpoint': config['PI_MAINTENANCE_BASE_URL'],
                        'model': config['PI_MAINTENANCE_MODEL'],
                        'config_identity': _hash({'path': str(path.resolve()), 'bytes': digest(raw)})}
        return config, identity

    def preview(self):
        """Read explicit configuration; do not write a journal or call the model."""
        self._guard()
        _, identity = self._configuration()
        with self.registry.transaction() as db:
            binding = json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
            budget = db.execute('SELECT ceiling,used,held FROM budget WHERE id=1').fetchone()
        _require(binding['provider'] == provider_binding(self.provider),
                 'Original provider configuration or budget binding changed')
        _require(all(type(n) is int and n >= 0 for n in budget) and budget[1]+budget[2] < budget[0],
                 'Original model budget is exhausted or reserved unknown')
        value = {'schema_version': 1, **identity, 'request_count': 1,
                 'requests_used': budget[1], 'requests_reserved_unknown': budget[2],
                 'request_budget': budget[0], 'requests_remaining': budget[0]-budget[1]-budget[2],
                 'proves': 'model_response_only', 'repair_verified': False}
        value['revision'] = _hash({'preview': value, 'binding': binding, 'session': self.session.token})
        return value

    def view(self):
        """Projection of preserved evidence only; safe for GET, no config/key reads."""
        attempts = self.registry.load('attempt')
        result = []
        for value in self.registry.load('model_connectivity').values():
            value = dict(value)
            if value['status'] == 'started':
                attempt = attempts.get(value['attempt_id'], {})
                if attempt.get('status') == 'unknown':
                    value['status'] = 'unknown'
                elif attempt.get('status') == 'finished':
                    value['status'] = 'completed'
                    value['evidence'] = attempt.get('evidence', {})
            result.append(value)
        return {'checks': result, 'proves': 'model_response_only', 'repair_verified': False}

    def execute(self, request_id, revision):
        """Called by the original serialized job, with its original cancel event."""
        _require(isinstance(request_id, str) and re.fullmatch('[0-9a-f]{32}', request_id),
                 'Invalid connectivity request identity')
        _require(isinstance(revision, str) and re.fullmatch('[0-9a-f]{64}', revision),
                 'Invalid connectivity preview revision')
        previous = self.registry.load('model_connectivity').get(request_id)
        if previous:
            _require(previous['revision'] == revision, 'Connectivity request identity reused')
            return next(v for v in self.view()['checks'] if v['id'] == request_id)
        self._guard()
        cancel = self.provider._cancel_event or threading.Event()
        if cancel.is_set():
            raise OperationStopped('Connectivity stopped before reservation')
        preview = self.preview()
        _require(preview['revision'] == revision, 'Configuration, permission, budget or session changed; preview again')
        config, identity = self._configuration()
        _require(all(preview[k] == v for k, v in identity.items()), 'Configuration changed after preview')
        attempt_id = uuid.uuid4().hex
        value = {'id': request_id, 'revision': revision, 'attempt_id': attempt_id, 'status': 'started',
                 'started_at': now(), 'model': preview['model'], 'endpoint': preview['endpoint'],
                 'config_identity': preview.get('config_identity'), 'proves': 'model_response_only',
                 'repair_verified': False}
        with self.registry.transaction() as db:
            # Intent and its ONE held request commit together, including racing duplicates.
            row = db.execute("SELECT body FROM objects WHERE kind='model_connectivity' AND id=?", (request_id,)).fetchone()
            if row:
                saved = json.loads(row[0])
                _require(saved['revision'] == revision, 'Connectivity request identity reused')
                return saved
            budget = db.execute('SELECT ceiling,used,held FROM budget WHERE id=1').fetchone()
            _require(tuple(budget) == (preview['request_budget'], preview['requests_used'],
                                     preview['requests_reserved_unknown']), 'Original budget changed; preview again')
            self.registry.put('attempt', attempt_id, {'id': attempt_id, 'status': 'started', 'reserved': 1,
                              'started_at': now(), 'purpose': 'model_connectivity', 'request_id': request_id}, db)
            self.registry.put('model_connectivity', request_id, value, db)
            db.execute('UPDATE budget SET held=held+1 WHERE id=1')
        try:
            self._guard()
            _, latest = self._configuration()
            _require(latest == identity, 'Configuration changed before transport launch')
            evidence = _invoke(self.provider, config, revision, cancel)
            _validate(evidence, revision)
            self._guard()
            _, latest = self._configuration()
            _require(latest == identity, 'Configuration changed while checking connectivity')
        except BaseException:
            evidence = {'purpose': 'model_connectivity', 'requests': None, 'consumption': 'unknown',
                        'status': 'failed', 'preview_revision': revision, 'repair_verified': False}
            self.registry.settle(attempt_id, evidence, success=False)
            value.update(status='unknown', finished_at=now(), evidence=evidence)
            self.registry.put('model_connectivity', request_id, value)
            raise
        self.registry.settle(attempt_id, evidence, success=True)
        value.update(status='completed', finished_at=now(), evidence=evidence)
        self.registry.put('model_connectivity', request_id, value)
        return value


def _validate(value, revision):
    fields = {'schema_version', 'purpose', 'status', 'pi_version', 'requests', 'requests_started',
              'consumption', 'count_scope', 'active_tools', 'tool_calls', 'response_received',
              'preview_revision', 'transport_retries', 'repair_verified', 'model_cost_verified', 'transport'}
    _require(isinstance(value, dict) and set(value) == fields and type(value.get('schema_version')) is int
             and value.get('schema_version') == 1
             and value.get('purpose') == 'model_connectivity' and value.get('status') == 'completed'
             and value.get('requests') == value.get('requests_started') == 1
             and type(value.get('requests')) is int and type(value.get('requests_started')) is int
             and value.get('consumption') == 'reported'
             and value.get('active_tools') == [] and type(value.get('tool_calls')) is int and value.get('tool_calls') == 0
             and value.get('pi_version') == '1.0.4' and value.get('preview_revision') == revision
             and value.get('response_received') is True and value.get('repair_verified') is False
             and type(value.get('transport_retries')) is int and value.get('transport_retries') == 0
             and value.get('model_cost_verified') is False and value.get('count_scope') == 'adapter_stream_invocations'
             and value.get('transport') in {'live', 'loopback-mock'},
             'Pi connectivity did not return a valid one-response receipt')


def _invoke(provider, config, revision, cancel):
    from .pi_live import communicate_live
    from .runner import stop_tree
    if cancel.is_set():
        raise OperationStopped('Connectivity stopped before transport launch')
    node = shutil.which('node')
    _require(node is not None, 'Connectivity requires the configured Pi Node runtime')
    command = [node, str(runtime_script('connectivity.mjs'))]
    if provider.mock_url:
        command += ['--mock-url', provider.mock_url]
    # Only the explicit file supplies model credentials; inherited model env is excluded.
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'APPDATA',
        'LOCALAPPDATA', 'COMSPEC', 'PATHEXT'}}
    env.update(config)
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, start_new_session=os.name != 'nt',
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
    try:
        output, _ = communicate_live(process, json.dumps({'protocol_version': 1, 'request_budget': 1,
                                    'preview_revision': revision}).encode(), 1, lambda _: None, cancel, 75)
        _require(process.returncode == 0 and len(output) <= 16384, 'Pi connectivity failed; preserve unknown reservation')
        data = json.loads(output)
        _require(data.get('protocol_version') == 1 and data.get('status') == 'completed',
                 'Pi connectivity did not complete')
        return data['evidence']
    except BaseException:
        stop_tree(process)
        raise
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
