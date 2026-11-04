import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {startMock} from './test/mock-server.mjs';
import {python,project,startUI,git} from './test/ui-case.mjs';

if(!process.argv[2]||!path.isAbsolute(process.argv[2]))throw new Error('A NEW absolute evidence directory is required');
const directory=path.resolve(process.argv[2]);await fs.mkdir(directory);
const {chromium}=createRequire(import.meta.url)(process.env.PI_UI_PLAYWRIGHT_PACKAGE||'playwright');
const proof={passed:false,checks:[],paid_model_requests:0,credential_reads:0,antigravity_calls:0};
const exec=promisify(execFile),mock=await startMock(),failing=await startMock({failure:true});
let ui,browser;const origins=new Set();
async function fixture(name,url){
 const sample=path.join(directory,name);
 await exec(python,[path.join(project,'tests','pi_ui_memory.py'),'--directory',sample,'--mode','fixture'],{cwd:project,windowsHide:true});
 return launch(sample,url);
}
async function launch(sample,url){
 const result=await startUI(sample,{mockUrl:url,restart:true,requestBudget:4,
  memoryRoot:path.join(sample,'documents'),memoryIndex:path.join(sample,'private.memory.sqlite3')});
 origins.add(result.base);return result;
}
try{
 ui=await fixture('approval',mock.url);const originalHead=await git(ui.directory,'rev-parse','HEAD');
 browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1080}}),errors=[],external=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(!origins.has(new URL(request.url()).origin))external.push(request.url());});
 async function open(){await page.goto(ui.launch);await page.locator('#notice').filter({hasText:'已连接'}).waitFor();}
 async function done(text){await page.locator('#notice').filter({hasText:text}).waitFor();}
 await open();await page.locator('#issue').fill('addition bug');await page.locator('#memory-panel > summary').click();
 const config=await page.locator('#memory-configuration').innerText();assert.match(config,/已确认/);assert.match(config,/历史报告声称/);
 await page.locator('#memory-preview').click();await done('预览记忆出处完成');
 assert.equal(mock.requests.length,0);let state=await ui.api('state');assert.equal(state.planning_attempts.length,0);
 await page.locator('#memory-preview-result summary').click();assert.match(await page.locator('#memory-preview-result').innerText(),/原文 SHA256/);
 proof.checks.push('actual preview click shows classified source ranges and hashes without model requests or budget reservation');
 await page.locator('#plan').click();await done('生成方案完成');state=await ui.api('state');assert.equal(mock.requests.length,2);assert.equal(state.runs.length,0);
 await page.locator('#content .memory-citations summary').click();assert.match(await page.locator('#content .memory-citations').innerText(),/上下文回执/);
 await page.screenshot({path:path.join(directory,'memory-plan.png'),fullPage:true});
 proof.checks.push('real Pi plan exposes original classified citations and context receipt; unapproved plan creates no candidate');
 const source=path.join(ui.directory,'documents','rule.md'),original=await fs.readFile(source);
 await fs.writeFile(source,Buffer.concat([original,Buffer.from('\nchanged source')]));await page.locator('#refresh').click();
 await page.locator('#memory-configuration').getByText(/来源已失效/).waitFor();assert.equal(await page.locator('#plan').isDisabled(),true);
 await page.locator('#approve-plan').click();await done('Memory sources changed');state=await ui.api('state');
 assert.equal(state.plans[0].status,'awaiting_approval');assert.equal(state.runs.length,0);assert.equal(mock.requests.length,2);
 proof.checks.push('changed source displayed as invalid; both new planning and old edit approval blocked before consumption');
 await page.screenshot({path:path.join(directory,'memory-stale.png'),fullPage:true});
 await fs.writeFile(source,original);await ui.stop();ui=await launch(ui.directory,mock.url);await open();
 state=await ui.api('state');assert.equal(state.requests_used,2);assert.equal(state.plans[0].claimed,false);assert.equal(mock.requests.length,2);
 await page.locator('#items button').first().click();await page.locator('#claim-plan').click();await done('核验并认领方案完成');
 await page.locator('#approve-plan').click();await done('候选已验证');state=await ui.api('state');
 assert.equal(state.plans[0].status,'consumed');assert.equal(state.requests_used,2);assert.equal(mock.requests.length,2);
 await page.locator('#content .memory-citations summary').click();assert.match(await page.locator('#content .memory-citations').innerText(),/历史报告声称/);
 proof.checks.push('process restart keeps budget and history readonly; new claim and approval run saved plan with zero extra model requests');
 await page.locator('#confirm-cleanup').check();await page.locator('#cleanup').click();await done('候选已清理');
 assert.equal(await git(ui.directory,'rev-parse','HEAD'),originalHead);assert.equal(await git(ui.directory,'status','--porcelain'),'');
 assert.equal(await page.evaluate(()=>window.memoryInjected),undefined);assert.equal(await page.locator('img').count(),0);
 proof.checks.push('candidate cleanup preserves source HEAD/files; historical script-shaped text creates no executable DOM');
 await page.setViewportSize({width:390,height:844});await page.locator('#memory-panel > summary').click();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
 await page.screenshot({path:path.join(directory,'memory-mobile.png'),fullPage:true});proof.checks.push('390px classified-memory and approval layout has no page overflow');
 await ui.stop();ui=await fixture('failure',failing.url);await open();await page.locator('#issue').fill('addition bug');
 await page.locator('#plan').click();await done('Pi planning failed');state=await ui.api('state');assert.equal(state.requests_reserved_unknown,4);
 await ui.stop();ui=await launch(ui.directory,failing.url);await open();state=await ui.api('state');
 assert.equal(state.requests_reserved_unknown,4);assert.equal(state.requests_remaining,0);
 await page.locator('#memory-panel > summary').click();await page.locator('#issue').fill('addition bug');
 await page.locator('#memory-preview').click();await done('预览记忆出处完成');assert.equal(failing.requests.length,1);
 await page.locator('#plan').click();await done('budget exhausted or reserved');assert.equal(failing.requests.length,1);
 assert.equal((await ui.api('state')).planning_attempts.length,1);
 proof.checks.push('synthetic provider failure retains unknown allowance through process restart; preview and new actions cannot refund it');
 await page.screenshot({path:path.join(directory,'memory-unknown-budget.png'),fullPage:true});
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);proof.checks.push('no browser errors or external requests');
 proof.synthetic_http_requests=mock.requests.length+failing.requests.length;proof.source_unchanged=true;proof.passed=true;
}catch(error){proof.error=error.message;throw error;}
finally{
 if(browser)await browser.close();if(ui)await ui.stop();await mock.close();await failing.close();
 await fs.writeFile(path.join(directory,'browser-validation.json'),JSON.stringify(proof,null,2)+'\n');
 console.log(JSON.stringify({passed:proof.passed,checks:proof.checks.length,evidence:directory}));
}
