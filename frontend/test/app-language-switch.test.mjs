import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import {transformSync} from 'rolldown/utils';
import React from 'react';
import {getUiLanguage,setUiLanguage} from '../src/ui-language.mjs';

// Run the real App, preference callback, request adapter and effect lifecycle.
// Only browser bootstrap/DOM and native adapters are replaced. Native adapter
// internals and the SettingsWindow save button have their own separate tests.
const mainUrl=new URL('../src/main.tsx',import.meta.url).href;
registerHooks({load(url,context,next){
 if(url.endsWith('/capabilities.json'))return {format:'module',source:'export default '+readFileSync(new URL(url),'utf8'),shortCircuit:true};
 if(url!==mainUrl)return next(url,context);
 const source=readFileSync(new URL(url),'utf8');
 const bootstrap=/^createRoot\(document\.getElementById\('root'\)!\)\.render\(.*\);\s*$/m;
 assert.match(source,bootstrap,'Review the App bootstrap loader when its entry changes');
 const result=transformSync(fileURLToPath(url),source.replace(bootstrap,'export {App};'),{jsx:{runtime:'classic'}});
 assert.equal(result.errors?.length||0,0);
 return {format:'module',source:result.code,shortCircuit:true};
}});
let environment;
const session={token:'synthetic-hub-capability',expire(){throw Error('unexpected session expiry');},
 selectedView:()=>environment?.records[0].id,saveView:id=>environment?.savedViews.push(id)};
globalThis.window={RepoCareTabSession:{open:()=>session}};
const {App}=await import(mainUrl);

const clone=value=>structuredClone(value);
const response=value=>({ok:true,status:200,json:async()=>clone(value),text:async()=>'<div></div>'});
const first={id:'original-workspace',repository_id:'original-repository',repository:'C:/synthetic/original',
 token:'original-capability',api_prefix:'/api/workspaces/original-workspace/',model_profile_id:'legacy',model_name:'服务管理',request_budget:50};
const second={...first,id:'second-workspace',repository_id:'second-repository',repository:'C:/synthetic/second',
 token:'second-capability',api_prefix:'/api/workspaces/second-workspace/',model_profile_id:'b'.repeat(32),model_name:'连接仓库'};
const profiles={revision:'fixed-profiles',default_profile_id:'legacy',max_profiles:32,profiles:[
 {id:'legacy',name:'服务管理',model:'raw-model-a',legacy:true,selectable:true},
 {id:second.model_profile_id,name:'连接仓库',model:'raw-model-b',legacy:false,selectable:true}]};
const initialState=record=>({repository:record.repository,active:null,model_profile_id:record.model_profile_id,
 provider:record.model_name,request_budget:50,requests_used:7,requests_remaining:41,requests_reserved_unknown:2,
 policy:{allowed_paths:['src/app.py'],readonly_paths:['tests/test_app.py']},runtime:{code_changed:false},jobs:[],runs:[],
 plans:[{id:record.id+'-plan',revision:'fixed-original-fingerprint',status:'awaiting_approval',plan:{issue:record.id+' 原审批任务',base_sha:'a'.repeat(40),edits:[],tests:[]}}]});
function nodes(node,predicate){return !node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(child=>nodes(child,predicate)):
 [...(predicate(node)?[node]:[]),...nodes(node.props?.children,predicate)];}
function text(node){return node==null||typeof node==='boolean'?'':typeof node!=='object'?String(node):Array.isArray(node)?node.map(text).join(''):text(node.props?.children);}
class FixtureElement{
 constructor(){this.children=[];this.dataset={};this.isConnected=true;}
 replaceChildren(...children){this.children=children;}
 append(...children){this.children.push(...children);}
 querySelectorAll(){return [];}
 focus(){}
}

