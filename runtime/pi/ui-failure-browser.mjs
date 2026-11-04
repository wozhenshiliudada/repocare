// A single synthetic failure and budget refusal, using a new fixture and actual Chrome clicks.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {startUI,git} from './test/ui-case.mjs';
import {startMock} from './test/mock-server.mjs';

if(!process.argv[2]||!path.isAbsolute(process.argv[2]))throw new Error('A NEW absolute evidence directory is required');
const directory=path.resolve(process.argv[2]);await fs.mkdir(directory);
const {chromium}=createRequire(import.meta.url)(process.env.PI_UI_PLAYWRIGHT_PACKAGE||'playwright');
const proof={passed:false,browser:'Chromium / installed Chrome',checks:[],paid_model_requests:0,credential_reads:0,antigravity_calls:0};
let ui,browser;const mock=await startMock({failure:true});
try{
 ui=await startUI(path.join(directory,'sample'),{mockUrl:mock.url});const head=await git(ui.directory,'rev-parse','HEAD');
 browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1080}});
 const errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(!r.url().startsWith(ui.base+'/'))external.push(r.url());});
 async function open(){await page.goto(ui.launch);await page.locator('#notice').filter({hasText:'已连接'}).waitFor();}
 await open();await page.locator('#plan').click();await page.locator('#notice').filter({hasText:'Pi planning failed'}).waitFor();
 let state=await ui.api('state');assert.equal(mock.requests.length,1);assert.equal(state.plans.length,0);assert.equal(state.runs.length,0);
 assert.equal(state.requests_reserved_unknown,2);assert.equal(state.requests_remaining,0);
 assert.equal(state.planning_attempts[0].evidence.failure_category,'provider_error');
 proof.checks.push('actual plan button invokes real Pi against synthetic 500; no accepted plan, workspace or test');
 await page.getByText('操作记录',{exact:true}).click();await page.locator('.planning-failure summary').click();
 const details=await page.locator('.planning-failure').innerText();
 assert.match(details,/消耗未知/);assert.match(details,/已观察到的调用开始：1/);assert.match(details,/服务端实际消费或计费/);
 assert.match(details,/agent_end/);assert.doesNotMatch(details,/syntheticNotARealKey|syntheticCredentialNotReal/);
 await page.screenshot({path:path.join(directory,'failure-evidence.png'),fullPage:true});
 proof.checks.push('visible filtered end events and invocation count; unknown consumption and reserved allowance stated');
 const old=ui.launch;await ui.stop();ui=await startUI(ui.directory,{mockUrl:mock.url,restart:true});await open();
 assert.notEqual(ui.launch,old);state=await ui.api('state');assert.equal(state.requests_reserved_unknown,2);
 assert.equal(state.planning_attempts[0].evidence.requests_started,1);assert.equal(mock.requests.length,1);
 proof.checks.push('restart preserves filtered evidence and unknown reservation without replay');
 await page.locator('#plan').click();await page.locator('#notice').filter({hasText:'budget exhausted or reserved'}).waitFor();
 assert.equal(mock.requests.length,1);assert.equal((await ui.api('state')).planning_attempts.length,1);
 proof.checks.push('fresh plan action cannot bypass unknown reservation; no automatic retry or budget reset');
 await page.getByText('操作记录',{exact:true}).click();await page.locator('.planning-failure summary').click();
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(directory,'failure-mobile.png'),fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);proof.checks.push('390px layout without page overflow');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);proof.checks.push('no page errors or external browser requests');
 proof.source_clean=await git(ui.directory,'status','--porcelain')==='';proof.source_head_unchanged=await git(ui.directory,'rev-parse','HEAD')===head;
 assert.ok(proof.source_clean&&proof.source_head_unchanged);proof.observed_synthetic_http_requests=mock.requests.length;proof.passed=true;
}catch(error){proof.error=error.message;throw error;}
finally{
 if(browser)await browser.close();if(ui)await ui.stop();await mock.close();
 await fs.writeFile(path.join(directory,'browser-validation.json'),JSON.stringify(proof,null,2)+'\n');
 console.log(JSON.stringify({passed:proof.passed,checks:proof.checks.length,evidence:directory}));
}
