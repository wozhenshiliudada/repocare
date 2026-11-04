// Actual browser acceptance on new fixed fixtures only. No paid model or credential discovery.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createRequire} from 'node:module';
import {startUI, git, python, project} from './test/ui-case.mjs';

if(!process.argv[2]||!path.isAbsolute(process.argv[2]))throw new Error('A NEW absolute evidence directory is required');
const directory=path.resolve(process.argv[2]);await fs.mkdir(directory);
const {chromium}=createRequire(import.meta.url)(process.env.PI_UI_PLAYWRIGHT_PACKAGE || 'playwright');
const proof={passed:false,browser:'Chromium / installed Chrome',checks:[],paid_model_requests:0,antigravity_calls:0};
let ui,browser;
try{
 ui=await startUI(path.join(directory,'sample'));browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1080}}),errors=[],external=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(!request.url().startsWith(ui.base+'/'))external.push(request.url());});
 async function open(){await page.goto(ui.launch);await page.locator('#notice').filter({hasText:'已连接'}).waitFor();}
 async function click(id,label){
  await page.locator('#'+id).click();
  await page.waitForFunction(value=>document.querySelector('#notice').textContent.startsWith(value),label+'完成',{timeout:90000});
  assert.equal(await page.locator('#notice').evaluate(el=>el.classList.contains('error')),false);
 }
 async function select(plan){
  await page.getByRole('button',{name:'方案 '+plan.id.slice(0,8)+' · 方案已处理 · 历史只读',exact:true}).click();
  await page.locator('#linked-run').click();
 }
 const head=await git(ui.directory,'rev-parse','HEAD');
 await open();await page.locator('#issue').fill('修复加法并保留原测试');await click('plan','生成方案');
 const plan=(await ui.api('state')).plans[0];
 const oldToken=new URLSearchParams(new URL(ui.launch).hash.slice(1)).get('session');
 await ui.stop();
 await assert.rejects(promisify(execFile)(python,[path.join(project,'tests','repair_crash.py'),ui.directory,'--ui'],
   {cwd:project,windowsHide:true,timeout:30000}),error=>error.code===94);
 ui=await startUI(ui.directory,{restart:true});await open();await select(plan);
 let state=await ui.api('state');const run=state.runs[0];
 assert.equal(run.repair_finished,false);assert.equal(run.repair_execution.phase,'workspace_ready');
 assert.equal((await fetch(ui.base+'/api/state',{headers:{Authorization:'Bearer '+oldToken}})).status,401);
 assert.equal(await page.locator('#claim-run').count(),0);assert.equal(await page.locator('#cleanup').count(),0);
 assert.equal(await page.locator('#publish').count(),0);assert.equal(await page.locator('#abort-repair').count(),0);
 proof.checks.push('actual exit after approved patch checkpoint retains candidate; restart shows execution view without cleanup/claim/publication');
 await click('inspect-repair','核验修复执行');state=await ui.api('state');
 assert.equal(state.runs[0].repair_inspection.can_abort,true);assert.equal(await page.locator('#abort-repair').isDisabled(),true);
 await page.screenshot({path:path.join(directory,'repair-preview.png'),fullPage:true});
 proof.checks.push('fixed ownership and recorded process preview; explicit unchecked abort required');
 const workspace=path.join(ui.directory,'runs',run.run_id,'workspace');
 const extra=path.join(workspace,'__pycache__');await fs.mkdir(extra);
 const human=path.join(extra,'human.pyc');await fs.writeFile(human,'keep human ignored file');
 await page.locator('#confirm-abort-repair').check();await page.locator('#abort-repair').click();
 await page.waitForFunction(()=>document.querySelector('#notice').classList.contains('error'),null,{timeout:30000});
 assert.match(await page.locator('#notice').innerText(),/evidence or session changed/);
 assert.equal(await fs.readFile(human,'utf8'),'keep human ignored file');
 await click('inspect-repair','核验修复执行');assert.equal(await page.locator('#abort-repair').count(),0);
 assert.equal((await ui.api('state')).runs[0].repair_inspection.can_abort,false);
 proof.checks.push('ignored human file invalidates preview, is preserved, and fresh blocked preview offers no abort');
 // Remove exactly the acceptance-script-created file and directory; no recursive cleanup.
 await fs.unlink(human);await fs.rmdir(extra);await click('inspect-repair','核验修复执行');
 assert.equal(await page.locator('#abort-repair').isDisabled(),true);
 await ui.stop();ui=await startUI(ui.directory,{restart:true});await open();await select(plan);
 assert.equal(await page.locator('#abort-repair').count(),0);
 await click('inspect-repair','核验修复执行');assert.equal(await page.locator('#abort-repair').isDisabled(),true);
 proof.checks.push('restart discards old preview and checkbox; fresh inspection and explicit consent required');
 await page.locator('#confirm-abort-repair').check();await click('abort-repair','中止中断修复');state=await ui.api('state');
 assert.equal(state.runs[0].status,'repair_aborted');assert.equal(state.runs[0].repair_execution.phase,'aborted');
 assert.equal(state.runs[0].source_unchanged,true);assert.equal(state.plans[0].status,'consumed');
 assert.equal(await page.locator('#cleanup').count(),0);assert.equal(await page.locator('#publish').count(),0);
 await assert.rejects(fs.stat(workspace),error=>error.code==='ENOENT');
 assert.equal(await git(ui.directory,'for-each-ref','--format=%(refname)','refs/heads/maintenance','refs/heads/integration'),'');
 const execution=JSON.parse(await fs.readFile(path.join(path.dirname(workspace),'repair-execution.json'),'utf8'));
 assert.equal(execution.abort_authorizations.length,1);assert.equal(execution.abort_authorization.inspection.execution_outcome,'unknown');
 assert.ok(execution.process_facts.some(item=>item.kind==='baseline_test'&&item.returncode===1));
 await page.screenshot({path:path.join(directory,'repair-aborted.png'),fullPage:true});
 proof.checks.push('explicit abort removes only unchanged owned workspace/ref, keeps report/process/authorization facts, plan consumed');
 await ui.stop();ui=await startUI(ui.directory,{restart:true});await open();state=await ui.api('state');await select(plan);
 assert.equal(state.runs[0].status,'repair_aborted');assert.equal(await page.locator('#abort-repair').count(),0);
 assert.equal(state.requests_used,0);assert.equal(state.requests_reserved_unknown,0);
 proof.checks.push('terminal abort survives restart; no model/test replay, publication or budget reset');
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(directory,'repair-mobile.png'),fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
 proof.checks.push('390px responsive view');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);proof.checks.push('no page errors or external requests');
 proof.source_clean=await git(ui.directory,'status','--porcelain')==='';proof.source_head_unchanged=await git(ui.directory,'rev-parse','HEAD')===head;
 assert.ok(proof.source_clean&&proof.source_head_unchanged);proof.run_id=run.run_id;proof.final_status='repair_aborted';proof.passed=true;
}catch(error){proof.error=error.message;throw error;}
finally{
 if(browser)await browser.close();if(ui)await ui.stop();
 await fs.writeFile(path.join(directory,'browser-validation.json'),JSON.stringify(proof,null,2)+'\n');
 console.log(JSON.stringify({passed:proof.passed,checks:proof.checks.length,evidence:directory}));
}
