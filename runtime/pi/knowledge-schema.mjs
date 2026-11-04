import {experienceHash,verifiedExperience} from './experience-context.mjs';
export const exact=(v,n)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join()===n.split(' ').sort().join();
export const hex=(v,n=64)=>typeof v==='string'&&new RegExp(`^[a-f0-9]{${n}}$`).test(v);
const text=(v,n)=>typeof v==='string'&&v.trim()&&Array.from(v).length<=n&&!/[\x00-\x1f]/.test(v);
export function verifiedNote(v){
 const fail=()=>{throw new Error('Invalid proposed knowledge data');};
 if(!exact(v,'kind title summary triggers preconditions steps verification stop_conditions')
  ||!['knowledge','procedure'].includes(v.kind)||!text(v.title,160)||!text(v.summary,1200))fail();
 for(const key of ['triggers','preconditions','steps','verification','stop_conditions'])
  if(!Array.isArray(v[key])||v[key].length<1||v[key].length>8||!v[key].every(s=>text(s,500)))fail();
 if(Buffer.byteLength(JSON.stringify(v))>10000)fail();return v;
}
export function verifiedReflection(v){
 if(![1,2].includes(v?.schema_version)||!exact(v,'schema_version repository_sha256 policy_sha256 source_files hits context_sha256'+(v.schema_version===2?' previous':''))
  ||!hex(v.repository_sha256)||!hex(v.policy_sha256)||!v.source_files||Array.isArray(v.source_files)
  ||!Object.keys(v.source_files).length||Object.keys(v.source_files).length>20||!Object.values(v.source_files).every(h=>hex(h))
  ||!Array.isArray(v.hits)||v.hits.length<1||v.hits.length>3||new Set(v.hits.map(h=>h.id)).size!==v.hits.length
  ||Buffer.byteLength(JSON.stringify(v))>48000)throw new Error('Invalid reflection source');
 if(v.schema_version===2){const p=v.previous;
  if(!exact(p,'id payload_sha256 source_sha256 note review review_version classification runtime_authorized')
   ||!hex(p.id,32)||!hex(p.payload_sha256)||!hex(p.source_sha256)||p.classification!=='proposed'||p.runtime_authorized!==false
   ||!['pending','accepted','rejected'].includes(p.review)||!Number.isInteger(p.review_version)||p.review_version<1)
   throw new Error('Invalid correction parent reference');
  verifiedNote(p.note);
 }
 for(const h of v.hits){
  if(!exact(h,'id payload_sha256 review_version episode')||!hex(h.id,32)||!hex(h.payload_sha256)
   ||!Number.isInteger(h.review_version)||h.review_version<1||h.episode?.run_id!==h.id
   ||h.episode?.repository_sha256!==v.repository_sha256||h.episode?.classification!=='historical'
   ||h.episode?.runtime_authorized!==false||experienceHash(h.episode)!==h.payload_sha256)
   throw new Error('Invalid reflection episode');
  // Reuse the complete existing episode parser without exposing any planning task or path.
  // This local parser envelope is discarded; it is not a controller checkpoint or model receipt.
  const request={issue:h.episode.issue,base_sha:h.episode.base_sha,
   files:Object.keys(v.source_files).map(path=>({path})),task_session:{binding:{task_id:h.episode.task_id}}};
  const body={schema_version:1,task_id:h.episode.task_id,repository_sha256:v.repository_sha256,
   issue_sha256:hashText(request.issue),planning_base_sha:request.base_sha,
   planning_files_sha256:hashText(JSON.stringify(request.files)),classification:'historical',runtime_authorized:false,hits:[h]};
  verifiedExperience({...body,context_sha256:experienceHash(body)},request);
 }
 const {context_sha256,...body}=v;
 if(!hex(context_sha256)||experienceHash(body)!==context_sha256)throw new Error('Reflection receipt differs');
 return v;
}
import {createHash} from 'node:crypto';
const hashText=v=>createHash('sha256').update(v).digest('hex');
