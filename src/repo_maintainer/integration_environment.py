"""Reverify sealed code with the original granted environment, never host fallback."""
import json
import os
import re
from pathlib import Path
import stat

from .issue_intake import fingerprint
from .models import SafetyError,digest
from .recovery import process_identity,unlinked
from .store import now,redact
from .target_environment import TargetEnvironment,validate_grant,compare_environment_checks,requires_npm
from .workspace_inventory import inventory


def require(value,message):
    if not value: raise SafetyError(message)


def load_origin(directory,record,source,candidate,policy):
    """Metadata only: this function never starts a runtime or installs anything."""
    declared=record['environment']
    require(declared.get('status')=='prepared' and declared.get('receipt')=='environment/environment.json',
            'Original environment is not prepared; legacy fallback is disabled')
    path=unlinked(directory/'environment/environment.json')
    require(path.is_file() and path.stat().st_size<=8*1024*1024,'Original environment receipt unavailable')
    raw=path.read_bytes()
    receipt=json.loads(raw)
    plan,grant,binding=receipt['plan'],receipt['grant'],receipt['environment_binding']
    validate_grant(plan,grant)
    require(receipt['status']=='prepared' and binding==declared['binding']
            and fingerprint(binding)==declared['environment_sha256']==receipt['environment_sha256'],
            'Original environment binding changed')
    require(plan['head_sha']==record['base_sha']==binding['base_sha']
            and plan['repository_id']==digest(os.path.normcase(str(source.common_dir)).encode())
            and binding['grant_sha256']==grant['grant_sha256']
            and binding['inputs_sha256']==fingerprint(plan['decision_inputs'])
            and binding['checks_sha256']==fingerprint(plan['checks']), 'Original environment source/scope differs')
    require(plan['runtime'] in {'python','node'} and
            all(list(c) in [r['argv'] for r in plan['checks']] for c in policy.allowed_tests),
            'Original environment check scope differs')
    declaration = record.get('behavior_validation')
    if declaration is not None or plan.get('behavior_contract') is not None:
        from .workflow_behavior import verify_pair
        require(declaration is not None and declaration.get('contract') == plan.get('behavior_contract'),
                'Original environment behavior declaration changed or missing')
        verify_pair(record, source, policy, candidate)
    tests=record['candidate_tests']
    require(len(tests)==len(policy.allowed_tests) and all(t.get('category')=='passed' and
            t.get('environment_sha256')==declared['environment_sha256'] for t in tests)
            and compare_environment_checks(record['baseline_tests'],tests)['candidate_verified'],
            'No complete same-environment candidate verification')
    for pin in plan['decision_inputs']:
        require(digest(candidate.read(pin['path']))==pin['sha256'],'Original check/configuration input changed')
    return {'receipt':receipt,'receipt_sha256':digest(raw),'path':path,
            'plan':plan,'grant':grant,'binding':binding}


def static_state(directory,record,source,candidate,policy,origin):
    actual=load_origin(directory,record,source,candidate,policy)
    require(actual['receipt_sha256']==origin['receipt_sha256'],'Original environment receipt changed during delivery')
    execution_path=unlinked(directory/'repair-execution.json')
    require(execution_path.is_file() and execution_path.stat().st_size<=16*1024*1024,
            'Original repair ownership checkpoint unavailable')
    execution=json.loads(execution_path.read_text(encoding='utf-8'))
    owner=execution['owner']
    require(owner['run_id']==record['run_id'] and owner['workspace']==str(candidate.root)
            and owner['common_dir']==str(source.common_dir) and owner['source_repo']==str(source.root)
            and owner['base_sha']==record['base_sha'] and owner['policy']==record['policy'],
            'Original repair/environment ownership differs')
    files=inventory(candidate.root)
    require(files==execution['checkpoint']['inventory'],'Original candidate/dependency files changed')
    binding,grant=origin['binding'],origin['grant']
    if origin['plan']['runtime']=='python':
        venv=unlinked(directory/'environment/venv')
        python=unlinked(venv/('Scripts/python.exe' if os.name=='nt' else 'bin/python'))
        runtime=binding['runtime']
        site=unlinked(Path(runtime['site']))
        require(site.is_relative_to(venv)
                and unlinked(Path(runtime['executable']))==python,'Original venv path differs')
        # Legacy receipts have only the shared hash and retain that check.
        venv_sha256=runtime.get('venv_executable_sha256',binding['runtime_executable_sha256'])
        require(digest(python.read_bytes())==venv_sha256,
                'Original venv executable changed')
        dependencies=inventory(site)
        executable=unlinked(Path(grant['python']))
        node_root=False
    else:
        python=None
        dependencies={name[len('node_modules/'):]:row for name,row in files.items() if name.startswith('node_modules/')}
        node_root='node_modules' in files
        executable=unlinked(Path(grant['node']))
        runtime = binding['runtime']
        if requires_npm(origin['plan']) or {'npm_version', 'npm_cli_sha256'} & runtime.keys():
            require(grant['npm_cli'] and 'npm_version' in runtime and 'npm_cli_sha256' in runtime,
                    'Original npm runtime binding missing')
            require(digest(unlinked(Path(grant['npm_cli'])).read_bytes())==runtime['npm_cli_sha256'],
                    'Original npm runtime changed')
    require(digest(executable.read_bytes())==binding['runtime_executable_sha256']
            and fingerprint(dependencies)==binding['dependencies_sha256'],'Original runtime/dependencies changed')
    return {'python':str(python) if python else None,'dependencies':dependencies,
            'has_node_modules':node_root,'inventory':files}


