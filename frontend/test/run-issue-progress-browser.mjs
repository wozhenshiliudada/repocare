import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {createServer} from 'vite';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PI_UI_PLAYWRIGHT_PACKAGE||'playwright');
const target=process.argv[2];if(!target||!path.isAbsolute(target))throw Error('New absolute evidence directory required');
await fs.mkdir(target);
const server=await createServer({configFile:false,server:{host:'127.0.0.1',port:0},logLevel:'error'});
await server.listen();let browser;
const proof={synthetic:true,real_github_writes:0,model_calls:0,checks:[],passed:false};
try{
 browser=await chromium.launch({headless:true,...(process.env.PI_UI_BROWSER_EXECUTABLE?
  {executablePath:process.env.PI_UI_BROWSER_EXECUTABLE}:{channel:'chrome'})});
 const page=await browser.newPage({viewport:{width:1280,height:1040}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const address=server.httpServer.address();
 await page.goto(`http://127.0.0.1:${address.port}/test/issue-progress-browser.html`);
 await page.getByLabel('Issue 评论阶段').selectOption('candidate_passed');
 await page.getByRole('button',{name:'预览此阶段的评论'}).click();
 const approve=page.getByRole('button',{name:'批准发送这条评论'});
 await approve.waitFor();assert.equal(await approve.isDisabled(),true);proof.checks.push('preview_unchecked');
 await page.getByRole('checkbox').check();assert.equal(await approve.isEnabled(),true);
 await page.screenshot({path:path.join(target,'preview.png'),fullPage:true});
 await approve.click();await page.getByText('网络结果未知，先核对',{exact:true}).waitFor();
 assert.equal(await page.getByTestId('writes').textContent(),'1');
 assert.equal(await page.getByRole('button',{name:'批准发送这条评论'}).count(),0);proof.checks.push('unknown_no_replay');
 await page.getByRole('button',{name:'只读核对这条评论，不重发'}).click();
 await page.getByText('评论已确认',{exact:true}).waitFor();
 assert.equal(await page.getByTestId('writes').textContent(),'1');proof.checks.push('reconcile_no_write');
 await page.screenshot({path:path.join(target,'reconciled.png'),fullPage:true});
 await page.reload();await page.getByLabel('Issue 评论阶段').waitFor();
 assert.equal(await page.getByRole('button',{name:'批准发送这条评论'}).count(),0);proof.checks.push('refresh_clears_consent');
 assert.deepEqual(errors,[]);proof.passed=true;
}finally{await fs.writeFile(path.join(target,'result.json'),JSON.stringify(proof,null,2));await browser?.close();await server.close();}
console.log(JSON.stringify(proof));
