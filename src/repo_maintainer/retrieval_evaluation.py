"""Offline frozen-fixture ranking evaluation. No model, task, source corpus or budget mutation."""
import json
import math
from pathlib import Path
from time import perf_counter
from .models import SafetyError,digest
from .recovery import unlinked
from .task_session import require
from .ui_backup import encoded,json_value,read_bytes,write
from .memory_items import ADAPTERS
from .memory_search import rank


def validate_suite(value):
    require(isinstance(value,dict) and set(value)=={'schema_version','name','provenance','documents','queries'}
            and type(value['schema_version']) is int and value['schema_version']==1
            and isinstance(value['name'],str) and 0<len(value['name'])<=100
            and isinstance(value['provenance'],str) and 0<len(value['provenance'])<=1000
            and isinstance(value['documents'],list) and 1<=len(value['documents'])<=200
            and isinstance(value['queries'],list) and 1<=len(value['queries'])<=300,'Invalid retrieval evaluation suite')
    identities=set();queries=set()
    for row in value['documents']:
        require(isinstance(row,dict) and set(row)=={'id','adapter','text'} and isinstance(row['adapter'],str) and row['adapter'] in ADAPTERS
                and isinstance(row['id'],str) and 0<len(row['id'])<=64 and row['id'] not in identities
                and isinstance(row['text'],str) and 0<len(row['text'].encode())<=12000,'Invalid retrieval fixture document')
        identities.add(row['id'])
    for row in value['queries']:
        require(isinstance(row,dict) and set(row)=={'id','split','category','query','relevant'}
                and isinstance(row['id'],str) and 0<len(row['id'])<=64 and row['id'] not in queries
                and isinstance(row['split'],str) and row['split'] in {'development','regression','semantic_probe','negative'}
                and isinstance(row['category'],str) and 0<len(row['category'])<=64
                and isinstance(row['query'],str) and 0<len(row['query'])<=2000
                and isinstance(row['relevant'],list) and len(row['relevant'])<=10
                and all(isinstance(i,str) and i in identities for i in row['relevant'])
                and len(set(row['relevant']))==len(row['relevant']),'Invalid retrieval fixture query')
        queries.add(row['id'])
        require((row['split']=='negative')==(not bool(row['relevant'])),'Negative fixture split must have no relevant IDs')
    return value


def metrics(ranked,relevant,k=5):
    require(type(k) is int and k>0 and len(set(ranked))==len(ranked),'Invalid ranking metrics input')
    relevant=set(relevant);top=ranked[:k]
    if not relevant:return {'answerable':False,'false_positive':bool(top)}
    reciprocal=next((1/(n+1) for n,i in enumerate(top) if i in relevant),0)
    dcg=sum(1/math.log2(n+2) for n,i in enumerate(top) if i in relevant)
    ideal=sum(1/math.log2(n+2) for n in range(min(k,len(relevant))))
    return {'answerable':True,'recall_at_1':len(set(ranked[:1])&relevant)/len(relevant),
            'recall_at_3':len(set(ranked[:3])&relevant)/len(relevant),
            'recall_at_5':len(set(top)&relevant)/len(relevant),'mrr_at_5':reciprocal,'ndcg_at_5':dcg/ideal}


def aggregate(rows):
    answerable=[r['metrics'] for r in rows if r['metrics']['answerable']]
    negatives=[r['metrics'] for r in rows if not r['metrics']['answerable']]
    return {'queries':len(rows),'answerable':len(answerable),'unanswerable':len(negatives),
            **{key:sum(v[key] for v in answerable)/len(answerable) if answerable else None
               for key in ('recall_at_1','recall_at_3','recall_at_5','mrr_at_5','ndcg_at_5')},
            'negative_false_positive_rate':sum(v['false_positive'] for v in negatives)/len(negatives) if negatives else None}


def evaluate_fixture(value):
    validate_suite(value);documents={r['id']:r['text'] for r in value['documents']};rows=[];start=perf_counter()
    for query in value['queries']:
        scores=rank(query['query'],documents);ranked=[identity for _,identity in scores]
        rows.append({**query,'ranked_ids':ranked[:5],'scores':[score for score,_ in scores[:5]],
                     'metrics':metrics(ranked,query['relevant'])})
    return {'name':value['name'],'provenance':value['provenance'],'document_count':len(documents),'query_count':len(rows),
            'aggregates':{'all':aggregate(rows),**{split:aggregate([r for r in rows if r['split']==split])
                         for split in sorted({r['split'] for r in rows})}},
            'categories':{category:aggregate([r for r in rows if r['category']==category])
                          for category in sorted({r['category'] for r in rows})},
            'rows':rows,'elapsed_seconds':perf_counter()-start,'paid_model_requests':0,
            'runtime_authorized':False,'layer':'raw_lexical_rank_only_not_controller_gates_or_repair_effectiveness'}


def evaluate_file(suite,output):
    suite=unlinked(suite);output=unlinked(output);raw=read_bytes(suite,512*1024)
    require(not suite.is_relative_to(output),'Evaluation output overlaps fixture input')
    require(not output.exists(),'Evaluation output exists; preserve previous result')
    value=validate_suite(json_value(raw));result=evaluate_fixture(value)
    result.update(schema_version=1,suite_sha256=digest(raw),
                  ranker_source_sha256=digest(read_bytes(Path(__file__).with_name('memory_search.py'),128*1024)))
    require(read_bytes(suite,512*1024)==raw,'Retrieval fixture changed while evaluating')
    output.mkdir(parents=True,exist_ok=False);write(output/'result.json',encoded(result))
    lines=['# Frozen retrieval fixture result','',value['provenance'],'',
           'This measures raw ranking only; it does not measure model repair quality or current source validity.','',
           f'Suite SHA256: {result["suite_sha256"]}',f'Ranker SHA256: {result["ranker_source_sha256"]}','',
           '| Split | Queries | Recall@3 | MRR@5 | nDCG@5 | Negative FP |','|---|---:|---:|---:|---:|---:|']
    for split,stats in result['aggregates'].items():
        show=lambda key:'n/a' if stats[key] is None else f'{stats[key]:.4f}'
        lines.append(f'| {split} | {stats["queries"]} | {show("recall_at_3")} | {show("mrr_at_5")} | {show("ndcg_at_5")} | {show("negative_false_positive_rate")} |')
    write(output/'report.md',('\n'.join(lines)+'\n').encode())
    return {'status':'retrieval_fixture_evaluated','output':str(output),'suite_sha256':result['suite_sha256'],
            'aggregates':result['aggregates'],'paid_model_requests':0,'runtime_authorized':False}
