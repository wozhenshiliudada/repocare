"""Six read-only adapters over existing verified protocols; no generic write authority."""
import json
import re
from .models import digest,safe_context_path
from .task_session import fingerprint,require
from .runtime_limits import MAX_REQUESTS

ADAPTERS=('SourceDocumentAdapter','RunCaseAdapter','MaintenanceStateAdapter','StructureFactAdapter',
          'DecisionClaimAdapter','KnowledgeEntryAdapter')
DECISIONS={'confirmed','proposed','pending','superseded'}


def ref(kind,identity,sha,path=None,start=None,end=None):
    return {'kind':kind,'id':identity,'sha256':sha,'path':path,'start_line':start,'end_line':end}


def make(adapter,identity,repository,kind,topic,summary,refs,*,classification,review=None,freshness='valid',
         knowledge_type='semantic',task=None,run=None,plan=None,lifecycle=None,edges=(),context=None,loaded=False):
    return {'object_id':adapter+':'+identity,'repository_id':repository,'task_id':task,'run_id':run,'plan_id':plan,
            'adapter':adapter,'kind':kind,'knowledge_type':knowledge_type,'topic':topic,'summary':summary,
            'source_refs':refs,'classification':classification,'review_state':review,'freshness':freshness,
            'topic_group':None,'edges':list(edges),'lifecycle':lifecycle,
            'provenance':{'context_sha256':context,'integrity':'verified_original','reference_only':True},
            'in_context':loaded,'runtime_authorized':False}


def source_document(row,repository,context,loaded=False):
    adapter=ADAPTERS[1] if row['kind']=='case' else ADAPTERS[0]
    refs=[ref('document_file',row['id'],row['source_sha256'],row['path'],row['start_line'],row['end_line'])]
    if row.get('excerpt_sha256'):refs.append(ref('excerpt',row['id'],row['excerpt_sha256'],row['path'],row['start_line'],row['end_line']))
    origin=row.get('origin')
    if origin:
        refs += [ref('catalog',origin['member_id'],origin['catalog_sha256']),ref('source_catalog',origin['member_id'],origin['sources_sha256'])]
    return make(adapter,('case:' if row['kind']=='case' else '')+row['id'],repository,'run_case' if row['kind']=='case' else 'source_document',row['topic'],
        {k:row[k] for k in ('path','start_line','end_line','classification_origin')},refs,classification=row['classification'],
        knowledge_type='episodic' if row['kind']=='case' else 'semantic',run=origin.get('run_id') if origin else None,
        context=context,loaded=loaded)


def run_case(hit,repository,context,files,loaded=False,review='accepted'):
    e=hit['episode'];last=e['rounds'][-1] if e['rounds'] else None
    refs=[ref('episode_payload',hit['id'],hit['payload_sha256'])]
    refs += [ref(name,e['run_id'],sha) for name,sha in sorted(e['evidence'].items())]
    summary={'issue':e['issue'],'outcome':e['outcome'],'recorded_status':e['recorded_status'],
             'diagnosis':last['diagnosis'] if last else None,'test_exits':last['test_exits'] if last else []}
    return make(ADAPTERS[1],'episode:'+hit['id'],repository,'episode',e['issue'],summary,refs,classification='historical',
                review=review,freshness='valid' if e['source_files']==files else 'stale',knowledge_type='episodic',
                task=e['task_id'],run=e['run_id'],context=context,loaded=loaded)


def maintenance_state(row,repository,context,loaded=False):
    b=row['body'];r=row['source_ref']
    return make(ADAPTERS[2],row['id'],repository,'maintenance',b['title'],b,
                [ref('maintenance_'+r['kind'],r['id'],r['sha256'])],classification=row['classification'],
                freshness='valid' if b['proof']=='user_declaration' else 'unverified',knowledge_type='episodic',
                task=b['task_id'],run=b['run_id'],plan=b['plan_id'],context=context,loaded=loaded)


def structure_fact(node,structure,repository,files,loaded=False):
    refs=[ref('source_file',node['id'],node['source_sha256'],node['path'],node['start_line'],node['end_line']),
          ref('structure_graph',node['id'],structure['graph_sha256'])]
    edges=[{'kind':e['kind'],'source':ADAPTERS[3]+':'+e['source'],'target':ADAPTERS[3]+':'+e['target']}
           for e in structure['edges'] if node['id'] in (e['source'],e['target'])]
    return make(ADAPTERS[3],node['id'],repository,'structure_node',node['path'],
                {k:node[k] for k in ('kind','name','qualified_name','path','language','start_line','end_line')},refs,
                classification='reference',freshness='valid' if files.get(node['path'])==node['source_sha256'] else 'stale',
                edges=edges,context=structure['context_sha256'],loaded=loaded)


