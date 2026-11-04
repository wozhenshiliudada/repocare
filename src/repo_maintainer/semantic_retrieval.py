"""Explicit, local-only reference ranking. Never downloads or grants execution authority."""
from collections import OrderedDict
import re
from types import SimpleNamespace
from .models import SafetyError,digest
from .recovery import unlinked
from .task_session import fingerprint,require
from .ui_backup import read_bytes,json_value
from .memory_search import rank,terms,STRATEGY

MODEL='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
REVISION='e8f8c211226b894fcb81acc59f3b34ba3efd5f42'
FILES={
    'onnx/model_quint8_avx2.onnx':(118453870,'98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f'),
    'tokenizer.json':(9081518,'2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8')}
HYBRID='local_minilm_fields_fulltokens_bm25_rrf60_cosine0.5_v3'


class ModelPackage:
    """Two pinned data files; no arbitrary ONNX, Python, custom code or external initializer loading."""
    def __init__(self,manifest):
        self.path=unlinked(manifest);self.raw=read_bytes(self.path,16384)
        try:value=json_value(self.raw)
        except (ValueError,TypeError) as error:raise SafetyError('Invalid local encoder manifest') from error
        require(isinstance(value,dict) and type(value.get('schema_version')) is int and value['schema_version']==1
                and value.get('repository')==MODEL and value.get('revision')==REVISION
                and value.get('license')=='Apache-2.0' and isinstance(value.get('files'),list)
                and 2<=len(value['files'])<=10,'Unsupported local encoder package')
        rows={}
        for row in value['files']:
            require(isinstance(row,dict) and set(row)=={'path','bytes','sha256'} and isinstance(row['path'],str)
                    and row['path'] not in rows,'Invalid local encoder file descriptor')
            rows[row['path']]=row
        for name,(size,sha) in FILES.items():
            require(rows.get(name)=={'path':name,'bytes':size,'sha256':sha},'Local encoder file is not pinned')
        self.sha=digest(self.raw)
        self.checked()

    def checked(self,*,content=False):
        require(read_bytes(self.path,16384)==self.raw,'Registered local encoder manifest changed')
        values={}
        for name,(size,sha) in FILES.items():
            raw=read_bytes(self.path.parent/name,size)
            require(len(raw)==size and digest(raw)==sha,'Registered local encoder file changed or missing')
            if content:values[name]=raw
        return values


def fuse(lexical,dense):
    """Equal reciprocal-rank fusion, predeclared k=60; lexical order breaks exact score ties."""
    scores={i:1/(60+n) for n,(_,i) in enumerate(lexical,1)}
    for n,(_,identity) in enumerate(dense,1):scores[identity]=scores.get(identity,0)+1/(60+n)
    lexical_ids={i for _,i in lexical}
    return sorted(((s,i) for i,s in scores.items()),key=lambda r:(-r[0],r[1] not in lexical_ids,r[1]))


class LocalEncoder:
    def __init__(self,package):
        self.package=package;self.engine=None;self.cache=OrderedDict();self.cached_windows=0

    def clear(self):
        self.cache.clear();self.cached_windows=0;self.engine=None

    def _load(self,raw):
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as error:raise SafetyError('Optional local encoder dependencies are unavailable') from error
        ort.disable_telemetry_events()
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1;options.log_severity_level=3
        self.engine=ort.InferenceSession(raw['onnx/model_quint8_avx2.onnx'],sess_options=options,providers=['CPUExecutionProvider'])
        self.tokenizer=Tokenizer.from_str(raw['tokenizer.json'].decode('utf-8'))
        self.tokenizer.no_truncation();self.tokenizer.no_padding()
        require([self.tokenizer.token_to_id(t) for t in ('<s>','</s>','<pad>')]==[0,2,1],
                'Pinned encoder special token IDs changed')
        self.np=np

    def encode(self,texts):
        require(isinstance(texts,list) and len(texts)<=301 and all(isinstance(t,str) and len(t.encode())<=64000 for t in texts)
                and sum(len(t.encode()) for t in texts)<=2*1024*1024,'Local encoder input capacity exceeded; nothing silently truncated')
        raw=self.package.checked(content=self.engine is None)
        if self.engine is None:self._load(raw)
        np=self.np;keys=[digest(t.encode()) for t in texts];pending={};total=0
        for key,text in zip(keys,texts):
            if key in self.cache:
                total+=len(self.cache[key]);self.cache.move_to_end(key);continue
            if key in pending:total+=len(pending[key]);continue
            chunks=[];seen=set()
            for line in text.splitlines() or ['']:
                ids=self.tokenizer.encode(line,add_special_tokens=False).ids
                # Preserve explicit field boundaries. Split long prose at sentences before windowing it;
                # otherwise a short relevant tail is averaged together with a long unrelated introduction.
                parts=re.split(r'(?<=[.!?。！？;；])\s+',line) if len(ids)>126 else [line]
                for part in parts:
                    tokens=self.tokenizer.encode(part,add_special_tokens=False).ids
                    # Explicit full-token windows: never trust a library's possibly bounded overflow list.
                    for start in range(0,max(1,len(tokens)),94):
                        content=[0,*tokens[start:start+126],2]
                        identity=tuple(content)
                        if identity not in seen:
                            chunks.append(SimpleNamespace(ids=content+[1]*(128-len(content)),
                                attention_mask=[1]*len(content)+[0]*(128-len(content)),type_ids=[0]*128));seen.add(identity)
                        if start+126>=len(tokens):break
            require(len(chunks)<=64,'Local encoder per-text window capacity exceeded; full text retained')
            pending[key]=chunks;total+=len(chunks)
        require(total<=2048,'Local encoder window capacity exceeded; full records retained')
        new={}
        for key,chunks in pending.items():
            vectors=[]
            for offset in range(0,len(chunks),8):
                batch=chunks[offset:offset+8]
                inputs={name:np.asarray([getattr(e,field) for e in batch],dtype=np.int64)
                        for name,field in [('input_ids','ids'),('attention_mask','attention_mask'),('token_type_ids','type_ids')]}
                output=self.engine.run(None,{r.name:inputs[r.name] for r in self.engine.get_inputs()})[0]
                mask=inputs['attention_mask'][...,None].astype(np.float32)
                require(output.shape[:2]==mask.shape[:2] and output.shape[-1]==384,'Unexpected local encoder output')
                pooled=(output*mask).sum(axis=1)/mask.sum(axis=1).clip(min=1e-9)
                norm=np.linalg.norm(pooled,axis=1,keepdims=True)
                require(np.isfinite(pooled).all() and np.all(norm>0),'Invalid local encoder vectors')
                vectors.extend(pooled/norm)
            new[key]=np.asarray(vectors)
        self.package.checked()  # Never publish vectors produced across a package change.
        result=[self.cache[k] if k in self.cache else new[k] for k in keys]
        for key,value in new.items():
            self.cache[key]=value;self.cached_windows+=len(value)
        while self.cached_windows>2048 or len(self.cache)>512:
            _,value=self.cache.popitem(last=False);self.cached_windows-=len(value)
        return result

    def rank(self,query,documents):
        require(isinstance(query,str) and 0<len(query)<=2000 and isinstance(documents,dict)
                and len(documents)<=300,'Invalid local retrieval input')
        lexical=rank(query,documents)
        if not documents or not terms(query):return lexical,{}
        ids=sorted(documents);vectors=self.encode([query,*[documents[i] for i in ids]])
        dense=sorted(((float((v@vectors[0].T).max()),i) for i,v in zip(ids,vectors[1:])),key=lambda r:(-r[0],r[1]))
        dense=[(s,i) for s,i in dense if s>=.5]
        return fuse(lexical,dense),dict((i,s) for s,i in dense)


