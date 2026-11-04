import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {startMock} from './mock-server.mjs';
import {startUI, operation, git, removeOwnFixture, python, project} from './ui-case.mjs';

test('real Pi legacy import stays outside UI registrations, usage and execution rights', {timeout:120000}, async()=>{
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pi-ui-'));
  const mock=await startMock();let ui,complete=false;
  const command=async(...args)=>promisify(execFile)(python,[path.join(project,'maintain.py'),...args],
    {cwd:project,windowsHide:true,timeout:30000});
  const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
  try{
    ui=await startUI(directory,{mockUrl:mock.url});
    const head=await git(directory,'rev-parse','HEAD');
    await operation(ui,'plan',{issue:'addition bug'});
    let state=await ui.api('state');const plan=state.plans[0];
    await operation(ui,'decision',{plan_id:plan.id,fingerprint:plan.fingerprint,decision:'approve'});
    state=await ui.api('state');const runId=state.runs[0].run_id;
    const runs=path.join(directory,'runs'),source=path.join(directory,'old-records',runId);
    await fs.mkdir(source,{recursive:true});
    for(const name of ['run.json','report.md'])await fs.copyFile(path.join(runs,runId,name),path.join(source,name));
    const db=path.join(runs,'ui-state','registry.sqlite3'),before=hash(await fs.readFile(db));
    const result=JSON.parse((await command('import-legacy-run','--run-dir',source,'--archive-root',runs)).stdout);
    assert.equal(result.runtime_authorized,false);assert.equal(result.read_only,true);
    assert.equal(result.budget,'unknown_not_imported');assert.equal(result.record_state,'historical_run_record');
    assert.equal(hash(await fs.readFile(db)),before);assert.equal(mock.requests.length,2);
    const checked=JSON.parse((await command('verify-legacy-run','--archive-root',runs,'--archive-id',result.archive_id,
      '--source-run-dir',source)).stdout);
    assert.equal(checked.source_state,'same_evidence');
    assert.equal(hash(await fs.readFile(db)),before);
    state=await ui.api('state');assert.equal(state.runs.length,1);
    assert.equal(state.requests_used,2);assert.equal(state.requests_remaining,0);
    await assert.rejects(operation(ui,'claim_run',{run_id:result.archive_id,revision:'a'.repeat(64)}));
    assert.equal((await ui.api('state')).runs.length,1);
    await ui.stop();ui=await startUI(directory,{mockUrl:mock.url,restart:true});
    state=await ui.api('state');assert.equal(state.runs.length,1);assert.equal(state.runs[0].claimed,false);
    assert.equal(state.plans[0].status,'consumed');assert.equal(state.requests_used,2);assert.equal(state.requests_remaining,0);
    assert.equal(mock.requests.length,2);assert.equal(await git(directory,'rev-parse','HEAD'),head);
    const run=state.runs[0];await operation(ui,'claim_run',{run_id:runId,revision:run.revision});
    await operation(ui,'cleanup',{run_id:runId,revision:(await ui.api('state')).runs[0].revision});
    complete=true;
  }finally{
    if(ui)await ui.stop();await mock.close();
    if(complete)await removeOwnFixture(directory,os.tmpdir());
  }
});