def decision_claim(row,relations,repository,context,loaded=False):
    newer,older=row['newer'],row['older']
    refs=[ref('statement',row['id'],row['statement_sha256'],newer['path'],row['statement_line'],row['statement_line']),
          ref('relation_manifest',row['id'],relations['manifest_sha256'])]
    summary={'kind':row['kind'],'newer':{'id':newer['id'],'classification':newer['classification']},
             'older':{'id':older['id'],'classification':older['classification']},'statement_line':row['statement_line'],'claims_only':True}
    return make(ADAPTERS[4],row['id'],repository,'decision_claim',newer['topic'],summary,refs,
                classification='reference',edges=[{'kind':'supersedes_claim','source':ADAPTERS[0]+':'+newer['id'],
                'target':ADAPTERS[0]+':'+older['id']}],context=context,loaded=loaded)


def knowledge_entry(hit,repository,context,files,loaded=False,review='accepted',lifecycle='active'):
    p=hit['knowledge'];note=p['note']
    refs=[ref('knowledge_payload',hit['id'],hit['payload_sha256'])]
    refs += [ref('episode_payload',r['id'],r['payload_sha256']) for r in p['refs']]
    edges=[{'kind':'derived_from','source':ADAPTERS[5]+':'+hit['id'],'target':ADAPTERS[1]+':episode:'+r['id']} for r in p['refs']]
    summary={k:note[k] for k in ('kind','title','summary')}
    summary.update(steps=len(note.get('steps',[])),preconditions=len(note.get('preconditions',[])),stop_conditions=len(note.get('stop_conditions',[])))
    return make(ADAPTERS[5],hit['id'],repository,'knowledge',note['title'],summary,refs,classification=p['classification'],
                review=review,freshness='valid' if p['source_files']==files else 'stale',
                knowledge_type='procedural' if note['kind']=='procedure' else 'semantic',lifecycle=lifecycle,
                edges=edges,context=context,loaded=loaded)


def finalize(rows):
    by_id={};groups={};decision_groups={}
    for row in rows:
        require(row['object_id'] not in by_id,'Duplicate unified memory identity')
        by_id[row['object_id']]=row
        groups.setdefault(row['topic'],set()).add(row['classification'])
        if row['kind']=='source_document' and row['classification'] in DECISIONS:
            decision_groups.setdefault(row['topic'],set()).add(row['classification'])
    for row in rows:
        labels=sorted(v for v in groups[row['topic']] if v is not None)
        row['topic_group']={'key':row['topic'],'classifications':labels,'mixed_decisions':len(decision_groups.get(row['topic'],()))>1}
    return sorted(rows,key=lambda row:row['object_id'])


def project_contexts(repository,files,blocks,loaded=True):
    rows=[];source=blocks.get('source');structure=blocks.get('structure');knowledge=blocks.get('knowledge')
    source_ids=set()
    if source:
        for row in source['hits']:
            rows.append(source_document(row,repository,source['context_sha256'],loaded));source_ids.add(row['id'])
        relations=source.get('decision_relations')
        if relations:
            for row in relations['relations']:
                for role in ('newer','older'):
                    if row[role]['id'] not in source_ids:
                        rows.append(source_document(row[role],repository,source['context_sha256'],loaded));source_ids.add(row[role]['id'])
                rows.append(decision_claim(row,relations,repository,source['context_sha256'],loaded))
    if blocks.get('experience'):
        value=blocks['experience']
        rows += [run_case(h,repository,value['context_sha256'],files,loaded) for h in value['hits']]
    if blocks.get('maintenance'):
        value=blocks['maintenance']
        rows += [maintenance_state(h,repository,value['context_sha256'],loaded) for h in value['hits']]
    if structure:
        rows += [structure_fact(n,structure,repository,files,loaded) for n in [*structure['hits'],*structure['related_nodes']]]
    if knowledge:
        rows += [knowledge_entry(h,repository,knowledge['context_sha256'],files,loaded) for h in knowledge['hits']]
    return finalize(rows)


