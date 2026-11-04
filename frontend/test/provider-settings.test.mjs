import {test} from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {build} from 'vite';
const out=path.resolve('.demo/provider-settings-'+process.pid);
await build({configFile:false,logLevel:'error',build:{ssr:'src/ProviderSettings.tsx',outDir:out,emptyOutDir:false,
 rolldownOptions:{external:['react','react-dom','react/jsx-runtime'],output:{entryFileNames:'settings.mjs'}}}});
const {ProviderSettings}=await import(pathToFileURL(path.join(out,'settings.mjs')));
function render(initial){let calls=0;const html=renderToStaticMarkup(React.createElement(ProviderSettings,{initial,request:()=>{calls++;}}));assert.equal(calls,0);return html;}
test('first-run settings use a password input with no autofill and never send during render',()=>{
 const html=render({editable:true,configured:false,key_set:false,base_url:'',model:''});
 assert.match(html,/type="password"/);assert.match(html,/autoComplete="new-password"/);
 assert.match(html,/模型 Base URL/);assert.match(html,/模型名称/);assert.match(html,/保存模型配置/);
 assert.doesNotMatch(html,/<fieldset disabled/);
});
test('saved credential never becomes an input value, even if a malformed status includes api_key',()=>{
 const html=render({editable:true,configured:true,key_set:true,base_url:'https://example.invalid/v1',model:'fixture',api_key:'synthetic-secret-never-show'});
 assert.match(html,/已保存；留空保留/);assert.doesNotMatch(html,/synthetic-secret-never-show/);
});
test('bound and external settings disable edits and explain the preserved configuration',()=>{
 const html=render({editable:false,configured:true,key_set:true,reason:'已有维护记录绑定此模型配置',base_url:'https://example.invalid/v1',model:'fixed'});
 assert.match(html,/<fieldset disabled=""/);assert.match(html,/已有维护记录绑定此模型配置/);
 assert.match(html,/刷新模型设置/);
});

function nodes(n,check){return !n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(v=>nodes(v,check)):
 [...(check(n)?[n]:[]),...nodes(n.props?.children,check)];}
function content(n){return n==null||typeof n==='boolean'?'':typeof n!=='object'?String(n):Array.isArray(n)?n.map(content).join(''):content(n.props?.children);}
function component(props){
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
 let cursor=0,dirty=false,tree,effects=[];
 const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
 const dispatch={useState(initial){const i=cursor++;hooks[i]??={value:typeof initial==='function'?initial():initial};
  return [hooks[i].value,next=>{const value=typeof next==='function'?next(hooks[i].value):next;if(!Object.is(value,hooks[i].value)){hooks[i].value=value;dirty=true;}}];},
  useRef(value){const i=cursor++;return hooks[i]??={current:value};},
  useEffect(fn,deps){const i=cursor++;if(!hooks[i]||!same(hooks[i].deps,deps)){const cleanup=hooks[i]?.cleanup;hooks[i]={deps};effects.push(()=>{cleanup?.();hooks[i].cleanup=fn();});}}};
 function refresh(){let rounds=0;do{assert.ok(++rounds<25);dirty=false;cursor=0;effects=[];const old=internal.H;internal.H=dispatch;
  try{tree=ProviderSettings(props);}finally{internal.H=old;}for(const fn of effects)fn();}while(dirty);return tree;}
 refresh();return {refresh,tree:()=>tree,text:()=>content(tree),
  input(label){return nodes(tree,n=>n.type==='input'&&n.props['aria-label']===label)[0];},
  fill(label,value){this.input(label).props.onChange({target:{value}});refresh();},
  button(label){return nodes(tree,n=>n.type==='button'&&(content(n)===label||n.props['aria-label']===label))[0];},
  form(index=1){return nodes(tree,n=>n.type==='form')[index];},
  unmount(){for(const hook of hooks)hook?.cleanup?.();}};
}
const original={editable:false,configured:true,key_set:true,reason:'已有维护记录绑定此模型配置',base_url:'https://legacy.invalid/v1',model:'original-model',revision:'legacy-revision'};
const legacy={id:'legacy',name:'原配置',base_url:original.base_url,model:original.model,key_set:true,legacy:true,selectable:true};
const added={id:'a'.repeat(32),name:'开发模型',base_url:'https://new.invalid/v1',model:'new-model',key_set:true,legacy:false,selectable:true};
function profiles(extra=[],patch={}){return {revision:'r1',default_profile_id:'legacy',profiles:[legacy,...extra],max_profiles:32,...patch};}
function props(patch={}){return {initial:original,initialProfiles:profiles(),request:async()=>{throw Error('unexpected request');},...patch};}
function fillModel(ui){ui.fill('新模型配置名称',' 开发模型 ');ui.fill('新模型 Base URL','https://new.invalid/v1');ui.fill('新模型 ID','new-model');ui.fill('新模型 API Key','synthetic-create-secret');}
const event={preventDefault(){}};

