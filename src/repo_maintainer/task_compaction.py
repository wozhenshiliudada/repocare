"""Controller-owned between-turn Pi compaction, pinned configuration and original budget."""
import json
import uuid
from .compaction_config import validate_config,default_config
from .git import RepositoryGateway
from .models import digest
from .pi_auxiliary import invoke
from .runtime_limits import MAX_OUTPUT_TOKENS
from .store import now,redact
from .task_session import fingerprint,require,TASK_ID

ERRORS=(ValueError,KeyError,TypeError,OSError)


def native_compact(provider,source,guard):
    limits=provider.effective_model_limits(context_policy=source['config'])
    summary_output=min(source['config']['summary_max_output_tokens'],limits['max_output_tokens']) if limits['limits_explicit'] else source['config']['summary_max_output_tokens']
    def validate(data,evidence,_context):
        receipt=data['compaction']
        fields={'task_id','file','session_id','previous_leaf_id','leaf_id','entry_sha256','previous_file_sha256',
                'file_sha256','config_sha256','context_sha256','summary_sha256'}
        require(isinstance(receipt,dict) and set(receipt)==fields and evidence.get('active_tools')==[]
                and evidence.get('compaction_context_sha256')==source['context_sha256']==receipt['context_sha256']
                and evidence.get('compaction_entry_sha256')==receipt['entry_sha256']
                and evidence.get('compaction_max_output_tokens')==summary_output
                and receipt['config_sha256']==fingerprint(source['config']) and receipt['task_id']==source['task_id'],
                'Native compaction output differs from original input')
        return receipt
    return invoke(provider,'compact.mjs','task_compaction',{'source':source},guard,validate)


