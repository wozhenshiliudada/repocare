import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {startMock} from './mock-server.mjs';
import {startUI, operation, git, removeOwnFixture, python, project} from './ui-case.mjs';

test('Pi saved candidate prepare crash: explicit abort, full retry and fresh publication', {timeout:180000}, async()=>{
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pi-ui-'));
  const mock=await startMock();let ui,complete=false;
  try{
    ui=await startUI(directory,{mockUrl:mock.url});
    const head=await git(directory,'rev-parse','HEAD');
    await operation(ui,'plan',{issue:'addition bug'});
    let state=await ui.api('state');const plan=state.plans[0];
    await operation(ui,'decision',{plan_id:plan.id,fingerprint:plan.fingerprint,decision:'approve'});
    const runId=(await ui.api('state')).runs[0].run_id;
    await ui.stop();
    await assert.rejects(promisify(execFile)(python,[path.join(project,'tests','prepare_ui_crash.py'),directory,'after_remove','--pi-mock-url',mock.url],
      {cwd:project,windowsHide:true,timeout:60000}),error=>error.code===91);
    const reviewPath=path.join(directory,'runs',runId,'integration-review.json');
    const old=JSON.parse(await fs.readFile(reviewPath,'utf8'));
    ui=await startUI(directory,{mockUrl:mock.url,restart:true});
    state=await ui.api('state');let run=state.runs[0];
    assert.equal(run.claimed,false);assert.equal(state.jobs.find(j=>j.id===runId).status,'interrupted');
    await assert.rejects(operation(ui,'recover',{run_id:runId,revision:run.revision}),/explicit claim/);
    await operation(ui,'claim_run',{run_id:runId,revision:run.revision});
    run=(await ui.api('state')).runs[0];
    await operation(ui,'recover',{run_id:runId,revision:run.revision});
    const aborted=await fs.readFile(reviewPath);
    run=(await ui.api('state')).runs[0];assert.equal(run.review.status,'aborted');
    await operation(ui,'prepare',{run_id:runId,revision:run.revision,retry_aborted:true});
    run=(await ui.api('state')).runs[0];const fresh=JSON.parse(await fs.readFile(reviewPath,'utf8'));
    assert.notEqual(fresh.preparation.attempt_id,old.preparation.attempt_id);
    assert.notEqual(fresh.review_sha256,old.review_sha256);
    assert.deepEqual(await fs.readFile(path.join(directory,'runs',runId,'integration-attempts',old.preparation.attempt_id,'integration-review.json')),aborted);
    assert.equal(fresh.review.tests.length,1);assert.equal(fresh.review.tests[0].returncode,0);
    await assert.rejects(operation(ui,'publish',{run_id:runId,revision:run.revision,fingerprint:old.review_sha256}),/current independent/);
    run=(await ui.api('state')).runs[0];
    await operation(ui,'publish',{run_id:runId,revision:run.revision,fingerprint:fresh.review_sha256});
    state=await ui.api('state');run=state.runs[0];
    assert.equal(run.review.status,'published');assert.equal(state.requests_used,2);
    assert.equal(state.requests_remaining,0);assert.equal(mock.requests.length,2);
    assert.equal(state.plans[0].status,'consumed');assert.equal(await git(directory,'rev-parse','HEAD'),head);
    await operation(ui,'cleanup',{run_id:runId,revision:run.revision});complete=true;
  }finally{
    if(ui)await ui.stop();await mock.close();
    if(complete)await removeOwnFixture(directory,os.tmpdir());
  }
});

