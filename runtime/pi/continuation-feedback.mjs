// Controller-provided diagnostics; never a tool, scope, approval or test-success proof.
import {createHash} from 'node:crypto';
const sha=value=>createHash('sha256').update(value).digest('hex');
export function verifiedContinuation(value, request) {
  if(value===undefined)return null;
  const hex=(v,n)=>typeof v==='string'&&new RegExp(`^[0-9a-f]{${n}}$`).test(v);
  if(!value||value.schema_version!==1||!request.task_session||
     value.task_id!==request.task_session.binding.task_id||!hex(value.run_id,32)||
     value.base_sha!==request.base_sha||value.issue_sha256!==sha(request.issue)||
     !hex(value.previous_plan_sha256,64)||!Number.isInteger(value.planning_revision)||value.planning_revision<1||
     !Array.isArray(value.tests)||!value.tests.length||value.tests.length>5||
     !value.tests.some(t=>Number.isInteger(t.returncode)&&(t.returncode!==0||t.timed_out))||
     value.tests.some(t=>!Array.isArray(t.command)||!t.command.every(a=>typeof a==='string')||
       !Number.isInteger(t.returncode)||typeof t.timed_out!=='boolean'||typeof t.stdout!=='string'||typeof t.stderr!=='string')||
     !value.proposed_content_hashes||Object.entries(value.proposed_content_hashes).some(([p,h])=>
       !request.files.some(f=>f.path===p&&f.editable!==false)||!hex(h,64))||
     Buffer.byteLength(JSON.stringify(value))>48000)throw new Error('Invalid controlled continuation feedback');
  return value;
}
