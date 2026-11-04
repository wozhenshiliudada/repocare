"""Synthetic HTTP boundary checks, never using GCM, sockets or live GitHub."""
import io
import json
import sys
import unittest
from unittest.mock import patch
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from repo_maintainer.ci_evidence import CiEvidenceReader
from repo_maintainer.ci_http import CiDownloadPolicy, CiHttpTransport
from repo_maintainer.ci_transport import CiLimits, CiReadBudget, CiReadError


HOST = 'fixture.blob.core.windows.net'
SIGNED = 'https://' + HOST + '/fixture/log?sig=not-a-real-signature'
PATH = '/repos/example/repo/actions/jobs/201/logs'


class Response(io.BytesIO):
    def __init__(self, status=200, body=b'{}', headers=()):
        super().__init__(body)
        self.status, self.headers = status, headers

    def read1(self, n):
        return super().read(n)

    def getheaders(self):
        return self.headers


class Factory:
    def __init__(self, responses):
        self.responses, self.calls, self.closed, self.timeouts = list(responses), [], 0, []

    def __call__(self, host, timeout):
        factory = self
        self.timeouts.append(timeout)
        class Connection:
            sock = SimpleNamespace(settimeout=lambda value: factory.timeouts.append(value))
            def request(self, method, path, headers):
                factory.calls.append((host, method, path, dict(headers)))
            def getresponse(self):
                value = factory.responses.pop(0)
                if isinstance(value, Exception):
                    raise value
                return value
            def close(self):
                factory.closed += 1
        return Connection()


