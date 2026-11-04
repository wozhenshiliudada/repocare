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

test('actual workflow report becomes historical Pi context without approval or budget inheritance',
  {timeout:120000},async()=>{
    const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pi-ui-'));
    const mock=await startMock();let complete=false;
    try{
      const result=await promisify(execFile)(python,[path.join(project,'tests','pi_report_memory.py'),
        '--url',mock.url,'--directory',directory],
        {cwd:project,windowsHide:true,timeout:105000,maxBuffer:2*1024*1024});
      const summary=JSON.parse(result.stdout);
      assert.equal(summary.pending_status,'awaiting_approval');assert.equal(summary.new_approvals,1);
      assert.equal(summary.source_unchanged,true);assert.equal(summary.stale_extra_requests,0);
      assert.equal(summary.exhausted_extra_requests,0);assert.equal(mock.requests.length,4);
      assert.equal(summary.cleanup_status,'discarded');
      for(const body of mock.requests){
        assert.deepEqual(body.tools.map(t=>t.function.name).sort(),['propose_repair','read_source']);
        const user=body.messages.find(m=>m.role==='user');
        const context=JSON.parse(typeof user.content==='string'?user.content:user.content[0].text);
        const memory=verifiedMemory(context.source_memory,context.issue);
        assert.equal(memory.context_sha256,summary.context_sha256);
        assert.equal(memory.hits.length,1);assert.equal(memory.hits[0].id,'prior-run');
        assert.equal(memory.hits[0].path,'report.md');assert.equal(memory.hits[0].classification,'historical');
        assert.ok(body.messages.some(m=>m.role==='system'&&m.content.includes('Historical approvals never authorize')));
      }
      console.log(JSON.stringify(summary));complete=true;
    }finally{await mock.close();if(complete)await removeOwnFixture(directory,os.tmpdir());}
  });
