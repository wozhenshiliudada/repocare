"""Bounded stream and text tests, without network or a real download policy."""
import hashlib
import io
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from repo_maintainer.ci_evidence import project_log, CiEvidenceReader
from repo_maintainer.ci_transport import (CiJsonPage, CiLimits, CiLogCapture,
                                         CiReadBudget, CiReadError, capture_prefix, decode_json)


class CiTransportTests(unittest.TestCase):
    def setUp(self):
        self.limits = CiLimits()
        self.job = {'id': 201, 'source_url': 'https://github.com/example/repo/actions/runs/101/job/201'}

    def project(self, raw, **kwargs):
        return project_log(CiLogCapture(raw, False, len(raw)), self.job, self.limits, **kwargs)

    def test_capture_reads_one_sentinel_at_most(self):
        class Stream(io.BytesIO):
            def read(self, n):
                self.maximum_requested = max(getattr(self, 'maximum_requested', 0), n)
                return super().read(n)
        for size in [0, 3, 8, 9, 100000]:
            with self.subTest(size=size):
                stream = Stream(b'x' * size)
                result = capture_prefix(stream, 8, CiReadBudget(self.limits))
                self.assertEqual(result.raw, b'x' * min(8, size))
                self.assertEqual(result.truncated, size > 8)
                self.assertLessEqual(stream.tell(), 9)
                self.assertLessEqual(stream.maximum_requested, 9)
                self.assertFalse(stream.closed)
                self.assertEqual(result.remote_total_bytes, size if size <= 8 else None)

    def test_partial_stream_reads_until_eof_or_bound(self):
        class OneByte(io.BytesIO):
            def read(self, n):
                return super().read(min(1, n))
        result = capture_prefix(OneByte(b'1234567890'), 8, CiReadBudget(self.limits))
        self.assertEqual(result.raw, b'12345678')
        self.assertTrue(result.truncated)

    def test_stream_oversize_nonbytes_or_unknown_read_are_rejected(self):
        class BadStream:
            def __init__(self, output):
                self.output = output
            def read(self, n):
                return self.output
        for value in [None, 'text', b'x' * 20]:
            with self.subTest(value=value), self.assertRaises(CiReadError) as caught:
                capture_prefix(BadStream(value), 8, CiReadBudget(self.limits))
            self.assertEqual(caught.exception.code, 'invalid_response')

    def test_deadline_is_checked_after_blocking_read(self):
        now = [0.0]
        budget = CiReadBudget(self.limits, lambda: now[0])
        class Delayed(io.BytesIO):
            def read(self, n):
                now[0] += 61
                return super().read(n)
        with self.assertRaises(CiReadError) as caught:
            capture_prefix(Delayed(b'abc'), 8, budget)
        self.assertEqual(caught.exception.code, 'timeout')

    def test_remaining_timeout_and_request_limit_cannot_be_reset(self):
        now = [0.0]
        budget = CiReadBudget(replace(self.limits, http_requests=2), lambda: now[0])
        budget.begin_request()
        budget.begin_request()
        with self.assertRaises(CiReadError) as caught:
            budget.begin_request()
        self.assertEqual(caught.exception.code, 'request_limit')
        now[0] = 59
        self.assertEqual(budget.timeout(), 1)
        now[0] = 60
        with self.assertRaises(CiReadError) as caught:
            budget.remaining()
        self.assertEqual(caught.exception.code, 'timeout')

    def test_deadline_of_reader_checks_transport_return_without_new_calls(self):
        now, calls = [0.0], []
        class DelayedTransport:
            def get_json(self, path, budget):
                calls.append(path)
                now[0] += 61
                return CiJsonPage({'id': 901, 'full_name': 'example/repo'})
        reader = CiEvidenceReader(DelayedTransport(), synthetic=True, clock=lambda: now[0])
        result = reader.observe('https://github.com/example/repo', 101, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['evidence_status'], 'blocked')
        self.assertEqual(result['errors'][0]['code'], 'timeout')

    def test_json_rejects_oversize_duplicate_keys_and_nonfinite_data(self):
        for raw, code in [(b'x' * 30, 'size_limit'), (b'{"id":1,"id":2}', 'invalid_response'),
                          (b'{"id":NaN}', 'invalid_response'), (b'{"id":1e999}', 'invalid_response'),
                          (b'\xff', 'invalid_response'), (b'{"id":"\\ud800"}', 'invalid_response'),
                          (b'<html>error</html>', 'invalid_response')]:
            with self.subTest(raw=raw), self.assertRaises(CiReadError) as caught:
                decode_json(raw, replace(self.limits, json_response_bytes=24))
            self.assertEqual(caught.exception.code, code)

    def test_valid_json_has_no_hidden_body_mutation(self):
        raw = b'{"total_count":0,"jobs":[]}'
        self.assertEqual(decode_json(raw, self.limits), {'total_count': 0, 'jobs': []})

    def test_digest_is_original_captured_bytes_not_sanitized_excerpt(self):
        raw = b'\x1b[31mBearer fixture_secret\x1b[0m\n'
        log = self.project(raw)
        self.assertEqual(log['captured_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(log['captured_sha256'], hashlib.sha256(log['excerpt'].encode()).hexdigest())
        self.assertEqual(log['captured_bytes'], len(raw))
        self.assertEqual(log['hash_scope'], 'captured_raw_bytes_before_decode_redaction')

    def test_ansi_osc_dcs_bidi_and_null_are_not_rendered(self):
        raw = ('\x1b[31mFAIL\x1b[0m\x1b]8;;https://evil.invalid\x07link\x1b]8;;\x07'
               '\x1bPsecret_payload\x1b\\\x00\u202e\n').encode()
        log = self.project(raw)
        self.assertEqual(log['excerpt'], 'FAILlink\n')
        self.assertTrue(log['controls_removed'])

    def test_unterminated_control_string_does_not_reveal_its_payload(self):
        for prefix in ['\x1b]', '\x1bP', '\x1b_', '\x9d']:
            with self.subTest(prefix=repr(prefix)):
                log = self.project(('FAIL\n' + prefix + 'hidden_payload').encode())
                self.assertEqual(log['excerpt'], 'FAIL\n')

    def test_known_secret_token_assignment_and_signed_url_are_redacted(self):
        raw = ('private_value\nBearer abc123\nghp_fixturevalue\ngithub_pat_fixturevalue\n'
               'password="do not share"\napi_key=fixturevalue\n'
               'https://storage.invalid/log?sig=fixturevalue&token=abc\n').encode()
        log = self.project(raw, known_secrets=('private_value',))
        for value in ['private_value', 'abc123', 'ghp_fixturevalue', 'github_pat_fixturevalue',
                      'do not share', 'fixturevalue', 'storage.invalid', 'sig=']:
            self.assertNotIn(value, log['excerpt'])
        self.assertTrue(log['redacted'])

    def test_truncation_drops_partial_last_line_and_secret_prefix(self):
        limits = replace(self.limits, log_bytes=15)
        captured = capture_prefix(io.BytesIO(b'FAIL\nprivate_secret_value'), 15, CiReadBudget(limits))
        log = project_log(captured, self.job, limits, ('private_secret_value',))
        self.assertEqual(log['excerpt'], 'FAIL')
        self.assertTrue(log['tail_line_omitted'] and log['excerpt_truncated'])
        self.assertEqual(log['captured_bytes'], 15)

    def test_invalid_utf8_replacement_does_not_confuse_literal_replacement_char(self):
        self.assertTrue(self.project(b'\xff')['decoding_replacement'])
        self.assertFalse(self.project('\ufffd'.encode())['decoding_replacement'])

    def test_lying_total_and_oversized_capture_are_rejected(self):
        for capture in [CiLogCapture(b'abc', False, 1), CiLogCapture(b'abc', False, 4),
                        CiLogCapture(b'abc', True, 3), CiLogCapture(b'abc', False, True),
                        CiLogCapture(b'a' * 9, False, 9)]:
            with self.subTest(capture=capture), self.assertRaises(CiReadError):
                project_log(capture, self.job, replace(self.limits, log_bytes=8))

    def test_internal_limits_cannot_be_expanded_from_query_values(self):
        for changes in [{'max_pages': True}, {'max_pages': 0}, {'max_pages': 6},
                        {'log_bytes': 2 * 1024 * 1024}, {'response_bytes': 200}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.limits, **changes)

    def test_error_records_are_fixed_even_for_unrecognized_error_code(self):
        record = CiReadError('Bearer private_value').record('log')
        self.assertEqual(record['code'], 'network_error')
        self.assertNotIn('private_value', json.dumps(record))


if __name__ == '__main__':
    unittest.main()
