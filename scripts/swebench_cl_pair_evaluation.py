"""New-root adapter for two frozen CL pairs; reuses original Pi/journal/evaluation.

No legacy intent is adopted. Paid stages still require separate conversation
authorization. This adapter does not authorize billing by accepting a digest.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[1]
for path in (PROJECT/'src',PROJECT.parent/'ui-python-deps',PROJECT.parent/'recovery-python-deps'):
    sys.path.insert(0,str(path))
from repo_maintainer.models import digest
from repo_maintainer.task_session import fingerprint,require
from swebench_cl_pair_specs import PAIRS,KIND
from swebench_cl_pair_behavior import verified

def binding(root):
    root=Path(root).resolve(strict=True)
    value=json.loads((root/'pair-spec.json').read_bytes())
    require(set(value)=={'schema_version','kind','name','spec','batch_root','batch_plan_sha256'},'Unexpected pair fields')
    require(type(value['schema_version']) is int and value['schema_version']==1 and value['kind']=='swebench-cl-new-pair-v1'
        and value['name'] in PAIRS and value['spec']==PAIRS[value['name']],'Pair definition changed')
    batch=Path(value['batch_root']).resolve(strict=True)
    require(root==batch/'pairs'/value['name'] and root.is_relative_to(batch),'Only the new owned batch pair directory is supported')
    plan=json.loads((batch/'batch-plan.json').read_bytes())
    require(value['batch_plan_sha256']==fingerprint(plan) and plan.get('kind')=='swebench-cl-two-pair-development-v1'
        and plan.get('root')==str(batch) and plan.get('pair_names')==list(PAIRS)
        and plan.get('request_ceiling')==200 and plan.get('per_pair_ceiling')==100
        and plan.get('credentials_read') is False and plan.get('paid_requests')==0,'Batch plan changed')
    prepared=json.loads((root/'prepared/preparation.json').read_bytes())
    spec=value['spec']
    require((prepared['history'],prepared['target'])==(spec['history'],spec['target']),'Prepared pair differs')
    agents=json.loads((root/'prepared/agent/tasks.json').read_bytes())
    require(len(agents)==2 and all(set(row)=={'instance_id','repo','base_commit','problem_statement'} for row in agents),'Only allowlisted Agent input is supported')
    require([row['instance_id'] for row in agents]==[spec['history'],spec['target']]
        and all(row['repo']=='sympy/sympy' and row['base_commit']==spec['bases'][row['instance_id']] for row in agents),'Task/base binding differs')
    return value

def sources():
    return {name:digest((PROJECT/'scripts'/name).read_bytes()) for name in
        ('swebench_cl_pair_specs.py','swebench_cl_pair_behavior.py','cl_pair_behavior_probe.py')}

def behavior_controls(root,value,images):
    pack=json.loads((root/'behavior-controls.json').read_bytes());spec=value['spec']
    require(pack.get('kind')==KIND and pack.get('source_hashes')==sources()
        and pack.get('model_requests')==0 and pack.get('official_score') is False
        and set(pack.get('baselines',{}))==set(spec['bases']),'Behavior controls changed')
    for identity,base in spec['bases'].items():
        row=pack['baselines'][identity]
        verified(row,0,value['name'],base,digest(b''))
        require(row.get('image')==images[identity] and row.get('source_unchanged') is True
            and row.get('network_disabled') is True and row.get('infrastructure_error') is False,'Behavior baseline is not bound')

def configured(root):
    root=Path(root).resolve(strict=True)
    value=binding(root);spec=value['spec']
    loaded=importlib.util.spec_from_file_location('isolated_cl_pair_core',PROJECT/'scripts/swebench_cl_evaluation.py')
    core=importlib.util.module_from_spec(loaded);loaded.loader.exec_module(core)
    core.IDS=(spec['history'],spec['target'])
    original_code,original_files,original_commands=core.code_hashes,core.fixed_files,core.policy_commands
    def code():
        result=original_code()
        for name in (*sources(),'swebench_cl_pair_evaluation.py','prepare_swebench_cl.py'):
            result['scripts/'+name]=digest((PROJECT/'scripts'/name).read_bytes())
        return result
    def files(directory,behavior_checks=False):
        require(Path(directory).resolve()==root and not behavior_checks,'No legacy root or point-specific gate may be adopted')
        result=original_files(directory,False)
        result['pair-spec.json']=digest((root/'pair-spec.json').read_bytes())
        result['behavior-controls.json']=digest((root/'behavior-controls.json').read_bytes())
        return result
    def commands(directory,manifest):
        current=binding(root);require(current==value and manifest.get('history')==spec['history'] and manifest.get('target')==spec['target'],'Manifest pair differs')
        images=json.loads((root/'image-lock.json').read_bytes());behavior_controls(root,value,images)
        environment=json.loads((root/'environment-ready.json').read_bytes())
        require(environment['allowed_paths']==spec['allowed_paths'] and environment['readonly_paths']==spec['readonly_paths'],'Source scope changed')
        return original_commands(directory,manifest)+((manifest['judge_python'],'-X','utf8',str(PROJECT/'scripts/swebench_cl_pair_behavior.py'),
            '--workspace','.','--images',str(root/'image-lock.json'),'--output',str(root/'behavior-results'),'--pair',value['name']),)
    core.code_hashes=code;core.fixed_files=files;core.policy_commands=commands
    return core

def freeze(root,python):
    core=configured(root)
    # Reject incomplete/changed behavior controls before a manifest can be issued.
    value=binding(root);behavior_controls(root,value,json.loads((root/'image-lock.json').read_bytes()))
    core.policy_commands(root,{'judge_python':str(python.resolve()),
        'history':value['spec']['history'],'target':value['spec']['target']})
    result=core.freeze(root,python,False)
    core.checked(root,result['manifest_sha256'])
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('freeze','history','measure','check'))
    p.add_argument('--root',type=Path,required=True);p.add_argument('--judge-python',type=Path);p.add_argument('--approved-manifest');p.add_argument('--review-history')
    a=p.parse_args();root=a.root.resolve()
    if a.action=='freeze':
        require(a.judge_python is not None,'Explicit judge Python required');result=freeze(root,a.judge_python)
    else:
        core=configured(root);manifest=core.checked(root,a.approved_manifest)
        result=({'checked':True,'paid_requests':0,'credentials_read':False} if a.action=='check'
            else core.history(root,manifest) if a.action=='history' else core.measure(root,manifest,a.review_history))
    print(json.dumps(result),flush=True)
