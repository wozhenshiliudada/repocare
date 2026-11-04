// End-to-end test against scripts/workbench_ui_preview.py's disposable fixture only.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PI_UI_PLAYWRIGHT_PACKAGE||'playwright');
const [controlFile,output]=process.argv.slice(2);
const control=JSON.parse(await fs.readFile(controlFile,'utf8'));
if(!control.root.includes(path.join('.demo','existing-wiring-browser','fixture-')))throw Error('Disposable fixture required');
const startup=(await fs.readFile(control.log,'utf8')).split(/\r?\n/).find(v=>/^http:\/\/127\.0\.0\.1:\d+\/#session=[a-f0-9]+$/.test(v));
if(!startup)throw Error('Fixture not ready');
const url=new URL(startup),token=url.hash.slice('#session='.length),headers={Authorization:'Bearer '+token};
const run=path.join(control.root,'evidence','a'.repeat(32)),summary=path.join(control.root,'summary.json');
await fs.mkdir(run,{recursive:true});await fs.mkdir(output,{recursive:true});
await fs.writeFile(path.join(run,'run.json'),JSON.stringify({schema_version:1,run_id:'a'.repeat(32),status:'interrupted',source_repo:path.join(control.root,'calculator-demo'),issue:'Synthetic browser evidence'}));
const metrics=Object.fromEntries(['patch_only','test_gate','guarded_one','guarded_two'].map(arm=>[arm,{tasks:5,repair_tasks:4,graded_repairs:1,false_acceptances:0,failed_candidates_retained:0,approval_violations:0,synthetic_model_requests:0,workflow_duration_seconds:0.01}]));
await fs.writeFile(summary,JSON.stringify({schema_version:1,evidence_kind:'fixture-scripted',model_accuracy_claim:false,real_model_requests:0,passed:false,metrics,dataset_sha256:'b'.repeat(64),inputs_and_first_patches_matched:true,cells:[{secret:'must-not-export'}]}));
const browser=await chromium.launch({headless:true,channel:'msedge'});
const page=await browser.newPage({viewport:{width:1280,height:1000},acceptDownloads:true});
const report={synthetic:true,checks:[],pageErrors:[],actions:[]};
page.on('pageerror',e=>report.pageErrors.push(e.message));
page.on('request',r=>{if(r.method()==='POST')report.actions.push(new URL(r.url()).pathname);});
async function state(){const r=await page.request.get(url.origin+'/api/state',{headers});assert.equal(r.status(),200);return r.json();}
async function workspaceBudgets(){const s=await state(),rows=[];for(const w of s.workspaces){const r=await page.request.get(url.origin+w.api_prefix+'state',{headers:{Authorization:'Bearer '+w.token}});const value=await r.json();rows.push({repository:w.repository,used:value.requests_used,unknown:value.requests_reserved_unknown,remaining:value.requests_remaining});}return rows;}
const before=await workspaceBudgets();
try{
 await page.goto(startup);
 // The intentionally credential-free preview opens the normal settings prompt.
 await page.getByRole('button',{name:'关闭设置',exact:true}).click();
 await page.getByRole('navigation',{name:'工作台导航'}).getByRole('button',{name:'工作概览',exact:true}).click();
 const flow=page.locator('#native-overview').getByLabel('选择本地预览流程');await flow.selectOption('dashboard');
 await page.getByLabel('仪表盘运行目录',{exact:true}).fill(run);
 const downloadReady=page.waitForEvent('download');
 await page.getByRole('button',{name:'生成离线仪表盘',exact:true}).click();
 await page.getByRole('button',{name:'下载 dashboard.html',exact:true}).click();
 const download=await downloadReady;await download.saveAs(path.join(control.root,'download-dashboard.html'));
 const html=await fs.readFile(path.join(control.root,'download-dashboard.html'),'utf8');
 assert.match(html,/Synthetic browser evidence/);assert.match(html,/connect-src 'none'/);
 assert.equal(download.suggestedFilename(),'dashboard.html');report.checks.push('real HTTP dashboard export and authenticated browser download');
 await page.getByRole('region',{name:'离线运行仪表盘实际操作',exact:true}).screenshot({path:path.join(output,'dashboard.png')});
 await flow.selectOption('comparison');await page.getByLabel('对照摘要文件',{exact:true}).fill(summary);
 await page.getByRole('button',{name:'读取并导出对照摘要',exact:true}).click();
 await page.getByText('原对照结果不完整（partial），导出不代表修复成功。',{exact:true}).waitFor();
 for(const name of ['comparison.json','comparison.md']){
  const ready=page.waitForEvent('download');await page.getByRole('button',{name:'下载 '+name,exact:true}).click();
  const file=await ready;const target=path.join(control.root,'download-'+name);await file.saveAs(target);
  const text=await fs.readFile(target,'utf8');assert.doesNotMatch(text,/must-not-export|"cells"/);assert.match(text,/partial/);
 }
 report.checks.push('existing partial summary exported as allowlisted JSON and Markdown, no experiment');
 await page.getByRole('region',{name:'对照与展示结果导出实际操作',exact:true}).screenshot({path:path.join(output,'comparison.png')});
 await page.getByLabel('对照摘要文件',{exact:true}).fill(path.join(control.root,'missing-summary.json'));
 await page.getByRole('button',{name:'读取并导出对照摘要',exact:true}).click();
 await page.getByRole('alert').filter({hasText:/accessible regular file/}).waitFor();
 assert.equal(await page.getByRole('button',{name:'下载 comparison.json',exact:true}).count(),0);
 assert.equal(report.actions.filter(v=>v==='/api/actions/export_comparison').length,2);
 report.checks.push('missing evidence fails visibly with no automatic replay');
 await flow.selectOption('large-repository');
 await page.getByText('已有仓库检查、文件过滤和有限范围连接。当前目录仍整体枚举，完整分页与渐进加载尚待实现。',{exact:true}).waitFor();
 await page.getByRole('button',{name:'打开已有仓库连接与范围选择',exact:true}).click();
 await page.locator('.onboarding').getByRole('button',{name:'选择文件夹',exact:true}).waitFor();
 await page.getByRole('navigation',{name:'工作台导航'}).getByRole('button',{name:'工作概览',exact:true}).click();
 report.checks.push('large repository entry opens original connection and states actual partial capability');
 await flow.selectOption('global-metrics');
 assert.equal(await page.getByRole('button',{name:'提交执行（尚未接通）',exact:true}).isDisabled(),true);
 report.checks.push('cross repository metrics remains an explicit unavailable preview');
 await flow.selectOption('recent-projects');await page.getByRole('button',{name:'读取已有项目',exact:true}).click();
 const inspect=page.getByRole('button',{name:'重新检查此项目',exact:true});await inspect.first().waitFor();assert.equal(await inspect.count(),2);
 await inspect.first().click();
 const onboarding=page.locator('.onboarding');await onboarding.getByRole('heading',{name:'确认检查方式',exact:true}).waitFor();
 assert.equal(await onboarding.locator('[id="confirmed"]').isChecked(),false);
 assert.equal(report.actions.filter(v=>v==='/api/actions/connect').length,0);
 await onboarding.locator('[id="confirmed"]').check();await onboarding.getByRole('button',{name:'确认并打开维护工作台',exact:true}).click();
 await page.getByRole('navigation',{name:'工作台导航'}).getByRole('button',{name:'工作概览',exact:true}).click();
 report.checks.push('saved project is listed, inspected and only explicitly reconnected');
 assert.deepEqual(await workspaceBudgets(),before);report.checks.push('all original request budgets unchanged, no model tasks');
 await flow.selectOption('dashboard');await page.setViewportSize({width:390,height:844});
 await page.getByRole('region',{name:'离线运行仪表盘实际操作',exact:true}).scrollIntoViewIfNeeded();
 await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 const panelBounds=await page.getByRole('region',{name:'离线运行仪表盘实际操作',exact:true}).boundingBox();
 assert.ok(panelBounds&&panelBounds.x>=0&&panelBounds.x+panelBounds.width<=390);
 report.checks.push('mobile overview has no document horizontal overflow');
 assert.deepEqual(report.pageErrors,[]);
 report.downloadSha256=createHash('sha256').update(html).digest('hex');report.passed=true;
}catch(error){report.error=String(error);await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});throw error;}
finally{
 await fs.writeFile(path.join(output,'result.json'),JSON.stringify(report,null,2));
 await browser.close();
}
