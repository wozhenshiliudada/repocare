import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {randomUUID} from 'node:crypto';

// Exercise the real standalone templates, mount handlers and language setters.
const asset=async(name,file)=>fs.readFile(new URL('../../src/repo_maintainer/'+name+'/'+file,import.meta.url),'utf8');
const decode=value=>value.replaceAll('&quot;','"').replaceAll('&amp;','&').replaceAll('&#39;',"'");
class Text {
 constructor(value){this.nodeType=3;this.textContent=String(value);this.parentNode=null;}
 get isConnected(){return Boolean(this.parentNode?.isConnected);}
}
class Element {
 constructor(tag='div'){this.nodeType=1;this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attributes=new Map();this.parentNode=null;this.value='';this.checked=false;this.hidden=false;this.disabled=false;this.open=false;this.classList={add:(value)=>{this.className=(this.className||'')+' '+value;},toggle(){}};}
 get childNodes(){return this.children;}get firstChild(){return this.children[0]||null;}
 get isConnected(){return this.connected||Boolean(this.parentNode?.isConnected);}
 get textContent(){return this.children.map(child=>child.textContent).join('');}
 set textContent(value){this.replaceChildren(new Text(value));}
 get options(){return this.children.filter(node=>node.tagName==='OPTION');}
 append(...children){for(let child of children){if(typeof child==='string')child=new Text(child);child.parentNode=this;this.children.push(child);}}
 prepend(...children){for(const child of children.reverse()){child.parentNode=this;this.children.unshift(child);}}
 replaceChildren(...children){for(const child of this.children)child.parentNode=null;this.children=[];this.append(...children);}
 setAttribute(name,value){value=String(value);this.attributes.set(name,value);if(name==='id')this.id=value;if(name==='class')this.className=value;if(name.startsWith('data-'))this.dataset[name.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=value;}
 getAttribute(name){return this.attributes.get(name)??(name==='id'?this.id??null:null);}
 addEventListener(name,fn){this['on'+name]=fn;}
 matches(selector){if(selector.startsWith('.'))return (this.className||'').split(/\s+/).includes(selector.slice(1));if(selector.startsWith('#'))return this.id===selector.slice(1);const attr=/^\[([^=\]]+)(?:=["']?([^"'\]]*)["']?)?\]$/.exec(selector);if(attr)return this.getAttribute(attr[1])!==null&&(attr[2]===undefined||this.getAttribute(attr[1])===attr[2]);return this.tagName===selector.toUpperCase();}
 querySelectorAll(selector){const queries=selector.split(',').map(item=>item.trim()),results=[];function visit(node){for(const child of node.children||[]){if(child.nodeType===1){if(queries.some(q=>child.matches(q)))results.push(child);visit(child);}}}visit(this);return results;}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 scrollIntoView(){}
}
function parse(html){
 const root=new Element('div');root.connected=true;const stack=[root],voids=new Set(['META','LINK','INPUT','HR','BR']);
 for(const part of html.match(/<[^>]+>|[^<]+/g)||[]){
  if(part.startsWith('<!'))continue;
  if(part.startsWith('</')){if(stack.length>1)stack.pop();continue;}
  if(part.startsWith('<')){const match=/^<([\w-]+)([^>]*)>/.exec(part);if(!match)continue;const node=new Element(match[1]);for(const attr of match[2].matchAll(/([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g))node.setAttribute(attr[1],decode(attr[2]??attr[3]??attr[4]??''));for(const attr of ['hidden','disabled','checked'])node[attr]=node.attributes.has(attr);if(node.attributes.has('value'))node.value=node.getAttribute('value');stack.at(-1).append(node);if(!voids.has(node.tagName))stack.push(node);}
  else stack.at(-1).append(new Text(decode(part)));
 }
 for(const node of root.querySelectorAll('textarea'))node.value=node.textContent;
 for(const node of root.querySelectorAll('select'))node.value=node.options[0]?.value||'';
 return root;
}
function fields(root){return root.querySelectorAll('input,textarea,select').map(node=>({node,value:node.value,checked:node.checked,disabled:node.disabled}));}
function retained(before,root){const after=fields(root);assert.equal(after.length,before.length);for(let index=0;index<before.length;index++){assert.equal(after[index].node,before[index].node,'language change must retain each original control');assert.equal(after[index].value,before[index].value);assert.equal(after[index].checked,before[index].checked);assert.equal(after[index].disabled,before[index].disabled);}}
const raw='正在规划：用户原文、模型输出和源文件保持中文 <script>literal</script>';
const file='src/中文计算器.py';
async function mount(name,{language='zh-CN',source,analysis,state:statePatch={}}={}){
 const root=parse(await asset(name,'index.html')),calls=[];
 const state=name==='workbench_assets'?{workspaces:[],sessions:[],monitors:[],allow_auto:true,request_budget:6}:{repository:'C:/原仓库',provider:'Synthetic fixture',policy:{allowed_paths:[file]},allow_auto:false,request_budget:50,requests_used:2,requests_remaining:45,requests_reserved_unknown:3,active:null,runtime:{build_sha256:'a'.repeat(64),started_at:'fixture',max_output_tokens:128000,planning_timeout_seconds:1800,code_changed:false},jobs:[],plans:[],runs:[],recovery_cards:[],planning_attempts:[],source_memory:{enabled:false},structure_index:{enabled:false},experience_memory:{enabled:false,entries:[]},knowledge_memory:{entries:[]},task_compaction:{},maintenance_memory:{status:'disabled'},memory_catalog:{adapters:[]},memory_retrieval:{enabled:false,available:false}};
 Object.assign(state,statePatch);
 const document={getElementById:()=>name==='local_ui_assets'?{}:null,createElement:tag=>new Element(tag),createTextNode:value=>new Text(value),querySelectorAll:selector=>root.querySelectorAll(selector)};
 const sandbox={window:{RepoCareTabSession:{open:()=>({token:'synthetic-only',selectedView:()=>null,saveView(){}})},scrollTo(){}},document,location:{origin:'http://127.0.0.1:12345'},URL,crypto:{randomUUID},confirm:()=>false,setInterval:()=>1,clearInterval(){},fetch:async(path,options={})=>{
  const method=options.method||'GET';calls.push({path,method,...(options.body?{body:JSON.parse(options.body)}:{})});
  if(method==='GET')return {ok:true,status:200,json:async()=>structuredClone(state)};
  const action=path.split('/').at(-1);
  if(action==='local')return {ok:true,status:202,json:async()=>({status:'finished',result:structuredClone(source)})};
  if(action==='analyze_repo')return {ok:true,status:202,json:async()=>({status:'finished',result:structuredClone(analysis)})};
  throw Error('unexpected business request: '+action);
 }};
 vm.runInNewContext(await asset(name,'app.js'),sandbox,{filename:name+'/app.js'});
 const controller=name==='workbench_assets'?sandbox.window.mountWorkbenchOnboarding(root,{language,unifiedNotifications:true,onSelectWorkspace(){}}):sandbox.window.mountMaintenanceUI(root,{language,embedded:true,token:'synthetic-only'});
 if(controller.ready)await controller.ready;else for(let i=0;i<12;i++)await Promise.resolve();
 return {root,calls,controller,state,get:id=>root.querySelector('[id="'+id+'"]')};
}
const inspected={path:'C:/用户仓库',branch:'main',head:'b'.repeat(40),revision:'original-revision',clean:true,files:[file,'tests/原测试.py'],checks:{recommendations:[],warnings:[],suggested_editable:[file],suggested_readonly:['tests/原测试.py']}};
test('onboarding translates explicit static text and placeholders from initial language, including approval labels',async()=>{
 const f=await mount('workbench_assets',{language:'en'});try{assert.equal(f.get('inspect').textContent,'Inspect repository');assert.equal(f.get('file-filter').getAttribute('placeholder'),'Enter a filename');assert.match(f.get('test-command').getAttribute('placeholder'),/^For example/);assert.match(f.get('confirmed').parentNode.textContent,/I confirm the checks/);assert.equal(f.get('confirmed').checked,false);assert.equal(f.calls.filter(call=>call.method==='POST').length,0);}finally{f.controller.dispose();}
});
test('onboarding real inspect handler keeps selected scope, raw paths, drafts and checked confirmations through both languages without requests',async()=>{
 const f=await mount('workbench_assets',{source:inspected});try{
  await f.controller.inspectRepository(inspected.path);assert.equal(f.calls.filter(call=>call.method==='POST').length,1);const request=f.calls.find(call=>call.method==='POST');assert.deepEqual(Object.keys(request.body).sort(),['path','request_id']);assert.equal(request.body.path,inspected.path);assert.match(request.body.request_id,/^[a-f0-9]{32}$/);
  const select=f.get('files').querySelector('select');select.value='read';select.onchange();f.get('test-command').value='[["node","原测试.mjs"]]';f.get('test-timeout').value='91';f.get('github-url').value='https://github.com/用户/原仓库';f.get('file-filter').value='未提交草稿';f.get('confirmed').checked=true;f.get('monitor-confirmed').checked=true;f.get('auto-mode').checked=true;
  const before=fields(f.root),calls=JSON.stringify(f.calls),selectNode=select;
  f.controller.setLanguage('en');retained(before,f.root);assert.equal(f.get('files').querySelector('select'),selectNode);assert.match(f.get('repository-summary').textContent,/Branch: main/);assert.ok(f.get('repository-summary').textContent.includes(inspected.path));assert.ok(f.get('files').textContent.includes(file));assert.equal(select.options[1].textContent,'Editable');assert.match(select.getAttribute('aria-label'),/file scope draft/);assert.match(f.get('scope-summary').textContent,/Read-only:/);assert.equal(JSON.stringify(f.calls),calls);
  f.controller.setLanguage('zh-CN');retained(before,f.root);assert.equal(select.options[1].textContent,'可修改');assert.match(f.get('scope-summary').textContent,/只读：/);assert.equal(JSON.stringify(f.calls),calls);
 }finally{f.controller.dispose();}
});
test('maintenance initial English and repeated language switches retain all draft controls, selection and original ledger',async()=>{
 const f=await mount('local_ui_assets',{language:'en'});try{
  assert.equal(f.get('issue').value,'');assert.equal(f.get('issue').getAttribute('placeholder'),'Describe an issue or failing test in this repository');
  assert.equal(f.get('plan').textContent,'Generate plan for approval');assert.match(f.get('configuration').textContent,/Metered model requests: 2\/50/);assert.match(f.get('configuration').textContent,/Reserved or unverified: 3/);assert.match(f.get('content').textContent,/Evidence at every step/);
  f.get('issue').value=raw;f.get('structure-query').value='原符号';f.get('budget-total').value='90';f.get('memory-panel').open=true;
  const before=fields(f.root),calls=JSON.stringify(f.calls),originalState=JSON.stringify(f.controller.getState()),content=f.get('content').firstChild;
  for(const language of ['zh-CN','en','zh-CN','en']){f.controller.setLanguage(language);retained(before,f.root);assert.equal(f.get('content').firstChild,content);assert.equal(f.get('memory-panel').open,true);assert.equal(JSON.stringify(f.controller.getState()),originalState);assert.equal(JSON.stringify(f.calls),calls);}
  assert.equal(f.get('issue').value,raw);assert.equal(f.calls.filter(call=>call.method==='POST').length,0);
 }finally{f.controller.dispose();}
});
test('legacy maintenance shows active and ended reservations independently',async()=>{
 const f=await mount('local_ui_assets',{language:'en',state:{requests_reserved_active:2,requests_ended_unknown:1}});try{
  assert.match(f.get('configuration').textContent,/In-flight reservations: 2 · Ended, unverified: 1/);
  f.controller.setLanguage('zh-CN');
  assert.match(f.get('configuration').textContent,/进行中预留：2 · 已结束待核对：1/);
 }finally{f.controller.dispose();}
});
test('selected original plan retains raw diagnosis, issue, risk, diff, JSON and enabled manual approval through language changes',async()=>{
 const evidence={requests:2,events:[{text:raw}],usage:{original:'原用量'}};
 const plan={id:'original-plan',status:'awaiting_approval',claimed:true,fingerprint:'original-fingerprint',plan:{issue:raw,diagnosis:raw,risk:raw,base_sha:'c'.repeat(40),tests:[['node','原测试.mjs']]},diff:'+ 原始代码不翻译\n- old source',planning_evidence:evidence};
 const f=await mount('local_ui_assets',{state:{plans:[plan]}});try{
  const original=JSON.stringify(f.controller.getState());const approve=f.get('approve-plan');assert.ok(approve);assert.equal(approve.disabled,false);const content=f.get('content').firstChild,calls=JSON.stringify(f.calls);f.get('issue').value='用户草稿';
  f.controller.setLanguage('en');assert.equal(f.get('content').firstChild,content);assert.equal(f.get('approve-plan'),approve);assert.equal(approve.disabled,false);assert.equal(approve.textContent,'Approve changes and tests');assert.match(f.get('content').textContent,/Diagnosis/);assert.ok(f.get('content').textContent.includes(raw));assert.ok(f.get('content').textContent.includes(plan.diff));const pre=f.get('content').querySelectorAll('pre').map(n=>n.textContent);assert.ok(pre.includes(JSON.stringify(evidence,null,2)));assert.equal(f.get('issue').value,'用户草稿');assert.equal(JSON.stringify(f.controller.getState()),original);assert.equal(JSON.stringify(f.calls),calls);assert.equal(f.calls.some(c=>c.method==='POST'),false);
  f.controller.setLanguage('zh-CN');assert.equal(approve.textContent,'批准修改并测试');assert.equal(JSON.stringify(f.calls),calls);
 }finally{f.controller.dispose();}
});
test('dynamic knowledge editor and maintenance follow-up controls survive translation with their original values and handlers',async()=>{
 const note={kind:'procedure',title:raw,summary:raw,triggers:[raw],preconditions:[raw],steps:[raw],verification:[raw],stop_conditions:[raw]};
 const f=await mount('local_ui_assets',{state:{maintenance_memory:{status:'verified',enabled:true,expected_revision:'same-revision',cursors:[],fixes:[],issues:[],observations:[],excluded:[]},knowledge_memory:{entries:[{id:'original-knowledge',review:'accepted',freshness:'verified',lifecycle:'active',note,refs:[],revision:'original-revision'}]}}});try{
  assert.equal(f.get('notice').textContent,'已连接。请生成方案，开始本地审批流程。');const editor=f.get('knowledge-entries').querySelectorAll('textarea');assert.equal(editor.length,7);editor[0].value='用户知识草稿';
  const all=f.get('maintenance-controls').querySelectorAll('input,textarea,select');assert.ok(all.length>=4);for(const node of all){if(node.tagName==='SELECT')node.value=node.options.at(-1).value;else node.value=raw;}const before=fields(f.root),calls=JSON.stringify(f.calls);const buttons=f.get('maintenance-controls').querySelectorAll('button'),handlers=buttons.map(b=>b.onclick);
  f.controller.setLanguage('en');retained(before,f.root);assert.equal(f.get('knowledge-entries').querySelectorAll('textarea')[0],editor[0]);assert.equal(editor[0].value,'用户知识草稿');assert.ok(f.get('knowledge-entries').textContent.includes(raw));assert.match(f.get('knowledge-entries').textContent,/Knowledge type|Investigation procedure/);assert.match(f.get('maintenance-controls').textContent,/Save follow-up/);assert.equal(JSON.stringify(f.calls),calls);for(let i=0;i<buttons.length;i++)assert.equal(buttons[i].onclick,handlers[i]);f.controller.setLanguage('zh-CN');retained(before,f.root);assert.equal(JSON.stringify(f.calls),calls);
 }finally{f.controller.dispose();}
});
test('known check IDs translate product recommendations and generated warnings while original command arguments and source filenames stay literal',async()=>{
 const source=structuredClone(inspected);source.checks={...source.checks,inputs:{'README.md':'original'},recommendations:[{id:'unittest-0',title:'Python 功能测试（unittest）',argv:['{python}','-m','unittest','discover','-s','原测试目录','-v'],sources:['正在规划.py'],detail:'运行 原测试目录 中的测试，比较功能结果是否符合已有断言。',prerequisites:'需要项目所需依赖；发现命令不代表环境就绪或已有测试通过。'}],warnings:['发现 README，可用于确认环境说明；不会执行其中的自由文本命令。']};
 const f=await mount('workbench_assets',{source});try{await f.controller.inspectRepository(source.path);const original=JSON.stringify(source),radio=f.get('check-recommendations').querySelector('input');f.controller.setLanguage('en');assert.equal(f.get('check-recommendations').querySelector('input'),radio);assert.match(f.get('check-recommendations').textContent,/Python functional tests \(unittest\)/);assert.match(f.get('check-recommendations').textContent,/Run tests in 原测试目录/);assert.ok(f.get('check-recommendations').textContent.includes('正在规划.py'));assert.match(f.get('check-warnings').textContent,/README found/);assert.equal(f.get('test-command').value,JSON.stringify([source.checks.recommendations[0].argv]));assert.equal(JSON.stringify(source),original);const calls=JSON.stringify(f.calls);f.controller.setLanguage('zh-CN');assert.match(f.get('check-recommendations').textContent,/Python 功能测试/);assert.equal(JSON.stringify(f.calls),calls);}finally{f.controller.dispose();}
});
test('read-only report uses status-bound translated product descriptions without mutating the original report',async()=>{
 const analysis={mode:'local_readonly',summary:'只读分析完成；未运行功能测试，项目仍尚未验证。',syntax_results:[{file:'正在规划.py',status:'parsed',detail:'语法可解析；不代表功能正确。'}],analyzed_files:['正在规划.py'],next_steps:['确认项目启动方式和依赖环境。']};
 const original=JSON.stringify(analysis),f=await mount('workbench_assets',{source:inspected,analysis});try{await f.controller.inspectRepository(inspected.path);await f.get('analyze-repo').onclick();const report=f.get('readonly-report'),node=report.firstChild,calls=JSON.stringify(f.calls);f.controller.setLanguage('en');assert.equal(report.firstChild,node);assert.match(report.textContent,/Read-only analysis complete/);assert.match(report.textContent,/Syntax parsed/);assert.match(report.textContent,/Confirm project startup/);assert.ok(report.textContent.includes('正在规划.py'));assert.equal(JSON.stringify(analysis),original);assert.equal(JSON.stringify(f.calls),calls);}finally{f.controller.dispose();}
});
test('monitor UI changes status and exit-code labels while preserving test stdout, stderr and event message with zero POSTs',async()=>{
 const monitor={id:'original-monitor',enabled:true,spec:{path:'C:/原仓库',branch:'main',interval_seconds:300},requests_remaining:5,requests_reserved_unknown:1,events:[{id:'original-event',sha:'a'.repeat(40),status:'healthy',message:raw,tests:[{command:['node','原测试.mjs'],returncode:0,timed_out:false,stdout:raw,stderr:'原始 stderr'}]}]};
 Object.assign(monitor,{requests_reserved_active:2,requests_ended_unknown:1,requests_reserved_unknown:3});
 const f=await mount('workbench_assets',{state:{monitors:[monitor]}});try{const node=f.get('monitors').firstChild,calls=JSON.stringify(f.calls);assert.ok(f.get('monitors').textContent.includes(raw));f.controller.setLanguage('en');assert.equal(f.get('monitors').firstChild,node);assert.match(f.get('monitors').textContent,/In-flight reservations: 2 · Ended, unverified: 1/);assert.match(f.get('monitors').textContent,/Authorized tests passed/);assert.match(f.get('monitors').textContent,/Exit code: 0/);assert.ok(f.get('monitors').textContent.includes(raw));assert.ok(f.get('monitors').textContent.includes('原始 stderr'));assert.equal(JSON.stringify(f.calls),calls);assert.equal(f.calls.some(c=>c.method==='POST'),false);}finally{f.controller.dispose();}
});
test('language assets contain no DOM guessing observer or network translator and expose the display-only contract',async()=>{
 for(const name of ['workbench_assets','local_ui_assets']){const code=await asset(name,'app.js');assert.doesNotMatch(code,/MutationObserver|translate\.google|api\.deepl|translateText/);assert.match(code,/setLanguage/);assert.match(code,/options\.language/);assert.match(code,/\[data-i18n\]/);}
});
