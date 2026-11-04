"""R4 controller-owned read-only investigation bridge for Pi/Native.

The bridge is a per-turn loopback endpoint in this controller process. It grants
no writes, model requests, tests, approvals or new accounting: every call still
goes through the original TaskInvestigation journal and its persisted counters.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import re
import secrets
import threading

from .git_investigation import validate_roots
from .models import SafetyError, safe_context_path

INVESTIGATION_PURPOSE = 'repair-investigation-v1'
INVESTIGATION_TOOLS = ('search_approved_sources', 'read_approved_source')
DENIED = 'denied'
BUDGET_EXHAUSTED = 'budget_exhausted'
UNAVAILABLE = 'unavailable'
MAX_BODY_BYTES = 65536
MAX_RESULT_BYTES = 1200000
TOKEN = re.compile(r'[a-f0-9]{64}')
BRIDGE_PATH = '/v1/tool'
OPERATIONS = {'search_approved_sources': 'search_sources',
              'read_approved_source': 'read_source'}


def _require(condition, message):
    if not condition:
        raise SafetyError(message)


def validate_config(config):
    _require(isinstance(config, dict) and set(config) ==
             {'read_roots', 'max_calls', 'max_output_bytes', 'ttl_seconds'}, 'Invalid investigation config')
    roots = validate_roots(config['read_roots'])
    _require(1 <= len(roots) <= 20, 'Investigation needs an explicit read root')
    _require(type(config['max_calls']) is int and 1 <= config['max_calls'] <= 500
             and type(config['max_output_bytes']) is int and 1024 <= config['max_output_bytes'] <= 1048576,
             'Invalid investigation tool bounds')
    _require(type(config['ttl_seconds']) is int and 60 <= config['ttl_seconds'] <= 86400,
             'Invalid investigation scope lifetime')
    return roots


def investigation_context(context, config):
    """Build the only Native context that may carry investigation tools."""
    roots = validate_config(config)
    _require(isinstance(context, dict) and set(context) - {'thinking_level'} == {'provider', 'model', 'paths'}
             and isinstance(context.get('provider'), str) and isinstance(context.get('model'), str)
             and isinstance(context.get('paths'), list) and context['paths'],
             'Invalid base Pi task context')
    for path in context['paths']:
        safe_context_path(path)
    return {'schema_version': 2, 'purpose': INVESTIGATION_PURPOSE,
            'provider': context['provider'], 'model': context['model'],
            'paths': list(context['paths']),
            **({'thinking_level': context['thinking_level']} if 'thinking_level' in context else {}),
            'investigation': {'schema_version': 1, 'read_roots': list(roots),
                              'max_calls': config['max_calls'],
                              'max_output_bytes': config['max_output_bytes']}}


def validate_investigation_context(context):
    """Native-side shape check used before any session file is opened."""
    _require(isinstance(context, dict) and set(context) - {'thinking_level'} ==
             {'schema_version', 'purpose', 'provider', 'model', 'paths', 'investigation'}
             and type(context['schema_version']) is int and context['schema_version'] == 2
             and context['purpose'] == INVESTIGATION_PURPOSE,
             'Invalid investigation Pi task context')
    _require(isinstance(context['provider'], str) and isinstance(context['model'], str)
             and isinstance(context['paths'], list) and context['paths'],
             'Invalid investigation provider or paths')
    investigation = context['investigation']
    _require(isinstance(investigation, dict) and set(investigation) ==
             {'schema_version', 'read_roots', 'max_calls', 'max_output_bytes'}
             and type(investigation['schema_version']) is int and investigation['schema_version'] == 1,
             'Invalid investigation context binding')
    roots = validate_roots(investigation['read_roots'])
    _require(1 <= len(roots) <= 20 and type(investigation['max_calls']) is int
             and 1 <= investigation['max_calls'] <= 500
             and type(investigation['max_output_bytes']) is int
             and 1024 <= investigation['max_output_bytes'] <= 1048576,
             'Invalid investigation context bounds')
    return context


class InvestigationBridge:
    """Short-lived loopback tool handler; the broker remains the only authority."""

    def __init__(self, broker, *, scope_sha256, base_sha, read_roots):
        _require(isinstance(scope_sha256, str) and re.fullmatch(r'[a-f0-9]{64}', scope_sha256),
                 'Invalid investigation scope receipt')
        _require(isinstance(base_sha, str) and re.fullmatch(r'[a-f0-9]{40}', base_sha),
                 'Invalid investigation baseline')
        roots = validate_roots(read_roots)
        _require(roots, 'Investigation bridge needs read roots')
        self.broker, self.scope_sha256, self.base_sha = broker, scope_sha256, base_sha
        self.read_roots = roots
        self.token = secrets.token_hex(32)
        self._lock = threading.Lock()
        self._closed = False
        self._server = _Server(('127.0.0.1', 0), _Handler)
        self._server.bridge = self
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True,
                                        name='pi-investigation-bridge')
        self._thread.start()

    @property
    def url(self):
        host, port = self._server.server_address[:2]
        return f'http://{host}:{port}'

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)

    def call(self, payload):
        _require(isinstance(payload, dict) and set(payload) == {'tool', 'args'},
                 'Invalid investigation bridge request')
        tool, args = payload['tool'], payload['args']
        _require(tool in OPERATIONS and isinstance(args, dict), 'Invalid investigation tool')
        if tool == 'search_approved_sources':
            _require(set(args) <= {'query', 'max_hits'} and set(args) >= {'query'}, 'Invalid search arguments')
            query = args['query']
            _require(isinstance(query, str) and '\x00' not in query
                     and 1 <= len(query.encode('utf-8')) <= 512, 'Invalid search query')
            max_hits = args.get('max_hits', 20)
            _require(type(max_hits) is int and 1 <= max_hits <= 100, 'Invalid search hit bound')
            call = lambda: self.broker.search_sources(query, max_hits=max_hits)
        else:
            _require(set(args) <= {'path', 'start_line', 'max_lines'} and set(args) >= {'path'},
                     'Invalid read arguments')
            path = args['path']
            _require(isinstance(path, str) and 0 < len(path) <= 4096 and '\x00' not in path,
                     'Invalid investigation path')
            start_line = args.get('start_line', 1)
            max_lines = args.get('max_lines', 80)
            _require(type(start_line) is int and start_line >= 1
                     and type(max_lines) is int and 1 <= max_lines <= 200,
                     'Invalid investigation line window')
            call = lambda: self.broker.read_source(path, start_line=start_line, max_lines=max_lines)
        try:
            with self._lock:
                result = call()
        except SafetyError as error:
            # Fixed codes only: no path, file text, exception text or secret leaves
            # this controller boundary.
            if 'budget' in str(error).casefold():
                raise BridgeError(BUDGET_EXHAUSTED) from None
            raise BridgeError(DENIED) from None
        _require(isinstance(result, dict) and result.get('scope_sha256') == self.scope_sha256
                 and result.get('base_sha') == self.base_sha
                 and result.get('runtime_authorized') is False
                 and result.get('write_authorized') is False
                 and result.get('operation') == OPERATIONS[tool],
                 'Investigation result escaped its original scope')
        return result


class BridgeError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, *_args):
        return

    def _send(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._send(405, {'error': 'method_not_allowed'})

    def do_POST(self):
        bridge = getattr(self.server, 'bridge', None)
        if bridge is None or self.path != BRIDGE_PATH:
            self._send(404, {'error': 'not_found'})
            return
        authorization = self.headers.get('Authorization', '')
        if not hmac.compare_digest(authorization, 'Bearer ' + bridge.token):
            self._send(401, {'error': 'unauthorized'})
            return
        if self.headers.get('Transfer-Encoding'):
            self._send(400, {'error': 'invalid_request'})
            return
        try:
            length = int(self.headers.get('Content-Length', ''))
        except (TypeError, ValueError):
            self._send(400, {'error': 'invalid_request'})
            return
        if not 1 <= length <= MAX_BODY_BYTES:
            self._send(413, {'error': 'invalid_request'})
            return
        raw = self.rfile.read(length)
        if len(raw) != length:
            self._send(400, {'error': 'invalid_request'})
            return
        try:
            payload = json.loads(raw.decode('utf-8'))
        except (UnicodeError, ValueError):
            self._send(400, {'error': 'invalid_request'})
            return
        try:
            result = bridge.call(payload)
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(encoded) > MAX_RESULT_BYTES:
                raise ValueError('Investigation result exceeds bridge bound')
        except BridgeError as error:
            status = 409 if error.code == BUDGET_EXHAUSTED else 403
            self._send(status, {'error': error.code})
        except Exception:
            # Never echo exception text, paths or file content.
            self._send(500, {'error': UNAVAILABLE})
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(encoded)
