import {createHash} from 'node:crypto';
import {experienceHash} from './experience-context.mjs';
import {exact,hex,verifiedNote} from './knowledge-schema.mjs';
const sha=v=>createHash('sha256').update(v).digest('hex');
export function verifiedKnowledge(v,request){
 if(v===undefined)return null;
 const fail=()=>{throw new Error('Invalid reviewed knowledge context');};
 if(!exact(v,'schema_version task_id repository_sha256 issue_sha256 planning_base_sha planning_files_sha256 selection_files_sha256 classification runtime_authorized hits context_sha256')
  ||v.schema_version!==1||v.task_id!==request.task_session?.binding.task_id||!hex(v.task_id,32)||!hex(v.repository_sha256)
  ||v.issue_sha256!==sha(request.issue)||v.planning_base_sha!==request.base_sha||v.planning_files_sha256!==sha(JSON.stringify(request.files))
  ||!hex(v.selection_files_sha256)||v.classification!=='proposed'||v.runtime_authorized!==false
  ||!Array.isArray(v.hits)||v.hits.length>3||new Set(v.hits.map(h=>h.id)).size!==v.hits.length
  ||Buffer.byteLength(JSON.stringify(v))>40000)fail();
 const paths=new Set(request.files.map(f=>f.path));
 for(const hit of v.hits){
  if(!exact(hit,'id payload_sha256 review_version knowledge applicability')||!hex(hit.id,32)||!hex(hit.payload_sha256)
   ||!Number.isInteger(hit.review_version)||hit.review_version<1
   ||!['matching_original_source','requires_revalidation'].includes(hit.applicability))fail();
  const p=hit.knowledge;
  if(!exact(p,'schema_version repository_sha256 classification runtime_authorized note source_sha256 refs source_files policy_sha256 parent')
   ||p.schema_version!==1||p.repository_sha256!==v.repository_sha256||p.classification!=='proposed'||p.runtime_authorized!==false
   ||!hex(p.source_sha256)||!hex(p.policy_sha256)||(p.parent!==null&&!hex(p.parent,32))||!p.source_files||Array.isArray(p.source_files)
   ||Object.keys(p.source_files).length!==paths.size||!Object.entries(p.source_files).every(([path,h])=>paths.has(path)&&hex(h))
   ||experienceHash(p)!==hit.payload_sha256||!Array.isArray(p.refs)||p.refs.length<1||p.refs.length>3
   ||new Set(p.refs.map(r=>r.id)).size!==p.refs.length)fail();
  verifiedNote(p.note);
  for(const ref of p.refs)if(!exact(ref,'id payload_sha256 review_version')||!hex(ref.id,32)||!hex(ref.payload_sha256)
    ||!Number.isInteger(ref.review_version)||ref.review_version<1)fail();
  if((hit.applicability==='matching_original_source')!==(experienceHash(p.source_files)===v.selection_files_sha256))fail();
 }
 const {context_sha256,...body}=v;
 if(!hex(context_sha256)||experienceHash(body)!==context_sha256)fail();return v;
}
