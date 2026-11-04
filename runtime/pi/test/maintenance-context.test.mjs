import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {experienceHash} from '../experience-context.mjs';
import {verifiedMaintenance} from '../maintenance-context.mjs';
import {generatePlan} from '../plan.mjs';
const sha=v=>createHash('sha256').update(v).digest('hex');
const request={issue:'signed addition',base_sha:'a'.repeat(40),task_session:{binding:{task_id:'b'.repeat(32)}},
 files:[{path:'calculator.py',content:'return a-b',before_sha256:sha('return a-b'),editable:true}]};
function sample(){
 const body={kind:'note',title:'Signed addition',detail:'Independently check negative operands.',stage:'open',proof:'user_declaration',
  task_id:null,run_id:null,plan_id:null,rounds:null,test_exits:[],priority:2};
 const hit={id:'note:'+ 'c'.repeat(32),body,body_sha256:experienceHash(body),
  source_ref:{kind:'note',id:'c'.repeat(32),sha256:'d'.repeat(64)},classification:'proposed'};
 const packet={schema_version:1,task_id:request.task_session.binding.task_id,repository_sha256:'e'.repeat(64),
  issue_sha256:sha(request.issue),planning_base_sha:request.base_sha,planning_files_sha256:sha(JSON.stringify(request.files)),
  selection_files_sha256:'f'.repeat(64),selection_sha256:'1'.repeat(64),runtime_authorized:false,hits:[hit]};
 return {...packet,context_sha256:experienceHash(packet)};
}
function rehash(value){for(const row of value.hits)row.body_sha256=experienceHash(row.body);
 const {context_sha256,...body}=value;value.context_sha256=experienceHash(body);return value;}
function rejected(change){const value=sample();change(value);assert.throws(()=>verifiedMaintenance(rehash(value),request));}

test('maintenance context is bounded reference data, legacy absence remains absent',()=>{
 const value=sample();assert.deepEqual(verifiedMaintenance(value,request),value);assert.equal(verifiedMaintenance(undefined,request),null);
});
test('human closure or hash recomputation cannot certify tests, commands or execution',()=>{
 rejected(v=>v.runtime_authorized=true);rejected(v=>v.hits[0].body.proof='actual_test');
 rejected(v=>v.hits[0].body.stage='fixed');rejected(v=>v.hits[0].body.commands=['git']);
 rejected(v=>v.hits[0].classification='confirmed');rejected(v=>v.hits[0].body.test_exits=[0]);
 rejected(v=>v.hits[0].body.priority=true);
});
test('wrong task issue source bytes receipt identity or duplicate hit is rejected',()=>{
 rejected(v=>v.task_id='a'.repeat(32));rejected(v=>v.issue_sha256='a'.repeat(64));
 rejected(v=>v.planning_files_sha256='b'.repeat(64));rejected(v=>delete v.selection_sha256);
 rejected(v=>v.hits[0].source_ref.id='a'.repeat(32));rejected(v=>v.hits.push(structuredClone(v.hits[0])));
 rejected(v=>v.hits[0].body.detail='control\u0000character');
});
test('original run observations cannot masquerade as declared notes or arbitrary tools',()=>{
 const value=sample(),row=value.hits[0],identity='2'.repeat(32);
 row.id='run:'+identity;row.source_ref={kind:'run',id:identity,sha256:'3'.repeat(64)};row.classification='historical';
 Object.assign(row.body,{kind:'run',stage:'test_failed',proof:'actual_test',task_id:'4'.repeat(32),run_id:identity,rounds:1,test_exits:[1]});
 assert.equal(verifiedMaintenance(rehash(value),request).hits[0].body.stage,'test_failed');
 row.body.tool='shell';assert.throws(()=>verifiedMaintenance(rehash(value),request));
});
test('malformed maintenance input stops before credentials or SDK activity',async()=>{
 const value=sample();value.runtime_authorized=true;
 await assert.rejects(generatePlan({...request,repository_maintenance:rehash(value)}),/maintenance context/);
});
test('registered checkpoint remains metadata and cannot be promoted into tested evidence',()=>{
 const value=sample(),row=value.hits[0],identity='5'.repeat(32);
 row.id='checkpoint:'+identity;row.source_ref={kind:'checkpoint',id:identity,sha256:'6'.repeat(64)};row.classification='historical';
 Object.assign(row.body,{kind:'checkpoint',stage:'registered_checkpoint',proof:'registration',task_id:identity,rounds:1});
 assert.equal(verifiedMaintenance(rehash(value),request).hits[0].body.rounds,1);
 row.body.proof='actual_test';assert.throws(()=>verifiedMaintenance(rehash(value),request));
});
