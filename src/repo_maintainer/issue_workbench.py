"""Issue actions in the original UISession queue, registry and request ledger.

The local operator registers immutable maintenance roots. Investigation selects
exact files/checks; neither Issue text nor a model can register that authority.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import threading
import time
import uuid

from .git import RepositoryGateway
from .issue_intake import IssueIntake, IssueReader, fingerprint, specification
from .maintenance_rules import maintenance_rules, compile_policy, validate_rules
from .models import SafetyError
from .store import now, redact
from .target_environment import discover_environment, make_grant


def require(value, message):
    if not value:
        raise SafetyError(message)


def metadata(registry):
    with registry.transaction() as db:
        value = json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
    # A separately approved original budget extension does not change source,
    # provider or maintenance authority. Never rewrite its used/held counters.
    value['provider'].pop('request_budget', None)
    return value


def authorized_rules(registry, repository, spec, rules):
    """A derivative of explicit local configuration, in the SAME original ledger.

An externally supplied rules hash is not sufficient. Reconstruct the rules from
the registered maintainer roots and the pinned controller environment proposal.
"""
    try:
        config = registry.load('issue_workbench_config')['current']
        prepared = registry.load('issue_workbench_scope')[rules['rules_sha256']]
        core = {k: v for k, v in config.items() if k != 'sha256'}
        if config['sha256'] != fingerprint(core) or config['metadata_sha256'] != fingerprint(metadata(registry)):
            return False
        if prepared['config_sha256'] != config['sha256'] or prepared['spec'] != spec or config['spec'] != spec:
            return False
        plan = prepared['environment']
        expected = maintenance_rules(repository, read_roots=spec['read_roots'],
            write_roots=config['write_roots'], check_ids=[c['id'] for c in plan['checks']],
            mode=config['mode'], allow_auto=config['mode'] == 'auto',
            timeout_seconds=config['timeout_seconds'], max_files=prepared['rules']['max_files'],
            actor='local-workbench')
        validate_rules(rules, repository)
        return expected == rules == prepared['rules']
    except (KeyError, TypeError, ValueError, SafetyError):
        return False


FIELDS = {
    'issue_progress_investigation_preview': {'investigation_id','stage'},
    'issue_progress_investigation_send': {'investigation_id','progress_id','approval_sha256','expected_actor_id'},
    'issue_progress_investigation_reconcile': {'investigation_id','progress_id'},
    'issue_progress_preview': {'execution_id','stage'},
    'issue_progress_send': {'execution_id','progress_id','approval_sha256','expected_actor_id'},
    'issue_progress_reconcile': {'execution_id','progress_id'},
    'issue_watch_preview': {'expires_at','interval_seconds','max_attempts'},
    'issue_watch_configure': {'preview','revision','confirmed'},
    'issue_watch_check': set(),
    'issue_watch_pause': {'grant_id'}, 'issue_watch_revoke': {'grant_id'},
    'issue_config_preview': {'read_roots','write_roots','trusted_actors','mode','allow_install','allow_network','workflow_ids'},
    'issue_configure': {'config','revision'},
    'issue_read': {'number'}, 'issue_investigate': {'number'},
    'issue_environment': {'investigation_id'},
    'issue_validation_preview': {'investigation_id'},
    'issue_validation_clarification_preview': {'investigation_id','revision','answer'},
    'issue_validation_clarify': {'investigation_id','preview','revision','confirmed'},
    'issue_validation_propose': {'investigation_id','revision'},
    'issue_validation_review': {'investigation_id','revision','decision'},
    'issue_validation_reconcile': {'investigation_id','revision'},
    'issue_successor_preview': {'number'},
    'issue_successor_configure': {'preview','revision','confirmed'},
    'issue_successor_execute': {'successor_id','revision'},
    'issue_execute': {'investigation_id','revision'},
    'issue_decide': {'approval_id','revision','decision'},
    'issue_request_changes': {'approval_id','revision','reason'},
    'issue_review_revision_preview': {'execution_id','reason'},
    'issue_revise_review': {'execution_id','revision','reason'},
    'issue_pause': {'execution_id','revision'},
    'issue_resume': {'execution_id','revision'},
    'issue_environment_retry_preview': {'execution_id'},
    'issue_retry_environment': {'execution_id','revision'},
    'issue_takeover': {'execution_id','revision'},
    'issue_result': {'execution_id'}, 'issue_recover_preview': {'execution_id'},
    'issue_prepare_local': {'execution_id'},
    'issue_publish_local': {'execution_id','revision'},
    'issue_recover': {'execution_id','revision'},
    'issue_push_preview': {'execution_id'}, 'issue_push': {'execution_id','revision','approval_sha256'},
    'issue_pr_preview': {'execution_id'}, 'issue_pr': {'execution_id','revision','approval_sha256'},
    'issue_close_preview': {'execution_id'}, 'issue_close': {'execution_id','revision','approval_sha256'},
    'issue_reconcile': {'execution_id','kind'},
}


def validate_action(action, body):
    from .local_ui import identifier, SHA
    require(action in FIELDS and isinstance(body, dict)
            and set(body) == FIELDS[action] | {'request_id'}, 'Unexpected Issue operation fields')
    identifier(body['request_id'])
    for key in ('execution_id','approval_id','successor_id'):
        if key in body: identifier(body[key])
    for key in ('revision','approval_sha256','investigation_id','progress_id'):
        if key in body:
            require(isinstance(body[key], str) and SHA.fullmatch(body[key]), 'Expected full Issue SHA-256')
    if 'number' in body:
        require(type(body['number']) is int and 1 <= body['number'] <= 9999999999, 'Invalid Issue number')
    if 'stage' in body:
        from .issue_progress import STAGES
        require(body['stage'] in STAGES, 'Unsupported Issue comment stage')
    if 'expected_actor_id' in body:
        require(type(body['expected_actor_id']) is int and 0 < body['expected_actor_id'] <= 2**53-1,
                'Exact approved GitHub comment account required')
    if 'decision' in body:
        require(body['decision'] in (('accept','reject') if action=='issue_validation_review' else ('approve','reject')),
                'Invalid Issue decision')
    if 'reason' in body:
        from .issue_review import review_text
        require(review_text(body['reason'])==body['reason'],'Review feedback must be trimmed')
    if 'kind' in body: require(body['kind'] in ('push','pr','close'), 'Invalid reconciliation kind')
    return {k:v for k,v in body.items() if k != 'request_id'}


class IssueWorkbench:
    def __init__(self, session, connection, *, permit_auto=False, remote=None, transport=None, comment_remote=None):
        self.session, self.registry, self.connection = session, session.registry, connection
        self.source = RepositoryGateway(session.repo)
        self.permit_auto, self.remote, self.transport = permit_auto, remote, transport
        self.comment_remote = comment_remote
        self.waiters, self.services = {}, {}
        self.lock = threading.RLock()
        # Reopening never resumes a waiting approval or replays a model/write.
        for identity, row in self.registry.load('issue_approval').items():
            if row['status'] == 'pending':
                self.registry.put('issue_approval', identity, {**row,'status':'interrupted','finished_at':now()})
        self.watch_context=None
        from .issue_watch import IssueWatch
        self.watch=IssueWatch(self)

    def view(self):
        config = self.registry.load('issue_workbench_config').get('current')
        executions = self.registry.load('issue_execution')
        approvals = []
        for row in self.registry.load('issue_approval').values():
            approvals.append({**row,'revision':self.challenge(row)})
        from .issue_validation import projection as validation_projection
        return {'configured':bool(config), 'configuration':config,'validations':validation_projection(self),
            'configurations_immutable':True, 'executions':[
                {**r, 'challenge':fingerprint(r)} for r in executions.values()],
            'investigations':[{k:v for k,v in r.items() if k!='report'}
                for r in self.registry.load('issue_diagnosis').values()],
            'environments':[{**{k:v for k,v in r.items() if k not in {'environment','grant','spec'}},'revision':self.challenge(r)}
                for r in self.registry.load('issue_workbench_environment').values()],
            'approvals':approvals,
            'successors':[{**row,'challenge':fingerprint(row)}
                          for row in self.registry.load('issue_successor').values()],
            'publications':list(self.registry.load('issue_publication').values()),
            'pull_requests':list(self.registry.load('issue_delivery').values()),
            'closures':list(self.registry.load('issue_closure').values()),
            'progress_comments':[{k:r[k] for k in ('id','core','approval_sha256','status','receipt','continuity_error','actor_id') if k in r}
                for r in self.registry.load('issue_progress').values()],
            'permit_auto':self.permit_auto, 'model_calls_on_read':0,'watch':self.watch.view()}

    def challenge(self, value):
        return fingerprint({'value':value,'session':self.session.token})

    def _config(self):
        value = self.registry.load('issue_workbench_config').get('current')
        require(value is not None, '请先设置 Issue 维护规则')
        require(value['sha256'] == fingerprint({k:v for k,v in value.items() if k != 'sha256'})
                and value['metadata_sha256'] == fingerprint(metadata(self.registry)), '原维护规则或账本绑定已变化')
        return value

    def config_preview(self, payload):
        from .github_connection import github_url
        require(set(payload) == FIELDS['issue_config_preview'], 'Unexpected maintainer configuration fields')
        url = github_url(self.source.command('config','--get','remote.origin.url').decode().strip())[1]
        branch = self.source.command('branch','--show-current').decode().strip()
        spec = specification(url, branch, payload['read_roots'], payload['trusted_actors'], True)
        require(spec['trusted_label_actors'], '请填写可准入任务的维护者 GitHub 用户名')
        # Existing rule validation, with a placeholder check ID used ONLY to validate roots/mode.
        rule = maintenance_rules(self.source, read_roots=spec['read_roots'],
            write_roots=payload['write_roots'], check_ids=['scope-validation-only'],
            mode=payload['mode'], allow_auto=self.permit_auto,
            timeout_seconds=self.session.policy.timeout_seconds, actor='local-workbench')
        for key in ('allow_install','allow_network'):
            require(type(payload[key]) is bool, '环境许可须明确选择')
        require(not payload['allow_network'] or payload['allow_install'], '联网安装需要同时允许依赖安装')
        from .issue_acceptance import acceptance_policy
        workflows = payload['workflow_ids']
        require(isinstance(workflows,list), '验收工作流 ID 必须是列表')
        if workflows: acceptance_policy(workflows)
        core = {'schema_version':1, 'spec':spec, 'write_roots':rule['write_roots'],
            'mode':rule['mode'], 'timeout_seconds':rule['timeout_seconds'],
            'allow_install':payload['allow_install'], 'allow_network':payload['allow_network'],
            'workflow_ids':workflows, 'metadata_sha256':fingerprint(metadata(self.registry)),
            'scope_semantics':'controller-selected tracked implementation and fixed discovered test templates',
            'local_execution_is_os_sandbox':False}
        value = {**core,'sha256':fingerprint(core)}
        return {'configuration':value,'revision':self.challenge(value)}

    def configure(self, value, revision):
        require(isinstance(value,dict) and revision == self.challenge(value), '配置预览已失效，请重新预览')
        reconstructed = self.config_preview({'read_roots':value['spec']['read_roots'],
            'write_roots':value['write_roots'],'trusted_actors':value['spec']['trusted_label_actors'],
            **{k:value[k] for k in ('mode','allow_install','allow_network','workflow_ids')}})['configuration']
        require(reconstructed == value, '维护配置与本地预览不符')
        with self.registry.transaction() as db:
            old = db.execute("SELECT body FROM objects WHERE kind='issue_workbench_config' AND id='current'").fetchone()
            require(not old or json.loads(old[0]) == value, '此原账本已有固定 Issue 规则；不能换范围或重置额度')
            self.registry.put('issue_workbench_config','current',value,db)
        return {'status':'configured','configuration':value, **self.registry.budget()}

    def environment(self, identity):
        config = self._config()
        job = self.registry.load('issue_diagnosis').get(identity)
        require(job and job['status']=='investigated' and job['snapshot']['status']=='ready', '需要当前可接手的调查结果')
        fresh = IssueReader(self.connection,config['spec'],registry=self.registry).snapshot(job['snapshot']['issue']['number'])
        require(fresh.get('token') == identity and self.source.head == job['snapshot']['head_sha'], 'Issue 或目标版本已变化，请重新调查')
        from .issue_validation import service as validation_service
        # Only look for an accepted original proposal. Reading this phase never
        # calls the model or creates a new Native task/request reservation.
        contract=None
        from .issue_validation import projection as validation_projection
        if any(row['investigation_id']==identity for row in validation_projection(self)):
            validation=validation_service(self,identity)
            original=self.registry.load('validation_proposal').get(validation.identity)
            if (original is None or original['status'] in {'started','awaiting_validation_review','clarification_needed','interrupted_no_replay'}
                    or validation.clarification_depth()>0 and original['status']!='accepted'):
                return {'id':identity,'status':'validation_review_required','validation_id':validation.identity,
                        'runtime_authorized':False}
            contract=validation.accepted()
        from .task_test_selection import NoTaskTests
        try:
            plan = discover_environment(self.source,self.source.head,config['spec']['read_roots'],investigation=job['report'],
                                        behavior_contract=contract)
        except NoTaskTests:
            require(contract is None,'已审阅案例未能形成原检查，不能跳过验证')
            return {'id':identity,'status':'validation_needed','blockers':['没有现成检查，请先让Agent提议验收案例'],
                    'runtime_authorized':False}
        if plan['status']!='proposed' and contract is None:
            return {'id':identity,'status':'validation_needed','blockers':plan['blockers'],
                    'runtime_authorized':False}
        require(plan['status']=='proposed', '目标环境阻塞：'+ '; '.join(plan['blockers']))
        rules = maintenance_rules(self.source,read_roots=config['spec']['read_roots'],
            write_roots=config['write_roots'],check_ids=[c['id'] for c in plan['checks']],
            mode=config['mode'],allow_auto=config['mode']=='auto',timeout_seconds=config['timeout_seconds'],actor='local-workbench')
        policy,binding = compile_policy(self.source,job['snapshot'],job['report'],plan,rules)
        grant = make_grant(plan,allow_local_code=True,allow_install=config['allow_install'],
                           allow_network=config['allow_network'],timeout=config['timeout_seconds'],actor='local-workbench')
        value = {'id':identity,'config_sha256':config['sha256'],'spec':config['spec'],
                 'environment':plan,'rules':rules,'grant':grant,'binding':binding,'policy':asdict(policy)}
        if contract is not None and validation.clarification_depth()>0:
            from .issue_validation import accepted_context
            value['validation_context']=accepted_context(self,identity)
            value['maintenance_request']=validation.source['issue']
        value = json.loads(json.dumps(value))  # Stable JSON arrays on refresh/restart.
        with self.registry.transaction() as db:
            old=db.execute("SELECT body FROM objects WHERE kind='issue_workbench_environment' AND id=?",(identity,)).fetchone()
            require(not old or json.loads(old[0])==value, '旧环境提案已变化，请保留并核对')
            self.registry.put('issue_workbench_environment',identity,value,db)
            self.registry.put('issue_workbench_scope',rules['rules_sha256'],value,db)
        return {**value,'revision':self.challenge(value)}

    def _followup(self, service=None, rules=None):
        from .issue_followup import IssueFollowup
        return IssueFollowup(self.registry, self.source, self.connection,
                             self._config()['spec'], rules, service)

    def _service(self, prepared):
        from .issue_execution import IssueExecution
        from .pi_provider import PiProvider
        s = self.session
        inner = s.provider.inner
        require(isinstance(inner,PiProvider), 'Issue 自动修复需要原 Pi provider')
        require(prepared['config_sha256']==self._config()['sha256'], '原规则版本不符')
        def provider(policy):
            p = PiProvider(policy.allowed_paths,inner.mock_url,policy.readonly_paths,inner.max_requests)
            for key in ('credential_source','config_file','memory_root','memory_index','memory_relations',
                        'memory_relations_context','_memory_relations_guard','_structure_guard','_structure_context',
                        '_memory_scope','_memory_common','_memory_index_sha','_experience_context',
                        '_knowledge_context','_maintenance_context','_memory_catalog'):
                setattr(p,key,getattr(inner,key))
            return p
        watch=self.watch_context
        if watch:require(watch['investigation_id']==prepared['id'],'持续接单的原调查不符')
        def guard():
            self._config()
            s.runtime.require_current()
            original=self.registry.load('issue_diagnosis')[prepared['id']]['snapshot']['issue']
            s._task_sources(prepared.get('maintenance_request',original['title']+'\n'+original['body']))
            if watch:self.watch.guard(watch['grant_id'])
            if prepared.get('maintenance_request') is not None:
                from .issue_validation import accepted_context
                require(accepted_context(self,prepared['id'])==prepared['validation_context'],
                        '原验收需求或当前澄清子版本已变化')
        service = IssueExecution(s.repo,self.registry,self.connection,prepared['spec'],prepared['rules'],provider,
            source_scope={'memory':s.memory.binding,'structure':s.structure.binding},source_guard=guard)
        if prepared.get('maintenance_request') is not None:
            service.maintenance_request=lambda:prepared['maintenance_request']
        if watch:service.model_guard=lambda:self.watch.model_guard(watch['grant_id'])
        correlation=getattr(s,'correlation',None)
        if correlation:
            service.dispatch_fence=lambda row,verify,attempts:correlation.claim_issue(self,prepared['id'],row,verify,attempts)
            service.dispatch_verify=lambda receipt:correlation.verify(self.registry,receipt,
                lambda:correlation._issue(self,prepared['id']))
        return service

    def _approval(self, prepared, plan):
        identity = uuid.uuid4().hex
        row = {'id':identity,'investigation_id':prepared['id'],'status':'pending',
               'plan':asdict(plan),'fingerprint':plan.fingerprint,'created_at':now()}
        active=[service.view(eid) for service in self.services.values() for eid in service.controls]
        if len(active)==1:row['execution_id']=active[0]['id']
        row = json.loads(json.dumps(row))
        event = threading.Event()
        with self.lock:
            self.waiters[identity] = event
            self.registry.put('issue_approval',identity,row)
        try:
            require(self.registry.load('issue_approval')[identity]==row,
                    '审批内容被脱敏规则改变，不能批准与实际方案不同的内容')
            deadline = time.monotonic()+1800
            while not event.wait(.25):
                require(not self.session.closed and time.monotonic()<deadline, '审批等待已结束；旧决定不复用')
                if self.watch_context:self.watch.guard(self.watch_context['grant_id'])
                for service in self.services.values():
                    for execution_id in list(service.controls):
                        if service.view(execution_id)['pause_requested']:
                            from .job_control import OperationStopped
                            raise OperationStopped('Issue approval paused')
            current = self.registry.load('issue_approval')[identity]
            require(current['status'] in ('approved','rejected'), '审批中断，不执行修改')
            return 'approve' if current['status']=='approved' else 'reject'
        finally:
            with self.lock:
                self.waiters.pop(identity,None)
                current = self.registry.load('issue_approval')[identity]
                if current['status']=='pending':
                    self.registry.put('issue_approval',identity,{**current,'status':'interrupted','finished_at':now()})

    def immediate(self, action, payload):
        """Only bounded control receipts bypass the busy worker; never execute here."""
        with self.lock:
            if action in {'issue_decide','issue_request_changes'}:
                row=self.registry.load('issue_approval').get(payload['approval_id'])
                require(row and row['status']=='pending' and payload['revision']==self.challenge(row), '审批已变化或已处理')
                require(row['id'] in self.waiters, '旧审批无活跃执行者，不能重放')
                prepared=self.registry.load('issue_workbench_environment')[row['investigation_id']]
                current=IssueReader(self.connection,prepared['spec'],registry=self.registry).snapshot(
                    self.registry.load('issue_diagnosis')[prepared['id']]['snapshot']['issue']['number'])
                require(current.get('token')==prepared['id'] and current['status']=='ready', 'Issue 已变化，旧审批失效')
                changes={}
                if action=='issue_request_changes':
                    from .issue_review import review_text
                    self.session.runtime.require_current()
                    changes={'review_reason':review_text(payload['reason']),'decision_kind':'request_changes'}
                self.registry.put('issue_approval',row['id'],{**row,
                    'status':'approved' if action=='issue_decide' and payload['decision']=='approve' else 'rejected',
                    'finished_at':now(),**changes})
                self.waiters[row['id']].set()
                return {'status':'changes_requested' if changes else 'decision_recorded','approval_id':row['id']}
            service=next((s for s in self.services.values() if payload['execution_id'] in s.controls),None)
            require(service is not None, '当前进程没有此活跃执行者；先核对原记录')
            return service.request_pause(payload['execution_id'],payload['revision'])

    def stop(self):
        self.watch.stop()
        with self.lock:
            for service in self.services.values():
                for identity in list(service.controls):
                    row=service.view(identity)
                    service.request_pause(identity,row['challenge'])
            for event in self.waiters.values(): event.set()

    def _execution(self, identity):
        row = self.registry.load('issue_execution').get(identity)
        require(row is not None, '未知 Issue 执行')
        prepared = self.registry.load('issue_workbench_environment')[row['investigation_id']]
        service = self._service(prepared)
        return row, prepared, service

    def operate(self, action, payload):
        self.session.runtime.require_current()
        if action=='issue_watch_preview':return self.watch.preview(payload)
        if action=='issue_watch_configure':return self.watch.configure(payload)
        if action=='issue_watch_check':return self.watch.check()
        if action=='issue_watch_process':return self.watch.process(payload)  # Internal queue only, no public request schema.
        if action=='issue_config_preview': return self.config_preview(payload)
        if action=='issue_configure': return self.configure(payload['config'],payload['revision'])
        config = self._config()
        if action=='issue_read':
            return IssueReader(self.connection,config['spec'],registry=self.registry).snapshot(payload['number'])
        if action=='issue_investigate':
            result=IssueIntake(self.connection,self.registry,config['spec']).investigate(payload['number'],self.source)
            if result['status']=='duplicate_no_replay':
                return self.registry.load('issue_diagnosis').get(result['investigation_id'],result)
            return result
        if action=='issue_environment': return self.environment(payload['investigation_id'])
        if action=='issue_successor_preview':
            return self._followup().preview(payload['number'])
        if action=='issue_successor_configure':
            return self._followup().configure(payload)
        if action=='issue_successor_execute':
            row=self._followup().view(payload['successor_id'])
            require(row['status']=='prepared','Issue 后继状态已变化，不能重复启动')
            prepared=self.registry.load('issue_workbench_environment').get(row['investigation_id'])
            require(prepared is not None, '请先为新 Issue 版本准备原环境与检查')
            require(self.environment(prepared['id'])['binding']==prepared['binding'],
                    '新版本环境/检查已变化；保留旧意图')
            service=self._service(prepared)
            followup=self._followup(service,prepared['rules'])
            self.services['starting']=service
            try:
                return followup.start(row['id'],payload['revision'],prepared['environment'],
                    prepared['grant'],approval=lambda plan:self._approval(prepared,plan))
            finally:
                self.services.pop('starting',None)
        if action in {'issue_validation_preview','issue_validation_propose','issue_validation_review','issue_validation_reconcile',
                      'issue_validation_clarification_preview','issue_validation_clarify'}:
            from .issue_validation import service as validation_service
            service=validation_service(self,payload['investigation_id'])
            if action=='issue_validation_preview':return service.preview()
            require(payload['investigation_id'] not in self.registry.load('issue_workbench_environment'),
                    '本次环境已经固定；保留其原检查与批准，不能事后替换案例')
            if action in {'issue_validation_clarification_preview','issue_validation_clarify'}:
                for admission in self.registry.load('issue_watch_admission').values():
                    if admission.get('investigation_id')==payload['investigation_id']:
                        self.watch.guard(admission['grant_id'])
            if action=='issue_validation_clarification_preview':return service.clarification_preview(payload['revision'],payload['answer'])
            if action=='issue_validation_clarify':
                require(payload['confirmed'] is True,'需要明确确认补充后的只读提议和原额度')
                require(isinstance(payload['preview'],dict),'补充预览格式无效')
                root=validation_service(self,payload['investigation_id'],root=True)
                parent=root.ancestor(payload['preview'].get('parent_id'))
                result=parent.clarify(payload['preview'],payload['revision'])
                service=root.current()
            elif action=='issue_validation_reconcile':
                from .validation_reconcile import reconcile
                result=reconcile(service,payload['revision'])
            elif action=='issue_validation_propose':result=service.propose(payload['revision'])
            else:result=service.review(payload['revision'],payload['decision'])
            return {k:v for k,v in result.items() if k!='source'}|{
                'investigation_id':payload['investigation_id'],'revision':service.revision(result)}
        if action=='issue_execute':
            prepared=self.registry.load('issue_workbench_environment').get(payload['investigation_id'])
            require(prepared and payload['revision']==self.challenge(prepared), '环境预览已变化，请重新查看')
            # Rebuild the entire read-only binding before any original model intent.
            require(self.environment(prepared['id'])['binding']==prepared['binding'], '调查范围已变化')
            service=self._service(prepared)
            self.services['starting']=service
            try:
                return service.start(prepared['id'],prepared['environment'],prepared['grant'],
                    approval=lambda plan:self._approval(prepared,plan),
                    max_attempts=self.watch_context['max_attempts'] if self.watch_context else 3)
            finally:
                self.services.pop('starting',None)
        if action.startswith('issue_progress_'):
            from .issue_progress import IssueProgress, IssueProgressSource, IssueInvestigationProgressSource
            from .issue_delivery import IssueDelivery
            from .issue_closure import IssueClosure
            from .issue_acceptance import acceptance_policy
            from .github_acceptance import GitHubAcceptance
            from .github_issue_comments import GitHubIssueComments
            if 'investigation_id' in payload:
                identity = payload['investigation_id']
                source = IssueInvestigationProgressSource(self)
                original = self.registry.load('issue_diagnosis').get(identity)
                require(original, 'Original investigation missing')
                row = {'id':identity,'run_id':identity,'issue_number':original['snapshot']['issue']['number']}
                repository = config['spec']['repository']
            else:
                row,prepared,service = self._execution(payload['execution_id'])
                delivery = IssueDelivery(service, self.remote or GitHubAcceptance())
                closure = IssueClosure(delivery, delivery.remote, acceptance_policy(config['workflow_ids'])) if config['workflow_ids'] else None
                source = IssueProgressSource(service, delivery=delivery, closure=closure)
                repository = service.spec['repository']
            remote = self.comment_remote or GitHubIssueComments()
            def public_comment(value):
                # Raw source snapshots remain in the original journal only.
                return {k:v for k,v in value.items() if k != 'continuity_before'}
            if action.endswith('_preview'):
                facts = source.facts(row['id'], payload['stage'])
                actor = remote.comment_actor()
                comments = IssueProgress(self.registry, remote, source.observer(row['id'], payload['stage']))
                return {**public_comment(comments.prepare(facts['repository'], facts['issue_number'], facts['run_id'], facts['event_id'],
                                           actor_id=actor['id'])), 'github_actor':actor}
            saved = self.registry.load('issue_progress').get(payload['progress_id'])
            require(saved and saved['core']['run_id'] == row['run_id']
                    and saved['core']['repository'] == repository
                    and saved['core']['issue_number'] == row['issue_number'], 'Comment belongs to another original execution')
            comments = IssueProgress(self.registry, remote, source.observer(row['id'], saved['core']['stage']))
            if action.endswith('_send'):
                return public_comment(comments.send(saved['id'], approve_sha256=payload['approval_sha256'],
                                     expected_actor_id=payload['expected_actor_id']))
            return public_comment(comments.reconcile(saved['id']))
        row,prepared,service = self._execution(payload['execution_id'])
        if action=='issue_environment_retry_preview':
            return service.environment_retry_preview(row['id'])
        if action=='issue_review_revision_preview':
            return service.review_revision_preview(row['id'],payload['reason'])
        if action=='issue_revise_review':
            self.services['starting']=service
            try:
                return service.revise_review(row['id'],payload['revision'],payload['reason'],
                    prepared['environment'],prepared['grant'],approval=lambda plan:self._approval(prepared,plan))
            finally:self.services.pop('starting',None)
        if action=='issue_takeover': return service.takeover(row['id'],payload['revision'])
        if action in {'issue_resume','issue_retry_environment'}:
            self.services['starting']=service
            try:
                method=service.retry_environment if action=='issue_retry_environment' else service.resume
                return method(row['id'],payload['revision'],prepared['environment'],prepared['grant'],
                    approval=lambda plan:self._approval(prepared,plan))
            finally: self.services.pop('starting',None)
        directory = service.runs/row['run_id']
        if action=='issue_result':
            from .dashboard import read_json, project_run
            record,_=read_json(directory/'run.json')
            require(record.get('run_id')==row['run_id'] and record.get('source_repo')==str(self.source.root)
                    and record.get('issue_execution',{}).get('execution_id')==row['id']
                    and record.get('issue_execution',{}).get('policy_binding_sha256')==row['policy_binding']['binding_sha256'],
                    '原 Issue/run 关联不符')
            return project_run(directory)
        from .integration import prepare_integration, integrate
        if action=='issue_prepare_local': return prepare_integration(directory)
        if action=='issue_publish_local': return integrate(directory,payload['revision'])
        if action in {'issue_recover','issue_recover_preview'}:
            from .repair_recovery import inspect_repair, recover_repair
            inspected=inspect_repair(directory)
            if action=='issue_recover_preview': return inspected
            require(inspected['revision']==payload['revision'], '恢复证据已变化')
            return recover_repair(directory,payload['revision'])
        from .issue_delivery import IssueDelivery
        from .github_acceptance import GitHubAcceptance
        from .issue_publication import IssuePublication
        from .issue_closure import IssueClosure
        from .issue_acceptance import acceptance_policy
        remote=self.remote or GitHubAcceptance()
        delivery=IssueDelivery(service,remote)
        publisher=IssuePublication(delivery,self.transport)
        if action=='issue_push_preview': return publisher.prepare(row['id'])
        if action=='issue_pr_preview': return delivery.prepare(row['id'])
        if action=='issue_close_preview':
            return IssueClosure(delivery,remote,acceptance_policy(config['workflow_ids'])).prepare(row['id'])
        if action=='issue_push':
            return publisher.push(row['id'],payload['revision'],approve_sha256=payload['approval_sha256'],actor='local-workbench')
        if action=='issue_pr':
            return delivery.create(row['id'],payload['revision'],approve_sha256=payload['approval_sha256'],actor='local-workbench')
        if action=='issue_close':
            return IssueClosure(delivery,remote,acceptance_policy(config['workflow_ids'])).close(
                row['id'],payload['revision'],approve_sha256=payload['approval_sha256'],actor='local-workbench')
        if action=='issue_reconcile':
            handler=publisher if payload['kind']=='push' else delivery if payload['kind']=='pr' else IssueClosure(
                delivery,remote,acceptance_policy(config['workflow_ids']))
            return handler.reconcile(row['id'])
        raise SafetyError('未知 Issue 操作')
