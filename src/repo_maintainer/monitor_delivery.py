"""Monitor-owned delivery using the same branch/PR algorithms, not Issue claims."""
from dataclasses import asdict
import json
from pathlib import Path

from .dashboard import read_json,unlinked
from .git import RepositoryGateway
from .github_connection import github_url
from .github_pr import GitHubPullRequests
from .integration import prepare_integration,integrate,verified_publication
from .issue_delivery import CandidateDelivery,require
from .issue_publication import CandidatePublication
from .issue_intake import fingerprint
from .monitor_history import identity,journal,object_at


class MonitorDelivery(CandidateDelivery):
    kind='monitor_delivery'

    def __init__(self,monitor,monitor_id,event_id,remote=None):
        identity(monitor_id,16);identity(event_id,16)
        require(monitor_id in monitor.records,'未知原监控仓库')
        self.monitor,self.mid,self.eid=monitor,monitor_id,event_id
        super().__init__(monitor.registries[monitor_id],remote if remote is not None else GitHubPullRequests())

    def _record_origin(self,row,run_id):
        binding=row['core']['binding']
        require(binding.get('origin_kind')=='monitor' and binding.get('monitor_id')==self.mid
            and binding.get('event_id')==self.eid and binding.get('run_id')==run_id,
            '原交付意图不属于此监控事件或运行')
        return row

    def _get(self,identity,db):
        return self._record_origin(super()._get(identity,db),identity)

    def _paths(self,run_id):
        identity(run_id,32)
        root=unlinked(self.monitor.root/self.mid/'r'/self.eid)
        return root,unlinked(root/run_id)

    def _native(self,source,policy,record,link,metadata):
        if link is None:
            require(metadata['provider']['type']!='PiProvider','原Pi任务关联缺失；不能新建或猜测')
            return None
        require(isinstance(link,dict) and set(link)=={'task_id'},'原Native关联格式不符')
        from .task_session import TaskSessionStore
        task_id=identity(link['task_id'],32)
        task=self.registry.load('pi_task').get(task_id)
        require(task is not None,'原共享账中的Pi任务缺失')
        store=TaskSessionStore(self.registry,source.root,source.common_dir)
        task=store._load(task_id,task['scope'])
        expected_policy={**asdict(policy),'timeout_seconds':float(policy.timeout_seconds)}
        require(task['revision']>0 and task['scope']['base_sha']==record['base_sha']
            and task['scope']['issue_sha256']==fingerprint(record['issue'])
            and task['scope']['policy_sha256']==fingerprint(expected_policy)
            and task['scope']['tests_sha256']==fingerprint(policy.allowed_tests),'原Pi任务与候选或范围不符')
        with store.lease(task_id,task['scope'],task['revision']) as lease:
            lease.descriptor()  # Original full-file/schema verification; no model/SDK recovery.
        return {'task_id':task_id,'revision':task['revision'],
            'snapshot_sha256':task['checkpoint']['snapshot_sha256']}

    def _origin(self,run_id):
        root,directory=self._paths(run_id)
        event,source,policy,factory,controller=self.monitor.execution.guard(self.mid,self.eid,return_context=True)
        control=self.monitor.execution.current_execution(self.mid,event)
        require(control['status'] in {'candidate_ready','awaiting_approval'},'原事件尚无待交付候选')
        with journal(root) as db:
            meta=json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
            require(meta.get('repository')==str(source.root)
                and meta.get('policy')==json.loads(json.dumps(asdict(policy)))
                and meta.get('shared_budget')==str(self.registry.path)
                and meta.get('target_environment')=={'plan_sha256':factory.plan['plan_sha256'],
                    'grant_sha256':factory.grant['grant_sha256'],'controller':controller},'原事件日志/范围/共享账绑定不符')
            saved=object_at(db,'run',run_id)
            require(saved.get('run_id')==run_id and not saved.get('resolution'),'原运行登记缺失或已处置')
            row=db.execute("SELECT body FROM objects WHERE kind='pi_task_link' AND id=?",('run:'+run_id,)).fetchone()
            link=json.loads(row[0]) if row else None
            if control.get('run_id'):
                require(control['run_id']==run_id,'只允许当前原事件候选；旧后继不能授权交付')
                origin_plan=None
            else:
                plan_id=identity(control['plan_id'],32);plan=object_at(db,'plan',plan_id)
                require(plan['view']['status']=='consumed' and plan['view']['run_id']==run_id,
                        '原方案尚未新审批或不是此候选')
                origin_plan={'plan_id':plan_id,'file_sha256':plan['file_sha']}
        record,record_sha=read_json(directory/'run.json')
        require(record.get('run_id')==run_id and record.get('source_repo')==str(source.root)
            and record.get('base_sha')==event['sha'] and record.get('policy')==json.loads(json.dumps(asdict(policy)))
            and not record.get('issue_execution') and record.get('source_unchanged') is True
            and record.get('status') in {'candidate_ready','integrated'},'候选与原监控来源或状态不符')
        environment,environment_sha=read_json(unlinked(directory/'environment/environment.json'))
        require(environment.get('status')=='prepared' and environment.get('plan')==factory.plan
            and environment.get('grant')==factory.grant and isinstance(record.get('environment'),dict)
            and record['environment'].get('status')=='prepared'
            and record['environment'].get('binding')==environment.get('environment_binding'),
            '原目标环境或执行回执缺失；不能回退到主机环境发布')
        if origin_plan:
            require(all(record.get('planning_origin',{}).get(k)==v for k,v in origin_plan.items()),
                    '原审批方案与候选反向关联不符')
        native=self._native(source,policy,record,link,meta)
        spec=self.monitor.records[self.mid]['spec']
        original=RepositoryGateway(unlinked(Path(spec['path'])))
        slug,_=github_url(original.command('config','--get','remote.origin.url').decode().strip())
        if spec['github_url']:require(github_url(spec['github_url'])[0].casefold()==slug.casefold(),'原监控/仓库GitHub来源已变化')
        slug=slug.casefold();remote=self.remote.api('/repos/'+slug)
        require(isinstance(remote,dict) and type(remote.get('id')) is int and remote['id']>0
            and str(remote.get('full_name','')).casefold()==slug,'GitHub仓库身份不可核验')
        if controller.get('ci_failure'):
            require(controller['ci_failure']['repository_id']==remote['id'],'CI与交付仓库身份不同')
        origin={'origin_kind':'monitor','monitor_id':self.mid,'event_id':self.eid,'run_id':run_id,
            'repository':slug,'repository_id':remote['id'],'base':spec['branch'],'base_sha':event['sha'],
            'root_rules_sha256':controller['root_rules_sha256'],'controller_sha256':fingerprint(controller),
            'control_sha256':fingerprint({k:control.get(k) for k in ('binding','job_id','plan_id','run_id',
                'parent_run_id','parent_record_sha256','task','original_intent_sha256')}),
            'run_record_sha256':record_sha,'environment_origin_sha256':environment_sha,'native':native}
        return record,origin

    def _local(self,run_id):
        record,origin=self._origin(run_id);_,directory=self._paths(run_id)
        proof=verified_publication(directory);review=proof['review']
        # The verifier appends diagnostic Git commands to its in-memory copy.
        # Bind the persisted original bytes, without saving those transient reads.
        require(read_json(directory/'run.json')[1]==origin['run_record_sha256'],
                '原封装候选在核对期间发生变化')
        binding={**origin,'commit':review['commit'],'head':review['target_branch'],
            'review_sha256':proof['review_sha256'],'diff_sha256':review['diff_sha256'],
            'policy_sha256':fingerprint(record['policy']),
            'candidate_tests_sha256':fingerprint(record['candidate_tests']),
            'sealed_tests_sha256':fingerprint(review['tests']),'test_count':len(review['tests']),
            'environment_sha256':fingerprint(record.get('environment'))}
        return record,binding

    def _fresh(self,binding):
        _,current=self._local(binding['run_id'])
        require(current==binding,'原监控候选、Native或来源已变化；不执行旧批准')

    def _publication_paths(self,binding):
        require(binding.get('origin_kind')=='monitor' and binding.get('monitor_id')==self.mid
            and binding.get('event_id')==self.eid,'交付载体来源不符')
        _,directory=self._paths(binding['run_id'])
        source=RepositoryGateway(unlinked(self.monitor.root/self.mid/'events'/self.eid/'source'))
        return unlinked(source.common_dir/'objects'),unlinked(directory/'remote-publication')

    def _payload(self,run_id,binding):
        body=('Verified monitor candidate; awaiting human review. No merge or Issue closure is authorized.\n\n'
            +'\n'.join(f'- {k}: `{binding[k]}`' for k in ('monitor_id','event_id','run_id','base_sha','commit',
                'root_rules_sha256','controller_sha256','control_sha256','review_sha256',
                'candidate_tests_sha256','sealed_tests_sha256','environment_sha256','test_count'))
            +f'\n\n<!-- repocare-monitor-delivery:{self.mid}:{self.eid}:{run_id}:{binding["review_sha256"]} -->\n')
        return {'title':'RepoCare: monitor candidate '+run_id[:12],'body':body,
            'head':binding['head'],'base':binding['base'],'draft':True}

    def operate(self,action,payload,transport=None):
        run_id=payload['run_id'];identity(run_id,32)
        if action.startswith('monitor_delivery_watch_'):
            return self.monitor.acceptance_watch.operate(self,action,payload)
        if action in {'monitor_delivery_acceptance_preview','monitor_delivery_acceptance_verify'}:
            from .monitor_acceptance import MonitorAcceptance
            service=MonitorAcceptance(self,getattr(self.monitor.hub,'monitor_acceptance_remote',None))
            if action.endswith('_preview'):return service.prepare(run_id)
            row=service.view(payload['acceptance_id'])
            require(row['run_id']==run_id,'验收提案不属于此原运行')
            return service.verify(row['id'],payload['revision'],payload['approval_sha256'])
        publisher=MonitorPublication(self,transport)
        if action=='monitor_delivery_prepare_local':
            self._origin(run_id);_,directory=self._paths(run_id)
            return prepare_integration(directory)
        if action=='monitor_delivery_publish_local':
            self._origin(run_id);_,directory=self._paths(run_id)
            return integrate(directory,payload['revision'])
        if action=='monitor_delivery_push_preview':return publisher.prepare(run_id)
        if action=='monitor_delivery_pr_preview':return self.prepare(run_id)
        if action=='monitor_delivery_push':
            return publisher.push(run_id,payload['revision'],approve_sha256=payload['approval_sha256'],actor='local-workbench')
        if action=='monitor_delivery_pr':
            return self.create(run_id,payload['revision'],approve_sha256=payload['approval_sha256'],actor='local-workbench')
        require(action=='monitor_delivery_reconcile' and payload['kind'] in {'push','pr'},'未知监控交付操作')
        return (publisher if payload['kind']=='push' else self).reconcile(run_id)


class MonitorPublication(CandidatePublication):
    kind='monitor_publication'

    def _get(self,identity,db):
        return self.delivery._record_origin(super()._get(identity,db),identity)
