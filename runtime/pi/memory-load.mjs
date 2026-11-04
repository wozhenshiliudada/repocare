// Rebuild a read-only load receipt from the independently validated legacy contexts.
import {createHash} from 'node:crypto';
import {experienceHash} from './experience-context.mjs';
import {MAX_REQUESTS} from './runtime-limits.mjs';
export const ADAPTERS=['SourceDocumentAdapter','RunCaseAdapter','MaintenanceStateAdapter','StructureFactAdapter','DecisionClaimAdapter','KnowledgeEntryAdapter'];
const sha=v=>createHash('sha256').update(v).digest('hex');
const pick=(v,n)=>Object.fromEntries(n.split(' ').map(k=>[k,v[k]]));
const ref=(kind,id,sha256,path=null,start_line=null,end_line=null)=>({kind,id,sha256,path,start_line,end_line});
const cmp=(a,b)=>Buffer.compare(Buffer.from(a),Buffer.from(b));
const sameFiles=(a,b)=>Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(k=>a[k]===b[k]);
const exact=(v,n)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join()===n.split(' ').sort().join();
const hex=(v,n=64)=>typeof v==='string'&&new RegExp(`^[a-f0-9]{${n}}$`).test(v);
function make(adapter,id,repo,kind,topic,summary,refs,o={}){
 return {object_id:adapter+':'+id,repository_id:repo,task_id:o.task??null,run_id:o.run??null,plan_id:o.plan??null,
  adapter,kind,knowledge_type:o.knowledge_type??'semantic',topic,summary,source_refs:refs,classification:o.classification,
  review_state:o.review??null,freshness:o.freshness??'valid',topic_group:null,edges:o.edges??[],lifecycle:o.lifecycle??null,
  provenance:{context_sha256:o.context??null,integrity:'verified_original',reference_only:true},
  in_context:true,runtime_authorized:false};
}
function document(row,repo,context){
 const adapter=ADAPTERS[row.kind==='case'?1:0],refs=[ref('document_file',row.id,row.source_sha256,row.path,row.start_line,row.end_line)];
 if(row.excerpt_sha256)refs.push(ref('excerpt',row.id,row.excerpt_sha256,row.path,row.start_line,row.end_line));
 if(row.origin)refs.push(ref('catalog',row.origin.member_id,row.origin.catalog_sha256),ref('source_catalog',row.origin.member_id,row.origin.sources_sha256));
 return make(adapter,(row.kind==='case'?'case:':'')+row.id,repo,row.kind==='case'?'run_case':'source_document',row.topic,
  pick(row,'path start_line end_line classification_origin'),refs,{classification:row.classification,
   knowledge_type:row.kind==='case'?'episodic':'semantic',run:row.origin?.run_id,context});
}
function episode(hit,repo,context,files){
 const e=hit.episode,last=e.rounds.at(-1),refs=[ref('episode_payload',hit.id,hit.payload_sha256)];
 for(const k of Object.keys(e.evidence).sort())refs.push(ref(k,e.run_id,e.evidence[k]));
 return make(ADAPTERS[1],'episode:'+hit.id,repo,'episode',e.issue,
  {issue:e.issue,outcome:e.outcome,recorded_status:e.recorded_status,diagnosis:last?.diagnosis??null,test_exits:last?.test_exits??[]},refs,
  {classification:'historical',review:'accepted',freshness:sameFiles(e.source_files,files)?'valid':'stale',knowledge_type:'episodic',
   task:e.task_id,run:e.run_id,context});
}
function maintenance(row,repo,context){
 const b=row.body,r=row.source_ref;
 return make(ADAPTERS[2],row.id,repo,'maintenance',b.title,b,[ref('maintenance_'+r.kind,r.id,r.sha256)],
  {classification:row.classification,freshness:b.proof==='user_declaration'?'valid':'unverified',knowledge_type:'episodic',
   task:b.task_id,run:b.run_id,plan:b.plan_id,context});
}
function structure(n,value,repo,files){
 const refs=[ref('source_file',n.id,n.source_sha256,n.path,n.start_line,n.end_line),ref('structure_graph',n.id,value.graph_sha256)];
 const edges=value.edges.filter(e=>e.source===n.id||e.target===n.id).map(e=>({kind:e.kind,source:ADAPTERS[3]+':'+e.source,target:ADAPTERS[3]+':'+e.target}));
 return make(ADAPTERS[3],n.id,repo,'structure_node',n.path,pick(n,'kind name qualified_name path language start_line end_line'),refs,
  {classification:'reference',freshness:files[n.path]===n.source_sha256?'valid':'stale',edges,context:value.context_sha256});
}
function relation(row,value,repo,context){
 const newer=row.newer,older=row.older,refs=[ref('statement',row.id,row.statement_sha256,newer.path,row.statement_line,row.statement_line),
  ref('relation_manifest',row.id,value.manifest_sha256)];
 return make(ADAPTERS[4],row.id,repo,'decision_claim',newer.topic,
  {kind:row.kind,newer:pick(newer,'id classification'),older:pick(older,'id classification'),statement_line:row.statement_line,claims_only:true},refs,
  {classification:'reference',edges:[{kind:'supersedes_claim',source:ADAPTERS[0]+':'+newer.id,target:ADAPTERS[0]+':'+older.id}],context});
}
function knowledge(hit,repo,context,files){
 const p=hit.knowledge,n=p.note,refs=[ref('knowledge_payload',hit.id,hit.payload_sha256),...p.refs.map(r=>ref('episode_payload',r.id,r.payload_sha256))];
 const edges=p.refs.map(r=>({kind:'derived_from',source:ADAPTERS[5]+':'+hit.id,target:ADAPTERS[1]+':episode:'+r.id}));
 return make(ADAPTERS[5],hit.id,repo,'knowledge',n.title,{...pick(n,'kind title summary'),steps:(n.steps??[]).length,
  preconditions:(n.preconditions??[]).length,stop_conditions:(n.stop_conditions??[]).length},refs,
  {classification:p.classification,review:'accepted',freshness:sameFiles(p.source_files,files)?'valid':'stale',
   knowledge_type:n.kind==='procedure'?'procedural':'semantic',lifecycle:'active',edges,context});
}
function finalize(rows){
 const ids=new Set(),groups=new Map(),decisions=new Map();
 for(const row of rows){if(ids.has(row.object_id))throw new Error('Duplicate unified memory identity');ids.add(row.object_id);
  if(!groups.has(row.topic))groups.set(row.topic,new Set());groups.get(row.topic).add(row.classification);
  if(row.kind==='source_document'&&['confirmed','proposed','pending','superseded'].includes(row.classification)){
   if(!decisions.has(row.topic))decisions.set(row.topic,new Set());decisions.get(row.topic).add(row.classification);}}
 for(const row of rows){const labels=[...groups.get(row.topic)].filter(v=>v!==null).sort();
  row.topic_group={key:row.topic,classifications:labels,mixed_decisions:(decisions.get(row.topic)?.size??0)>1};}
 return rows.sort((a,b)=>cmp(a.object_id,b.object_id));
}
export function buildMemoryLoad(frame,request,blocks){
 if(!exact(frame,'repository_sha256 policy_sha256 source_binding_sha256 task_type invocation_id')
  ||!['bug_fix','commit_inspection'].includes(frame.task_type)||!hex(frame.invocation_id,32)
  ||!['repository_sha256','policy_sha256','source_binding_sha256'].every(k=>hex(frame[k]))
  ||!Number.isInteger(request.request_budget)||request.request_budget<1||request.request_budget>MAX_REQUESTS)
  throw new Error('Invalid controlled memory route');
 if(!exact(blocks,'source structure experience knowledge maintenance')||Object.values(blocks).some(v=>v!=null&&
  (typeof v!=='object'||Array.isArray(v)||!hex(v.context_sha256))))throw new Error('Invalid memory channel envelope');
 const task=request.task_session?.binding.task_id??null;if(task!==null&&!hex(task,32))throw new Error('Invalid route task');
 const repo=frame.repository_sha256,files=Object.fromEntries(request.files.map(f=>[f.path,f.before_sha256??sha(f.content)]));
 const rows=[],sourceIds=new Set(),source=blocks.source;
 if(source){
  for(const row of source.hits){rows.push(document(row,repo,source.context_sha256));sourceIds.add(row.id);}
  if(source.decision_relations)for(const row of source.decision_relations.relations){
   for(const role of ['newer','older'])if(!sourceIds.has(row[role].id)){rows.push(document(row[role],repo,source.context_sha256));sourceIds.add(row[role].id);}
   rows.push(relation(row,source.decision_relations,repo,source.context_sha256));
  }
 }
 if(blocks.experience)for(const hit of blocks.experience.hits)rows.push(episode(hit,repo,blocks.experience.context_sha256,files));
 if(blocks.maintenance)for(const hit of blocks.maintenance.hits)rows.push(maintenance(hit,repo,blocks.maintenance.context_sha256));
 if(blocks.structure)for(const node of [...blocks.structure.hits,...blocks.structure.related_nodes])rows.push(structure(node,blocks.structure,repo,files));
 if(blocks.knowledge)for(const hit of blocks.knowledge.hits)rows.push(knowledge(hit,repo,blocks.knowledge.context_sha256,files));
 const items=finalize(rows),channels=Object.keys(blocks).sort().filter(k=>blocks[k]!=null).map(name=>({name,context_sha256:blocks[name].context_sha256}));
 const present=new Set(items.map(row=>row.adapter)),excluded=ADAPTERS.filter(a=>!present.has(a)).map(adapter=>({adapter,reason:'not_in_current_context'}));
 const body={schema_version:1,route_version:1,...frame,task_id:task,issue_sha256:sha(request.issue),
  planning_base_sha:request.base_sha,planning_files_sha256:sha(JSON.stringify(request.files)),channels,items,excluded,
  failure_policy:!channels.length?'degraded':excluded.length?'partial_inject':'hard_stop',required_source_integrity:'hard_stop',
  budget:{request_allowance:request.request_budget,extra_model_requests:0},runtime_authorized:false};
 if(items.length>160||Buffer.byteLength(JSON.stringify(body))>128000)throw new Error('Unified memory load exceeds bounded size');
 return {...body,context_sha256:experienceHash(body)};
}
export function verifiedMemoryLoad(value,request,blocks){
 if(value===undefined&&request.controller_binding===undefined)return null;
 if(!value||Buffer.byteLength(JSON.stringify(value))>128000)throw new Error('Invalid memory load receipt');
 const expected=buildMemoryLoad(request.controller_binding,request,blocks);
 if(experienceHash(value)!==experienceHash(expected))throw new Error('Memory load differs from independently verified contexts');
 return expected;
}
