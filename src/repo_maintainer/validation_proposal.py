"""Readonly validation proposal and review, in the original task/request journal."""
import copy
from contextlib import nullcontext
import json
from pathlib import Path

from .behavior_validation import make_contract, is_behavior_module
from .git import RepositoryGateway
from .models import digest, SafetyError
from .source_discovery import CommittedTree
from .store import now, redact
from .task_planning import TaskPlanner
from .task_session import fingerprint,repository_identity,strict_json,require,_validation_proposal,VALIDATION_DATA_SCHEMA,_context
from .ui_registry import DurableProvider,provider_binding

PURPOSE='validation-proposal-v1'


def source_schema(source):
    return (VALIDATION_DATA_SCHEMA if any(is_behavior_module(f['path']) and not f['path'].endswith('.py')
            for f in source['files']) else 'python-behavior-v1')


def source_packet(repository,issue,read_roots,paths,authority):
    require(isinstance(issue,str) and issue.strip() and len(issue.encode())<=65536,
            'Validation needs an explicit bounded maintenance request')
    require(isinstance(paths,(list,tuple)) and 1<=len(paths)<=20 and len(set(paths))==len(paths),
            'Validation needs bounded controller-selected readonly paths')
    tree=CommittedTree(repository,repository.head,read_roots)
    files=[]
    for path in paths:
        raw=tree.read(path)
        files.append({'path':path,'content':raw.decode('utf-8'),'before_sha256':digest(raw),'editable':False})
    value={'schema_version':1,'purpose':PURPOSE,'repository_id':repository_identity(repository.root,repository.common_dir),
           'base_sha':repository.head,'request_sha256':digest(issue.encode()),'issue':issue,
           'read_roots':list(read_roots),'files':files,'authority':copy.deepcopy(authority),
           'authority_sha256':fingerprint(authority),'runtime_authorized':False}
    value['source_sha256']=fingerprint(value)
    return value


def validate_source(source,repository,issue):
    require(isinstance(source,dict) and set(source)=={'schema_version','purpose','repository_id','base_sha',
        'request_sha256','issue','read_roots','files','authority','authority_sha256','runtime_authorized','source_sha256'}
        and source['schema_version']==1 and source['purpose']==PURPOSE and source['runtime_authorized'] is False,
        'Invalid readonly validation source')
    require(source['authority_sha256']==fingerprint(source['authority'])
        and source['source_sha256']==fingerprint({k:v for k,v in source.items() if k!='source_sha256'})
        and source['repository_id']==repository_identity(repository.root,repository.common_dir)
        and source['base_sha']==repository.head and source['issue']==issue
        and source['request_sha256']==digest(issue.encode()),'Validation source or request changed')
    fresh=source_packet(repository,issue,source['read_roots'],[f['path'] for f in source['files']],
                        {})
    require(fresh['files']==source['files'],'Validation original source bytes changed')
    return source


def proposal_contract(source,proposal):
    # Native does the same independent schema/path validation. Never trust
    # source IDs, grants, commands or SHA values supplied inside a model tool.
    _validation_proposal(proposal,[f['path'] for f in source['files']],source_schema(source))
    cases=strict_json(proposal['cases_json']) if 'cases_json' in proposal else proposal['cases']
    if proposal['clarification']:
        return None
    return make_contract(repository_id=source['repository_id'],base_sha=source['base_sha'],
        request_sha256=source['request_sha256'],requirement=proposal['requirement'],cases=cases)


def clarification_leaf(registry,identity):
    """Readonly journal projection/restart traversal; never infer an execution grant."""
    links=registry.load('validation_clarification');rows=registry.load('validation_proposal')
    seen=set();history=[]
    for _ in range(len(links)+1):
        require(identity not in seen,'Clarification chain cycle');seen.add(identity)
        link=links.get(identity)
        if link is None:return identity,history
        parent=rows.get(identity)
        require(parent and parent['status']=='clarification_needed' and link['parent_id']==identity
                and fingerprint(parent)==link['parent_sha256'],'原澄清父版本缺失或变化')
        child=link['child_id'];require(isinstance(child,str) and child not in seen,'Clarification chain cycle')
        expected=copy.deepcopy(parent['source'])
        expected['issue']+='\n\n用户补充业务期望：\n'+link['answer']
        expected['request_sha256']=digest(expected['issue'].encode());expected.pop('source_sha256')
        expected['source_sha256']=fingerprint(expected)
        require(link['source']==expected,'原澄清子来源或权限已变化')
        binding=expected['authority'].get('provider')
        if binding is not None:
            require(child==fingerprint({'source':expected,'provider_identity':binding}),'原澄清子身份已变化')
        row=rows.get(child)
        require(row is None or row['id']==child and row['source']==expected,'原澄清子记录不符')
        history.append(identity);identity=child
    raise SafetyError('Clarification chain cycle')


