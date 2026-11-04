import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {startMock} from './mock-server.mjs';
import {python,project,removeOwnFixture} from './ui-case.mjs';
import {verifiedMemory} from '../source-memory.mjs';

for(const mode of ['approval','auto','failure'])test('real Pi UI memory: '+mode+' across restart with pinned scope and budget',
 {timeout:120000},async()=>{
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pi-ui-'));
  const mock=await startMock({scenario:mode==='auto'?'retry_fix':'fix',failure:mode==='failure'});
  let complete=false;
  try{
   const result=await promisify(execFile)(python,[path.join(project,'tests','pi_ui_memory.py'),
    '--directory',directory,'--url',mock.url,'--mode',mode],
    {cwd:project,windowsHide:true,timeout:100000,maxBuffer:2*1024*1024});
   const summary=JSON.parse(result.stdout);assert.equal(summary.status,'ui_memory_verified');
   assert.equal(summary.source_unchanged,true);assert.equal(mock.requests.length,{approval:2,auto:4,failure:1}[mode]);
   for(const body of mock.requests){
    assert.deepEqual(body.tools.map(t=>t.function.name).sort(),['propose_repair','read_source']);
    const user=body.messages.find(m=>m.role==='user');
    const context=JSON.parse(typeof user.content==='string'?user.content:user.content[0].text);
    const memory=verifiedMemory(context.source_memory,context.issue);
    assert.deepEqual(memory.hits.map(r=>r.id).sort(),['case','rule']);
    assert.equal(memory.hits.find(r=>r.id==='case').classification,'historical');
   }
   if(mode==='failure'){assert.equal(summary.unknown_reserved,4);assert.equal(summary.attempt_count,1);}
   if(mode==='auto')assert.deepEqual(summary.rounds,['failed','passed']);
   complete=true;
  }finally{await mock.close();if(complete)await removeOwnFixture(directory,os.tmpdir());}
 });
