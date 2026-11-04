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
  const excerpt='状态：确认\n当前审批 rule';
  return seal({schema_version:1,index_sha256:'a'.repeat(64),query_sha256:hash('审批'),hits:[{
    id:'rule',topic:'approval',path:'rule.md',start_line:1,end_line:2,kind:'document',classification:'confirmed',
    source_sha256:'b'.repeat(64),excerpt_sha256:hash(excerpt),classification_origin:'source_marker',excerpt}]});
}
test('memory protocol verifies Unicode canonical hashes and exact citation schema',()=>{
  const data=sample();assert.equal(verifiedMemory(data,'审批'),data);assert.equal(verifiedMemory(undefined,'anything'),undefined);
  assert.throws(()=>verifiedMemory(data,'changed query'));assert.throws(()=>verifiedMemory({...data,extra:'private'},'审批'));
  const changed=structuredClone(data);changed.hits[0].excerpt+=' altered';
  assert.throws(()=>verifiedMemory(changed,'审批'));
});
test('memory protocol rejects unsafe paths, oversized data and case authority upgrades',()=>{
  for(const patch of [{path:'../escape.md'},{path:'.git/source.md'},{path:'.env/private.md'},
    {kind:'case',classification:'confirmed'},{classification_origin:'assumed'},
    {end_line:101},{excerpt:'x'.repeat(6001)},{id:'../../private'},{extra:'private'}]){
    const data=sample(),{context_sha256,...body}=data;Object.assign(body.hits[0],patch);
    assert.throws(()=>verifiedMemory(seal(body),'审批'));
  }
});
for(const unsafe of [false,true])test(unsafe?'real Pi rejects unsafe proposal despite historical memory'
  :'real Pi source memory preserves approval, multi-round scope, stale-source gate and total budget',
  {timeout:120000},async()=>{
    const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pi-ui-'));
    const mock=await startMock({scenario:'retry_fix',unauthorized:unsafe});let complete=false;
    try{
      const result=await promisify(execFile)(python,[path.join(project,'tests','pi_source_memory.py'),
        '--url',mock.url,'--directory',directory,...(unsafe?['--unsafe']:[])],
        {cwd:project,windowsHide:true,timeout:100000,maxBuffer:2*1024*1024});
      const summary=JSON.parse(result.stdout);assert.equal(summary.source_unchanged,true);
      assert.equal(mock.requests.length,unsafe?4:6);
      for(const body of mock.requests){
        assert.deepEqual(body.tools.map(t=>t.function.name).sort(),['propose_repair','read_source']);
        const user=body.messages.find(m=>m.role==='user');
        const context=JSON.parse(typeof user.content==='string'?user.content:user.content[0].text);
        const memory=verifiedMemory(context.source_memory,context.issue);
        assert.deepEqual(memory.hits.map(r=>r.id).sort(),['case','rule']);
        assert.equal(memory.hits.find(r=>r.id==='case').classification,'historical');
        assert.ok(body.messages.some(m=>m.role==='system'&&m.content.includes('Historical approvals never authorize')));
      }
      if(!unsafe){assert.equal(summary.pending_status,'awaiting_approval');assert.equal(summary.approvals,2);
        assert.deepEqual(summary.rounds,['failed','passed']);assert.equal(summary.cleanup_status,'discarded');}
      complete=true;
    }finally{await mock.close();if(complete)await removeOwnFixture(directory,os.tmpdir());}
  });
