import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import {transformSync} from 'rolldown/utils';
import React from 'react';

// Exercise the real App and handlers in memory. Suppress its browser bootstrap
// only; retain imports, request adapter, all hooks and production routing logic.
const mainUrl=new URL('../src/main.tsx',import.meta.url).href;
registerHooks({load(url,context,next){
 if(url.endsWith('/capabilities.json'))return {format:'module',source:'export default '+readFileSync(new URL(url),'utf8'),shortCircuit:true};
 if(url!==mainUrl)return next(url,context);
 const source=readFileSync(new URL(url),'utf8');
 const bootstrap=/^createRoot\(document\.getElementById\('root'\)!\)\.render\(.*\);\s*$/m;
 assert.match(source,bootstrap,'main bootstrap changed; update test loader explicitly');
 const result=transformSync(fileURLToPath(url),source.replace(bootstrap,'export {App};'),{jsx:{runtime:'classic'}});
 assert.equal(result.errors?.length||0,0);return {format:'module',source:result.code,shortCircuit:true};
}});
let environment;
const tabSession={token:'synthetic-hub-capability',expire(){throw Error('unexpected session expiry');},
 selectedView:()=>environment?.original.id,saveView:id=>environment?.savedViews.push(id)};
globalThis.window={RepoCareTabSession:{open:()=>tabSession}};
const {App}=await import(mainUrl);
const {captureForm,applyProposal}=await import('../src/form-assist.mjs');

function nodes(n,check){return !n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(v=>nodes(v,check)):
 [...(check(n)?[n]:[]),...nodes(n.props?.children,check)];}
function text(n){return n==null||typeof n==='boolean'?'':typeof n!=='object'?String(n):Array.isArray(n)?n.map(text).join(''):text(n.props?.children);}
class Element {
 constructor(){this.children=[];this.dataset={};this.isConnected=true;}
 replaceChildren(...children){this.children=children;}
 append(...children){this.children.push(...children);}
 querySelectorAll(){return [];}
 focus(){}
}
const clone=value=>structuredClone(value);
const response=value=>({ok:true,status:200,json:async()=>clone(value),text:async()=>'<div></div>'});
const original={id:'original-workspace',repository_id:'stable-repository',repository:'C:/synthetic/repository',
 token:'original-workspace-capability',api_prefix:'/api/workspaces/original-workspace/',model_profile_id:'legacy',model_name:'原配置',request_budget:50};
const second={...original,id:'second-workspace',token:'second-workspace-capability',api_prefix:'/api/workspaces/second-workspace/',
 model_profile_id:'a'.repeat(32),model_name:'开发模型'};
const modelProfiles={revision:'r1',default_profile_id:'legacy',max_profiles:32,profiles:[
 {id:'legacy',name:'原配置',model:'model-a',legacy:true,selectable:true},
 {id:second.model_profile_id,name:'开发模型',model:'model-b',legacy:false,selectable:true}]};
function initialState(record){return {repository:record.repository,active:null,model_profile_id:record.model_profile_id,
 model_name:record.model_name,provider:record.model_name,request_budget:50,requests_used:record===original?7:0,
 requests_remaining:record===original?43:50,requests_reserved_unknown:0,policy:{allowed_paths:['app.py']},
 runtime:{code_changed:false},jobs:[],runs:[],plans:record===original?[{id:'original-plan',status:'awaiting_approval',plan:{issue:'原模型旧审批任务'}}]:[]};}

