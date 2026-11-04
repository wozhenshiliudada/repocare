"""Opt-in Issue observation and admission on the original session queue/ledger."""
from datetime import datetime, timezone
import json
import threading
import time
import uuid

from .issue_intake import IssueReader, fingerprint
from .issue_workbench import metadata, require
from .maintenance_limits import LIMITS
from .models import SafetyError, file_digest
from .store import now


class IssueWatch:
    def __init__(self, workbench):
        self.w=workbench;self.s=workbench.session;self.registry=workbench.registry
        self.clock=time.time;self.stopping=threading.Event();self.thread=None
        self.queued=False;self.next_due=0;self.executing=None;self.validation_active=False
        row=self.current()
        if row and row['status']=='active':
            self.registry.put('issue_watch','current',{**row,'status':'paused','reason':'service_restart'})
        for identity,row in self.registry.load('issue_watch_admission').items():
            if row['status']=='started':
                self.registry.put('issue_watch_admission',identity,{**row,'status':'interrupted_no_replay'})

    def current(self):
        row=self.registry.load('issue_watch').get('current')
        if row:
            require(row['sha256']==fingerprint(row['core']) and row['status'] in {'active','paused','revoked'},
                    'Issue 接单授权记录失效；不重建或重放')
        return row

    def binding(self):
        config=self.w._config();inner=self.s.provider.inner
        identity=inner.task_identity()[1] if callable(getattr(inner,'task_identity',None)) else {}
        path=getattr(inner,'config_file',None)
        return {'configuration_sha256':config['sha256'],'metadata_sha256':fingerprint(metadata(self.registry)),
                'model':identity,'provider_config_sha256':file_digest(path) if path else None,
                'runtime_sha256':self.s.runtime.loaded,'request_ceiling':self.registry.budget()['request_budget']}

    def view(self):
        row=self.current();status='not_configured' if row is None else row['status']
        if row and status=='active':
            try:
                if not row['issued_epoch']<=self.clock()<row['core']['expires_epoch']:status='expired'
                elif row['core']['binding']!=self.binding():status='binding_changed'
                elif self.registry.budget()['requests_reserved_unknown']:status='unknown_consumption'
                elif self.registry.budget()['requests_remaining']<=0:status='exhausted'
            except (SafetyError,OSError,KeyError,ValueError):status='binding_changed'
        return {'authorization':row,'status':status,'active':status=='active',
                'scan':self.registry.load('issue_watch_scan').get('current'),
                'observations':list(self.registry.load('issue_watch_observation').values()),
                'admissions':list(self.registry.load('issue_watch_admission').values())}

    def preview(self,payload):
        require(set(payload)=={'expires_at','interval_seconds','max_attempts'},'Issue 接单预览字段不符')
        self.s.runtime.require_current();config=self.w._config()
        require(isinstance(payload['expires_at'],str) and len(payload['expires_at'])<=32,'请选择明确到期时间')
        expiry=datetime.fromisoformat(payload['expires_at'].replace('Z','+00:00'))
        require(expiry.tzinfo is not None and expiry.utcoffset().total_seconds()==0,'请选择UTC到期时间')
        epoch=int(expiry.timestamp());require(60<=epoch-self.clock()<=7*86400,'有效期须在1分钟至7天内')
        interval=payload['interval_seconds'];attempts=payload['max_attempts']
        require(type(interval) is int and 60<=interval<=3600,'检查间隔须为60至3600秒')
        require(type(attempts) is int and 1<=attempts<=LIMITS['candidate_max_rounds'],'候选轮数超出原范围')
        budget=self.registry.budget()
        require(not budget['requests_reserved_unknown'] and budget['requests_remaining']>0,'原消费未知或额度耗尽')
        row=self.current()
        core={'binding':self.binding(),'budget':budget,'configuration':config,
              'expires_at':datetime.fromtimestamp(epoch,timezone.utc).isoformat(),'expires_epoch':epoch,
              'interval_seconds':interval,'max_attempts':attempts if config['mode']=='auto' else 1,
              'previous':{'id':row['id'],'status':row['status']} if row else None,
              'semantics':'first-time eligible Issues only; existing work and changed versions require explicit follow-up',
              'push_or_merge_or_close':False}
        return {'preview':core,'revision':self.w.challenge(core)}

    def configure(self,payload):
        require(set(payload)=={'preview','revision','confirmed'} and payload['confirmed'] is True,'须明确确认持续接单的模型及本地执行许可')
        value=payload['preview']
        require(isinstance(value,dict) and len(json.dumps(value).encode())<=32768
                and payload['revision']==self.w.challenge(value),'接单预览已失效')
        fresh=self.preview({k:value[k] for k in ('expires_at','interval_seconds','max_attempts')})['preview']
        require(value==fresh,'原配置、额度或授权已变化，请重新预览')
        row={'id':uuid.uuid4().hex,'core':fresh,'sha256':fingerprint(fresh),'status':'active',
             'issued_epoch':self.clock(),'created_at':now()}
        with self.registry.transaction() as db:
            old=db.execute("SELECT body FROM objects WHERE kind='issue_watch' AND id='current'").fetchone()
            old=json.loads(old[0]) if old else None
            require(({'id':old['id'],'status':old['status']} if old else None)==fresh['previous'],'原接单授权变化')
            budget=db.execute('SELECT ceiling,used,held FROM budget WHERE id=1').fetchone()
            require(budget==tuple(fresh['budget'][k] for k in ('request_budget','requests_used','requests_reserved_unknown')),'原消费变化')
            self.registry.put('issue_watch_history',row['id'],row,db)
            self.registry.put('issue_watch','current',row,db)
        self.next_due=0
        if self.thread is None:
            self.thread=threading.Thread(target=self._timer,daemon=True,name='issue-observer');self.thread.start()
        return self.view()

    def control(self,action,payload,request_id):
        require(set(payload)=={'grant_id'} and action in {'issue_watch_pause','issue_watch_revoke'},'接单控制字段不符')
        signature=fingerprint([action,payload])
        with self.registry.transaction() as db:
            old=db.execute("SELECT body FROM objects WHERE kind='issue_watch_control' AND id=?",(request_id,)).fetchone()
            if old:
                old=json.loads(old[0]);require(old['signature']==signature,'控制编号不能改变内容');return old['result']
            saved=db.execute("SELECT body FROM objects WHERE kind='issue_watch' AND id='current'").fetchone()
            require(saved is not None,'尚未配置持续Issue接单')
            row=json.loads(saved[0])
            require(row['id']==payload['grant_id'],'接单授权版本已变化')
            row.update(status='revoked' if action.endswith('revoke') or row['status']=='revoked' else 'paused')
            result={'status':row['status'],'grant_id':row['id']}
            self.registry.put('issue_watch','current',row,db)
            self.registry.put('issue_watch_control',request_id,{'signature':signature,'result':result},db)
        self.pause_active()
        return result

    def pause_active(self):
        if not self.executing:return
        if self.validation_active and self.s.control:self.s.control.cancel.set()
        for service in list(self.w.services.values()):
            for identity in list(service.controls):
                row=service.view(identity)
                if row['investigation_id']==self.executing and row['status'] in {'claimed','running'}:
                    try:service.request_pause(identity,row['challenge'])
                    except SafetyError:pass  # A concurrently completed result stays preserved.

    def guard(self,grant_id):
        self.s.runtime.require_current()
        row=self.current()
        require(row and row['id']==grant_id and row['status']=='active'
                and row['issued_epoch']<=self.clock()<row['core']['expires_epoch']
                and row['core']['binding']==self.binding() and not self.s.closed,'持续接单授权已暂停、到期或失效')
        return row

    def model_guard(self,grant_id):
        self.guard(grant_id);budget=self.registry.budget()
        require(not budget['requests_reserved_unknown'] and budget['requests_remaining']>0,'原消费未知或额度耗尽，不开始新模型回合')

    def scan(self):
        require(self.registry.path.stat().st_size<30*1024*1024,'原日志接近容量；保留历史，不继续接单')
        view=self.view();require(view['active'],'持续Issue接单未启用或已失效')
        row=view['authorization'];config=self.w._config();reader=IssueReader(self.w.connection,config['spec'],registry=self.w.registry)
        listed=reader.open_issues()
        scan={'at':now(),'complete':listed['complete'],'open_count':len(listed['issues']),'error':None}
        self.registry.put('issue_watch_scan','current',scan)
        require(listed['complete'],'Issue分页不完整；不启动修复')
        known=self.registry.load('issue_watch_observation')
        numbers=sorted(set([v['number'] for v in listed['issues']]+[v['number'] for v in known.values()]))
        cursor=self.registry.load('issue_watch_cursor').get('current',{}).get('number',0)
        selected=([n for n in numbers if n>cursor]+[n for n in numbers if n<=cursor])[:5]
        ready=[]
        for number in selected:
            self.guard(row['id'])
            # A separate bounded snapshot window; at most five Issues per scan.
            snapshot=IssueReader(self.w.connection,config['spec'],registry=self.w.registry).snapshot(number)
            if 'token' not in snapshot:continue
            key=fingerprint([snapshot['repository_id'],snapshot['issue']['id']])
            require(key in known or len(known)<100,'Issue观察达到100项；保留历史，不重建额度')
            observation={'id':key,'number':number,'token':snapshot['token'],'status':snapshot['status'],
                         'updated_at':now(),'head_sha':snapshot['head_sha']}
            self.registry.put('issue_watch_observation',key,observation)
            known[key]=observation
            old=self.registry.load('issue_watch_admission').get(key)
            if old:
                if old['token']!=snapshot['token']:
                    self.registry.put('issue_watch_admission',key,{**old,'source_changed':True})
                elif (old.get('status') in {'awaiting_validation_review','validation_interrupted_no_replay'}
                      and not old.get('source_changed') and old.get('grant_id')==row['id']):
                    from .issue_validation import service
                    proposal=service(self.w,old['investigation_id'])
                    if proposal.accepted() is not None:
                        ready.append({'id':key,'number':number,'token':snapshot['token'],'grant_id':row['id'],
                                      'validation_resume':True})
                continue
            if snapshot['status']!='ready':continue
            existing=[v for v in self.registry.load('issue_diagnosis').values()
                      if v['snapshot']['repository_id']==snapshot['repository_id']
                      and v['snapshot']['issue']['id']==snapshot['issue']['id']]
            if existing:
                self.registry.put('issue_watch_admission',key,{'id':key,'number':number,'token':snapshot['token'],
                    'status':'existing_work_no_replay','investigation_id':existing[-1]['id']})
                continue
            ready.append({'id':key,'number':number,'token':snapshot['token'],'grant_id':row['id']})
        if selected:self.registry.put('issue_watch_cursor','current',{'number':selected[-1]})
        return ready

    def process(self,receipt):
        grant=self.guard(receipt['grant_id']);key=receipt['id']
        config=self.w._config()
        fresh=IssueReader(self.w.connection,config['spec'],registry=self.w.registry).snapshot(receipt['number'])
        require(fresh.get('token')==receipt['token'] and fresh['status']=='ready','Issue已变化，先重新观察')
        with self.registry.transaction() as db:
            old=db.execute("SELECT body FROM objects WHERE kind='issue_watch_admission' AND id=?",(key,)).fetchone()
            old=json.loads(old[0]) if old else None
            if old and not receipt.get('validation_resume'):return old
            if receipt.get('validation_resume'):
                require(old and old['status'] in {'awaiting_validation_review','validation_interrupted_no_replay'}
                    and not old.get('source_changed') and all(old[k]==receipt[k] for k in ('id','token','number','grant_id')),
                    '原案例接单记录已变化；不能重放')
                row={**old,'status':'started','resumed_after_validation_at':now()}
            else:row={**receipt,'status':'started','started_at':now()}
            self.registry.put('issue_watch_admission',key,row,db)
        try:
            self.guard(receipt['grant_id'])
            if receipt.get('validation_resume'):
                from .issue_validation import service
                require(service(self.w,row['investigation_id']).accepted() is not None,'需要原案例审阅确认')
                job=self.registry.load('issue_diagnosis')[row['investigation_id']]
            else:job=self.w.operate('issue_investigate',{'number':receipt['number']})
            require(job['status']=='investigated' and job['snapshot']['token']==receipt['token'],'调查未形成当前可执行结果')
            row['investigation_id']=job['id'];self.executing=job['id']
            self.w.watch_context={'grant_id':receipt['grant_id'],'investigation_id':job['id'],
                                  'max_attempts':grant['core']['max_attempts']}
            prepared=self.w.environment(job['id'])
            if prepared.get('status') in {'validation_needed','validation_review_required'}:
                from .issue_validation import service
                proposal=service(self.w,job['id']);preview=proposal.preview()
                existing=self.registry.load('validation_proposal').get(proposal.identity)
                if existing is None:
                    self.validation_active=True
                    self.model_guard(receipt['grant_id'])
                    existing=proposal.propose(preview['revision'])
                row.update(status='awaiting_validation_review',validation_id=proposal.identity,
                           result_status=existing['status'],finished_at=now())
                self.registry.put('issue_watch_admission',key,row)
                return row
            result=self.w.operate('issue_execute',{'investigation_id':job['id'],'revision':prepared['revision']})
            row.update(status='finished',investigation_id=job['id'],execution_id=result.get('id',result.get('execution_id')),
                       result_status=result['status'],finished_at=now())
        except Exception as error:
            row.update(status='validation_interrupted_no_replay' if self.validation_active else 'blocked_no_replay',
                       error_type=type(error).__name__,finished_at=now())
        finally:
            self.executing=None;self.w.watch_context=None;self.validation_active=False
        self.registry.put('issue_watch_admission',key,row)
        return row

    def check(self):
        ready=self.scan()
        return self.process(ready[0]) if ready else {'status':'observed','model_requests':0}

    def _dispatch(self):
        try:
            if self.s.closed or self.s.active or not self.view()['active']:return
            ready=self.scan()
            if ready:
                with self.s.lock:
                    if not self.s.closed and not self.s.active:
                        self.s.submit(uuid.uuid4().hex,'issue_watch_process',ready[0])
        except Exception as error:
            self.registry.put('issue_watch_scan','current',{'at':now(),'complete':False,'error':type(error).__name__})
        finally:
            self.queued=False
            row=self.current();self.next_due=time.monotonic()+(row['core']['interval_seconds'] if row else 60)

    def _timer(self):
        while not self.stopping.wait(1):
            row=self.current()
            if row and row['status']=='active' and not row['issued_epoch']<=self.clock()<row['core']['expires_epoch']:
                self.pause_active()
            if self.s.closed or self.s.active or self.queued or time.monotonic()<self.next_due:continue
            if not self.view()['active']:continue
            self.queued=True
            try:self.s.executor.submit(self._dispatch)
            except RuntimeError:self.queued=False;return

    def stop(self):
        self.stopping.set()
        if self.thread:self.thread.join()
