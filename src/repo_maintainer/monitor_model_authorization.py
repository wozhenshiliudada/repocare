"""Explicit future-event model authorization, on the original monitor ledger."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
import threading
import time
import uuid

from .dashboard import unlinked
from .issue_intake import fingerprint
from .maintenance_limits import LIMITS
from .maintenance_rules import require
from .models import Policy, file_digest, SafetyError
from .monitor_preparation import REPAIR_SCOPE_BLOCKED
from .store import now
from .ui_registry import provider_binding


class AuthorizationDeadline:
    """Watch only local time; stop through the original exact-job controller."""
    def __init__(self, clock, issued, expires, stop):
        self.cancelled=threading.Event()
        def watch():
            while not self.cancelled.wait(.25):
                if not issued<=clock()<expires:
                    stop();return
        self.thread=threading.Thread(target=watch,daemon=True,name='monitor-model-deadline')
        self.thread.start()

    def cancel(self):
        self.cancelled.set()


class MonitorModelAuthorization:
    def __init__(self, monitor):
        self.monitor, self.hub = monitor, monitor.hub
        self.clock = time.time

    def binding(self, mid):
        require(mid in self.monitor.records, '未知监控仓库')
        config = self.monitor.root_rules.current(mid, required=True)
        settings = config['settings']
        require(settings['allow_local_code'], '请先明确允许隔离执行本仓库代码与检查')
        require(settings['mode']!='auto' or self.hub.allow_auto, '当前服务未启用自主候选')
        spec = self.monitor.records[mid]['spec']
        policy=Policy((),(),spec['timeout_seconds'])
        provider=self.monitor._provider(policy)  # Identity only, no SDK/model allocation.
        model=provider.task_identity()[1] if callable(getattr(provider,'task_identity',None)) else provider_binding(provider)
        return {'monitor_id':mid,'spec_sha256':fingerprint(spec),'root_rules_sha256':config['sha256'],
            'provider_config_sha256':file_digest(unlinked(self.hub.provider_config)),
            'model':model,'provider':provider_binding(provider),'mode':settings['mode'],
            'build_sha256':self.hub.runtime.loaded}

    def current(self, mid):
        registry=self.monitor.registries[mid]
        head=registry.load('monitor_model_authorization_head').get('current')
        if head is None:return None
        row=registry.load('monitor_model_authorization').get(head['id'])
        require(row and row['id']==head['id'] and row['core']['binding']['monitor_id']==mid
                and row['core_sha256']==fingerprint(row['core'])
                and row['status'] in {'active','paused','revoked'}, '模型授权记录失效；保留原账，不恢复权限')
        return row

    def view(self, mid):
        row=self.current(mid)
        if row is None:return None
        value={**row,'effective_status':row['status'],'active':False}
        try:
            core=row['core'];budget=self.monitor.registries[mid].budget()
            if row['status']!='active':return value
            if not self.monitor.records[mid]['enabled']:
                value['effective_status']='monitor_paused'
            elif self.clock()>=core['expires_epoch']:
                value['effective_status']='expired'
            elif self.clock()<row['issued_epoch']:
                value['effective_status']='clock_changed'
            elif self.binding(mid)!=core['binding'] or budget['request_budget']!=core['request_ceiling']:
                value['effective_status']='binding_changed'
            elif budget['requests_reserved_unknown']:
                value['effective_status']='unknown_consumption'
            elif budget['requests_remaining']<=0:
                value['effective_status']='exhausted'
            else:value['active']=True
        except (SafetyError,KeyError,ValueError,OSError):
            value['effective_status']='binding_changed'
        return value

    def preview(self, payload):
        require(set(payload)=={'monitor_id','expires_at','max_attempts'}, '模型授权预览字段不匹配')
        self.hub.runtime.require_current()
        mid=payload['monitor_id'];binding=self.binding(mid)
        record=self.monitor.records[mid]
        require(record['enabled'], '请先恢复监控检查，再预览模型授权')
        require(isinstance(payload['expires_at'],str) and len(payload['expires_at'])<=32, '请选择明确的UTC到期时间')
        try:expiry=datetime.fromisoformat(payload['expires_at'].replace('Z','+00:00'))
        except ValueError:raise SafetyError('到期时间无效')
        require(expiry.tzinfo is not None and expiry.utcoffset().total_seconds()==0, '到期时间须包含UTC时区')
        epoch=int(expiry.timestamp())
        require(60<=epoch-self.clock()<=7*86400, '授权有效期须在1分钟至7天内')
        attempts=payload['max_attempts']
        require(type(attempts) is int and 1<=attempts<=LIMITS['candidate_max_rounds'], '候选轮数超出原配置范围')
        registry=self.monitor.registries[mid];budget=registry.budget()
        require(not budget['requests_reserved_unknown'] and budget['requests_remaining']>0, '原消费未知或额度耗尽；不新建额度')
        old=self.current(mid)
        value={'binding':binding,'budget':budget,'request_ceiling':budget['request_budget'],
            'expires_epoch':epoch,'expires_at':datetime.fromtimestamp(epoch,timezone.utc).isoformat(),
            'max_attempts':attempts if binding['mode']=='auto' else 1,
            'observed_head':self.monitor.current_head(record['spec']),
            'excluded_event_ids':list(registry.load('event')),
            'previous':{'id':old['id'],'status':old['status']} if old else None,
            'read_roots':self.monitor.root_rules.current(mid)['settings']['read_roots'],
            'write_roots':self.monitor.root_rules.current(mid)['settings']['write_roots'],
            'future_events_only':True,'restart_requires_confirmation':True,'push_or_merge':False,
            'model_limits':self.hub.original_model_limits()}
        return {'preview':value,'revision':self.monitor.root_rules.challenge(value)}

    def configure(self, payload):
        require(set(payload)=={'preview','revision','confirmed'} and payload['confirmed'] is True, '需明确确认未来事件的模型调用与原剩余额度')
        value=payload['preview']
        require(isinstance(value,dict) and len(json.dumps(value).encode())<=32768
                and payload['revision']==self.monitor.root_rules.challenge(value), '模型授权预览已失效')
        fresh=self.preview({'monitor_id':value['binding']['monitor_id'],'expires_at':value['expires_at'],'max_attempts':value['max_attempts']})
        require(fresh['preview']==value, '来源、规则、模型或原消费变化，请重新预览')
        core=fresh['preview'];mid=core['binding']['monitor_id'];registry=self.monitor.registries[mid]
        row={'id':uuid.uuid4().hex,'core':core,'core_sha256':fingerprint(core),'status':'active',
             'issued_epoch':self.clock(),'created_at':now()}
        with registry.transaction() as db:
            head=db.execute("SELECT body FROM objects WHERE kind='monitor_model_authorization_head' AND id='current'").fetchone()
            require((json.loads(head[0])['id'] if head else None)==(core['previous']['id'] if core['previous'] else None), '原授权已变化')
            budget=db.execute('SELECT ceiling,used,held FROM budget WHERE id=1').fetchone()
            require(budget==tuple(core['budget'][k] for k in ('request_budget','requests_used','requests_reserved_unknown')), '原额度变化')
            registry.put('monitor_model_authorization',row['id'],row,db)
            registry.put('monitor_model_authorization_head','current',{'id':row['id']},db)
        return {'monitor_id':mid,'authorization':self.view(mid),'model_requests':0}

    def restore(self, mid):
        row=self.current(mid)
        if row and row['status']=='active':
            row.update(status='paused',reason='service_restart',changed_at=now())
            self.monitor.registries[mid].put('monitor_model_authorization',row['id'],row)

    def control(self, action, payload, request_id):
        require(action in {'monitor_model_pause','monitor_model_revoke'} and set(payload)=={'monitor_id','grant_id'}, '模型授权控制字段不匹配')
        mid=payload['monitor_id'];require(mid in self.monitor.records, '未知监控仓库')
        registry=self.monitor.registries[mid];signature=fingerprint([action,payload])
        with registry.transaction() as db:
            saved=db.execute("SELECT body FROM objects WHERE kind='monitor_model_control' AND id=?",(request_id,)).fetchone()
            if saved:
                saved=json.loads(saved[0]);require(saved['signature']==signature,'控制请求编号不能更改内容');return saved['result']
            head=db.execute("SELECT body FROM objects WHERE kind='monitor_model_authorization_head' AND id='current'").fetchone()
            require(head and json.loads(head[0])['id']==payload['grant_id'], '模型授权版本已变化')
            row=json.loads(db.execute("SELECT body FROM objects WHERE kind='monitor_model_authorization' AND id=?",(payload['grant_id'],)).fetchone()[0])
            row.update(status='revoked' if action.endswith('revoke') or row['status']=='revoked' else 'paused',changed_at=now())
            result={'monitor_id':mid,'grant_id':row['id'],'status':row['status'],'model_requests':0}
            registry.put('monitor_model_authorization',row['id'],row,db)
            registry.put('monitor_model_control',request_id,{'signature':signature,'result':result},db)
        # The original stop receipt precedes cooperative cancellation; never select a PID.
        for (owner,_),server in list(self.monitor.servers.items()):
            session=server.session
            if owner!=mid:continue
            for event in registry.load('event').values():
                active=self.monitor.execution.current_execution(mid,event) if event.get('root_execution') else {}
                if active.get('status')=='started' and active.get('model_authorization',{}).get('id')==row['id']:
                    if session.runs_dir==self.monitor.root/mid/'r'/event['id']:self.stop_job(session)
        return result

    @staticmethod
    def stop_job(session, job_id=None):
        with session.lock:
            if session.active and session.control and not session.control.cancel.is_set() and (job_id is None or session.active==job_id):
                session.submit(uuid.uuid4().hex,'stop_job',{'job_id':session.active,'revision':session._stop_revision()})

    def observe(self, mid, event):
        grant=self.view(mid)
        if grant and grant['active'] and event['sha']!=grant['core']['observed_head'] and event['id'] not in grant['core']['excluded_event_ids']:
            event['model_observation']={'id':grant['id'],'core_sha256':grant['core_sha256']}

    def authorize(self, mid, event, pin, *, execution=False):
        if event.get('failure_successor'):
            return self.monitor.failure_followup.authorize_child(mid,event,pin,execution=execution)
        grant=self.view(mid)
        eligible=grant and (grant['active'] or execution and grant['effective_status'] in {'unknown_consumption','exhausted'})
        require(eligible and pin=={'id':grant['id'],'core_sha256':grant['core_sha256']}
                and event.get('model_observation')==pin and event['sha']!=grant['core']['observed_head']
                and event['id'] not in grant['core']['excluded_event_ids'], '未来事件模型授权已暂停、到期或失效')
        return grant

    def prepare_deadline(self, mid, event, session, job_id):
        """Verify before dispatch; arm later without racing the worker's proof.

        The worker still rechecks the live authorization through its original
        source/before-model guards. This snapshot can only stop this exact job;
        it grants no model, editing or replay authority.
        """
        grant=self.authorize(mid,event,event['root_execution']['model_authorization'],execution=True)
        issued,expires=grant['issued_epoch'],grant['core']['expires_epoch']
        return lambda:AuthorizationDeadline(lambda:self.clock(),issued,expires,
                                            lambda:self.stop_job(session,job_id))

    def deadline(self, mid, event, session, job_id):
        return self.prepare_deadline(mid,event,session,job_id)()

    def dispatch(self, mid, sha, *, event_id=None):
        registry=self.monitor.registries[mid]
        from .models import digest
        event=registry.load('event')[event_id or digest(sha.encode())[:16]]
        if event_id is not None:
            require(event['sha']==sha,'内部后继提交不同')
            self.monitor.failure_followup.validate_event(mid,event)
        pin=event.get('model_observation')
        if (not pin or event.get('root_execution') or event.get('model_dispatch')
            or event.get('root_ci_followup',{}).get('status','finished')!='finished'
            or event.get('root_preparation',{}).get('status')!='reproduced'):return
        event['model_dispatch']={'status':'started','authorization':pin};self.monitor._event(mid,event)
        starting_repair = False
        try:
            grant=self.authorize(mid,event,pin)
            receipt=self.monitor.execution.preview({'monitor_id':mid,'event_id':event['id'],'max_attempts':grant['core']['max_attempts']})
            starting_repair = True
            result=self.monitor.execution.start({**receipt,'confirmed':True},_authorization=pin)
            current=registry.load('event')[event['id']]
            current['model_dispatch'].update(status='finished',result_status=result['status'])
            self.monitor._event(mid,current)
        except Exception:
            current=registry.load('event')[event['id']]
            if starting_repair:
                message = '自动修复未完成；请核对原运行报告和模型消费记录。原现场保留，此事件不会自动重试。'
            elif event['root_preparation'].get('scope_compilation', {}).get('status')=='needs_investigation':
                message = REPAIR_SCOPE_BLOCKED
            else:
                message = '自动修复预览受阻；请核对维护规则、模型授权和原账。此事件不会自动重试。'
            current['model_dispatch'].update(status='blocked_no_replay',message=message)
            current['message'] = message
            self.monitor._event(mid,current)
