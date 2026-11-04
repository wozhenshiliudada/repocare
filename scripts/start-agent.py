"""Start/reuse an explicitly configured local service; restarting never executes a task."""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

PROJECT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(PROJECT/'src'),str(PROJECT.parent/'ui-python-deps'),str(PROJECT.parent/'recovery-python-deps')]
from filelock import FileLock
from repo_maintainer.cli import load_policy
from repo_maintainer.dashboard import unlinked
from repo_maintainer.models import SafetyError
from repo_maintainer.service_state import saved_budget


def main(argv=None):
    parser=argparse.ArgumentParser()
    for name in ('repo','policy','runs-dir','provider-config'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--request-budget',type=int,default=100)
    parser.add_argument('--max-attempts',type=int,choices=(1,2,3),default=1)
    parser.add_argument('--allow-auto',action='store_true')
    parser.add_argument('--restart',action='store_true')
    args=parser.parse_args(argv)
    for name in ('repo','policy','runs_dir','provider_config'):
        value=getattr(args,name)
        if not value.is_absolute():raise SafetyError('Use explicit absolute source/configuration/state paths')
        setattr(args,name,unlinked(value))
    expected_policy=json.loads(json.dumps(asdict(load_policy(args.policy))))
    launcher=unlinked(args.runs_dir/'launcher');launcher.mkdir(parents=True,exist_ok=True)
    connection=unlinked(args.runs_dir/'ui-state/server-private.json')

    def probe():
        if not connection.is_file():return None
        value=json.loads(connection.read_text())
        url=urlsplit(value['launch_url'])
        if url.scheme!='http' or url.hostname!='127.0.0.1' or url.path!='/' or not url.fragment.startswith('session='):
            raise SafetyError('Invalid private local service descriptor')
        token=url.fragment[len('session='):]
        if len(token)!=64 or any(char not in '0123456789abcdef' for char in token):
            raise SafetyError('Invalid private service capability')
        base=f'http://127.0.0.1:{url.port}'
        try:
            with urllib.request.urlopen(urllib.request.Request(base+'/api/service',headers={'Authorization':'Bearer '+token}),timeout=2) as response:
                state=json.loads(response.read(65536))
        except (urllib.error.URLError,TimeoutError):return None
        if (state['repository']!=str(args.repo) or state['runs_dir']!=str(args.runs_dir)
                or state['policy']!=expected_policy or state['provider_binding']['config_file']!=str(args.provider_config)
                or state['provider_binding']['type']!='PiProvider'
                or state['provider_binding']['credential_source']!='environment'
                or state['provider_binding']['mock_endpoint']!=['',None,None,'']
                or state['allow_auto']!=args.allow_auto or state['auto_attempts']!=args.max_attempts
                or args.port and url.port!=args.port):
            raise SafetyError('Running service has a different explicit scope; preserve it')
        return value,state,base,token

    with FileLock(str(unlinked(launcher/'start.lock')),timeout=2):
        live=probe()
        if live and not args.restart:
            if live[1]['runtime']['code_changed']:raise SafetyError('Owned service needs --restart to load new code; its ledger is preserved')
            print(live[0]['launch_url']);return
        if live:
            if live[1]['active']:raise SafetyError('Service is busy; restarting would interrupt an operation')
            payload={'request_id':uuid.uuid4().hex,'revision':live[1]['stop_revision']}
            request=urllib.request.Request(live[2]+'/api/actions/stop_service',json.dumps(payload).encode(),
                {'Authorization':'Bearer '+live[3],'Content-Type':'application/json','Origin':live[2]})
            with urllib.request.urlopen(request,timeout=3) as response:assert response.status==202
            # Wait for the original owner lock to be released, not just for its listener to disappear.
            owner=FileLock(str(unlinked(args.runs_dir/'ui-state/owner.lock')),timeout=10)
            with owner:pass
        command=[sys.executable,'-X','utf8','-m','repo_maintainer','serve','--repo',str(args.repo),
            '--policy',str(args.policy),'--runs-dir',str(args.runs_dir),'--provider-config',str(args.provider_config),
            '--port',str(args.port),'--max-attempts',str(args.max_attempts)]
        if (args.runs_dir/'ui-state').exists() or (args.runs_dir/'ui-sessions').exists():
            saved_budget(args.runs_dir);command+=['--resume-budget']
        else:command+=['--request-budget',str(args.request_budget)]
        if args.allow_auto:command+=['--allow-auto']
        environment={key:value for key,value in os.environ.items() if not key.startswith('PI_MAINTENANCE_')}
        environment['PYTHONPATH']=os.pathsep.join(sys.path[:3]+[environment.get('PYTHONPATH','')])
        with (launcher/'stdout-private.log').open('ab') as stdout,(launcher/'stderr.log').open('ab') as stderr:
            child=subprocess.Popen(command,cwd=PROJECT,env=environment,stdin=subprocess.DEVNULL,
                stdout=stdout,stderr=stderr,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            live=probe()
            if live:
                print(live[0]['launch_url']);return
            if child.poll() is not None:raise SafetyError('Service did not start; inspect the local launcher stderr log')
            time.sleep(.25)
        raise SafetyError('Service startup pending; inspect before starting another instance')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error),file=sys.stderr);sys.exit(1)
