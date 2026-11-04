// Run only against a disposable full_workbench_preview.py fixture.
// No remote operations or model actions are submitted by this visual audit.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PI_UI_PLAYWRIGHT_PACKAGE||'playwright');
const [url,output,phase='after']=process.argv.slice(2);
if(!url?.startsWith('http://127.0.0.1:')||!path.isAbsolute(output))throw Error('Disposable loopback URL and absolute output directory required');
await fs.mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.PI_UI_BROWSER_EXECUTABLE?{executablePath:process.env.PI_UI_BROWSER_EXECUTABLE}:{channel:'msedge'})});
const report={phase,synthetic:true,checks:[],pageErrors:[],writes:[],failures:[]};
const page=await browser.newPage({viewport:{width:1440,height:1000}});
page.on('pageerror',e=>report.pageErrors.push(e.message));
page.on('request',r=>{if(r.method()!=='GET')report.writes.push({method:r.method(),pathname:new URL(r.url()).pathname});});
const names=['工作概览','维护任务','Issue 工作流','监控与待办','记忆与知识','交付与验收','维护设置','服务管理'];
async function navigate(name){
 if(!await page.getByRole('navigation',{name:'工作台导航'}).isVisible())await page.getByRole('button',{name:'打开仓库侧栏',exact:true}).click();
 await page.getByRole('navigation',{name:'工作台导航'}).getByRole('button',{name,exact:true}).click();
 await page.waitForTimeout(100);
}
async function audit(label){
 const result=await page.evaluate(()=>{
  const visible=e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
  const roots=[...document.querySelectorAll('.workspace-pages,.conversation:not([hidden]),.settings-modal,.inspector-content,.original-details:not([hidden]),.rail')].filter(visible);
  const overflow=roots.filter(e=>e.scrollWidth>e.clientWidth+2).map(e=>({class:e.className,width:e.clientWidth,scroll:e.scrollWidth}));
  const cropped=[...document.querySelectorAll('button,select,input:not([type=checkbox]),h1,h2,h3')].filter(visible).filter(e=>e.clientHeight&&e.scrollHeight>e.clientHeight+2&&['hidden','clip'].includes(getComputedStyle(e).overflowY)).map(e=>e.textContent?.slice(0,60));
  return {width:innerWidth,rootFont:getComputedStyle(document.documentElement).fontSize,overflow,cropped,documentOverflow:document.documentElement.scrollWidth>innerWidth+2,
   title:[...document.querySelectorAll('h1')].filter(visible).map(e=>({text:e.textContent,size:getComputedStyle(e).fontSize,spacing:getComputedStyle(e).letterSpacing})),
   bodyFont:getComputedStyle(document.body).fontFamily};
 });
 report.checks.push({label,...result});
 if(result.overflow.length||result.cropped.length||result.documentOverflow)report.failures.push({label,...result});
}
try{
 await page.goto(url);await page.getByRole('navigation',{name:'工作台导航'}).waitFor();
 await page.locator('.repository-row').first().click();
 await page.locator('.workspace-pages .page-intro').waitFor();
 await page.waitForTimeout(700);
 await page.screenshot({path:path.join(output,'overview-desktop.png')});
 await navigate('维护设置');await page.screenshot({path:path.join(output,'configuration-desktop.png')});
 await navigate('工作概览');
 if(phase==='before'){
  await page.setViewportSize({width:320,height:900});await page.screenshot({path:path.join(output,'overview-mobile.png')});await audit('baseline-mobile');
 }else{
  if(phase!=='focus')for(const width of [1440,768,320])for(const theme of ['light','dark'])for(const font of [13,15,17]){
   await page.setViewportSize({width,height:1000});
   await page.evaluate(({theme,font})=>{document.documentElement.dataset.theme=theme;document.documentElement.dataset.font=String(font);},{theme,font});
   for(const name of names){await navigate(name);await audit(`${width}/${theme}/${font}/${name}`);}
  }
  await page.setViewportSize({width:320,height:900});await navigate('工作概览');
  await page.evaluate(()=>{document.documentElement.dataset.theme='light';document.documentElement.dataset.font='15';});
  await page.screenshot({path:path.join(output,'overview-mobile.png')});
  // Authored text stress cases, only in the disposable browser DOM, never persisted.
  for(const width of [320,768,1440]){
   await page.setViewportSize({width,height:1000});await navigate('工作概览');
   await page.evaluate(()=>{
    const card=document.createElement('section');card.className='native-card typography-stress';
    const title=document.createElement('h2');title.textContent='长中文与来源信息的合成排版样例';
    const paragraph=document.createElement('p');paragraph.className='issue-body';paragraph.textContent='这是明确编写的合成文字，用于检查阅读宽度、自然换行以及放大后的排版。'.repeat(12);
    const fingerprint=document.createElement('code');fingerprint.className='exact-fingerprint';fingerprint.textContent='C:/fixture/中文仓库/'+('long-directory-name/'.repeat(16))+'源码.tsx SHA256:'+('a'.repeat(64));
    const badge=document.createElement('span');badge.className='badge tone-warning';badge.textContent='等待维护者核验完整来源与修改范围，当前候选仍待验收';
    const table=document.createElement('div');table.className='test-table-scroll';table.innerHTML='<table><tbody><tr><td><pre>'+('command_argument_'.repeat(50))+'</pre></td></tr></tbody></table>';
    card.append(title,paragraph,fingerprint,badge,table);document.querySelector('.workspace-pages').prepend(card);
   });
   await audit(`long-authored-text/${width}`);
   if(width===320)await page.screenshot({path:path.join(output,'long-text-mobile.png')});
   await page.locator('.typography-stress').evaluate(e=>e.remove());
  }
  await page.setViewportSize({width:320,height:900});
  await page.locator('.header-detail').click();await audit('embedded-original-mobile');
  await page.screenshot({path:path.join(output,'embedded-mobile.png')});
  await page.evaluate(()=>{document.documentElement.dataset.theme='dark';document.documentElement.dataset.font='17';});
  await audit('embedded-original-dark');await page.screenshot({path:path.join(output,'embedded-dark.png')});
  await page.getByRole('button',{name:'关闭任务详情',exact:true}).click();
  // Read-only inspector and real settings dialog, including persistence through reload.
  await page.getByRole('button',{name:'代码',exact:true}).click();await audit('inspector-mobile');
  await page.screenshot({path:path.join(output,'inspector-mobile.png')});
  await page.getByRole('button',{name:'关闭资料栏'}).click();
  await page.getByRole('button',{name:'打开仓库侧栏',exact:true}).click();
  await page.locator('.settings-button').click();await page.getByLabel('界面字号',{exact:true}).selectOption('17');
  await page.getByRole('button',{name:'暗色',exact:true}).click();await audit('settings-mobile');
  await page.screenshot({path:path.join(output,'settings-mobile.png')});
  await page.reload();await page.locator('.primary-nav').waitFor({state:'attached'});
  await page.waitForFunction(()=>document.documentElement.dataset.font==='17');
  const restored=await page.evaluate(()=>({font:document.documentElement.dataset.font,theme:document.documentElement.dataset.theme}));
  report.preferenceRestored=restored;if(restored.font!=='17'||restored.theme!=='dark')report.failures.push({preference:restored});
  // At 200% browser zoom, 1440 physical pixels has 720 CSS pixels of layout space.
  // Also double the browser default font via an absolute percentage stylesheet.
  await page.setViewportSize({width:720,height:500});
  await page.route('**/typography-user.css',route=>route.fulfill({contentType:'text/css',body:':root[data-font]{font-size:212.5%} *{line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important} p{margin-bottom:2em!important}'}));
  const spacing=await page.addStyleTag({url:new URL('/typography-user.css',url).href});
  for(const name of names){await navigate(name);await audit(`200%-text/spacing/${name}`);}
  // The narrow utility rail must also remain scrollable and operable at enlarged text.
  await page.getByRole('button',{name:'代码',exact:true}).click();await audit('200%-text/inspector');
  await page.getByRole('button',{name:'关闭资料栏'}).click();
  await page.getByRole('button',{name:'Actions',exact:true}).click();await audit('200%-text/actions');
  await page.getByRole('button',{name:'关闭资料栏'}).click();
  await page.screenshot({path:path.join(output,'text-spacing.png')});await spacing.evaluate(e=>e.remove());
  // Token contrast audit over the surfaces on which these foregrounds are used.
  report.contrast=[];
  for(const theme of ['light','dark']){
   const colors=await page.evaluate(theme=>{
    document.documentElement.dataset.theme=theme;const s=getComputedStyle(document.documentElement);
    return Object.fromEntries(['text','muted','blue','success','danger','warning','bg','panel','sidebar','selected'].map(k=>[k,s.getPropertyValue('--'+k).trim()]));
   },theme);
   const luminance=hex=>hex.match(/[a-f\d]{2}/gi).map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
   for(const foreground of ['text','muted','blue','success','danger','warning'])for(const background of ['bg','panel','sidebar','selected']){
    const a=luminance(colors[foreground]),b=luminance(colors[background]),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    report.contrast.push({theme,foreground,background,ratio});if(ratio<4.5)report.failures.push({contrast:{theme,foreground,background,ratio}});
   }
  }
 }
}catch(e){report.failures.push({error:String(e.stack).replace(/#session=[a-f0-9]+/g,'#session=[REDACTED]')});}
finally{
 report.passed=!report.failures.length&&!report.pageErrors.length&&!report.writes.length;
 await fs.writeFile(path.join(output,'result.json'),JSON.stringify(report,null,2));await browser.close();
 console.log(JSON.stringify({phase,checks:report.checks.length,failures:report.failures.length,pageErrors:report.pageErrors,writes:report.writes,passed:report.passed}));
 if(!report.passed)process.exitCode=1;
}
