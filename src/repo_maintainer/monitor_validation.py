"""Explicit readonly case proposals on the original monitor account.

No future-model grant is invented. The operator supplies a business expectation,
not source paths or executable commands. Review and preparation remain separate.
"""
import copy
import json
import threading
import uuid

from .check_discovery import is_test
from .behavior_validation import is_behavior_module
from .dashboard import unlinked
from .git import RepositoryGateway
from .issue_intake import fingerprint
from .job_control import JobControl
from .models import Policy, file_digest
from .pi_provider import PiProvider
from .repository_diagnosis import build_report
from .store import now, redact
from .task_session import require, strict_json
from .validation_proposal import ValidationProposal, source_packet, clarification_leaf


class MonitorValidation:
    def __init__(self,monitor):
        self.m,self.hub=monitor,monitor.hub
        self.active={};self.lock=threading.RLock()

    def challenge(self,row):
        return fingerprint({'value':row,'session':self.hub.token})

    @staticmethod
    def request_text(value):
        require(isinstance(value,str) and value==value.strip() and 1<=len(value)<=2000
                and not any(ord(c)<32 and c not in '\n\t' for c in value)
                and redact(value)==value,'请提供1至2000字明确的业务期望，不含凭据或控制字符')
        return value

    def _event(self,mid,eid):
        require(mid in self.m.records,'未知监控仓库')
        event=self.m.registries[mid].load('event').get(eid)
        require(event is not None,'监控事件缺失')
        return event

    @staticmethod
    def _ci(event):
        return fingerprint(event.get('ci_failures',[]))

    def _initial(self,mid,event):
        require(not any(event.get(k) for k in ('root_execution','model_dispatch','root_ci_followup','failure_successor')),
                '此事件已有执行或后继意图；不能替换其验收范围')
        require(not self.m.registries[mid].load('monitor_continuation_head').get(event['id']),
                '原后继已经登记；不能替换案例')
        prep=event.get('root_preparation',{})
        require(prep.get('status') in {'validation_needed','baseline_passes','reproduced','execution_consent_required'}
                and prep.get('result_sha256')==fingerprint({k:v for k,v in prep.items() if k!='result_sha256'})
                and not event.get('root_preparation_historical'),
                '需要完整的当前调查；中断、未知或环境阻塞不能借案例提议重放')
        return prep

    def _service(self,mid,eid,text=None,*,root=False):
        event=self._event(mid,eid);registry=self.m.registries[mid]
        config=self.m.root_rules.current(mid,required=True)
        source=RepositoryGateway(unlinked(self.m.root/mid/'events'/eid/'source'))
        intake=registry.load('monitor_validation_request').get(eid)
        if intake:
            require(intake['id']==fingerprint(intake['core']),'案例需求登记已变化')
            core=intake['core'];text=core['request_text'] if text is None else self.request_text(text)
            require(text==core['request_text'],'原案例需求已固定；不得改写旧意图')
            origin=core['authority'];paths=core['readonly_paths'];report=intake['report']
            require(fingerprint(report)==origin['investigation_sha256'],'原案例调查已变化')
        else:
            text=self.request_text(text);prep=self._initial(mid,event)
            report=copy.deepcopy(prep['report'])
            candidates=report.get('file_candidates',[])
            if not any(is_behavior_module(c.get('path','')) and not is_test(c['path']) for c in candidates):
                report=build_report(source,head_sha=event['sha'],read_roots=config['settings']['read_roots'],
                                    search=True,query=text)
            paths=list(dict.fromkeys(c['path'] for k in ('file_candidates','test_candidates')
                for c in report.get(k,[]) if isinstance(c,dict) and isinstance(c.get('path'),str)))[:20]
            require(any(is_behavior_module(p) and not is_test(p) for p in paths),
                    '尚未找到读范围内可验收的Python/Node实现；请先补充业务期望，不能猜测文件或扩大权限')
            origin={'monitor_id':mid,'event_id':eid,'base_sha':event['sha'],
                'preparation_sha256':fingerprint(prep),'root_rules_sha256':config['sha256'],
                'provider_config_sha256':file_digest(unlinked(self.hub.provider_config)),
                'investigation_sha256':fingerprint(report),'ci_sha256':self._ci(event)}
        provider=self.m._provider(Policy((),(),config['settings']['timeout_seconds'],tuple(paths)))
        require(isinstance(provider,PiProvider) and not provider.allowed_paths,'案例提议需要原只读Pi配置')
        binding=provider.task_identity()[1]
        if intake:require(origin['provider']==binding,'原案例模型已变化')
        else:origin['provider']=binding
        packet=source_packet(source,text,config['settings']['read_roots'],paths,origin)
        if intake:require(packet['source_sha256']==core['source_sha256'],'原案例来源已变化')

        def guard():
            self.hub.runtime.require_current()
            require(not self.hub.closed,'工作台已关闭')
            self.m.guard(mid,event['sha'])
            current=self._event(mid,eid)
            require(self.m.root_rules.current(mid,required=True)==config
                    and file_digest(unlinked(self.hub.provider_config))==origin['provider_config_sha256']
                    and source.head==event['sha'] and not current.get('root_preparation_historical')
                    and self._ci(current)==origin['ci_sha256'],'案例规则、模型、源码或CI版本已变化')
            source.require_clean(source.snapshot())
            originals=[current.get('root_preparation',{}),*current.get('root_preparation_history',[])]
            require(any(fingerprint(p)==origin['preparation_sha256'] for p in originals),
                    '案例所依据的原调查缺失或变化')
            saved=registry.load('monitor_validation_request').get(eid)
            if saved:require(saved['core']=={
                'request_text':text,'readonly_paths':paths,'authority':origin,'source_sha256':packet['source_sha256']},
                '案例原需求或来源登记不符')
            with self.lock:active=self.active.get((mid,eid))
            if active:active['control'].check()
        service=ValidationProposal(registry,source,provider,packet,guard,self.hub.token,
                                   operation_id=self.hub.active,completion_guard=lambda:self.lock)
        service._fresh()
        core={'request_text':text,'readonly_paths':paths,'authority':origin,'source_sha256':packet['source_sha256']}
        selected=service if root else service.current()
        return selected,event,config,{**core,'request_text':selected.source['issue']},report

    def projection(self,mid,event):
        registry=self.m.registries[mid];intake=registry.load('monitor_validation_request').get(event['id'])
        if not intake:return None
        selected,history=clarification_leaf(registry,intake['proposal_id'])
        text=(registry.load('validation_clarification')[history[-1]]['source']['issue']
              if history else intake['core']['request_text'])
        row=registry.load('validation_proposal').get(selected)
        if row is None:return {'id':selected,'status':'interrupted_no_replay',
                             'request_text':text,'runtime_authorized':False}
        value={k:v for k,v in row.items() if k!='source'}
        value.update(monitor_id=mid,event_id=event['id'],request_text=text,clarification_history=history,
                     revision=self.challenge(row))
        if row['status']=='started':value['stop_revision']=self.challenge(row)
        resume=registry.load('monitor_validation_resume').get(event['id'])
        if resume:value['resume']={k:resume[k] for k in ('status','result_status','error_type') if k in resume}
        return value

    def preview(self,payload):
        require(set(payload)=={'monitor_id','event_id','request_text'},'案例预览字段不符')
        mid,eid=payload['monitor_id'],payload['event_id']
        service,*_=self._service(mid,eid,payload['request_text'])
        value={**payload,'phase':service.preview()}
        return {'preview':value,'revision':self.m.root_rules.challenge(value)}

    def propose(self,payload):
        require(set(payload)=={'preview','revision','confirmed'} and payload['confirmed'] is True,
                '需明确确认这一次只读模型调用与原额度')
        value=payload['preview']
        require(isinstance(value,dict) and set(value)=={'monitor_id','event_id','request_text','phase'}
                and payload['revision']==self.m.root_rules.challenge(value),'案例预览失效')
        mid,eid=value['monitor_id'],value['event_id']
        service,event,config,core,report=self._service(mid,eid,value['request_text'])
        expected=self.preview({k:value[k] for k in ('monitor_id','event_id','request_text')})
        require(expected['preview']==value and expected['revision']==payload['revision'],'原需求、来源或额度已变化')
        registry=service.registry
        require(not registry.budget()['requests_reserved_unknown'] and registry.budget()['requests_remaining']>0,
                '原消费未知或额度耗尽，不启动模型')
        self._initial(mid,event)
        operation=self.hub.active or uuid.uuid4().hex
        intake={'id':fingerprint(core),'core':core,'report':report,'proposal_id':service.identity,
                'consent':{'operation_id':operation,'budget':value['phase']['budget'],
                           'purpose':'readonly_validation_only','confirmed_at':now()}}
        with registry.transaction() as db:
            require(not db.execute("SELECT 1 FROM objects WHERE kind='monitor_validation_request' AND id=?",(eid,)).fetchone(),
                    '原案例意图已登记，不重新调用模型')
            original=db.execute("SELECT body FROM objects WHERE kind='event' AND id=?",(eid,)).fetchone()
            require(original and strict_json(original[0])==event,'原事件已变化')
            registry.put('monitor_validation_request',eid,intake,db)
        service.operation_id=operation
        control=JobControl(lambda _:None)
        with self.lock:self.active[mid,eid]={'control':control,'operation_id':operation,'proposal_id':service.identity}
        service.provider._cancel_event=control.cancel
        service.provider._progress_callback=lambda _:control.check()
        try:service.propose(value['phase']['revision'])
        finally:
            with self.lock:self.active.pop((mid,eid),None)
        return self.projection(mid,self._event(mid,eid))

    def clarification_preview(self,payload):
        require(set(payload)=={'monitor_id','event_id','revision','answer'},'补充预览字段不符')
        mid,eid=payload['monitor_id'],payload['event_id'];service,event,*_=self._service(mid,eid)
        self._initial(mid,event)
        return service.clarification_preview(payload['revision'],payload['answer'])

    def clarify(self,payload):
        require(set(payload)=={'monitor_id','event_id','preview','revision','confirmed'}
                and payload['confirmed'] is True,'需要明确确认补充后的只读提议与原额度')
        require(isinstance(payload['preview'],dict),'补充预览格式无效')
        mid,eid=payload['monitor_id'],payload['event_id'];root,event,*_=self._service(mid,eid,root=True)
        self._initial(mid,event)
        parent=root.ancestor(payload['preview'].get('parent_id'))
        # Same original operation lock orders stop/finish. No future-model consent.
        control=JobControl(lambda _:None)
        with self.lock:self.active[mid,eid]={'control':control,'operation_id':self.hub.active,
            'proposal_id':payload['preview']['child_id']}
        parent.provider._cancel_event=control.cancel
        parent.provider._progress_callback=lambda _:control.check()
        try:parent.clarify(payload['preview'],payload['revision'])
        finally:
            with self.lock:self.active.pop((mid,eid),None)
        return self.projection(mid,self._event(mid,eid))

    def review(self,payload):
        require(set(payload)=={'monitor_id','event_id','revision','decision'},'案例审阅字段不符')
        mid,eid=payload['monitor_id'],payload['event_id'];service,*_=self._service(mid,eid)
        service.review(payload['revision'],payload['decision'])
        return self.projection(mid,self._event(mid,eid))

    def reconcile(self,payload):
        require(set(payload)=={'monitor_id','event_id','revision'},'案例核对字段不符')
        from .validation_reconcile import reconcile
        mid,eid=payload['monitor_id'],payload['event_id'];service,*_=self._service(mid,eid)
        reconcile(service,payload['revision'])
        return self.projection(mid,self._event(mid,eid))

    def prepare(self,payload):
        require(set(payload)=={'monitor_id','event_id','revision'},'案例准备字段不符')
        mid,eid=payload['monitor_id'],payload['event_id']
        service,event,config,core,report=self._service(mid,eid)
        self._initial(mid,event)
        row=service.registry.load('validation_proposal').get(service.identity)
        require(row and payload['revision']==self.challenge(row),'案例审阅版本已变化')
        contract=service.accepted();require(contract is not None,'需要先核对并接受原案例')
        registry=service.registry;previous=copy.deepcopy(event['root_preparation'])
        intent={'status':'started','proposal_id':service.identity,'proposal_sha256':fingerprint(row),
                'parent_preparation_sha256':fingerprint(previous),'contract_sha256':contract['contract_sha256'],
                'started_at':now()}
        with registry.transaction() as db:
            require(not db.execute("SELECT 1 FROM objects WHERE kind='monitor_validation_resume' AND id=?",(eid,)).fetchone(),
                    '原案例准备意图已登记，不重跑基线')
            saved=db.execute("SELECT body FROM objects WHERE kind='event' AND id=?",(eid,)).fetchone()
            accepted=db.execute("SELECT body FROM objects WHERE kind='validation_proposal' AND id=?",(service.identity,)).fetchone()
            require(saved and strict_json(saved[0])==event and accepted and strict_json(accepted[0])==row,
                    '原事件或案例审阅已变化')
            registry.put('monitor_validation_resume',eid,intent,db)
            event.setdefault('root_preparation_history',[]).append(previous)
            registry.put('event',eid,event,db)
        def guard():
            service._fresh()
            require(service.accepted()==contract,'原案例审阅或契约已变化')
        def save(value):
            require(len(json.dumps(value,ensure_ascii=False).encode())<=524288,'原事件证据超过容量')
            event['root_preparation']=value;self.m._event(mid,event)
        try:
            from .monitor_preparation import prepare_monitor
            result=prepare_monitor(service.repository,self.m.records[mid]['spec'],config,event,self.m.root/mid,
                guard,save,ci_diagnosis=previous.get('ci_diagnosis'),behavior_contract=contract,
                maintenance_request=core['request_text'],behavior_guard=guard,case_report=report,
                preserved_checks=previous['environment']['checks'])
            intent.update(status='finished',result_status=result['status'],finished_at=now())
            event.update(status='healthy' if result['status']=='baseline_passes' else 'blocked',
                         message=result['message'],notification=uuid.uuid4().hex)
        except BaseException as error:
            intent.update(status='interrupted_no_replay',error_type=type(error).__name__,finished_at=now())
            event.update(status='blocked',message='原案例基线未完整结束，保留意图，不自动重跑')
            raise
        finally:
            registry.put('monitor_validation_resume',eid,intent);self.m._event(mid,event)
        return {'monitor_id':mid,'event_id':eid,'status':result['status'],
                'validation':self.projection(mid,event),'resume':copy.deepcopy(intent)}

    def accepted_context(self,mid,event):
        service,_,_,core,_=self._service(mid,event['id'])
        row=service.registry.load('validation_proposal').get(service.identity)
        resume=service.registry.load('monitor_validation_resume').get(event['id'])
        contract=service.accepted()
        require(contract is not None and resume and resume['status']=='finished' and resume['result_status']=='reproduced'
                and resume['proposal_id']==service.identity and resume['proposal_sha256']==fingerprint(row)
                and resume['contract_sha256']==contract['contract_sha256']
                and event['root_preparation']['environment'].get('behavior_contract')==contract
                and event['root_preparation'].get('maintenance_request')==core['request_text'],
                '监控行为检查缺少原案例审阅、需求或准备回执')
        return core['request_text'],contract,{'proposal_id':service.identity,'proposal_sha256':fingerprint(row),
            'source_sha256':core['source_sha256'],'contract_sha256':contract['contract_sha256'],
            'resume_sha256':fingerprint(resume)}

    def stop(self,payload,request_id):
        require(set(payload)=={'monitor_id','event_id','revision'},'停止案例提议字段不符')
        mid,eid=payload['monitor_id'],payload['event_id'];event=self._event(mid,eid)
        registry=self.m.registries[mid];signature=fingerprint(payload)
        with self.lock:
            active=self.active.get((mid,eid))
            with registry.transaction() as db:
                saved=db.execute("SELECT body FROM objects WHERE kind='monitor_validation_stop' AND id=?",(request_id,)).fetchone()
                if saved:
                    saved=strict_json(saved[0]);require(saved['signature']==signature,'停止编号不能改变内容');return saved['result']
                require(active is not None,'没有此事件的活跃案例提议，不重放停止')
                saved=db.execute("SELECT body FROM objects WHERE kind='validation_proposal' AND id=?",(active['proposal_id'],)).fetchone()
                row=strict_json(saved[0]) if saved else None
                require(row and row['status']=='started' and row['operation_id']==active['operation_id']
                        and payload['revision']==self.challenge(row),'原案例操作已变化')
                result={'monitor_id':mid,'event_id':eid,'status':'stop_requested','operation_id':active['operation_id']}
                registry.put('monitor_validation_stop',request_id,{'signature':signature,'result':result},db)
            active['control'].cancel.set()
        return result