def build_load(frame,issue,base,files,blocks,allowance,task_id):
    require(set(frame)=={'repository_sha256','policy_sha256','source_binding_sha256','task_type','invocation_id'}
            and frame['task_type'] in {'bug_fix','commit_inspection'}
            and all(isinstance(frame[k],str) and re.fullmatch('[a-f0-9]{64}',frame[k])
                    for k in ('repository_sha256','policy_sha256','source_binding_sha256'))
            and isinstance(frame['invocation_id'],str) and re.fullmatch('[a-f0-9]{32}',frame['invocation_id'])
            and (task_id is None or isinstance(task_id,str) and re.fullmatch('[a-f0-9]{32}',task_id))
            and type(allowance) is int and 1<=allowance<=MAX_REQUESTS,'Unsupported controlled memory route')
    require(set(blocks)=={'source','structure','experience','knowledge','maintenance'}
            and all(v is None or isinstance(v,dict) and isinstance(v.get('context_sha256'),str)
                    and re.fullmatch('[a-f0-9]{64}',v['context_sha256']) for v in blocks.values()),'Invalid memory channel envelope')
    file_hashes={f['path']:f.get('before_sha256') or digest(f['content'].encode()) for f in files}
    items=project_contexts(frame['repository_sha256'],file_hashes,blocks)
    channels=[{'name':name,'context_sha256':value['context_sha256']} for name,value in sorted(blocks.items()) if value is not None]
    present={row['adapter'] for row in items}
    excluded=[{'adapter':adapter,'reason':'not_in_current_context'} for adapter in ADAPTERS if adapter not in present]
    body={'schema_version':1,'route_version':1,**frame,'task_id':task_id,'issue_sha256':digest(issue.encode()),
          'planning_base_sha':base,'planning_files_sha256':digest(json.dumps(files,ensure_ascii=False,separators=(',',':')).encode()),
          'channels':channels,'items':items,'excluded':excluded,
          'failure_policy':'degraded' if not channels else 'partial_inject' if excluded else 'hard_stop',
          'required_source_integrity':'hard_stop','budget':{'request_allowance':allowance,'extra_model_requests':0},'runtime_authorized':False}
    require(len(items)<=160 and len(json.dumps(body,ensure_ascii=False).encode())<=128000,'Unified memory load exceeds bounded size')
    return {**body,'context_sha256':fingerprint(body)}


def validate_load_view(value,receipt):
    """Bounded report projection; never treats a self-hash as execution approval."""
    body={k:v for k,v in value.items() if k!='context_sha256'}
    require(set(value)=={'schema_version','route_version','repository_sha256','policy_sha256','source_binding_sha256','task_type',
            'invocation_id','task_id','issue_sha256','planning_base_sha','planning_files_sha256','channels','items','excluded',
            'failure_policy','required_source_integrity','budget','runtime_authorized','context_sha256'}
            and value['context_sha256']==receipt==fingerprint(body) and value['runtime_authorized'] is False
            and type(value['schema_version']) is int and value['schema_version']==1
            and type(value['route_version']) is int and value['route_version']==1
            and value['task_type'] in {'bug_fix','commit_inspection'}
            and isinstance(value['items'],list) and len(value['items'])<=160
            and len(json.dumps(value,ensure_ascii=False).encode())<=128000,'Invalid recorded memory load')
    for row in value['items']:
        require(set(row)=={'object_id','repository_id','task_id','run_id','plan_id','adapter','kind','knowledge_type','topic','summary',
                'source_refs','classification','review_state','freshness','topic_group','edges','lifecycle','provenance','in_context','runtime_authorized'}
                and row['adapter'] in ADAPTERS and row['runtime_authorized'] is False and row['in_context'] is True
                and isinstance(row['source_refs'],list) and 1<=len(row['source_refs'])<=8
                and isinstance(row['topic'],str) and len(row['topic'])<=2000
                and row['repository_id']==value['repository_sha256'],'Invalid recorded memory item')
        for source in row['source_refs']:
            require(set(source)=={'kind','id','sha256','path','start_line','end_line'}
                    and isinstance(source['sha256'],str) and re.fullmatch('[a-f0-9]{64}',source['sha256']),
                    'Invalid recorded memory provenance')
            if source['path'] is not None:safe_context_path(source['path'])
    return value
