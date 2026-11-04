"""An explicit, bounded metadata transition; original failure receipts remain immutable."""
import json
import os
from datetime import datetime

from .dashboard import read_json
from .store import now
from .task_session import fingerprint,require

KIND='pi_failure_cleanup'
MUTABLE={'status','finished_at','events','git_commands','recovery'}

def record_binding(record):
    return {'protected_sha256':fingerprint({k:v for k,v in record.items() if k not in MUTABLE}),
            'status':record['status'],'finished_at':record['finished_at'],
            'event_count':len(record['events']),'events_sha256':fingerprint(record['events']),
            'command_count':len(record['git_commands']),'commands_sha256':fingerprint(record['git_commands'])}


class FeedbackCleanup:
    def __init__(self,session):
        self.session=session

    def _intent(self,identity):
        return self.session.registry.load(KIND).get(identity)

    def begin(self,identity):
        from .task_feedback import KIND as FEEDBACK,SEAL_KIND
        session=self.session
        if not session.registry.load(FEEDBACK).get(identity) and not session.registry.load(SEAL_KIND).get(identity):
            return True  # No persisted failure memory; retain the original cleanup path.
        record,_=read_json(session._run_dir(identity)/'run.json')
        session._task_sources(record['issue'])
        old=self._intent(identity)
        if old:
            require(old['status']=='completed','Failure cleanup is pending; inspect and reconcile it first')
            session.failure_feedback.prepare(identity)
            return False  # A verified completed cleanup is idempotent, including its report bytes.
        receipt=session.failure_feedback.prepare(identity)
        require(receipt and session.registry.load(SEAL_KIND).get(identity)==receipt,
                'Failure cleanup needs its original complete seal')
        require(not session.registry.load('run').get(identity,{}).get('resolution'), 'Failure registration is closed')
        require(session.registry.budget()['requests_reserved_unknown']==0,'Unknown consumption blocks failure cleanup')
        binding=record_binding(record)
        intent={'schema_version':1,'run_id':identity,'status':'pending','before_receipt':receipt,
                'before_record':binding,'before_record_sha256':fingerprint(binding),'actor':'local-browser-operator','time':now()}
        with session.registry.transaction() as db:
            require(not db.execute('SELECT 1 FROM objects WHERE kind=? AND id=?',(KIND,identity)).fetchone(),
                    'Failure cleanup intent already exists')
            session.registry.put(KIND,identity,intent,db)
        return True

    def _validate(self,identity,intent,current):
        from .task_feedback import KIND as FEEDBACK,SEAL_KIND
        session=self.session
        require(isinstance(intent,dict) and intent.get('schema_version')==1 and intent.get('run_id')==identity and
                intent.get('status') in {'pending','completed'},'Invalid failure cleanup intent')
        old=intent['before_record'];receipt=intent['before_receipt']
        require(fingerprint(old)==intent['before_record_sha256'] and
                session.registry.load(SEAL_KIND).get(identity)==receipt and
                session.registry.load(FEEDBACK).get(identity)==receipt,'Original failure cleanup authority changed')
        after,sha=read_json(session._run_dir(identity)/'run.json')
        require(fingerprint({k:v for k,v in after.items() if k not in MUTABLE})==old['protected_sha256'],
                'Cleanup changed protected failure facts')
        require(current and current['payload']==receipt['payload'] and current['payload_sha256']==receipt['payload_sha256'] and
                current['execution_sha256']==receipt['execution_sha256'] and current['run_sha256']==sha,
                'Cleanup changed actual failure feedback or execution evidence')
        events=after.get('events',[]);count=old['event_count']
        appended=events[count:]
        require(fingerprint(events[:count])==old['events_sha256'] and len(appended)<=1 and
                all(set(e)=={'time','stage'} and e['stage']=='discarded' for e in appended),
                'Cleanup events are not the authorized append-only transition')
        require(after['status']==('discarded' if appended else old['status']) and
                after.get('recovery')=={'result':'candidate_removed','source_reset':False},'Invalid cleanup completion state')
        try:
            end=datetime.fromisoformat(after['finished_at']);begin=datetime.fromisoformat(old['finished_at'])
            require(end.tzinfo is not None and begin.tzinfo is not None and end>=begin,
                    'Cleanup completion time moved backwards or lost timezone')
        except (ValueError,TypeError):
            require(False,'Invalid cleanup completion time')
        commands=after.get('git_commands',[]);before=old['command_count']
        require(fingerprint(commands[:before])==old['commands_sha256'],'Cleanup rewrote earlier Git audit')
        # A sealed failure has no candidate or publication; cleanup only queries the absent owned ref.
        prefix=['git','-c','core.hooksPath='+os.devnull,'-c','core.fsmonitor=false',
                '-c','commit.gpgsign=false','-c','core.quotepath=false','-C',str(session.repo)]
        prefixes=[prefix,['git','-c','core.longpaths=true',*prefix[1:]]]
        sequence=[['show-ref','--verify','--hash','refs/heads/maintenance/'+identity],
                  ['ls-files','-z','--cached','--others','--exclude-standard'],
                  ['rev-parse','--path-format=absolute','--git-path','index'],
                  ['for-each-ref','--format=%(refname) %(objectname)','refs/heads'],
                  ['rev-parse','HEAD'],['symbolic-ref','-q','HEAD'],
                  ['status','--porcelain=v1','-z','--untracked-files=all']]
        appended_commands=commands[before:]
        # Completed historical receipts used the pre-longpaths prefix. Both
        # exact sequences are read-only; never accept arbitrary Git options or
        # a mixture of prefixes within one cleanup transition.
        require(len(appended_commands)<=len(sequence) and any(all(c.get('command')==p+sequence[i] and
                c.get('returncode') in ({1,128} if i==0 else {0,1} if i==5 else {0}) and
                (i!=0 or c.get('returncode')!=128 or c.get('stderr','').strip()==
                    "fatal: 'refs/heads/maintenance/"+identity+"' - not a valid ref") and
                c.get('stdin_sha256') is None and c.get('alternate_index') is None for i,c in enumerate(appended_commands)) for p in prefixes),
                'Cleanup Git audit exceeds the authorized read-only ref and snapshot sequence')
        return after

    def effective(self,identity,stored,current):
        intent=self._intent(identity)
        if intent is None:
            require(stored==current,'Failure feedback checkpoint missing or changed; inspect original evidence')
        else:
            require(intent.get('status')=='completed','Failure cleanup transition unfinished; inspect and reconcile it')
            require(stored==intent['before_receipt'] and intent.get('after_receipt')==current,
                    'Failure cleanup transition receipt changed')
            self._validate(identity,intent,current)
        return current

    def preview(self,identity):
        session=self.session;intent=self._intent(identity)
        require(intent and intent.get('status')=='pending','No pending controlled failure cleanup')
        require(not session.registry.load('run').get(identity,{}).get('resolution'),'Failure registration is closed')
        record,_=read_json(session._run_dir(identity)/'run.json');session._task_sources(record['issue'])
        require(fingerprint({k:v for k,v in record.items() if k not in MUTABLE})==intent['before_record']['protected_sha256'],
                'Cleanup changed protected failure facts')
        current=session.failure_feedback.build(identity)
        self._validate(identity,intent,current)
        budget=session.registry.budget()
        require(budget['requests_reserved_unknown']==0,'Unknown consumption blocks cleanup reconciliation')
        task=session.task_planner.reference('run',identity,record['issue'])
        return {'revision':fingerprint({'intent':intent,'current':current,'task':task,'budget':budget,'session':session.token}),
                'run_id':identity,'model_requests':0,'current':current,'intent':intent}

    def finish(self,identity):
        if self._intent(identity) is None:
            return
        value=self.preview(identity)
        self._commit(identity,value)

    def _commit(self,identity,value):
        with self.session.registry.transaction() as db:
            row=db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(KIND,identity)).fetchone()
            require(row and json.loads(row[0])==value['intent'],'Failure cleanup intent changed during reconciliation')
            # Rehash both files before the metadata binding commit. The enclosing original run lock is retained.
            directory=self.session._run_dir(identity)
            require(read_json(directory/'run.json')[1]==value['current']['run_sha256'] and
                    read_json(directory/'repair-execution.json')[1]==value['current']['execution_sha256'],
                    'Failure evidence changed during cleanup reconciliation')
            self.session.registry.put(KIND,identity,{**value['intent'],'status':'completed','after_receipt':value['current'],
                'reconciled_at':now(),'reconciliation_sha256':value['revision']},db)

    def reconcile(self,identity,revision):
        from .recovery import operation_lock
        with operation_lock(self.session._run_dir(identity)):
            value=self.preview(identity)
            require(value['revision']==revision,'Cleanup evidence or session changed; inspect again')
            self._commit(identity,value)
        return {'run_id':identity,'feedback_cleanup_reconciled':True,'model_requests':0,'new_approval_required':True}
