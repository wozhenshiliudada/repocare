"""Explicit Issue execution using the original registry, Pi tasks and repair controller."""
from __future__ import annotations
from .maintenance_limits import LIMITS

from dataclasses import asdict
import json
import os
import threading
import uuid

from .dashboard import unlinked, read_json
from .git import RepositoryGateway
from .issue_intake import IssueReader, fingerprint
from .job_control import JobControl, OperationStopped
from .maintenance_rules import compile_policy, validate_rules
from .models import SafetyError
from .pi_provider import PiProvider
from .recovery import process_identity
from .store import now, redact
from .task_planning import TaskPlanner
from .ui_registry import DurableProvider, provider_binding
from .workflow import repair
from .workflow_environment import GrantedEnvironment

KIND='issue_execution'
ACTIVE={'claimed','running'}




class IssueChanged(SafetyError):
    """A historical task cannot be treated as the current Issue."""


def require(value,message):
    if not value:
        raise SafetyError(message)


class ExecutionReader(IssueReader):
    """Bound the whole execution's freshness reads, not just each individual read."""
    def __init__(self,service,window):
        super().__init__(service.connection,service.spec,registry=service.registry)
        self.window=window

    def api(self,path):
        require(self.window['calls']<self.window['limit'],
                'Issue freshness request bound reached; no automatic retry')
        self.window['calls']+=1
        return super().api(path)


