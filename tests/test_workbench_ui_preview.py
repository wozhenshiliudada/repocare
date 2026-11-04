"""Preview port 0 must bind root and embedded authentication to one listener."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from starlette.testclient import TestClient

spec=importlib.util.spec_from_file_location('preview_port_test',ROOT/'scripts/workbench_ui_preview.py')
preview=importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class PreviewPortTests(unittest.TestCase):
    def test_ephemeral_port_is_bound_before_embedded_sessions_and_served_once(self):
        observed=[]
        def server(config):
            hub=config.app.workbench
            def run(*,sockets):
                self.assertEqual(1,len(sockets))
                address,port=sockets[0].getsockname()
                self.assertEqual('127.0.0.1',address)
                self.assertGreater(port,0)
                self.assertEqual(port,hub.port)
                with TestClient(config.app,base_url=f'http://127.0.0.1:{port}') as client:
                    response=client.get('/api/state',headers={'Authorization':'Bearer '+hub.token})
                    self.assertEqual(200,response.status_code)
                    workspaces=response.json()['workspaces']
                    self.assertEqual(2,len(workspaces))
                    for workspace in workspaces:
                        response=client.get(workspace['api_prefix']+'state',headers={'Authorization':'Bearer '+workspace['token']})
                        self.assertEqual(200,response.status_code,response.text)
                        self.assertEqual(0,response.json()['requests_used'])
                        self.assertEqual(401,client.get(workspace['api_prefix']+'state').status_code)
                        self.assertEqual(403,client.get(workspace['api_prefix']+'state',headers={
                            'Authorization':'Bearer '+workspace['token'],'Origin':'http://127.0.0.1:0'}).status_code)
                hub.stop_callback()
                self.assertTrue(instance.should_exit)
                observed.append(hub)
            instance=SimpleNamespace(run=run,should_exit=False)
            return instance
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'new-preview'
            with patch.object(sys,'argv',['preview','--directory',str(root),'--port','0']),\
                 patch.object(preview.uvicorn,'Server',side_effect=server),contextlib.redirect_stdout(io.StringIO()):
                preview.main()
            self.assertEqual(1,len(observed))
            self.assertTrue(observed[0].closed)