class SealedEnvironment(TargetEnvironment):
    """Reopen the owned environment; preserve original receipt and venv location."""
    def __init__(self,directory,record,source,candidate,policy,review,persist):
        self.owner,self.record,self.source,self.original,self.policy=directory,record,source,candidate,policy
        self.origin=load_origin(directory,record,source,candidate,policy)
        self.state=static_state(directory,record,source,candidate,policy,self.origin)
        self.plan,self.grant,self.binding=self.origin['plan'],self.origin['grant'],self.origin['binding']
        self.root,self.workspace,self.python=directory/'environment',candidate,self.state['python']
        self.review,self.persist=review,persist
        require(re.fullmatch(r'[0-9a-f]{32}',review['preparation']['attempt_id']) is not None,
                'Invalid environment preparation attempt')
        self.receipt_name='integration-environment-'+review['preparation']['attempt_id']+'.json'
        require(not unlinked(directory/self.receipt_name).exists(),'Never replay verification environment receipt')
        self.copied=0
        self.receipt={'schema_version':1,'status':'prepared','plan':self.plan,'grant':self.grant,
            'environment_binding':self.binding,'environment_sha256':fingerprint(self.binding),
            'origin_receipt_sha256':self.origin['receipt_sha256'],'commands':[],'checks':[],
            'started_at':now(),'runtime_authorized':False,'os_network_isolated':False}
        self.save()
        require(self.identity()==self.binding,'Original environment no longer matches candidate')

    def save(self):
        path=unlinked(self.owner/self.receipt_name)
        temporary=unlinked(self.owner/(self.receipt_name+'.tmp'))
        temporary.write_text(redact(json.dumps(self.receipt,ensure_ascii=False,indent=2))+'\n',encoding='utf-8')
        temporary.replace(path)

    def process(self,argv,*,on_start=None,timeout=None):
        self.review.update(operation_stage='test_starting',test_inflight=None)
        self.persist()
        def started(pid):
            self.review.update(operation_stage='test_running',test_inflight=process_identity(pid))
            self.persist()
            if on_start: on_start(pid)
        result=super().process(argv,on_start=started,timeout=timeout)
        self.review.setdefault('environment_processes',[]).append({
            'command':list(argv),'returncode':result['returncode'],'timed_out':result['timed_out'],
            'output_truncated':result['output_truncated'],'status':'finished',
            'identity':self.receipt['commands'][-1].get('process_identity')})
        self.review.update(operation_stage='test_finished',test_inflight=None)
        self.persist()
        return result

    def attach(self,sealed):
        intent=self.review['preparation']
        require(sealed.root==self.owner/'integration-verification' and sealed.common_dir==self.source.common_dir
                and sealed.head==intent['commit'] and not sealed.command('symbolic-ref','-q','HEAD',check=False),
                'Sealed environment workspace identity differs')
        static_state(self.owner,self.record,self.source,self.original,self.policy,self.origin)
        if self.plan['runtime']=='node' and self.state['has_node_modules']:
            destination=unlinked(sealed.root/'node_modules')
            require(not destination.exists(),'Do not adopt existing sealed dependencies')
            expected=dict(intent['verification_inventory'])
            expected['node_modules']={'directory':True}
            expected.update({'node_modules/'+n:row for n,row in self.state['dependencies'].items()})
            intent['verification_inventory']=expected  # Own exact bytes before any copies.
            intent['environment_dependency_copy']=True
            self.review['operation_stage']='environment_copying'
            self.persist()
            destination.mkdir()
            total=0
            for name,row in sorted(self.state['dependencies'].items()):
                target=unlinked(destination/name)
                if row.get('directory'):
                    target.mkdir(parents=True,exist_ok=True)
                else:
                    path=unlinked(self.original.root/'node_modules'/name)
                    with path.open('rb') as stream: raw=stream.read(64*1024*1024+1)
                    total+=len(raw)
                    require(total<=64*1024*1024 and digest(raw)==row['sha256'],'Original dependency changed during copy')
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with target.open('xb') as stream: stream.write(raw)
                    target.chmod(stat.S_IMODE(row['mode']))
                    self.copied+=1
            require(inventory(destination)==self.state['dependencies'],'Copied dependencies differ')
        self.workspace=sealed
        self.receipt['verification_workspace']=str(sealed.root)
        self.save()
        require(inventory(sealed.root)==intent['verification_inventory'],'Sealed dependency preparation changed unexpected files')
        require(self.identity()==self.binding,'Sealed runtime/dependencies differ from verified candidate')

    def summary(self,tests):
        static_state(self.owner,self.record,self.source,self.original,self.policy,self.origin)
        comparison=compare_environment_checks(self.record['candidate_tests'],tests)
        require(comparison['candidate_verified'],'Sealed same-environment verification failed')
        from .workflow_behavior import validate_sealed_checks
        validate_sealed_checks(self.record, tests, source=self.source, commit=self.workspace.head)
        self.receipt['status']='verified'
        self.save()
        return {'origin_receipt_sha256':self.origin['receipt_sha256'],'binding':self.binding,
            'environment_sha256':fingerprint(self.binding),'receipt':self.receipt_name,
            'receipt_sha256':digest(unlinked(self.owner/self.receipt_name).read_bytes()),
            'comparison':comparison,'copied_dependency_files':self.copied,
            'interpretation':'Same owned runtime/dependencies; trusted local tests, no OS/network sandbox.'}


