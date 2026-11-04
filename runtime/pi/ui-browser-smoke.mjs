// Optional real-browser acceptance script. Install Playwright separately; no live models.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {startUI, git} from './test/ui-case.mjs';

const directory=path.resolve(process.argv[2] || '');
if(!process.argv[2]||!path.isAbsolute(process.argv[2]))throw new Error('A NEW absolute evidence directory is required');
await fs.mkdir(directory);
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PI_UI_PLAYWRIGHT_PACKAGE || 'playwright');
let ui=await startUI(path.join(directory,'sample'),{auto:true});
let browser;
const proof={passed:false,browser:'Chromium / installed Chrome',checks:[],paid_model_requests:0,antigravity_calls:0};
try{
 browser=await chromium.launch({channel:'chrome',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1080}});
 const page=await context.newPage();const errors=[],external=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(!request.url().startsWith(ui.base+'/'))external.push(request.url());});
 page.on('dialog',dialog=>dialog.accept());
 const head=await git(ui.directory,'rev-parse','HEAD');
 const unauthorized=await fetch(ui.base+'/api/state');assert.equal(unauthorized.status,401);proof.checks.push('unauthenticated live API denied');
 await page.goto(ui.launch);await page.locator('#notice').filter({hasText:'已连接'}).waitFor();
 assert.equal(page.url(),ui.base+'/');assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);proof.checks.push('fragment removed; no browser storage');
 async function click(id,label){
  await page.locator('#'+id).click();
  await page.waitForFunction(value=>document.querySelector('#notice').textContent.startsWith(value),label+'完成',{timeout:90000});
  assert.equal(await page.locator('#notice').evaluate(el=>el.classList.contains('error')),false);
 }
 await page.locator('#issue').fill('<img src="https://evil.invalid/x" onerror="window.attack=1"> 修复加法');
 await click('plan','生成方案');assert.equal(await page.evaluate(()=>window.attack || 0),0);
 assert.equal(await page.locator('#content img').count(),0);assert.ok(await page.locator('.d2h-file-wrapper').count()>0);proof.checks.push('untrusted issue stays text; diff2html renders');
 await click('reject-plan','方案审批');let state=await ui.api('state');
 assert.equal(state.runs[0].status,'rejected');assert.equal(state.runs[0].baseline_tests.length,0);
 assert.equal(await git(ui.directory,'rev-parse','HEAD'),head);proof.checks.push('reject before workspace/tests');
 await page.locator('#issue').fill('修复 add 加法问题并通过现有测试。');
 await click('plan','生成方案');state=await ui.api('state');const savedPlan=state.plans.at(-1);
 const oldToken=new URLSearchParams(new URL(ui.launch).hash.slice(1)).get('session');
 await ui.stop();ui=await startUI(ui.directory,{auto:true,restart:true});
 assert.equal((await fetch(ui.base+'/api/state',{headers:{Authorization:'Bearer '+oldToken}})).status,401);
 await page.goto(ui.launch);await page.locator('#notice').filter({hasText:'已连接'}).waitFor();
 await page.getByRole('button',{name:'方案 '+savedPlan.id.slice(0,8)+' · 等待审批 · 历史只读',exact:true}).click();
 assert.equal(await page.locator('#approve-plan').count(),0);
 await page.screenshot({path:path.join(directory,'historical-plan-preview.png'),fullPage:true});
 await click('claim-plan','核验并认领方案');
 assert.equal((await ui.api('state')).runs.length,1);proof.checks.push('restart keeps planning evidence; old capability denied; claim makes no edits');
 await click('approve-plan','方案审批');state=await ui.api('state');
 assert.equal(state.runs.at(-1).status,'candidate_ready');assert.equal(state.runs.at(-1).source_unchanged,true);proof.checks.push('click approval runs isolated baseline and candidate tests');
 await click('prepare','封装并重测');state=await ui.api('state');
 assert.equal(state.runs.at(-1).review.status,'prepared');
 assert.equal(await git(ui.directory,'for-each-ref','--format=%(refname)','refs/heads/integration'),'');
 assert.equal(await page.locator('#publish').isDisabled(),true);proof.checks.push('prepare does not publish; separate checkbox required');
 const historicalRun=state.runs.at(-1);
 await ui.stop();ui=await startUI(ui.directory,{auto:true,restart:true});
 await page.goto(ui.launch);await page.locator('#notice').filter({hasText:'已连接'}).waitFor();
 await page.getByRole('button',{name:'运行 '+historicalRun.run_id.slice(0,8)+' · 候选已验证 · 历史只读',exact:true}).click();
 assert.equal(await page.locator('#publish').count(),0);await click('claim-run','核验并认领运行');
 assert.equal(await page.locator('#publish').isDisabled(),true);proof.checks.push('historical prepared run needs claim and fresh publication checkbox');
 await click('reject-publish','拒绝发布');assert.equal((await ui.api('state')).runs.at(-1).review.status,'rejected');
 await page.locator('#confirm-publish').check();await click('publish','批准发布');state=await ui.api('state');
 const run=state.runs.at(-1);assert.equal(run.review.status,'published');assert.equal(await git(ui.directory,'rev-parse','HEAD'),head);
 proof.checks.push('independent publication rejection and subsequent explicit approval');
 await page.screenshot({path:path.join(directory,'approval-preview.png'),fullPage:true});
 await page.locator('#confirm-cleanup').check();await click('cleanup','清理候选');
 assert.equal(await git(ui.directory,'rev-parse',run.integration_branch),run.integration_commit);
 assert.equal(await git(ui.directory,'status','--porcelain'),'');proof.checks.push('cleanup preserves published commit and source checkout');
 // A further task exercises the startup-scoped autonomous button, still no automatic publication.
 await click('auto','自主修复');state=await ui.api('state');
 assert.equal(state.runs.at(-1).mode,'auto');assert.equal(state.runs.at(-1).status,'candidate_ready');
 assert.equal(state.runs.at(-1).review,null);proof.checks.push('startup-enabled autonomous button repairs without publication');
 await page.locator('#confirm-cleanup').check();await click('cleanup','清理候选');
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(directory,'mobile-preview.png'),fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);proof.checks.push('390px responsive layout');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);proof.checks.push('no page errors or external requests');
 proof.source_head_unchanged=await git(ui.directory,'rev-parse','HEAD')===head;
 proof.source_clean=await git(ui.directory,'status','--porcelain')==='';
 proof.published_branch=run.integration_branch;proof.published_commit=run.integration_commit;
 proof.workflow_statuses=(await ui.api('state')).runs.map(r=>({run_id:r.run_id,status:r.status,mode:r.mode,source_unchanged:r.source_unchanged}));
 proof.passed=true;
}catch(error){proof.error=error.message;throw error;}
finally{
 if(browser)await browser.close();await ui.stop();
 await fs.writeFile(path.join(directory,'browser-validation.json'),JSON.stringify(proof,null,2)+'\n');
 console.log(JSON.stringify({passed:proof.passed,checks:proof.checks.length,evidence:directory}));
}