class CiHttpTests(unittest.TestCase):
    def adapter(self, responses, hosts=(HOST,), limits=None, clock=None):
        self.credential_calls = 0
        def credential(*, timeout):
            self.credential_calls += 1
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 20)
            return 'opaque_fixture_credential'
        self.factory = Factory(responses)
        adapter = CiHttpTransport(SimpleNamespace(credential=credential),
                                  policy=CiDownloadPolicy(hosts), https_factory=self.factory)
        budget = CiReadBudget(limits or CiLimits(), **({'clock': clock} if clock else {}))
        budget.begin_request()
        return adapter, budget

    def test_log_uses_fresh_unauthenticated_download_and_charges_redirect(self):
        adapter, budget = self.adapter([Response(302, headers=[('Location', SIGNED)]),
                                       Response(body=b'FAIL fixture\n')])
        captured = adapter.get_log(PATH, budget)
        self.assertEqual(captured.raw, b'FAIL fixture\n')
        self.assertEqual(budget.requests, 2)
        api, download = self.factory.calls
        self.assertEqual(api[0], 'api.github.com')
        self.assertEqual(api[3]['Authorization'], 'Bearer opaque_fixture_credential')
        self.assertEqual(download[0], HOST)
        self.assertNotIn('Authorization', download[3])
        self.assertNotIn('Cookie', download[3])
        self.assertEqual([call[1] for call in self.factory.calls], ['GET', 'GET'])
        self.assertEqual(self.factory.closed, 2)
        self.assertNotIn('opaque_fixture_credential', repr(captured))

    def test_default_empty_policy_does_not_even_read_credentials(self):
        adapter, budget = self.adapter([], hosts=())
        with self.assertRaises(CiReadError) as caught:
            adapter.get_log(PATH, budget)
        self.assertEqual(caught.exception.code, 'redirect_rejected')
        self.assertEqual(self.credential_calls, 0)
        self.assertEqual(self.factory.calls, [])

    def test_unapproved_redirects_never_receive_any_request(self):
        for url in ['http://' + HOST + '/log?sig=x', SIGNED + '#fragment',
                    'https://user:pass@' + HOST + '/log?sig=x',
                    'https://' + HOST + ':444/log?sig=x',
                    'https://' + HOST + '.evil.invalid/log?sig=x',
                    'https://127.0.0.1/log?sig=x', 'https://another.blob.core.windows.net/log?sig=x',
                    'https://' + HOST + '/no-signature', SIGNED + '\nInjected: bad']:
            with self.subTest(url=url):
                adapter, budget = self.adapter([Response(302, headers=[('Location', url)])])
                with self.assertRaises(CiReadError) as caught:
                    adapter.get_log(PATH, budget)
                self.assertEqual(caught.exception.code, 'redirect_rejected')
                self.assertEqual(len(self.factory.calls), 1)

    def test_second_redirect_is_rejected_without_third_request(self):
        adapter, budget = self.adapter([Response(302, headers=[('Location', SIGNED)]),
                                       Response(302, headers=[('Location', 'https://evil.invalid/?sig=x')])])
        with self.assertRaises(CiReadError) as caught:
            adapter.get_log(PATH, budget)
        self.assertEqual(caught.exception.code, 'redirect_rejected')
        self.assertEqual(len(self.factory.calls), 2)

    def test_unrelated_api_paths_fail_before_authentication(self):
        for path in ['/user', '/repos/example/repo/issues', '//evil.invalid/',
                     '/repos/example/repo/actions/jobs/201/rerun',
                     '/repos/example/repo/actions/runs/101/attempts/0',
                     '/repos/example/repo/actions/jobs/9007199254740992',
                     '/repos/example/repo/actions/runs/101/attempts/1/jobs?per_page=100&page=6']:
            with self.subTest(path=path):
                adapter, budget = self.adapter([])
                with self.assertRaises(CiReadError):
                    adapter.get_json(path, budget)
                self.assertEqual(self.credential_calls, 0)

    def test_metadata_redirect_does_not_follow(self):
        adapter, budget = self.adapter([Response(302, headers=[('Location', SIGNED)])])
        with self.assertRaises(CiReadError) as caught:
            adapter.get_json('/repos/example/repo', budget)
        self.assertEqual(caught.exception.code, 'redirect_rejected')
        self.assertEqual(len(self.factory.calls), 1)

    def test_http_error_classification_without_leaking_body(self):
        for status, headers, code in [(401, [], 'authentication_required'),
                    (403, [], 'permission_denied'), (403, [('X-RateLimit-Remaining', '0')], 'rate_limited'),
                    (403, [('Retry-After', '1')], 'rate_limited'), (429, [], 'rate_limited'),
                    (404, [], 'not_found'), (503, [], 'network_error')]:
            with self.subTest(status=status, headers=headers):
                adapter, budget = self.adapter([Response(status, b'PRIVATE_RESPONSE_TOKEN', headers)])
                with self.assertRaises(CiReadError) as caught:
                    adapter.get_json('/repos/example/repo', budget)
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn('PRIVATE', str(caught.exception))

    def test_log_404_and_410_are_unavailable_not_claimed_expired(self):
        for status in (404, 410):
            adapter, budget = self.adapter([Response(status)])
            with self.assertRaises(CiReadError) as caught:
                adapter.get_log(PATH, budget)
            self.assertEqual(caught.exception.code, 'logs_unavailable')

    def test_compressed_or_inconsistent_response_is_rejected(self):
        for headers in [[('Content-Encoding', 'gzip')], [('Content-Length', '999')],
                        [('Content-Length', '-1')], [('Content-Length', '2.0')]]:
            adapter, budget = self.adapter([Response(body=b'{}', headers=headers)])
            with self.assertRaises(CiReadError) as caught:
                adapter.get_json('/repos/example/repo', budget)
            self.assertEqual(caught.exception.code, 'invalid_response')

    def test_metadata_size_and_json_parse_limits(self):
        for body, code in [(b'x' * 25, 'size_limit'), (b'{"id":1,"id":2}', 'invalid_response')]:
            adapter, budget = self.adapter([Response(body=body)],
                                           limits=replace(CiLimits(), json_response_bytes=24))
            with self.assertRaises(CiReadError) as caught:
                adapter.get_json('/repos/example/repo', budget)
            self.assertEqual(caught.exception.code, code)

    def test_api_pagination_checks_exact_next_without_following_it(self):
        path = '/repos/example/repo/actions/runs/101/attempts/1/jobs?per_page=100&page=1'
        for link, good in [('<https://api.github.com' + path.replace('&page=1', '&page=2') + '>; rel="next"', True),
                          ('<https://evil.invalid/payload>; rel="next"', False),
                          ('<https://api.github.com' + path.replace('&page=1', '&page=2&page=') + '>; rel="next"', False)]:
            adapter, budget = self.adapter([Response(headers=[('Link', link)])])
            if good:
                self.assertTrue(adapter.get_json(path, budget).has_next)
            else:
                with self.assertRaises(CiReadError):
                    adapter.get_json(path, budget)
            self.assertEqual(len(self.factory.calls), 1)

    def test_deadline_checks_delayed_headers_and_no_retry(self):
        now = [0.0]
        adapter, budget = self.adapter([TimeoutError('PRIVATE_SIGNATURE')], clock=lambda: now[0])
        with self.assertRaises(CiReadError) as caught:
            adapter.get_json('/repos/example/repo', budget)
        self.assertEqual(caught.exception.code, 'timeout')
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertEqual(len(self.factory.calls), 1)
        self.assertEqual(self.factory.closed, 1)

    def test_credential_uses_remaining_deadline_and_no_network_after_overrun(self):
        now = [0.0]
        adapter, budget = self.adapter([], clock=lambda: now[0])
        now[0] = 57
        def credential(*, timeout):
            self.assertEqual(timeout, 3)
            now[0] = 61
            return 'opaque_fixture_credential'
        adapter.connection.credential = credential
        with self.assertRaises(CiReadError) as caught:
            adapter.get_json('/repos/example/repo', budget)
        self.assertEqual(caught.exception.code, 'timeout')
        self.assertEqual(self.factory.calls, [])

    def test_expired_budget_does_not_read_credential_and_gcm_timeout_is_safe(self):
        now = [0.0]
        adapter, budget = self.adapter([], clock=lambda: now[0])
        now[0] = 61
        with self.assertRaises(CiReadError):
            adapter.get_json('/repos/example/repo', budget)
        self.assertEqual(self.credential_calls, 0)
        now[0] = 0
        adapter.connection.credential = lambda **_: (_ for _ in ()).throw(TimeoutError('PRIVATE'))
        with self.assertRaises(CiReadError) as caught:
            adapter.get_json('/repos/example/repo', budget)
        self.assertEqual(caught.exception.code, 'timeout')
        self.assertNotIn('PRIVATE', str(caught.exception))

    def test_production_gcm_keeps_default_timeout_and_ci_can_shorten_it(self):
        import subprocess
        from repo_maintainer.github_connection import GitHubConnection
        from repo_maintainer.models import SafetyError
        connection = GitHubConnection()
        with patch('repo_maintainer.github_connection.subprocess.run',
                   return_value=SimpleNamespace(returncode=0, stdout=b'password=fixture\n')) as run:
            connection.credential()
            self.assertEqual(run.call_args.kwargs['timeout'], 20)
            connection.credential(timeout=3)
            self.assertEqual(run.call_args.kwargs['timeout'], 3)
            connection.credential(timeout=99)
            self.assertEqual(run.call_args.kwargs['timeout'], 20)
        with patch('repo_maintainer.github_connection.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('credential', 1)):
            with self.assertRaises(TimeoutError):
                connection.credential(timeout=1)
            with self.assertRaises(SafetyError):
                connection.credential()

    def test_opaque_credential_is_scrubbed_from_json_and_log_in_actual_reader(self):
        repository = {'id': 901, 'full_name': 'example/repo'}
        run = {'id': 101, 'run_attempt': 1, 'workflow_id': 301, 'head_sha': 'a'*40,
               'repository': repository, 'status': 'completed', 'conclusion': 'failure'}
        job = {'id': 201, 'run_id': 101, 'run_attempt': 1, 'head_sha': 'a'*40,
               'name': 'opaque_fixture_credential', 'steps': [], 'status': 'completed', 'conclusion': 'failure'}
        adapter, _ = self.adapter([*(Response(body=json.dumps(data).encode()) for data in
                                  [repository, run, {'total_count': 1, 'jobs': [job]}, job]),
                                  Response(302, headers=[('Location', SIGNED)]),
                                  Response(body=b'opaque_fixture_credential\nFAIL\n')])
        result = CiEvidenceReader(adapter, synthetic=True).observe('https://github.com/example/repo',101,1,201)
        self.assertEqual(result['evidence_status'], 'ready')
        self.assertNotIn('opaque_fixture_credential', json.dumps(result))
        self.assertNotIn('sig=', json.dumps(result))

    def test_policy_is_exact_host_only_and_not_wildcard_or_arbitrary_host(self):
        for hosts in [('*.blob.core.windows.net',), ('evil.invalid',), ('.blob.core.windows.net',),
                      ('api.github.com',), ('BAD.blob.core.windows.net',), ('x..blob.core.windows.net',)]:
            with self.subTest(hosts=hosts), self.assertRaises(ValueError):
                CiDownloadPolicy(hosts)

    def test_ambiguous_content_length_or_redirect_is_rejected(self):
        for status, headers in [(200,[('Content-Length','2'),('content-length','100')]),
                                (302,[('Location',SIGNED),('Location',SIGNED)])]:
            adapter,budget=self.adapter([Response(status,headers=headers)])
            with self.assertRaises(CiReadError) as caught:
                if status==302:adapter.get_log(PATH,budget)
                else:adapter.get_json('/repos/example/repo',budget)
            self.assertEqual(caught.exception.code,'invalid_response')

    def test_delayed_body_hits_observation_deadline(self):
        now=[0.0]
        class Slow(Response):
            def read1(self,n):
                now[0]=61
                return super().read1(n)
        adapter,budget=self.adapter([Slow()],clock=lambda:now[0])
        with self.assertRaises(CiReadError) as caught:
            adapter.get_json('/repos/example/repo',budget)
        self.assertEqual(caught.exception.code,'timeout')
        self.assertEqual(self.factory.closed,1)


if __name__ == '__main__':
    unittest.main()
