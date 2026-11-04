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

test('real Pi history archive preserves consumed plan and usage, no restore or replay', {timeout:120000}, async()=>{
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pi-ui-'));
  const mock=await startMock();let ui,complete=false;
  const command=async(...args)=>promisify(execFile)(python,[path.join(project,'maintain.py'),...args],{cwd:project,windowsHide:true,timeout:30000});
  const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
  try{
    ui=await startUI(directory,{mockUrl:mock.url});
    const head=await git(directory,'rev-parse','HEAD');
    await operation(ui,'plan',{issue:'addition bug'});
    const plan=(await ui.api('state')).plans[0];
    await operation(ui,'decision',{plan_id:plan.id,fingerprint:plan.fingerprint,decision:'approve'});
    let state=await ui.api('state');const runId=state.runs[0].run_id;
    assert.equal(state.requests_used,2);assert.equal(mock.requests.length,2);
    const runs=path.join(directory,'runs');
    await assert.rejects(command('backup-ui-history','--runs-dir',runs),error=>error.code===1&&/UI server is active/.test(error.stderr));
    await ui.stop();
    const db=path.join(runs,'ui-state','registry.sqlite3');
    const before=hash(await fs.readFile(db));
    const originalRun=await fs.readFile(path.join(runs,runId,'run.json'));
    const result=JSON.parse((await command('backup-ui-history','--runs-dir',runs)).stdout);
    const verified=JSON.parse((await command('verify-ui-backup','--runs-dir',runs,'--backup-id',result.backup_id)).stdout);
    assert.deepEqual(result,verified);assert.equal(result.runtime_authorized,false);
    assert.equal(result.requests_used,2);assert.equal(result.requests_reserved_unknown,0);
    assert.equal(result.requests_remaining,0);assert.equal(result.consumed_plans,1);
    assert.equal(result.native_session_files,1);assert.equal(result.native_history_is_archival_only,true);
    const snapshot=JSON.parse(await fs.readFile(path.join(runs,'ui-backups',result.backup_id,'snapshot.json'),'utf8'));
    const task=snapshot.objects.find(row=>row.kind==='pi_task').body;
    const native=path.join('task-sessions',task.task_id,'sessions',task.checkpoint.file);
    assert.deepEqual(await fs.readFile(path.join(runs,'ui-backups',result.backup_id,'files',native)),
      await fs.readFile(path.join(runs,native)));
    assert.equal(hash(await fs.readFile(db)),before);
    assert.deepEqual(await fs.readFile(path.join(runs,'ui-backups',result.backup_id,'files',runId,'run.json')),originalRun);
    await assert.rejects(fs.stat(path.join(runs,'ui-backups',result.backup_id,'files',runId,'workspace')),error=>error.code==='ENOENT');
    assert.equal(await git(directory,'rev-parse','HEAD'),head);assert.equal(mock.requests.length,2);
    ui=await startUI(directory,{mockUrl:mock.url,restart:true});state=await ui.api('state');
    assert.equal(state.requests_used,2);assert.equal(state.requests_remaining,0);
    assert.equal(state.plans[0].status,'consumed');assert.equal(state.runs[0].claimed,false);
    assert.equal(mock.requests.length,2);assert.equal(state.runs[0].status,'candidate_ready');
    const run=state.runs[0];await operation(ui,'claim_run',{run_id:runId,revision:run.revision});
    await operation(ui,'cleanup',{run_id:runId,revision:(await ui.api('state')).runs[0].revision});
    complete=true;
  }finally{
    if(ui)await ui.stop();await mock.close();
    if(complete)await removeOwnFixture(directory,os.tmpdir());
  }
});