test('bound legacy keeps its disabled form while multiple profiles can be added; no credential is rendered',()=>{
 let calls=0;const html=renderToStaticMarkup(React.createElement(ProviderSettings,props({initialProfiles:profiles([added],{api_key:'top-secret',profiles:[legacy,{...added,api_key:'nested-secret'}]}),request:()=>{calls++;}})));
 assert.equal(calls,0);assert.match(html,/<fieldset disabled=""/);assert.match(html,/<fieldset><legend>添加模型/);
 assert.match(html,/开发模型/);assert.match(html,/新任务默认/);assert.match(html,/旧任务、审批、恢复和原监控继续使用原配置/);
 assert.doesNotMatch(html,/top-secret|nested-secret/);
});

test('create sends one explicit model profile, clears credential input and payload, and retains legacy configuration',async()=>{
 const calls=[],changes=[];let resolve;
 const ui=component(props({onProfilesChange:value=>changes.push(value),request:async(path,token,body)=>{
  calls.push({path,token,body,snapshot:structuredClone(body)});return await new Promise(done=>{resolve=done;});}}));
 fillModel(ui);const submit=ui.form().props.onSubmit,pending=submit(event);ui.refresh();
 assert.equal(ui.input('新模型 API Key').props.value,'');assert.equal(nodes(ui.tree(),n=>n.type==='fieldset')[1].props.disabled,true);
 await submit(event);assert.equal(calls.length,1);
 assert.deepEqual(calls[0].snapshot,{revision:'r1',action:'create',name:'开发模型',base_url:'https://new.invalid/v1',model:'new-model',api_key:'synthetic-create-secret'});
 assert.equal(calls[0].path,'/api/model-profiles');assert.equal(calls[0].token,undefined);
 resolve(profiles([added],{revision:'r2',created_profile_id:added.id,api_key:'never-echo',profiles:[legacy,{...added,api_key:'never-echo'}]}));await pending;ui.refresh();
 assert.equal(calls[0].body.api_key,'');assert.equal(ui.input('模型 Base URL').props.value,original.base_url);
 assert.equal(ui.input('模型名称').props.value,original.model);assert.equal(ui.input('新模型配置名称').props.value,'');
 assert.equal(changes.length,1);assert.equal(changes[0].profiles[1].api_key,undefined);assert.equal(changes[0].api_key,undefined);
 assert.match(ui.text(),/模型已添加到本机，未调用模型/);assert.doesNotMatch(ui.text(),/synthetic-create-secret|never-echo/);
});

test('set-default uses revision and profile id only and does not rewrite old provider settings',async()=>{
 const calls=[],ui=component(props({initialProfiles:profiles([added]),request:async(path,token,body)=>{calls.push({path,token,body});return profiles([added],{revision:'r2',default_profile_id:added.id});}}));
 await ui.button('将 开发模型 设为新任务默认模型').props.onClick();ui.refresh();
 assert.deepEqual(calls,[{path:'/api/model-profiles',token:undefined,body:{revision:'r1',action:'set_default',profile_id:added.id}}]);
 assert.match(ui.text(),/旧任务与原监控继续使用原配置/);assert.equal(ui.button('将 开发模型 设为新任务默认模型').props.disabled,true);
 assert.equal(ui.input('模型名称').props.value,'original-model');
});

