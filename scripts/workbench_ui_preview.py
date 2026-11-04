"""Run a disposable, explicitly synthetic UI fixture without any model/network calls."""
from pathlib import Path
import argparse
import sys
import time
import uuid
import socket
import uvicorn
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.provider import MockProvider
from repo_maintainer.repository_workbench import RepositoryWorkbench, inspect_repository, create_workbench_app

class SyntheticProvider(MockProvider):
    max_requests=6
    requests_used=0
    def plan(self,*args):
        value=super().plan(*args)
        self.evidence={'requests':0,'transport':'deterministic-mock'}
        return value

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--port',type=int,default=28314)
    args=parser.parse_args()
    root=args.directory.resolve()
    if root.exists():parser.error('Use a new disposable directory; existing fixtures are never reset')
    # Bind once before creating embedded sessions: port 0 must not become
    # their permanent Host/Origin boundary. Keep the same socket until exit.
    hub=None
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1',args.port))
        port=listener.getsockname()[1]
        try:
            root.mkdir(parents=True)
            repositories=[create_fixture(root/name) for name in ('calculator-demo','docs-demo')]
            config=root/'synthetic-config.txt';config.write_text('SYNTHETIC UI FIXTURE - NO MODEL CREDENTIALS',encoding='utf-8')
            hub=RepositoryWorkbench(root/'state',config,request_budget=6,provider_factory=lambda _:SyntheticProvider(),ui='react')
            hub.port=port
            for repository in repositories:
                preview=inspect_repository(repository)
                job=hub.submit(uuid.uuid4().hex,'connect',{'path':str(repository),'revision':preview['revision'],
                    'allowed_paths':['calculator.py'],'readonly_paths':['tests/test_calculator.py'],
                    'allowed_tests':[list(DEMO_TEST)],'timeout_seconds':30,'allow_auto':False,'confirmed':True})
                while hub.jobs[job['id']]['status']=='running':time.sleep(.02)
                if hub.jobs[job['id']]['status']!='finished':raise RuntimeError(hub.jobs[job['id']])
            for workspace in hub.workspaces.values():
                workspace.session.label='合成 UI 演示 · MockProvider · 真实模型调用 0'
            hub.url=f'http://127.0.0.1:{port}/#session={hub.token}'
            print('Synthetic calculator fixture. No model credentials or paid calls.',flush=True)
            print(hub.url,flush=True)
            server=uvicorn.Server(uvicorn.Config(create_workbench_app(hub,port),access_log=False,
                proxy_headers=False,ws='none',log_level='warning'))
            hub.stop_callback=lambda:setattr(server,'should_exit',True)
            server.run(sockets=[listener])
        finally:
            if hub is not None:hub.close()

if __name__=='__main__':main()
