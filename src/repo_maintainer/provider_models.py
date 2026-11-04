"""One explicit bounded model-list request, with no model invocation or persistence."""
from __future__ import annotations

import http.client
import json
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from .models import SafetyError
from .assistant_secrets import contains_credential
from .provider_config import validate_provider_config
from .runtime_limits import MAX_SAFE_TOKEN_COUNT

MAX_MODEL_RESPONSE_BYTES = 1048576
MAX_MODELS = 1000
MODEL_DISCOVERY_TIMEOUT_SECONDS = 10
_ERROR = '无法获取模型目录。请检查地址、密钥与服务商是否支持 /models；未调用模型。'


def _label(value, limit=160):
    return (isinstance(value, str) and 0 < len(value) <= limit and value == value.strip()
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _pairs(items):
    result = {}
    for name, value in items:
        if name in result:
            raise ValueError('Duplicate model metadata')
        result[name] = value
    return result


def _models(data):
    if not isinstance(data, dict) or not isinstance(data.get('data'), list) or len(data['data']) > MAX_MODELS:
        raise ValueError('Invalid model directory')
    models, seen = [], set()
    for item in data['data']:
        if (not isinstance(item, dict) or not _label(item.get('id'), 128)
                or any(c.isspace() for c in item['id']) or item['id'] in seen):
            raise ValueError('Invalid model identifier')
        seen.add(item['id'])
        name = item.get('name')
        row = {'id': item['id'], 'name': name if _label(name) else item['id'],
               'context_window_tokens': None, 'max_output_tokens': None, 'reasoning_efforts': []}
        for source, target, minimum in (('context_window', 'context_window_tokens', 4096),
                                        ('max_output_tokens', 'max_output_tokens', 1)):
            value = item.get(source)
            if type(value) is int and minimum <= value <= MAX_SAFE_TOKEN_COUNT:
                row[target] = value
        effort = item.get('effort')
        levels = effort.get('supported_levels') if isinstance(effort, dict) else None
        if (isinstance(levels, list) and len(levels) <= 32
                and all(_label(level, 32) and not any(c.isspace() for c in level) for level in levels)
                and len(set(levels)) == len(levels)):
            row['reasoning_efforts'] = levels
        models.append(row)
    return models


def discover_provider_models(value):
    """GET exactly the configured HTTPS base path plus /models; never follow redirects.

    Credentials are held only for this request. Provider bodies and exception
    strings are never propagated, persisted, logged, or used as a follow-up URL.
    """
    if not isinstance(value, dict) or set(value) != {'base_url', 'api_key'}:
        raise SafetyError(_ERROR)
    connection = timer = response = request_socket = None
    try:
        config = validate_provider_config({'PI_MAINTENANCE_BASE_URL': value['base_url'],
            'PI_MAINTENANCE_API_KEY': value['api_key'], 'PI_MAINTENANCE_MODEL': 'directory-only'})
        base = urlsplit(config['PI_MAINTENANCE_BASE_URL'])
        # Preserve a configured /v1 or relay prefix, but never accept a response URL.
        target = base.path.rstrip('/') + '/models'
        models_url = urlunsplit((base.scheme, base.netloc, target, '', ''))
        deadline = time.monotonic() + MODEL_DISCOVERY_TIMEOUT_SECONDS
        connection = http.client.HTTPSConnection(base.hostname, port=base.port,
            timeout=MODEL_DISCOVERY_TIMEOUT_SECONDS, context=ssl.create_default_context())
        def expire():
            # A socket read timeout alone can be renewed by trickled headers.
            # Abort the same socket at the overall deadline as well.
            try:
                transport = request_socket if request_socket is not None else connection.sock
                if transport is not None:
                    transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        timer = threading.Timer(max(0, deadline - time.monotonic()), expire)
        timer.daemon = True
        timer.start()
        connection.request('GET', target, headers={'Authorization': 'Bearer ' + config['PI_MAINTENANCE_API_KEY'],
            'Accept': 'application/json', 'Accept-Encoding': 'identity'})
        # getresponse() may detach self.sock for HTTP/1.0 or Connection: close.
        # The response still reads through that exact socket, so retain it before
        # the handoff for both the watchdog and every remaining-time update.
        request_socket = connection.sock
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Model directory deadline')
        if request_socket is not None:
            request_socket.settimeout(remaining)
        response = connection.getresponse()
        if response.status != 200 or response.getheader('Content-Encoding', 'identity').lower() != 'identity':
            raise ValueError('Model list unavailable')
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdecimal() or int(length) > MAX_MODEL_RESPONSE_BYTES):
            raise ValueError('Model directory too large')
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Model directory deadline')
            if request_socket is not None:
                request_socket.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_MODEL_RESPONSE_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_MODEL_RESPONSE_BYTES:
                raise ValueError('Model directory too large')
            chunks.append(chunk)
        payload = b''.join(chunks).decode('utf-8')
        # Even an accidental credential echo in a display label must not reach the browser.
        if config['PI_MAINTENANCE_API_KEY'] in payload:
            raise ValueError('Credential echo')
        decoded = json.loads(payload, object_pairs_hook=_pairs)
        if contains_credential(decoded, config['PI_MAINTENANCE_API_KEY']):
            raise ValueError('Credential echo')
        models = _models(decoded)
        if time.monotonic() > deadline:
            raise TimeoutError('Model directory deadline')
        return {'models': models, 'models_url': models_url}
    except (SafetyError, ValueError, TypeError, OSError, http.client.HTTPException, RecursionError):
        raise SafetyError(_ERROR) from None
    finally:
        if timer is not None:
            timer.cancel()
        if response is not None:
            try:
                response.close()
            except OSError:
                pass
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
