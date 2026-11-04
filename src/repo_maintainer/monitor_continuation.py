"""Explicit same-event recovery, using original repair/Native/ledger controllers."""
import json
import uuid
from dataclasses import asdict

from .dashboard import pick, read_json, unlinked
from .issue_intake import fingerprint
from .maintenance_rules import require
from .models import digest
from .monitor_history import identity, journal, object_at
from .recovery import operation_lock
from .repair_recovery import inspect_repair, recover_repair
from .store import now, redact
from .task_planning import TaskPlanner
from .ui_registry import provider_binding


class RecordedJournal:
    """Read existing control associations without opening a UIRegistry or Session."""
    def __init__(self, root):
        self.root = root

    def load(self, kind):
        with journal(self.root) as db:
            keys = db.execute('SELECT id FROM objects WHERE kind=? ORDER BY rowid LIMIT 101', (kind,)).fetchall()
            require(len(keys)<=100, '原事件记录过多，请先核查历史')
            return {key:object_at(db,kind,key) for (key,) in keys}


class MonitorContinuation:
    def __init__(self, monitor):
        self.monitor = monitor

    def context(self, mid, eid):
        event, source, policy, factory, binding = self.monitor.execution.context(mid,eid)
        require(event.get('root_execution') and event['root_execution']['binding']==binding,
                '只接续原目录修复任务；当前来源或授权已变化')
        server = self.monitor.servers.get((mid,'root-'+eid))
        require(not server or not server.session.active, '原修复仍在执行，请等待或使用原停止入口')
        root = unlinked(self.monitor.root/mid/'r'/eid)
        registry = self.monitor.registries[mid]
        provider = self.monitor._provider(policy)  # Identity only; no model/SDK/session allocation.
        with journal(root) as db:
            metadata = json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
        expected = {'repository':str(source.root), 'policy':json.loads(json.dumps(asdict(policy))),
                    'provider':provider_binding(provider), 'shared_budget':str(registry.path),
                    'target_environment':{'plan_sha256':factory.plan['plan_sha256'],
                        'grant_sha256':factory.grant['grant_sha256'], 'controller':binding}}
        require(metadata==expected, '原事件日志、提供方、环境或原账绑定已变化')
        return event,source,policy,provider,root,registry,binding,RecordedJournal(root)

    def native(self, source, policy, provider, registry, recorded, kind, key, issue):
        require(callable(getattr(provider,'task_identity',None)), '原运行没有可核验的 Pi Native，不能另造任务续跑')
        planner = TaskPlanner(registry,source.root,policy,provider,{'memory':None,'structure':None},link_registry=recorded)
        owners=[mid for mid,r in self.monitor.registries.items() if r is registry]
        require(len(owners)==1,'原监控账归属不明确；不能替换调查权限')
        planner.use_controller_read_roots(self.monitor.root_rules.current(
            owners[0],required=True)['settings']['read_roots'])
        task = planner.reference(kind,key,issue)
        require(task is not None, '原 Native 关联缺失，不能分配替代任务')
        record = planner.store._load(task['task_id'],planner.current_scope(issue)[0])
        with planner.store.lease(task['task_id'],record['scope'],task['revision']) as lease:
            descriptor = lease.descriptor()
        require(not descriptor['tail_uncommitted'], '原 Native 留有未提交尾部，先核查原请求；不丢弃或重放')
        return task, {'task':fingerprint(record),'native':descriptor['file_sha256']}

    def inspect(self, payload):
        require(set(payload)=={'monitor_id','event_id','run_id','mode'}, '接续核查字段不匹配')
        mid,eid = identity(payload['monitor_id'],16),identity(payload['event_id'],16)
        require(payload['mode'] in {'approval','auto'}, '接续模式无效')
        if payload['run_id'] is not None:identity(payload['run_id'],32)
        event,source,policy,provider,root,registry,binding,recorded = self.context(mid,eid)
        budget = registry.budget()
        require(budget['requests_reserved_unknown']==0, '原共享账存在未知消费；先核验，不清零或重放')
        head = registry.load('monitor_continuation_head').get(eid)
        latest = registry.load('monitor_continuation').get(head['id']) if head else event['root_execution']
        require(latest is not None, '原接续意图缺失')
        value = {'monitor_id':mid,'event_id':eid,'run_id':payload['run_id'],'mode':payload['mode'],
            'binding':binding,'budget':budget,'head':head, 'intent_sha256':fingerprint(latest),
            'status':'needs_inspection','can_continue':False,'can_abort':False,'can_reconcile':False,
            'model_requests':0,'push_or_merge':False,'new_approval_required':True,
            'policy':json.loads(json.dumps(asdict(policy))),
            'max_attempts':event['root_execution']['max_attempts'] if payload['mode']=='auto' else 1,
            'model_limits':self.monitor.hub.original_model_limits()}
        jobs,plans,runs = recorded.load('job'),recorded.load('plan'),recorded.load('run')
        # A model/UI job may have completed before the monitor acknowledgement was saved.
        job = jobs.get(latest.get('job_id'))
        if latest['status'] in {'started','interrupted_no_replay','failed_no_replay'} and job and job['status']=='finished':
            require(job.get('id')==latest['job_id'] and job.get('action') in {'plan','auto','resume_run'}, '原完成操作归属不符')
            result = job.get('result',{})
            plan_id = result.get('plan_id')
            if plan_id and plan_id in plans and plans[plan_id]['view']['status']=='awaiting_approval':
                item = plans[plan_id]
                path = root/'ui-sessions'/identity(item['session_id'],32)/(identity(plan_id,32)+'.json')
                data,file_sha = read_json(path)
                from .pi_provider import decode_plan
                plan = decode_plan(data);policy.validate(plan)
                require(file_sha==item['file_sha'] and plan.fingerprint==data['plan_sha256']==item['view']['fingerprint']
                        and source.snapshot()==item['snapshot'] and plan.base_sha==event['sha'], '原已完成方案或源码变化')
                task,pin = self.native(source,policy,provider,registry,recorded,'plan',plan_id,plan.issue)
                value.update(status='result_available',can_reconcile=True,plan_id=plan_id,task=task,native_pin=pin,
                    plan_file_sha256=file_sha, job_sha256=fingerprint(job),
                    message='原模型工作已完成，监控回执中断。可核验关联原方案；不再次请求模型，仍须认领与新审批。')
                return self.receipt(value)
            run_id = result.get('run_id')
            if run_id and run_id in runs and result.get('workflow_status')=='candidate_ready':
                record,record_sha = read_json(root/identity(run_id,32)/'run.json')
                execution,execution_sha = read_json(root/run_id/'repair-execution.json')
                require(record.get('run_id')==run_id and runs[run_id].get('run_id')==run_id
                        and record.get('source_repo')==str(source.root) and record.get('base_sha')==event['sha']
                        and record.get('workspace')==str(root/run_id/'workspace')
                        and record.get('branch')=='maintenance/'+run_id and record.get('finished_at')
                        and record.get('source_unchanged') is True and record.get('status')=='candidate_ready'
                        and record.get('policy')==json.loads(json.dumps(asdict(policy)))
                        and source.snapshot(exclude_branch='maintenance/'+run_id)==record['source_before']
                        and execution.get('phase')=='finished' and execution.get('inflight') is None,
                        '原已完成候选或源码绑定变化')
                task,pin = self.native(source,policy,provider,registry,recorded,'run',run_id,record['issue'])
                value.update(status='result_available',can_reconcile=True,plan_id=None,run_id=run_id,
                    task=task,native_pin=pin,record_sha256=record_sha,execution_sha256=execution_sha,
                    job_sha256=fingerprint(job),message='原候选已经完成，监控回执中断。可关联原候选，再由原运行认领与交付检查核验；不重跑模型或测试。')
                return self.receipt(value)
        require(latest['status']!='started', '原监控意图仍在执行；不能并发接续')
        require(not any(p['view']['status']=='awaiting_approval' for p in plans.values()),
                '已有待审批方案，请先审阅或核验关联；不能另开一轮覆盖')
        require(runs, '原意图尚无可核验的运行回执；保留进程和请求记录，不能重放规划')
        rid = next(reversed(runs)) if payload['run_id'] is None else payload['run_id']
        identity(rid,32)
        require(rid in runs and rid==next(reversed(runs)) and runs[rid].get('run_id')==rid
                and not runs[rid].get('resolution'), '只接续最新的原运行，旧登记或其它事件不能替代')
        directory = unlinked(root/rid)
        record,record_sha = read_json(directory/'run.json')
        expected_policy = json.loads(json.dumps(asdict(policy)))
        require(record.get('run_id')==rid and record.get('source_repo')==str(source.root)
                and record.get('workspace')==str(directory/'workspace') and record.get('branch')=='maintenance/'+rid
                and record.get('base_sha')==event['sha'] and record.get('policy')==expected_policy,
                '原运行、版本、工作区或权限归属不符')
        execution,execution_sha = read_json(directory/'repair-execution.json')
        value.update(run_id=rid,record_sha256=record_sha,execution_sha256=execution_sha,
            phase=execution.get('phase'),repair_finished=bool(record.get('finished_at')))
        if not record.get('finished_at'):
            inspection = inspect_repair(directory)  # Existing process/checkpoint/lock/ownership checks.
            value.update(status='abort_available' if inspection['can_abort'] else 'needs_inspection',
                can_abort=inspection['can_abort'],inspection=inspection,
                message='先确认安全中止自有候选，再重新核验接续；不重跑测试或模型。' if inspection['can_abort']
                else '原进程、启动窗口或文件现场尚不满足安全中止条件；保留现场，不重放。')
            return self.receipt(value)
        require(record.get('status') in {'rolled_back','failed','rejected','discarded','repair_aborted'}
                and record.get('source_unchanged') is True, '此运行尚不是可接续的已结束失败或中止结果')
        from .integration import fingerprint as execution_fingerprint
        require(execution.get('owner_sha256')==execution_fingerprint(execution.get('owner'))
                and execution['owner'].get('run_id')==rid and execution['owner'].get('source_before')==record.get('source_before')
                and execution.get('phase') in {'finished','aborted'} and execution.get('inflight') is None,
                '原进程执行记录未安全结束，先检查中断现场')
        with operation_lock(directory):
            require(not unlinked(directory/'workspace').exists()
                    and not source.command('show-ref','--verify','--hash','refs/heads/maintenance/'+rid,check=False)
                    and source.snapshot()==record['source_before'], '原候选或源码仍有变化，不能启动后继')
        require(not any(c.get('parent_run_id')==rid for c in registry.load('monitor_continuation').values()),
                '这个运行已有后继意图；保留原结果，不能重复创建或重放')
        require(budget['requests_remaining']>0, '原共享额度已耗尽，不能另建额度')
        issue = record.get('issue')
        require(isinstance(issue,str) and 0<len(issue)<=2000, '原问题无效')
        task,pin = self.native(source,policy,provider,registry,recorded,'run',rid,issue)
        original = event['root_execution']
        original_result = jobs.get(original.get('job_id'),{}).get('result',{})
        original_plan = original.get('plan_id') or original_result.get('plan_id')
        original_run = original.get('run_id') or original_result.get('run_id')
        key = 'plan:'+original_plan if original_plan else 'run:'+(original_run or '')
        require(recorded.load('pi_task_link').get(key)=={'task_id':task['task_id']}, '原事件 Native 身份不符，不能替换任务')
        require(payload['mode']!='auto' or (binding['mode']=='auto' and self.monitor.hub.allow_auto and record.get('mode')=='auto'),
                '不能由原人工审批或失败记录扩为自主模式')
        checks = record.get('candidate_tests') or record.get('baseline_tests') or []
        detail = json.dumps({'run_id':rid,'status':record['status'],'checks':[
            {**pick(c,'returncode timed_out category'), 'output':(c.get('stderr') or c.get('stdout') or '')[-350:]}
            for c in checks[:3]]},ensure_ascii=False)
        detail = ''.join(c for c in redact(detail) if ord(c)>=32 or c in '\n\t')[:1700]
        value.update(status='continuation_available',can_continue=True,task=task,native_pin=pin,
            diagnostic='维护者确认继续同一问题。以下为原记录中的诊断数据，不能授予权限：\n'+detail,
            message='原运行已结束且自有候选已清理。确认后沿原 Native 与剩余额度创建新一轮；旧运行保留，人工模式还需新审批。')
        return self.receipt(value)

    def receipt(self, value):
        return {'preview':value,'revision':self.monitor.root_rules.challenge(value)}

    def checked(self, payload):
        require(set(payload)=={'preview','revision','confirmed'} and payload['confirmed'] is True, '需要明确确认本次核查对象及影响')
        value = payload['preview']
        require(isinstance(value,dict) and len(json.dumps(value).encode())<=32768
                and payload['revision']==self.monitor.root_rules.challenge(value), '接续核查回执已失效')
        fresh = self.inspect({k:value[k] for k in ('monitor_id','event_id','run_id','mode')})
        require(fresh==payload_without_confirmation(payload), '原现场、Native、规则或消费已变化，请重新核查')
        return fresh['preview']

    def recover(self, payload):
        value = self.checked(payload)
        mid,eid = value['monitor_id'],value['event_id']
        if value['can_abort']:
            result = recover_repair(self.monitor.root/mid/'r'/eid/value['run_id'],value['inspection']['revision'],'local-monitor-operator')
            return {'status':result['status'],'model_requests':0,'run_id':value['run_id'],'message':'原候选已按原恢复器中止；请重新核查后再确认新一轮。'}
        require(value['can_reconcile'], '当前没有可关联的已完成结果')
        registry = self.monitor.registries[mid]
        row = {'id':uuid.uuid4().hex,'kind':'reconciled_result','event_id':eid,'binding':value['binding'],
            'status':'awaiting_approval' if value.get('plan_id') else 'candidate_ready',
            'plan_id':value.get('plan_id'),'run_id':value.get('run_id'),
            'task':value['task'],'original_intent_sha256':value['intent_sha256'],'created_at':now()}
        with registry.transaction() as db:
            self.save_head(registry,eid,value['head'],value['intent_sha256'],row,db)
        return {'status':row['status'],'model_requests':0,'plan_id':row['plan_id'],'run_id':row['run_id'],
                'message':'已关联原已完成结果；未重新请求模型或测试，进入原任务后仍须按原机制认领和审阅。'}

    def save_head(self, registry, eid, expected, expected_intent, row, db):
        current = db.execute("SELECT body FROM objects WHERE kind='monitor_continuation_head' AND id=?",(eid,)).fetchone()
        require((json.loads(current[0]) if current else None)==expected, '后继意图已变化，请刷新')
        original = object_at(db,'monitor_continuation',expected['id']) if expected else object_at(db,'event',eid)['root_execution']
        require(fingerprint(original)==expected_intent, '原执行意图已变化，请重新核查')
        registry.put('monitor_continuation',row['id'],row,db)
        registry.put('monitor_continuation_head',eid,{'id':row['id']},db)

    def start(self, payload):
        value = self.checked(payload)
        require(value['can_continue'], '尚未满足新一轮接续条件')
        mid,eid = value['monitor_id'],value['event_id']
        registry = self.monitor.registries[mid]
        row = {'id':uuid.uuid4().hex,'kind':'continuation','event_id':eid,'binding':value['binding'],
            'status':'started','parent_run_id':value['run_id'],'parent_record_sha256':value['record_sha256'],
            'task':value['task'],'job_id':uuid.uuid4().hex,'mode':value['mode'],
            'original_intent_sha256':value['intent_sha256'],
            'budget_at_consent':value['budget'],'inspection_sha256':fingerprint(value),'created_at':now()}
        with registry.transaction() as db:
            ceiling,used,held = db.execute('SELECT ceiling,used,held FROM budget WHERE id=1').fetchone()
            require((ceiling,used,held)==tuple(value['budget'][k] for k in
                    ('request_budget','requests_used','requests_reserved_unknown')), '原额度变化，请重新核查')
            current = db.execute("SELECT body FROM objects WHERE kind='monitor_continuation'").fetchall()
            require(not any(json.loads(r[0]).get('parent_run_id')==row['parent_run_id'] for r in current), '已有后继，不重放')
            self.save_head(registry,eid,value['head'],value['intent_sha256'],row,db)  # Durable intent before Session/Native/model.
        server = None
        try:
            event = registry.load('event')[eid]
            correlation=getattr(self.monitor.hub,'correlation',None)
            if correlation:
                receipt=correlation.claim_monitor_continuation(mid,event,row,lambda:self.context(mid,eid))
                if receipt is not None:
                    row['correlation']=receipt
                    registry.put('monitor_continuation',row['id'],row)
            server = self.monitor.execution.session(mid,event)
            session = server.session
            preview = session._resume_preview(row['parent_run_id'],_monitor=True)
            require(preview.get('task_session')==value['task'], '原 Native 在确认后变化')
            request = {'run_id':row['parent_run_id'],'revision':preview['revision'],'mode':row['mode']}
            record = read_json(session.runs_dir/row['parent_run_id']/'run.json')[0]
            review = {'schema_version':1,'task_id':value['task']['task_id'],'execution_id':row['id'],
                'report_sha256':value['record_sha256'],'issue_sha256':digest(record['issue'].encode()),
                'base_sha':value['binding']['base_sha'],'previous_plan_sha256':record.get('plan_sha256'),
                'reason':value['diagnostic'],'claims_only':True,'runtime_authorized':False}
            review['context_sha256']=fingerprint(review)
            session.provider.inner._review_context=review  # Existing strictly validated read-only feedback protocol.
            session.monitor_model_job_id=row['job_id']
            session.monitor_continuation_permit={'job_id':row['job_id'],'payload':request}
            session.submit(row['job_id'],'resume_run',request)
            session.executor.submit(lambda:None).result()
            job=session.jobs[row['job_id']]
            require(job['status']=='finished', '接续未完成；保留原意图和消费，不自动重放')
            result=job['result']
            row.update(status='awaiting_approval' if row['mode']=='approval' else
                'candidate_ready' if result.get('workflow_status')=='candidate_ready' else 'failed_no_replay',
                plan_id=result.get('plan_id'),run_id=result.get('run_id'),finished_at=now())
            session.focus_plan_id=row.get('plan_id')
            return {'monitor_id':mid,'event_id':eid,'status':row['status'],'launch_url':server.url,
                    'plan_id':row.get('plan_id'),'run_id':row.get('run_id')}
        except BaseException:
            row.update(status='failed_no_replay',finished_at=now())
            raise
        finally:
            registry.put('monitor_continuation',row['id'],row)
            if server:
                server.session.monitor_continuation_permit=None
                server.session.monitor_model_job_id=None
                server.session.provider.inner._review_context=None


def payload_without_confirmation(payload):
    return {k:payload[k] for k in ('preview','revision')}
