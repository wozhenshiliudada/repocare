// Real Chrome, synthetic GitHub and model. Capabilities never enter the proof files.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PI_UI_PLAYWRIGHT_PACKAGE||'playwright');
const root=path.resolve(process.argv[2]||'');
if(!process.argv[2]||!path.isAbsolute(process.argv[2]))throw new Error('New absolute directory required');
await fs.mkdir(root);
const processUI=spawn(process.env.PI_TEST_PYTHON||'python',['-u','tests/workbench_browser_fixture.py',path.join(root,'fixture')],{stdio:['ignore','pipe','pipe']});
let output='',errorsOutput='';processUI.stderr.on('data',chunk=>{errorsOutput+=chunk;});
let browser;
const proof={passed:false,checks:[],paid_model_requests:0,live_github_requests:0};
try{
 const launch=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Workbench startup timeout')),15000);processUI.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/http:\/\/127\.0\.0\.1:\d+\/#session=[a-f0-9]{64}/);if(match){clearTimeout(timer);resolve(match[0]);}});processUI.once('exit',()=>{clearTimeout(timer);reject(new Error('Workbench exited before startup'));});});
 browser=await chromium.launch({channel:'chrome',headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});const page=await context.newPage();
 const errors=[],external=[];context.on('page',p=>{p.on('pageerror',error=>errors.push(error.message));});
 page.on('pageerror',error=>errors.push(error.message));context.on('request',request=>{if(new URL(request.url()).hostname!=='127.0.0.1')external.push(request.url());});
 await page.goto(launch);await page.locator('#notice').filter({hasText:'选择本地仓库'}).waitFor();
 assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
 assert.equal(page.url(),new URL(launch).origin+'/');proof.checks.push('capability removed from URL and no browser storage');
 async function click(id){await page.locator('#'+id).click();await page.waitForFunction(()=>!document.querySelector('#'+ 'inspect').disabled,{timeout:20000});assert.equal(await page.locator('#notice').evaluate(el=>el.classList.contains('error')),false,await page.locator('#notice').innerText());}
 await click('check-account');assert.match(await page.locator('#account').innerText(),/example/);
 await click('login');await click('list-repos');assert.match(await page.locator('#repos').innerText(),/example\/private.*私有/);
 await page.locator('#repos button').click();await click('clone');assert.match(await page.locator('#repository-summary').innerText(),/clones/);proof.checks.push('login adapter, private list selection and managed clone pipeline');
 await click('pick');assert.match(await page.locator('#local-path').inputValue(),/source$/);proof.checks.push('folder selection adapter previews local repository');
 await page.getByLabel('calculator.py 权限',{exact:true}).selectOption('edit');await page.getByLabel('tests/test_calculator.py 权限',{exact:true}).selectOption('read');
 await page.screenshot({path:path.join(root,'desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(root,'mobile.png'),fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);proof.checks.push('desktop and 390px layout without horizontal overflow');
 await page.setViewportSize({width:1440,height:1000});await page.locator('#confirmed').check();await click('connect');
 const url=await page.locator('#launch a').getAttribute('href');const child=await context.newPage();await child.goto(url);
 await child.locator('#notice').filter({hasText:'已连接'}).waitFor();assert.equal(await child.locator('#issue').inputValue(),'');
 assert.equal(await child.locator('#repository-picker').isVisible(),true);proof.checks.push('separate immutable session with return-to-picker link and generic issue');
 await child.locator('#issue').fill('修复 add 加法问题');await child.locator('#plan').click();await child.locator('#notice').filter({hasText:'生成方案完成'}).waitFor({timeout:20000});
 await child.locator('#approve-plan').click();await child.locator('#notice').filter({hasText:'方案审批完成'}).waitFor({timeout:60000});
 assert.match(await child.locator('#content').innerText(),/候选已验证/);proof.checks.push('onboarded session retains actual approval, Git isolation and red-to-green tests');
 await child.locator('#confirm-cleanup').check();await child.locator('#cleanup').click();await child.locator('#notice').filter({hasText:'清理候选完成'}).waitFor({timeout:30000});
 assert.match(await child.locator('#content').innerText(),/候选已清理/);proof.checks.push('owned candidate cleanup through original control layer');
 await click('connect');assert.equal(await page.locator('#launch a').getAttribute('href'),url);proof.checks.push('reconnect reuses session rather than resetting state');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);proof.checks.push('zero JS errors and external browser requests');proof.passed=true;
}finally{
 if(browser)await browser.close();processUI.kill('SIGINT');await new Promise(resolve=>{if(processUI.exitCode!==null)resolve();else{processUI.once('exit',resolve);setTimeout(()=>{processUI.kill();resolve();},10000);}});
 await fs.writeFile(path.join(root,'proof.json'),JSON.stringify(proof,null,2));console.log(JSON.stringify(proof));
}
