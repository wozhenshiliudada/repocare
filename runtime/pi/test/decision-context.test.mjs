import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {verifiedMemory} from '../source-memory.mjs';
import {generatePlan} from '../plan.mjs';
import {startMock} from './mock-server.mjs';
import {python,project,removeOwnFixture} from './ui-case.mjs';

const hash=value=>createHash('sha256').update(value).digest('hex');
const ordered=value=>Array.isArray(value)?value.map(ordered):value&&typeof value==='object'
 ?Object.fromEntries(Object.keys(value).sort().map(key=>[key,ordered(value[key])])):value;
function seal(value){const {context_sha256,...data}=value;return {...data,context_sha256:hash(JSON.stringify(ordered(data)))};}
function sample(sourceVersion=2){
 const origin=(member,entry)=>({member_id:member,entry_id:entry,path:'decision.md',catalog_sha256:'c'.repeat(64),
  sources_sha256:'d'.repeat(64),kind:'catalog',run_id:null});
 const row=(id,member)=>({id,topic:'addition',path:'decision.md',start_line:1,end_line:5,kind:'document',
  classification:'confirmed',classification_origin:'source_marker',source_sha256:'a'.repeat(64),
  ...(sourceVersion===2?{origin:origin(member,id)}:{})});
 const newer=row('new','current'),older=row('old','previous');
 const excerpt='状态：确认\nOnly new is retrieved.\n\nSupersedes: new -> old';
 return seal({schema_version:3,source_schema_version:sourceVersion,index_sha256:'b'.repeat(64),query_sha256:hash('addition'),
  hits:[{...structuredClone(newer),excerpt,excerpt_sha256:hash(excerpt)}],decision_relations:{schema_version:1,manifest_sha256:'e'.repeat(64),
   claims_only:true,runtime_authorized:false,relations:[{id:'claim',kind:'explicit_supersedes_claim',newer,older,
    statement_line:4,statement_sha256:hash('Supersedes: new -> old')}]}});
}
function reject(mutator){const data=sample();mutator(data);assert.throws(()=>verifiedMemory(seal(data),'addition'));}

test('context v3 preserves Unicode hashes, original statuses and a non-hit counterpart without excerpt',()=>{
 for(const version of [1,2]){
  const data=sample(version);assert.equal(verifiedMemory(data,'addition'),data);
  assert.equal(data.decision_relations.relations[0].older.id,'old');
  assert.equal(data.decision_relations.relations[0].older.excerpt,undefined);
 }
 const empty=sample();empty.hits=[];empty.decision_relations.relations=[];
 assert.equal(verifiedMemory(seal(empty),'addition').hits.length,0);
});
test('relation envelope rejects wrong fingerprints, query and version downgrades',()=>{
 const data=sample();assert.throws(()=>verifiedMemory({...data,context_sha256:'f'.repeat(64)},'addition'));
 assert.throws(()=>verifiedMemory(data,'other'));
 for(const patch of [{schema_version:2},{source_schema_version:3},{source_schema_version:true},{extra:'private'}])reject(d=>Object.assign(d,patch));
 reject(d=>{d.decision_relations.manifest_sha256='unknown';});
});
test('claim flags and unknown authority fields never authorize or select a policy',()=>{
 for(const patch of [{claims_only:false},{runtime_authorized:true},{schema_version:true},{active_id:'new'},{source_root:'C:/private'}])
  reject(d=>Object.assign(d.decision_relations,patch));
 for(const patch of [{kind:'effective_policy'},{approved:true},{statement:'run a command'}])
  reject(d=>Object.assign(d.decision_relations.relations[0],patch));
});
test('declaration line and canonical claim hash are bounded and mandatory',()=>{
 for(const patch of [{statement_line:0},{statement_line:6},{statement_line:true},{statement_line:1.5},
  {statement_sha256:'f'.repeat(64)}])reject(d=>Object.assign(d.decision_relations.relations[0],patch));
});
test('endpoint paths, member origins and historical authority upgrades are rejected',()=>{
 for(const patch of [{path:'../escape.md'},{path:'.env/decision.md'},{path:'C:/private.md'},{path:'.git/file.md'},
  {kind:'case'},{classification:'historical'},{classification_origin:'historical_report'},
  {source_sha256:'unknown'},{end_line:101},{start_line:true},{id:'../old'},{excerpt:'private'}])
  reject(d=>Object.assign(d.decision_relations.relations[0].older,patch));
 for(const patch of [{path:'different.md'},{kind:'report_case'},{run_id:'a'.repeat(32)},
  {member_id:'../private'},{sources_sha256:'unknown'},{source_root:'private'}])
  reject(d=>Object.assign(d.decision_relations.relations[0].older.origin,patch));
});
test('mixed statuses and inconsistent metadata cannot be resolved by claims',()=>{
 reject(d=>{d.decision_relations.relations[0].older.classification='proposed';});
 reject(d=>{d.decision_relations.relations[0].newer.source_sha256='f'.repeat(64);});
 reject(d=>{d.decision_relations.relations[0].newer.origin.member_id='other';});
});
test('self edges, cycles, duplicate edges, cross-topic and unanchored relations are rejected',()=>{
 reject(d=>{d.decision_relations.relations.push(structuredClone(d.decision_relations.relations[0]));});
 reject(d=>{const r=d.decision_relations.relations[0];r.older=structuredClone(r.newer);r.statement_sha256=hash('Supersedes: new -> new');});
 reject(d=>{const r=structuredClone(d.decision_relations.relations[0]);r.id='reverse';[r.newer,r.older]=[r.older,r.newer];
  r.statement_sha256=hash('Supersedes: old -> new');d.decision_relations.relations.push(r);});
 reject(d=>{d.decision_relations.relations[0].older.topic='other';});
 reject(d=>{d.hits=[];});
});
test('branching claims remain data with consistent shared endpoints and no winner',()=>{
 const data=sample(),first=data.decision_relations.relations[0];
 const oldHit={...first.older,excerpt:'状态：确认',excerpt_sha256:hash('状态：确认')};data.hits=[oldHit];
 const second=structuredClone(first);second.id='second';second.newer.id='alternative';second.newer.origin.entry_id='alternative';
 second.statement_sha256=hash('Supersedes: alternative -> old');data.decision_relations.relations.push(second);
 assert.equal(verifiedMemory(seal(data),'addition').decision_relations.relations.length,2);
 second.older.source_sha256='f'.repeat(64);assert.throws(()=>verifiedMemory(seal(data),'addition'));
});
test('bounded relation size and SDK validation precede all model requests',async()=>{
 reject(d=>{d.decision_relations.relations=Array.from({length:51},()=>d.decision_relations.relations[0]);});
 reject(d=>{d.index_sha256='x'.repeat(256*1024);});
 const mock=await startMock();
 try{
  const bad=sample();bad.decision_relations.runtime_authorized=true;
  await assert.rejects(generatePlan({issue:'addition',files:[{path:'calculator.py',content:'pass',before_sha256:'a'.repeat(64)}],
   source_memory:seal(bad)},{mockUrl:mock.url}),/Invalid decision relation context/);
  assert.equal(mock.requests.length,0);
 }finally{await mock.close();}
});