function mount(options={}){
 const originals=new Map(['window','document','sessionStorage','localStorage','DOMParser','fetch','setInterval','clearInterval','setTimeout','clearTimeout'].map(key=>[key,globalThis[key]]));
 const additional=options.additionalWorkspaces||[],calls=[],mounts=[],savedViews=[],timers=new Map(),controllerEntries=new Map(),states=new Map([[original.id,initialState(original)],[second.id,initialState(second)],...additional.map(record=>[record.id,initialState(record)])]);
 const workspaces=[clone(original),...additional.map(clone)],storage=new Map([['repocare.ui.v1',JSON.stringify({version:1,theme:'light',fontSize:15,page:'tasks',repository:original.repository_id,task:'plan:original-plan',panel:null})]]);
 const fixture={original,calls,mounts,savedViews,states,workspaces,contextPosts:[],createCalls:[],contextJob:null};environment=fixture;
 const sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};
 const localValues=new Map(options.preferences?[['repocare.preferences.v1',JSON.stringify(options.preferences)]]:[]);
 globalThis.localStorage={getItem:key=>localValues.get(key)||null,setItem:(key,value)=>localValues.set(key,value)};fixture.localValues=localValues;
 const doc={documentElement:{dataset:{}},activeElement:null,createElement:()=>new Element(),importNode:()=>new Element(),addEventListener(){},removeEventListener(){}};
 let nextTimer=0;
 globalThis.sessionStorage=sessionStorage;globalThis.document=doc;globalThis.DOMParser=class{parseFromString(){return {body:{children:[]}};}};
 globalThis.setInterval=(fn,ms)=>{const id=++nextTimer;timers.set(id,{fn,ms,repeat:true});return id;};
 globalThis.setTimeout=(fn,ms)=>{const id=++nextTimer;timers.set(id,{fn,ms,repeat:false});return id;};
 globalThis.clearInterval=globalThis.clearTimeout=id=>timers.delete(id);
 globalThis.window={RepoCareTabSession:{open:()=>tabSession},sessionStorage,
  mountWorkbenchOnboarding:()=>({dispose(){}}),
  mountMaintenanceUI(root,config){
   const record=[original,second,...additional].find(w=>w.api_prefix===config.apiPrefix);assert.ok(record,'controller must bind an exact original workspace');
   mounts.push({id:record.id,token:config.token,prefix:config.apiPrefix,root});
   const controller={select(selection){fixture.selections??=[];fixture.selections.push({id:record.id,selection:clone(selection)});},
    getState:()=>states.get(record.id),refresh:async()=>config.onState(clone(states.get(record.id))),dispose(){},
    async createTask(issue){fixture.createCalls.push({workspace_id:record.id,token:config.token,issue});
     if(options.createTask)return options.createTask(record,issue,fixture);
     return {status:'interrupted'};}};
   controllerEntries.set(record.id,{controller,config});config.onState(clone(states.get(record.id)));return controller;}};
 globalThis.fetch=async(path,init={})=>{
  const method=init.method||'GET',body=init.body?JSON.parse(init.body):undefined;
  const call={path:String(path),method,body,authorization:init.headers?.Authorization};calls.push(call);
  if(options.fetch){const result=await options.fetch(call,fixture);if(result!==undefined)return result;}
  if(path==='/onboarding.html'||path==='/maintenance.html')return response({});
  if(path==='/api/provider-config')return response({configured:true});
  if(path==='/api/model-profiles')return response(modelProfiles);
  if(path==='/api/delivery')return response({schema_version:1,read_only:true,items:[]});
  if(path==='/api/state')return response({workspaces:clone(workspaces),monitors:[],jobs:fixture.contextJob?[fixture.contextJob]:[]});
  throw Error(`Unexpected synthetic request: ${method} ${path}`);
 };
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
 let cursor=0,dirty=false,tree,effects=[],closed=false;
 const equal=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
 const dispatch={useState(initial){const i=cursor++;hooks[i]??={value:typeof initial==='function'?initial():initial};
  return [hooks[i].value,next=>{const value=typeof next==='function'?next(hooks[i].value):next;if(!Object.is(value,hooks[i].value)){hooks[i].value=value;dirty=true;}}];},
  useRef(value){const i=cursor++;return hooks[i]??={current:value};},
  useEffect(fn,deps){const i=cursor++;if(!hooks[i]||!equal(hooks[i].deps,deps)){const cleanup=hooks[i]?.cleanup;hooks[i]={deps};effects.push(()=>{cleanup?.();hooks[i].cleanup=fn();});}}};
 function render(){let rounds=0;do{assert.ok(++rounds<40,'App did not settle');dirty=false;cursor=0;effects=[];
  const previous=internal.H;internal.H=dispatch;try{tree=App();}finally{internal.H=previous;}
  for(const node of nodes(tree,n=>n.props?.ref&&typeof n.props.ref==='object'))node.props.ref.current??=new Element();
  for(const effect of effects)effect();}while(dirty);return tree;}
 async function drain(){for(let i=0;i<35;i++){await Promise.resolve();if(dirty)render();}}
 async function advance(maxMs=1000){for(const [id,timer] of [...timers])if(timer.ms<=maxMs){if(!timer.repeat)timers.delete(id);timer.fn();}await drain();}
 function close(){if(closed)return;closed=true;for(const hook of hooks)hook?.cleanup?.();for(const [key,value] of originals){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}environment=null;}
 render();return {fixture,render,drain,advance,close,tree:()=>tree,text:()=>text(tree),
  selector:()=>nodes(tree,n=>n.type?.name==='ModelTaskSelector')[0],
  composer:()=>nodes(tree,n=>n.type==='textarea'&&['新维护任务的问题描述','New maintenance task description'].includes(n.props['aria-label']))[0],
  button:label=>nodes(tree,n=>n.props?.['aria-label']===label||(n.type==='button'||n.type?.name==='AsyncButton')&&text(n)===label)[0],
  async chooseProfile(id){const selector=this.selector();assert.ok(selector,'real App must render model selector');selector.props.onChange(id);render();await drain();},
  async fillDraft(value){const textarea=this.composer();assert.ok(textarea);textarea.props.onChange({target:{value}});render();},
  controllers:controllerEntries};
}

