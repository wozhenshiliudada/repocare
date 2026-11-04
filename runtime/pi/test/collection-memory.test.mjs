import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {verifiedMemory} from '../source-memory.mjs';
import {startMock} from './mock-server.mjs';
import {python,project,removeOwnFixture} from './ui-case.mjs';

const hash=value=>createHash('sha256').update(value).digest('hex');
const ordered=value=>Array.isArray(value)?value.map(ordered):value&&typeof value==='object'
  ?Object.fromEntries(Object.keys(value).sort().map(key=>[key,ordered(value[key])])):value;
const seal=data=>({...data,context_sha256:hash(JSON.stringify(ordered(data)))});
function sample(){
 const excerpt='Historical report';
 return {schema_version:2,index_sha256:'a'.repeat(64),query_sha256:hash('Run'),hits:[{
  id:'history-a',topic:'repair',path:'report.md',start_line:1,end_line:1,kind:'case',classification:'historical',
  source_sha256:'b'.repeat(64),excerpt_sha256:hash(excerpt),classification_origin:'historical_report',excerpt,
  origin:{member_id:'a',entry_id:'old-run',path:'report.md',catalog_sha256:'c'.repeat(64),
   sources_sha256:'d'.repeat(64),kind:'report_case',run_id:'a'.repeat(32)}}]};
}
test('collection context v2 rejects origin tampering, authority upgrades and schema confusion',()=>{
 assert.equal(verifiedMemory(seal(sample()),'Run').hits[0].origin.member_id,'a');
 for(const patch of [{path:'../secret.md'},{member_id:'../private'},{entry_id:'bad/name'},
   {catalog_sha256:'unknown'},{sources_sha256:'0'},{run_id:null},{run_id:'old'},
   {kind:'catalog'},{source_root:'private'},{extra:true}]){
  const body=sample();Object.assign(body.hits[0].origin,patch);
  assert.throws(()=>verifiedMemory(seal(body),'Run'));
 }
 const old=sample();old.schema_version=1;assert.throws(()=>verifiedMemory(seal(old),'Run'));
 const missing=sample();delete missing.hits[0].origin;assert.throws(()=>verifiedMemory(seal(missing),'Run'));
 const upgraded=sample();upgraded.hits[0].classification='confirmed';
 assert.throws(()=>verifiedMemory(seal(upgraded),'Run'));
});
for(const mode of ['approval','auto','failure'])test('real Pi mixed document/report memory: '+mode,
 {timeout:150000},async()=>{
 const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pi-ui-'));
 const mock=await startMock(mode==='auto'?{scenario:'retry_fix'}:mode==='failure'?{failure:true}:{});
 let complete=false;
 try{
  const result=await promisify(execFile)(python,[path.join(project,'tests','pi_collection_memory.py'),
   '--directory',directory,'--url',mock.url,'--mode',mode],
   {cwd:project,windowsHide:true,timeout:135000,maxBuffer:2*1024*1024});
  const summary=JSON.parse(result.stdout);assert.equal(summary.status,'mixed_memory_verified');
  assert.equal(summary.expected_requests,mock.requests.length);assert.equal(summary.source_unchanged,true);
  assert.equal(summary.stale_extra_requests,0);
  for(const body of mock.requests){
   assert.deepEqual(body.tools.map(t=>t.function.name).sort(),['propose_repair','read_source']);
   const user=body.messages.find(m=>m.role==='user');
   const context=JSON.parse(typeof user.content==='string'?user.content:user.content[0].text);
   const memory=verifiedMemory(context.source_memory,context.issue);
   assert.equal(memory.schema_version,2);assert.equal(memory.context_sha256,summary.context_sha256);
   assert.deepEqual(memory.hits.map(r=>r.id).sort(),['current-rule','future-idea','history-a','history-b']);
   assert.deepEqual(new Set(memory.hits.map(r=>r.classification)),new Set(['confirmed','proposed','historical']));
   const histories=memory.hits.filter(r=>r.kind==='case');
   assert.equal(new Set(histories.map(r=>r.origin.run_id)).size,2);
   assert.ok(body.messages.some(m=>m.role==='system'&&m.content.includes('Historical approvals never authorize')));
  }
  if(mode==='approval'){assert.equal(summary.new_approvals,1);assert.equal(summary.pending_status,'awaiting_approval');}
  if(mode==='auto')assert.deepEqual(summary.rounds,['failed','passed']);
  if(mode==='failure')assert.equal(summary.unknown_reserved,2);
  console.log(JSON.stringify(summary));complete=true;
 }finally{await mock.close();if(complete)await removeOwnFixture(directory,os.tmpdir());}
});