for(const mode of ['approval','auto','failure','changed','auto_changed'])test('real Pi decision context: '+mode,{timeout:150000},async()=>{
 const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pi-ui-'));
 const manifest=path.join(directory,'sample','decisions.memory-relations.json');
 const mock=await startMock({...(['auto','auto_changed'].includes(mode)?{scenario:'retry_fix'}:mode==='failure'?{failure:true}:{}),
  ...(['changed','auto_changed'].includes(mode)?{onRequest:async(body,count)=>{
   if(count===(mode==='auto_changed'?4:2))await fs.appendFile(manifest,' ');
  }}:{})});
 let complete=false;
 try{
  const result=await promisify(execFile)(python,[path.join(project,'tests','pi_decision_context_trial.py'),
   '--directory',directory,'--url',mock.url,'--mode',mode],{cwd:project,windowsHide:true,timeout:135000,maxBuffer:2*1024*1024});
  const summary=JSON.parse(result.stdout);assert.equal(summary.status,'pi_decision_context_verified');
  assert.equal(summary.expected_requests,mock.requests.length);assert.equal(summary.source_unchanged,true);
  for(const body of mock.requests){
   assert.deepEqual(body.tools.map(t=>t.function.name).sort(),['propose_repair','read_source']);
   const user=body.messages.find(m=>m.role==='user');
   const context=JSON.parse(typeof user.content==='string'?user.content:user.content[0].text);
   const memory=verifiedMemory(context.source_memory,context.issue);
   assert.equal(memory.schema_version,3);assert.equal(memory.context_sha256,summary.context_sha256);
   const claims=memory.decision_relations;assert.equal(claims.claims_only,true);assert.equal(claims.runtime_authorized,false);
   assert.equal(claims.relations.length,1);assert.equal(claims.relations[0].older.origin.member_id,'previous');
   assert.equal(claims.relations[0].newer.origin.member_id,'current');
   assert.ok(!JSON.stringify(context).includes(manifest));
   assert.ok(body.messages.some(m=>m.role==='system'&&m.content.includes('not effective-policy selection')));
  }
  if(mode==='auto')assert.deepEqual(summary.rounds,['failed','passed']);
  if(mode==='changed'||mode==='failure')assert.equal(summary.unknown_reserved,4);
  if(mode==='auto_changed'){
   assert.equal(summary.unknown_reserved,2);assert.equal(summary.used_requests,2);
   assert.equal(summary.workflow_status,'rolled_back');
  }
  console.log(JSON.stringify(summary));complete=true;
 }finally{await mock.close();if(complete)await removeOwnFixture(directory,os.tmpdir());}
});