test('real Pi session + UI restart requires claims, fresh approvals and durable budget', {timeout:180000}, async()=>{
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pi-ui-'));
  const mock=await startMock();let ui,complete=false;
  try{
    ui=await startUI(directory,{mockUrl:mock.url});
    const head=await git(directory,'rev-parse','HEAD');
    await operation(ui,'plan',{issue:'addition bug'});
    let state=await ui.api('state');const plan=state.plans[0];
    assert.equal(state.requests_used,2);assert.equal(mock.requests.length,2);
    assert.equal(state.runs.length,0);assert.equal(await git(directory,'status','--porcelain'),'');
    const oldLaunch=ui.launch;
    await ui.stop();ui=await startUI(directory,{mockUrl:mock.url,restart:true});
    state=await ui.api('state');let restored=state.plans[0];
    assert.notEqual(ui.launch,oldLaunch);assert.equal(restored.claimed,false);
    assert.equal(state.requests_used,2);assert.equal(state.requests_remaining,0);
    await assert.rejects(operation(ui,'decision',{plan_id:plan.id,fingerprint:plan.fingerprint,decision:'approve'}),/requires explicit claim/);
    await assert.rejects(operation(ui,'claim_plan',{plan_id:plan.id,revision:plan.revision}),/claim challenge changed/);
    await operation(ui,'claim_plan',{plan_id:restored.id,revision:restored.revision});
    assert.equal(mock.requests.length,2);
    await operation(ui,'decision',{plan_id:plan.id,fingerprint:plan.fingerprint,decision:'approve'});
    state=await ui.api('state');let run=state.runs[0];
    assert.equal(run.status,'candidate_ready');assert.equal(mock.requests.length,2,'Saved-plan approval must not call Pi again');
    assert.equal(run.planning_origin.provider_evidence.requests,2);
    assert.equal(run.rounds[0].provider_evidence.requests,0);
    const report=await fs.readFile(path.join(directory,'runs',run.run_id,'report.md'),'utf8');
    assert.match(report,/Planning model requests: `2`/);
    assert.equal(run.source_unchanged,true);
    await operation(ui,'prepare',{run_id:run.run_id,revision:run.revision,retry_aborted:false});
    state=await ui.api('state');run=state.runs[0];
    assert.equal(run.review.status,'prepared');assert.equal(await git(directory,'for-each-ref','--format=%(refname)','refs/heads/integration'),'');
    await ui.stop();ui=await startUI(directory,{mockUrl:mock.url,restart:true});
    state=await ui.api('state');run=state.runs[0];assert.equal(run.claimed,false);
    await assert.rejects(operation(ui,'publish',{run_id:run.run_id,revision:run.revision,fingerprint:run.review.review_sha256}),/requires explicit claim/);
    await operation(ui,'claim_run',{run_id:run.run_id,revision:run.revision});
    state=await ui.api('state');run=state.runs[0];
    await operation(ui,'publish',{run_id:run.run_id,revision:run.revision,fingerprint:run.review.review_sha256});
    state=await ui.api('state');run=state.runs[0];
    assert.equal(run.review.status,'published');assert.equal(await git(directory,'rev-parse','HEAD'),head);
    await operation(ui,'cleanup',{run_id:run.run_id,revision:run.revision});
    assert.equal(await git(directory,'status','--porcelain'),'');
    assert.equal(await git(directory,'rev-parse',run.integration_branch),run.integration_commit);
    assert.equal(mock.requests.length,2);
    complete=true;
  }finally{
    if(ui)await ui.stop();await mock.close();
    // Retain failed-case evidence rather than deleting an interrupted candidate.
    if(complete)await removeOwnFixture(directory,os.tmpdir());
  }
});

test('failed real Pi planning preserves unknown request reservation across restart', {timeout:90000}, async()=>{
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pi-ui-'));
  const mock=await startMock({failure:true});let ui,complete=false;
  try{
    ui=await startUI(directory,{mockUrl:mock.url});
    await assert.rejects(operation(ui,'plan',{issue:'addition bug'}),/Pi planning failed/);
    assert.equal(mock.requests.length,1);
    await ui.stop();ui=await startUI(directory,{mockUrl:mock.url,restart:true});
    const state=await ui.api('state');
    assert.equal(state.requests_used,0,'No trustworthy final count was received');
    assert.equal(state.requests_reserved_unknown,2,'Unknown does not mean zero consumption');
    assert.equal(state.requests_remaining,0);
    assert.equal(state.planning_attempts[0].status,'unknown');
    const evidence=state.planning_attempts[0].evidence;
    assert.equal(evidence.schema_version,1);assert.equal(evidence.failure_category,'provider_error');
    assert.equal(evidence.requests,null);assert.equal(evidence.requests_started,1);
    assert.ok(evidence.events.some(e=>e.type==='agent_end'));
    assert.doesNotMatch(JSON.stringify(state),/syntheticNotARealKey|Bearer synthetic/);
    await assert.rejects(operation(ui,'plan',{issue:'another try'}),/budget exhausted or reserved/);
    assert.equal(mock.requests.length,1,'Restart must not replenish the allowance');
    assert.equal(await git(directory,'status','--porcelain'),'');complete=true;
  }finally{
    if(ui)await ui.stop();await mock.close();
    if(complete)await removeOwnFixture(directory,os.tmpdir());
  }
});