function completeContext(fixture,record=second){
 if(!fixture.workspaces.some(w=>w.id===record.id))fixture.workspaces.push(clone(record));
 fixture.contextJob={id:fixture.contextJob.id,status:'finished',result:{workspace:clone(record)}};
}
function contextFetcher({pollsBeforeFinish=0,unknownPost=false,resultRecord=second}={}){
 return async(call,fixture)=>{
  if(call.path==='/api/actions/task_model_workspace'){
   assert.equal(call.method,'POST');fixture.contextPosts.push(clone(call));
   fixture.contextJob={id:call.body.request_id,status:'running'};fixture.contextPolls=0;
   if(unknownPost)throw Error('synthetic response lost after submission');
   return response(fixture.contextJob);
  }
  if(call.path==='/api/state'&&fixture.contextJob){
   fixture.contextPolls++;
   if(fixture.contextPolls>pollsBeforeFinish&&!unknownPost)completeContext(fixture,resultRecord);
  }
 };
}

test('App profile selection changes only the new-task choice; old review retains its original controller and capability',async()=>{
 const ui=mount();try{
  await ui.drain();assert.equal(ui.selector().props.currentId,'legacy');assert.equal(ui.selector().props.value,'legacy');
  const mountsBefore=ui.fixture.mounts.length;
  await ui.fillDraft('尚未提交的新任务草稿');await ui.chooseProfile(second.model_profile_id);
  assert.equal(ui.selector().props.value,second.model_profile_id);assert.equal(ui.selector().props.currentId,'legacy');
  assert.equal(ui.fixture.calls.filter(c=>c.method==='POST').length,0);assert.equal(ui.fixture.mounts.length,mountsBefore);
  const review=ui.button('审阅方案与审批');assert.ok(review);review.props.onClick();ui.render();
  assert.deepEqual(ui.fixture.selections.at(-1),{id:original.id,selection:{kind:'plan',id:'original-plan'}});
  assert.deepEqual(ui.fixture.mounts.map(({id,token,prefix})=>({id,token,prefix})),[
   {id:original.id,token:original.token,prefix:original.api_prefix}]);
  assert.equal(ui.composer().props.value,'尚未提交的新任务草稿');assert.equal(ui.fixture.createCalls.length,0);
 }finally{ui.close();}
});

