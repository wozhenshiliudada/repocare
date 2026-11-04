import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {setUiLanguage,ui} from '../src/ui-language.mjs';
import {parseAst} from 'rolldown/parseAst';
const {WorkbenchPages,Evidence}=await import('../src/WorkbenchPages.tsx');
const {IssueProgressPanel}=await import('../src/IssueProgressPanel.tsx');
const {ReferenceSearch}=await import('../src/MemoryAdvanced.tsx');
const {AcceptanceWatchControls}=await import('../src/AcceptanceWatchControls.tsx');
const {ProviderSettings}=await import('../src/ProviderSettings.tsx');
const {ToolCapability}=await import('../src/ToolCapability.tsx');
const render=(type,props)=>renderToStaticMarkup(React.createElement(type,props));
const noAction=()=>{throw Error('Rendering or drafting performed a request/action');};
const state={repository:'C:/fixture',jobs:[],plans:[],runs:[],requests_used:0,requests_remaining:10,requests_reserved_unknown:0,
 policy:{},runtime:{},issue_workbench:{},memory_catalog:{},maintenance_memory:{observations:[]},source_memory:{}};
const props={record:{id:'workspace',repository:state.repository},state,hub:{jobs:[],monitors:[]},rows:[],request:noAction,refresh:noAction,
 navigate:noAction,connect:noAction,openOriginal:noAction,selectTask:noAction};

test('overview, Issue, memory, monitoring, settings and service owned UI copy switches to English',()=>{
 try{
  const expected={overview:'A workflow for maintenance.',issues:'Issue workflow',memory:'Unified memory query',events:'Monitor events',configuration:'Maintenance settings',service:'Know exactly what is running.'};
  for(const [page,label] of Object.entries(expected)){
   setUiLanguage('en');const english=render(WorkbenchPages,{...props,page});
   assert.ok(english.includes(label),page+' '+label);
   // The fixture has no raw Chinese reports, model answers or user messages to exempt.
   assert.doesNotMatch(english,/\p{Script=Han}/u,page+' contains untranslated interface text');
   setUiLanguage('zh-CN');assert.match(render(WorkbenchPages,{...props,page}),/\p{Script=Han}/u,page);
  }
 }finally{setUiLanguage('zh-CN');}
});

test('original JSON and user text identical to a known UI label remain unchanged and escaped',()=>{
 try{
  setUiLanguage('en');const raw={user:'服务管理',model:'连接仓库',repository:'工作概览',log:'<script>服务管理</script>'};
  const html=render(Evidence,{value:raw});
  assert.match(html,/Evidence and original fields/);
  for(const text of ['服务管理','连接仓库','工作概览'])assert.ok(html.includes(text));
  assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
 }finally{setUiLanguage('zh-CN');}
});

function nodes(node,predicate){return !node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(n=>nodes(n,predicate)):
 [...(predicate(node)?[node]:[]),...nodes(node.props?.children,predicate)];}
function text(node){return node==null||typeof node==='boolean'?'':typeof node!=='object'?String(node):Array.isArray(node)?node.map(text).join(''):text(node.props?.children);}
function hooks(Component,props,overrides=[]){
 const internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE,old=internal.H;
 let stateIndex=0;
 internal.H={useState:value=>{const index=stateIndex++;return [index in overrides?overrides[index]:typeof value==='function'?value():value,()=>{}];},useRef:value=>({current:value}),useEffect:()=>{}};
 try{return Component(props);}finally{internal.H=old;}
}

test('the same comment button sends byte-identical original action and payload in both languages',async()=>{
 const calls=[],execution={id:'original-execution',run_id:'original-run',investigation_id:'original-investigation'};
 try{
  for(const language of ['zh-CN','en']){
   setUiLanguage(language);const tree=hooks(IssueProgressPanel,{execution,comments:[],output:null,disabled:false,send:async(...args)=>calls.push(JSON.stringify(args))});
   const select=nodes(tree,n=>n.type==='select')[0];
   assert.deepEqual(nodes(select,n=>n.type==='option').map(n=>n.props.value),['accepted','investigated','blocked','candidate_passed','awaiting_review','delivered']);
   const button=nodes(tree,n=>text(n)===ui('预览此阶段的评论')&&typeof n.props?.onClick==='function')[0];
   assert.ok(button);await button.props.onClick();
  }
  assert.equal(calls.length,2);assert.equal(calls[0],calls[1]);
  assert.deepEqual(JSON.parse(calls[0]),['issue_progress_preview',{execution_id:execution.id,stage:'accepted'}]);
 }finally{setUiLanguage('zh-CN');}
});

