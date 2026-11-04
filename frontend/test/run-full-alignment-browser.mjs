// Run only against tests/full_alignment_browser_fixture.py's new disposable root.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {workflows} from '../src/preview-workflows.mjs';
import {toolWorkflows} from '../src/capability-routing.mjs';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PI_UI_PLAYWRIGHT_PACKAGE||'playwright');
const [controlFile,output]=process.argv.slice(2);
const control=JSON.parse(await fs.readFile(controlFile,'utf8'));
if(!path.isAbsolute(control.root)||!/^repocare-full-alignment-fixture-[a-f0-9]{32}$/.test(path.basename(control.root)))throw Error('Disposable alignment fixture outside Git required');
const fixture=JSON.parse(await fs.readFile(path.join(control.root,'fixture.json'),'utf8'));
const startup=(await fs.readFile(control.log,'utf8')).split(/\r?\n/).find(v=>/^http:\/\/127\.0\.0\.1:\d+\/#session=[a-f0-9]+$/.test(v));
if(!startup)throw Error('Fixture not ready');
const url=new URL(startup),headers={Authorization:'Bearer '+url.hash.slice('#session='.length)};
await fs.mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'msedge'});
const page=await browser.newPage({viewport:{width:1360,height:1000},acceptDownloads:true});
page.setDefaultTimeout(60000);
const report={synthetic:true,pageErrors:[],checks:[],tools:[],actions:[]};
page.on('pageerror',e=>report.pageErrors.push(e.message));
page.on('request',r=>{if(r.method()==='POST')report.actions.push(new URL(r.url()).pathname);});
async function state(){return (await page.request.get(url.origin+'/api/state',{headers})).json();}
async function budget(record){const r=await page.request.get(url.origin+record.api_prefix+'state',{headers:{Authorization:'Bearer '+record.token}});const s=await r.json();return {used:s.requests_used,unknown:s.requests_reserved_unknown,remaining:s.requests_remaining};}
const navigation=page.getByRole('navigation',{name:'工作台导航'});
const names={overview:'工作概览',tasks:'维护任务',issues:'Issue 工作流',events:'监控与待办',memory:'记忆与知识',configuration:'维护设置',service:'服务管理'};
async function select(id){const flow=workflows.find(w=>w.id===id);await navigation.getByRole('button',{name:names[flow.page],exact:true}).click();await page.locator('#native-'+flow.page).getByLabel('选择本地预览流程').selectOption(id);}
const params={
 'git-compare':{repository:fixture.repository},
 preflight:{issue:'Synthetic addition baseline, no model'},
 'python-binding':{repository:fixture.repository,sources:'calculator.py',checks:'calculator.py'},
 'archive-import':{run_dir:fixture.run},
 backup:{runs_dir:fixture.offline_runs},
 'source-build':{repository:fixture.repository,source_root:fixture.documents},
 'case-export':{repository:fixture.repository,run_dir:fixture.run,case_id:'old-run',topic:'policy',start_line:1,end_line:2},
 'source-compose':{repository:fixture.repository,selection:fixture.compose},
 relations:{repository:fixture.repository,source_root:fixture.documents,index:fixture.index,spec:fixture.relations},
 'structure-build':{repository:fixture.repository,paths:'calculator.py'},
 'semantic-package':{manifest:fixture.bad_model},
 'memory-migration':{repository:fixture.repository,selection:fixture.migration},
 'report-export':{run_dir:fixture.run,format:'Markdown'},
 'source-bind':{repository:fixture.fresh_repository,source_root:fixture.documents,index:fixture.fresh_index},
};
try{
 await page.goto(startup);
 await page.getByRole('button',{name:/^calculator-demo/}).click();
 const hub=await state(),original=hub.workspaces.find(w=>w.repository===fixture.repository),before=await budget(original);
 const defs=(await (await page.request.get(url.origin+'/api/capabilities',{headers})).json()).capabilities;
 assert.deepEqual(defs.map(d=>d.id).sort(),[...toolWorkflows].sort());
 for(const id of toolWorkflows){
  await select(id);const tool=page.getByRole('region',{name:'已有本地工具实际操作',exact:true}),definition=defs.find(d=>d.id===id);
  await tool.getByRole('heading',{name:definition.title,exact:true}).waitFor();
  if(definition.unavailable_reason){assert.equal(await tool.getByRole('button',{name:'预览原能力与实际影响',exact:true}).isDisabled(),true);report.tools.push({id,status:'original_optional_runtime_missing',reason:definition.unavailable_reason});continue;}
  for(const [key,value] of Object.entries(params[id])){
   const field=definition.fields.find(f=>f.key===key),input=tool.getByLabel(field.label,{exact:false});
   if(field.kind==='select')await input.selectOption(String(value));else await input.fill(String(value));
  }
  await tool.getByRole('button',{name:'预览原能力与实际影响',exact:true}).click();
  const confirm=tool.getByRole('checkbox',{name:/确认执行此预览/});await confirm.waitFor();
  assert.equal(await tool.getByRole('button',{name:'执行已确认的原能力',exact:true}).isDisabled(),true);
  if(id==='git-compare')await tool.getByRole('button',{name:'采用这份固定提交与范围',exact:true}).click();
  await confirm.check();await tool.getByRole('button',{name:'执行已确认的原能力',exact:true}).click();
  if(id==='semantic-package'){
   await tool.getByRole('alert').filter({hasText:/Unsupported local encoder package/}).waitFor();
   assert.equal(await tool.getByRole('button',{name:'下载 tool-result.json',exact:true}).count(),0);
   report.tools.push({id,status:'original_package_validation_rejects_uninstalled_model'});continue;
  }
  await tool.getByRole('button',{name:'下载 tool-result.json',exact:true}).waitFor();
  report.tools.push({id,status:'executed_original_library'});
  await fs.writeFile(path.join(output,'progress.json'),JSON.stringify({last_completed_tool:id}));
  if(id==='preflight'){
   const s=await state(),last=s.jobs.filter(j=>j.action==='capability_execute'&&j.status==='finished'&&j.result.capability===id).at(-1);
   assert.equal(last.result.result.source_unchanged,true);assert.notEqual(last.result.result.tests[0].returncode,0);
  }
  if(id==='report-export'){
   const ready=page.waitForEvent('download');await tool.getByRole('button',{name:'下载 tool-report.md',exact:true}).click();
   const download=await ready;await download.saveAs(path.join(control.root,'download-report.md'));
   assert.match(await fs.readFile(path.join(control.root,'download-report.md'),'utf8'),/Old approvals/);
   await tool.screenshot({path:path.join(output,'report-export.png')});
  }
 }
 report.checks.push('all 14 original tool routes render actual server fields and explicit confirmation; optional prerequisites remain honest');
 assert.deepEqual(await budget(original),before);report.checks.push('original budget unchanged after all tools, no model requests');
 await select('provider');await page.getByRole('button',{name:'打开实际模型设置',exact:true}).click();
 await page.getByRole('dialog',{name:'外观与设置',exact:true}).waitFor();await page.getByRole('button',{name:'关闭设置',exact:true}).click();
 await select('auto-intake');await page.getByRole('button',{name:'打开实际 Issue 接单控制',exact:true}).click();
 await page.getByRole('region',{name:'Issue 接手与交付',exact:true}).waitFor();
 report.checks.push('native provider and intake links open existing actual controls');
 await navigation.getByRole('button',{name:'监控与待办',exact:true}).click();
 await page.getByLabel('维护待办搜索',{exact:true}).fill('approval');await page.getByRole('button',{name:'搜索维护待办',exact:true}).click();
 await page.getByText('原维护待办搜索结果',{exact:true}).waitFor();report.checks.push('maintenance search sends original workspace action and renders result');
 await page.getByRole('button',{name:'代码',exact:true}).click();
 const inspector=page.getByRole('complementary',{name:'代码与提交',exact:true});
 await inspector.getByRole('button',{name:'下一页文件',exact:true}).click();
 await inspector.getByLabel('筛选已追踪文件',{exact:true}).fill('page0229.md');
 await inspector.getByRole('button',{name:/page0229\.md$/}).click();
 await inspector.getByText('Synthetic tracked page',{exact:false}).waitFor();
 const read=await page.request.get(url.origin+'/api/browse/'+original.id+'/file?path=docs/large-readable.txt',{headers});
 assert.equal(read.status(),200);assert.equal((await read.json()).bytes,120000);
 report.checks.push('real >200-file browse paginates and searches beyond first page; >100KB text uses shared file capacity');
 await page.getByRole('button',{name:'关闭资料栏',exact:true}).click();
 await select('source-bind');await page.getByRole('button',{name:'检查并连接这个新会话仓库',exact:true}).click();
 const onboarding=page.locator('.onboarding');await onboarding.getByRole('heading',{name:'确认检查方式',exact:true}).waitFor();
 assert.equal(await onboarding.locator('[id="confirmed"]').isChecked(),false);
 await onboarding.locator('[id="confirmed"]').check();await onboarding.getByRole('button',{name:'确认并打开维护工作台',exact:true}).click();
 await page.getByRole('button',{name:/^new-source-demo/}).waitFor();
 await navigation.getByRole('button',{name:'记忆与知识',exact:true}).click();
 const after=await state(),fresh=after.workspaces.find(w=>w.repository===fixture.fresh_repository);
 assert.ok(fresh);const freshState=await (await page.request.get(url.origin+fresh.api_prefix+'state',{headers:{Authorization:'Bearer '+fresh.token}})).json();
 assert.equal(freshState.source_memory.enabled,true);assert.equal(freshState.requests_used,0);assert.deepEqual(await budget(original),before);
 report.checks.push('new resource binding bridges into original explicit connection and real source memory, old ledger unchanged');
 await select('source-build');await page.setViewportSize({width:390,height:844});
 const tool=page.getByRole('region',{name:'已有本地工具实际操作',exact:true});await tool.scrollIntoViewIfNeeded();
 await page.screenshot({path:path.join(output,'mobile-tools.png'),fullPage:true});
 const box=await tool.boundingBox();assert.ok(box&&box.x>=0&&box.x+box.width<=390);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 report.checks.push('generated actual tool form fits mobile viewport');
 assert.deepEqual(report.pageErrors,[]);report.passed=true;
}catch(e){report.error=String(e).replace(/#session=[a-f0-9]+/g,'#session=REDACTED');await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});throw e;}
finally{await fs.writeFile(path.join(output,'result.json'),JSON.stringify(report,null,2));await browser.close();}