test('real Pi consumed plan crash can close only its journal, without replay or budget reset', {timeout:120000}, async()=>{
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pi-ui-'));
  const mock=await startMock();let ui,complete=false;
  try{
    ui=await startUI(directory,{mockUrl:mock.url});
    const head=await git(directory,'rev-parse','HEAD');
    await operation(ui,'plan',{issue:'addition bug'});
    const plan=(await ui.api('state')).plans[0];
    assert.equal(mock.requests.length,2);
    await ui.stop();
    await assert.rejects(promisify(execFile)(python,[path.join(project,'tests','ui_consumption_crash.py'),directory,
      '--pi-mock-url',mock.url],{cwd:project,windowsHide:true,timeout:30000}),error=>error.code===23);
    ui=await startUI(directory,{mockUrl:mock.url,restart:true});
    let state=await ui.api('state');const run=state.runs[0];
    assert.equal(state.plans[0].status,'consumed');assert.equal(run.status,'evidence_invalid');
    assert.equal(state.requests_used,2);assert.equal(state.requests_remaining,0);
    const preview=(await operation(ui,'inspect_interruption',{run_id:run.run_id})).result.reconciliation;
    assert.equal(preview.can_close_registration,true);assert.equal(preview.execution_outcome,'unknown');
    assert.equal(preview.checks.directory_exists,false);
    await operation(ui,'close_interruption',{run_id:run.run_id,revision:preview.revision});
    state=await ui.api('state');assert.equal(state.runs[0].status,'registration_closed');
    assert.equal(state.plans[0].status,'consumed');assert.equal(mock.requests.length,2);
    assert.equal(await git(directory,'status','--porcelain'),'');assert.equal(await git(directory,'rev-parse','HEAD'),head);
    await assert.rejects(fs.stat(path.join(directory,'runs',run.run_id)),error=>error.code==='ENOENT');
    await ui.stop();ui=await startUI(directory,{mockUrl:mock.url,restart:true});
    state=await ui.api('state');assert.equal(state.runs[0].status,'registration_closed');
    assert.equal(state.runs[0].reconciliation,null);assert.equal(state.requests_used,2);
    await assert.rejects(operation(ui,'close_interruption',{run_id:run.run_id,revision:preview.revision}),/evidence or session changed/);
    await assert.rejects(operation(ui,'decision',{plan_id:plan.id,fingerprint:plan.fingerprint,decision:'approve'}),/requires explicit claim/);
    await assert.rejects(operation(ui,'claim_plan',{plan_id:plan.id,revision:state.plans[0].revision}),/already consumed/);
    assert.equal(mock.requests.length,2);complete=true;
  }finally{
    if(ui)await ui.stop();await mock.close();
    if(complete)await removeOwnFixture(directory,os.tmpdir());
  }
});

test('real Pi saved-plan execution crash needs fresh explicit abort and retains planning budget', {timeout:150000}, async()=>{
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pi-ui-'));
  const mock=await startMock();let ui,complete=false;
  try{
    ui=await startUI(directory,{mockUrl:mock.url});
    const head=await git(directory,'rev-parse','HEAD');
    await operation(ui,'plan',{issue:'addition bug'});await ui.stop();
    await assert.rejects(promisify(execFile)(python,[path.join(project,'tests','repair_crash.py'),directory,
      '--ui','--pi-mock-url',mock.url],{cwd:project,windowsHide:true,timeout:30000}),error=>error.code===94);
    ui=await startUI(directory,{mockUrl:mock.url,restart:true});
    let state=await ui.api('state');const run=state.runs[0];
    assert.equal(run.repair_finished,false);assert.equal(state.plans[0].status,'consumed');
    assert.equal(run.repair_execution.phase,'workspace_ready');assert.equal(state.requests_used,2);
    await assert.rejects(operation(ui,'claim_run',{run_id:run.run_id,revision:run.revision}),/no final process evidence/);
    const preview=(await operation(ui,'inspect_repair',{run_id:run.run_id})).result.repair_inspection;
    assert.equal(preview.can_abort,true);assert.equal(preview.checks.candidate_matches,true);
    await ui.stop();ui=await startUI(directory,{mockUrl:mock.url,restart:true});
    await assert.rejects(operation(ui,'recover_repair',{run_id:run.run_id,revision:preview.session_revision}),/evidence or session changed/);
    const fresh=(await operation(ui,'inspect_repair',{run_id:run.run_id})).result.repair_inspection;
    await operation(ui,'recover_repair',{run_id:run.run_id,revision:fresh.session_revision});
    state=await ui.api('state');assert.equal(state.runs[0].status,'repair_aborted');
    assert.equal(state.runs[0].repair_finished,true);assert.equal(state.runs[0].repair_execution.phase,'aborted');
    assert.equal(state.requests_used,2);assert.equal(state.requests_remaining,0);
    assert.equal(state.plans[0].status,'consumed');assert.equal(mock.requests.length,2);
    assert.equal(await git(directory,'status','--porcelain'),'');assert.equal(await git(directory,'rev-parse','HEAD'),head);
    assert.equal(await git(directory,'for-each-ref','--format=%(refname)','refs/heads/maintenance','refs/heads/integration'),'');
    await assert.rejects(fs.stat(path.join(directory,'runs',run.run_id,'workspace')),error=>error.code==='ENOENT');
    const execution=JSON.parse(await fs.readFile(path.join(directory,'runs',run.run_id,'repair-execution.json'),'utf8'));
    assert.equal(execution.abort_authorizations.length,1);assert.equal(execution.abort_authorization.inspection.execution_outcome,'unknown');
    assert.ok(execution.process_facts.some(item=>item.kind==='baseline_test'&&item.returncode===1));
    complete=true;
  }finally{
    if(ui)await ui.stop();await mock.close();
    if(complete)await removeOwnFixture(directory,os.tmpdir());
  }
});
