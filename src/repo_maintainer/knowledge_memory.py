"""Reviewed, versioned knowledge derived from original repository episodes."""
import json
import uuid
from .knowledge_schema import validate_note
from .models import digest
from .store import now, redact
from .task_session import fingerprint, require, TASK_ID
from .experience_memory import KIND as EPISODES, ERRORS
from .semantic_retrieval import session_rank,record_selection,note_text

KIND = 'repository_knowledge'
PIN = 'knowledge_task'
LIMIT = 100


class KnowledgeMemory:
    def __init__(self, session):
        self.session = session
        self.experience = session.experience
        self.registry = session.registry

    def parent_reference(self, identity):
        entry=self.registry.load(KIND).get(identity)
        require(entry and entry['lifecycle']=='active','Select an intact current knowledge version to correct')
        payload=self.validate(identity,entry,support=False)
        return {'id':identity,'payload_sha256':entry['payload_sha256'],'source_sha256':payload['source_sha256'],
                'note':payload['note'],'review':entry['review'],'review_version':entry['review_version'],
                'classification':'proposed','runtime_authorized':False}

    def source(self, identities, parent=None):
        require(isinstance(identities,list) and 1 <= len(identities) <= 3 and
                len(set(identities)) == len(identities) and all(isinstance(i,str) and TASK_ID.fullmatch(i) for i in identities),
                'Select 1-3 distinct original experience IDs')
        hits = []
        entries = self.registry.load(EPISODES)
        for identity in identities:
            entry = entries.get(identity)
            require(entry and entry['review'] == 'accepted', 'Reflection requires accepted original experiences')
            payload = self.experience.validate(identity,entry,applicable=True)
            hits.append({'id':identity,'review_version':entry['review_version'],
                         'payload_sha256':entry['payload_sha256'],'episode':payload})
        value = {'schema_version':1,'repository_sha256':self.experience.repository,
                 'policy_sha256':self.experience.policy(),'source_files':self.experience.files(),'hits':hits}
        if parent is not None:value.update(schema_version=2,previous=self.parent_reference(parent))
        require(len(json.dumps(value,ensure_ascii=False).encode()) <= 47000, 'Reflection source exceeds input byte budget')
        return {**value,'context_sha256':fingerprint(value)}

    def preview(self, identities, parent=None):
        source = self.source(identities,parent)
        require(len(self.registry.load(KIND)) < LIMIT, 'Knowledge version capacity reached; original evidence retained')
        budget = self.session.budget_registry.budget()
        require(not budget['requests_reserved_unknown'] and budget['requests_remaining'] > 0,
                'Inspect unknown consumption or remaining budget before reflection')
        revision = fingerprint({'source':source,'budget':budget,'session':self.session.token,
                                'knowledge':self.registry.load(KIND),
                                'provider':self.session.provider.inner.task_identity()})
        return {'revision':revision,'experience_ids':identities,'source_sha256':source['context_sha256'],
                **({'knowledge_id':parent,'previous':source['previous']} if parent is not None else {}),
                'remaining_requests':budget['requests_remaining'], 'paid_model_call_possible':True,
                'pending_candidate_only':True}

    def propose(self, identities, revision, parent=None):
        require(self.preview(identities,parent)['revision'] == revision, 'Reflection preview changed; review it again')
        source = self.source(identities,parent)
        original_files = self.experience.files()
        provider_binding = self.session.provider.inner.task_identity()
        def guard():
            if self.session.control:
                self.session.control.check()
            self.session._task_sources('memory reflection')
            require(self.source(identities,parent) == source and self.experience.files() == original_files
                    and self.session.provider.inner.task_identity() == provider_binding,
                    'Reflection evidence, review, policy or current source changed')
        guard()
        result = self.session.provider.reflect(source,guard)
        # Reserve/settle completed in the original ledger. A later metadata failure never refunds/replays it.
        guard()
        evidence = self.session.provider.evidence
        origin = {'kind':'model_reflection','source':source,'candidate':result['candidate'],
                  'attempt_id':evidence['attempt_id'],'evidence_sha256':fingerprint(evidence)}
        if parent is not None:origin['parent_sha256']=source['previous']['payload_sha256']
        return self._create(result['candidate'],source,origin,parent)

    def _create(self, note, source, origin, parent=None):
        validate_note(note)
        previous = self.registry.load(KIND)
        require(len(previous) < LIMIT,'Knowledge version capacity reached')
        payload = {'schema_version':1,'repository_sha256':self.experience.repository,
                   'classification':'proposed','runtime_authorized':False,'note':note,
                   'source_sha256':source['context_sha256'],
                   'refs':[{k:h[k] for k in ('id','payload_sha256','review_version')} for h in source['hits']],
                   'source_files':source['source_files'],'policy_sha256':source['policy_sha256'],
                   'parent':parent}
        # Repeated explicit reflection is still metered, but identical content needs no duplicate version.
        same = next((i for i,e in previous.items() if e['payload']==payload),None)
        if same:return {'knowledge_id':same,'unchanged':True}
        identity = uuid.uuid4().hex
        entry = {'payload':payload,'payload_sha256':fingerprint(payload),'review':'pending','review_version':1,
                 'lifecycle':'active','origin':origin,'created_at':now()}
        self.registry.put(KIND,identity,entry)
        return {'knowledge_id':identity,'review':'pending'}

    def challenge(self, identity, entry):
        return fingerprint({'id':identity,'entry':entry,'session':self.session.token})

    def validate(self, identity, entry, *, applicable=False, ancestors=(), support=True):
        require(support or not applicable,'Historical origin validation cannot grant applicability')
        require(isinstance(identity,str) and TASK_ID.fullmatch(identity) and identity not in ancestors and len(ancestors)<LIMIT,
                'Invalid or cyclic knowledge identity')
        payload, origin = entry['payload'], entry['origin']
        require(set(payload)=={'schema_version','repository_sha256','classification','runtime_authorized','note',
                              'source_sha256','refs','source_files','policy_sha256','parent'}, 'Invalid knowledge payload fields')
        require(payload['repository_sha256']==self.experience.repository and payload['schema_version']==1
                and payload['classification']=='proposed' and payload['runtime_authorized'] is False
                and entry['payload_sha256']==fingerprint(payload) and type(entry['review_version']) is int
                and entry['review_version']>0 and entry['review'] in {'pending','accepted','rejected'}
                and entry['lifecycle'] in {'active','superseded'}, 'Knowledge identity or content changed')
        validate_note(payload['note'])
        require(origin['candidate']==payload['note'], 'Knowledge candidate differs from original proposal')
        source = origin['source']
        require(type(source.get('schema_version')) is int and source['schema_version'] in {1,2}
                and set(source)=={'schema_version','repository_sha256','policy_sha256','source_files','hits','context_sha256'}
                    | ({'previous'} if source['schema_version']==2 else set()),'Invalid knowledge source version')
        body = {k:v for k,v in source.items() if k!='context_sha256'}
        require(fingerprint(body)==source['context_sha256']==payload['source_sha256']
                and source['repository_sha256']==self.experience.repository
                and source['source_files']==payload['source_files'] and source['policy_sha256']==payload['policy_sha256'],
                'Knowledge original source receipt differs')
        require(payload['refs']==[{k:h[k] for k in ('id','payload_sha256','review_version')} for h in source['hits']],
                'Knowledge original citations changed')
        if origin['kind']=='model_reflection':
            attempt = self.registry.load('attempt').get(origin['attempt_id'])
            require(attempt and attempt['status']=='finished' and fingerprint(attempt['evidence'])==origin['evidence_sha256']
                    and attempt['evidence'].get('purpose')=='memory_reflection'
                    and attempt['evidence'].get('knowledge_candidate_sha256')==fingerprint(payload['note'])
                    and attempt['evidence'].get('reflection_context_sha256')==source['context_sha256'],
                    'Knowledge reflection consumption receipt changed')
            if payload['parent'] is not None:
                parent=self.registry.load(KIND).get(payload['parent']);previous=source.get('previous')
                require(source['schema_version']==2 and parent and isinstance(previous,dict) and previous=={
                    'id':payload['parent'],'payload_sha256':parent['payload_sha256'],
                    'source_sha256':parent['payload']['source_sha256'],'note':parent['payload']['note'],
                    'review':previous.get('review'),'review_version':previous.get('review_version'),
                    'classification':'proposed','runtime_authorized':False}
                    and previous['review'] in {'pending','accepted','rejected'}
                    and type(previous['review_version']) is int and previous['review_version']>0
                    and origin['parent_sha256']==parent['payload_sha256'],'Knowledge correction parent changed')
                self.validate(payload['parent'],parent,ancestors=(*ancestors,identity),support=False)
            else:require(source['schema_version']==1,'Unlinked correction source')
        else:
            require(origin['kind']=='operator_revision' and payload['parent']==origin['parent'], 'Invalid knowledge revision origin')
            parent = self.registry.load(KIND).get(payload['parent'])
            require(parent and parent['payload_sha256']==origin['parent_sha256'], 'Knowledge parent version changed')
            require(source==parent['origin']['source'],'Operator revision cannot replace original supporting sources')
            self.validate(payload['parent'],parent,applicable=applicable,ancestors=(*ancestors,identity),support=support)
        episodes = self.registry.load(EPISODES)
        for hit in source['hits'] if support else []:
            item = episodes.get(hit['id'])
            require(item and item['review']=='accepted' and item['review_version']==hit['review_version']
                    and item['payload_sha256']==hit['payload_sha256'], 'Knowledge supporting experience changed or was revoked')
            require(self.experience.validate(hit['id'],item,applicable=applicable)==hit['episode'],
                    'Knowledge evidence differs from original facts')
        if applicable:
            require(payload['policy_sha256']==self.experience.policy() and
                    set(payload['source_files'])==set(self.experience.files()), 'Knowledge policy or authorized paths differ')
        return payload

    def revise(self, identity, revision, candidate):
        entry = self.registry.load(KIND).get(identity)
        require(entry and self.challenge(identity,entry)==revision, 'Knowledge revision challenge changed')
        self.validate(identity,entry,applicable=True)
        validate_note(candidate)
        return self._create(candidate,entry['origin']['source'],
            {'kind':'operator_revision','source':entry['origin']['source'],'candidate':candidate,
             'parent':identity,'parent_sha256':entry['payload_sha256']}, identity)

    def review(self, identity, revision, choice):
        entry = self.registry.load(KIND).get(identity)
        require(entry and self.challenge(identity,entry)==revision and choice in {'accepted','rejected'},
                'Knowledge review challenge changed')
        entries = self.registry.load(KIND)
        parent_id = entry['payload']['parent']
        parent = entries.get(parent_id) if parent_id else None
        if choice=='accepted':
            self.validate(identity,entry,applicable=True)
            require(entry['lifecycle']=='active','Superseded version cannot silently reactivate')
            if parent_id:
                require(parent and parent['lifecycle']=='active', 'Previous version changed; create a revision of the current version')
        updated={**entry,'review':choice,'review_version':entry['review_version']+1}
        with self.registry.transaction() as db:
            def original(i):
                row=db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(KIND,i)).fetchone()
                return json.loads(row[0]) if row else None
            require(original(identity)==entry,'Knowledge changed during review')
            if choice=='accepted' and parent_id:
                require(original(parent_id)==parent,'Previous version changed during review')
                self.registry.put(KIND,parent_id,{**parent,'lifecycle':'superseded','superseded_by':identity},db)
            self.registry.put(KIND,identity,updated,db)
            self.registry.put('knowledge_review',uuid.uuid4().hex,{'id':identity,'decision':choice,
                'payload_sha256':entry['payload_sha256'],'review_version':updated['review_version'],'time':now()},db)
        return {'knowledge_id':identity,'review':choice}

    def eligible(self):
        valid, excluded = {}, []
        for identity,entry in self.registry.load(KIND).items():
            if entry.get('review')!='accepted' or entry.get('lifecycle')!='active':continue
            try:
                valid[identity]=self.validate(identity,entry,applicable=True)
            except ERRORS as error:excluded.append({'id':identity,'reason':redact(str(error))})
        # Matching type/title is a conservative topic collision, not semantic contradiction inference.
        groups={}
        for identity,payload in valid.items():
            key=(payload['note']['kind'],payload['note']['title'].casefold().strip())
            groups.setdefault(key,[]).append(identity)
        for group in groups.values():
            if len(group)>1:
                for identity in group:
                    excluded.append({'id':identity,'reason':'Multiple accepted versions share a topic; explicitly revise and review'})
                    valid.pop(identity)
        return valid,excluded

    def search(self, issue):
        require(isinstance(issue,str) and 0<len(issue)<=2000,'Invalid knowledge search')
        valid,excluded=self.eligible(); entries=self.registry.load(KIND)
        documents={i:note_text(p['note']) for i,p in valid.items()}
        hits=[];size=0
        ranked,_,strategy=session_rank(self.session,issue,documents)
        for _,identity in ranked:
            entry,payload=entries[identity],valid[identity]
            hit={'id':identity,'payload_sha256':entry['payload_sha256'],'review_version':entry['review_version'],
                 'knowledge':payload,'applicability':'matching_original_source' if payload['source_files']==self.experience.files() else 'requires_revalidation'}
            length=len(json.dumps(hit,ensure_ascii=False).encode())
            if size+length>32000:
                excluded.append({'id':identity,'reason':'Knowledge retrieval byte budget exceeded; full version retained'});continue
            hits.append(hit);size+=length
            if len(hits)==3:break
        return {'hits':hits,'excluded':excluded,'runtime_authorized':False,'retrieval':strategy}

    def view(self):
        rows=[];_,excluded=self.eligible(); reasons={r['id']:r['reason'] for r in excluded}
        for identity,entry in self.registry.load(KIND).items():
            row={'id':identity,'review':entry.get('review'),'lifecycle':entry.get('lifecycle'),
                 'revision':self.challenge(identity,entry),'freshness':'invalid','runtime_authorized':False}
            try:
                payload=self.validate(identity,entry,support=False)
                row.update(note=payload['note'],refs=payload['refs'],parent=payload['parent'],classification=payload['classification'],
                           payload_sha256=entry['payload_sha256'],history_integrity='verified',can_refresh=entry['lifecycle']=='active',
                           applicability='matching_original_source' if payload['source_files']==self.experience.files() else 'requires_revalidation')
                self.validate(identity,entry)
                row['freshness']='verified'
                self.validate(identity,entry,applicable=True)
            except ERRORS as error:
                row['reason']=redact(str(error));row['applicability']='invalid'
            if identity in reasons:row['reason']=reasons[identity]
            rows.append(row)
        return {'entries':rows,'enabled_for_new_tasks':self.experience.enabled(),'max_versions':LIMIT,
                'runtime_authorized':False,'scope':'original_repository_journal'}

    def begin(self, identity, issue, continuation):
        if continuation or not self.experience.enabled():return
        selection=self.search(issue)
        self.registry.put(PIN,identity,{'schema_version':1,'task_id':identity,'issue_sha256':digest(issue.encode()),
            'source_files':self.experience.files(),'hits':selection['hits']})
        record_selection(self.session,identity,issue,'knowledge',selection)

    def verify_task(self, identity, issue):
        pin=self.registry.load(PIN).get(identity)
        if pin is None:
            for plan in self.session.plans.values():
                evidence=plan['view'].get('planning_evidence',{})
                require(not(evidence.get('task_session',{}).get('task_id')==identity and evidence.get('knowledge_context_sha256')),
                        'Original knowledge selection is missing')
            return None
        require(pin['task_id']==identity and pin['issue_sha256']==digest(issue.encode()) and
                pin['source_files']==self.experience.files() and isinstance(pin['hits'],list) and len(pin['hits'])<=3
                and len({h['id'] for h in pin['hits']})==len(pin['hits']),'Knowledge task selection changed')
        eligible,_=self.eligible();entries=self.registry.load(KIND)
        for hit in pin['hits']:
            entry=entries.get(hit['id'])
            require(hit['id'] in eligible and entry['review_version']==hit['review_version'] and
                    entry['payload_sha256']==hit['payload_sha256'] and eligible[hit['id']]==hit['knowledge'],
                    'Pinned knowledge version, evidence or review changed')
        return pin

    def context(self, issue, repository, identity=None):
        planner = self.session.task_planner
        identity = identity or (planner.identity if planner else None)
        pin=self.verify_task(identity,issue) if identity else None
        if pin is None:return None
        paths=(*self.session.policy.allowed_paths,*self.session.provider.inner.readonly_paths)
        files=[{'path':p,'content':repository.read(p).decode(),'before_sha256':digest(repository.read(p)),
                'editable':p in self.session.policy.allowed_paths} for p in paths]
        body={'schema_version':1,'task_id':identity,'repository_sha256':self.experience.repository,
              'issue_sha256':digest(issue.encode()),'planning_base_sha':repository.head,
              'planning_files_sha256':digest(json.dumps(files,ensure_ascii=False,separators=(',',':')).encode()),
              'selection_files_sha256':fingerprint(pin['source_files']),'classification':'proposed',
              'runtime_authorized':False,'hits':pin['hits']}
        result={**body,'context_sha256':fingerprint(body)}
        require(len(json.dumps(result,ensure_ascii=False).encode())<=40000,'Knowledge context byte limit exceeded')
        return result

    def checked_plan(self, data, issue):
        evidence=data.get('provider_evidence',{});identity=evidence.get('task_session',{}).get('task_id')
        pin=self.verify_task(identity,issue) if identity else None
        if pin is None:
            require('knowledge_context_sha256' not in evidence,'Unexpected knowledge receipt');return
        from .git import RepositoryGateway
        require(self.context(issue,RepositoryGateway(self.session.repo),identity)['context_sha256']==evidence.get('knowledge_context_sha256'),
                'Saved knowledge receipt changed')
