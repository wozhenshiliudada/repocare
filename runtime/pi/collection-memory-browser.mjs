// Bounded browser acceptance of the self-authored collection, not a live model benchmark.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {startMock} from './test/mock-server.mjs';
import {python,project,startUI,git} from './test/ui-case.mjs';

if(!process.argv[2]||!path.isAbsolute(process.argv[2]))throw new Error('NEW absolute evidence directory required');
const directory=path.resolve(process.argv[2]);await fs.mkdir(directory);
const sample=path.join(directory,'sample'),mock=await startMock();
const {chromium}=createRequire(import.meta.url)(process.env.PI_UI_PLAYWRIGHT_PACKAGE||'playwright');
const proof={passed:false,checks:[],paid_model_requests:0,credential_reads:0,antigravity_calls:0};
let ui,browser;const origins=new Set();
async function launch(){
 ui=await startUI(sample,{mockUrl:mock.url,restart:true,requestBudget:2,
  memoryRoot:path.join(sample,'memory-collections','mixed'),memoryIndex:path.join(sample,'mixed.memory.sqlite3')});
 origins.add(ui.base);
}
try{
 await promisify(execFile)(python,[path.join(project,'tests','pi_collection_memory.py'),
  '--directory',sample,'--mode','fixture'],{cwd:project,windowsHide:true,timeout:120000});
 await launch();const originalHead=await git(sample,'rev-parse','HEAD');
 browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1080}}),errors=[],external=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(!origins.has(new URL(request.url()).origin))external.push(request.url());});
 async function open(){await page.goto(ui.launch);await page.locator('#notice').filter({hasText:'已连接'}).waitFor();}
 async function done(text){await page.locator('#notice').filter({hasText:text}).waitFor();}
 await open();await page.locator('#memory-panel > summary').click();await page.locator('#issue').fill('Run addition');
 const config=await page.locator('#memory-configuration').innerText();
 for(const name of ['已确认','工程提议','待定','历史报告声称','集合来源：docs','集合来源：a','集合来源：b'])assert.ok(config.includes(name));
 proof.checks.push('five selected sources expose separate member IDs, original entry IDs and original decision classes');
 await page.locator('#memory-preview').click();await done('预览记忆出处完成');
 await page.locator('#memory-preview-result summary').click();
 const preview=await page.locator('#memory-preview-result').innerText();
 assert.match(preview,/集合来源：a/);assert.match(preview,/集合来源：b/);assert.match(preview,/原清单 SHA256/);
 assert.equal(mock.requests.length,0);assert.equal((await ui.api('state')).planning_attempts.length,0);
 proof.checks.push('mixed query preview distinguishes two report.md origins without model calls or reservations');
 await page.locator('#plan').click();await done('生成方案完成');
 let state=await ui.api('state');assert.equal(mock.requests.length,2);assert.equal(state.runs.length,0);
 await page.locator('#content .memory-citations summary').click();
 assert.match(await page.locator('#content .memory-citations').innerText(),/集合来源：docs/);
 const unhit=path.join(sample,'documents','unhit.md'),original=await fs.readFile(unhit);
 await fs.writeFile(unhit,Buffer.concat([original,Buffer.from('\nchanged unhit pending source')]));
 await page.locator('#refresh').click();await page.locator('#memory-configuration').getByText(/来源已失效/).waitFor();
 assert.equal(await page.locator('#plan').isDisabled(),true);
 await page.locator('#approve-plan').click();await done('Collection member sources changed');
 state=await ui.api('state');assert.equal(state.plans[0].status,'awaiting_approval');assert.equal(state.runs.length,0);
 assert.equal(mock.requests.length,2);assert.equal(state.requests_used,2);
 proof.checks.push('unhit source change blocks old approval before plan consumption and new model calls');
 // HTTP job auditing is expected; compare the exact consumed state rather than all DB bytes.
 assert.equal(state.plans[0].status,'awaiting_approval');
 await page.screenshot({path:path.join(directory,'mixed-memory-stale.png'),timeout:60000,animations:'disabled'});
 await fs.writeFile(unhit,original);await ui.stop();await launch();await open();
 state=await ui.api('state');assert.equal(state.requests_used,2);assert.equal(state.plans[0].claimed,false);
 await page.locator('#items button').first().click();await page.locator('#claim-plan').click();await done('核验并认领方案完成');
 await page.locator('#approve-plan').click();await done('候选已验证');
 state=await ui.api('state');assert.equal(state.plans[0].status,'consumed');assert.equal(mock.requests.length,2);
 await page.locator('#content .memory-citations summary').click();
 assert.match(await page.locator('#content .memory-citations').innerText(),/历史运行：/);
 proof.checks.push('restart retains used budget; fresh claim and approval preserve collection origins during zero-request saved execution');
 await page.screenshot({path:path.join(directory,'mixed-memory-run.png'),timeout:60000,animations:'disabled'});
 await page.locator('#confirm-cleanup').check();await page.locator('#cleanup').click();await done('候选已清理');
 assert.equal(await git(sample,'rev-parse','HEAD'),originalHead);assert.equal(await git(sample,'status','--porcelain'),'');
 proof.checks.push('cleanup retains source Git baseline and independently pinned original reports');
 await page.setViewportSize({width:390,height:844});await page.locator('#memory-panel > summary').click();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
 await page.screenshot({path:path.join(directory,'mixed-memory-mobile.png'),timeout:60000,animations:'disabled'});
 proof.checks.push('390px provenance display has no page overflow');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);proof.checks.push('no browser errors or external requests');
 proof.synthetic_http_requests=mock.requests.length;proof.source_unchanged=true;proof.passed=true;
}catch(error){proof.error=error.message;throw error;}
finally{
 if(browser)await browser.close();if(ui)await ui.stop();await mock.close();
 await fs.writeFile(path.join(directory,'browser-validation.json'),JSON.stringify(proof,null,2)+'\n');
 console.log(JSON.stringify({passed:proof.passed,checks:proof.checks.length,evidence:directory}));
}
