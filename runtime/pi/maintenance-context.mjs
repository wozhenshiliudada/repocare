// Bounded journal references. Human priorities and original observations never grant authority.
import {createHash} from 'node:crypto';
import {experienceHash} from './experience-context.mjs';
const sha=value=>createHash('sha256').update(value).digest('hex');
const keys=(v,n)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join()===n.split(' ').sort().join();
const hex=(v,n=64)=>typeof v==='string'&&new RegExp(`^[a-f0-9]{${n}}$`).test(v);
const text=(v,n)=>typeof v==='string'&&Array.from(v).length>0&&Array.from(v).length<=n&&!/[\x00-\x1f]/.test(v);
export function verifiedMaintenance(value,request){
 if(value===undefined)return null;
 const fail=()=>{throw new Error('Invalid original repository maintenance context');};
 if(!keys(value,'schema_version task_id repository_sha256 issue_sha256 planning_base_sha planning_files_sha256 selection_files_sha256 selection_sha256 runtime_authorized hits context_sha256')
  ||value.schema_version!==1||!hex(value.task_id,32)||value.task_id!==request.task_session?.binding.task_id
  ||!hex(value.repository_sha256)||value.issue_sha256!==sha(request.issue)||value.planning_base_sha!==request.base_sha
  ||value.planning_files_sha256!==sha(JSON.stringify(request.files))||!hex(value.selection_files_sha256)||!hex(value.selection_sha256)
  ||value.runtime_authorized!==false||!Array.isArray(value.hits)||value.hits.length>5||Buffer.byteLength(JSON.stringify(value))>20000)fail();
 const identities=new Set(),stages={note:['open','deferred','closed_by_user'],plan:['awaiting_approval','consumed'],
  run:['candidate_test_passed','test_failed','not_verified'],checkpoint:['registered_checkpoint']};
 for(const row of value.hits){
  if(!keys(row,'id body body_sha256 source_ref classification')||!keys(row.source_ref,'kind id sha256'))fail();
  const ref=row.source_ref,b=row.body;
  if(!Object.hasOwn(stages,ref.kind)||!hex(ref.id,32)||!hex(ref.sha256)||row.id!==ref.kind+':'+ref.id||identities.has(row.id)
   ||row.classification!==(ref.kind==='note'?'proposed':'historical')||experienceHash(b)!==row.body_sha256
   ||!keys(b,'kind title detail stage proof task_id run_id plan_id rounds test_exits priority')||b.kind!==ref.kind
   ||!text(b.title,1000)||!text(b.detail,1200)||!stages[ref.kind].includes(b.stage)
   ||!['user_declaration','actual_test','registration'].includes(b.proof)||!Number.isInteger(b.priority)||b.priority<0||b.priority>3
   ||!Array.isArray(b.test_exits)||b.test_exits.length>10||!b.test_exits.every(Number.isInteger)
   ||!['task_id','run_id','plan_id'].every(k=>b[k]===null||hex(b[k],32))
   ||!(b.rounds===null||Number.isInteger(b.rounds)&&b.rounds>=0&&b.rounds<=100))fail();
  if(ref.kind==='note'&&(b.proof!=='user_declaration'||b.task_id!==null||b.run_id!==null||b.plan_id!==null||b.rounds!==null||b.test_exits.length))fail();
  if(ref.kind==='plan'&&(b.proof!=='registration'||b.plan_id!==ref.id||!hex(b.task_id,32)||b.run_id!==null||b.test_exits.length))fail();
  if(ref.kind==='checkpoint'&&(b.proof!=='registration'||b.task_id!==ref.id||!Number.isInteger(b.rounds)||b.rounds<1||b.run_id!==null||b.plan_id!==null||b.test_exits.length))fail();
  if(ref.kind==='run'&&(b.run_id!==ref.id||!hex(b.task_id,32)||!Number.isInteger(b.rounds)||b.rounds>3
    ||b.plan_id!==null||b.proof==='user_declaration'||(b.proof==='actual_test'&&!b.test_exits.length)))fail();
  identities.add(row.id);
 }
 const {context_sha256,...body}=value;if(experienceHash(body)!==context_sha256)fail();
 return value;
}
