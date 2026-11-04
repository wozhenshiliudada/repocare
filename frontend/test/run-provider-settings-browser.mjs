// Only use with a disposable, credential-free `maintain.py web` workspace.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PI_UI_PLAYWRIGHT_PACKAGE||'playwright');
const [startupLog,output]=process.argv.slice(2);
if(!path.isAbsolute(startupLog)||!path.isAbsolute(output))throw Error('Absolute startup log and evidence directory required');
const url=(await fs.readFile(startupLog,'utf8')).split(/\r?\n/)[0];
if(!/^http:\/\/127\.0\.0\.1:\d+\/#session=[a-f0-9]+$/.test(url))throw Error('Disposable loopback service required');
await fs.mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'msedge'});
const page=await browser.newPage({viewport:{width:1280,height:1000}});
const report={synthetic:true,checks:[],writes:[],pageErrors:[]};
page.on('pageerror',e=>report.pageErrors.push(e.message));
page.on('request',r=>{if(r.method()!=='GET')report.writes.push({method:r.method(),pathname:new URL(r.url()).pathname});});
const secret='browser-fixture-key-never-echo';
try{
 await page.goto(url);
 const dialog=page.getByRole('dialog',{name:'外观与设置'});
 await dialog.waitFor();
 await dialog.getByLabel('模型 Base URL').fill('https://provider.example.invalid/v1');
 await dialog.getByLabel('模型名称',{exact:true}).fill('browser-model');
 const key=dialog.getByLabel('模型 API Key');
 assert.equal(await key.getAttribute('type'),'password');
 await key.fill(secret);
 await dialog.getByRole('button',{name:'保存模型配置',exact:true}).click();
 await dialog.getByRole('status').filter({hasText:'已保存到本机'}).waitFor();
 assert.equal(await key.inputValue(),'');report.checks.push('first-run save and key cleared');
 await dialog.getByLabel('模型 Base URL').fill('http://provider.example.invalid/v1');
 await dialog.getByRole('button',{name:'保存模型配置',exact:true}).click();
 await dialog.getByRole('alert').waitFor();report.checks.push('HTTP endpoint rejected without overwriting saved settings');
 await dialog.getByRole('button',{name:'刷新模型设置',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('[aria-label="模型 Base URL"]')?.value==='https://provider.example.invalid/v1');
 await dialog.getByLabel('模型名称',{exact:true}).fill('browser-model-updated');
 await dialog.getByRole('button',{name:'保存模型配置',exact:true}).click();
 await dialog.getByRole('status').filter({hasText:'已保存到本机'}).waitFor();report.checks.push('blank key retains existing credential for same endpoint');
 // Lose one response after the actual save; UI must read back instead of replaying.
 await page.route('**/api/provider-config',async route=>{
  if(route.request().method()!=='POST'){await route.continue();return;}
  await route.fetch();await route.abort('failed');await page.unroute('**/api/provider-config');
 });
 await dialog.getByLabel('模型名称',{exact:true}).fill('browser-model-uncertain');
 await dialog.getByRole('button',{name:'保存模型配置',exact:true}).click();
 await dialog.getByRole('button',{name:'重新读取设置，核对保存结果',exact:true}).waitFor();
 assert.equal(await dialog.getByRole('button',{name:'保存模型配置',exact:true}).isDisabled(),true);
 await dialog.getByRole('button',{name:'重新读取设置，核对保存结果',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('[aria-label="模型名称"]')?.value==='browser-model-uncertain'&&!document.querySelector('.provider-settings fieldset')?.disabled);
 report.checks.push('lost response readback without automatic mutation replay');
 await dialog.getByRole('button',{name:'关闭设置',exact:true}).click();
 await page.getByRole('button',{name:/外观与设置/}).click();
 await dialog.getByRole('button',{name:'刷新模型设置',exact:true}).waitFor();
 await page.waitForFunction(()=>document.querySelector('[aria-label="模型名称"]')?.value==='browser-model-uncertain');
 assert.equal(await dialog.getByLabel('模型 API Key').inputValue(),'');
 assert.equal(await page.evaluate(s=>JSON.stringify({...localStorage,...sessionStorage}).includes(s),secret),false);
 report.checks.push('reopened settings restore model and never persist browser key');
 await page.screenshot({path:path.join(output,'settings-desktop.png')});
 await page.setViewportSize({width:320,height:900});
 const overflow=await dialog.evaluate(e=>e.scrollWidth>e.clientWidth+2);
 assert.equal(overflow,false);await page.screenshot({path:path.join(output,'settings-mobile.png')});
 report.checks.push('320px modal has no horizontal overflow');
 assert.deepEqual(report.pageErrors,[]);
 assert.equal(report.writes.filter(r=>r.pathname==='/api/provider-config').length,4);
 assert.equal(report.writes.some(r=>/plan|repair|execute/.test(r.pathname)),false);
}catch(error){report.failure=error.message;throw error;}
finally{await fs.writeFile(path.join(output,'browser-result.json'),JSON.stringify(report,null,2));await browser.close();}
console.log(JSON.stringify({checks:report.checks.length,pageErrors:report.pageErrors.length,providerWrites:report.writes.filter(r=>r.pathname==='/api/provider-config').length}));
