import {createHash} from 'node:crypto';
import {experienceHash} from './experience-context.mjs';
const sha=s=>createHash('sha256').update(s).digest('hex');
const hex=(v,n=64)=>typeof v==='string'&&new RegExp(`^[a-f0-9]{${n}}$`).test(v);
export function verifiedReview(value,request){
  if(value===undefined)return null;
  const fields='schema_version task_id execution_id report_sha256 issue_sha256 base_sha previous_plan_sha256 reason claims_only runtime_authorized context_sha256'.split(' ').sort();
  if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join()!==fields.join()
    ||value.schema_version!==1||!hex(value.task_id,32)||!hex(value.execution_id,32)||!hex(value.report_sha256)
    ||value.task_id!==request.task_session?.binding.task_id||value.issue_sha256!==sha(request.issue)
    ||value.base_sha!==request.base_sha||!(value.previous_plan_sha256===null||hex(value.previous_plan_sha256))
    ||typeof value.reason!=='string'||!value.reason.trim()||value.reason!==value.reason.trim()
    ||Array.from(value.reason).length>2000||Buffer.byteLength(value.reason)>6000||/[\x00-\x08\x0b-\x1f]/.test(value.reason)
    ||value.claims_only!==true||value.runtime_authorized!==false)throw new Error('Invalid controlled review context');
  const {context_sha256,...body}=value;
  if(!hex(context_sha256)||experienceHash(body)!==context_sha256)throw new Error('Review context fingerprint differs');
  return value;
}
