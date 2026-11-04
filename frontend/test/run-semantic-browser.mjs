// Explicitly test the original full_alignment disposable service; no user service or paid model.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PI_UI_PLAYWRIGHT_PACKAGE||'playwright');
const [controlFile,manifest,output]=process.argv.slice(2);
const control=JSON.parse(await fs.readFile(controlFile,'utf8'));
assert.match(path.basename(control.root),/^repocare-full-alignment-fixture-[a-f0-9]{32}$/);
const fixture=JSON.parse(await fs.readFile(path.join(control.root,'fixture.json'),'utf8'));
const startup=(await fs.readFile(control.log,'utf8')).split(/\r?\n/).find(v=>/^http:\/\/127\.0\.0\.1:\d+\/#session=[a-f0-9]+$/.test(v));
assert.ok(startup);
const url=new URL(startup),headers={Authorization:'Bearer '+url.hash.slice('#session='.length)};
const browser=await chromium.launch({headless:true,channel:'msedge'});
const page=await browser.newPage({viewport:{width:1360,height:1000}});
page.setDefaultTimeout(60000);
const report={checks:[],pageErrors:[],synthetic_language_model:true,actual_local_encoder:true};
page.on('pageerror',e=>report.pageErrors.push(e.message));
await fs.mkdir(output,{recursive:true});
async function state(){return (await page.request.get(url.origin+'/api/state',{headers})).json();}
async function local(record){return (await page.request.get(url.origin+record.api_prefix+'state',{headers:{Authorization:'Bearer '+record.token}})).json();}
const nav=page.getByRole('navigation',{name:'工作台导航'});
async function choose(id){await nav.getByRole('button',{name:'记忆与知识',exact:true}).click();await page.locator('#native-memory').getByLabel('选择本地预览流程').selectOption(id);}
async function execute(){const tool=page.getByRole('region',{name:'已有本地工具实际操作',exact:true});await tool.getByRole('button',{name:'预览原能力与实际影响',exact:true}).click();await tool.getByRole('checkbox').check();await tool.getByRole('button',{name:'执行已确认的原能力',exact:true}).click();await tool.getByRole('button',{name:'下载 tool-result.json',exact:true}).waitFor();return tool;}
try{
 await page.goto(startup);await page.getByRole('button',{name:/^calculator-demo/}).click();
 const initial=await state(),old=initial.workspaces.find(w=>w.repository===fixture.repository),before=await local(old);
 assert.equal(before.memory_retrieval.available,false);
 await choose('semantic-package');let tool=page.getByRole('region',{name:'已有本地工具实际操作',exact:true});
 await tool.getByLabel('已安装固定语义包的 manifest JSON 完整路径',{exact:false}).fill(manifest);
 await execute();assert.equal((await local(old)).memory_retrieval.available,false);
 report.checks.push('actual manifest registered by preview and explicit confirm; existing task remains unchanged');
 await choose('source-bind');tool=page.getByRole('region',{name:'已有本地工具实际操作',exact:true});
 await tool.getByLabel('目标 Git 仓库完整路径',{exact:false}).fill(fixture.fresh_repository);
 await tool.getByLabel('资料集路径（与索引成对填写）',{exact:false}).fill(fixture.documents);
 await tool.getByLabel('已有 .memory.sqlite3 索引路径',{exact:false}).fill(fixture.fresh_index);
 await execute();await tool.getByRole('button',{name:'检查并连接这个新会话仓库',exact:true}).click();
 const onboarding=page.locator('.onboarding');await onboarding.getByRole('heading',{name:'确认检查方式',exact:true}).waitFor();
 assert.equal(await onboarding.locator('[id="confirmed"]').isChecked(),false);
 await onboarding.locator('[id="confirmed"]').check();await onboarding.getByRole('button',{name:'确认并打开维护工作台',exact:true}).click();
 await page.getByRole('button',{name:/^new-source-demo/}).waitFor();await nav.getByRole('button',{name:'记忆与知识',exact:true}).click();
 const fresh=(await state()).workspaces.find(w=>w.repository===fixture.fresh_repository);assert.ok(fresh);
 let actual=await local(fresh);assert.equal(actual.memory_retrieval.available,true);assert.equal(actual.memory_retrieval.enabled,false);
 const used=actual.requests_used,unknown=actual.requests_reserved_unknown;
 await page.getByRole('checkbox',{name:'启用已登记的本地混合检索',exact:true}).check();
 await page.getByRole('button',{name:'保存检索设置',exact:true}).click();
 await page.waitForResponse(r=>r.url().endsWith(fresh.api_prefix+'state')&&r.status()===200).catch(()=>{});
 actual=await local(fresh);assert.equal(actual.memory_retrieval.enabled,true);
 await page.getByLabel('记忆查询',{exact:true}).fill('Old approval rule');
 const response=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===fresh.api_prefix+'actions/memory_query');
 await page.getByRole('button',{name:'查询记忆',exact:true}).click();await response;
 // The HTTP action returns an asynchronous job; wait for its actual finished result.
 let result;
 await page.waitForFunction(()=>!document.querySelector('button[aria-busy="true"]'));
 for(let i=0;i<60;i++){
   const s=await local(fresh);const job=[...(s.jobs||[])].reverse().find(j=>j.action==='memory_query');
   if(job?.status==='finished'){result=job.result.memory_query;break;}
   if(job?.status==='failed')throw Error(JSON.stringify(job.result));
   await new Promise(resolve=>setTimeout(resolve,500));
 }
 assert.ok(result);assert.equal(result.retrieval,'local_minilm_fields_fulltokens_bm25_rrf60_cosine0.5_v3');assert.ok(result.returned_items>0);assert.equal(result.extra_model_requests,0);
 actual=await local(fresh);assert.equal(actual.requests_used,used);assert.equal(actual.requests_reserved_unknown,unknown);assert.equal((await local(old)).requests_used,before.requests_used);
 report.query={retrieval:result.retrieval,returned_items:result.returned_items,extra_model_requests:result.extra_model_requests};
 report.checks.push('new original Pi session registers model, stays disabled until actual browser opt-in, returns actual local hybrid results with unchanged budget');
 await page.screenshot({path:path.join(output,'semantic-query.png'),fullPage:true});
 assert.deepEqual(report.pageErrors,[]);report.passed=true;
}catch(e){report.error=String(e).replace(/#session=[a-f0-9]+/g,'#session=REDACTED');await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});throw e;}
finally{await fs.writeFile(path.join(output,'result.json'),JSON.stringify(report,null,2));await browser.close();}