test('uncertain create blocks stale handlers and all writes until explicit successful GET reconciliation',async()=>{
 const calls=[];let readsFail=true;
 const ui=component(props({initialProfiles:profiles([added]),request:async(path,token,body)=>{calls.push({path,body});
  if(body){const e=Error('Network request failed');e.uncertain=true;throw e;}
  if(readsFail)throw Error('offline');return profiles([added],{revision:'r3'});}}));
 fillModel(ui);const submit=ui.form().props.onSubmit,makeDefault=ui.button('将 开发模型 设为新任务默认模型').props.onClick;
 await submit(event);ui.refresh();assert.equal(calls[0].body.api_key,'');assert.match(ui.text(),/保存结果未知/);
 await submit(event);await makeDefault();ui.refresh();assert.equal(calls.length,1);
 assert.equal(ui.button('将 开发模型 设为新任务默认模型').props.disabled,true);
 await ui.button('重新读取模型列表，核对保存结果').props.onClick();ui.refresh();
 await makeDefault();assert.equal(calls.length,2);assert.match(ui.text(),/无法读取模型列表/);
 readsFail=false;await ui.button('重新读取模型列表，核对保存结果').props.onClick();ui.refresh();assert.equal(calls.length,3);
 await ui.button('将 开发模型 设为新任务默认模型').props.onClick();ui.refresh();
 assert.equal(calls.length,4);assert.equal(calls[3].body.revision,'r3');
});

test('known rejection uses Chinese feedback and clears credential without automatically resending',async()=>{
 const calls=[],ui=component(props({request:async(path,token,body)=>{calls.push(body);throw Error('Rejected synthetic-create-secret');}}));
 fillModel(ui);await ui.form().props.onSubmit(event);ui.refresh();
 assert.equal(calls.length,1);assert.equal(calls[0].api_key,'');assert.equal(ui.input('新模型 API Key').props.value,'');
 assert.match(ui.text(),/模型设置未保存/);assert.doesNotMatch(ui.text(),/synthetic-create-secret/);
 assert.equal(ui.button('刷新模型列表').props.disabled,false);
});

test('GET loads model management after old project bindings and never sends a mutation',async()=>{
 const calls=[],ui=component({initial:original,request:async(path,token,body)=>{calls.push({path,token,body});return profiles([added]);}});
 await Promise.resolve();await Promise.resolve();ui.refresh();
 assert.deepEqual(calls,[{path:'/api/model-profiles',token:undefined,body:undefined}]);
 assert.ok(ui.input('新模型 API Key'));assert.equal(nodes(ui.tree(),n=>n.type==='fieldset')[0].props.disabled,true);
});

test('profile count limit and unavailable profiles disable writes',async()=>{
 let calls=0;const ui=component(props({initialProfiles:profiles([{...added,selectable:false}],{max_profiles:1}),request:async()=>{calls++;}}));
 fillModel(ui);await ui.form().props.onSubmit(event);await ui.button('将 开发模型 设为新任务默认模型').props.onClick();
 assert.equal(calls,0);assert.match(ui.text(),/已达到模型配置数量上限/);
 assert.equal(nodes(ui.tree(),n=>n.type==='fieldset')[1].props.disabled,true);
});

test('legacy entry does not consume an additional profile slot',()=>{
 const ui=component(props({initialProfiles:profiles([],{max_profiles:1})}));
 assert.equal(nodes(ui.tree(),n=>n.type==='fieldset')[1].props.disabled,false);
 assert.doesNotMatch(ui.text(),/已达到模型配置数量上限/);
});

test('legacy unknown save blocks stale handlers and clears its key until explicit readback',async()=>{
 const calls=[];const ui=component(props({initial:{...original,editable:true},request:async(path,token,body)=>{
  calls.push({path,body});if(body){const error=Error('synthetic interrupted');error.uncertain=true;throw error;}return {...original,editable:true};}}));
 ui.fill('模型 API Key','synthetic-legacy-secret');const save=ui.form(0).props.onSubmit;
 await save(event);ui.refresh();assert.equal(ui.input('模型 API Key').props.value,'');assert.equal(calls[0].body.api_key,'');
 await save(event);assert.equal(calls.length,1);assert.ok(ui.button('重新读取设置，核对保存结果'));
 await ui.button('重新读取设置，核对保存结果').props.onClick();ui.refresh();
 assert.equal(calls.length,2);assert.equal(calls[1].body,undefined);assert.equal(calls[1].path,'/api/provider-config');
 assert.equal(nodes(ui.tree(),n=>n.type==='fieldset')[0].props.disabled,false);
});