test('explicit apply sends one context POST, polls only read-only state, and preserves repository draft and old controller',async()=>{
 const ui=mount({fetch:contextFetcher({pollsBeforeFinish:1})});try{
  await ui.drain();await ui.fillDraft('切换模型后仍需保留的草稿');await ui.chooseProfile(second.model_profile_id);
  const apply=ui.selector().props.onApply,pending=apply();await ui.drain();
  assert.equal(ui.fixture.contextPosts.length,1);assert.equal(ui.selector().props.busy,true);
  await apply();await ui.advance();await pending;await ui.drain();
  const post=ui.fixture.contextPosts[0];assert.deepEqual(Object.keys(post.body).sort(),['confirmed','profile_id','request_id','workspace_id']);
  assert.equal(post.authorization,'Bearer synthetic-hub-capability');assert.equal(post.body.workspace_id,original.id);
  assert.equal(post.body.profile_id,second.model_profile_id);assert.equal(post.body.confirmed,true);assert.match(post.body.request_id,/^[0-9a-f]{32}$/);
  const after=ui.fixture.calls.slice(ui.fixture.calls.findIndex(c=>c.method==='POST')+1);
  assert.ok(after.filter(c=>c.path==='/api/state').length>=2);assert.ok(after.every(c=>c.method==='GET'));
  assert.equal(ui.selector().props.currentId,second.model_profile_id);assert.equal(ui.composer().props.value,'切换模型后仍需保留的草稿');
  assert.equal(ui.fixture.createCalls.length,0);assert.equal(ui.fixture.contextPosts.length,1);
  assert.deepEqual(ui.fixture.mounts.map(m=>[m.id,m.token,m.prefix]),[
   [original.id,original.token,original.api_prefix],[second.id,second.token,second.api_prefix]]);
  assert.equal(ui.fixture.states.get(original.id).requests_used,7);assert.equal(ui.fixture.states.get(original.id).requests_remaining,43);
  const originalRepository=nodes(ui.tree(),n=>n.type==='button'&&n.props.className?.includes('repository-row')&&n.props.title===original.repository+' · '+original.model_name)[0];
  assert.ok(originalRepository);originalRepository.props.onClick();ui.render();await ui.drain();
  const oldTask=nodes(ui.tree(),n=>n.type==='button'&&n.props.className?.includes('task-row')&&text(n).includes('原模型旧审批任务'))[0];
  assert.ok(oldTask);oldTask.props.onClick();ui.render();await ui.drain();
  ui.button('审阅方案与审批').props.onClick();ui.render();
  assert.deepEqual(ui.fixture.selections.at(-1),{id:original.id,selection:{kind:'plan',id:'original-plan'}});
  assert.equal(ui.fixture.mounts.length,2,'returning to A reuses its original controller');
  assert.equal(ui.selector().props.currentId,'legacy');assert.equal(ui.composer().props.value,'切换模型后仍需保留的草稿');
  assert.equal(ui.fixture.contextPosts.length,1);assert.equal(ui.fixture.createCalls.length,0);
 }finally{ui.close();}
});

test('new-task submission automatically resolves selected model and invokes only its controller; completed task clears draft',async()=>{
 const ui=mount({fetch:contextFetcher(),createTask(record,issue,fixture){
  assert.equal(record.id,second.id);const state=fixture.states.get(record.id);
  state.plans.push({id:'second-plan',status:'awaiting_approval',plan:{issue}});
  return {status:'finished',result:{plan_id:'second-plan'}};
 }});try{
  await ui.drain();await ui.fillDraft('使用第二模型的新任务');await ui.chooseProfile(second.model_profile_id);
  const pending=ui.button('生成新任务方案').props.onClick();await ui.drain();await pending;await ui.drain();
  assert.deepEqual(ui.fixture.createCalls,[{workspace_id:second.id,token:second.token,issue:'使用第二模型的新任务'}]);
  assert.equal(ui.fixture.contextPosts.length,1);assert.equal(ui.selector().props.currentId,second.model_profile_id);
  assert.equal(ui.composer().props.value,'');assert.equal(ui.fixture.states.get(original.id).plans[0].id,'original-plan');
 }finally{ui.close();}
});