test('legacy provider saves retain identical raw model IDs, original payloads and secret clearing in both languages',async()=>{
 const initial={revision:'original',editable:true,configured:true,key_set:true,base_url:'https://example.invalid/v1',model:'服务管理',api_key:'must-never-render'};
 const initialProfiles={revision:'profiles',default_profile_id:null,max_profiles:8,profiles:[]},saves=[];
 for(const language of ['zh-CN','en']){
  const calls=[],request=async(path,token,body)=>{calls.push([path,token,body?JSON.stringify(body):null]);return path==='/api/model-profiles'?initialProfiles:initial;};
  const html=render(ProviderSettings,{initial,initialProfiles,request,language});
  assert.ok(html.includes('服务管理'));assert.doesNotMatch(html,/must-never-render/);assert.match(html,/type="password"/);
  assert.ok(html.includes(language==='en'?'Model connection':'模型连接'));
  const tree=hooks(ProviderSettings,{initial,initialProfiles,request,language}),form=nodes(tree,n=>n.type==='form')[0];
  await form.props.onSubmit({preventDefault(){}});
  assert.equal(calls[0][0],'/api/provider-config');saves.push(calls[0][2]);
  assert.equal(JSON.parse(calls[0][2]).model,'服务管理');assert.equal(JSON.parse(calls[0][2]).api_key,'');
  assert.ok(calls.every(([path])=>['/api/provider-config','/api/model-profiles'].includes(path)));
 }
 assert.equal(saves[0],saves[1]);
});

test('newly registered source-search and observation drafts stay in state without requests or implicit consent',()=>{
 try{for(const language of ['zh-CN','en']){
  setUiLanguage(language);
  const search=hooks(ReferenceSearch,{...props,kind:'experience'}),input=nodes(search,n=>n.type==='input')[0];
  assert.equal(input.props['data-form-assist-draft'],'true');input.props.onChange({target:{value:'服务管理'}});
  const observation=hooks(AcceptanceWatchControls,{p:props,row:{origin:{monitor_id:'m',event_id:'e',run_id:'r'},delivery:{}}});
  const fields=nodes(observation,n=>n.type==='input');
  assert.equal(fields.filter(n=>n.props['data-form-assist-draft']==='true').length,2);
  for(const field of fields.filter(n=>n.props.type==='number'))field.props.onChange({target:{value:'90'}});
  assert.equal(nodes(observation,n=>n.type==='input'&&n.props.type==='checkbox'&&n.props['data-form-assist-draft']==='true').length,0);
 }}finally{setUiLanguage('zh-CN');}
});

test('service tool captions use exact known definitions while fields, option values and unknown diagnostics remain original',()=>{
 const definition={title:'固定 Git 版本比较',impact:'只读 Git 提交与差异，不 checkout/fetch 或修改文件。',unavailable_reason:'服务管理',fields:[
  {key:'repository',label:'目标 Git 仓库完整路径',kind:'text',required:true},
  {key:'mode',label:'比较方式',kind:'select',options:['single','two','merge-base'],required:true}]};
 const values={repository:'连接仓库',mode:'two'},before=JSON.stringify({definition,values}),options=[];
 try{for(const language of ['zh-CN','en']){
  setUiLanguage(language);const tree=hooks(ToolCapability,{id:'git-compare',services:{hub:{jobs:[]},request:noAction,refresh:noAction}},[definition,values]);
  assert.ok(text(tree).includes(language==='en'?'Compare fixed Git versions':'固定 Git 版本比较'));
  assert.ok(text(tree).includes('服务管理'));assert.equal(nodes(tree,n=>n.type==='input')[0].props.value,'连接仓库');
  options.push(nodes(tree,n=>n.type==='option').map(n=>n.props.value));
  assert.equal(JSON.stringify({definition,values}),before);
 }}finally{setUiLanguage('zh-CN');}
 assert.deepEqual(options,[['single','two','merge-base'],['single','two','merge-base']]);
});

test('every single-argument UI lookup has an explicit local English message',()=>{
 const excluded=new Set(['ui-language.mjs','ui-messages.mjs','main.tsx','SettingsWindow.tsx','ProviderSettings.tsx','AssistantPane.tsx','HelpDialog.tsx','ModelTaskSelector.tsx']);
 const missing=[];
 try{
  setUiLanguage('en');
  for(const file of fs.readdirSync(new URL('../src/',import.meta.url)).filter(f=>/\.(tsx|mjs)$/.test(f)&&!excluded.has(f))){
   const source=fs.readFileSync(new URL('../src/'+file,import.meta.url),'utf8'),ast=parseAst(source,{lang:file.endsWith('tsx')?'tsx':'js'},file);
   function visit(node){if(!node||typeof node!=='object')return;
    if(node.type==='CallExpression'&&node.callee.type==='Identifier'&&node.callee.name==='ui'&&node.arguments.length===1&&node.arguments[0].type==='Literal'){
     const value=node.arguments[0].value;if(typeof value==='string'&&/\p{Script=Han}/u.test(value)&&/\p{Script=Han}/u.test(ui(value)))missing.push({file,value});
    }
    for(const value of Object.values(node)){if(Array.isArray(value))value.forEach(visit);else if(value&&typeof value==='object')visit(value);}
   }visit(ast);
  }
  assert.deepEqual(missing,[]);
 }finally{setUiLanguage('zh-CN');}
});
