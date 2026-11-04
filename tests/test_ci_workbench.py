"""Shared CI HTTP authentication, projection and real worker lifetime contracts."""
import asyncio
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient
from repo_maintainer.ci_http import CiHttpTransport
from repo_maintainer.repository_workbench import RepositoryWorkbench, create_workbench_app
from repo_maintainer.workbench_read import GitHubBrowser, ReadError, ReadPool
from test_ci_http import Factory, Response
from test_ci_evidence import FixtureTransport, PREFIX


class CiWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root/'unused-config').write_text('not read by CI requests')
        self.hub = RepositoryWorkbench(root/'state', root/'unused-config', ui='legacy')
        self.addCleanup(self.hub.close)
        self.client = TestClient(create_workbench_app(self.hub, 12345), base_url='http://127.0.0.1:12345')
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer '+self.hub.token}
        self.params = {'url': 'https://github.com/example/repo', 'run_id': '101', 'attempt': '1'}

    def get(self, params=None, **kwargs):
        return self.client.get('/api/ci/evidence', params=self.params if params is None else params,
                               headers=kwargs.pop('headers', self.headers), **kwargs)

    def test_shared_http_keeps_local_auth_host_origin_and_needs_no_workspace(self):
        with patch('repo_maintainer.ci_http.CiHttpTransport', return_value=FixtureTransport()) as transport:
            for headers, status in [({}, 401), ({'Authorization':'Bearer wrong'}, 401),
                                    ({**self.headers,'Origin':'https://foreign.invalid'}, 403),
                                    ({**self.headers,'Host':'evil.invalid'}, 403)]:
                self.assertEqual(self.get(headers=headers).status_code, status)
            transport.assert_not_called()
            response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['evidence_status'], 'ready')
        self.assertFalse(response.json()['synthetic'])
        self.assertEqual(self.hub.workspaces, {})
        self.assertEqual(self.hub.jobs, {})
        self.assertFalse(self.hub.ci_download_policy.hosts)

    def test_invalid_parameters_fail_before_credentials(self):
        invalid = [{}, {**self.params, 'run_id':'0'}, {**self.params, 'attempt':'10001'},
                   {**self.params, 'job_id':'true'}, {**self.params, 'run_id':'9007199254740992'},
                   {**self.params, 'expected_head_sha':'b'}, {**self.params, 'host':'evil.invalid'},
                   {**self.params, 'url':'https://evil.invalid/repo'},
                   list(self.params.items())+[('attempt','2')],
                   {**self.params, 'url':'x'*8200}]
        with patch('repo_maintainer.ci_http.CiHttpTransport') as transport:
            for params in invalid:
                with self.subTest(params=str(params)[:100]):
                    self.assertEqual(self.get(params).status_code, 400)
            transport.assert_not_called()

    def test_readpool_busy_uses_429_without_upstream_io(self):
        self.hub.read_pool.slots.acquire()
        self.hub.read_pool.slots.acquire()
        try:
            with patch.object(self.hub.github, 'credential', side_effect=AssertionError('credentials read')):
                response = self.get()
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.json()['code'], 'ci_read_error')
        finally:
            self.hub.read_pool.slots.release()
            self.hub.read_pool.slots.release()

    def test_upstream_401_403_429_are_200_evidence_and_local_session_survives(self):
        for status, headers, code in [(401, [], 'authentication_required'),
                                      (403, [], 'permission_denied'),
                                      (403, [('X-RateLimit-Remaining','0')], 'rate_limited'),
                                      (429, [], 'rate_limited')]:
            with self.subTest(status=status, code=code):
                factory = Factory([Response(status, headers=headers)])
                transport = CiHttpTransport(SimpleNamespace(credential=lambda **_: 'fixture'), https_factory=factory)
                with patch('repo_maintainer.ci_http.CiHttpTransport', return_value=transport):
                    response = self.get()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['evidence_status'], 'blocked')
                self.assertEqual(response.json()['errors'][0]['code'], code)
                self.assertEqual(self.client.get('/api/state', headers=self.headers).status_code, 200)

    def test_partial_and_stale_evidence_use_200(self):
        from repo_maintainer.ci_transport import CiReadError
        fixture = FixtureTransport()
        fixture.pages[PREFIX] = CiReadError('timeout')
        with patch('repo_maintainer.ci_http.CiHttpTransport', return_value=fixture):
            self.assertEqual(self.get().json()['evidence_status'], 'blocked')
        for expected in [901, 999]:
            with patch('repo_maintainer.ci_http.CiHttpTransport', return_value=FixtureTransport()):
                response = self.get({**self.params, 'expected_repository_id':str(expected)})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['evidence_status'], 'ready' if expected==901 else 'stale')
        fixture = FixtureTransport()
        fixture.pages[PREFIX+'/actions/runs/101/attempts/1/jobs?per_page=100&page=1'] = CiReadError('permission_denied')
        with patch('repo_maintainer.ci_http.CiHttpTransport', return_value=fixture):
            response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['evidence_status'], 'partial')

    def test_invalid_server_host_configuration_fails_before_state_is_created(self):
        root = Path(self.temp.name)/'invalid-host-state'
        with self.assertRaises(ValueError):
            RepositoryWorkbench(root, self.hub.provider_config, ci_download_hosts=('*.blob.core.windows.net',))
        self.assertFalse(root.exists())

    def test_actions_only_projects_valid_attempt_head_workflow_fields(self):
        browser = GitHubBrowser(None)
        base = {'id':101, 'html_url':'https://github.com/example/repo/actions/runs/101',
                'run_attempt':2, 'head_sha':'a'*40, 'workflow_id':301}
        with patch.object(browser, 'get', return_value=({'workflow_runs':[base]}, False)):
            item = browser.view('actions', self.params['url'])['items'][0]
            self.assertEqual([item[k] for k in ['run_attempt','head_sha','workflow_id']], [2,'a'*40,301])
        for fields in [{'run_attempt':True,'head_sha':'bad','workflow_id':False},
                       {'run_attempt':10001,'workflow_id':2**53}]:
            with patch.object(browser, 'get', return_value=({'workflow_runs':[{**base,**fields}]}, False)):
                item = browser.view('actions', self.params['url'])['items'][0]
                for key in fields:
                    self.assertNotIn(key, item)
        with patch.object(browser, 'get', return_value=([base], False)):
            item = browser.view('pulls', self.params['url'])['items'][0]
            self.assertNotIn('run_attempt', item)


class CiReadLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_http_cancellation_does_not_release_a_running_read(self):
        import httpx
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config').write_text('never read')
            hub = RepositoryWorkbench(root/'state', root/'config')
            release = threading.Event()
            entered = threading.Event()
            class Delayed(FixtureTransport):
                def get_json(self, path, budget):
                    if path == PREFIX:
                        entered.set()
                        release.wait(5)
                    return super().get_json(path, budget)
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_workbench_app(hub,12345)), base_url='http://127.0.0.1:12345')
            hub.read_pool.slots.acquire()  # Other read slot already in use.
            try:
                with patch('repo_maintainer.ci_http.CiHttpTransport', return_value=Delayed()):
                    task = asyncio.create_task(client.get('/api/ci/evidence', params={
                        'url':'https://github.com/example/repo','run_id':101,'attempt':1},
                        headers={'Authorization':'Bearer '+hub.token}))
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertFalse(hub.read_pool.slots.acquire(blocking=False))
                    with self.assertRaises(ReadError):
                        await hub.read_pool.run(lambda: 'too early')
            finally:
                release.set()
                hub.read_pool.slots.release()
                await client.aclose()
                hub.close()  # Waits for the actual worker to exit.

    async def test_cancelled_or_timed_out_http_waiter_holds_slots_until_workers_exit(self):
        for timeout in [False, True]:
            with self.subTest(timeout=timeout):
                pool = ReadPool()
                entered = [threading.Event(), threading.Event()]
                release = threading.Event()
                def worker(index):
                    entered[index].set()
                    release.wait(5)
                    return 'completed'
                tasks = [asyncio.create_task(pool.run(worker, i)) for i in range(2)]
                try:
                    for event in entered:
                        self.assertTrue(await asyncio.to_thread(event.wait, 2))
                    if timeout:
                        with self.assertRaises(TimeoutError):
                            await asyncio.wait_for(tasks[0], .01)
                    else:
                        tasks[0].cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await tasks[0]
                    with self.assertRaises(ReadError) as caught:
                        await pool.run(lambda: 'must not start')
                    self.assertEqual(caught.exception.status, 429)
                    self.assertFalse(tasks[1].done())
                    release.set()
                    await tasks[1]
                    self.assertEqual(await pool.run(lambda:'after exit'), 'after exit')
                finally:
                    release.set()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    pool.close()
