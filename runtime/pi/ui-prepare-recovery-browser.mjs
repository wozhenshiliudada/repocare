// Browser acceptance against self-authored fixtures; no credentials or live model.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createRequire} from 'node:module';
import {startUI, operation, git, python, project} from './test/ui-case.mjs';

if(!process.argv[2]||!path.isAbsolute(process.argv[2]))throw new Error('A NEW absolute directory is required');
const directory=path.resolve(process.argv[2]);await fs.mkdir(directory);
const {chromium}=createRequire(import.meta.url)(process.env.PI_UI_PLAYWRIGHT_PACKAGE || 'playwright');
const proof={passed:false,browser:'installed Chrome',checks:[],paid_model_requests:0,antigravity_calls:0};
let ui,browser;
try{
  ui=await startUI(path.join(directory,'sample'));
  browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1080}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  async function open(){await page.goto(ui.launch);await page.locator('#notice').filter({hasText:'已连接'}).waitFor();}
  async function click(id,label){
    await page.locator('#'+id).click();
    await page.waitForFunction(value=>document.querySelector('#notice').textContent.startsWith(value),label+'完成',{timeout:90000});
    assert.equal(await page.locator('#notice').evaluate(el=>el.classList.contains('error')),false);
  }
  async function select(runId){await page.getByRole('button',{name:'运行 '+runId.slice(0,8)+' · 候选已验证 · 历史只读',exact:true}).click();}
  async function crash(stage){
    await ui.stop();
    await assert.rejects(promisify(execFile)(python,[path.join(project,'tests','prepare_ui_crash.py'),ui.directory,stage],
      {cwd:project,windowsHide:true,timeout:60000}),error=>error.code===91);
    ui=await startUI(ui.directory,{restart:true});await open();
  }
  const head=await git(ui.directory,'rev-parse','HEAD');
  await open();await page.locator('#issue').fill('修复加法并保留测试');await click('plan','生成方案');await click('approve-plan','方案审批');
  let run=(await ui.api('state')).runs[0];const runId=run.run_id;
  await crash('after_remove');await select(runId);
  assert.equal(await page.locator('#recover').count(),0);assert.equal(await page.locator('#claim-run').count(),1);
  await click('claim-run','核验并认领运行');
  assert.equal(await page.locator('#recover').count(),1);assert.equal(await page.locator('#cleanup').count(),0);
  const reviewPath=path.join(ui.directory,'runs',runId,'integration-review.json');
  const old=JSON.parse(await fs.readFile(reviewPath,'utf8'));
  proof.checks.push('real prepare job exit after removal, restart requires claim, no automatic continuation or candidate cleanup');
  await click('recover','恢复中断');assert.equal(await page.locator('#retry-prepare').count(),1);
  await click('retry-prepare','封装并重测');run=(await ui.api('state')).runs[0];
  assert.notEqual(run.review.review_sha256,old.review_sha256);
  assert.equal(await page.locator('#confirm-publish').isChecked(),false);assert.equal(await page.locator('#publish').isDisabled(),true);
  await assert.rejects(operation(ui,'publish',{run_id:runId,revision:run.revision,fingerprint:old.review_sha256}),/current independent/);
  proof.checks.push('explicit abort and full retry produce a new fingerprint; old approval rejected, new approval starts unchecked');
  await page.screenshot({path:path.join(directory,'prepare-retested.png'),fullPage:true});
  await ui.stop();ui=await startUI(ui.directory,{restart:true});await open();await select(runId);
  await click('claim-run','核验并认领运行');assert.equal(await page.locator('#confirm-publish').isChecked(),false);
  await page.locator('#confirm-publish').check();await click('publish','批准发布');
  assert.equal((await ui.api('state')).runs[0].review.status,'published');
  await page.locator('#confirm-cleanup').check();await click('cleanup','清理候选');
  proof.checks.push('another restart needs fresh claim and explicit publication; only fixture integration branch is created');
  // A separate fixture exercises cleanup_required and the restored recovery button.
  await ui.stop();ui=await startUI(path.join(directory,'blocked-sample'));await open();
  await page.locator('#issue').fill('修复加法');await click('plan','生成方案');await click('approve-plan','方案审批');
  run=(await ui.api('state')).runs[0];await crash('after_add');await select(run.run_id);await click('claim-run','核验并认领运行');
  const empty=path.join(ui.directory,'runs',run.run_id,'integration-verification','human-empty');await fs.mkdir(empty);
  await page.locator('#recover').click();await page.waitForFunction(()=>document.querySelector('#notice').classList.contains('error'),null,{timeout:30000});
  assert.match(await page.locator('#notice').innerText(),/unrecorded directories/);
  assert.equal((await ui.api('state')).runs[0].review.status,'cleanup_required');
  assert.ok((await fs.stat(empty)).isDirectory());assert.equal(await page.locator('#recover').count(),1);
  assert.equal(await page.locator('#cleanup').count(),0);
  await page.screenshot({path:path.join(directory,'prepare-blocked.png'),fullPage:true});
  proof.checks.push('untracked empty directory is retained; cleanup_required keeps recovery button and hides candidate cleanup');
  await fs.rmdir(empty);await click('recover','恢复中断');
  assert.equal((await ui.api('state')).runs[0].review.status,'aborted');
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(directory,'prepare-mobile.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  assert.deepEqual(errors,[]);proof.checks.push('fresh recovery after removing only own fixture directory; 390px layout and no page errors');
  proof.source_clean=await git(ui.directory,'status','--porcelain')==='';
  proof.source_head_unchanged=await git(path.join(directory,'sample'),'rev-parse','HEAD')===head;
  assert.ok(proof.source_clean&&proof.source_head_unchanged);proof.passed=true;
}catch(error){proof.error=error.message;throw error;}
finally{
  if(browser)await browser.close();if(ui)await ui.stop();
  await fs.writeFile(path.join(directory,'browser-validation.json'),JSON.stringify(proof,null,2)+'\n');
  console.log(JSON.stringify({passed:proof.passed,checks:proof.checks.length,evidence:directory}));
}