class SessionRetrieval:
    def __init__(self,session,package=None):
        self.session=session;self.package=package;self.encoder=LocalEncoder(package) if package else None

    def settings(self):
        saved=self.session.registry.load('retrieval_settings').get('default')
        if saved is None:return {'schema_version':1,'enabled':False,'model_manifest_sha256':None}
        require(isinstance(saved,dict) and set(saved)=={'schema_version','enabled','model_manifest_sha256'}
                and type(saved['schema_version']) is int and saved['schema_version']==1 and type(saved['enabled']) is bool
                and (saved['model_manifest_sha256'] is None or isinstance(saved['model_manifest_sha256'],str)
                and len(saved['model_manifest_sha256'])==64),'Invalid local retrieval settings')
        return saved

    def revision(self):
        return fingerprint({'settings':self.settings(),'model':self.package.sha if self.package else None,'session':self.session.token})

    def configure(self,enabled,revision):
        require(type(enabled) is bool and revision==self.revision(),'Retrieval settings changed; review again')
        if enabled:
            require(self.package is not None,'Register a pinned local encoder at startup before enabling it')
            self.package.checked()
        self.session.registry.put('retrieval_settings','default',{'schema_version':1,'enabled':enabled,
            'model_manifest_sha256':self.package.sha if enabled else None})
        if not enabled and self.encoder:self.encoder.clear()
        return {'enabled':enabled,'future_queries_and_new_tasks_only':True,'paid_model_requests':0}

    def view(self):
        try:
            settings=self.settings()
            return {**settings,'available':self.package is not None,'revision':self.revision(),
                    'registered_manifest_sha256':self.package.sha if self.package else None,
                    'model':MODEL if self.package else None,'data_bytes':sum(v[0] for v in FILES.values()) if self.package else 0,
                    'status':'registered' if self.package else 'unregistered','runtime_authorized':False,
                    'paid_model_requests':0,'future_queries_and_new_tasks_only':True}
        except SafetyError as error:return {'status':'invalid','enabled':False,'available':False,'reason':str(error)}

    def rank(self,query,documents):
        settings=self.settings()
        if not settings['enabled']:return rank(query,documents),{},STRATEGY
        require(self.package and self.package.sha==settings['model_manifest_sha256'],
                'Enabled local encoder registration missing or changed; explicitly disable or restore it')
        result,cosines=self.encoder.rank(query,documents)
        return result,cosines,HYBRID

    def close(self):
        if self.encoder:self.encoder.clear()


def session_rank(session,query,documents):
    retrieval=getattr(session,'retrieval',None)
    return retrieval.rank(query,documents) if retrieval else (rank(query,documents),{},STRATEGY)


def note_text(note):
    return '\n'.join(v for value in note.values() for v in ([value] if isinstance(value,str) else value))


def record_selection(session,task_id,issue,channel,selection):
    """Informational provenance on an existing native task; never restores permissions or changes its pins."""
    retrieval=getattr(session,'retrieval',None)
    if retrieval is None:return
    require(task_id in session.registry.load('pi_task'),'Retrieval provenance requires an original native task')
    previous=session.registry.load('retrieval_task').get(task_id,{'schema_version':1,'task_id':task_id,
        'issue_sha256':digest(issue.encode()),'channels':{}})
    require(previous['issue_sha256']==digest(issue.encode()) and channel not in previous['channels'],
            'Original retrieval selection already recorded or belongs to another issue')
    settings=retrieval.settings()
    previous['channels'][channel]={'strategy':selection['retrieval'],
        'model_manifest_sha256':settings['model_manifest_sha256'] if settings['enabled'] else None,
        'selected_hits_sha256':fingerprint(selection['hits']),'runtime_authorized':False}
    session.registry.put('retrieval_task',task_id,previous)