class ValidationPlanner(TaskPlanner):
    """Same original lease/checkpoint machinery, with an explicitly readonly scope."""
    def __init__(self,registry,repository,provider,source,guard):
        # None is intentional: this phase has NO executable Policy or fake tests.
        super().__init__(registry,repository.root,None,provider,{'validation_source':source['source_sha256']},
                         sources_guard=lambda _:guard())
        self.read_source=copy.deepcopy(source)

    def planning_checks(self):
        return ()

    def current_scope(self,issue):
        repo=RepositoryGateway(self.source)
        validate_source(self.read_source,repo,issue)
        context,binding=self.provider.task_identity()
        require(context['paths']==[f['path'] for f in self.read_source['files']]
            and not self.provider.allowed_paths,'Validation task cannot acquire writable paths')
        context={**context,'schema_version':2,'purpose':PURPOSE}
        if source_schema(self.read_source) == VALIDATION_DATA_SCHEMA:
            context.update(schema_version=3,validation_schema=VALIDATION_DATA_SCHEMA)
        # Existing proposal/task identity owns its frozen Native protocol. Do
        # not re-infer a new schema for an old committed or unknown task merely
        # because its original readonly paths also contained a Node file.
        identity=fingerprint({'source':self.read_source,'provider_identity':binding})
        previous=self.store.registry.load('validation_proposal').get(identity)
        if previous and previous.get('task_id'):
            task=self.store.registry.load('pi_task').get(previous['task_id'])
            require(previous['source']==self.read_source and task is not None,
                    'Validation original Native reference is unavailable')
            original=_context(task['context'])
            require(original.get('purpose')==PURPOSE and all(original[k]==context[k] for k in ('provider','model','paths')),
                    'Validation original Native source/model changed')
            require(original.get('thinking_level', 'off') == context.get('thinking_level', 'off'),
                    'Validation original Native thinking level changed')
            context=original
        return dict(repository_sha256=self.store.repository,base_sha=repo.head,
            issue_sha256=fingerprint(issue),policy_sha256=fingerprint({'purpose':PURPOSE,'readonly':context['paths']}),
            provider_sha256=fingerprint(binding),source_sha256=fingerprint(self.sources),
            tests_sha256=fingerprint({'purpose':PURPOSE,'execution_checks':False}),context_sha256=fingerprint(context)),context


