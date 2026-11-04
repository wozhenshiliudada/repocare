"""Issue-owned readonly validation phase; no Issue writes or execution grants."""
from .check_discovery import is_test
from .behavior_validation import is_behavior_module
from .issue_intake import IssueReader
from .pi_provider import PiProvider
from .validation_proposal import ValidationProposal,source_packet
from .task_session import require


def service(workbench,identity,*,root=False):
    w,s=workbench,workbench.session
    config=w._config();job=w.registry.load('issue_diagnosis').get(identity)
    require(job and job['status']=='investigated' and job['snapshot']['status']=='ready',
            '先取得当前可接手的Issue调查结果')
    require(w.registry is s.budget_registry and not s.monitor_owned,
            'Issue验证提议必须使用本任务原账和控制器，不能借监控会话扩权')
    inner=s.provider.inner
    require(isinstance(inner,PiProvider),'验收案例提议需要原Pi模型配置')
    snapshot=job['snapshot'];issue=snapshot['issue']['title']+'\n'+snapshot['issue']['body']
    paths=list(dict.fromkeys(row['path'] for key in ('file_candidates','test_candidates')
        for row in job['report'].get(key,[]) if isinstance(row,dict) and isinstance(row.get('path'),str)))[:20]
    require(any(is_behavior_module(p) and not is_test(p) for p in paths),
            '尚未找到授权范围内可验收的Python/Node实现文件，请先补充调查；不会伪造测试或扩大读取权限')
    def guard():
        require(w._config()==config,'原Issue维护规则已变化')
        require(not s.closed,'原Issue工作区已关闭')
        s.runtime.require_current()
        if s.control:s.control.check()
        fresh=IssueReader(w.connection,config['spec'],registry=w.registry).snapshot(snapshot['issue']['number'])
        require(fresh.get('token')==identity and fresh['status']=='ready' and w.source.head==snapshot['head_sha'],
                'Issue或目标版本已变化，请保留原提议并重新调查')
        s._task_sources(issue)
        # This guard is also called while the ORIGINAL attempt holds its own
        # reservation. Admission checks remaining/unknown before reserve; source
        # revalidation must not misclassify that active reservation as unknown.
        if w.watch_context:w.watch.guard(w.watch_context['grant_id'])
        if w.registry.load('validation_clarification'):
            for admission in w.registry.load('issue_watch_admission').values():
                if admission.get('investigation_id')==identity:
                    w.watch.guard(admission['grant_id'])
    provider=PiProvider((),inner.mock_url,tuple(paths),inner.max_requests)
    for key in ('credential_source','config_file','_cancel_event','_progress_callback'):
        setattr(provider,key,getattr(inner,key))
    source=source_packet(w.source,issue,config['spec']['read_roots'],paths,
        {'issue_token':identity,'config_sha256':config['sha256'],'provider':provider.task_identity()[1]})
    proposal=ValidationProposal(w.registry,w.source,provider,source,guard,s.token,operation_id=s.active)
    return proposal if root else proposal.current()


def projection(workbench):
    rows=[]
    links=workbench.registry.load('validation_clarification')
    proposals=workbench.registry.load('validation_proposal')
    superseded=set(links)
    for row in workbench.registry.load('validation_proposal').values():
        if row['id'] in superseded:continue
        origin=row['source'].get('authority',{})
        if origin.get('issue_token') and row['source']['purpose']=='validation-proposal-v1':
            rows.append({k:v for k,v in row.items() if k!='source'}|{
                'source_sha256':row['source']['source_sha256'],'base_sha':row['source']['base_sha'],
                'request_sha256':row['source']['request_sha256'],
                'investigation_id':origin['issue_token'],
                'revision':workbench.challenge(row)})
    for parent,link in links.items():
        if link['child_id'] in proposals or link['child_id'] in superseded:continue
        origin=link['source']['authority']
        if origin.get('issue_token'):
            rows.append({'id':link['child_id'],'status':'interrupted_no_replay',
                'investigation_id':origin['issue_token'],'runtime_authorized':False,
                'clarification_history':[parent],'missing_result_no_replay':True})
    return rows


def accepted_context(workbench,identity):
    current=service(workbench,identity)
    contract=current.accepted();require(contract is not None,'原案例子版本尚未接受')
    row=current.registry.load('validation_proposal')[current.identity]
    from .task_session import fingerprint
    return {'proposal_id':current.identity,'proposal_sha256':fingerprint(row),
            'source_sha256':current.source['source_sha256'],'request':current.source['issue'],
            'contract_sha256':contract['contract_sha256']}