test('interrupted generation keeps draft in the selected model and never falls back to the old model',async()=>{
 const ui=mount({fetch:contextFetcher()});try{
  await ui.drain();await ui.fillDraft('生成未完成应保留');await ui.chooseProfile(second.model_profile_id);
  const pending=ui.button('生成新任务方案').props.onClick();await ui.drain();await pending;await ui.drain();
  assert.equal(ui.composer().props.value,'生成未完成应保留');assert.match(ui.text(),/草稿已保留/);
  assert.equal(ui.fixture.createCalls.length,1);assert.equal(ui.fixture.createCalls[0].workspace_id,second.id);
  assert.equal(ui.fixture.contextPosts.length,1);assert.equal(ui.fixture.states.get(original.id).plans.length,1);
 }finally{ui.close();}
});

test('lost context response holds resubmission until explicit read-only reconciliation and retains the draft',async()=>{
 const ui=mount({fetch:contextFetcher({unknownPost:true})});try{
  await ui.drain();await ui.fillDraft('未知结果不能重发或丢草稿');await ui.chooseProfile(second.model_profile_id);
  const apply=ui.selector().props.onApply;await apply();await ui.drain();
  assert.equal(ui.fixture.contextPosts.length,1);assert.match(ui.text(),/上次模型会话操作尚待核对/);
  assert.equal(ui.selector().props.busy,true);await apply();await ui.drain();
  assert.equal(ui.fixture.contextPosts.length,1);assert.equal(ui.fixture.createCalls.length,0);
  assert.equal(ui.composer().props.value,'未知结果不能重发或丢草稿');
  completeContext(ui.fixture);await ui.button('核对上次模型切换').props.onClick();await ui.drain();
  assert.equal(ui.fixture.contextPosts.length,1);assert.equal(ui.selector().props.currentId,second.model_profile_id);
  assert.equal(ui.selector().props.busy,false);assert.equal(ui.composer().props.value,'未知结果不能重发或丢草稿');
  assert.equal(ui.fixture.createCalls.length,0);
 }finally{ui.close();}
});

test('known context rejection leaves old model and draft intact without retrying or generating',async()=>{
 const ui=mount({fetch:async(call,fixture)=>{
  if(call.path==='/api/actions/task_model_workspace'){
   fixture.contextPosts.push(call);return {ok:false,status:400,json:async()=>({error:'配置已变化，请重新读取模型列表。'})};
  }
 }});try{
  await ui.drain();await ui.fillDraft('被拒绝后保留的草稿');await ui.chooseProfile(second.model_profile_id);
  const pending=ui.button('生成新任务方案').props.onClick();await ui.drain();await pending;await ui.drain();
  assert.equal(ui.fixture.contextPosts.length,1);assert.equal(ui.fixture.createCalls.length,0);
  assert.equal(ui.selector().props.currentId,'legacy');assert.equal(ui.composer().props.value,'被拒绝后保留的草稿');
  assert.match(ui.text(),/配置已变化/);assert.equal(ui.fixture.mounts.length,1);assert.equal(ui.selector().props.busy,false);
 }finally{ui.close();}
});

test('foreign context receipt cannot bind a controller or submit a draft under the wrong repository',async()=>{
 const ui=mount({fetch:contextFetcher({resultRecord:{...second,repository:'C:/foreign-repository'}})});try{
  await ui.drain();await ui.fillDraft('只能提交给当前仓库');await ui.chooseProfile(second.model_profile_id);
  const pending=ui.button('生成新任务方案').props.onClick();await ui.drain();await pending;await ui.drain();
  assert.equal(ui.fixture.contextPosts.length,1);assert.equal(ui.fixture.createCalls.length,0);
  assert.equal(ui.fixture.mounts.length,1);assert.equal(ui.selector().props.currentId,'legacy');
  assert.equal(ui.composer().props.value,'只能提交给当前仓库');assert.match(ui.text(),/模型会话回执不完整/);
 }finally{ui.close();}
});

