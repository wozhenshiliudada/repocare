"""Bounded explicit Pi data operations; caller's DurableProvider owns reserve/settle."""
import json
import os
import shutil
import subprocess
from . import pi_evidence
from .job_control import OperationStopped
from .models import SafetyError
from .runner import stop_tree
from .runtime_limits import MAX_REQUESTS
from .pi_distribution import runtime_script
from .task_session import require


def terminal_failure_evidence(raw, purpose, source_sha256, allowance):
    """Keep a validated failed terminal receipt, never raw output or authority.

    This improves diagnostics only. It cannot settle usage, recover a candidate,
    release a reservation, or retroactively change a previous unknown outcome.
    """
    if not isinstance(raw, bytes) or len(raw) > 200000:
        return None
    try:
        data = json.loads(raw)
        require(isinstance(data, dict) and set(data) == {'protocol_version', 'status', 'evidence'}
                and type(data['protocol_version']) is int and data['protocol_version'] == 1
                and data['status'] == 'failed', 'Invalid failed Pi envelope')
        evidence = pi_evidence.validate_evidence(data['evidence'], allowance)
        context_field = {'memory_reflection': 'reflection_context_sha256',
                         'task_compaction': 'compaction_context_sha256'}[purpose]
        require(evidence['status'] == 'failed' and evidence['events_complete']
                and evidence.get('purpose') == purpose and evidence.get(context_field) == source_sha256,
                'Failed Pi receipt differs from the original operation')
        return evidence
    except (SafetyError, ValueError, KeyError, TypeError):
        return None


def invoke(provider, script, purpose, request, guard, validator):
    require((script,purpose) in {('reflect.mjs','memory_reflection'),('compact.mjs','task_compaction')},
            'Unknown auxiliary Pi operation')
    cancel=provider._cancel_event
    if cancel is not None and cancel.is_set():raise OperationStopped('Pi operation stopped before launch')
    require(callable(guard),'Pi operation requires its controller source guard');guard()
    allowance=provider.max_requests-provider.requests_used
    require(type(allowance) is int and 1<=allowance<=MAX_REQUESTS,'Original Pi request budget exhausted')
    context,_=provider.task_identity()
    require(not(provider.config_file and (provider.mock_url or provider.credential_source!='environment')),
            'Pi configuration sources cannot be mixed')
    config={}
    if provider.config_file:
        from .provider_config import load_provider_config
        config=load_provider_config(provider.config_file)
    from .provider_config import PROVIDER_FIELDS, resolve_model_limits
    env={k:v for k,v in os.environ.items() if k.upper() in {
        'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA','COMSPEC','PATHEXT'}
        or (not provider.config_file and k in PROVIDER_FIELDS)}
    env.update(config);node=shutil.which('node');require(node is not None,'Pi requires Node.js')
    command=[node,str(runtime_script(script))]
    if provider.mock_url:
        command+=['--mock-url',provider.mock_url];env={k:v for k,v in env.items() if not k.startswith('PI_MAINTENANCE_')}
    elif provider.credential_source=='openclaw-readonly':command+=['--credential-source','openclaw-readonly']
    model_limits=resolve_model_limits(env,mock_url=provider.mock_url)
    provider.evidence={'status':'started','requests':None,'purpose':purpose}
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,
        start_new_session=os.name!='nt',creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
    def failure(raw=b'', terminal=b''):
        partial=terminal_failure_evidence(terminal,purpose,request['source']['context_sha256'],allowance)
        if partial is None:partial=pi_evidence.progress_evidence(raw,allowance)
        if partial and not pi_evidence.matches_model_limits(partial,model_limits):partial=None
        provider.evidence={**(partial or {}),'status':'failed','requests':None,'consumption':'unknown',
                           'purpose':purpose,'returncode':process.returncode}
    try:
        raw=json.dumps({**request,'request_budget':allowance}).encode()
        if provider._progress_callback is not None and cancel is not None:
            from .pi_live import communicate_live
            output,error=communicate_live(process,raw,allowance,provider._progress_callback,cancel,pi_evidence.PROCESS_TIMEOUT_SECONDS)
        else:output,error=process.communicate(raw,timeout=pi_evidence.PROCESS_TIMEOUT_SECONDS)
    except BaseException as problem:
        stop_tree(process);failure(getattr(problem,'partial',b''));raise
    secret=env.get('PI_MAINTENANCE_API_KEY','')
    if secret:output=output.replace(secret.encode(),b'[REDACTED]')
    try:
        require(len(output)<=200000,'Pi auxiliary output exceeds byte limit')
        data=json.loads(output);evidence=pi_evidence.validate_evidence(data['evidence'],allowance)
        require(pi_evidence.matches_model_limits(evidence,model_limits),'Pi output limit differs from selected model')
        require(evidence.get('purpose')==purpose and process.returncode==0 and type(data.get('protocol_version')) is int
                and data['protocol_version']==1 and data.get('status')==evidence['status']=='completed',
                'Pi auxiliary operation did not complete')
        result=validator(data,evidence,context)
    except (SafetyError,ValueError,KeyError,TypeError):
        failure(error,output);raise SafetyError('Pi operation failed; original request consumption retained for inspection') from None
    provider.evidence={**evidence,'returncode':process.returncode}
    provider.requests_used+=evidence['requests'];guard()
    return result