class ValidationProposal:
    def __init__(self,registry,repository,provider,source,guard,session_token,*,operation_id=None,
                 completion_guard=nullcontext):
        require(callable(guard),'Validation requires the original maintainer guard')
        self.registry,self.repository,self.provider=registry,repository,provider
        self.source=copy.deepcopy(source);self.guard=guard;self.session_token=session_token
        self.operation_id=operation_id
        self.completion_guard=completion_guard
        self.identity=fingerprint({'source':source,'provider_identity':provider.task_identity()[1]})

    def _fresh(self):
        self.guard()
        require(self.identity==fingerprint({'source':self.source,'provider_identity':self.provider.task_identity()[1]}),
                '原模型身份已变化；保留原提议，不开始或接受新调用')
        validate_source(self.source,self.repository,self.source['issue'])

    def revision(self,value):
        return fingerprint({'value':value,'session':self.session_token})

    def _model_admission(self):
        self._fresh()
        budget=self.registry.budget()
        require(not budget['requests_reserved_unknown'] and budget['requests_remaining']>0,
                '原消费未知或额度耗尽；不开始新的案例提议')

    def preview(self):
        self._fresh()
        existing=self.registry.load('validation_proposal').get(self.identity)
        display=({k:v for k,v in existing.items() if k!='source'}|{'revision':self.revision(existing)}) if existing else None
        value={'id':self.identity,'source_sha256':self.source['source_sha256'],
               'base_sha':self.source['base_sha'],'request_sha256':self.source['request_sha256'],
               'readonly_paths':[f['path'] for f in self.source['files']],
               'runtime_authorized':False,'uses_original_model_budget':True,
               'existing':display,'budget':self.registry.budget()}
        return {**value,'revision':self.revision(value)}

    def propose(self,revision):
        self._fresh()
        previous=self.registry.load('validation_proposal').get(self.identity)
        if previous:
            require(previous['preview_revision']==revision,'Validation intent already exists; inspect its original record')
            return previous
        require(not any(v['child_id']==self.identity for v in self.registry.load('validation_clarification').values())
                or getattr(self,'_clarification_dispatch',False),
                '原补充意图已登记但未完整提交；不重新调用模型')
        self._model_admission()
        preview=self.preview()
        require(revision==preview['revision'],'Validation preview changed; inspect its current original record')
        with self.registry.transaction() as db:
            old=db.execute("SELECT body FROM objects WHERE kind='validation_proposal' AND id=?",(self.identity,)).fetchone()
            if old:return json.loads(old[0])  # Never replay a started/unknown/finished proposal.
            row={'id':self.identity,'status':'started','source':self.source,'task_id':None,'operation_id':self.operation_id,
                 'preview_revision':revision,
                 'started_at':now(),'runtime_authorized':False}
            self.registry.put('validation_proposal',self.identity,row,db)
        provider=self.provider
        durable=DurableProvider(provider,self.registry);durable.before_model=self._model_admission
        planner=ValidationPlanner(self.registry,self.repository,provider,self.source,self._fresh)
        durable.task_planner=planner
        previous=provider._validation_source
        try:
            require(previous is None,'Validation provider already belongs to a phase')
            provider._validation_source=copy.deepcopy(self.source)
            task=planner.begin(self.source['issue'])
            row.update(task_id=task);self.registry.put('validation_proposal',self.identity,row)
            result=durable.propose_validation(self.repository,self.source['issue'],())
            # A stop receipt and successful publication must have one ordering.
            # The monitor supplies its original operation lock; other callers
            # retain their existing serialized queue behavior.
            with self.completion_guard():
                self._fresh()
                row.update(result,status=result['status'],finished_at=now(),evidence=copy.deepcopy(durable.evidence))
                self.registry.put('validation_proposal',self.identity,row)
        except BaseException:
            with self.completion_guard():
                row.update(status='interrupted_no_replay',finished_at=now(),evidence=copy.deepcopy(durable.evidence))
                self.registry.put('validation_proposal',self.identity,row)
            raise
        finally:
            provider._validation_source=previous
        return row

    def review(self,revision,decision):
        self._fresh()
        require(decision in {'accept','reject'},'Invalid validation review decision')
        with self.registry.transaction() as db:
            raw=db.execute("SELECT body FROM objects WHERE kind='validation_proposal' AND id=?",(self.identity,)).fetchone()
            require(raw is not None,'No original validation proposal')
            row=json.loads(raw[0])
            require(row['source']==self.source and row['status']=='awaiting_validation_review'
                and revision==self.revision(row),'Validation proposal or review changed')
            contract=proposal_contract(self.source,row['proposal'])
            require(contract==row['contract'],'Validation proposal contract changed')
            row.update(status='accepted' if decision=='accept' else 'rejected',reviewed_at=now(),
                       review={'decision':decision,'proposal_sha256':row['proposal_sha256'],
                               'contract_sha256':contract['contract_sha256'],'actor':'local-browser-operator'})
            self.registry.put('validation_proposal',self.identity,row,db)
        return row

    def current(self):
        """Follow immutable clarification links without changing old proposals/Native."""
        service=self;seen=set()
        for _ in range(len(self.registry.load('validation_clarification'))+1):
            require(service.identity not in seen,'Clarification chain cycle')
            seen.add(service.identity)
            link=self.registry.load('validation_clarification').get(service.identity)
            if link is None:return service
            parent=self.registry.load('validation_proposal').get(service.identity)
            require(parent and parent['status']=='clarification_needed'
                    and fingerprint(parent)==link['parent_sha256']
                    and parent['source']==service.source,'原澄清父记录已变化，保留原意图')
            expected=service._clarification_source(link['answer'])
            require(expected==link['source'],'澄清来源或原权限已变化')
            previous=service
            def guard(previous=previous,link=copy.deepcopy(link)):
                previous._fresh()
                require(self.registry.load('validation_clarification').get(previous.identity)==link
                        and fingerprint(self.registry.load('validation_proposal').get(previous.identity))==link['parent_sha256'],
                        '原澄清来源或父版本已变化')
            service=ValidationProposal(self.registry,self.repository,self.provider,expected,guard,self.session_token,
                operation_id=self.operation_id,completion_guard=self.completion_guard)
            require(service.identity==link['child_id'],'澄清子意图身份不符')
        raise SafetyError('Clarification chain limit reached')

    def ancestor(self,identity):
        self.current()  # Verify the complete immutable chain first.
        service=self
        for _ in range(len(self.registry.load('validation_clarification'))+1):
            if service.identity==identity:return service
            link=self.registry.load('validation_clarification').get(service.identity)
            require(link is not None,'原澄清父版本不可达')
            previous=service
            def guard(previous=previous,link=copy.deepcopy(link)):
                previous._fresh()
                require(self.registry.load('validation_clarification').get(previous.identity)==link
                        and fingerprint(self.registry.load('validation_proposal').get(previous.identity))==link['parent_sha256'],
                        '原澄清父链已变化')
            service=ValidationProposal(self.registry,self.repository,self.provider,link['source'],
                guard,self.session_token,operation_id=self.operation_id,completion_guard=self.completion_guard)
        raise SafetyError('Clarification chain limit reached')

    def _clarification_source(self,answer):
        require(isinstance(answer,str) and answer==answer.strip() and 1<=len(answer)<=2000
                and redact(answer)==answer and not any(ord(c)<32 and c not in '\n\t' for c in answer),
                '请提供1至2000字补充业务期望，不含凭据或控制字符')
        issue=self.source['issue']+'\n\n用户补充业务期望：\n'+answer
        return source_packet(self.repository,issue,self.source['read_roots'],
            [f['path'] for f in self.source['files']],self.source['authority'])

    def clarification_depth(self):
        links=self.registry.load('validation_clarification');identity=self.identity;seen=set()
        for depth in range(len(links)+1):
            require(identity not in seen,'Clarification chain cycle');seen.add(identity)
            parents=[p for p,v in links.items() if v['child_id']==identity]
            if not parents:return depth
            require(len(parents)==1,'Clarification child has multiple parents')
            identity=parents[0]
        raise SafetyError('Clarification chain limit reached')

    def clarification_preview(self,revision,answer):
        self._fresh();self._model_admission()
        row=self.registry.load('validation_proposal').get(self.identity)
        require(row and row['status']=='clarification_needed' and revision==self.revision(row)
                and row['source']==self.source,'需要当前已结算的待澄清版本')
        require(self.registry.load('validation_clarification').get(self.identity) is None,
                '原补充意图已登记，请查看当前子版本')
        source=self._clarification_source(answer)
        child=ValidationProposal(self.registry,self.repository,self.provider,source,self._fresh,self.session_token)
        value={'parent_id':self.identity,'parent_sha256':fingerprint(row),'answer':answer,
               'source_sha256':source['source_sha256'],'child_id':child.identity,'phase':child.preview()}
        return {'preview':value,'revision':self.revision(value)}

    def clarify(self,value,revision):
        self._fresh()
        require(isinstance(value,dict) and revision==self.revision(value),'补充预览已失效')
        old=self.registry.load('validation_clarification').get(self.identity)
        if old:
            require(old['preview']==value,'原补充意图已固定；不能替换或重发')
            self.current()
            return self.registry.load('validation_proposal').get(old['child_id']) or {
                'id':old['child_id'],'status':'interrupted_no_replay','runtime_authorized':False}
        row=self.registry.load('validation_proposal').get(self.identity)
        require(row is not None,'原待澄清记录缺失')
        require(self.clarification_preview(self.revision(row),value.get('answer'))=={'preview':value,'revision':revision},
                '原待澄清版本、来源或额度已变化')
        source=self._clarification_source(value['answer'])
        link={'parent_id':self.identity,'parent_sha256':fingerprint(row),'child_id':value['child_id'],
              'answer':value['answer'],'source':source,'preview':value,'created_at':now()}
        with self.registry.transaction() as db:
            raw=db.execute("SELECT body FROM objects WHERE kind='validation_proposal' AND id=?",(self.identity,)).fetchone()
            require(raw and strict_json(raw[0])==row,'原父版本已变化')
            existing=db.execute("SELECT body FROM objects WHERE kind='validation_clarification' AND id=?",(self.identity,)).fetchone()
            require(existing is None,'原补充意图已登记，不重发')
            self.registry.put('validation_clarification',self.identity,link,db)
        child=self.current()
        child._clarification_dispatch=True
        return child.propose(value['phase']['revision'])

    def accepted(self):
        self._fresh()
        row=self.registry.load('validation_proposal').get(self.identity)
        if row is None or row['status']!='accepted':return None
        require(row['source']==self.source and row['review']['contract_sha256']==row['contract']['contract_sha256']
            and row['contract']==proposal_contract(self.source,row['proposal']), 'Accepted validation contract changed')
        return copy.deepcopy(row['contract'])
