// Actual Pi/Chrome acceptance against a bounded synthetic loopback provider.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {startMock} from './test/mock-server.mjs';
import {python,project,startUI,git,operation} from './test/ui-case.mjs';
import {verifiedMemory} from './source-memory.mjs';

if(!process.argv[2]||!path.isAbsolute(process.argv[2]))throw new Error('NEW absolute evidence directory required');
const directory=path.resolve(process.argv[2]);await fs.mkdir(directory);
const sample=path.join(directory,'sample'),mock=await startMock();
const modelContext=process.argv.slice(3).includes('--model-context');
const {chromium}=createRequire(import.meta.url)(process.env.PI_UI_PLAYWRIGHT_PACKAGE||'playwright');
const proof={passed:false,checks:[],paid_model_requests:0,credential_reads:0,antigravity_calls:0};
let ui,browser;const origins=new Set();
const manifest=path.join(sample,'decisions.memory-relations.json');
async function launch(){
 ui=await startUI(sample,{mockUrl:mock.url,restart:true,requestBudget:2,auto:true,
  memoryRoot:path.join(sample,'memory-collections','decisions'),memoryIndex:path.join(sample,'decisions.memory.sqlite3'),
  memoryRelations:manifest,memoryRelationsContext:modelContext});origins.add(ui.base);
}
try{
 await promisify(execFile)(python,[path.join(project,'tests','gui_relations_fixture.py'),sample],
  {cwd:project,windowsHide:true,timeout:60000});
 await launch();const originalHead=await git(sample,'rev-parse','HEAD');
 browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1080}}),errors=[],external=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(!origins.has(new URL(request.url()).origin))external.push(request.url());});
 async function open(){await page.goto(ui.launch);await page.locator('#notice').filter({hasText:'已连接'}).waitFor();}
 async function done(text){await page.locator('#notice').filter({hasText:text}).waitFor();}
 await open();await page.locator('#memory-panel > summary').click();
 await page.locator('#memory-configuration .decision-relations > summary').click();
 const display=await page.locator('#memory-configuration .decision-relations').innerText();
 for(const text of ['Supersedes: new -> old','第 4 行','新条目 new · 已确认','旧条目 old · 已确认',
  '集合来源：current','集合来源：previous','关系文件 SHA256','声明行 SHA256','原清单 SHA256'])assert.ok(display.includes(text),text);
 assert.ok(!display.includes(manifest));
 proof.checks.push('both original confirmed statuses, member origins, ranges and declaration fingerprints are displayed without private manifest path');
 await page.locator('#issue').fill('addition');await page.locator('#memory-preview').click();await done('预览记忆出处完成');
 await page.locator('#memory-preview-result .decision-relations > summary').click();
 assert.match(await page.locator('#memory-preview-result .decision-relations').innerText(),/new → old/);
 assert.equal(mock.requests.length,0);assert.equal((await ui.api('state')).planning_attempts.length,0);
 proof.checks.push('direct relation preview uses zero model calls and zero budget reservations');
 await page.locator('#plan').click();await done('生成方案完成');
 let state=await ui.api('state');assert.equal(mock.requests.length,2);assert.equal(state.runs.length,0);
 assert.equal(state.plans[0].decision_relations.relations.length,1);
 await page.locator('#content .decision-relations > summary').click();
 assert.match(await page.locator('#content .decision-relations').innerText(),/集合来源：previous/);
 const modelEnvelopes=mock.requests.flatMap(r=>r.messages.filter(m=>m.role==='user')
  .map(m=>JSON.parse(typeof m.content==='string'?m.content:m.content[0].text)));
 assert.ok(modelEnvelopes.some(e=>e.source_memory?.schema_version===(modelContext?3:2)));
 for(const envelope of modelEnvelopes){
  verifiedMemory(envelope.source_memory,envelope.issue);
  if(modelContext){
   assert.equal(envelope.source_memory.source_schema_version,2);
   assert.equal(envelope.source_memory.decision_relations.relations.length,1);
   assert.equal(envelope.source_memory.context_sha256,state.plans[0].planning_evidence.memory_context_sha256);
   assert.equal(envelope.source_memory.decision_relations.runtime_authorized,false);
  }else assert.equal(envelope.source_memory?.decision_relations,undefined);
  assert.equal(envelope.decision_relations,undefined);
  if(!modelContext)assert.ok(!JSON.stringify(envelope).includes('explicit_supersedes_claim'));
  assert.ok(!JSON.stringify(envelope).includes(manifest));
 }
 const toolNames=mock.requests[0].tools.map(t=>t.function.name).sort();
 assert.deepEqual(toolNames,['propose_repair','read_source']);
 proof.checks.push(modelContext?'explicitly enabled real Pi receives context v3, verified claims and matching receipt with unchanged tools'
  :'actual Pi AgentSession produces a saved plan using two synthetic requests and unchanged tools/context schema; structured relation metadata is UI-only');
 const oldPlan=state.plans[0],original=await fs.readFile(manifest);
 await fs.writeFile(manifest,Buffer.concat([original,Buffer.from(' ')]));
 await page.locator('#refresh').click();await page.locator('#memory-configuration').getByText(/来源已失效/).waitFor();
 assert.equal(await page.locator('#plan').isDisabled(),true);assert.equal(await page.locator('#auto').isDisabled(),true);
 await page.locator('#approve-plan').click();await done('relations file changed');
 state=await ui.api('state');assert.equal(state.plans[0].status,'awaiting_approval');assert.equal(state.runs.length,0);
 assert.equal(mock.requests.length,2);assert.equal(state.requests_used,2);
 assert.match(await page.locator('#memory-preview-result').innerText(),/上次预览已过期/);
 proof.checks.push('manifest byte change disables planning/auto and blocks old approval without consumption or extra requests; old preview is visibly stale');
 await page.screenshot({path:path.join(directory,'relations-stale.png'),timeout:60000,animations:'disabled'});
 await fs.writeFile(manifest,original);await ui.stop();await launch();await open();
 state=await ui.api('state');assert.equal(state.requests_used,2);assert.equal(state.plans[0].claimed,false);
 await assert.rejects(operation(ui,'claim_plan',{plan_id:oldPlan.id,revision:oldPlan.revision}),/claim challenge changed/);
 proof.checks.push('process restart retains the used budget and rejects the previous claim revision');
 await page.locator('#refresh').click();await page.locator('#items button').first().click();
 await page.locator('#claim-plan').click();await done('核验并认领方案完成');
 await page.locator('#content .decision-relations > summary').click();
 assert.match(await page.locator('#content .decision-relations').innerText(),/Supersedes: new -> old/);
 await page.screenshot({path:path.join(directory,'relations-plan.png'),timeout:60000,animations:'disabled'});
 await page.locator('#approve-plan').click();await done('候选已验证');
 state=await ui.api('state');assert.equal(state.plans[0].status,'consumed');assert.equal(mock.requests.length,2);
 proof.checks.push('fresh claim and independent approval execute the saved plan with zero additional model requests');
 await page.locator('#confirm-cleanup').check();await page.locator('#cleanup').click();await done('候选已清理');
 assert.equal(await git(sample,'rev-parse','HEAD'),originalHead);assert.equal(await git(sample,'status','--porcelain'),'');
 proof.checks.push('candidate cleanup leaves the original Git HEAD and source files unchanged');
 const unhit=path.join(sample,'current','unhit.md'),unhitBytes=await fs.readFile(unhit);
 await fs.writeFile(unhit,Buffer.concat([unhitBytes,Buffer.from('\nchanged unhit source')]));
 await assert.rejects(operation(ui,'memory_preview',{issue:'addition'}),/changed/);
 assert.equal(mock.requests.length,2);assert.equal((await ui.api('state')).source_memory.status,'invalid');
 await fs.writeFile(unhit,unhitBytes);await page.locator('#refresh').click();
 await page.locator('#memory-configuration').getByText('来源与索引已核验',{exact:true}).waitFor({state:'attached'});
 proof.checks.push('unhit original member change invalidates relationship evidence before retrieval');
 await page.setViewportSize({width:390,height:844});
 if(await page.locator('#memory-panel').getAttribute('open')===null)await page.locator('#memory-panel > summary').click();
 if(await page.locator('#memory-configuration .decision-relations').getAttribute('open')===null)
  await page.locator('#memory-configuration .decision-relations > summary').click();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
 await page.locator('#memory-configuration .decision-relations').screenshot({path:path.join(directory,'relations-mobile.png'),animations:'disabled'});
 proof.checks.push('390px relationship display has no page overflow');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);proof.checks.push('no browser errors or external browser requests');
 proof.model_context_enabled=modelContext;proof.synthetic_http_requests=mock.requests.length;proof.source_unchanged=true;proof.passed=true;
}catch(error){proof.error=error.message;throw error;}
finally{
 if(browser)await browser.close();if(ui)await ui.stop();await mock.close();
 await fs.writeFile(path.join(directory,'browser-validation.json'),JSON.stringify(proof,null,2)+'\n');
 console.log(JSON.stringify({passed:proof.passed,checks:proof.checks.length,evidence:directory}));
}
