// Test-only browser acceptance: generated fixture, crash injection, no live model or credentials.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createRequire} from 'node:module';
import {startUI, git, project, python} from './test/ui-case.mjs';

const directory=path.resolve(process.argv[2] || '');
if(!process.argv[2]||!path.isAbsolute(process.argv[2]))throw new Error('A NEW absolute evidence directory is required');
await fs.mkdir(directory);
const {chromium}=createRequire(import.meta.url)(process.env.PI_UI_PLAYWRIGHT_PACKAGE || 'playwright');
const proof={passed:false,browser:'Chromium / installed Chrome',checks:[],paid_model_requests:0,antigravity_calls:0};
let ui,browser;
try{
 ui=await startUI(path.join(directory,'sample'));
 browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1080}});
 const errors=[],external=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(!request.url().startsWith(ui.base+'/'))external.push(request.url());});
 async function open(){await page.goto(ui.launch);await page.locator('#notice').filter({hasText:'已连接'}).waitFor();}
 async function click(id,label){
  await page.locator('#'+id).click();
  await page.waitForFunction(value=>document.querySelector('#notice').textContent.startsWith(value),label+'完成',{timeout:90000});
  assert.equal(await page.locator('#notice').evaluate(el=>el.classList.contains('error')),false);
 }
 const head=await git(ui.directory,'rev-parse','HEAD');
 await open();await page.locator('#issue').fill('修复加法');await click('plan','生成方案');
 const plan=(await ui.api('state')).plans[0];
 const oldToken=new URLSearchParams(new URL(ui.launch).hash.slice(1)).get('session');
 await ui.stop();
 await assert.rejects(promisify(execFile)(python,[path.join(project,'tests','ui_consumption_crash.py'),ui.directory],
   {cwd:project,windowsHide:true,timeout:30000}),error=>error.code===23);
 ui=await startUI(ui.directory,{restart:true});
 assert.equal((await fetch(ui.base+'/api/state',{headers:{Authorization:'Bearer '+oldToken}})).status,401);
 await open();let state=await ui.api('state');const run=state.runs[0];
 assert.equal(run.status,'evidence_invalid');assert.equal(state.plans[0].status,'consumed');
 await page.getByRole('button',{name:'方案 '+plan.id.slice(0,8)+' · 方案已处理 · 历史只读',exact:true}).click();
 assert.equal(await page.locator('#approve-plan').count(),0);
 await page.locator('#linked-run').click();
 assert.equal(await page.locator('#cleanup').count(),0);assert.equal(await page.locator('#publish').count(),0);
 assert.equal(await page.locator('#close-interruption').count(),0);
 proof.checks.push('real process exit after consumed commit: no replay, old capability denied, linked run visible');
 await click('inspect-interruption','核验中断证据');
 state=await ui.api('state');assert.equal(state.runs[0].reconciliation.can_close_registration,true);
 assert.equal(await page.locator('#close-interruption').isDisabled(),true);
 await page.screenshot({path:path.join(directory,'interruption-preview.png'),fullPage:true});
 proof.checks.push('read-only preview shows fixed source/plan/path/ref/worktree evidence; separate unchecked closure required');
 const runDirectory=path.join(ui.directory,'runs',run.run_id);
 await fs.mkdir(runDirectory);const sentinel=path.join(runDirectory,'human.txt');await fs.writeFile(sentinel,'preserve');
 await page.locator('#confirm-close-interruption').check();await page.locator('#close-interruption').click();
 await page.waitForFunction(()=>document.querySelector('#notice').classList.contains('error'),null,{timeout:30000});
 assert.match(await page.locator('#notice').innerText(),/evidence or session changed/);
 assert.equal(await fs.readFile(sentinel,'utf8'),'preserve');
 assert.equal((await ui.api('state')).runs[0].status,'evidence_invalid');
 await click('inspect-interruption','核验中断证据');
 assert.equal(await page.locator('#close-interruption').count(),0);
 assert.equal((await ui.api('state')).runs[0].reconciliation.can_close_registration,false);
 proof.checks.push('new human file invalidates preview and remains untouched; fresh blocked preview hides closure');
 // Remove only the file/directory just created by this acceptance script, never generic recursive cleanup.
 await fs.unlink(sentinel);await fs.rmdir(runDirectory);
 await click('inspect-interruption','核验中断证据');
 assert.equal(await page.locator('#close-interruption').isDisabled(),true);
 await page.locator('#confirm-close-interruption').check();await click('close-interruption','关闭中断登记');
 state=await ui.api('state');assert.equal(state.runs[0].status,'registration_closed');
 assert.equal(state.runs[0].resolution.execution_outcome,'unknown');assert.equal(state.plans[0].status,'consumed');
 assert.equal(await page.locator('#close-interruption').count(),0);
 assert.equal(await git(ui.directory,'rev-parse','HEAD'),head);assert.equal(await git(ui.directory,'status','--porcelain'),'');
 await assert.rejects(fs.stat(runDirectory),error=>error.code==='ENOENT');
 assert.equal(await git(ui.directory,'for-each-ref','--format=%(refname)','refs/heads/maintenance','refs/heads/integration'),'');
 await page.screenshot({path:path.join(directory,'registration-closed.png'),fullPage:true});
 proof.checks.push('explicit closure changes only journal; outcome unknown, plan consumed, no run files or Git changes');
 await ui.stop();ui=await startUI(ui.directory,{restart:true});await open();state=await ui.api('state');
 assert.equal(state.runs[0].status,'registration_closed');assert.equal(state.runs[0].reconciliation,null);
 await page.getByRole('button',{name:'运行 '+run.run_id.slice(0,8)+' · 登记已关闭，结果未知 · 历史只读',exact:true}).click();
 assert.equal(await page.locator('#close-interruption').count(),0);
 await click('inspect-interruption','核验中断证据');assert.equal(await page.locator('#close-interruption').count(),0);
 assert.equal(state.requests_remaining,0);
 proof.checks.push('restart retains closure and consumption; historical inspection grants no new action or budget');
 await fs.mkdir(runDirectory);await fs.writeFile(path.join(runDirectory,'run.json'),'invalid-late-evidence');
 await ui.stop();ui=await startUI(ui.directory,{restart:true});await open();state=await ui.api('state');
 assert.equal(state.runs[0].status,'resolution_conflict');
 await page.getByRole('button',{name:'运行 '+run.run_id.slice(0,8)+' · 关闭后出现证据变化 · 历史只读',exact:true}).click();
 assert.equal(await page.locator('#claim-run').count(),0);assert.equal(await page.locator('#cleanup').count(),0);
 await click('inspect-interruption','核验中断证据');assert.equal(await page.locator('#close-interruption').count(),0);
 proof.checks.push('late unreadable evidence becomes conflict after restart; no claim, cleanup or reclosure');
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:path.join(directory,'conflict-mobile.png'),fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
 proof.checks.push('390px responsive interrupted-run view');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);proof.checks.push('no page errors or external requests');
 proof.source_head_unchanged=await git(ui.directory,'rev-parse','HEAD')===head;
 proof.source_clean=await git(ui.directory,'status','--porcelain')==='';
 proof.plan_id=plan.id;proof.run_id=run.run_id;proof.final_status=state.runs[0].status;proof.passed=true;
}catch(error){proof.error=error.message;throw error;}
finally{
 if(browser)await browser.close();if(ui)await ui.stop();
 await fs.writeFile(path.join(directory,'browser-validation.json'),JSON.stringify(proof,null,2)+'\n');
 console.log(JSON.stringify({passed:proof.passed,checks:proof.checks.length,evidence:directory}));
}