class TaskCompaction:
    def __init__(self,session):
        self.session=session;self.planner=session.task_planner;self.registry=session.registry

    def settings(self):
        saved=self.registry.load('context_settings').get('default')
        return validate_config(saved['config']) if saved else default_config(self.session.provider.inner)

    def settings_revision(self):
        return fingerprint({'settings':self.registry.load('context_settings'),'session':self.session.token})

    def configure(self,config,revision):
        validate_config(config)
        require(revision==self.settings_revision(),'Context settings changed; review again')
        self.registry.put('context_settings','default',{'schema_version':1,'config':config})
        return {'context_settings_for_future_tasks':config}

    def begin(self,record,continuation):
        if continuation:return
        config=self.settings()
        self.registry.put('pi_context_policy',record['task_id'],{'schema_version':1,'task_id':record['task_id'],
            'scope_sha256':fingerprint(record['scope']),'config':config,'config_sha256':fingerprint(config)})

    def policy(self,record):
        pin=self.registry.load('pi_context_policy').get(record['task_id'])
        receipts=[a.get('evidence',{}).get('context_policy_sha256') for a in self.registry.load('attempt').values()
                  if a.get('evidence',{}).get('task_id')==record['task_id'] and a.get('evidence',{}).get('context_policy_sha256')]
        if pin is None:
            require(not receipts and not any(r.get('task_id')==record['task_id'] for r in self.registry.load('pi_compaction_intent').values()),
                    'Original context policy is missing')
            return None  # Existing tasks never silently acquire a new context policy.
        require(set(pin)=={'schema_version','task_id','scope_sha256','config','config_sha256'} and
                type(pin['schema_version']) is int and pin['schema_version']==1 and pin['task_id']==record['task_id'] and
                pin['scope_sha256']==fingerprint(record['scope']) and pin['config_sha256']==fingerprint(pin['config']),
                'Original task context policy changed')
        require(all(sha==pin['config_sha256'] for sha in receipts),'Context policy differs from original planning receipts')
        return validate_config(pin['config'])

    def issue(self,identity):
        require(isinstance(identity,str) and TASK_ID.fullmatch(identity),'Invalid original task identity')
        links=self.registry.load('pi_task_link');issues=[]
        for key,link in links.items():
            if link.get('task_id')!=identity:continue
            kind,_,target=key.partition(':')
            if kind=='plan' and target in self.session.plans:
                issues.append(self.session.plans[target]['view']['plan']['issue'])
            elif kind=='run' and target in self.session.runs:
                from .dashboard import read_json
                issues.append(read_json(self.session._run_dir(target)/'run.json')[0]['issue'])
        require(issues and len(set(issues))==1,'Task issue requires consistent original plan/run links')
        return issues[0]

    def checked(self,identity,issue,repository=None):
        self.session._task_sources(issue)
        scope,_=self.planner.current_scope(issue)
        record=self.planner.store._load(identity,scope)
        if record['revision']:
            source=RepositoryGateway(self.session.repo);branch=''
            if repository is not None and repository.root!=source.root:
                from .task_session import unlinked
                branch=repository.command('symbolic-ref','--short','HEAD').decode().strip()
                run=branch.removeprefix('maintenance/')
                require(branch.startswith('maintenance/') and repository.root==unlinked(self.session._run_dir(run)/'workspace')
                        and repository.common_dir==source.common_dir
                        and self.registry.load('pi_task_link').get('run:'+run)=={'task_id':identity},
                        'Compaction candidate has no original task ownership')
            snapshots=[]
            for key,link in self.registry.load('pi_task_link').items():
                if link.get('task_id')!=identity:continue
                kind,_,target=key.partition(':')
                if kind=='plan' and target in self.session.plans:snapshots.append(self.session.plans[target]['snapshot'])
                elif kind=='run' and target in self.session.runs:
                    from .dashboard import read_json
                    snapshots.append(read_json(self.session._run_dir(target)/'run.json')[0].get('source_before'))
            require(source.snapshot(exclude_branch=branch) in snapshots,'Original task source changed before context operation')
        if self.session.experience:self.session.experience.verify_task(identity,issue)
        if self.session.knowledge:self.session.knowledge.verify_task(identity,issue)
        if self.session.maintenance:self.session.maintenance.verify_task(identity,issue)
        return record

    def detail(self,identity,issue,repository=None):
        record=self.checked(identity,issue,repository);config=self.policy(record)
        require(record['revision']>0,'Task has no completed native checkpoint')
        with self.planner.store.lease(identity,record['scope'],record['revision']) as lease:
            before=lease.descriptor();_,actual=lease._read(record['checkpoint']['file'],before['leaf_id'],before['session_id'])
        from .task_session import MAX_SESSION_BYTES
        append_room=before['file_bytes']+256*1024<MAX_SESSION_BYTES
        budget=self.session.budget_registry.budget()
        pending=any(r.get('task_id')==identity and r.get('status')=='pending'
                    for r in self.registry.load('pi_compaction_intent').values())
        allowed=bool(config and config['enabled'] and record['revision']>=2 and not actual['leaf_is_compaction']
                     and append_room and not pending and not budget['requests_reserved_unknown'] and budget['requests_remaining']>=2)
        body={'task_id':identity,'checkpoint_sha256':fingerprint(record['checkpoint']),'context_bytes_estimate':actual['context_bytes_estimate'],
              'completed_planning_rounds':record['revision'],'config':config,'budget':budget,
              'can_compact':allowed,'last_leaf_is_compaction':actual['leaf_is_compaction'],'pending_intent':pending,
              'native_file_bytes':before['file_bytes'],'native_append_room':append_room}
        return {**body,'revision':fingerprint({'body':body,'session':self.session.token})}

    def execute(self,identity,issue,repository,revision=None):
        preview=self.detail(identity,issue,repository)
        require(preview['can_compact'] and (revision is None or revision==preview['revision']),
                'Compaction preview changed, policy disabled, budget unavailable, or no new completed round')
        record=self.checked(identity,issue,repository);config=self.policy(record)
        original=RepositoryGateway(self.session.repo).snapshot();candidate=repository.snapshot()
        with self.planner.store.lease(identity,record['scope'],record['revision']) as lease:
            before=lease.descriptor()
            body={'schema_version':1,'task_id':identity,'scope_sha256':fingerprint(record['scope']),
                  'checkpoint_sha256':fingerprint(record['checkpoint']),'config':config,
                  'task_session':{'mode':'open','binding':before,'context':record['context']}}
            source={**body,'context_sha256':fingerprint(body)}
            def guard():
                if self.session.control:self.session.control.check()
                current=self.checked(identity,issue,repository)
                require(current==record and self.policy(current)==config and
                        RepositoryGateway(self.session.repo).snapshot()==original and repository.snapshot()==candidate,
                        'Compaction task, configuration, current source or candidate changed')
                if self.planner.feedback_guard:self.planner.feedback_guard(repository)
            guard();operation=uuid.uuid4().hex
            self.registry.put('pi_compaction_intent',operation,{'task_id':identity,'source_sha256':source['context_sha256'],
                'source':source,'checkpoint_sha256':fingerprint(record['checkpoint']),'status':'pending','started_at':now()})
            receipt=self.session.provider.compact(source,guard)
            guard();evidence=self.session.provider.evidence
            require(receipt['context_sha256']==source['context_sha256'],'Compaction input changed')
            updated=lease.commit_compaction(receipt,before,evidence['attempt_id'],fingerprint(evidence))
            self.registry.put('pi_compaction_intent',operation,{'task_id':identity,'source_sha256':source['context_sha256'],
                'source':source,'checkpoint_sha256':fingerprint(record['checkpoint']),'status':'committed','attempt_id':evidence['attempt_id'],
                'entry_id':receipt['leaf_id'],'finished_at':now()})
        return {'task_id':identity,'planning_rounds':updated['revision'],'compaction_entry_id':receipt['leaf_id'],
                'requests':evidence['requests'],'runtime_authorized':False}

    def next_input(self,identity,issue,repository,feedback):
        # Conservative byte proxy plus serialized current data; not a model tokenizer claim.
        size=16000+len(json.dumps({'issue':issue,'tests':self.session.policy.allowed_tests,
                                  'paths':self.planner.provider.task_identity()[0]['paths']},ensure_ascii=False).encode())*2
        if feedback is not None:size+=len(json.dumps(feedback,ensure_ascii=False).encode())*2+50000
        if self.planner.feedback:size+=len(json.dumps(self.planner.feedback,ensure_ascii=False).encode())*2
        for memory in (self.session.experience,self.session.knowledge,self.session.maintenance):
            if memory:
                value=memory.context(issue,repository,identity)
                if value:size+=len(json.dumps(value,ensure_ascii=False).encode())*2
        if self.session.memory.binding:size+=50000
        if self.session.structure.binding:size+=100000
        return size

    def before_turn(self,identity,issue,repository,feedback=None):
        record=self.checked(identity,issue,repository);config=self.policy(record)
        resolve=getattr(self.planner.provider,'effective_model_limits',None)
        limits=resolve(context_policy=config) if resolve else {'limits_explicit':False,'max_output_tokens':MAX_OUTPUT_TOKENS}
        if config is None and not limits['limits_explicit']:return None
        estimate=0
        if record['revision']:
            detail=self.detail(identity,issue,repository);estimate=detail['context_bytes_estimate']
            from .task_session import MAX_SESSION_BYTES
            require(detail['native_file_bytes']+4*1024*1024<MAX_SESSION_BYTES,
                    'Native history capacity is near its read limit; original checkpoint retained, no planning request reserved')
            require(not detail['pending_intent'],'Inspect the original pending compaction before another planning request')
            abandoned=any(r.get('task_id')==identity and r.get('status')=='abandoned'
                and r.get('checkpoint_sha256')==fingerprint(record['checkpoint']) for r in self.registry.load('pi_compaction_intent').values())
            if config and config['enabled'] and estimate>=config['trigger_tokens'] and detail['can_compact'] and not abandoned:
                self.execute(identity,issue,repository)
                estimate=self.detail(identity,issue,repository)['context_bytes_estimate']
        window=(config or {}).get('context_window_tokens')
        if limits['limits_explicit']:window=min(window or limits['context_window_tokens'],limits['context_window_tokens'])
        if window:
            require(estimate+self.next_input(identity,issue,repository,feedback)+limits['max_output_tokens']<window,
                    'Context capacity is insufficient; original checkpoint retained, no planning request reserved')
        return config

    def intent_revision(self,identity,value):
        return fingerprint({'id':identity,'intent':value,'budget':self.session.budget_registry.budget(),'session':self.session.token})

    def resolve(self,identity,revision,choice):
        from .task_session import strict_json
        intent=self.registry.load('pi_compaction_intent').get(identity)
        require(intent and intent.get('status')=='pending' and revision==self.intent_revision(identity,intent),
                'Compaction recovery challenge changed')
        require(choice in {'reconcile','abandon'},'Invalid compaction recovery choice')
        if choice=='abandon':
            updated={**intent,'status':'abandoned','finished_at':now(),'old_checkpoint_retained':True}
        else:
            source=intent['source'];body={k:v for k,v in source.items() if k!='context_sha256'}
            require(fingerprint(body)==intent['source_sha256']==source['context_sha256'] and source['task_id']==intent['task_id'],
                    'Original compaction input changed')
            require(not self.session.budget_registry.budget()['requests_reserved_unknown'],
                    'Unknown consumption cannot be converted into a completed compaction')
            matches=[(key,a) for key,a in self.registry.load('attempt').items() if a.get('status')=='finished'
                and a.get('evidence',{}).get('purpose')=='task_compaction'
                and a['evidence'].get('compaction_context_sha256')==source['context_sha256']]
            require(len(matches)==1,'Compaction needs one original settled request receipt')
            attempt_id,attempt=matches[0];task=intent['task_id'];issue=self.issue(task);record=self.checked(task,issue)
            require(self.policy(record)==source['config'] and fingerprint(record['scope'])==source['scope_sha256'],
                    'Compaction scope or settings changed')
            before=source['task_session']['binding']
            with self.planner.store.lease(task,record['scope'],record['revision']) as lease:
                registered=self.registry.load('pi_compaction_receipt').get(attempt_id)
                if registered:
                    lease.descriptor()
                    require(record['checkpoint']['leaf_id']==registered['entry_id'],'Task advanced past original compaction')
                    entry_id=registered['entry_id']
                else:
                    require(fingerprint(record['checkpoint'])==intent['checkpoint_sha256'],'Original checkpoint changed before compaction recovery')
                    file=lease._file(record['checkpoint']['file']);raw=lease._bytes(file);entry=strict_json(raw.splitlines()[-1].decode())
                    require(entry.get('type')=='compaction' and fingerprint(entry)==attempt['evidence'].get('compaction_entry_sha256'),
                            'Native compaction differs from the original metered result')
                    receipt={'task_id':task,'file':file.name,'session_id':record['checkpoint']['session_id'],
                        'previous_leaf_id':before['leaf_id'],'leaf_id':entry['id'],'entry_sha256':fingerprint(entry),
                        'previous_file_sha256':before['file_sha256'],'file_sha256':digest(raw),
                        'config_sha256':fingerprint(source['config']),'context_sha256':source['context_sha256'],
                        'summary_sha256':digest(entry['summary'].encode())}
                    lease.commit_compaction(receipt,before,attempt_id,fingerprint(attempt['evidence']));entry_id=entry['id']
            updated={**intent,'status':'committed','attempt_id':attempt_id,'entry_id':entry_id,'reconciled_at':now()}
        with self.registry.transaction() as db:
            row=db.execute('SELECT body FROM objects WHERE kind=? AND id=?',('pi_compaction_intent',identity)).fetchone()
            require(row and json.loads(row[0])==intent,'Compaction intent changed during resolution')
            self.registry.put('pi_compaction_intent',identity,updated,db)
        return {'intent_id':identity,'status':updated['status'],'new_model_requests':0,'budget_unchanged':True}

    def view(self):
        from .models import SafetyError
        result={'tasks':[],'runtime_authorized':False,'settings_revision':self.settings_revision()}
        try:result['settings']=self.settings()
        except (SafetyError,*ERRORS) as error:result['reason']=redact(str(error));return result
        for identity,record in self.registry.load('pi_task').items():
            if not record.get('revision'):continue
            if self.session.active:
                result['tasks'].append({'task_id':identity,'status':'busy','completed_planning_rounds':record.get('revision')});continue
            try:result['tasks'].append({**self.detail(identity,self.issue(identity)),'status':'verified'})
            except (SafetyError,*ERRORS) as error:
                result['tasks'].append({'task_id':identity,'status':'blocked','reason':redact(str(error))})
        result['compactions']=list(self.registry.load('pi_compaction_receipt').values())
        result['pending']= [{'id':i,'task_id':r['task_id'],'revision':self.intent_revision(i,r),
            'can_reconcile':not self.session.budget_registry.budget()['requests_reserved_unknown']}
            for i,r in self.registry.load('pi_compaction_intent').items() if r.get('status')=='pending']
        return result