function mount({language='zh-CN',multiple=false,holdAssets=false}={}){
 const globalKeys=['window','document','sessionStorage','localStorage','DOMParser','fetch','setInterval','clearInterval','setTimeout','clearTimeout'];
 const originals=new Map(globalKeys.map(key=>[key,globalThis[key]]));
 const records=clone(multiple?[first,second]:[first]),calls=[],savedViews=[],controllers=new Map(),assetWaiters=[];
 const states=new Map(records.map(record=>[record.id,initialState(record)]));
 const storage=new Map([['repocare.ui.v1',JSON.stringify({version:1,theme:'light',fontSize:15,page:'tasks',repository:first.repository_id,task:'plan:'+first.id+'-plan',panel:null})]]);
 const preferences=new Map([['repocare.preferences.v1',JSON.stringify({language,theme:'light',fontSize:15})]]);
 const fixture={records,calls,savedViews,controllers,states,onboarding:[],assets:[],actions:[],storage,preferences};environment=fixture;
 const sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};
 globalThis.sessionStorage=sessionStorage;
 globalThis.localStorage={getItem:key=>preferences.get(key)||null,setItem:(key,value)=>preferences.set(key,value)};
 globalThis.document={documentElement:{dataset:{}},activeElement:null,createElement:()=>new FixtureElement(),importNode:()=>new FixtureElement(),addEventListener(){},removeEventListener(){}};
 globalThis.DOMParser=class{parseFromString(){return {body:{children:[]}};}};
 // Poll timers are registered but not fired: a language change itself must not
 // produce another request or replay a historical operation.
 const timers=new Map();let timerId=0;
 globalThis.setInterval=globalThis.setTimeout=(fn,ms)=>{const id=++timerId;timers.set(id,{fn,ms});return id;};
 globalThis.clearInterval=globalThis.clearTimeout=id=>timers.delete(id);
 globalThis.window={RepoCareTabSession:{open:()=>session},sessionStorage,
  mountWorkbenchOnboarding(root,config){
   const entry={root,config,languages:[],disposals:0};fixture.onboarding.push(entry);
   entry.controller={setLanguage:value=>entry.languages.push(value),dispose:()=>{entry.disposals++;},
    inspectRepository:()=>{fixture.actions.push('inspectRepository');throw Error('language change inspected repository');},
    connectRepository:()=>{fixture.actions.push('connectRepository');throw Error('language change connected repository');},
    showPicker:()=>{fixture.actions.push('showPicker');}};
   return entry.controller;
  },
  mountMaintenanceUI(root,config){
   const record=records.find(value=>value.api_prefix===config.apiPrefix);assert.ok(record);
   assert.equal(controllers.has(record.id),false,'an existing controller must never be remounted');
   const entry={root,config,languages:[],selections:[],refreshes:0,disposals:0};
   entry.controller={setLanguage:value=>entry.languages.push(value),dispose:()=>{entry.disposals++;},
    select:selection=>entry.selections.push(clone(selection)),getState:()=>states.get(record.id),
    async refresh(){entry.refreshes++;config.onState(clone(states.get(record.id)));},
    async createTask(){fixture.actions.push('createTask');throw Error('language change generated a task');}};
   controllers.set(record.id,entry);config.onState(clone(states.get(record.id)));return entry.controller;
  }};
 globalThis.fetch=async(path,init={})=>{
  const call={path:String(path),method:init.method||'GET',body:init.body?JSON.parse(init.body):undefined,authorization:init.headers?.Authorization};calls.push(call);
  assert.equal(call.method,'GET','language-only test must never post or replay an action');
  if(path==='/onboarding.html'||path==='/maintenance.html'){
   fixture.assets.push(String(path));
   if(holdAssets)return new Promise(resolve=>assetWaiters.push(()=>resolve(response({}))));
   return response({});
  }
  if(path==='/api/model-profiles')return response(profiles);
  if(path==='/api/provider-config')return response({configured:true});
  if(path==='/api/delivery')return response({schema_version:1,read_only:true,items:[]});
  if(path==='/api/state')return response({workspaces:clone(records),model_profiles:clone(profiles),monitors:[],jobs:[],request_budget:50});
  throw Error('Unexpected request: '+call.method+' '+call.path);
 };
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
 let cursor=0,dirty=false,tree,effects=[],closed=false;
 const equal=(left,right)=>left&&right&&left.length===right.length&&left.every((value,index)=>Object.is(value,right[index]));
 const dispatcher={
  useState(initial){const index=cursor++;hooks[index]??={value:typeof initial==='function'?initial():initial};
   return [hooks[index].value,next=>{const value=typeof next==='function'?next(hooks[index].value):next;if(!Object.is(value,hooks[index].value)){hooks[index].value=value;dirty=true;}}];},
  useRef(value){const index=cursor++;return hooks[index]??={current:value};},
  useEffect(fn,deps){const index=cursor++;if(!hooks[index]||!equal(hooks[index].deps,deps)){
   const cleanup=hooks[index]?.cleanup;hooks[index]={deps};effects.push(()=>{cleanup?.();hooks[index].cleanup=fn();});}},
 };
 function render(){let turns=0;do{
  assert.ok(++turns<40,'App effects did not settle');dirty=false;cursor=0;effects=[];
  const previous=internal.H;internal.H=dispatcher;try{tree=App();}finally{internal.H=previous;}
  for(const element of nodes(tree,node=>node.props?.ref&&typeof node.props.ref==='object'))element.props.ref.current??=new FixtureElement();
  for(const effect of effects)effect();
 }while(dirty);return tree;}
 async function drain(){for(let turn=0;turn<40;turn++){await Promise.resolve();if(dirty)render();}}
 const button=label=>nodes(tree,node=>node.props?.['aria-label']===label||['button','AsyncButton'].includes(typeof node.type==='string'?node.type:node.type?.name)&&text(node)===label)[0];
 const component=name=>nodes(tree,node=>node.type?.name===name)[0];
 const composer=()=>nodes(tree,node=>node.type==='textarea'&&['新维护任务的问题描述','New maintenance task description'].includes(node.props['aria-label']))[0];
 function close(){if(closed)return;closed=true;for(const hook of hooks)hook?.cleanup?.();for(const [key,value]of originals){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}environment=null;setUiLanguage('zh-CN');}
 render();return {fixture,render,drain,close,tree:()=>tree,button,component,composer,
  async fillDraft(value){const field=composer();assert.ok(field);field.props.onChange({target:{value}});render();await drain();},
  async changeLanguage(value){let settings=component('SettingsWindow');if(!settings){const open=button('设置')||button('Settings');assert.ok(open);open.props.onClick();render();settings=component('SettingsWindow');}
   assert.ok(settings);settings.props.onPrefsChange({...settings.props.prefs,language:value});render();await drain();},
  async releaseAssets(){for(const resolve of assetWaiters.splice(0))resolve();await drain();},
 };
}

