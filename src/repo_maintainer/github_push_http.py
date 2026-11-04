"""A receive-only loopback HTTP boundary for the original push controller."""
import json
import io
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .models import SafetyError


MAX_BODY_BYTES = 25 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 5.0
MAX_HEADER_BYTES = 64 * 1024
_INPUT_ERRORS = {
    'invalid_push': 400, 'invalid_target': 400, 'invalid_signature': 403,
    'delivery_conflict': 409, 'inbox_full': 503, 'unsupported_event': 422,
    'payload_too_large': 413, 'unsupported_media_type': 415,
    'receiver_unavailable': 503,
}
_HTTP_ERRORS = {
    400: 'invalid_request', 404: 'not_found', 405: 'method_not_allowed',
    408: 'request_timeout', 411: 'length_required', 413: 'payload_too_large',
    415: 'unsupported_media_type', 431: 'invalid_request',
    500: 'internal_error', 503: 'receiver_unavailable',
}


def _failure(status, code):
    # Public errors are fixed codes, never exception text or request material.
    return status, {'error': code, 'code': code}


def _controller_result(controller, headers, body):
    try:
        status, result = controller.receive(headers, body)
        if type(status) is not int or not isinstance(result, dict):
            return _failure(500, 'internal_error')
        if status in (200, 202):
            return status, result
        code = result.get('code')
        if isinstance(code, str) and _INPUT_ERRORS.get(code) == status:
            return _failure(status, code)
        return _failure(409, 'internal_error')
    except SafetyError as error:
        code, status = getattr(error, 'code', None), getattr(error, 'http_status', None)
        if isinstance(code, str) and type(status) is int and _INPUT_ERRORS.get(code) == status:
            return _failure(status, code)
        return _failure(409, 'internal_error')
    except Exception:
        return _failure(500, 'internal_error')