test('App opens the real assistant and help without a repository and never creates a task on entry',async()=>{
 const ui=mount({fetch:async(call)=>call.path==='/api/state'?response({workspaces:[],monitors:[],jobs:[]}):undefined});try{
  await ui.drain();ui.button('新会话').props.onClick();ui.render();const assistant=nodes(ui.tree(),n=>n.type?.name==='AssistantPane')[0];assert.ok(assistant);assert.equal(assistant.props.language,'zh-CN');assert.equal(typeof assistant.props.getFormRoot,'function');
  ui.button('使用教程').props.onClick();ui.render();const help=nodes(ui.tree(),n=>n.type?.name==='HelpDialog')[0];assert.ok(help);assert.equal(help.props.language,'zh-CN');assert.equal(ui.fixture.calls.some(call=>call.method==='POST'),false);assert.equal(ui.fixture.createCalls.length,0);assert.equal(ui.fixture.mounts.length,0);
 }finally{ui.close();}
});
test('App persists English menus and real settings/help/assistant props while retaining the original workspace, task and draft',async()=>{
 const ui=mount();try{
  await ui.drain();await ui.fillDraft('保留原任务草稿');const main=nodes(ui.tree(),n=>n.type==='main')[0].props.ref.current;
  ui.button('设置').props.onClick();ui.render();const settings=nodes(ui.tree(),n=>n.type?.name==='SettingsWindow')[0];assert.ok(settings);settings.props.onPrefsChange({...settings.props.prefs,language:'en',theme:'dark'});ui.render();await ui.drain();
  assert.ok(ui.button('New session'));assert.ok(ui.button('Getting started'));assert.ok(ui.button('Memory and knowledge'));assert.equal(JSON.parse(ui.fixture.localValues.get('repocare.preferences.v1')).language,'en');
  const updated=nodes(ui.tree(),n=>n.type?.name==='SettingsWindow')[0];updated.props.onOpenAssistant();ui.render();const embedded=nodes(ui.tree(),n=>n.type?.name==='SettingsWindow')[0].props.assistant;assert.equal(embedded.type.name,'AssistantPane');assert.equal(embedded.props.language,'en');assert.ok(embedded.props.memory);assert.equal(nodes(ui.tree(),n=>n.type==='main')[0].props.ref.current,main);
  updated.props.onClose();ui.render();const docked=nodes(ui.tree(),n=>n.type?.name==='AssistantPane')[0];assert.ok(docked);assert.equal(docked.props.memory,embedded.props.memory,'both parents must use the same App-owned memory ref');assert.equal(ui.composer().props.value,'保留原任务草稿');assert.equal(ui.selector().props.currentId,'legacy');assert.equal(ui.fixture.mounts.length,1);assert.equal(ui.fixture.calls.some(call=>call.method==='POST'),false);
 }finally{ui.close();}
});
test('App expires a captured form on navigation even when the workspace DOM root is reused',async()=>{
 const ui=mount();try{
  await ui.drain();ui.button('新会话').props.onClick();ui.render();const assistant=nodes(ui.tree(),n=>n.type?.name==='AssistantPane')[0],root=assistant.props.getFormRoot();
  const field={tagName:'INPUT',type:'text',isConnected:true,value:'original',labels:[{textContent:'描述'}],id:'description',name:'description',getAttribute(name){return name==='data-form-assist-draft'?'true':null;},closest(){return null;},matches(){return false;},getClientRects(){return [{}];}};root.querySelectorAll=()=>[field];
  const snapshot=await captureForm(root);assert.ok(snapshot);ui.button('记忆与知识').props.onClick();ui.render();const proposal={form_id:snapshot.form_id,revision:snapshot.revision,changes:[{field_id:snapshot.fields[0].id,value:'must not apply'}],draft_only:true};
  assert.deepEqual(await applyProposal(root,snapshot,proposal),{applied:false,reason:'stale'});assert.equal(field.value,'original');assert.equal(ui.fixture.calls.some(call=>call.method==='POST'),false);
 }finally{ui.close();}
});
test('App notification diffs ignore first-read history, alert once, dismiss, and navigate the original approval without writes',async()=>{
 const ui=mount();try{
  await ui.drain();assert.equal(nodes(ui.tree(),n=>n.props?.className==='notification-open').length,0);const current=ui.fixture.states.get(original.id);current.plans.push({id:'new-plan',status:'awaiting_approval',plan:{issue:'新审批'}});
  await ui.controllers.get(original.id).controller.refresh();ui.render();const notices=nodes(ui.tree(),n=>n.props?.className==='notification-open');assert.equal(notices.length,1);assert.match(text(notices[0]),/需要审批/);
  await ui.controllers.get(original.id).controller.refresh();ui.render();assert.equal(nodes(ui.tree(),n=>n.props?.className==='notification-open').length,1);notices[0].props.onClick();ui.render();await ui.drain();assert.deepEqual(ui.fixture.selections.at(-1),{id:original.id,selection:{kind:'plan',id:'new-plan'}});assert.equal(ui.fixture.calls.some(call=>call.method==='POST'),false);
  nodes(ui.tree(),n=>n.props?.className==='notification-dismiss')[0].props.onClick();ui.render();await ui.controllers.get(original.id).controller.refresh();ui.render();assert.equal(nodes(ui.tree(),n=>n.props?.className==='notification-open').length,0);
 }finally{ui.close();}
});
test('App emits one service-disconnect alert per outage and uses the current notification preference',async()=>{
 const ui=mount({fetch:async(call,fixture)=>{if(call.path==='/api/state'&&fixture.failHub)throw Error('offline');}});try{
  await ui.drain();ui.fixture.failHub=true;await ui.advance(3000);assert.equal(nodes(ui.tree(),n=>n.props?.className==='notification-open').length,1);await ui.advance(3000);assert.equal(nodes(ui.tree(),n=>n.props?.className==='notification-open').length,1);
  ui.fixture.failHub=false;await ui.advance(3000);assert.equal(nodes(ui.tree(),n=>n.props?.className==='notification-open').length,2);
  ui.button('设置').props.onClick();ui.render();const settings=nodes(ui.tree(),n=>n.type?.name==='SettingsWindow')[0];settings.props.onPrefsChange({...settings.props.prefs,notifications:{...settings.props.prefs.notifications,inApp:false}});ui.render();ui.fixture.failHub=true;await ui.advance(3000);assert.equal(nodes(ui.tree(),n=>n.props?.className==='notification-open').length,2);
 }finally{ui.close();}
});

