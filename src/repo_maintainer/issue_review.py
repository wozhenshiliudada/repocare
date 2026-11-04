"""Explicit pre-candidate review feedback; historical data, never authority."""
import re
from .dashboard import read_json, unlinked
from .issue_intake import fingerprint
from .models import SafetyError, digest
from .store import redact

def require(value,message):
    if not value:raise SafetyError(message)

def review_text(value):
    require(isinstance(value,str) and 1<=len(value.strip())<=2000
            and len(value.encode('utf-8'))<=6000
            and not any(ord(c)<32 and c not in '\n\t' for c in value)
            and redact(value)==value,'Review feedback must be bounded plain text without secrets')
    return value.strip()

def verified_review_context(value,issue,base_sha,task_id):
    if value is None:return None
    fields={'schema_version','task_id','execution_id','report_sha256','issue_sha256','base_sha',
            'previous_plan_sha256','reason','claims_only','runtime_authorized','context_sha256'}
    require(isinstance(value,dict) and set(value)==fields and type(value['schema_version']) is int
            and value['schema_version']==1 and value['task_id']==task_id and task_id is not None
            and value['issue_sha256']==digest(issue.encode()) and value['base_sha']==base_sha
            and value['claims_only'] is True and value['runtime_authorized'] is False,'Invalid review context binding')
    for key,n in [('task_id',32),('execution_id',32),('report_sha256',64),('context_sha256',64)]:
        require(isinstance(value[key],str) and re.fullmatch('[a-f0-9]{'+str(n)+'}',value[key]),'Invalid review context identity')
    require(value['previous_plan_sha256'] is None or (isinstance(value['previous_plan_sha256'],str)
            and re.fullmatch('[a-f0-9]{64}',value['previous_plan_sha256'])),'Invalid historical plan fingerprint')
    require(review_text(value['reason'])==value['reason'] and fingerprint({k:v for k,v in value.items() if k!='context_sha256'})==value['context_sha256'],
            'Review context changed')
    return value

def revision_preview(service,identity,reason):
    reason=review_text(reason)
    row=service.view(identity)
    require(row['status']=='failed_no_replay','Only a completed pre-candidate failure can be revised')
    record,report_sha=read_json(unlinked(service.runs/row['run_id'])/'run.json')
    expected=dict(execution_id=row['id'],issue_number=row['issue_number'],issue_token=row['issue_token'],
        policy_binding_sha256=row['policy_binding']['binding_sha256'],task_id=row.get('task_id'),rules_sha256=row['rules_sha256'])
    require(record.get('issue_execution')==expected and record.get('run_id')==row['run_id']
            and record.get('source_repo')==str(service.source.root),'Original review/run binding differs')
    require(record.get('finished_at') and record.get('source_unchanged') is True
            and record.get('baseline_tests')==[] and record.get('candidate_tests')==[]
            and not record.get('environment') and not unlinked(service.runs/row['run_id']/'workspace').exists()
            and not service.source.command('show-ref','--verify','--hash','refs/heads/maintenance/'+row['run_id'],check=False),
            'Review revision only supports safely finished planning before candidate preparation')
    execution,execution_sha=read_json(service.runs/row['run_id']/'repair-execution.json')
    from .integration import fingerprint as process_fingerprint
    require(execution.get('phase')=='finished' and execution.get('inflight') is None
            and execution.get('owner',{}).get('run_id')==row['run_id']
            and execution.get('owner_sha256')==process_fingerprint(execution.get('owner')),
            'Original review process outcome needs inspection')
    evidence=record.get('provider_evidence',{})
    receipt=row.get('model_evidence',{})
    require(receipt.get('evidence_sha256')==fingerprint(evidence) and evidence.get('consumption')=='reported'
            and type(evidence.get('requests')) is int and evidence['requests']>0
            and receipt.get('requests')==evidence['requests'],'Original model settlement differs')
    rejected=record.get('status')=='rejected' and record.get('authorization',{}).get('decision')=='reject'
    no_plan=(record.get('status')=='failed' and evidence.get('failure_category')=='no_plan'
             and evidence.get('terminal_count')=={'schema_version':1,'completed_streams':evidence['requests']}
             and evidence.get('requests_started')==evidence['requests'] and evidence.get('events_complete') is True)
    require(rejected or no_plan,'Only rejected proposals or settled no_plan may be reviewed')
    require(service.source.snapshot()==row['source_before']==record.get('source_before'),'Source changed since review')
    with service.registry.transaction() as db:
        head=service._load('issue_execution_head',row['issue_key'],db)
    require(head=={'execution_id':identity},'A newer execution already owns this review')
    budget=service.registry.budget()
    require(budget['requests_reserved_unknown']==0 and budget['requests_remaining']>0,
            'Original budget is exhausted or has unknown consumption')
    task=service.registry.load('pi_task').get(row.get('task_id'))
    require(task and task.get('revision',0)>0 and service.registry.load('pi_task_link').get('run:'+row['run_id'])=={'task_id':row['task_id']},
            'Review revision requires the original committed Native checkpoint')
    from .task_session import TaskSessionStore
    store=TaskSessionStore(service.registry,service.source.root,service.source.common_dir)
    with store.lease(row['task_id'],task['scope'],task['revision']) as lease:
        native=lease.descriptor()  # Strictly parse abandoned tails; do not delete or adopt them.
    context=dict(schema_version=1,task_id=row['task_id'],execution_id=identity,report_sha256=report_sha,
        issue_sha256=digest(record['issue'].encode()),base_sha=row['source_before']['head'],
        previous_plan_sha256=record.get('plan_sha256'),reason=reason,claims_only=True,runtime_authorized=False)
    context['context_sha256']=fingerprint(context)
    value=dict(execution_id=identity,execution_challenge=row['challenge'],report_sha256=report_sha,
        execution_sha256=execution_sha,checkpoint_sha256=fingerprint(task),native_sha256=native['file_sha256'],
        review_context=context,budget=budget,new_model_requests=0,new_approval_required=True,old_failure_preserved=True)
    return {**value,'revision':fingerprint(value)}