function stableSnapshot(ui){
 const report=ui.component('TaskReport');
 return {calls:clone(ui.fixture.calls),assets:clone(ui.fixture.assets),actions:clone(ui.fixture.actions),savedViews:clone(ui.fixture.savedViews),
  states:JSON.stringify([...ui.fixture.states]),model:{value:ui.component('ModelTaskSelector')?.props.value,currentId:ui.component('ModelTaskSelector')?.props.currentId},
  selectedWorkspace:report?.props.record.id,visibleState:JSON.stringify(report?.props.state),currentTask:JSON.stringify(report?.props.current),
  composer:ui.composer()?.props.value,controllers:[...ui.fixture.controllers].map(([id,entry])=>({id,selections:clone(entry.selections),refreshes:entry.refreshes,disposals:entry.disposals,
   token:entry.config.token,prefix:entry.config.apiPrefix})),onboardingDisposals:ui.fixture.onboarding.map(entry=>entry.disposals)};
}
function assertLiveLanguage(ui,value){
 assert.equal(getUiLanguage(),value);assert.equal(document.documentElement.lang,value);
 assert.equal(JSON.parse(ui.fixture.preferences.get('repocare.preferences.v1')).language,value);
 assert.equal(JSON.parse(ui.fixture.storage.get('repocare.ui.v1')).language,value);
 assert.equal(ui.fixture.onboarding.at(-1).languages.at(-1),value);
 for(const entry of ui.fixture.controllers.values())assert.equal(entry.languages.at(-1),value);
}