def verify_publication(directory,record,source,candidate,policy,review):
    origin=load_origin(directory,record,source,candidate,policy)
    static_state(directory,record,source,candidate,policy,origin)
    core=review['review']
    require(isinstance(core.get('preparation_attempt_id'),str)
            and re.fullmatch(r'[0-9a-f]{32}',core['preparation_attempt_id']) is not None,
            'Invalid environment preparation attempt')
    value=core.get('environment_verification',{})
    expected='integration-environment-'+core['preparation_attempt_id']+'.json'
    require(value.get('origin_receipt_sha256')==origin['receipt_sha256'] and value.get('binding')==origin['binding']
            and value.get('receipt')==expected,'Matching integration environment evidence required; legacy fallback is disabled')
    path=unlinked(directory/expected)
    require(path.is_file() and path.stat().st_size<=8*1024*1024
            and digest(path.read_bytes())==value.get('receipt_sha256'),'Sealed environment evidence changed')
    require(compare_environment_checks(record['candidate_tests'],core['tests'])['candidate_verified'],
            'No complete passed same-environment sealed checks')
    from .workflow_behavior import validate_sealed_checks
    validate_sealed_checks(record, core['tests'], source=source, commit=core['commit'])


def dependency_directories(directory,intent):
    """Only the original pinned dependency tree may extend cleanup ownership."""
    path=unlinked(directory/'environment/environment.json')
    require(path.is_file() and path.stat().st_size<=8*1024*1024,'Original environment receipt unavailable')
    raw=path.read_bytes()
    require(digest(raw)==intent['environment_origin_sha256'],'Original environment ownership receipt changed')
    receipt=json.loads(raw)
    if receipt['plan']['runtime']!='node' or not intent.get('environment_dependency_copy'):
        return set()
    projected={n[len('node_modules/'):]:row for n,row in intent.get('verification_inventory',{}).items()
               if n.startswith('node_modules/')}
    require(fingerprint(projected)==receipt['environment_binding']['dependencies_sha256'],
            'Verification dependency ownership manifest differs')
    return {'node_modules'} | {'node_modules/'+name for name,row in projected.items() if row.get('directory')}


def ignored_match(directory,intent,actual):
    dependency_directories(directory,intent)  # Verify declared copies against original binding.
    if 'verification_ignored' in intent:
        return actual==bytes.fromhex(intent['verification_ignored'])
    # A crash can follow a complete, predeclared dependency copy but precede
    # saving Git's ignored listing. Only exact known dependency files qualify;
    # full inventory/registration checks still run before any removal.
    allowed={name for name,row in intent.get('verification_inventory',{}).items()
             if name.startswith('node_modules/') and not row.get('directory')}
    try: paths={p.decode('utf-8') for p in actual.split(b'\0') if p}
    except UnicodeDecodeError: return False
    return paths<=allowed
