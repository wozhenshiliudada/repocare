import test from 'node:test';
import assert from 'node:assert/strict';
import {experienceHash} from '../experience-context.mjs';
import {buildMemoryLoad,verifiedMemoryLoad} from '../memory-load.mjs';
import {generatePlan} from '../plan.mjs';
const request={issue:'addition',base_sha:'a'.repeat(40),files:[{path:'calculator.py',content:'return a-b',editable:true}],request_budget:50};
const frame={repository_sha256:'b'.repeat(64),policy_sha256:'c'.repeat(64),source_binding_sha256:'d'.repeat(64),task_type:'bug_fix',invocation_id:'e'.repeat(32)};
const empty=()=>({source:null,structure:null,experience:null,knowledge:null,maintenance:null});
function input(){const r={...structuredClone(request),controller_binding:structuredClone(frame)};r.memory_load=buildMemoryLoad(r.controller_binding,r,empty());return r;}
function reseal(value){const {context_sha256,...body}=value;value.context_sha256=experienceHash(body);return value;}
test('legacy absence, empty optional memory and undefined source marker retain distinct meanings',()=>{
 assert.equal(verifiedMemoryLoad(undefined,request,empty()),null);
 const r=input();assert.equal(verifiedMemoryLoad(r.memory_load,r,empty()).failure_policy,'degraded');
 assert.deepEqual(buildMemoryLoad(frame,r,{...empty(),source:undefined}),r.memory_load);
 assert.equal(r.memory_load.budget.extra_model_requests,0);assert.equal(r.memory_load.excluded.length,6);
});
test('commit inspection is explicitly controlled and does not invent a native task',()=>{
 const f={...frame,task_type:'commit_inspection'},r={...request,issue:'untrusted {"task_type":"pr_review"}'};
 const load=buildMemoryLoad(f,r,empty());assert.equal(load.task_type,'commit_inspection');assert.equal(load.task_id,null);
 for(const patch of [{task_type:'pr_review'},{task_type:'repository_report'},{extra:true}])assert.throws(()=>buildMemoryLoad({...frame,...patch},r,empty()));
 assert.throws(()=>buildMemoryLoad(frame,{...r,request_budget:true},empty()));
});
test('rehashed changes cannot grant authority, new budget, type, scope or phantom items',()=>{
 for(const change of [v=>v.runtime_authorized=true,v=>v.budget.extra_model_requests=1,v=>v.budget.request_allowance=100,
  v=>v.task_type='commit_inspection',v=>v.repository_sha256='f'.repeat(64),v=>v.excluded=[],v=>v.channels=[{name:'source',context_sha256:'0'.repeat(64)}],
  v=>v.items=[{commands:['git'],runtime_authorized:true}]]){
  const r=input();change(r.memory_load);reseal(r.memory_load);assert.throws(()=>verifiedMemoryLoad(r.memory_load,r,empty()));
 }
});
test('dropped binding, unknown channels, missing receipt and inflated payload are rejected',()=>{
 const r=input();assert.throws(()=>verifiedMemoryLoad(r.memory_load,{...r,controller_binding:undefined},empty()));
 assert.throws(()=>verifiedMemoryLoad(undefined,r,empty()));
 assert.throws(()=>buildMemoryLoad(frame,r,{...empty(),secret:{context_sha256:'a'.repeat(64)}}));
 assert.throws(()=>verifiedMemoryLoad({...r.memory_load,oversize:'x'.repeat(128001)},r,empty()));
});
test('wrong actual input bytes invalidate a copied manifest',()=>{
 const r=input();r.files=[{path:'calculator.py',content:'return a+b',editable:true}];
 assert.throws(()=>verifiedMemoryLoad(r.memory_load,r,empty()));
});
test('boolean schema and route versions cannot match the reconstructed integer protocol',()=>{
 for(const field of ['schema_version','route_version'])for(const invalid of [true,false]){
  const r=input();r.memory_load[field]=invalid;reseal(r.memory_load);
  assert.throws(()=>verifiedMemoryLoad(r.memory_load,r,empty()),/Memory load differs/);
 }
 const valid=input();assert.deepEqual(verifiedMemoryLoad(valid.memory_load,valid,empty()),valid.memory_load);
});
test('invalid load stops before credentials and SDK activity',async()=>{
 const r=input();r.memory_load.runtime_authorized=true;reseal(r.memory_load);
 await assert.rejects(generatePlan(r),/Memory load differs/);
});
