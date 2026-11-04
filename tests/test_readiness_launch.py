"""Actual loopback workbench: startup, auth, assets, local checks and safe stop."""
import json
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid


class ReadinessLaunchTests(unittest.TestCase):
    def test_fresh_random_port_checks_and_stop_without_repository_or_model(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'provider.local'
            config.write_bytes((project / '.env.example').read_bytes())
            process = subprocess.Popen([sys.executable, '-B', str(project / 'maintain.py'), 'workbench',
                '--workspace-root', str(root / 'state'), '--provider-config', str(config),
                '--request-budget', '4', '--port', '0'], cwd=project,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding='utf-8', errors='replace')
            lines = queue.Queue()
            def drain(stream, channel):
                for line in stream:
                    lines.put((channel, line.rstrip()))
            threads = [threading.Thread(target=drain, args=(stream, channel), daemon=True)
                       for stream, channel in ((process.stdout, 'out'), (process.stderr, 'err'))]
            for thread in threads:
                thread.start()
            try:
                launch = None
                for _ in range(30):
                    try:
                        channel, line = lines.get(timeout=1)
                    except queue.Empty:
                        if process.poll() is not None:
                            self.fail('Isolated startup ended before its private launch receipt')
                        continue
                    if channel == 'out' and re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]*/#session=[a-f0-9]{64}', line):
                        launch = line
                        break
                self.assertIsNotNone(launch, 'No bounded private launch receipt')
                base, token = launch.split('/#session=')
                def get(path, authenticated=True, origin=None):
                    headers = {'Authorization': 'Bearer ' + token} if authenticated else {}
                    if origin:
                        headers['Origin'] = origin
                    with urlopen(Request(base + path, headers=headers), timeout=15) as response:
                        return response.read(), response.status
                # Uvicorn may print the receipt a fraction before accepting.
                import time
                for attempt in range(50):
                    try:
                        service = json.loads(get('/api/service')[0])
                        break
                    except OSError:
                        if attempt == 49:
                            raise
                        time.sleep(0.05)
                self.assertEqual('unknown', service['readiness']['status'])
                for path, authenticated, origin, code in (
                    ('/api/readiness', False, None, 401),
                    ('/api/readiness', True, 'https://outside.invalid', 403),
                ):
                    with self.subTest(code=code), self.assertRaises(HTTPError) as caught:
                        get(path, authenticated, origin)
                    self.assertEqual(code, caught.exception.code)
                html, status = get('/')
                self.assertEqual(200, status)
                for asset in re.findall(rb'(?:src|href)=["\'](/assets/[^"\']+)', html):
                    self.assertEqual(200, get(asset.decode())[1])
                checked = json.loads(get('/api/readiness')[0])
                self.assertEqual('blocked', checked['status'])  # Intentionally unfilled example.
                self.assertEqual(0, checked['model_calls'])
                self.assertFalse(checked['network_checked'])
                state = json.loads(get('/api/state')[0])
                self.assertEqual([], state['workspaces'])
                self.assertEqual([], state['sessions'])
                self.assertEqual([], state['jobs'])
                self.assertEqual([], state['monitors'])
                body = json.dumps({'request_id': uuid.uuid4().hex, 'revision': service['stop_revision']}).encode()
                request = Request(base + '/api/actions/stop_workbench', data=body, method='POST', headers={
                    'Authorization': 'Bearer ' + token, 'Origin': base, 'Content-Type': 'application/json'})
                with urlopen(request, timeout=15) as response:
                    self.assertEqual(202, response.status)
                self.assertEqual(0, process.wait(timeout=15))
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)
                for thread in threads:
                    thread.join(timeout=2)
                process.stdout.close()
                process.stderr.close()


if __name__ == '__main__':
    unittest.main()
