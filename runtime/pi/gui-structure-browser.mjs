// Actual Pi/Chrome checks using only self-authored fixtures and loopback SSE.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {startMock} from './test/mock-server.mjs';
import {python,project,startUI,git,operation} from './test/ui-case.mjs';

if(!process.argv[2]||!path.isAbsolute(process.argv[2]))throw new Error('NEW absolute evidence directory required');
const directory=path.resolve(process.argv[2]);await fs.mkdir(directory);
const modelContext=process.argv.slice(3).includes('--model-context');
const proof={passed:false,checks:[],paid_model_requests:0,credential_reads:0,antigravity_calls:0};
const {chromium}=createRequire(import.meta.url)(process.env.PI_UI_PLAYWRIGHT_PACKAGE||'playwright');
const mocks=[],origins=new Set();let ui,browser;
const sample=path.join(directory,'manual'),index=path.join(sample,'structure-indexes','sample');
async function fixture(root){await promisify(execFile)(python,[path.join(project,'tests','gui_structure_fixture.py'),root],
 {cwd:project,windowsHide:true,timeout:60000});}
async function launch(root,mock,budget=2,attempts=1){
 ui=await startUI(root,{mockUrl:mock.url,restart:true,requestBudget:budget,auto:true,maxAttempts:attempts,
  structureIndex:path.join(root,'structure-indexes','sample'),structureContextQuery:modelContext?'add':null});origins.add(ui.base);
}
try{
 const mock=await startMock();mocks.push(mock);await fixture(sample);await launch(sample,mock);
 const originalHead=await git(sample,'rev-parse','HEAD');
 browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1080}}),errors=[],external=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(!origins.has(new URL(request.url()).origin))external.push(request.url());});
 async function open(){await page.goto(ui.launch);await page.locator('#notice').filter({hasText:'已连接'}).waitFor();}
 async function done(text){
  try{await page.locator('#notice').filter({hasText:text}).waitFor({timeout:90000});}
  catch(error){const jobs=(await ui.api('state')).jobs;throw new Error(`${error.message}; job failures: ${jobs.filter(j=>j.status==='failed').map(j=>j.error).join('; ')}`);}
 }
 await open();await page.locator('#structure-panel > summary').click();
 const display=await page.locator('#structure-configuration').innerText();
 for(const text of ['代码结构来源已核验','calculator.py','源码 SHA256','1.6.2',modelContext?'读取明确查询':'不向模型传递结构图'])assert.ok(display.includes(text),text);
 assert.ok(!display.includes(index));proof.checks.push('fixed engine, selected source range and fingerprints displayed without private index path');
 await page.locator('#structure-query').fill('add');await page.locator('#structure-preview').click();await done('查询结构出处完成');
 await page.locator('#structure-preview-result .structure-citations > summary').click();
 assert.match(await page.locator('#structure-preview-result').innerText(),/add · function · calculator.py:1/);
 assert.equal(mock.requests.length,0);assert.equal((await ui.api('state')).planning_attempts.length,0);
 proof.checks.push('metadata query and provenance have zero model calls or reservations');
 await page.locator('#plan').click();await done('生成方案完成');
 let state=await ui.api('state');assert.equal(mock.requests.length,2);assert.equal(state.runs.length,0);
 assert.ok(state.plans[0].structure_binding);
 for(const request of mock.requests){
  for(const message of request.messages.filter(m=>m.role==='user')){
   const envelope=JSON.parse(typeof message.content==='string'?message.content:message.content[0].text);
   assert.equal(envelope.structure_index,undefined);
   if(modelContext){
    const {verifiedStructure}=await import('./structure-context.mjs');
    const snapshot=await Promise.all(envelope.authorized_paths.map(async name=>({path:name,
     content:await fs.readFile(path.join(sample,'repository',name),'utf8')})));
    assert.equal(envelope.files,undefined);
    const context=verifiedStructure(envelope.structure_context,envelope.issue,envelope.base_sha,snapshot);
    assert.equal(context.query,'add');assert.equal(context.context_sha256,state.plans[0].planning_evidence.structure_context_sha256);
    assert.ok(context.hits.some(n=>n.name==='add'));assert.equal(context.runtime_authorized,false);
   }else assert.equal(envelope.structure_context,undefined);
   assert.ok(!JSON.stringify(envelope).includes('structure-manifest'));
  }
 }
 assert.deepEqual(mock.requests[0].tools.map(t=>t.function.name).sort(),['propose_repair','read_source']);
 proof.checks.push(modelContext?'real Pi AgentSession receives verified structure metadata and matching receipt with two requests and original tools'
  :'real Pi AgentSession produces a saved plan with two requests; original tools and model envelope have no structural graph');
 const oldPlan=state.plans[0],manifest=path.join(index,'structure-manifest.json'),original=await fs.readFile(manifest);
 await fs.writeFile(manifest,Buffer.concat([original,Buffer.from(' ')]));
 await page.locator('#refresh').click();await page.locator('#structure-configuration').getByText(/代码结构来源已失效/).waitFor();
 assert.equal(await page.locator('#plan').isDisabled(),true);assert.equal(await page.locator('#auto').isDisabled(),true);
 await page.locator('#approve-plan').click();await done('structure files changed');
 state=await ui.api('state');assert.equal(state.plans[0].status,'awaiting_approval');assert.equal(state.runs.length,0);
 assert.equal(mock.requests.length,2);assert.equal(state.requests_used,2);
 assert.match(await page.locator('#structure-preview-result').innerText(),/上次结构查询已过期/);
 proof.checks.push('manifest byte change blocks old approval and disables planning without consumption or new calls');
 await page.screenshot({path:path.join(directory,'structure-stale.png'),animations:'disabled',timeout:60000});
 await fs.writeFile(manifest,original);await ui.stop();await launch(sample,mock);await open();
 state=await ui.api('state');assert.equal(state.requests_used,2);assert.equal(state.plans[0].claimed,false);
 await assert.rejects(operation(ui,'claim_plan',{plan_id:oldPlan.id,revision:oldPlan.revision}),/claim challenge changed/);
 await page.locator('#refresh').click();await page.locator('#items button').first().click();
 await page.locator('#claim-plan').click();await done('核验并认领方案完成');
 assert.match(await page.locator('#content').innerText(),modelContext?/原规划读取了经核验的结构元数据/:/图数据未传给模型/);
 proof.checks.push('restart preserves used budget and rejects old challenge; fresh claim requires independent approval');
 await page.locator('#structure-panel > summary').click();
 await page.screenshot({path:path.join(directory,'structure-plan.png'),animations:'disabled',timeout:60000});
 await page.locator('#approve-plan').click();await done('候选已验证');
 state=await ui.api('state');assert.equal(state.plans[0].status,'consumed');assert.equal(mock.requests.length,2);
 const candidate=state.runs[0];await fs.appendFile(manifest,' ');
 await assert.rejects(operation(ui,'prepare',{run_id:candidate.run_id,revision:candidate.revision,retry_aborted:false}),/structure files changed/);
 assert.equal((await ui.api('state')).runs[0].status,'candidate_ready');assert.equal(mock.requests.length,2);
 await fs.writeFile(manifest,original);await page.locator('#refresh').click();
 proof.checks.push('changed structural provenance blocks candidate preparation without consuming it; cleanup remains available after verification');
 await page.locator('#confirm-cleanup').check();await page.locator('#cleanup').click();await done('候选已清理');
 assert.equal(await git(sample,'rev-parse','HEAD'),originalHead);assert.equal(await git(sample,'status','--porcelain'),'');
 proof.checks.push('saved-plan approval and candidate cleanup use no new requests and preserve original source');
 await ui.stop();await launch(sample,mock);await open();state=await ui.api('state');
 assert.equal(state.plans[0].status,'consumed');assert.equal(state.requests_used,2);
 proof.checks.push('consumed plan remains consumed through a second process restart');
 await page.setViewportSize({width:390,height:844});await page.locator('#structure-panel > summary').click();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
 await page.locator('#structure-panel').screenshot({path:path.join(directory,'structure-mobile.png'),animations:'disabled',timeout:60000});
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);proof.checks.push('390px provenance display has no overflow, browser errors or external requests');
 await browser.close();browser=null;await ui.stop();ui=null;
 for(const mode of ['success','stale_second_round']){
  const root=path.join(directory,mode);await fixture(root);
  const graph=path.join(root,'structure-indexes','sample','structure-graph.json'),graphBytes=await fs.readFile(graph);
  const retry=await startMock({scenario:'retry_fix',...(mode==='stale_second_round'?{onRequest:async(body,count)=>{
   if(count===4)await fs.writeFile(graph,Buffer.concat([graphBytes,Buffer.from(' ')]));
  }}:{})});mocks.push(retry);await launch(root,retry,4,2);
  const head=await git(root,'rev-parse','HEAD');const outcome=await operation(ui,'auto',{issue:'addition'});
  assert.equal(retry.requests.length,4);
  assert.equal(outcome.result.workflow_status,mode==='success'?'candidate_ready':'rolled_back');
  const budget=await ui.api('state');assert.equal(budget.requests_used,mode==='success'?4:2);
  assert.equal(budget.requests_reserved_unknown,mode==='success'?0:2);assert.equal(budget.requests_remaining,0);
  if(modelContext){
   const {verifiedStructure}=await import('./structure-context.mjs');
   const contexts=await Promise.all(retry.requests.map(async(request,i)=>{
    const message=request.messages.find(m=>m.role==='user');
    const envelope=JSON.parse(typeof message.content==='string'?message.content:message.content[0].text);
    const withRead=retry.requests[i%2?i:i+1];
    const returned=JSON.parse(withRead.messages.find(m=>m.role==='tool'&&m.content?.includes('before_sha256')).content);
    const snapshot=await Promise.all(envelope.authorized_paths.map(async name=>({path:name,
     content:name===returned.path?returned.content:await fs.readFile(path.join(root,'repository',name),'utf8')})));
    assert.equal(envelope.files,undefined);
    return verifiedStructure(envelope.structure_context,envelope.issue,envelope.base_sha,snapshot);
   }));
   for(const context of contexts)assert.equal(context.source.head,head);
   assert.equal(contexts[0].planning_base_sha,head);assert.equal(contexts[2].planning_base_sha,head);
   assert.notEqual(contexts[0].planning_files_sha256,contexts[2].planning_files_sha256);
   assert.notEqual(contexts[0].context_sha256,contexts[2].context_sha256);
   const runDir=path.join(root,'runs',budget.runs[0].run_id);
   const record=JSON.parse(await fs.readFile(path.join(runDir,'run.json'),'utf8'));
   assert.equal(record.rounds[0].provider_evidence.structure_context_sha256,contexts[0].context_sha256);
   if(mode==='success'){
    assert.equal(record.rounds[1].provider_evidence.structure_context_sha256,contexts[2].context_sha256);
    const report=await fs.readFile(path.join(runDir,'report.md'),'utf8');
    assert.ok(report.includes(contexts[0].context_sha256));assert.ok(report.includes(contexts[2].context_sha256));
    assert.ok(report.includes('Read-only structure provenance'));assert.ok(report.includes('calculator.py'));
   }else{
    const report=await fs.readFile(path.join(runDir,'report.md'),'utf8');
    assert.ok(report.includes(contexts[2].context_sha256));assert.ok(report.includes('no successful plan implied'));
   }
   proof.checks.push(`${mode}: actual model envelopes bind each candidate base to the original index; per-round receipts persist`);
  }
  if(mode==='success'){
   const run=budget.runs[0];await operation(ui,'cleanup',{run_id:run.run_id,revision:run.revision});
  }else await fs.writeFile(graph,graphBytes);
  await ui.stop();await launch(root,retry,4,2);const reopened=await ui.api('state');
  assert.equal(reopened.requests_used,budget.requests_used);assert.equal(reopened.requests_reserved_unknown,budget.requests_reserved_unknown);
  assert.equal(await git(root,'rev-parse','HEAD'),head);assert.equal(await git(root,'status','--porcelain'),'');
  proof.checks.push(mode==='success'?'two-round real Pi auto repair succeeds while verifying original structure rather than edited candidate; used4 survives restart'
   :'second-round graph change during inference prevents application, rolls back owned candidate and preserves used2/unknown2 across restart');
  await ui.stop();ui=null;
 }
 proof.model_context_enabled=modelContext;
 proof.synthetic_http_requests=mocks.reduce((n,m)=>n+m.requests.length,0);proof.source_unchanged=true;proof.passed=true;
}catch(error){proof.error=error.message;throw error;}
finally{
 if(browser)await browser.close();if(ui)await ui.stop();for(const mock of mocks)await mock.close();
 await fs.writeFile(path.join(directory,'browser-validation.json'),JSON.stringify(proof,null,2)+'\n');
 console.log(JSON.stringify({passed:proof.passed,checks:proof.checks.length,evidence:directory}));
}