class _PushServer(ThreadingHTTPServer):
    # close() wakes reads, then joins these owned handlers instead of abandoning
    # daemon request threads. Controller.receive must only persist/enqueue work.
    daemon_threads = False
    allow_reuse_address = False

    def __init__(self, controller, port, max_body_bytes):
        self.controller = controller
        self.max_body_bytes = max_body_bytes
        self.requests_lock = threading.Lock()
        self.requests = set()
        self.stopping = threading.Event()
        super().__init__(('127.0.0.1', port), _PushHandler)

    def process_request(self, request, client_address):
        with self.requests_lock:
            self.requests.add(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self.requests_lock:
                self.requests.discard(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.requests_lock:
                self.requests.discard(request)

    def shutdown_request(self, request):
        # Publish the complete response/EOF before closing unread input. On
        # Windows an immediate full close can reset an already-written rejection
        # when the client's unsupported body arrives just after its headers.
        try:
            request.shutdown(socket.SHUT_WR)
            until, remaining = time.monotonic() + .1, MAX_HEADER_BYTES
            request.settimeout(.02)
            while remaining and time.monotonic() < until and not self.stopping.is_set():
                try:
                    chunk = request.recv(min(4096, remaining))
                except TimeoutError:
                    continue
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        finally:
            self.close_request(request)

    def handle_error(self, request, client_address):
        # The stdlib traceback may include request/controller exception details.
        pass


class _DeadlineReader(io.RawIOBase):
    """One total read deadline, including headers, not an idle-only timeout."""

    def __init__(self, connection, stopping):
        super().__init__()
        self.connection, self.stopping = connection, stopping
        self.deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
        self.timed_out = False

    def readable(self):
        return True

    def readinto(self, buffer):
        while True:
            if self.stopping.is_set():
                raise ConnectionAbortedError('Receiver stopping')
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                self.timed_out = True
                raise TimeoutError('Request deadline')
            # Polling also unblocks close on Windows, where another thread's
            # shutdown does not reliably interrupt a buffered blocking read.
            self.connection.settimeout(min(.1, remaining))
            try:
                return self.connection.recv_into(buffer)
            except TimeoutError:
                continue


class _PushHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def setup(self):
        super().setup()
        self.rfile.close()
        self._reader = _DeadlineReader(self.connection, self.server.stopping)
        self.rfile = io.BufferedReader(self._reader)

    def log_message(self, format, *args):
        pass

    def handle(self):
        self.close_connection = True
        self._replied = False
        self.request_version = 'HTTP/1.0'
        self.command = None
        try:
            self.handle_one_request()
            if self._reader.timed_out and not self._replied:
                self._respond(*_failure(408, 'request_timeout'))
        except (OSError, TimeoutError):
            if not self._replied:
                status = 503 if self.server.stopping.is_set() else 408 if self._reader.timed_out else 400
                self._respond(*_failure(status, _HTTP_ERRORS[status]))

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler also uses this for malformed request lines and
        # header limits. Its original message can contain raw request material.
        if self._reader.timed_out:
            code = 408
        if code == 501:
            code = 405
        self._respond(*_failure(code if code in _HTTP_ERRORS else 400,
                               _HTTP_ERRORS.get(code, 'invalid_request')))

    def _respond(self, status, result):
        if self._replied:
            return
        try:
            data = json.dumps(result, ensure_ascii=True, allow_nan=False,
                              separators=(',', ':')).encode('utf-8')
        except Exception:
            status, result = _failure(500, 'internal_error')
            data = json.dumps(result).encode('utf-8')
        self._replied = True
        self.close_connection = True
        try:
            self.request_version = 'HTTP/1.0'
            self.send_response_only(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)
        except OSError:
            pass

    def _exact_path(self):
        # The stdlib normalizes a leading //; do not accept that alias either.
        parts = self.requestline.split()
        return len(parts) == 3 and parts[1] == '/github/push'

    def _reject_method(self):
        status = 405 if self._exact_path() else 404
        self._respond(*_failure(status, _HTTP_ERRORS[status]))

    do_GET = do_HEAD = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _reject_method

    def do_POST(self):
        if not self._exact_path():
            self._respond(*_failure(404, 'not_found'))
            return
        headers = list(self.headers.raw_items())
        if (sum(len(name) + len(value) + 4 for name, value in headers) > MAX_HEADER_BYTES
                or any('\r' in value or '\n' in value for _, value in headers)):
            self._respond(*_failure(400, 'invalid_request'))
            return
        framing = {}
        for name, value in headers:
            key = name.lower()
            if key in {'content-length', 'content-type', 'content-encoding', 'transfer-encoding', 'host', 'expect'}:
                if key in framing:
                    self._respond(*_failure(400, 'invalid_request'))
                    return
                framing[key] = value
        if 'transfer-encoding' in framing:
            self._respond(*_failure(415, 'unsupported_media_type'))
            return
        if 'expect' in framing:
            self._respond(*_failure(400, 'invalid_request'))
            return
        if framing.get('content-encoding','identity').strip().casefold() != 'identity':
            self._respond(*_failure(415, 'unsupported_media_type'))
            return
        length = framing.get('content-length')
        if length is None:
            self._respond(*_failure(411, 'length_required'))
            return
        if not re.fullmatch(r'[0-9]{1,20}', length):
            self._respond(*_failure(400, 'invalid_request'))
            return
        length = int(length)
        if length > self.server.max_body_bytes:
            self._respond(*_failure(413, 'payload_too_large'))
            return
        content_type = framing.get('content-type', '').strip()
        if not re.fullmatch(r'application/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?',
                            content_type, re.IGNORECASE):
            self._respond(*_failure(415, 'unsupported_media_type'))
            return
        body = self.rfile.read(length)
        if time.monotonic() >= self._reader.deadline or len(body) != length:
            status = 408 if time.monotonic() >= self._reader.deadline else 400
            self._respond(*_failure(status, _HTTP_ERRORS[status]))
            return
        # No timeout/replay of a persisted controller decision. Slow business
        # belongs to its original asynchronous queue, outside this listener.
        if self.server.stopping.is_set():
            self._respond(*_failure(503, 'receiver_unavailable'))
            return
        self._respond(*_controller_result(self.server.controller, headers, body))


class PushReceiver:
    """Own one receive-only listener; no registry, executor or model lifecycle."""

    def __init__(self, controller, port=0, *, max_body_bytes=MAX_BODY_BYTES):
        if (type(port) is not int or not 0 <= port <= 65535
                or type(max_body_bytes) is not int or not 1 <= max_body_bytes <= MAX_BODY_BYTES
                or not callable(getattr(controller, 'receive', None))):
            raise SafetyError('Invalid push receiver configuration')
        self.controller = controller
        self.port = port
        self.max_body_bytes = max_body_bytes
        self._server = None
        self._thread = None
        self._closed = False
        self._lock = threading.Lock()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}/github/push'

    def start(self):
        with self._lock:
            if self._closed:
                raise SafetyError('Push receiver is closed')
            if self._server is None:
                server = _PushServer(self.controller, self.port, self.max_body_bytes)
                thread = threading.Thread(target=server.serve_forever,
                                          kwargs={'poll_interval': .05},
                                          name=f'repocare-github-push-{server.server_port}')
                try:
                    thread.start()
                except BaseException:
                    server.server_close()
                    raise
                self.port = server.server_port
                self._server, self._thread = server, thread
        return self

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._server is None:
                return
            server = self._server
            server.stopping.set()
            server.shutdown()
            with server.requests_lock:
                requests = list(server.requests)
            for request in requests:
                try:
                    request.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            server.server_close()
            self._thread.join()