class IssueExecution:
    """Inject an already-owned UIRegistry. Never creates a budget or resets history.

    This is a synchronous controller API; a UI worker can call start/resume while
    another authenticated action requests a cooperative pause. No remote writes.
    """
    def __init__(self,source,registry,connection,spec,rules,provider_factory,*,source_scope=None,source_guard=None):
        self.source=RepositoryGateway(source)
        self.registry,self.connection,self.spec=registry,connection,spec
        with registry.transaction() as db:
            original=json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
        require(original.get('repository')==str(self.source.root),'Original registry repository identity differs')
        validate_rules(rules,self.source)
        require(list(spec['read_roots'])==rules['read_roots'],'Issue and maintenance read scope differ')
        require(not registry.root.is_relative_to(self.source.root)
                and not registry.root.is_relative_to(self.source.common_dir),'Execution journal must be outside source/Git')
        self.rules,self.provider_factory=rules,provider_factory
        self.source_scope,self.source_guard=source_scope or {},source_guard
        self.runs=unlinked(registry.root.parent)  # Original run-id directory convention; UI/recovery share it.
        self.controls={}
        self.lock=threading.RLock()

    def _load(self,kind,identity,db):
        row=db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(kind,identity)).fetchone()
        return json.loads(row[0]) if row else None

    def view(self,identity):
        with self.registry.transaction() as db:
            value=self._load(KIND,identity,db)
        require(value is not None,'Unknown Issue execution')
        return {**value,'challenge':fingerprint(value)}

    def _update(self,identity,**changes):
        with self.registry.transaction() as db:
            value=self._load(KIND,identity,db)
            require(value is not None,'Issue execution missing')
            value.update(changes)
            value['revision']+=1
            self.registry.put(KIND,identity,value,db)
        return value

    def _snapshot(self,number,window):
        return ExecutionReader(self,window).snapshot(number)

    def request_pause(self,identity,challenge):
        with self.registry.transaction() as db:
            row=self._load(KIND,identity,db)
            require(row and fingerprint(row)==challenge,'Pause challenge changed; refresh')
            require(row['status'] in ACTIVE,'Only an active execution can be paused')
            row.update(pause_requested=True,revision=row['revision']+1)
            self.registry.put(KIND,identity,row,db)
        with self.lock:
            control=self.controls.get(identity)
            if control:
                control.cancel.set()
        return self.view(identity)

    def _fresh(self,row,window):
        current=self.view(row['id'])
        if current['pause_requested']:
            raise OperationStopped('Issue execution pause requested; consumption and Native history are preserved')
        require(current['status'] in ACTIVE,'Execution is no longer active')
        if row.get('continuation_kind')=='review_revision':
            require(current.get('review_context')==row['review_context'],'Controlled review changed during execution')
            prior=self.view(row['review_context']['execution_id'])
            require(prior['challenge']==row['review_origin_challenge']
                    and read_json(self.runs/prior['run_id']/'run.json')[1]==row['review_context']['report_sha256'],
                    'Original review evidence changed during execution')
        validate_rules(self.rules,self.source)
        require(self.rules['rules_sha256']==row['rules_sha256']
                and fingerprint(self.spec)==row['spec_sha256'],'Maintenance/trust rules changed')
        fresh=self._snapshot(row['issue_number'],window)
        if fresh.get('status')!='ready' or fresh.get('token')!=row['issue_token']:
            raise IssueChanged('Issue/trust/target changed; no automatic reopen or replay')
        with self.registry.transaction() as db:
            head=self._load('issue_head',row['issue_key'],db)
            if not head or head['token']!=row['issue_token']:
                raise IssueChanged('Newer Issue investigation is registered')
            if row.get('continuation_kind')=='version_successor':
                intent=self._load('issue_successor',row['successor_id'],db)
                parent=self._load(KIND,row['parent_execution_id'],db)
                claim_head=self._load('issue_execution_head',row['issue_key'],db)
                require(intent and intent.get('execution_id')==row['id']
                        and intent.get('status') in {'claimed','running','finished'}
                        and intent.get('new_token')==row['issue_token']
                        and parent and fingerprint(parent)==row['parent_challenge']
                        and claim_head and claim_head.get('execution_id')==row['id'],
                        'Version successor intent/parent/execution head changed; no replay')
        require(self.source.snapshot(exclude_branch='maintenance/'+row['run_id'])==row['source_before'],
                'Source files, Git metadata or references changed')
        if self.source_guard:
            self.source_guard()

    def takeover(self,identity,challenge):
        """Only reconcile a stopped owner. Original recovery must finish its run first."""
        with self.registry.transaction() as db:
            row=self._load(KIND,identity,db)
            require(row and fingerprint(row)==challenge,'Takeover challenge changed; refresh')
            require(row['status'] in ACTIVE,'Takeover only reconciles an interrupted active owner')
            owner=row['owner_process']
            require(owner and process_identity(owner['pid'])!=owner,'Original execution owner is still alive')
            record=read_json(self.runs/row['run_id']/'run.json')[0]
            require(record.get('finished_at') and record.get('status') not in {'candidate_ready','integrated'},
                    'Inspect and explicitly recover the original repair before takeover')
            row.update(status='interrupted',revision=row['revision']+1,finished_at=now())
            self.registry.put(KIND,identity,row,db)
            self.registry.put('issue_execution_owner',self.rules['repository_id'],{'execution_id':None},db)
        return self.view(identity)

    def resume(self,identity,challenge,environment_plan,grant,*,approval=None,max_attempts=3):
        return self.start(None,environment_plan,grant,approval=approval,max_attempts=max_attempts,
                          _resume=(identity,challenge))

    def environment_retry_preview(self,identity):
        """Read-only review of a safely rolled-back, pre-test environment failure.

        This does not change failed_no_replay into paused or reuse an old approval.
        Only this explicit path may create a successor on the same Native/ledger.
        """
        row=self.view(identity)
        require(row['status']=='failed_no_replay','Only a failed environment execution can be reviewed')
        directory=unlinked(self.runs/row['run_id'])
        record,report_sha=read_json(directory/'run.json')
        expected={'execution_id':row['id'],'issue_number':row['issue_number'],
                  'issue_token':row['issue_token'],'policy_binding_sha256':row['policy_binding']['binding_sha256'],
                  'task_id':row.get('task_id'),'rules_sha256':row['rules_sha256']}
        require(record.get('run_id')==row['run_id'] and record.get('source_repo')==str(self.source.root)
                and record.get('issue_execution')==expected,'Original environment failure/run binding differs')
        execution,execution_sha=read_json(directory/'repair-execution.json')
        from .integration import fingerprint as execution_fingerprint
        require(execution.get('phase')=='finished' and execution.get('inflight') is None
                and execution.get('owner',{}).get('run_id')==row['run_id']
                and execution.get('owner_sha256')==execution_fingerprint(execution.get('owner')),
                'Original environment process outcome needs inspection')
        require(record.get('status')=='rolled_back' and record.get('finished_at')
                and record.get('source_unchanged') is True
                and record.get('recovery')=={'result':'candidate_removed','source_reset':False}
                and record.get('environment',{}).get('status')=='environment_blocked'
                and record.get('baseline_tests')==[] and record.get('candidate_tests')==[]
                and record.get('authorization',{}).get('decision') in {'approve','policy_scope'},
                'Only a safely removed environment failure before baseline/candidate tests may continue')
        require(not unlinked(directory/'workspace').exists()
                and not self.source.command('show-ref','--verify','--hash','refs/heads/maintenance/'+row['run_id'],check=False),
                'Original candidate still exists; inspect/recover it first')
        require(self.source.snapshot()==row['source_before']==record.get('source_before'),
                'Source changed since the environment failure')
        budget=self.registry.budget()
        require(budget['requests_reserved_unknown']==0 and budget['requests_remaining']>0,
                'Original budget is exhausted or has unknown consumption')
        link=self.registry.load('pi_task_link').get('run:'+row['run_id'])
        task=self.registry.load('pi_task').get(row.get('task_id'))
        require(task and task.get('revision',0)>0 and link=={'task_id':row['task_id']},
                'Original completed Native checkpoint is required; no replacement task')
        from .task_session import TaskSessionStore
        store=TaskSessionStore(self.registry,self.source.root,self.source.common_dir)
        with store.lease(row['task_id'],task['scope'],task['revision']) as lease:
            lease.descriptor()  # Verify original Native bytes before a new claim/reservation.
        value={'execution_id':identity,'execution_challenge':row['challenge'],'report_sha256':report_sha,
               'execution_sha256':execution_sha,
               'checkpoint_sha256':fingerprint(task),'budget':budget,'new_model_requests':0,
               'new_approval_required':True,'old_failure_preserved':True}
        return {**value,'revision':fingerprint(value)}

    def retry_environment(self,identity,revision,environment_plan,grant,*,approval=None,max_attempts=3):
        preview=self.environment_retry_preview(identity)
        require(preview['revision']==revision,'Environment retry preview changed; review again')
        require(callable(approval),'Environment retry requires a new approval callback')
        return self.start(None,environment_plan,grant,approval=approval,max_attempts=max_attempts,
                          _resume=(identity,preview['execution_challenge']),_environment_retry=preview)

    def review_revision_preview(self,identity,reason):
        from .issue_review import revision_preview
        return revision_preview(self,identity,reason)

    def revise_review(self,identity,revision,reason,environment_plan,grant,*,approval=None,max_attempts=3):
        preview=self.review_revision_preview(identity,reason)
        require(preview['revision']==revision,'Review revision preview changed; review again')
        require(callable(approval),'Review revision requires a new approval callback')
        return self.start(None,environment_plan,grant,approval=approval,max_attempts=max_attempts,
            _resume=(identity,preview['execution_challenge']),_review_retry=preview)

    def start(self,investigation_id,environment_plan,grant,*,approval=None,max_attempts=3,_resume=None,
              _environment_retry=None,_review_retry=None,_successor=None):
        require(type(max_attempts) is int and 1<=max_attempts<=LIMITS["candidate_max_rounds"],'Repair attempts exceed configured limit')
        require(not (_successor and (_resume or _environment_retry or _review_retry)),
                'A version successor cannot inherit a pause/retry/approval')
        # Snapshot reads retain IssueReader's own 60s bound. This is a separate
        # GitHub-read limit, not a new model/request/whole-task timeout.
        window={'calls':0,'limit':40*(max_attempts+1)}
        previous=None
        if _resume:
            previous=self.view(_resume[0])
            allowed={'failed_no_replay'} if _environment_retry or _review_retry else {'paused','interrupted'}
            require(previous['challenge']==_resume[1] and previous['status'] in allowed,
                    'Resume needs a fresh paused/interrupted execution challenge')
            investigation_id=previous['investigation_id']
            old_path=self.runs/previous['run_id']/'run.json'
            if old_path.exists():
                old=read_json(old_path)[0]
                require(old.get('finished_at') and old.get('status') not in {'candidate_ready','integrated'},
                        'Old repair is not safely terminal; inspect/recover it first')
            else:
                require(not previous.get('task_id'),'Missing original repair evidence; preserve it for inspection')
        with self.registry.transaction() as db:
            job=self._load('issue_diagnosis',investigation_id,db)
        require(job and job['status']=='investigated' and job['snapshot']['status']=='ready',
                'A completed eligible original Issue investigation is required')
        snapshot=job['snapshot']
        fresh=self._snapshot(snapshot['issue']['number'],window)
        require(fresh.get('token')==snapshot['token'] and fresh['status']=='ready',
                'Issue changed after investigation; refresh without replay')
        if _successor:
            require(snapshot['token']==_successor['new_token']
                    and investigation_id==_successor['investigation_id'],
                    'Version successor is bound to a different Issue token/investigation')
            parent=self.view(_successor['parent_execution_id'])
            require(parent['issue_key']==job['issue_key']
                    and parent['issue_token']==_successor['parent_token']
                    and parent['status'] in {'awaiting-review','delivered','failed_no_replay'}
                    and parent['challenge']==_successor['parent_challenge'],
                    'Version successor parent is active, changed or not disposed')
        policy,binding=compile_policy(self.source,snapshot,job['report'],environment_plan,self.rules)
        factory=GrantedEnvironment(environment_plan,grant)  # Before model planning/claim.
        require(self.rules['mode']!='approval' or callable(approval),
                'Manual mode requires a new approval callback; no old decision is inherited')
        require(self.registry.path.stat().st_size<30*1024*1024,
                'Original execution journal near capacity; preserve it without new claims')
        provider=self.provider_factory(policy)
        require(isinstance(provider,PiProvider) and tuple(provider.allowed_paths)==policy.allowed_paths
                and tuple(provider.readonly_paths)==policy.readonly_paths,'Original Pi provider must match compiled Policy')
        require(getattr(provider,'_cancel_event',None) is None,'Provider is already owned by another operation')
        with self.registry.transaction() as db:
            metadata=json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
        require(metadata.get('repository')==str(self.source.root),'Original registry repository identity differs')
        saved_policy=json.loads(json.dumps(asdict(policy)))
        from .issue_workbench import authorized_rules
        require(metadata.get('policy')==saved_policy or
                (metadata.get('maintenance_rules_sha256')==self.rules['rules_sha256']
                 and metadata.get('issue_spec_sha256')==fingerprint(self.spec)) or
                authorized_rules(self.registry,self.source,self.spec,self.rules),
                'Original registry does not authorize this Policy/rule binding; no alternate ledger')
        for field,key in (('source_memory','memory'),('structure_index','structure')):
            if metadata.get(field) is not None:
                require(self.source_scope.get(key)==metadata[field] and callable(self.source_guard),
                        'Original source-memory/structure binding needs its unchanged scope and guard')
        budget=self.registry.budget()
        require(metadata.get('provider')==provider_binding(provider)
                and provider.max_requests==budget['request_budget'] and budget['requests_remaining']>0,
                'Original provider/budget differs or is exhausted/unknown; do not create a new ledger')
        before=self.source.snapshot()
        self.source.require_clean(before)
        identity,run_id=uuid.uuid4().hex,uuid.uuid4().hex
        owner=process_identity(os.getpid())
        require(owner,'Controller process identity unavailable')
        row={'id':identity,'revision':0,'status':'claimed','investigation_id':investigation_id,
             'issue_key':job['issue_key'],'issue_number':snapshot['issue']['number'],
             'issue_token':snapshot['token'],'repository_id':snapshot['repository_id'],
             'rules_sha256':self.rules['rules_sha256'],'spec_sha256':fingerprint(self.spec),
             'policy_binding':binding,'environment_grant_sha256':grant['grant_sha256'],
             'source_before':before,'run_id':run_id,'owner_process':owner,'pause_requested':False,
             'created_at':now(),'previous_execution':previous['id'] if previous else None}
        if _successor:
            row.update(continuation_kind='version_successor',successor_id=_successor['id'],
                       parent_execution_id=_successor['parent_execution_id'],
                       parent_challenge=_successor['parent_challenge'],
                       parent_report_sha256=_successor['parent_report_sha256'],
                       successor_delivery_sha256=_successor['delivery_sha256'])
        if _environment_retry:
            require(self.environment_retry_preview(previous['id'])==_environment_retry,
                    'Environment retry preview changed before claim')
            row.update(continuation_kind='environment_retry',
                       previous_report_sha256=_environment_retry['report_sha256'])
        if _review_retry:
            require(self.review_revision_preview(previous['id'],_review_retry['review_context']['reason'])==_review_retry,
                    'Review revision preview changed before claim')
            row.update(continuation_kind='review_revision',previous_report_sha256=_review_retry['report_sha256'],
                       review_origin_challenge=_review_retry['execution_challenge'],review_context=_review_retry['review_context'])
        elif previous and previous.get('review_context'):
            from .issue_review import verified_review_context
            issue=snapshot['issue']['title']+'\n'+snapshot['issue']['body']
            if callable(getattr(self,'maintenance_request',None)):issue=self.maintenance_request()
            context=verified_review_context(previous['review_context'],issue,before['head'],previous['task_id'])
            require(before==previous['source_before'] and callable(approval),
                    'Paused review needs unchanged source and a new approval callback')
            row.update(continuation_kind='review_revision',review_context=context,
                       review_origin_challenge=previous['review_origin_challenge'])
        with self.registry.transaction() as db:
            head=self._load('issue_head',job['issue_key'],db)
            require(head and head['token']==snapshot['token'],'Investigation version advanced before claim')
            existing=self._load('issue_execution_head',job['issue_key'],db)
            if _successor:
                parent_now=self._load(KIND,_successor['parent_execution_id'],db)
                intent=self._load('issue_successor',_successor['id'],db)
                require(existing and existing['execution_id']==_successor['parent_execution_id']
                        and parent_now and fingerprint(parent_now)==_successor['parent_challenge']
                        and intent and intent['status']=='prepared'
                        and fingerprint(intent)==_successor['challenge']
                        and intent.get('execution_id') is None
                        and intent['new_token']==snapshot['token'],
                        'Version successor/parent claim CAS failed; retain the old execution')
            elif not previous and existing:
                return {'status':'duplicate_no_replay','execution_id':existing['execution_id']}
            if previous:
                actual=self._load(KIND,previous['id'],db)
                require(actual and fingerprint(actual)==_resume[1]
                        and existing and existing['execution_id']==previous['id'],'Resume version advanced')
                require(previous['policy_binding']['binding_sha256']==binding['binding_sha256']
                        and previous['environment_grant_sha256']==grant['grant_sha256'],
                        'Resume scope/environment changed; explicit scope review required')
            active=self._load('issue_execution_owner',self.rules['repository_id'],db)
            if active and active.get('execution_id'):
                existing_owner=self._load(KIND,active['execution_id'],db)
                require(existing_owner and existing_owner['status'] not in ACTIVE,
                        'Another original execution owns this repository; inspect/pause it first')
            require(db.execute("SELECT count(*) FROM objects WHERE kind=?",(KIND,)).fetchone()[0]<100,
                    'Issue execution journal capacity reached')
            self.registry.put(KIND,identity,row,db)
            self.registry.put('issue_execution_head',job['issue_key'],{'execution_id':identity},db)
            self.registry.put('issue_execution_owner',self.rules['repository_id'],{'execution_id':identity},db)
            self.registry.put('run',run_id,{'run_id':run_id,'issue_execution_id':identity},db)
            if _successor:
                intent.update(status='claimed',execution_id=identity,claimed_at=now(),
                              revision=intent['revision']+1)
                self.registry.put('issue_successor',intent['id'],intent,db)
        control=JobControl(lambda message:None)
        with self.lock:
            self.controls[identity]=control
        provider._cancel_event=control.cancel
        previous_progress=provider._progress_callback
        def progress(value):
            if previous_progress:previous_progress(value)
            control.check()
        # Pi's existing live transport consumes cancellation only with a
        # progress callback. Keep prior reporting while enabling owned stop.
        provider._progress_callback=progress
        issue=snapshot['issue']['title']+'\n'+snapshot['issue']['body']
        if callable(getattr(self,'maintenance_request',None)):issue=self.maintenance_request()
        try:
            self._fresh(row,window)
            dispatch=None
            if callable(getattr(self,'dispatch_fence',None)):
                dispatch=self.dispatch_fence(row,lambda:self._fresh(row,window),max_attempts)
                if dispatch is not None:
                    self._update(identity,correlation=dispatch)
                    if not dispatch['dispatch_allowed']:
                        self._update(identity,status='related_no_dispatch',finished_at=now())
                        return self.view(identity)
            durable=DurableProvider(provider,self.registry)
            def before_model():
                original=getattr(self,'model_guard',None)
                if original:original()
                if dispatch:
                    budget=self.registry.budget()
                    require(budget['requests_reserved_unknown']==0 and budget['requests_remaining']>0,
                            'Original correlated dispatch has unknown or exhausted consumption')
                    self._fresh(row,window)
                    self.dispatch_verify(dispatch)
            durable.before_model=before_model
            planner=TaskPlanner(self.registry,self.source.root,policy,provider,
                {'issue_execution':binding['binding_sha256'],'sources':self.source_scope},
                sources_guard=lambda _:self._fresh(row,window))
            planner.use_controller_read_roots(self.rules['read_roots'])
            durable.task_planner=planner
            from .controller_memory import attach_issue
            memory=attach_issue(self,row,policy,durable,planner,lambda:self._fresh(row,window),self.source_scope)
            reference=None
            if previous:
                link=self.registry.load('pi_task_link').get('run:'+previous['run_id'])
                if link:
                    planner.restore_controller_read_scope(link['task_id'])
                    scope,_=planner.current_scope(issue)
                    prior=planner.store._load(link['task_id'],scope)
                    if prior['revision']:
                        memory.registry.reference_task=prior['task_id']
                        reference=planner.reference('run',previous['run_id'],issue)
                    else:
                        sessions=planner.store.directory(link['task_id'])/'sessions'
                        require(not sessions.exists() or not any(sessions.iterdir()),
                                'Uncommitted initial Native history needs inspection; never replay it')
            task_id=planner.begin(issue,reference)
            if row.get('review_context'):
                require(task_id==row['review_context']['task_id'],'Review cannot move to another Native task')
                provider._review_context=row['review_context']
            planner.link('run',run_id)
            self._update(identity,status='running',task_id=task_id)
            def phase(value):
                control.phase(value)
                current=self.view(identity)
                if current['pause_requested']:
                    raise OperationStopped('Issue execution paused')
            def approve(plan):
                self._fresh(row,window)
                decision=approval(plan)
                self._fresh(row,window)
                return decision
            issue_binding={'execution_id':identity,'issue_number':row['issue_number'],
                'issue_token':row['issue_token'],'policy_binding_sha256':binding['binding_sha256'],
                'task_id':task_id,'rules_sha256':row['rules_sha256']}
            record=repair(self.source.root,issue,durable,policy,'approval' if _environment_retry or row.get('review_context') else self.rules['mode'],self.runs,
                          approval=approve if approval else None,actor=self.rules['actor'],
                          max_attempts=max_attempts,run_id=run_id,freshness_guard=lambda:self._fresh(row,window),
                          operation_control=phase,environment_factory=factory,issue_binding=issue_binding)
            state=('paused' if record.get('operator_stop') else 'stale' if record.get('error','').startswith('IssueChanged:')
                   else 'awaiting-review' if record['status']=='candidate_ready' else 'failed_no_replay')
            if state=='awaiting-review':
                self._fresh(row,window)
            evidence=record.get('provider_evidence',{})
            self._update(identity,status=state,workflow_status=record['status'],finished_at=now(),
                         model_evidence={'attempt_id':evidence.get('attempt_id'),
                                         'requests':evidence.get('requests'),
                                         'consumption':evidence.get('consumption'),
                                         'evidence_sha256':fingerprint(evidence)},api_reads=window['calls'])
            try:
                memory.capture(run_id)
            except SafetyError as error:
                self.registry.put('experience_capture',run_id,{'status':'blocked','reason':redact(str(error))})
        except BaseException as error:
            state=('paused' if isinstance(error,OperationStopped) else 'stale' if isinstance(error,IssueChanged)
                   else 'interrupted' if isinstance(error,(KeyboardInterrupt,SystemExit)) else 'failed_no_replay')
            self._update(identity,status=state,
                         error_type=type(error).__name__,error=redact(str(error)),finished_at=now(),api_reads=window['calls'])
            if isinstance(error,(KeyboardInterrupt,SystemExit)):
                raise
        finally:
            provider._cancel_event=None
            provider._progress_callback=previous_progress
            with self.lock:
                self.controls.pop(identity,None)
            with self.registry.transaction() as db:
                active=self._load('issue_execution_owner',self.rules['repository_id'],db)
                if active and active.get('execution_id')==identity:
                    self.registry.put('issue_execution_owner',self.rules['repository_id'],{'execution_id':None},db)
        return self.view(identity)
