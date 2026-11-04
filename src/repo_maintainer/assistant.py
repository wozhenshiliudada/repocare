"""Repository-free Pi conversations with read-only help and draft-only form tools."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import uuid

from .assistant_forms import form_snapshot, form_proposal
from .dashboard import unlinked
from .models import SafetyError, digest
from .pi_distribution import runtime_script
from .provider_config import resolve_model_limits
from .runtime_limits import PROCESS_TIMEOUT_SECONDS
from .store import now
from .ui_registry import UIRegistry
from .pi_evidence import CountedPlanningFailure
from .assistant_receipts import HINTS, verify_terminal_native, has_native_checkpoint
from .assistant_secrets import contains_credential, redact_credentials
from .assistant_limits import (MAX_ASSISTANT_TEXT_BYTES, MAX_ASSISTANT_REPLY_BYTES, MAX_ASSISTANT_NATIVE_BYTES,
                               MAX_ASSISTANT_CATALOG_BYTES, MAX_ASSISTANT_HISTORY_BYTES,
                               MAX_ASSISTANT_REQUESTS_PER_TURN)

_ID = re.compile(r'[a-f0-9]{32}\Z')
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_NATIVE = re.compile(r'[a-zA-Z0-9_.+-]{1,180}\.jsonl\Z')


class AssistantRequestTimeout(SafetyError):
    """The Pi subprocess reported a timeout with uncertain consumption."""


def catalog_integrity(root):
    root=unlinked(root)
    marker=unlinked(root.parent/'assistant-binding.json')
    database=unlinked(root/'catalog/ui-state/registry.sqlite3')
    if marker.exists():
        if not marker.is_file() or marker.stat().st_size>4096:
            raise SafetyError('会话目录归属记录异常')
        saved=json.loads(marker.read_text(encoding='utf-8'))
        if (not isinstance(saved,dict) or saved!={'schema_version':1,'root':str(root)}
                or type(saved.get('schema_version')) is not int or not database.is_file()):
            raise SafetyError('会话目录归属或原catalog缺失；保留记录，不重建额度')
    elif root.exists() and any(root.iterdir()):
        raise SafetyError('会话目录缺少原归属；不接管或重建已有记录')
    return marker


def _identifier(value):
    if not isinstance(value,str) or not _ID.fullmatch(value):
        raise SafetyError('会话标识无效')
    return value


def _record(identity,record):
    expected={'id','profile_id','model','profile_sha256','title','messages','created_at','native_file','native_sha256'}
    if (not isinstance(record,dict) or set(record)!=expected or record['id']!=identity
            or not isinstance(record['profile_id'],str)
            or (record['profile_id']!='legacy' and not _ID.fullmatch(record['profile_id']))
            or not isinstance(record['profile_sha256'],str) or not _SHA.fullmatch(record['profile_sha256'])
            or not isinstance(record['model'],str) or not 1<=len(record['model'])<=256
            or not isinstance(record['title'],str) or len(record['title'])>100
            or not isinstance(record['created_at'],str) or len(record['created_at'])>64
            or not isinstance(record['messages'],list) or len(record['messages'])>400):
        raise SafetyError('会话归属或历史损坏，请保留原账')
    _identifier(identity)
    if record['native_file'] is None:
        if record['native_sha256'] is not None or record['messages']:
            raise SafetyError('会话缺少原Native检查点')
    elif (not isinstance(record['native_file'],str) or not _NATIVE.fullmatch(record['native_file'])
            or not isinstance(record['native_sha256'],str) or not _SHA.fullmatch(record['native_sha256'])):
        raise SafetyError('会话Native归属异常')
    seen=set()
    for message in record['messages']:
        if (not isinstance(message,dict) or set(message) not in ({'id','role','content'},{'id','role','content','proposal'})
                or not isinstance(message['role'],str) or message['role'] not in {'user','assistant'}
                or not isinstance(message['content'],str) or len(message['content'].encode())>MAX_ASSISTANT_TEXT_BYTES):
            raise SafetyError('会话消息损坏')
        _identifier(message['id'])
        if message['id'] in seen:raise SafetyError('会话消息标识重复')
        seen.add(message['id'])
        proposal=message.get('proposal')
        if proposal is not None:
            if (message['role']!='assistant' or not isinstance(proposal,dict)
                    or set(proposal)!={'form_id','revision','changes','explanation','draft_only'}
                    or proposal['draft_only'] is not True or not isinstance(proposal['changes'],list)
                    or not isinstance(proposal['revision'],str) or not _SHA.fullmatch(proposal['revision'])
                    or not isinstance(proposal['explanation'],str) or len(proposal['explanation'])>4000):
                raise SafetyError('原草稿建议损坏，不能作为授权')
            _identifier(proposal['form_id'])
            if not 1<=len(proposal['changes'])<=64:raise SafetyError('草稿字段数量异常')
            changed=set()
            for change in proposal['changes']:
                if not isinstance(change,dict) or set(change)!={'field_id','value'}:raise SafetyError('草稿字段损坏')
                _identifier(change['field_id'])
                if change['field_id'] in changed or type(change['value']) not in {str,int,float,bool}:
                    raise SafetyError('草稿字段值损坏')
                changed.add(change['field_id'])
    return record


def _read_objects(root, kind, *, limit=100):
    path = unlinked(root/'ui-state/registry.sqlite3')
    if not path.exists():
        return {}
    if not path.is_file() or path.stat().st_size > MAX_ASSISTANT_CATALOG_BYTES:
        raise SafetyError('会话历史不可读取；请保留记录')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        return {identity:json.loads(body) for identity,body in db.execute(
            'SELECT id,body FROM objects WHERE kind=? ORDER BY rowid DESC LIMIT ?',(kind,limit))}


def _budget(root):
    path = unlinked(root/'ui-state/registry.sqlite3')
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        row = db.execute('SELECT ceiling,used,held FROM budget WHERE id=1').fetchone()
    if not row or any(type(n) is not int or n<0 for n in row) or row[1]+row[2]>row[0]:
        raise SafetyError('会话请求账异常；保留原记录')
    return dict(zip(('request_budget','requests_used','requests_reserved_unknown','requests_remaining'),(*row,row[0]-row[1]-row[2])))


class AssistantControl:
    def __init__(self,hub,*,mock_url=None):
        self.hub,self.root,self.mock_url=hub,hub.root/'assistant',mock_url
        self.lock=threading.RLock()
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='repocare-conversation')
        self.head=None
        self.ledgers={}
        self.active=None
        self.closed=False
        self.cancelling=threading.Event()
        self.process=None

    def _head(self):
        if self.head is not None:
            catalog_integrity(self.root)
        if self.head is None:
            marker=catalog_integrity(self.root)
            if not marker.exists():
                with marker.open('x',encoding='utf-8') as stream:
                    os.chmod(marker,0o600)
                    json.dump({'schema_version':1,'root':str(self.root)},stream)
                    stream.flush();os.fsync(stream.fileno())
            self.head=UIRegistry(unlinked(self.root/'catalog'),{'schema_version':1,'purpose':'conversation-catalog-v1',
                'root':str(self.root),'provider':{'request_budget':0}})
        return self.head

    def _ledger(self,profile):
        with self.lock:
            return self._locked_ledger(profile)

    def _locked_ledger(self,profile):
        identity=profile['id']
        if identity in self.ledgers and not unlinked(self.ledgers[identity].path).is_file():
            raise SafetyError('原会话模型账缺失，不重建额度')
        if identity not in self.ledgers:
            head=self._head()
            directory=unlinked(self.root/'models'/identity)
            existing=_budget(directory)
            original=head.load('assistant_model').get(identity)
            binding={'profile_id':identity,'profile_sha256':profile['config_sha256'],'directory':str(directory)}
            if original is not None:
                if original!=binding or existing is None:
                    raise SafetyError('原会话模型账缺失或归属变化，不重建额度')
            else:
                if existing is not None or (directory.exists() and any(directory.iterdir())):
                    raise SafetyError('孤立会话模型账不能接管或重建')
                head.put('assistant_model',identity,binding)
            ceiling=existing['request_budget'] if existing else self.hub.request_budget
            self.ledgers[identity]=UIRegistry(directory,{'schema_version':1,'purpose':'conversation-model-v1',
                'profile_id':identity,'profile_sha256':profile['config_sha256'],
                'provider':{'type':'PiAssistant','config_file':str(profile['path']),
                            'mock_endpoint':self.mock_url,'request_budget':ceiling}})
        return self.ledgers[identity]

    def view(self):
        with self.lock:
            return self._view()

    def _view(self):
        # No directory creation, journal migration, provider calls or writes.
        catalog_integrity(self.root)
        sessions=_read_objects(self.root/'catalog','assistant_session')
        jobs=_read_objects(self.root/'catalog','job')
        budgets={}
        public=[]
        for identity,value in sessions.items():
            record=_record(identity,value)
            profile=record['profile_id']
            if profile not in budgets:
                budgets[profile]=_budget(unlinked(self.root/'models'/profile))
            row={key:record[key] for key in ('id','title','profile_id','model','messages','created_at')}
            history=self._history(record)
            if history:
                row.update(continuation={key:history[key] for key in ('source_session_id','source_profile_id','source_message_count')},
                           history_messages=history['messages'])
            public.append(redact_credentials(row,self._credentials(record,strict=False)))
        safe_jobs=[]
        for job in jobs.values():
            row={k:v for k,v in job.items() if k!='signature'}
            if row['status']=='running' and self.active!=row['id']:
                row={**row,'status':'interrupted','error':'会话操作曾中断；保留原账，不自动重发'}
            safe_jobs.append(row)
        return {'sessions':public,'jobs':safe_jobs,'budgets':budgets,'active':self.active,
                'request_budget':self.hub.request_budget,'tools':['get_help','propose_form_values'],
                'draft_only':True,'repository_required':False}

    def _history(self,record):
        history=_read_objects(self.root/'catalog','assistant_history',limit=1000).get(record['id'])
        if history is None:return None
        fields={'schema_version','target_session_id','target_profile_sha256','source_session_id','source_profile_id',
                'source_profile_sha256','source_message_count','source_own_message_count','source_messages_sha256',
                'source_native_sha256','messages','context_sha256'}
        if not isinstance(history,dict) or set(history)!=fields:
            raise SafetyError('续接历史损坏；保留原会话与账')
        body={key:value for key,value in history.items() if key!='context_sha256'}
        if (history['schema_version']!=1 or history['target_session_id']!=record['id']
                or history['target_profile_sha256']!=record['profile_sha256']
                or digest(json.dumps(body,sort_keys=True,ensure_ascii=False).encode())!=history['context_sha256']
                or not isinstance(history['messages'],list) or len(history['messages'])>400
                or type(history['source_own_message_count']) is not int or not 0<=history['source_own_message_count']<=400
                or history['source_message_count']!=len(history['messages'])
                or any(not isinstance(m,dict) or set(m)!={'role','content'} or m['role'] not in {'user','assistant'}
                       or not isinstance(m['content'],str) for m in history['messages'])
                or len(json.dumps(history['messages'],ensure_ascii=False).encode())>MAX_ASSISTANT_HISTORY_BYTES):
            raise SafetyError('续接历史归属不匹配；保留原会话与账')
        source=_read_objects(self.root/'catalog','assistant_session',limit=1000).get(history['source_session_id'])
        source=_record(history['source_session_id'],source)
        own=[{key:m[key] for key in ('role','content')} for m in source['messages'][:history['source_own_message_count']]]
        if (source['id']==record['id'] or source['profile_id']!=history['source_profile_id']
                or source['profile_sha256']!=history['source_profile_sha256']
                or len(own)!=history['source_own_message_count']
                or digest(json.dumps(own,sort_keys=True,ensure_ascii=False).encode())!=history['source_messages_sha256']):
            raise SafetyError('原续接消息变化；保留原会话与账')
        return history

    def _credentials(self,record,*,strict=True):
        # A linked snapshot may contain text from multiple source profiles. Do
        # not send any ancestor's credential to the next selected provider.
        keys,seen=[],set()
        while True:
            try:
                if record['id'] in seen:raise SafetyError('续接历史存在循环；不发送')
                seen.add(record['id'])
                profile=self.hub.model_profiles.resolve(record['profile_id'])
                if profile['config_sha256']!=record['profile_sha256']:
                    raise SafetyError('会话模型配置已变化；保留原历史和账')
                config,_=self.hub.model_profiles._provider(profile['path'],record['profile_sha256'])
                keys.append(config['PI_MAINTENANCE_API_KEY'])
                history=self._history(record)
                if history is None:break
                source=_read_objects(self.root/'catalog','assistant_session',limit=1000).get(history['source_session_id'])
                record=_record(history['source_session_id'],source)
            except (SafetyError,OSError,ValueError,TypeError):
                if strict:raise
                break
        return tuple(dict.fromkeys(keys))

    def submit(self,action,payload):
        fields={'create':{'request_id','profile_id'},'switch_model':{'request_id','session_id','profile_id'},
                'send':{'request_id','session_id','text','form_context','language'}}
        if not isinstance(payload,dict) or action not in fields or set(payload)!=fields[action]:
            raise SafetyError('会话字段不匹配；不接受操作、审批、路径或工具参数')
        identity=_identifier(payload['request_id'])
        if action in {'create','switch_model'}:
            if payload['profile_id']!='legacy':_identifier(payload['profile_id'])
            if action=='switch_model':_identifier(payload['session_id'])
        else:
            _identifier(payload['session_id'])
            if (not isinstance(payload['text'],str) or not 1<=len(payload['text'].strip())<=32768
                    or not isinstance(payload['language'],str) or payload['language'] not in {'zh-CN','en'}):
                raise SafetyError('请输入有效会话消息')
            form_snapshot(payload['form_context'])
        signature=digest(json.dumps({'action':action,'payload':payload},sort_keys=True,ensure_ascii=False).encode())
        with self.hub.lock,self.lock:
            if self.closed or self.hub.closed or getattr(self.hub,'stopping',False):raise SafetyError('会话服务已停止或正在停止')
            if self.active:raise SafetyError('会话正在运行，请读取原操作结果，不重复发送')
            self.active=identity
        try:
            def begin():
                with self.lock:
                    return self._begin_job(action,payload,identity,signature)
            job,created=self.executor.submit(begin).result()
            if created:self.executor.submit(self._run,job,payload)
            else:self.active=None
            return {k:v for k,v in job.items() if k!='signature'}
        except BaseException:
            self.active=None
            raise

    def _begin_job(self,action,payload,identity,signature):
        head=self._head();jobs=head.load('job')
        if identity in jobs:
            job=jobs[identity]
            if job['signature']!=signature:raise SafetyError('原会话操作标识已绑定其他内容')
            return job,False
        if len(jobs)>=1000:raise SafetyError('会话操作历史已达上限；保留记录')
        job={'id':identity,'signature':signature,'action':action,'status':'running',
             'session_id':payload.get('session_id'),'created_at':now()}
        head.put('job',identity,job);return job,True

    def _run(self,job,payload):
        attempt=ledger=None
        try:
            head=self._head()
            if job['action'] in {'create','switch_model'}:
                with self.hub.lock:
                    profile=self.hub.model_profiles.resolve_selectable(payload['profile_id'])
                    source=history=None
                    if job['action']=='switch_model':
                        source=_record(payload['session_id'],head.load('assistant_session').get(payload['session_id']))
                        current=self.hub.model_profiles.resolve(source['profile_id'])
                        if current['config_sha256']!=source['profile_sha256']:raise SafetyError('原会话模型配置变化；不续接')
                        if source['native_file']:
                            native=unlinked(self.root/'conversations'/source['id']/'sessions'/source['native_file'])
                            if not native.is_file() or native.stat().st_size>MAX_ASSISTANT_NATIVE_BYTES or not has_native_checkpoint(native.read_bytes(),source['native_sha256']):
                                raise SafetyError('原会话Native检查点不完整；不续接未知历史')
                        inherited=self._history(source)
                        own=[{key:m[key] for key in ('role','content')} for m in source['messages']]
                        messages=(inherited['messages'] if inherited else [])+own
                        if len(messages)>400 or len(json.dumps(messages,ensure_ascii=False).encode())>MAX_ASSISTANT_HISTORY_BYTES:
                            raise SafetyError('成功对话文本超过续接容量，请保留原会话并新建会话')
                        target_config,_=self.hub.model_profiles._provider(profile['path'],profile['config_sha256'])
                        credentials=(*self._credentials(source),target_config['PI_MAINTENANCE_API_KEY'])
                        if contains_credential([messages,source['messages']],credentials):
                            raise SafetyError('原对话包含模型凭据，已拒绝续接；原记录与账保持')
                    identity=uuid.uuid4().hex
                    record={'id':identity,'profile_id':profile['id'],'model':profile['model'],
                        'profile_sha256':profile['config_sha256'],'title':'新会话','messages':[],
                        'created_at':now(),'native_file':None,'native_sha256':None}
                    if source is not None:
                        history={'schema_version':1,'target_session_id':identity,'target_profile_sha256':profile['config_sha256'],
                            'source_session_id':source['id'],'source_profile_id':source['profile_id'],
                            'source_profile_sha256':source['profile_sha256'],'source_message_count':len(messages),
                            'source_own_message_count':len(own),'source_messages_sha256':digest(json.dumps(own,sort_keys=True,ensure_ascii=False).encode()),
                            'source_native_sha256':source['native_sha256'],'messages':messages}
                        history['context_sha256']=digest(json.dumps(history,sort_keys=True,ensure_ascii=False).encode())
                        # Save the link before publishing the new empty session; no model or ledger is invoked.
                        head.put('assistant_history',identity,history)
                    head.put('assistant_session',identity,record)
                job.update(session_id=identity,result={'session_id':identity})
            else:
                record=head.load('assistant_session').get(payload['session_id'])
                if record is None:raise SafetyError('原会话不存在')
                record=_record(payload['session_id'],record)
                if len(record['messages'])>=400:raise SafetyError('本会话消息已达上限，请保留并新建会话')
                profile=self.hub.model_profiles.resolve(record['profile_id'])
                if profile['config_sha256']!=record['profile_sha256']:raise SafetyError('会话模型配置已变化；保留原历史和账')
                credentials=self._credentials(record)
                history=self._history(record)
                if contains_credential({'text':payload['text'],'form_context':payload['form_context'],
                    'messages':record['messages'],'history':history['messages'] if history else []},credentials):
                    raise SafetyError('消息、表单或历史包含模型凭据，已拒绝发送；原记录与账保持')
                directory=unlinked(self.root/'conversations'/record['id'])
                before_native=None
                if record['native_file']:
                    native=unlinked(directory/'sessions'/record['native_file'])
                    if not native.is_file() or native.stat().st_size>MAX_ASSISTANT_NATIVE_BYTES or digest(native.read_bytes())!=record['native_sha256']:
                        raise SafetyError('会话Native历史不完整；保留原记录，不重放')
                    before_native=native.read_bytes()
                ledger=self._ledger(profile)
                if not ledger.budget()['requests_remaining']:
                    job['error_category']='budget_blocked'
                    raise SafetyError('助手模型的剩余额度为零；请查看会话与用量。')
                attempt,allowance=ledger.reserve(maximum=MAX_ASSISTANT_REQUESTS_PER_TURN)
                directory.mkdir(parents=True,exist_ok=True)
                result=self._converse(profile,directory,record,payload,allowance)
                candidate_name=result.get('native_file')
                candidate=None
                if isinstance(candidate_name,str) and _NATIVE.fullmatch(candidate_name):
                    candidate=unlinked(directory/'sessions'/candidate_name)
                leaked=contains_credential(result,credentials)
                if candidate is not None and candidate.is_file() and candidate.stat().st_size<=MAX_ASSISTANT_NATIVE_BYTES:
                    native_bytes=candidate.read_bytes()
                    if before_native is not None and not native_bytes.startswith(before_native):
                        raise SafetyError('原会话Native前缀变化；保留未知占用')
                    # A previous provider error may contain private raw text in its local
                    # Native entry. Only the new suffix can be a new credential echo.
                    appended=native_bytes[len(before_native):] if before_native is not None else native_bytes
                    leaked=leaked or any(contains_credential(json.loads(line),credentials) for line in appended.splitlines() if line.strip())
                if leaked:raise SafetyError('模型响应包含凭据，已拒绝保存回复；原账保留未知占用')
                current=self.hub.model_profiles.resolve(record['profile_id'])
                if current['config_sha256']!=record['profile_sha256']:raise SafetyError('会话期间模型配置变化；保留未知消费')
                proposal=form_proposal(result['proposal'],payload['form_context']) if result.get('proposal') else None
                if (result.get('schema_version')!=1 or result.get('active_tools')!=['get_help','propose_form_values']
                        or type(result.get('requests')) is not int or not 1<=result['requests']<=allowance
                        or not isinstance(result.get('text'),str) or len(result['text'].encode())>MAX_ASSISTANT_TEXT_BYTES
                        or not isinstance(result.get('native_file'),str)
                        or not _NATIVE.fullmatch(result['native_file'])
                        or (record['native_file'] is not None and result['native_file']!=record['native_file'])):
                    raise SafetyError('会话回执未核验；保留未知消费')
                native=unlinked(directory/'sessions'/result['native_file'])
                if not native.is_file() or native.stat().st_size>MAX_ASSISTANT_NATIVE_BYTES:raise SafetyError('会话检查点无效')
                ledger.settle(attempt,{'requests':result['requests'],'purpose':'conversation-v1'},success=True)
                attempt=None
                message_id=uuid.uuid4().hex
                record['messages'] += [{'id':uuid.uuid4().hex,'role':'user','content':payload['text']},
                    {'id':message_id,'role':'assistant','content':result['text'],
                     **({'proposal':proposal} if proposal else {})}]
                if len(record['messages'])==2:record['title']=payload['text'].strip()[:60]
                record.update(native_file=result['native_file'],native_sha256=digest(native.read_bytes()))
                head.put('assistant_session',record['id'],record)
                job.update(result={'session_id':record['id'],'message_id':message_id})
            job.update(status='finished',finished_at=now())
        except Exception as error:
            if isinstance(error, (AssistantRequestTimeout, subprocess.TimeoutExpired)):
                job['error_category']='timeout'
            if ledger is not None and attempt is not None:
                if type(error) is CountedPlanningFailure:
                    ledger.settle(attempt,error.receipt,success=False,failure_receipt=error)
                    category=error.receipt['failure_category']
                    job.update(error_category=category,requests_counted=True,requests=error.receipt['requests'])
                    # Append-only error Native remains local; this checkpoint permits a future
                    # explicit send while only successful messages enter linked history.
                    record.update(native_file=error.receipt['native_file'],native_sha256=error.receipt['native_sha256'])
                    head.put('assistant_session',record['id'],record)
                else:
                    ledger.settle(attempt,{'requests':None,'purpose':'conversation-v1'},success=False)
            job.update(status='failed',finished_at=now(),error=str(error) if isinstance(error,SafetyError) else
                '会话未完成；可能是模型、网络或参数不受支持。保留原记录和未知占用，不自动重试。')
            if job.get('requests_counted'):
                job['error']=HINTS[job['error_category']][payload.get('language')=='en']
        finally:
            with self.lock:
                try:self._head().put('job',job['id'],job)
                finally:self.active=None

    def _converse(self,profile,directory,record,payload,allowance):
        from .help_docs import help_document
        config,_=self.hub.model_profiles._provider(profile['path'],profile['config_sha256'])
        env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA','COMSPEC','PATHEXT'}}
        env.update(config)
        node=shutil.which('node')
        if not node:raise SafetyError('会话需要已安装Node与锁定Pi运行时')
        command=[node,str(runtime_script('assistant.mjs'))]
        if self.mock_url:command += ['--mock-url',self.mock_url]
        request={'schema_version':1,'request_budget':allowance,'directory':str(directory),
            'native_file':record['native_file'],'text':payload['text'],'form_context':payload['form_context'],
            'language':payload['language'],'help':help_document(payload['language'])['content'],
            'request_id':payload['request_id'],'session_id':record['id'],'profile_sha256':record['profile_sha256']}
        history=self._history(record)
        history_context=history['messages'] if history is not None and record['native_file'] is None else None
        if history_context is not None:request['history_context']=history_context
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,
            start_new_session=os.name!='nt',creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
        self.process=process
        try:
            from .pi_live import communicate_live
            output,errors=communicate_live(process,json.dumps(request,ensure_ascii=False).encode(),allowance,
                lambda _evidence:None,self.cancelling,PROCESS_TIMEOUT_SECONDS,output_limit_bytes=MAX_ASSISTANT_REPLY_BYTES)
            if process.returncode and errors.splitlines()[-1:]==[b'Conversation model request timed out.']:
                raise AssistantRequestTimeout('模型请求超时；本次占用保留供核对。')
            if process.returncode or len(output)>MAX_ASSISTANT_REPLY_BYTES:raise SafetyError('模型未返回有效会话；保留原账，不自动重试')
            result=json.loads(output)
            expected=resolve_model_limits(config,mock_url=self.mock_url)['max_output_tokens']
            if result.get('max_output_tokens')!=expected:raise SafetyError('模型输出配置回执不匹配')
            current=self.hub.model_profiles.resolve(record['profile_id'])
            if current['config_sha256']!=record['profile_sha256']:raise SafetyError('会话期间模型配置变化；保留未知消费')
            if result.get('status')=='failed':
                receipt=verify_terminal_native(result,allowance,directory=directory,record=record,payload=payload,
                    profile_sha256=record['profile_sha256'],history_context=history_context)
                raise CountedPlanningFailure(receipt,allowance)
            return result
        except BaseException:
            if process.poll() is None:
                from .runner import stop_tree
                stop_tree(process)
                process.communicate()
            raise
        finally:
            self.process=None
            for stream in (process.stdin,process.stdout,process.stderr):
                if stream:stream.close()

    def close(self):
        if self.closed:return
        self.closed=True
        # Normal service stop refuses busy state. On OS shutdown, interrupt only
        # this owned subprocess and retain the original unknown reservation.
        self.cancelling.set()
        def release():
            for ledger in self.ledgers.values():ledger.close()
            if self.head is not None:self.head.close()
        self.executor.submit(release).result()
        self.executor.shutdown(wait=True)
