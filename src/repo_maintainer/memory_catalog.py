"""Unified reference views and receipts for the existing, independently guarded contexts."""
import json
from dataclasses import asdict
from .git import RepositoryGateway
from .models import SafetyError,digest
from .store import redact
from .task_session import fingerprint,repository_identity,require
from . import memory_items as dto

ERRORS=(SafetyError,ValueError,OSError,KeyError,TypeError)
CHANNEL_EVIDENCE={'source':'memory_context_sha256','structure':'structure_context_sha256',
                  'experience':'experience_context_sha256','knowledge':'knowledge_context_sha256',
                  'maintenance':'maintenance_context_sha256'}
TASK_TYPES=('bug_fix','commit_inspection','pr_review','ci_investigation','doc_maintenance','dependency_analysis','repository_report')


class MemoryCatalog:
    def __init__(self,session):
        self.session=session
        repo=RepositoryGateway(session.repo);self.repository=repository_identity(repo.root,repo.common_dir)
        self.task_type='commit_inspection' if (session.budget_registry.path!=session.registry.path and session.source_guard) else 'bug_fix'

    def view(self):
        return {'schema_version':1,'adapters':list(dto.ADAPTERS),'task_type':self.task_type,
                'supported_task_types':list(TASK_TYPES[:2]),'future_task_types':list(TASK_TYPES[2:]),
                'runtime_authorized':False,'query_calls_model':False}

    def frame(self,invocation):
        policy=asdict(self.session.policy);policy['timeout_seconds']=float(policy['timeout_seconds'])
        return {'repository_sha256':self.repository,'policy_sha256':fingerprint(policy),
                'source_binding_sha256':fingerprint({'memory':self.session.memory.binding,'structure':self.session.structure.binding}),
                'task_type':self.task_type,'invocation_id':invocation}

    def compile(self,invocation,issue,repository,files,blocks,allowance,task_id):
        require(RepositoryGateway(self.session.repo).common_dir==repository.common_dir,'Memory load moved repositories')
        return dto.build_load(self.frame(invocation),issue,repository.head,files,blocks,allowance,task_id)

    def checked_plan(self,data,view_evidence):
        evidence=data.get('provider_evidence',{});load=evidence.get('memory_load')
        expected=view_evidence.get('memory_load_sha256')
        require(evidence.get('memory_load_sha256')==expected,'Original task load receipt changed')
        if load is None:
            require(expected is None,'Original task load manifest is missing');return
        body={k:v for k,v in load.items() if k!='context_sha256'}
        require(load['context_sha256']==fingerprint(body)==expected and load['runtime_authorized'] is False,
                'Original task load manifest changed')
        frame=self.frame(evidence['attempt_id'])
        require(all(load[k]==v for k,v in frame.items()) and load['planning_base_sha']==data['plan']['base_sha']
                and load['issue_sha256']==digest(data['plan']['issue'].encode()),'Original task route or scope changed')
        attempt=self.session.budget_registry.load('attempt').get(evidence['attempt_id'])
        require(attempt and attempt['status']=='finished' and attempt['evidence'].get('memory_load_sha256')==expected
                and attempt['evidence'].get('memory_load')==load,'Task load lacks its original settled attempt')
        channels={row['name']:row['context_sha256'] for row in load['channels']}
        require(all(channels.get(name)==evidence.get(field) for name,field in CHANNEL_EVIDENCE.items()),
                'Task load channel receipts differ from original evidence')

    def query(self,query,limit=20,*,owned=False):
        from .source_view import verified_source_view
        with verified_source_view():
            return self._query(query,limit,owned=owned)

    def _query(self,query,limit=20,*,owned=False):
        require(isinstance(query,str) and 0<len(query.strip())<=2000 and '\x00' not in query
                and type(limit) is int and 1<=limit<=50,'Invalid unified memory query')
        if self.session.active and not owned:return {**self.view(),'status':'busy','items':[],'excluded':[]}
        rows=[];excluded=[];files={};source_matches=set();search_text={}
        repo=RepositoryGateway(self.session.repo)
        for path in (*self.session.policy.allowed_paths,*self.session.provider.inner.readonly_paths):files[path]=digest(repo.read(path))
        def exclude(adapter,error):excluded.append({'adapter':adapter,'reason':redact(str(error))[:300]})
        view=self.session.memory.view()
        if view.get('status')=='verified':
            source_context=view['binding']['index_sha256']
            source_start=len(rows)
            try:
                rows += [dto.source_document(r,self.repository,source_context) for r in view['sources']]
                result=self.session.memory.verify(query)
                source_matches={dto.source_document(r,self.repository,result['context']['context_sha256'])['object_id']
                                for r in result['context']['hits']}
                search_text.update({dto.source_document(r,self.repository,source_context)['object_id']:r['excerpt']
                                    for r in self.session.memory.query_rows()})
                relations=view.get('decision_relations')
                if relations:
                    present={r['object_id'] for r in rows}
                    for raw in relations['relations']:
                        relation={**raw,**{role:{**raw[role],'kind':'document','classification_origin':'source_marker'} for role in ('newer','older')}}
                        for role in ('newer','older'):
                            endpoint=relation[role];identity=dto.ADAPTERS[0]+':'+endpoint['id']
                            if identity not in present:
                                rows.append(dto.source_document(endpoint,self.repository,source_context));present.add(identity)
                        rows.append(dto.decision_claim(relation,relations,self.repository,relations['manifest_sha256']))
            except ERRORS as error:
                del rows[source_start:];source_matches.clear()
                exclude(dto.ADAPTERS[0],error)
        else:exclude(dto.ADAPTERS[0],view.get('error') or 'source_not_enabled')
        if self.session.experience:
            for identity,entry in self.session.registry.load('repository_experience').items():
                try:
                    payload=self.session.experience.validate(identity,entry)
                    row=dto.run_case({'id':identity,'payload_sha256':entry['payload_sha256'],'episode':payload},
                        self.repository,entry['payload_sha256'],files,review={'pending':'awaiting_review'}.get(entry['review'],entry['review']))
                    rows.append(row);search_text[row['object_id']]=payload['issue']+'\n'+'\n'.join(
                        r['diagnosis']+'\n'+r.get('failure_excerpt','')+'\n'+r.get('change_excerpt','')+'\n'+'\n'.join(r['paths'])
                        for r in payload['rounds'])
                except ERRORS as error:exclude(dto.ADAPTERS[1],error)
        if self.session.maintenance:
            try:
                snapshot=self.session.maintenance.snapshot()
                rows += [dto.maintenance_state(r,self.repository,snapshot['snapshot_sha256']) for r in snapshot['observations']]
                excluded += [{'adapter':dto.ADAPTERS[2],'reason':r['reason']} for r in snapshot['excluded']]
            except ERRORS as error:exclude(dto.ADAPTERS[2],error)
        if self.session.structure.binding:
            try:
                from .memory_search import terms
                queries=list(dict.fromkeys([' '.join(query.split())[:128],*terms(query)[:3]]));seen=set()
                for literal in queries:
                    result=self.session.structure.preview(literal)
                    for node in result['hits']+result['related_nodes']:
                        if node['id'] not in seen:
                            rows.append(dto.structure_fact(node,result,self.repository,files));seen.add(node['id'])
            except ERRORS as error:exclude(dto.ADAPTERS[3],error)
        else:exclude(dto.ADAPTERS[3],'structure_not_enabled')
        if self.session.knowledge:
            for identity,entry in self.session.registry.load('repository_knowledge').items():
                try:
                    payload=self.session.knowledge.validate(identity,entry)
                    row=dto.knowledge_entry({'id':identity,'payload_sha256':entry['payload_sha256'],'knowledge':payload},
                        self.repository,entry['payload_sha256'],files,review={'pending':'awaiting_review'}.get(entry['review'],entry['review']),
                        lifecycle=entry['lifecycle'])
                    from .semantic_retrieval import note_text
                    rows.append(row);search_text[row['object_id']]=note_text(payload['note'])
                except ERRORS as error:exclude(dto.ADAPTERS[5],error)
        rows=dto.finalize(rows)
        present={r['adapter'] for r in rows}
        for adapter in dto.ADAPTERS:
            if adapter not in present and not any(e['adapter']==adapter for e in excluded):exclude(adapter,'no_matching_or_registered_items')
        from .memory_search import matched_terms
        from .memory_search import terms
        from .semantic_retrieval import session_rank
        if not terms(query):source_matches=set()
        documents={r['object_id']:r['topic']+'\n'+json.dumps(r['summary'],ensure_ascii=False)+'\n'+search_text.get(r['object_id'],'') for r in rows}
        ranked,cosines,strategy=session_rank(self.session,query,documents)
        scores={identity:score for score,identity in ranked}
        by_id={r['object_id']:r for r in rows};hits=[];size=0
        ordered=sorted((source_matches|set(scores))&set(by_id),key=lambda i:(i not in source_matches,-scores.get(i,0),i))
        for identity in ordered:
            row={**by_id[identity],'score':scores.get(identity),
                 'match_origin':'source_fulltext' if identity in source_matches else 'local_hybrid' if cosines else 'verified_record_bm25',
                 'semantic_cosine':cosines.get(identity),
                 'matched_terms':matched_terms(query,documents[identity])}
            length=len(json.dumps(row,ensure_ascii=False).encode())
            if size+length>128000:exclude(row['adapter'],'unified_query_byte_limit');continue
            hits.append(row);size+=length
            if len(hits)==limit:break
        from .memory_query_peers import topic_peers
        return {**self.view(),'status':'partial' if excluded else 'verified','query_sha256':digest(query.encode()),
                'items':hits,'excluded':excluded,'available_items':len(rows),'returned_items':len(hits),
                'snapshot_sha256':fingerprint(rows),'extra_model_requests':0,'retrieval':strategy,
                'topic_peers':topic_peers(rows,hits)}
