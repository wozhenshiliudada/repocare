import {createHash} from 'node:crypto';

const hash=value=>createHash('sha256').update(value).digest('hex');
const ordered=value=>Array.isArray(value)?value.map(ordered):value&&typeof value==='object'
 ?Object.fromEntries(Object.keys(value).sort().map(key=>[key,ordered(value[key])])):value;
const same=(a,b)=>JSON.stringify(ordered(a))===JSON.stringify(ordered(b));
const keys=(value,names)=>value&&typeof value==='object'&&!Array.isArray(value)
 &&Object.keys(value).sort().join(',')===names.split(' ').sort().join(',');
const hex=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);
const id=value=>typeof value==='string'&&/^[a-z0-9][a-z0-9_-]{0,63}$/.test(value);
const decisions=['confirmed','proposed','pending','superseded'];
const metaFields='id topic path start_line end_line classification source_sha256 kind classification_origin';

export function verifiedRelations(value,hits,sourceVersion){
 const fail=()=>{throw new Error('Invalid decision relation context');};
 if(!keys(value,'schema_version manifest_sha256 claims_only runtime_authorized relations')
  ||value.schema_version!==1||!hex(value.manifest_sha256)||value.claims_only!==true||value.runtime_authorized!==false
  ||!Array.isArray(value.relations)||value.relations.length>50)fail();
 const hitIds=new Set(hits.map(row=>row.id)), endpoints=new Map(), relations=new Set(), edges=new Set(), graph=new Map();
 const topics=new Map();
 function classification(row){
  if(!decisions.includes(row.classification))return;
  if(!topics.has(row.topic))topics.set(row.topic,new Set());
  topics.get(row.topic).add(row.classification);
  if(topics.get(row.topic).size>1)fail(); // Claims cannot resolve the existing mixed-status gate.
 }
 for(const hit of hits)classification(hit);
 for(const relation of value.relations){
  if(!keys(relation,'id kind newer older statement_line statement_sha256')||!id(relation.id)
   ||relations.has(relation.id)||relation.kind!=='explicit_supersedes_claim'
   ||!Number.isSafeInteger(relation.statement_line)||!hex(relation.statement_sha256))fail();
  relations.add(relation.id);
  for(const role of ['newer','older']){
   const row=relation[role];
   if(!keys(row,metaFields+(sourceVersion===2?' origin':''))||!id(row.id)||!id(row.topic)
    ||row.kind!=='document'||row.classification_origin!=='source_marker'||!decisions.includes(row.classification)
    ||!hex(row.source_sha256)||!Number.isSafeInteger(row.start_line)||!Number.isSafeInteger(row.end_line)
    ||row.start_line<1||row.end_line<row.start_line||row.end_line-row.start_line>=100
    ||typeof row.path!=='string'||row.path.length>512||!row.path.endsWith('.md')||/[\\:\0]/.test(row.path)
    ||row.path.split('/').some(p=>!p||p==='.'||p==='..'||p.toLowerCase()==='.git'
      ||/^\.env(?:\.|$)|^(?:credentials|secrets)|\.(?:key|pem)$/i.test(p)))fail();
   if(sourceVersion===2){
    const origin=row.origin;
    if(!keys(origin,'member_id entry_id path catalog_sha256 sources_sha256 kind run_id')
     ||!id(origin.member_id)||!id(origin.entry_id)||origin.path!==row.path||!hex(origin.catalog_sha256)
     ||!hex(origin.sources_sha256)||origin.kind!=='catalog'||origin.run_id!==null)fail();
   }
   const prior=endpoints.get(row.id);
   if(prior&&!same(prior,row))fail();
   const hit=hits.find(h=>h.id===row.id);
   if(hit){
    const projected=Object.fromEntries((metaFields+(sourceVersion===2?' origin':'')).split(' ').map(k=>[k,hit[k]]));
    if(!same(projected,row))fail();
   }
   endpoints.set(row.id,row);classification(row);
  }
  const {newer,older}=relation,edge=newer.id+'\0'+older.id;
  if(newer.id===older.id||newer.topic!==older.topic||edges.has(edge)
   ||!(hitIds.has(newer.id)||hitIds.has(older.id))
   ||relation.statement_line<newer.start_line||relation.statement_line>newer.end_line
   ||hash('Supersedes: '+newer.id+' -> '+older.id)!==relation.statement_sha256)fail();
  edges.add(edge);if(!graph.has(newer.id))graph.set(newer.id,[]);graph.get(newer.id).push(older.id);
 }
 const visiting=new Set(),visited=new Set();
 function visit(node){
  if(visiting.has(node))fail();if(visited.has(node))return;
  visiting.add(node);for(const target of graph.get(node)||[])visit(target);
  visiting.delete(node);visited.add(node);
 }
 for(const node of graph.keys())visit(node);
 return value;
}
