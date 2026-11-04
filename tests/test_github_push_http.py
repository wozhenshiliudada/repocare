"""Real owned loopback sockets, fake controller, no Git/provider/model calls."""
import contextlib
from http.client import HTTPConnection
import io
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

from repo_maintainer import github_push_http as http
from repo_maintainer.models import SafetyError


class InputError(SafetyError):
    def __init__(self, code, status):
        super().__init__('PRIVATE fixture body/header/exception must not escape')
        self.code, self.http_status = code, status


class Controller:
    def __init__(self):
        self.calls = []
        self.result = (202, {'id': 'receipt', 'status': 'received',
                             'work_id': 'work', 'event_id': 'event'})
        self.error = None

    def receive(self, headers, body):
        self.calls.append((headers, body))
        if self.error:
            raise self.error
        return self.result


class PushHttpTests(unittest.TestCase):
    def setUp(self):
        self.timeout = patch.object(http, 'REQUEST_TIMEOUT_SECONDS', .3)
        self.timeout.start()
        self.addCleanup(self.timeout.stop)
        self.controller = Controller()
        self.receiver = http.PushReceiver(self.controller).start()
        self.addCleanup(self.receiver.close)

    def request(self, method='POST', path='/github/push', body=b'{}', headers=None):
        conn = HTTPConnection('127.0.0.1', self.receiver.port, timeout=2)
        try:
            conn.request(method, path, body, headers or {'Content-Type': 'application/json'})
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}, dict(response.getheaders())
        finally:
            conn.close()

    def raw(self, head, body=b'', *, end=False):
        with socket.create_connection(('127.0.0.1', self.receiver.port), timeout=2) as conn:
            conn.sendall(head + body)
            if end:
                conn.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                chunks.append(data)
        response = b''.join(chunks)
        line, _, rest = response.partition(b'\r\n')
        status = int(line.split()[1])
        _, _, payload = rest.partition(b'\r\n\r\n')
        return status, json.loads(payload), response

    def assert_error(self, actual, status, code):
        self.assertEqual(actual[0], status)
        self.assertEqual(actual[1], {'error': code, 'code': code})
        self.assertNotIn('PRIVATE', str(actual))

    def test_port_zero_idempotent_start_close_and_closed_listener(self):
        self.assertGreater(self.receiver.port, 0)
        self.assertEqual(self.receiver.url, f'http://127.0.0.1:{self.receiver.port}/github/push')
        self.assertIs(self.receiver.start(), self.receiver)
        self.receiver.close()
        self.receiver.close()
        self.assertFalse(self.receiver._thread.is_alive())
        self.assertEqual(self.receiver._server.requests, set())
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', self.receiver.port), timeout=.5)
        with self.assertRaises(SafetyError):
            self.receiver.start()

    def test_close_before_start_and_strict_configuration(self):
        receiver = http.PushReceiver(self.controller)
        receiver.close()
        receiver.close()
        for args in ({'port': True}, {'port': -1}, {'port': 65536}, {'port': 1.5},
                     {'max_body_bytes': True}, {'max_body_bytes': 0},
                     {'max_body_bytes': http.MAX_BODY_BYTES + 1}):
            with self.subTest(args=args), self.assertRaises(SafetyError):
                http.PushReceiver(self.controller, **args)
        with self.assertRaises(SafetyError):
            http.PushReceiver(object())

    def test_original_complete_body_headers_and_fixed_json_response(self):
        body = b'{ "whitespace": "original", "utf8": "\xe4\xb8\xad" }\n'
        result = self.request(body=body, headers={'Content-Type': 'application/json; charset="utf-8"',
                                                'X-GitHub-Delivery': 'original-delivery'})
        self.assertEqual(result[0], 202)
        self.assertEqual(result[1], self.controller.result[1])
        headers, received = self.controller.calls[0]
        self.assertIsInstance(headers, list)
        self.assertIn(('X-GitHub-Delivery', 'original-delivery'), headers)
        self.assertEqual(received, body)
        self.assertEqual(result[2]['Connection'], 'close')
        self.assertEqual(result[2]['Cache-Control'], 'no-store')
        self.assertNotIn('Server', result[2])

    def test_ping_controller_can_return_200(self):
        self.controller.result = (200, {'id': 'ping', 'status': 'ping', 'work_id': None, 'event_id': None})
        self.assertEqual(self.request()[:2], self.controller.result)

    def test_identity_encoding_preserves_original_uncompressed_bytes(self):
        body=b'{ "unchanged": true }'
        result=self.request(body=body,headers={'Content-Type':'application/json','Content-Encoding':'identity'})
        self.assertEqual(202,result[0])
        self.assertEqual(body,self.controller.calls[0][1])

    def test_duplicate_github_headers_reach_controller_without_collapsing(self):
        self.controller.error = InputError('invalid_push', 400)
        result = self.raw(b'POST /github/push HTTP/1.1\r\nHost: local\r\n'
                          b'Content-Length: 2\r\nContent-Type: application/json\r\n'
                          b'X-GitHub-Delivery: first\r\nx-github-delivery: second\r\n\r\n', b'{}')
        self.assert_error(result[:2], 400, 'invalid_push')
        duplicate = [(k, v) for k, v in self.controller.calls[0][0] if k.lower() == 'x-github-delivery']
        self.assertEqual(duplicate, [('X-GitHub-Delivery', 'first'), ('x-github-delivery', 'second')])

    def test_duplicate_framing_headers_reject_before_controller(self):
        for extra in (b'Content-Length: 2\r\n', b'content-type: application/json\r\n', b'Host: other\r\n'):
            with self.subTest(extra=extra):
                result = self.raw(b'POST /github/push HTTP/1.1\r\nHost: local\r\nContent-Length: 2\r\n'
                                  b'Content-Type: application/json\r\n' + extra + b'\r\n', b'{}')
                self.assert_error(result[:2], 400, 'invalid_request')
        self.assertEqual(self.controller.calls, [])

    def test_length_missing_malformed_and_default_25mib_limit(self):
        for length, status, code in ((None, 411, 'length_required'),
                                     (b'-1', 400, 'invalid_request'),
                                     (b'1, 1', 400, 'invalid_request'),
                                     (b'9' * 100, 400, 'invalid_request'),
                                     (str(http.MAX_BODY_BYTES + 1).encode(), 413, 'payload_too_large')):
            with self.subTest(length=length):
                header = b'' if length is None else b'Content-Length: ' + length + b'\r\n'
                result = self.raw(b'POST /github/push HTTP/1.1\r\nHost: local\r\n' + header +
                                  b'Content-Type: application/json\r\n\r\n')
                self.assert_error(result[:2], status, code)
        self.assertEqual(self.controller.calls, [])

    def test_configured_body_boundary_is_inclusive(self):
        self.receiver.close()
        self.receiver = http.PushReceiver(self.controller, max_body_bytes=64).start()
        self.addCleanup(self.receiver.close)
        body = b'{}' + b' ' * 62
        self.assertEqual(self.request(body=body)[0], 202)
        self.assertEqual(self.controller.calls[0][1], body)
        self.assert_error(self.request(body=body + b' ')[:2], 413, 'payload_too_large')
        self.assertEqual(len(self.controller.calls), 1)

    def test_fixed_json_content_type_no_compression_transfer_or_expect(self):
        for headers, status, code in (({'Content-Type': 'text/plain'}, 415, 'unsupported_media_type'),
                ({'Content-Type': 'application/json; charset=latin-1'}, 415, 'unsupported_media_type'),
                ({'Content-Type': 'application/json', 'Content-Encoding': 'gzip'}, 415, 'unsupported_media_type'),
                ({'Content-Type': 'application/json', 'Transfer-Encoding': 'chunked'}, 415, 'unsupported_media_type'),
                ({'Content-Type': 'application/json', 'Expect': '100-continue'}, 400, 'invalid_request')):
            with self.subTest(headers=headers):
                self.assert_error(self.request(headers=headers)[:2], status, code)
        self.assertEqual(self.controller.calls, [])

    def test_only_exact_post_route_without_query_alias_or_management(self):
        for method, path, status in (('GET', '/github/push', 405), ('HEAD', '/github/push', 405),
                ('OPTIONS', '/github/push', 405), ('PUT', '/github/push', 405),
                ('POST', '/github/push?x=1', 404), ('POST', '/github/push/', 404),
                ('POST', '//github/push', 404), ('GET', '/api/state', 404),
                ('POST', '/api/actions/stop_workbench', 404), ('GET', '/', 404)):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path)[0], status)
        self.assertEqual(self.controller.calls, [])

    def test_rejection_finishes_response_when_unsupported_body_arrives_late(self):
        with socket.create_connection(('127.0.0.1', self.receiver.port), timeout=2) as conn:
            conn.sendall(b'GET /api/state HTTP/1.1\r\nHost: local\r\nContent-Length: 2\r\n\r\n')
            response = conn.recv(4096)
            self.assertIn(b'404', response.split(b'\r\n')[0])
            # Real framing can race a rejection; late bytes must never become
            # another request, reach the controller or erase the fixed response.
            conn.sendall(b'{}')
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                response += data
        payload = response.partition(b'\r\n\r\n')[2]
        self.assertEqual(json.loads(payload), {'error': 'not_found', 'code': 'not_found'})
        self.assertEqual(self.controller.calls, [])

    def test_truncated_original_body_is_never_dispatched(self):
        result = self.raw(b'POST /github/push HTTP/1.1\r\nHost: local\r\nContent-Length: 10\r\n'
                          b'Content-Type: application/json\r\n\r\n', b'{}', end=True)
        self.assert_error(result[:2], 400, 'invalid_request')
        self.assertEqual(self.controller.calls, [])

    def test_body_and_partial_header_timeout_are_bounded_and_sanitized(self):
        for head in (b'POST /github/push HTTP/1.1\r\nHost: local\r\nContent-Length: 10\r\n'
                     b'Content-Type: application/json\r\n\r\nPRIVATE',
                     b'POST /github/push HTTP/1.1\r\nHost: PRIVATE', b'POST /github/PRIVATE'):
            with self.subTest(head=head):
                started = time.monotonic()
                result = self.raw(head)
                self.assert_error(result[:2], 408, 'request_timeout')
                self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(self.controller.calls, [])

    def test_continual_body_trickle_does_not_extend_total_deadline(self):
        with socket.create_connection(('127.0.0.1', self.receiver.port), timeout=2) as conn:
            conn.sendall(b'POST /github/push HTTP/1.1\r\nContent-Length: 100\r\n'
                         b'Content-Type: application/json\r\n\r\n')
            started = time.monotonic()
            stop = threading.Event()

            def drip():
                while not stop.wait(.07):
                    try:
                        conn.sendall(b' ')
                    except OSError:
                        return

            sender = threading.Thread(target=drip)
            sender.start()
            try:
                response = conn.recv(4096)
            finally:
                stop.set()
                sender.join(1)
            self.assertFalse(sender.is_alive())
        self.assertIn(b'408', response.split(b'\r\n')[0])
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(self.controller.calls, [])

    def test_controller_fixed_safety_codes_only_and_no_exception_reflection(self):
        for code, status in http._INPUT_ERRORS.items():
            with self.subTest(code=code):
                self.controller.error = InputError(code, status)
                self.assert_error(self.request()[:2], status, code)
        for error, status in ((InputError('PRIVATE', 403), 409),
                             (InputError('invalid_signature', 500), 409),
                             (SafetyError('PRIVATE body/path'), 409), (RuntimeError('PRIVATE'), 500)):
            self.controller.error = error
            self.assert_error(self.request()[:2], status, 'internal_error')

    def test_invalid_controller_response_or_serialization_is_generic(self):
        for result, status in (((True, {}), 500), ((202, ['PRIVATE']), 500),
                ((403, {'code': 'PRIVATE', 'error': 'PRIVATE'}), 409),
                ((202, {'bad': float('nan')}), 500), ((202, {'bad': object()}), 500)):
            self.controller.result = result
            self.assert_error(self.request()[:2], status, 'internal_error')
        self.controller.result = (403, {'code': 'invalid_signature', 'error': 'PRIVATE'})
        self.assert_error(self.request()[:2], 403, 'invalid_signature')

    def test_slow_async_business_does_not_hold_ack_or_listener_close(self):
        gate, started, finished = threading.Event(), threading.Event(), threading.Event()

        def business():
            started.set()
            gate.wait(3)
            finished.set()

        worker = threading.Thread(target=business, name='fake-push-business')
        original = self.controller.receive

        def receive(headers, body):
            receipt = original(headers, body)
            worker.start()
            return receipt

        self.controller.receive = receive
        try:
            before = time.monotonic()
            self.assertEqual(self.request()[0], 202)
            self.assertTrue(started.wait(.5))
            self.assertFalse(finished.is_set())
            self.assertLess(time.monotonic() - before, 1)
            self.receiver.close()
            self.assertFalse(finished.is_set())
        finally:
            gate.set()
            if worker.ident is not None:
                worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_close_unblocks_partial_owned_request_and_concurrent_close_is_safe(self):
        conn = socket.create_connection(('127.0.0.1', self.receiver.port), timeout=2)
        conn.sendall(b'POST /github/push HTTP/1.1\r\nContent-Length: 100\r\n'
                     b'Content-Type: application/json\r\n\r\n')
        deadline = time.monotonic() + 1
        while not self.receiver._server.requests and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.receiver._server.requests)
        closers = [threading.Thread(target=self.receiver.close) for _ in range(2)]
        try:
            for closer in closers:
                closer.start()
            for closer in closers:
                closer.join(2)
                self.assertFalse(closer.is_alive())
        finally:
            conn.close()
        self.assertFalse(self.receiver._thread.is_alive())
        self.assertEqual(self.receiver._server.requests, set())
        self.assertEqual(self.controller.calls, [])

    def test_malformed_requests_and_errors_never_log_private_material(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.raw(b'PRIVATE invalid request line\r\n\r\n', end=True)
            self.controller.error = RuntimeError('PRIVATE exception')
            self.request(headers={'Content-Type': 'application/json', 'X-Private': 'PRIVATE header'})
            self.receiver.close()
        self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