test('App switches existing adapters both ways without remount, requests, draft loss or approval/model selection changes',async()=>{
 const ui=mount();try{
  await ui.drain();assert.equal(ui.fixture.onboarding.length,1);assert.equal(ui.fixture.controllers.size,1);
  await ui.fillDraft('服务管理\n保留未提交草稿与原审批');
  ui.component('ModelTaskSelector').props.onChange(second.model_profile_id);ui.render();await ui.drain();
  ui.button('审阅方案与审批').props.onClick();ui.render();await ui.drain();
  const entry=ui.fixture.controllers.get(first.id),onboarding=ui.fixture.onboarding[0],main=nodes(ui.tree(),node=>node.type==='main')[0].props.ref.current;
  const baseline=stableSnapshot(ui),maintenanceRoot=entry.root,onboardingRoot=onboarding.root;
  assert.equal(baseline.model.currentId,'legacy');assert.equal(baseline.model.value,second.model_profile_id);
  assert.deepEqual(entry.selections.at(-1),{kind:'plan',id:first.id+'-plan'});
  for(const language of ['en','zh-CN']){
   const languageCalls=[entry.languages.length,onboarding.languages.length];
   await ui.changeLanguage(language);assertLiveLanguage(ui,language);
   assert.deepEqual(stableSnapshot(ui),baseline);
   assert.equal(entry.languages.length,languageCalls[0]+1);assert.equal(onboarding.languages.length,languageCalls[1]+1);
   assert.equal(ui.fixture.controllers.get(first.id),entry);assert.equal(entry.root,maintenanceRoot);assert.equal(onboarding.root,onboardingRoot);
   assert.equal(nodes(ui.tree(),node=>node.type==='main')[0].props.ref.current,main);
   assert.equal(entry.config.language,'zh-CN','existing adapter uses setLanguage rather than a new mount configuration');
   assert.equal(ui.component('TaskReport').props.state.plans[0].status,'awaiting_approval');
   assert.equal(ui.component('TaskReport').props.state.plans[0].revision,'fixed-original-fingerprint');
  }
 }finally{ui.close();}
 assert.equal(ui.fixture.onboarding[0].disposals,1);assert.equal(ui.fixture.controllers.get(first.id).disposals,1);
});

test('language changes update every already-mounted workspace adapter, including the inactive one',async()=>{
 const ui=mount({multiple:true,language:'en'});try{
  await ui.drain();assert.equal(ui.fixture.onboarding[0].config.language,'en');assert.equal(ui.fixture.controllers.get(first.id).config.language,'en');
  const other=nodes(ui.tree(),node=>node.type==='button'&&node.props.title===second.repository+' · '+second.model_name)[0];assert.ok(other);
  other.props.onClick();ui.render();await ui.drain();ui.button('Maintenance tasks').props.onClick();ui.render();
  const task=nodes(ui.tree(),node=>node.type==='button'&&node.props.className?.includes('task-row')&&text(node).includes(second.id+' 原审批任务'))[0];assert.ok(task);
  task.props.onClick();ui.render();await ui.drain();await ui.fillDraft('第二仓库原文草稿');
  assert.equal(ui.fixture.controllers.size,2);const baseline=stableSnapshot(ui),entries=[...ui.fixture.controllers.values()];
  for(const language of ['zh-CN','en']){
   const before=entries.map(entry=>entry.languages.length);await ui.changeLanguage(language);assertLiveLanguage(ui,language);
   assert.deepEqual(stableSnapshot(ui),baseline);
   for(const [index,entry]of entries.entries())assert.equal(entry.languages.length,before[index]+1);
  }
  assert.equal(ui.component('TaskReport').props.record.id,second.id);assert.equal(ui.component('TaskReport').props.current.selection.id,second.id+'-plan');
 }finally{ui.close();}
 assert.ok([...ui.fixture.controllers.values()].every(entry=>entry.disposals===1));
});

test('adapters that finish loading after a language change mount once using current preferences and keep the pending draft',async()=>{
 const ui=mount({holdAssets:true});try{
  await ui.drain();assert.equal(ui.fixture.onboarding.length,0);assert.equal(ui.fixture.controllers.size,0);
  await ui.fillDraft('控制器准备期间保留的草稿');const calls=clone(ui.fixture.calls),assets=clone(ui.fixture.assets);
  await ui.changeLanguage('en');assert.deepEqual(ui.fixture.calls,calls);assert.deepEqual(ui.fixture.assets,assets);
  await ui.releaseAssets();assert.equal(ui.fixture.onboarding.length,1);assert.equal(ui.fixture.controllers.size,1);
  assert.equal(ui.fixture.onboarding[0].config.language,'en');assert.equal(ui.fixture.controllers.get(first.id).config.language,'en');
  assert.equal(getUiLanguage(),'en');assert.equal(ui.composer().props.value,'控制器准备期间保留的草稿');
  assert.deepEqual(ui.fixture.calls,calls);assert.deepEqual(ui.fixture.assets,assets);assert.deepEqual(ui.fixture.actions,[]);
  assert.equal(ui.fixture.controllers.get(first.id).disposals,0);assert.equal(ui.fixture.onboarding[0].disposals,0);
  assert.equal(ui.component('TaskReport').props.state.plans[0].status,'awaiting_approval');
 }finally{ui.close();}
});