const reasoningSource={...modelProfiles.profiles[0],base_url:'https://provider.invalid/v1',context_window_tokens:1000000,max_output_tokens:256000,
 reasoning_protocol:'openai',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'max'}]};
const reasoningDerived={...reasoningSource,id:'d'.repeat(32),reasoning_effort:'max'};
const reasoningCatalog={...modelProfiles,profiles:[reasoningSource,...modelProfiles.profiles.slice(1)]};
const variantReceipt={...reasoningCatalog,revision:'r2',profiles:[...reasoningCatalog.profiles,reasoningDerived],created_profile_id:reasoningDerived.id};

test('App retains variant unknown across page remounts and only an explicit successful catalog read releases the barrier',async()=>{
 let writes=0,failRead=false;
 const ui=mount({fetch:async(call)=>{
  if(call.path!=='/api/model-profiles')return;
  if(call.method==='POST'){writes++;throw Error('synthetic response lost');}
  if(failRead)throw Error('synthetic read unavailable');return response(reasoningCatalog);
 }});
 try{
  await ui.drain();await assert.rejects(ui.selector().props.onReasoningVariant('legacy','max'));ui.render();await ui.drain();assert.equal(writes,1);assert.equal(ui.selector().props.variantState,'unknown');
  ui.button('工作概览').props.onClick();ui.render();ui.button('维护任务').props.onClick();ui.render();await ui.drain();assert.equal(ui.selector().props.variantState,'unknown');
  await assert.rejects(ui.selector().props.onReasoningVariant('legacy','max'));assert.equal(writes,1);
  failRead=true;await assert.rejects(ui.selector().props.onRefresh());ui.render();assert.equal(ui.selector().props.variantState,'unknown');
  failRead=false;await ui.selector().props.onRefresh();ui.render();await ui.drain();assert.equal(ui.selector().props.variantState,null);assert.equal(writes,1);assert.equal(ui.fixture.contextPosts.length,0);
 }finally{ui.close();}
});

