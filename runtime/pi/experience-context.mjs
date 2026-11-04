// Strict historical episodes; no instructions, commands, paths to load, or permissions.
import {createHash} from 'node:crypto';
const sha=raw=>createHash('sha256').update(raw).digest('hex');
const ordered=v=>Array.isArray(v)?v.map(ordered):v&&typeof v==='object'
 ?Object.fromEntries(Object.keys(v).sort().map(k=>[k,ordered(v[k])])):v;
export const experienceHash=v=>sha(JSON.stringify(ordered(v)));
const keys=(v,n)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join()===n.split(' ').sort().join();
const hex=(v,n=64)=>typeof v==='string'&&new RegExp(`^[a-f0-9]{${n}}$`).test(v);
const text=(v,n)=>typeof v==='string'&&v.length>0&&Array.from(v).length<=n&&!/[\x00-\x1f]/.test(v);
export function verifiedExperience(value,request){
 if(value===undefined)return null;
 const fail=()=>{throw new Error('Invalid reviewed repository experience');};
 const version=value?.schema_version;
 if(!keys(value,'schema_version task_id repository_sha256 issue_sha256 planning_base_sha planning_files_sha256 classification runtime_authorized hits context_sha256'+(version===2?' selection_files_sha256':''))
  ||![1,2].includes(version)||(version===2&&!hex(value.selection_files_sha256))||!hex(value.task_id,32)||value.task_id!==request.task_session?.binding.task_id
  ||!hex(value.repository_sha256)||value.issue_sha256!==sha(request.issue)
  ||value.planning_base_sha!==request.base_sha||value.planning_files_sha256!==sha(JSON.stringify(request.files))
  ||value.classification!=='historical'||value.runtime_authorized!==false
  ||!Array.isArray(value.hits)||value.hits.length>3)fail();
 const paths=new Set(request.files.map(f=>f.path));const ids=new Set();
 for(const hit of value.hits){
  if(!keys(hit,'id payload_sha256 review_version episode'+(version===2?' applicability':''))||!hex(hit.id,32)||ids.has(hit.id)
   ||!hex(hit.payload_sha256)||!Number.isInteger(hit.review_version)||hit.review_version<1)fail();
  if(version===2&&!['matching_original_source','requires_revalidation'].includes(hit.applicability))fail();
  ids.add(hit.id);const e=hit.episode;
  if(!keys(e,'schema_version repository_sha256 run_id task_id classification runtime_authorized issue base_sha recorded_status outcome rounds policy_sha256 source_files evidence')
   ||![1,2,3,4].includes(e.schema_version)||e.repository_sha256!==value.repository_sha256||e.run_id!==hit.id||!hex(e.task_id,32)
   ||e.classification!=='historical'||e.runtime_authorized!==false||!text(e.issue,1000)||!hex(e.base_sha,40)
   ||!text(e.recorded_status,64)||!['candidate_test_passed','test_failed','not_verified'].includes(e.outcome)
   ||!hex(e.policy_sha256)||!keys(e.evidence,'run.json repair-execution.json report.md')
   ||!Object.values(e.evidence).every(v=>hex(v))||!e.source_files||Array.isArray(e.source_files)
   ||Object.keys(e.source_files).length!==paths.size
   ||!Object.entries(e.source_files).every(([p,h])=>paths.has(p)&&hex(h))
   ||!Array.isArray(e.rounds)||e.rounds.length>3||experienceHash(e)!==hit.payload_sha256)fail();
  if(version===2&&((hit.applicability==='matching_original_source')!==(experienceHash(e.source_files)===value.selection_files_sha256)))fail();
  for(const r of e.rounds){
   const compact=e.schema_version===4&&r.verification==='passed';
   if(!keys(r,'number plan_sha256 diagnosis verification paths test_exits test_timeouts'+(e.schema_version>=2?' change_excerpt '+(compact?'validation_summary':'failure_excerpt'):''))
    ||!Number.isInteger(r.number)||r.number<1||r.number>3||!hex(r.plan_sha256)||!text(r.diagnosis,500)
    ||!['passed','failed','not_run','pending'].includes(r.verification)||!Array.isArray(r.paths)
    ||r.paths.length>20||!r.paths.every(p=>paths.has(p))||!Array.isArray(r.test_exits)
    ||r.test_exits.length>10||!r.test_exits.every(Number.isInteger)||!Array.isArray(r.test_timeouts)
    ||r.test_timeouts.length!==r.test_exits.length||!r.test_timeouts.every(v=>typeof v==='boolean'))fail();
   if(e.schema_version>=2&&(!text(r.change_excerpt,1800)||(!compact&&!text(r.failure_excerpt,1600))))fail();
   if(compact){const s=r.validation_summary;
    if(!keys(s,'scope passed total semantic_correctness_proven')||s.scope!=='selected_commands'
     ||!Number.isInteger(s.passed)||!Number.isInteger(s.total)||s.total!==r.test_exits.length
     ||s.passed!==s.total||s.semantic_correctness_proven!==false)fail();
   }
   if(['passed','failed'].includes(r.verification)&&(!r.test_exits.length||
     ((r.verification==='passed')!==r.test_exits.every((code,i)=>code===0&&!r.test_timeouts[i]))))fail();
  }
  const last=e.rounds.at(-1)?.verification;
  if(e.schema_version===4&&last!=='passed')fail();
  if(e.outcome!==(last==='passed'?'candidate_test_passed':last==='failed'?'test_failed':'not_verified'))fail();
 }
 const {context_sha256,...body}=value;
 if(!hex(context_sha256)||experienceHash(body)!==context_sha256||Buffer.byteLength(JSON.stringify(value))>48000)fail();
 return value;
}
