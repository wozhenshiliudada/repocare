"""Pinned supplemental checks for two new CL pairs; no host target imports/model calls."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import uuid
from swebench_cl_pair_specs import KIND,PAIRS
from swebench_point_behavior import read_logs,require

sha=lambda value:hashlib.sha256(value).hexdigest()

def snapshot(workspace,pair):
    spec=PAIRS[pair];workspace=Path(workspace).resolve(strict=True)
    executable=shutil.which('git');require(executable and not Path(executable).resolve().is_relative_to(workspace),'Trusted Git required')
    env={key:value for key,value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM='1',GIT_TERMINAL_PROMPT='0')
    def git(*args):return subprocess.check_output([executable,'-c','core.hooksPath='+os.devnull,'-c','core.fsmonitor=false','-C',str(workspace),*args],env=env,stderr=subprocess.PIPE,timeout=30)
    require(Path(git('rev-parse','--show-toplevel').decode().strip()).resolve()==workspace,'Expected repository root')
    head=git('rev-parse','HEAD').decode().strip();require(head in spec['bases'].values(),'Wrong fixed pair base')
    for name in (*spec['allowed_paths'],*spec['readonly_paths']):
        path=workspace/name
        require(all(not parent.is_symlink() and not parent.is_junction() for parent in (path,*path.parents) if parent.is_relative_to(workspace)),'Linked source rejected')
    require(b'160000 ' not in git('ls-files','--stage') and not git('ls-files','--others','--exclude-standard').strip(),'Submodules/untracked source rejected')
    changed=set(git('diff','--name-only','-z','HEAD').decode().strip('\0').split('\0'))-{''}
    require(changed<=set(spec['allowed_paths']),'Patch outside fixed editable scope')
    patch=git('diff','--no-ext-diff','--no-textconv','--binary','HEAD');require(len(patch)<=1024*1024,'Patch too large')
    common=str(Path(git('rev-parse','--path-format=absolute','--git-common-dir').decode().strip()).resolve())
    return {'workspace':str(workspace),'common_dir':common,'base_commit':head,'patch':patch,'status':git('status','--porcelain=v1','--untracked-files=all')}

def verified(value,code,pair,base,patch_sha):
    require(isinstance(value,dict) and value.get('kind')==KIND and value.get('pair')==pair
        and value.get('base_commit')==base and value.get('patch_sha256')==patch_sha
        and type(value.get('model_requests')) is int and value['model_requests']==0
        and value.get('official_score') is False and value.get('infrastructure_error',False) is False,'Behavior binding differs')
    rows=value.get('cases')
    require(isinstance(rows,list) and all(isinstance(row,dict) for row in rows)
        and [row.get('case') for row in rows]==PAIRS[pair]['cases']
        and all(type(row.get('passed')) is bool and isinstance(row.get('diagnostic'),str) and len(row['diagnostic'])<=4000 for row in rows),'Incomplete behavior cases')
    passed=sum(row['passed'] for row in rows)
    require(type(value.get('passed')) is int and value['passed']==passed and type(value.get('total')) is int
        and value['total']==len(rows) and type(code) is int and code==(0 if passed==len(rows) else 1),'Behavior count/exit mismatch')
    return value

def evaluate(workspace,images,output,pair):
    before=snapshot(workspace,pair);locks=json.loads(Path(images).read_bytes());spec=PAIRS[pair]
    require(set(locks)==set(spec['bases']),'Expected both selected image locks')
    identity=next(key for key,base in spec['bases'].items() if base==before['base_commit']);pin=locks[identity]
    prefix='swebench/sweb.eval.x86_64.'+identity.replace('__','_1776_')+'@sha256:'
    require(re.fullmatch(re.escape(prefix)+'[0-9a-f]{64}',pin['digest']) and re.fullmatch('sha256:[0-9a-f]{64}',pin['image_id']),'Pinned image identity required')
    output=Path(output).resolve();require(not output.is_relative_to(Path(before['workspace'])) and not output.is_relative_to(Path(before['common_dir'])),'Evidence must be outside source/Git')
    directory=output/uuid.uuid4().hex;directory.mkdir(parents=True)
    files={name:Path(__file__).with_name(name).read_bytes() for name in ('cl_pair_behavior_probe.py','swebench_cl_pair_specs.py')}
    (directory/'intent.json').write_bytes(json.dumps({'pair':pair,'container_name':'repocare-cl-preservation-'+directory.name,'base':before['base_commit'],'patch_sha256':sha(before['patch']),
        'sources':{name:sha(raw) for name,raw in files.items()},'model_requests':0}).encode())
    import docker
    client=docker.DockerClient(base_url='npipe:////./pipe/dockerDesktopLinuxEngine' if os.name=='nt' else 'unix:///var/run/docker.sock',timeout=45)
    owned=None
    try:
        require(client.images.get(pin['digest']).id==pin['image_id'],'Local image changed')
        owned=client.containers.create(pin['digest'],name='repocare-cl-preservation-'+directory.name,entrypoint=[],working_dir='/testbed',
            command=['/opt/miniconda3/envs/testbed/bin/python','-I','-B','/probe/run.py','--pair',pair,'--base',before['base_commit']],
            network_disabled=True,cap_drop=['ALL'],security_opt=['no-new-privileges'],mem_limit='1g',pids_limit=128,
            log_config={'Type':'json-file','Config':{'max-size':'1m','max-file':'1'}})
        payload=io.BytesIO()
        with tarfile.open(fileobj=payload,mode='w') as archive:
            folder=tarfile.TarInfo('probe');folder.type=tarfile.DIRTYPE;folder.mode=0o755;archive.addfile(folder)
            for name,raw in [('run.py',files['cl_pair_behavior_probe.py']),('swebench_cl_pair_specs.py',files['swebench_cl_pair_specs.py']),('patch.diff',before['patch'])]:
                member=tarfile.TarInfo('probe/'+name);member.size=len(raw);member.mode=0o644;archive.addfile(member,io.BytesIO(raw))
        require(owned.put_archive('/',payload.getvalue()),'Probe installation failed')
        owned.start();terminal=owned.wait(timeout=90);stdout,stderr=read_logs(owned,True),read_logs(owned,False)
        (directory/'stdout.log').write_bytes(stdout);(directory/'stderr.log').write_bytes(stderr)
        value=verified(json.loads(stdout.splitlines()[-1]),terminal['StatusCode'],pair,before['base_commit'],sha(before['patch']))
        require(snapshot(workspace,pair)==before,'Source changed during check')
        value.update(image=pin,network_disabled=True,infrastructure_error=False,source_unchanged=True,
            tests_status={'SUPPLEMENTARY_BEHAVIOR':{'success':[r['case'] for r in value['cases'] if r['passed']],
                'failure':[r['case'] for r in value['cases'] if not r['passed']]}},
            failure_excerpt='\n'.join(r['case']+'\n'+r['diagnostic'] for r in value['cases'] if not r['passed'])[-14000:],evidence_directory=str(directory))
        (directory/'result.json').write_bytes(json.dumps(value,indent=2).encode()+b'\n');return value
    finally:
        try:
            if owned is not None:owned.remove(force=True)
        finally:client.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--images',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--pair',choices=PAIRS,required=True);a=p.parse_args()
    try:
        value=evaluate(a.workspace,a.images,a.output,a.pair);code=0 if value['passed']==value['total'] else 1
    except Exception as error:value={'kind':KIND,'infrastructure_error':True,'model_requests':0,'official_score':False,'error':type(error).__name__+': '+str(error)};code=2
    print(json.dumps(value),flush=True);raise SystemExit(code)
