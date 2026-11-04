import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {createServer} from 'vite';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PI_UI_PLAYWRIGHT_PACKAGE||'playwright');
const output=process.argv[2];if(!output||!path.isAbsolute(output))throw Error('New absolute evidence directory required');
await fs.mkdir(output);
const server=await createServer({configFile:false,server:{host:'127.0.0.1',port:0},logLevel:'error'});
await server.listen();const browser=await chromium.launch({headless:true,...(process.env.PI_UI_BROWSER_EXECUTABLE?{executablePath:process.env.PI_UI_BROWSER_EXECUTABLE}:{channel:'msedge'})});
const report={synthetic:true,real_model_calls:0,real_github_writes:0,checks:[],errors:[],passed:false};
const page=await browser.newPage({viewport:{width:1200,height:950}});
page.on('pageerror',e=>report.errors.push(e.message));
const origin=`http://127.0.0.1:${server.httpServer.address().port}`;
const fixture=()=>page.evaluate(()=>window.buttonFixture);
const control=(name,...args)=>page.evaluate(({name,args})=>window.buttonFixture[name](...args),{name,args});
const check=name=>report.checks.push(name);
async function reset(){await page.goto(origin+'/test/async-button-browser.html');await page.getByTestId('action-A').waitFor();}
async function busy(button){await button.locator('[class=button-wait-label]').waitFor({timeout:2000});assert.equal(await button.getAttribute('aria-busy'),'true');assert.equal(await button.isDisabled(),true);assert.equal(await button.evaluate(el=>getComputedStyle(el).opacity),'1');}
async function idle(button){await page.waitForFunction(()=>!document.querySelector('button[data-pending]'));assert.equal(await button.getAttribute('aria-busy'),null);}
try{
 await reset();const a=page.getByTestId('action-A'),b=page.getByTestId('action-B');
 // Native clicks dispatched in one frame exercise the synchronous duplicate guard.
 await a.evaluate(el=>{el.click();el.click();});await busy(a);assert.equal((await fixture()).calls.length,1);
 assert.equal(await b.getAttribute('data-pending'),null);check('immediate feedback / duplicate click / sibling ownership');
 await control('resolve',true);await page.getByText('已提交操作 · 正在读取原任务回执。',{exact:false}).waitFor();await busy(a);check('button remains waiting after HTTP response while job runs');
 await page.getByRole('region',{name:'共享操作按钮'}).screenshot({path:path.join(output,'queued-job.png')});
 await control('complete',false);await idle(a);assert.equal(await a.isEnabled(),true);check('matching completed receipt restores original button');
 await b.click();await busy(b);assert.equal(await a.getAttribute('data-pending'),null);await control('resolve',false);await idle(b);check('next action does not revive previous button spinner');

 await reset();await a.click();await control('reject',false);await idle(a);assert.equal(await a.isEnabled(),true);await page.getByText('合成权限拒绝，未执行',{exact:true}).waitFor();check('definite failure restores button and retains error');
 await a.click();await busy(a);assert.equal((await fixture()).calls.length,2);await control('resolve',true);await control('complete',true);await idle(a);await page.getByText('合成后台任务失败',{exact:true}).waitFor();check('explicit retry and failed background receipt');

 await reset();await a.click();await control('reject',true);await page.waitForFunction(()=>document.querySelector('[data-pending=uncertain]'));
 assert.match(await a.textContent(),/等待核验/);assert.equal(await a.getAttribute('aria-busy'),null);assert.equal(await a.isDisabled(),true);
 await page.getByText('操作结果待核验，请核对原记录，勿重复提交。',{exact:false}).waitFor();
 await a.evaluate(el=>el.click());assert.equal((await fixture()).calls.length,1);await page.getByRole('region',{name:'共享操作按钮'}).screenshot({path:path.join(output,'unknown-result.png')});check('unknown result stays on source button without spinning or replay');

 await reset();const memory=page.getByRole('region',{name:'实际记忆页面'}),input=memory.getByLabel('记忆查询');
 await input.fill('合成查询');await input.press('Enter');const query=memory.getByRole('button',{name:/查询记忆/});await busy(query);assert.equal((await fixture()).calls.length,1);await query.locator('xpath=ancestor::section[1]').screenshot({path:path.join(output,'memory-query.png')});check('real memory form Enter keeps native submit and inline wait');
 await control('resolve',true);await busy(query);await control('complete');await idle(query);check('memory query waits for queued receipt');

 await reset();const issue=page.getByRole('region',{name:'实际Issue入口'});await issue.getByPlaceholder('例如 6').fill('7');
 const investigate=issue.getByRole('button',{name:/^读取详情/});await investigate.click();await busy(investigate);
 await control('resolve',true);await busy(investigate);await control('complete');await idle(investigate);check('real Issue details button spans submission and queued job');

 await reset();const ci=page.getByRole('region',{name:'CI精确证据调查'}),read=ci.getByRole('button',{name:/读取 \/ 刷新只读证据/});
 await read.click();await busy(read);await control('resolve',false);await idle(read);check('read-only CI source button displays its own wait');
 const jobs=await ci.getByLabel('选择CI Job').locator('option').allTextContents();assert.ok(jobs.length>1);
 await ci.getByLabel('选择CI Job').selectOption({index:1});const log=ci.getByRole('button',{name:/^读取选定 Job 日志/});
 await log.click();await busy(log);assert.equal(await read.getAttribute('data-pending'),null);assert.doesNotMatch(await read.textContent(),/正在读取|处理中/);await control('resolve',false);await idle(log);check('Job log read does not mark Run refresh button as loading');

 await reset();const plain=page.getByTestId('plain-read');await plain.click();await busy(plain);await control('reject',false);await idle(plain);assert.equal(await plain.isEnabled(),true);check('independent async read handles failure without stuck spinner');

 await reset();await page.setViewportSize({width:320,height:850});await page.emulateMedia({reducedMotion:'reduce'});
 const progress=page.getByRole('region',{name:'Issue 进度评论'}),preview=progress.getByRole('button',{name:/^预览此阶段的评论/});await preview.click();await busy(preview);
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2);assert.equal(overflow,false);
 assert.equal(await preview.locator('.button-spinner').evaluate(el=>getComputedStyle(el).animationName),'none');
 await preview.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,'mobile-button.png')});await control('resolve',true);await busy(preview);await control('complete');await idle(preview);check('mobile progress action / reduced motion / queued callback feedback');
 assert.deepEqual(report.errors,[]);report.passed=true;
}catch(e){report.errors.push(e.stack);report.diagnostic=await page.evaluate(()=>({calls:window.buttonFixture?.calls,buttons:[...document.querySelectorAll('button')].filter(e=>e.textContent.includes('查询记忆')).map(e=>({html:e.outerHTML}))}));await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});}
finally{await fs.writeFile(path.join(output,'result.json'),JSON.stringify(report,null,2));await browser.close();await server.close();console.log(JSON.stringify(report));if(!report.passed)process.exitCode=1;}
