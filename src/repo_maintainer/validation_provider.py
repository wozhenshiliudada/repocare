"""Original Pi process transport for a controller-bound readonly proposal phase."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from . import pi_evidence
from .job_control import OperationStopped
from .maintenance_limits import LIMITS
from .models import SafetyError,digest
from .runner import stop_tree
from .runtime_limits import MAX_REQUESTS
from .pi_distribution import runtime_script
from .task_session import strict_json,validate_transcript,unlinked,MAX_SESSION_BYTES,fingerprint,require

PURPOSE='validation-proposal-v1'


def _native_result(provider,data,files):
    request=provider._task_session_request
    receipt=data.get('task_checkpoint')
    require(isinstance(receipt,dict) and set(receipt)=={
        'task_id','revision','file','leaf_id','session_id','snapshot_sha256'},'Missing validation Native receipt')
    require(receipt['task_id']==request['binding']['task_id'] and receipt['revision']==request['binding']['revision']
        and receipt['snapshot_sha256']==fingerprint(files),
        'Validation Native task or source differs')
    name=receipt['file']
    require(isinstance(name,str) and Path(name).name==name and '/' not in name and '\\' not in name
        and name.endswith('.jsonl'),'Validation Native filename is invalid')
    cwd=unlinked(Path(request['binding']['cwd']))
    path=unlinked(cwd.parent/'sessions'/name)
    require(path.is_file() and 0<path.stat().st_size<=MAX_SESSION_BYTES,'Validation Native file missing or oversized')
    raw=path.read_bytes()
    actual=validate_transcript(raw,cwd=cwd,context=request['context'],leaf=receipt['leaf_id'],session_id=receipt['session_id'])
    entries=[strict_json(line.decode('utf-8')) for line in raw.splitlines()]
    by_id={entry['id']:entry for entry in entries[1:]}
    branch=[];cursor=receipt['leaf_id']
    while cursor is not None:
        branch.append(by_id[cursor]);cursor=by_id[cursor]['parentId']
    calls={};accepted=[]
    for entry in reversed(branch):
        message=entry.get('message',{})
        if message.get('role')=='assistant':
            for part in message['content']:
                if part.get('type')=='toolCall' and part.get('name')=='propose_validation':calls[part['id']]=part['arguments']
        elif message.get('role')=='toolResult' and message.get('toolName')=='propose_validation' and not message['isError']:
            accepted.append((calls[message['toolCallId']],message['details']['proposal_sha256']))
    require(accepted and fingerprint(accepted[-1][0])==fingerprint(data.get('proposal'))
            and accepted[-1][1]==data.get('proposal_sha256')==data['evidence'].get('validation_proposal_sha256'),
            'Validation output differs from its accepted original Native tool call')
    require(actual['leaf_id']==receipt['leaf_id'],'Validation Native leaf changed')
    return receipt


def generate(provider,repository,issue,tests):
    from .validation_proposal import validate_source,proposal_contract
    require(tests==() and provider.allowed_paths==() and provider._task_session_request
        and provider._task_session_request['context'].get('purpose')==PURPOSE,
        'Validation planning requires its original readonly Native phase')
    source=provider._validation_source
    validate_source(source,repository,issue)
    cancel=provider._cancel_event
    if cancel is not None and cancel.is_set():raise OperationStopped('Validation stopped before model launch')
    require(not(provider.config_file and (provider.mock_url or provider.credential_source!='environment')),
            'Validation configuration sources cannot be mixed')
    allowance=provider.max_requests-provider.requests_used
    require(type(allowance) is int and 1<=allowance<=MAX_REQUESTS, 'Validation request allowance unavailable')
    config={}
    if provider.config_file:
        from .provider_config import load_provider_config
        config=load_provider_config(provider.config_file)
    from .provider_config import PROVIDER_FIELDS, resolve_model_limits
    env={k:v for k,v in os.environ.items() if k.upper() in {
        'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA','COMSPEC','PATHEXT'}
        or (not provider.config_file and k in PROVIDER_FIELDS)}
    env.update(config)
    if provider.mock_url:env={k:v for k,v in env.items() if not k.startswith('PI_MAINTENANCE_')}
    model_limits=resolve_model_limits(env,mock_url=provider.mock_url,
        legacy_context_window_tokens=1048576 if provider.mock_url else 32768)
    node=shutil.which('node');require(node is not None,'Pi validation requires Node.js')
    command=[node,str(runtime_script('validation-plan.mjs'))]
    if provider.mock_url:command+=['--mock-url',provider.mock_url]
    elif provider.credential_source=='openclaw-readonly':command+=['--credential-source','openclaw-readonly']
    body=json.dumps(source,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    request={'purpose':PURPOSE,'source_json':body,'source_sha256':digest(body.encode()),
             'request_budget':allowance,'task_session':provider._task_session_request}
    raw=json.dumps(request,ensure_ascii=False).encode()
    require(len(raw)<=LIMITS['planning_packet_max_bytes'],'Validation source packet exceeds original capacity')
    provider.evidence={'status':'started','requests':None,'purpose':PURPOSE}
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,
        start_new_session=os.name!='nt',creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
    def failure(error=b''):
        partial=pi_evidence.progress_evidence(error,allowance)
        if partial and not pi_evidence.matches_model_limits(partial,model_limits):partial=None
        provider.evidence={**(partial or {}),'status':'failed','requests':None,'consumption':'unknown','purpose':PURPOSE}
    try:
        if provider._progress_callback is not None and cancel is not None:
            from .pi_live import communicate_live
            output,error=communicate_live(process,raw,allowance,provider._progress_callback,cancel,pi_evidence.PROCESS_TIMEOUT_SECONDS)
        else:output,error=process.communicate(raw,timeout=pi_evidence.PROCESS_TIMEOUT_SECONDS)
    except BaseException:
        stop_tree(process);failure();raise
    finally:
        for stream in (process.stdin,process.stdout,process.stderr):
            if stream is not None and not stream.closed:stream.close()
    secret=env.get('PI_MAINTENANCE_API_KEY','')
    if secret:output=output.replace(secret.encode(),b'[REDACTED]')
    try:
        require(len(output)<=LIMITS['planning_packet_max_bytes'],'Oversized validation response')
        data=strict_json(output.decode('utf-8'))
        evidence=pi_evidence.validate_evidence(data['evidence'],allowance)
        require(pi_evidence.matches_model_limits(evidence,model_limits),'Validation output limit differs from selected model')
        require(process.returncode==0 and data.get('status')==evidence['status']=='completed'
            and evidence.get('purpose')==PURPOSE and evidence.get('validation_context_sha256')==request['source_sha256']
            and evidence.get('validation_proposal_sha256')==data.get('proposal_sha256')
            and evidence.get('source_snapshot_sha256')==fingerprint(source['files']),
            'Validation response differs from original request')
        contract=proposal_contract(source,data['proposal'])
        checkpoint=_native_result(provider,data,source['files'])
        validate_source(source,repository,issue)
    except (SafetyError,ValueError,KeyError,TypeError):
        failure(error);raise SafetyError('Validation proposal failed; original request consumption retained') from None
    provider.evidence=evidence;provider.requests_used+=evidence['requests'];provider._task_checkpoint=checkpoint
    return {'proposal':data['proposal'],'proposal_sha256':data['proposal_sha256'],'contract':contract,
            'status':'clarification_needed' if contract is None else 'awaiting_validation_review',
            'runtime_authorized':False}
