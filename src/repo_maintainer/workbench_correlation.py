"""Explicit comparable-source association on already-open original journals.

This first product adapter fences registered repair sources only. No new ledger,
automatic similarity link, fee permission, account migration or replay is created.
"""
import copy
import json
from pathlib import Path
import threading

from .dashboard import unlinked, read_json
from .github_connection import github_url
from .git import RepositoryGateway
from .issue_intake import fingerprint
from .maintenance_correlation import (MaintenanceCorrelation,APPROVAL_KIND,DISPATCH_KIND,
    OBSERVATIONS,CLAIMS,registry_identity,source_reference)
from .models import Policy,SafetyError,file_digest
from .repair_comparison import repair_comparison
from .store import now
from .task_session import require

PAIRS='maintenance_product_pair'
ROUTES='maintenance_product_route'
AUTHORITY='maintenance_product_authority'
RECEIPTS='maintenance_product_receipt'
MONITOR_CONTINUATION_PURPOSE='repair-continuation-v1'
ISSUE_CONTINUATION_PURPOSE='issue-repair-continuation-v1'


class WorkbenchCorrelation:
    def __init__(self,hub):
        self.hub=hub;self.lock=threading.RLock()

    def _registries(self):
        values=[*self.hub.monitor.registries.values(),
                *(s.session.registry for s in list(self.hub.sessions.values()))]
        return {str(r.path):r for r in values}

    @staticmethod
    def origin_id(origin):return fingerprint(origin)

    def _model(self,provider):
        return {**provider.task_identity()[1],
                'provider_config_sha256':file_digest(unlinked(self.hub.provider_config)),
                'runtime_sha256':self.hub.runtime.loaded}

    def _monitor(self,mid,eid):
        event,repo,policy,_factory,binding=self.hub.monitor.execution.context(mid,eid)
        config=self.hub.monitor.root_rules.current(mid,required=True)
        spec=self.hub.monitor.records[mid]['spec'];settings=config['settings']
        prep=event['root_preparation'];pin=binding.get('ci_failure',{})
        # A local path/hash is never fabricated into a GitHub numeric identity.
        url=spec.get('github_url') or RepositoryGateway(unlinked(Path(spec['path']))).command(
            'config','--get','remote.origin.url',check=False).decode().strip()
        slug=github_url(url)[0].casefold()
        registry=self.hub.monitor.registries[mid]
        origin={'kind':'monitor','monitor_id':mid,'event_id':eid,'registry_path':str(registry.path)}
        model=self._model(self.hub.monitor._provider(policy))
        basis={'preparation_sha256':prep['result_sha256'],'binding':binding,
               'spec_sha256':fingerprint(spec),'model_identity':model}
        request=prep.get('maintenance_request') or '修复本提交已复现的失败，保留全部原回归。'
        return dict(origin=origin,registry=registry,repository=repo,repository_id=pin.get('repository_id'),
            slug=slug,branch=spec['branch'],head_sha=event['sha'],policy=policy,environment=prep['environment'],
            grant=prep['grant'],read_roots=settings['read_roots'],write_roots=settings['write_roots'],
            mode=settings['mode'],model_identity=model,
            max_attempts=1 if settings['mode']=='approval' else 3,basis=basis,
            label='提交 / CI · '+mid[:8]+' / '+eid[:8],request=request)

    def _issue(self,workbench,identity):
        w=workbench;s=w.session
        require(not s.monitor_owned and w.registry is s.registry is s.budget_registry,
                'Issue关联必须保留自己的原控制器和原账')
        prepared=w.environment(identity)
        require(prepared.get('environment') is not None,'先完成原Issue环境提案，不能凭标题关联修复')
        config=w._config();job=w.registry.load('issue_diagnosis')[identity];snap=job['snapshot']
        policy=Policy(**{k:tuple(tuple(c) for c in v) if k=='allowed_tests' else tuple(v)
                         if k in {'allowed_paths','readonly_paths'} else v for k,v in prepared['policy'].items()})
        service=w._service(prepared)
        provider=service.provider_factory(policy)
        origin={'kind':'issue','registry_path':str(w.registry.path),'investigation_id':identity}
        model=self._model(provider)
        basis={'prepared_sha256':fingerprint({k:v for k,v in prepared.items() if k!='revision'}),
               'issue_token':snap['token'],'config_sha256':config['sha256'],
               'model_identity':model}
        return dict(origin=origin,registry=w.registry,repository=w.source,repository_id=snap['repository_id'],
            slug=snap['repository'],branch=config['spec']['branch'],head_sha=snap['head_sha'],policy=policy,
            environment=prepared['environment'],grant=prepared['grant'],read_roots=config['spec']['read_roots'],
            write_roots=config['write_roots'],mode=config['mode'],model_identity=model,
            # The original Issue controller allows three rounds even in manual
            # mode, requiring a new approval per round. Monitor is capped above.
            max_attempts=3,basis=basis,issue_runs=service.runs,
            label='Issue #'+str(snap['issue']['number']),request=prepared.get('maintenance_request') or snap['issue']['title']+'\n'+snap['issue']['body'])

    def _resolve(self,selector):
        require(isinstance(selector,dict),'来源选择无效')
        if selector.get('kind')=='monitor':
            require(set(selector)=={'kind','monitor_id','event_id'},'监控关联字段无效')
            return self._monitor(selector['monitor_id'],selector['event_id'])
        require(set(selector)=={'kind','workspace_id','investigation_id'} and selector['kind']=='issue',
                'Issue关联字段无效')
        workspace=self.hub.workspaces.get(selector['workspace_id'])
        require(workspace is not None and workspace.session.issue_workbench is not None,'请先明确连接原Issue仓库')
        return self._issue(workspace.session.issue_workbench,selector['investigation_id'])

    @staticmethod
    def _public(context):
        return {k:context[k] for k in ('label','head_sha')}|{
            'request':context['request'][:600],'request_truncated':len(context['request'])>600,
            'origin_id':fingerprint(context['origin']),'basis_sha256':fingerprint(context['basis'])}

    def _pair(self,left,right):
        a,b=self._resolve(left),self._resolve(right)
        require(a['origin']!=b['origin'],'请选择两个不同的原来源')
        require(a['slug']==b['slug'],'不同GitHub仓库不能关联为同一修复')
        numeric=[c['repository_id'] for c in (a,b) if c['repository_id'] is not None]
        require(numeric and all(type(n) is int and n==numeric[0] for n in numeric),
                '缺少原受限读取取得的GitHub仓库编号；不猜测本地身份')
        group=fingerprint(sorted([fingerprint({'origin':c['origin'],'basis':c['basis']}) for c in (a,b)]))
        for c in (a,b):
            kwargs={k:c[k] for k in ('slug','branch','head_sha','policy','environment','grant','read_roots',
                                     'write_roots','mode','model_identity','max_attempts')}
            c['comparison']=repair_comparison(c['repository'],repository_id=numeric[0],fault_group=group,**kwargs)
            c['origin_id']=fingerprint(c['origin'])
            c['authority']={'schema_version':1,'origin':c['origin'],'basis':c['basis'],
                            'comparison':c['comparison']}
        # Use an existing monitor journal when present, otherwise an original
        # Issue journal. Persisted routes below forbid later silent reselection.
        anchors=sorted((c for c in (a,b) if c['origin']['kind']=='monitor'),key=lambda c:c['origin_id'])
        anchor=(anchors or sorted((a,b),key=lambda c:str(c['registry'].path)))[0]['registry']
        core={'schema_version':1,'coordinator_path':str(anchor.path),'coordinator_id':registry_identity(anchor),
              'origins':{c['origin_id']:{'origin':c['origin'],'ledger_id':registry_identity(c['registry']),
                  'authority':c['authority']} for c in (a,b)}}
        return a,b,anchor,core

    def preview(self,payload):
        require(set(payload)=={'left','right'},'关联预览字段无效')
        self.hub.runtime.require_current()
        a,b,_,core=self._pair(payload['left'],payload['right'])
        differences=[k for k in a['comparison'] if a['comparison'][k]!=b['comparison'][k]]
        value={**copy.deepcopy(payload),'id':fingerprint(core),'core':core,
            'origins':[self._public(a),self._public(b)],'comparison':{'comparable':not differences,'differences':differences},
            'budgets':[c['registry'].budget() for c in (a,b)],'model_calls':0,
            'warning':'相同检查与版本不证明根因相同。只有你确认同一故障才建立派发关联；不合账、不继承审批。'}
        return {'preview':value,'revision':self.hub.monitor.root_rules.challenge(value)}

    def _coordinator(self,registry,verify=None):
        handles=self._registries()
        return MaintenanceCorrelation(registry,[v for v in handles.values() if v is not registry],
                                      owner_validator=verify)

    def confirm(self,payload):
        require(set(payload)=={'preview','revision','confirmed'} and payload['confirmed'] is True,'需要明确确认同一故障')
        value=payload['preview']
        require(isinstance(value,dict) and payload['revision']==self.hub.monitor.root_rules.challenge(value),
                '关联预览已失效')
        with self.lock:
            expected=self.preview({k:value[k] for k in ('left','right')})
            require(fingerprint(expected)==fingerprint({'preview':value,'revision':payload['revision']}) and value['comparison']['comparable'],
                    '原来源、检查、环境、权限或额度已变化/不可比，不能合并派发')
            a,b,anchor,core=self._pair(value['left'],value['right']);pid=fingerprint(core)
            coordinator=self._coordinator(anchor)
            row=anchor.load(PAIRS).get(pid)
            require(not row or row['core']==core,'原关联意图已变化')
            for c in (a,b):
                route=c['registry'].load(ROUTES).get(c['origin_id'])
                require(not route or route=={'pair_id':pid,'coordinator_path':str(anchor.path),
                    'coordinator_id':core['coordinator_id'],'origin':c['origin']},'此原来源已经固定另一关联；不能迁移负责人')
                # Never retroactively claim work already dispatched without this fence.
                if route is None:
                    if c['origin']['kind']=='monitor':
                        event=self.hub.monitor.registries[c['origin']['monitor_id']].load('event')[c['origin']['event_id']]
                        require(not event.get('root_execution'),'已有监控派发，不能事后合并')
                    else:
                        require(not any(r['investigation_id']==c['origin']['investigation_id']
                            for r in c['registry'].load('issue_execution').values()),'已有Issue派发，不能事后合并')
            row=row or {'id':pid,'core':core,'status':'link_pending','created_at':now()}
            anchor.put(PAIRS,pid,row)  # Intent before writes to another original journal.
            observations={}
            for c in (a,b):
                route={'pair_id':pid,'coordinator_path':str(anchor.path),'coordinator_id':core['coordinator_id'],
                       'origin':c['origin']}
                c['registry'].put(ROUTES,c['origin_id'],route)
                observations[c['origin_id']]=coordinator.observe_source(c['registry'],{
                    **c['comparison'],'source_type':'issue' if c['origin']['kind']=='issue' else 'ci'
                        if c['basis'].get('binding',{}).get('ci_failure') else 'commit',
                    'source_id':c['origin_id'],'source_version_sha256':fingerprint(c['authority'])})['observation_id']
            ids=sorted(observations.values())
            proof={'schema_version':1,'approved':True,'actor':'local-operator','observations':ids,
                   'comparison_sha256':coordinator.comparison_fingerprint(ids[0])}
            proofid=fingerprint(proof);anchor.put(APPROVAL_KIND,proofid,proof)
            linked=coordinator.link(*ids,source_reference(anchor,APPROVAL_KIND,proofid))
            require(linked['status']=='related','原关联无法确认：'+linked.get('reason','请核对原记录'))
            row.update(status='linked',observations=observations,link_id=linked['link_id'],confirmed_at=now())
            anchor.put(PAIRS,pid,row)
            return {'status':'linked','id':pid,'model_calls':0,'budgets_merged':False}

    def _route(self,registry,origin):
        oid=fingerprint(origin);route=registry.load(ROUTES).get(oid)
        if route is None:
            # A torn cross-journal confirmation cannot turn into an unguarded dispatch.
            require(not any(oid in row['core']['origins'] for r in self._registries().values()
                            for row in r.load(PAIRS).values()),'原关联登记未完整结束；先核对，不派发')
            return None
        require(route['origin']==origin,'原关联来源不符')
        anchor=self._registries().get(route['coordinator_path'])
        require(anchor is not None and registry_identity(anchor)==route['coordinator_id'],
                '原协调账未打开或身份/额度元数据变化；保留认领，不另建账')
        row=anchor.load(PAIRS).get(route['pair_id'])
        require(row and row['status']=='linked' and fingerprint(row['core'])==row['id']
                and row['core']['coordinator_id']==route['coordinator_id'],'原关联尚未完整确认')
        for member in row['core']['origins'].values():
            other=self._registries().get(member['origin']['registry_path'])
            require(other is not None and registry_identity(other)==member['ledger_id']
                and other.load(ROUTES).get(fingerprint(member['origin']))=={
                    'pair_id':row['id'],'coordinator_path':str(anchor.path),'coordinator_id':route['coordinator_id'],
                    'origin':member['origin']},'关联对方原账未打开、登记或身份变化；不放行重复派发')
        return row,anchor

    def _acquire(self,registry,origin,execution_id,current,verify,previous=None,attempts=None):
        with self.lock:
            found=self._route(registry,origin)
            if found is None:return None
            budget=registry.budget()
            require(budget['requests_reserved_unknown']==0 and budget['requests_remaining']>0,
                    '原请求消费未知或额度耗尽；不开始新的关联派发')
            pair,anchor=found;oid=fingerprint(origin);member=pair['core']['origins'][oid]
            # Rebuild through the original controllers before accepting any owner.
            context=current();require(context['origin']==origin and context['basis']==member['authority']['basis'],
                '原调查、执行范围或配置变化；关联不能扩大为新权限')
            require(context['max_attempts']==attempts,
                    '实际候选轮次与原关联范围不符；不能改变已固定授权')
            observation=pair['observations'][oid]
            def authorized(source,intent,authority):
                verify()
                return authority=={'schema_version':1,'origin':origin,'basis':context['basis'],
                    'execution_id':execution_id} and source['source_version_sha256']==fingerprint(member['authority'])
            coordinator=self._coordinator(anchor,authorized)
            # An explicitly authorized continuation of the SAME source keeps its
            # chain owner. It does not allow an unrelated execution to replay.
            if previous:
                require(origin['kind']=='issue','原后继不能借用其他purpose的前驱')
                return self._claim_issue_previous(registry,pair,anchor,context,execution_id,previous,verify)
            authority={'schema_version':1,'origin':origin,'basis':context['basis'],'execution_id':execution_id}
            ownerid=fingerprint({'origin':origin,'execution_id':execution_id})
            old=registry.load(AUTHORITY).get(ownerid)
            require(old is None or old==authority,'原派发授权快照变化')
            registry.put(AUTHORITY,ownerid,authority)
            source=anchor.load(OBSERVATIONS)[observation]['source']
            intent={'schema_version':1,'coordinator_id':coordinator.coordinator_id,'observation_id':observation,
                'source_reference':source['reference'],'authority_reference':source_reference(registry,AUTHORITY,ownerid),
                'authority_sha256':source['authority_sha256']}
            registry.put(DISPATCH_KIND,ownerid,intent)
            owner=source_reference(registry,DISPATCH_KIND,ownerid)
            result=coordinator.claim(observation,owner)
            require(result['status']!='conflict','原派发认领受阻：'+result.get('reason','请核对原记录'))
            receipt={'origin':origin,'execution_id':execution_id,'pair_id':pair['id'],
                     'observation_id':observation,'claim_id':result['claim_id'],'dispatch_allowed':result['dispatch_allowed'],
                     'owner_reference':owner if result['dispatch_allowed'] else result['held_by']['owner_reference']}
            registry.put(RECEIPTS,execution_id,receipt)
            if result['dispatch_allowed']:
                coordinator.mark_outcome(result['claim_id'],owner,'started',revision=0);verify()
            return receipt

    @staticmethod
    def _verify_receipt(coordinator,pair,receipt):
        require(receipt['dispatch_allowed'] is True and receipt['pair_id']==pair['id'],'此来源没有原派发认领')
        with coordinator.registry.transaction() as db:
            row=coordinator._claim(db,receipt['claim_id'])
            require(row['core']=={'observation_id':receipt['observation_id'],
                'owner_reference':receipt['owner_reference']},'原派发负责人已变化')

    def claim_monitor(self,mid,event):
        registry=self.hub.monitor.registries[mid]
        origin={'kind':'monitor','monitor_id':mid,'event_id':event['id'],'registry_path':str(registry.path)}
        return self._acquire(registry,origin,'monitor:'+event['id'],lambda:self._monitor(mid,event['id']),
            lambda:self.hub.monitor.execution.context(mid,event['id']),attempts=event['root_execution']['max_attempts'])

    def claim_issue(self,w,identity,row,verify,attempts):
        origin={'kind':'issue','registry_path':str(w.registry.path),'investigation_id':identity}
        return self._acquire(w.registry,origin,row['id'],lambda:self._issue(w,identity),verify,
                             row.get('previous_execution'),attempts)

    @staticmethod
    def _issue_execution_pin(row):
        fields=('id','investigation_id','issue_key','issue_number','issue_token','repository_id',
            'rules_sha256','spec_sha256','policy_binding','environment_grant_sha256','source_before',
            'run_id','owner_process','created_at','previous_execution')
        require(isinstance(row,dict) and all(k in row for k in fields),'原Issue执行绑定缺失')
        require(row.get('continuation_kind') in {None,'environment_retry','review_revision'},
                '不同origin/purpose不能伪装为同源repair接续')
        return {k:row[k] for k in (*fields,'continuation_kind','previous_report_sha256',
            'review_origin_challenge','review_context') if k in row}

    def _issue_previous_owner(self,registry,pair,anchor,context,previous):
        origin=context['origin'];oid=fingerprint(origin)
        executions=registry.load('issue_execution');receipts=registry.load(RECEIPTS)
        old=receipts.get(previous)
        require(old and old.get('execution_id')==previous and old.get('origin')==origin,
                '缺少准确canonical前驱repair回执；不能重建认领')
        require(old.get('purpose','repair') in {'repair',ISSUE_CONTINUATION_PURPOSE}
                and old.get('observation_id')==pair['observations'][oid],
                '前驱purpose或本来源observation不同')
        coordinator=self._coordinator(anchor)
        self._verify_receipt(coordinator,pair,old)
        with anchor.transaction() as db:
            claim,authority=coordinator._claim(db,old['claim_id'],include_authority=True)
        require(claim['status'] not in {'unknown','interrupted'}
                and authority.get('origin')==origin and authority.get('basis')==context['basis'],
                '原repair负责人未知/中断或来源不同；不重放')
        root=authority['execution_id'];cursor=previous;seen=set()
        # Legacy untyped copies are accepted only through actual original Issue
        # rows leading to this claim's repair root, never by a copied label alone.
        # Bound by the actual durable snapshot, without imposing a second
        # execution capacity. The original Issue controller owns that limit.
        for _ in range(min(len(executions),len(receipts))):
            row=executions.get(cursor);receipt=receipts.get(cursor)
            require(cursor not in seen and row and row.get('id')==cursor and receipt
                    and row.get('investigation_id')==origin['investigation_id']
                    and row.get('issue_token')==context['basis']['issue_token']
                    and receipt.get('execution_id')==cursor and receipt.get('origin')==origin
                    and receipt.get('purpose','repair') in {'repair',ISSUE_CONTINUATION_PURPOSE}
                    and all(receipt.get(k)==old.get(k) for k in
                        ('pair_id','observation_id','claim_id','owner_reference','dispatch_allowed')),
                    '原Issue前驱链或repair目的不符')
            seen.add(cursor)
            parent=row.get('previous_execution')
            require(receipt.get('previous_execution')==parent,'前驱执行与回执链不一致')
            if cursor==root:
                require(parent is None,'原repair root不是根执行')
                return executions[previous],old,root
            require(isinstance(parent,str) and parent,'原Issue前驱链未到原repair root')
            cursor=parent
        require(False,'原Issue前驱链未在现存执行/回执集合内到达repair root')

    def _issue_parent_pin(self,registry,context,parent):
        require(parent.get('status') in {'paused','interrupted','failed_no_replay'},
                '前驱不是原控制器允许的repair接续状态')
        task=parent.get('task_id');link=registry.load('pi_task_link').get('run:'+parent['run_id'])
        require((not task and link is None) or task and link=={'task_id':task}
                and registry.load('pi_task').get(task,{}).get('task_id')==task,
                '原Issue前驱Native任务链接不同')
        path=unlinked(context['issue_runs']/parent['run_id']/'run.json')
        if not path.exists():
            require(not task,'已有Native的前驱报告缺失；不能另造任务')
            report={'kind':'absent_before_native'}
        else:
            record,sha=read_json(path)
            expected={'execution_id':parent['id'],'issue_number':parent['issue_number'],
                'issue_token':parent['issue_token'],'policy_binding_sha256':parent['policy_binding']['binding_sha256'],
                'task_id':task,'rules_sha256':parent['rules_sha256']}
            require(record.get('run_id')==parent['run_id'] and record.get('finished_at')
                    and record.get('status') not in {'candidate_ready','integrated'}
                    and record.get('source_repo')==str(context['repository'].root)
                    and record.get('issue_execution')==expected,'原Issue前驱报告归属或终态不同')
            report={'kind':'original_report','sha256':sha}
        return {'execution_sha256':fingerprint(parent),'report':report,'task_id':task,'task_link':link}

    def _claim_issue_previous(self,registry,pair,anchor,context,identity,previous,verify):
        parent,old,root=self._issue_previous_owner(registry,pair,anchor,context,previous)
        row=registry.load('issue_execution').get(identity)
        require(row and row['id']==identity and row.get('previous_execution')==previous
                and row.get('investigation_id')==context['origin']['investigation_id']
                and row.get('issue_token')==parent['issue_token']
                and row.get('issue_key')==parent['issue_key']
                and row.get('policy_binding')==parent['policy_binding']
                and row.get('environment_grant_sha256')==parent['environment_grant_sha256'],
                '新Issue执行不是准确同origin前驱的原授权范围')
        pin=self._issue_execution_pin(row);parent_pin=self._issue_parent_pin(registry,context,parent)
        verify()  # The original controller is required even when reusing its owner.
        receipt={**old,'purpose':ISSUE_CONTINUATION_PURPOSE,'execution_id':identity,
            'previous_execution':previous,'root_execution_id':root,'parent':parent_pin,
            'previous_receipt_sha256':fingerprint(old),'issue_execution_sha256':fingerprint(pin)}
        with registry.transaction() as db:
            def body(kind,key):
                raw=db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(kind,key)).fetchone()
                return json.loads(raw[0]) if raw else None
            current=body('issue_execution',identity)
            require(current and current.get('status')=='claimed' and not current.get('pause_requested')
                    and fingerprint(self._issue_execution_pin(current))==receipt['issue_execution_sha256']
                    and body('issue_execution_head',row['issue_key'])=={'execution_id':identity}
                    and fingerprint(body('issue_execution',previous))==parent_pin['execution_sha256']
                    and body(RECEIPTS,previous)==old and body(RECEIPTS,identity) is None,
                    'Issue原前驱/新执行/回执已变化；不重放')
            registry.put(RECEIPTS,identity,receipt,db)
        return receipt

    def _verify_issue_previous(self,registry,pair,anchor,context,receipt):
        if 'purpose' not in receipt:
            return self._verify_issue_legacy_previous(registry,pair,anchor,context,receipt)
        require(receipt.get('purpose')==ISSUE_CONTINUATION_PURPOSE
                and registry.load(RECEIPTS).get(receipt['execution_id'])==receipt,
                'Issue接续purpose或持久回执缺失/变化')
        parent,old,root=self._issue_previous_owner(registry,pair,anchor,context,receipt['previous_execution'])
        current=registry.load('issue_execution').get(receipt['execution_id'])
        require(current and current.get('correlation')==receipt and not current.get('pause_requested')
                and current.get('status') in {'claimed','running','awaiting_approval'}
                and fingerprint(self._issue_execution_pin(current))==receipt['issue_execution_sha256']
                and root==receipt['root_execution_id'] and fingerprint(old)==receipt['previous_receipt_sha256']
                and self._issue_parent_pin(registry,context,parent)==receipt['parent'],
                'Issue接续新执行或准确前驱已变化')
        task=current.get('task_id')
        require(task and registry.load('pi_task_link').get('run:'+current['run_id'])=={'task_id':task}
                and (not receipt['parent']['task_id'] or task==receipt['parent']['task_id']),
                'Issue接续没有保留原Native任务链接')

    def _verify_issue_legacy_previous(self,registry,pair,anchor,context,receipt):
        # Read-only compatibility for the exact historical shallow-copy schema.
        # Do not manufacture typed pins, rewrite an old approval, or accept a
        # typed receipt whose purpose alone was removed.
        require(set(receipt)=={'origin','execution_id','pair_id','observation_id','claim_id',
                    'dispatch_allowed','owner_reference','previous_execution'}
                and registry.load(RECEIPTS).get(receipt['execution_id'])==receipt,
                '旧Issue接续回执不是准确历史格式或持久原记录')
        current,canonical,_=self._issue_previous_owner(registry,pair,anchor,context,receipt['execution_id'])
        self._issue_execution_pin(current)
        parent=registry.load('issue_execution').get(receipt['previous_execution'])
        require(canonical==receipt and current.get('correlation')==receipt
                and not current.get('pause_requested') and current.get('status') in {'claimed','running'}
                and parent and all(current.get(k)==parent.get(k) for k in
                    ('investigation_id','issue_key','issue_number','issue_token','repository_id',
                     'rules_sha256','policy_binding','environment_grant_sha256','source_before')),
                '旧Issue接续不再是原活跃repair或准确前驱范围')
        parent_pin=self._issue_parent_pin(registry,context,parent)
        task=current.get('task_id')
        require(task and registry.load('pi_task_link').get('run:'+current['run_id'])=={'task_id':task}
                and registry.load('pi_task').get(task,{}).get('task_id')==task
                and (not parent_pin['task_id'] or task==parent_pin['task_id']),
                '旧Issue接续没有保留原Native任务链接')

    @staticmethod
    def _continuation_receipt(root,row):
        """A narrower reference to the existing repair owner, never a new claim."""
        return {**root,'purpose':MONITOR_CONTINUATION_PURPOSE,
            'execution_id':'monitor-continuation:'+row['id'],
            'root_execution_id':root['execution_id'],'root_receipt_sha256':fingerprint(root),
            'continuation_id':row['id'],'parent_run_id':row['parent_run_id'],
            'parent_record_sha256':row['parent_record_sha256'],
            'inspection_sha256':row['inspection_sha256'],
            'original_intent_sha256':row['original_intent_sha256'],
            'binding_sha256':fingerprint(row['binding']),'task_sha256':fingerprint(row['task'])}

    def _monitor_owner(self,mid,event):
        registry=self.hub.monitor.registries[mid]
        origin={'kind':'monitor','monitor_id':mid,'event_id':event['id'],'registry_path':str(registry.path)}
        found=self._route(registry,origin)
        root=event.get('root_execution',{}).get('correlation')
        if found is None:
            require(root is None,'原负责人回执失去关联路由；不能另建认领')
            return None
        require(root and root.get('origin')==origin and root.get('execution_id')=='monitor:'+event['id']
                and root.get('purpose','repair')=='repair',
                '关联来源缺少准确repair原负责人回执；只读提议不能授予后继权限')
        pair,anchor=found
        require(registry.load(RECEIPTS).get(root['execution_id'])==root
                and root.get('observation_id')==pair['observations'][fingerprint(origin)],
                '原负责人回执不属于此来源原登记；不能借另一来源接续')
        self.verify(registry,root,lambda:self._monitor(mid,event['id']))
        coordinator=self._coordinator(anchor)
        with anchor.transaction() as db:
            claim=coordinator._claim(db,root['claim_id'])
        require(claim['status'] not in {'unknown','interrupted'},'原负责人消费或执行未知；不重放后继')
        return root

    def claim_monitor_continuation(self,mid,event,row,verify):
        """Fence an explicitly confirmed same-event continuation before Native/model."""
        with self.lock:
            root=self._monitor_owner(mid,event)
            if root is None:return None
            registry=self.hub.monitor.registries[mid]
            budget=registry.budget()
            require(not budget['requests_reserved_unknown'] and budget['requests_remaining']>0,
                    '原请求消费未知或额度耗尽；不开始新的关联后继')
            require(row.get('kind')=='continuation' and row.get('event_id')==event['id']
                    and row.get('status')=='started','后继原意图或purpose不符')
            require(registry.load('monitor_continuation_head').get(event['id'])=={'id':row['id']}
                    and registry.load('monitor_continuation').get(row['id'])==row,
                    '后继原意图已变化；不能分配替代任务')
            verify()  # Original source/controller guard, not an execution authorization.
            receipt=self._continuation_receipt(root,row)
            require(row['id'] not in registry.load(RECEIPTS),'关联后继派发回执已存在；不重放')
            registry.put(RECEIPTS,row['id'],receipt)
            return receipt

    def _verify_monitor_continuation(self,mid,event,root):
        registry=self.hub.monitor.registries[mid]
        head=registry.load('monitor_continuation_head').get(event['id'])
        if not head:return
        row=registry.load('monitor_continuation').get(head['id'])
        require(row and row['event_id']==event['id'],'原后继引用缺失')
        if row['kind']=='reconciled_result':
            require(set(row)=={'id','kind','event_id','binding','status','plan_id','run_id','task',
                               'original_intent_sha256','created_at'}
                    and row.get('status') in {'candidate_ready','awaiting_approval'}
                    and registry.load(RECEIPTS).get(row['id']) is None,
                    '原只读结果核对不能成为新的派发意图')
            return
        require(row['kind']=='continuation','原后继purpose类型已变化；不能跳过派发核验')
        receipt=row.get('correlation')
        require(receipt and receipt==self._continuation_receipt(root,row)
                and registry.load(RECEIPTS).get(row['id'])==receipt,
                '原后继purpose、负责人或准确派发回执缺失/变化；不重放')

    def verify_monitor(self,mid,event):
        with self.lock:
            root=self._monitor_owner(mid,event)
            if root is not None:self._verify_monitor_continuation(mid,event,root)

    def verify(self,registry,receipt,current):
        if receipt is None:return
        with self.lock:
            found=self._route(registry,receipt['origin']);require(found is not None,'原关联路由缺失')
            pair,anchor=found;member=pair['core']['origins'][fingerprint(receipt['origin'])]
            context=current()
            require(context['basis']==member['authority']['basis'],'原关联执行来源变化')
            self._verify_receipt(self._coordinator(anchor),pair,receipt)
            if receipt['origin']['kind']=='issue':
                row=registry.load('issue_execution').get(receipt.get('execution_id'),{})
                if (receipt.get('previous_execution') or row.get('previous_execution')
                        or receipt.get('purpose')==ISSUE_CONTINUATION_PURPOSE):
                    self._verify_issue_previous(registry,pair,anchor,context,receipt)

    def _source_view(self,origin):
        """Names from already-open original records; no reconstruction or reads of GitHub."""
        value={'kind':origin['kind'],'origin_id':fingerprint(origin)}
        registry=self._registries().get(origin['registry_path'])
        require(registry is not None,'原来源账未打开')
        if origin['kind']=='issue':
            identity=origin['investigation_id']
            row=registry.load('issue_diagnosis').get(identity)
            require(row and row['snapshot']['token']==identity,'原Issue调查引用缺失')
            number=row['snapshot']['issue']['number']
            require(type(number) is int and number>0,'原Issue编号无效')
            value.update(label='Issue #'+str(number),investigation_id=identity)
            workspaces=[key for key,w in self.hub.workspaces.items()
                        if w.session.registry is registry]
            if len(workspaces)==1:value['workspace_id']=workspaces[0]
        else:
            mid,eid=origin['monitor_id'],origin['event_id']
            require(self.hub.monitor.registries.get(mid) is registry
                    and registry.load('event').get(eid),'原监控事件引用缺失')
            value.update(label='提交 / CI · '+mid[:8]+' / '+eid[:8],monitor_id=mid,event_id=eid)
        return value

    def _owner_view(self,registry,pair,claim):
        coordinator=self._coordinator(registry)
        with registry.transaction() as db:
            verified,authority=coordinator._claim(db,claim['id'],include_authority=True)
        require(verified==claim and set(authority)=={'schema_version','origin','basis','execution_id'}
                and authority['schema_version']==1,'原负责人快照无效')
        origin=authority['origin'];oid=fingerprint(origin)
        member=pair['core']['origins'].get(oid)
        require(member and member['origin']==origin and member['authority']['basis']==authority['basis']
                and pair['observations'][oid]==claim['core']['observation_id'],'负责人不属于原关联')
        value=self._source_view(origin)
        source=self._registries()[origin['registry_path']]
        if origin['kind']=='issue':
            execution=source.load('issue_execution').get(authority['execution_id'])
            if execution and execution['id']==authority['execution_id'] and execution['investigation_id']==origin['investigation_id']:
                value['execution_id']=execution['id']
                if execution.get('run_id'):value['run_id']=execution['run_id']
        else:
            require(authority['execution_id']=='monitor:'+origin['event_id'],'原监控负责人不符')
            execution=source.load('event')[origin['event_id']].get('root_execution',{})
            if execution.get('run_id'):value['run_id']=execution['run_id']
        return value

    def view(self):
        rows=[]
        for registry in self._registries().values():
            for pair in registry.load(PAIRS).values():
                claims=[r for r in registry.load(CLAIMS).values()
                    if r['core']['observation_id'] in pair.get('observations',{}).values()]
                status=pair['status'];reason=None;owner_source=None;sources=[]
                try:
                    member=next(iter(pair['core']['origins'].values()))
                    source=self._registries().get(member['origin']['registry_path'])
                    require(source is not None,'原来源账未打开')
                    self._route(source,member['origin'])
                    sources=[self._source_view(v['origin']) for v in pair['core']['origins'].values()]
                    require(len(claims)<=1,'关联存在多个负责人')
                    if claims:owner_source=self._owner_view(registry,pair,claims[0])
                except (SafetyError,KeyError,ValueError,TypeError):
                    status='blocked';reason='关联未完成，或原账未打开/身份发生变化；核对原记录，不重建认领。'
                rows.append({'id':pair['id'],'status':status,'reason':reason,'sources':sources or [
                    {'kind':v['origin']['kind'],'origin_id':key} for key,v in pair['core']['origins'].items()],
                    'owner':claims[0]['core'] if len(claims)==1 else None,
                    'owner_source':owner_source if status!='blocked' else None,
                    'message':'负责人只限制重复派发；进度和交付以各自原任务为准。','budgets_merged':False})
        return {'links':rows,'model_calls_on_read':0}