for(const action of ['navigate','settings'])test(`App delayed variant after ${action} only updates the catalog and never applies or changes the new-task model`,async()=>{
 let resolve;
 const ui=mount({fetch:async(call)=>{
  if(call.path==='/api/model-profiles')return call.method==='POST'?new Promise(done=>{resolve=()=>done(response(variantReceipt));}):response(reasoningCatalog);
 }});
 try{
  await ui.drain();const pending=ui.selector().props.onReasoningVariant('legacy','max');await ui.drain();
  ui.button(action==='navigate'?'工作概览':'设置').props.onClick();ui.render();resolve();await assert.rejects(pending);await ui.drain();
  assert.equal(ui.fixture.contextPosts.length,0);assert.equal(ui.fixture.calls.filter(call=>call.method==='POST').length,1);
  if(action==='settings'){const settings=nodes(ui.tree(),node=>node.type?.name==='SettingsWindow')[0];assert.ok(settings);assert.equal(settings.props.initialProfiles.revision,'r2');settings.props.onClose();ui.render();}
  ui.button('维护任务').props.onClick();ui.render();assert.equal(ui.selector().props.value,'legacy');assert.equal(ui.selector().props.profiles.revision,'r2');
 }finally{ui.close();}
});

test('finishing an older submission cannot erase a newer repository draft typed while waiting',async()=>{
 let finish;
 const ui=mount({createTask:()=>new Promise(resolve=>{finish=()=>resolve({status:'finished',result:{}});})});
 try{
  await ui.drain();await ui.fillDraft('提交的旧问题');const pending=ui.button('生成新任务方案').props.onClick();await ui.drain();assert.equal(ui.fixture.createCalls.length,1);
  await ui.fillDraft('等待期间写下的新问题');finish();await pending;await ui.drain();assert.equal(ui.composer().props.value,'等待期间写下的新问题');assert.equal(ui.fixture.createCalls[0].issue,'提交的旧问题');
 }finally{ui.close();}
});

test('a variant finishing after switching repositories cannot overwrite the new repository choice even when both use the same profile',async()=>{
 const foreign={...original,id:'foreign-workspace',repository_id:'other-repository',repository:'C:/synthetic/other-repository',api_prefix:'/api/workspaces/foreign-workspace/',token:'foreign-workspace-capability'};let resolve;
 const ui=mount({additionalWorkspaces:[foreign],fetch:async(call)=>{
  if(call.path==='/api/model-profiles')return call.method==='POST'?new Promise(done=>{resolve=()=>done(response(variantReceipt));}):response(reasoningCatalog);
 }});
 try{
  await ui.drain();const pending=ui.selector().props.onReasoningVariant('legacy','max');await ui.drain();
  const repository=nodes(ui.tree(),node=>node.type==='button'&&node.props.className?.includes('repository-row')&&node.props.title?.startsWith(foreign.repository+' · '))[0];assert.ok(repository);assert.equal(repository.props.disabled,false);repository.props.onClick();ui.render();await ui.drain();
  resolve();await assert.rejects(pending);await ui.drain();assert.equal(ui.button('工作概览').props['aria-current'],'page');assert.equal(ui.fixture.contextPosts.length,0);
  ui.button('维护任务').props.onClick();ui.render();assert.equal(ui.selector().props.value,'legacy');assert.equal(ui.selector().props.currentId,'legacy');assert.equal(ui.selector().props.profiles.revision,'r2');assert.match(ui.text(),/other-repository/);
 }finally{ui.close();}
});


test('unapplied reasoning draft blocks task submission without clearing the problem or touching the old account',async()=>{
 const ui=mount();try{
  await ui.drain();await ui.fillDraft('未应用的思考强度草稿');
  ui.selector().props.onPendingChange(true);ui.render();
  assert.equal(ui.button('生成新任务方案').props.disabled,true);
  await ui.button('生成新任务方案').props.onClick();await ui.drain();
  assert.equal(ui.fixture.contextPosts.length,0);assert.equal(ui.fixture.createCalls.length,0);
  assert.equal(ui.composer().props.value,'未应用的思考强度草稿');
  assert.equal(ui.fixture.states.get(original.id).requests_used,7);
  ui.selector().props.onPendingChange(false);ui.render();
  assert.equal(ui.button('生成新任务方案').props.disabled,false);
  assert.equal(ui.fixture.createCalls.length,0);
 }finally{ui.close();}
});
