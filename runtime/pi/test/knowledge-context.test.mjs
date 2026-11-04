import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {experienceHash} from '../experience-context.mjs';
import {verifiedKnowledge} from '../knowledge-context.mjs';
import {verifiedNote,verifiedReflection} from '../knowledge-schema.mjs';
const sha=v=>createHash('sha256').update(v).digest('hex');
const note={kind:'procedure',title:'Negative addition',summary:'A candidate failed mixed operands.',triggers:['Addition failed'],
 preconditions:['Read current code'],steps:['Compare operands'],verification:['Run current authorized tests'],stop_conditions:['Stop when evidence differs']};
const request={issue:'addition',base_sha:'a'.repeat(40),files:[{path:'calculator.py',content:'a-b'}],task_session:{binding:{task_id:'b'.repeat(32)}}};
const seal=value=>{const {context_sha256,...body}=value;return {...body,context_sha256:experienceHash(body)};};
function sample(){
 const knowledge={schema_version:1,repository_sha256:'c'.repeat(64),classification:'proposed',runtime_authorized:false,
  note:structuredClone(note),source_sha256:'d'.repeat(64),refs:[{id:'e'.repeat(32),payload_sha256:'f'.repeat(64),review_version:2}],
  source_files:{'calculator.py':'f'.repeat(64)},policy_sha256:'a'.repeat(64),parent:null};
 return seal({schema_version:1,task_id:request.task_session.binding.task_id,repository_sha256:knowledge.repository_sha256,
  issue_sha256:sha(request.issue),planning_base_sha:request.base_sha,planning_files_sha256:sha(JSON.stringify(request.files)),
  selection_files_sha256:experienceHash(knowledge.source_files),classification:'proposed',runtime_authorized:false,
  hits:[{id:'d'.repeat(32),payload_sha256:experienceHash(knowledge),review_version:2,knowledge,applicability:'matching_original_source'}]});
}
test('accepted proposed knowledge is validated with exact context receipt',()=>{
 assert.equal(verifiedKnowledge(undefined,request),null);assert.deepEqual(verifiedKnowledge(sample(),request),sample());
 assert.deepEqual(verifiedNote(note),note);
});
test('proposed note cannot add commands authority confirmed status or malformed fields',()=>{
 for(const alter of [p=>p.classification='confirmed',p=>p.runtime_authorized=true,p=>p.commands=['git'],
  p=>p.note.steps=[],p=>p.note.steps.push('x'.repeat(501)),p=>p.note.title+='\n',p=>p.source_files['.env']='a'.repeat(64)]){
  const value=sample();alter(value.hits[0].knowledge);value.hits[0].payload_sha256=experienceHash(value.hits[0].knowledge);
  assert.throws(()=>verifiedKnowledge(seal(value),request));
 }
});
test('wrong task issue baseline source snapshot receipt duplicate or revalidation flag rejected',()=>{
 for(const alter of [v=>v.task_id='a'.repeat(32),v=>v.issue_sha256='f'.repeat(64),v=>v.planning_base_sha='b'.repeat(40),
  v=>v.planning_files_sha256='f'.repeat(64),v=>v.hits.push(v.hits[0]),v=>v.hits[0].payload_sha256='b'.repeat(64),
  v=>v.hits[0].applicability='requires_revalidation',v=>v.hits[0].knowledge.refs[0].review_version=0]){
  const value=sample();alter(value);assert.throws(()=>verifiedKnowledge(seal(value),request));
 }
 const value=sample();value.selection_files_sha256='a'.repeat(64);value.hits[0].applicability='requires_revalidation';
 assert.deepEqual(verifiedKnowledge(seal(value),request),seal(value));
});
test('reflection rejects unknown fields source hash forged episode or too many sources',()=>{
 const source=seal({schema_version:1,repository_sha256:'c'.repeat(64),policy_sha256:'a'.repeat(64),
  source_files:{'calculator.py':'a'.repeat(64)},hits:[{id:'d'.repeat(32),review_version:2,payload_sha256:'a'.repeat(64),episode:{}}]});
 for(const value of [source,{...source,commands:['git']},{...source,hits:[]}])assert.throws(()=>verifiedReflection(value));
});
