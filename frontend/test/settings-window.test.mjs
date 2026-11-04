import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import './tsx-register.mjs';
import {normalizePreferences,PREFERENCES_KEY,t} from '../src/preferences.mjs';
import {captureForm,applyProposal} from '../src/form-assist.mjs';
const {SettingsWindow}=await import('../src/SettingsWindow.tsx');
const {ModelProfilesSettings}=await import('../src/ProviderSettings.tsx');
const {ModelTaskSelector}=await import('../src/ModelTaskSelector.tsx');
function nodes(n,check){return !n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(v=>nodes(v,check)):[...(check(n)?[n]:[]),...nodes(n.props?.children,check)];}
function content(n){return n==null||typeof n==='boolean'?'':typeof n!=='object'?String(n):Array.isArray(n)?n.map(content).join(''):content(n.props?.children);}
function component(Component,initialProps){
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;let cursor=0,dirty=false,tree,effects=[],props=initialProps;
 const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
 const dispatcher={useState(initial){const i=cursor++;hooks[i]??={value:typeof initial==='function'?initial():initial};return [hooks[i].value,next=>{const value=typeof next==='function'?next(hooks[i].value):next;if(!Object.is(value,hooks[i].value)){hooks[i].value=value;dirty=true;}}];},
  useRef(value){const i=cursor++;return hooks[i]??={current:value};},useEffect(fn,deps){const i=cursor++;if(!hooks[i]||!same(hooks[i].deps,deps)){const cleanup=hooks[i]?.cleanup;hooks[i]={deps};effects.push(()=>{cleanup?.();hooks[i].cleanup=fn();});}}};
 function refresh(nextProps){if(nextProps)props=nextProps;let rounds=0;do{assert.ok(++rounds<25);dirty=false;cursor=0;effects=[];const old=internal.H;internal.H=dispatcher;
  try{tree=Component(props);}finally{internal.H=old;}for(const fn of effects)fn();}while(dirty);return tree;}
 refresh();return {refresh,tree:()=>tree,text:()=>content(tree),find:check=>nodes(tree,check),
  input(label){return nodes(tree,n=>['input','select'].includes(n.type)&&n.props['aria-label']===label)[0];},fill(label,value){this.input(label).props.onChange({target:{value}});refresh();},
  button(label){return nodes(tree,n=>n.type==='button'&&(content(n)===label||n.props['aria-label']===label))[0];},form(){return nodes(tree,n=>n.type==='form')[0];},
  unmount(){for(const hook of hooks)hook?.cleanup?.();}};
}
const legacy={id:'legacy',name:'原配置',base_url:'https://legacy.invalid/v1',model:'old-model',key_set:true,legacy:true,selectable:true,context_window_tokens:32768,max_output_tokens:128000,limits_explicit:false};
const added={id:'a'.repeat(32),name:'开发模型',base_url:'https://new.invalid/v1',model:'new-model',key_set:true,legacy:false,selectable:true,context_window_tokens:200000,max_output_tokens:16000,limits_explicit:true};
const profiles=(patch={})=>({revision:'r1',default_profile_id:'legacy',max_profiles:32,profiles:[legacy,added],...patch});
const props=(patch={})=>({initialProfiles:profiles(),request:async()=>{throw Error('unexpected request');},...patch});
const event={preventDefault(){}};
function fill(ui){ui.fill('配置名称','开发模型 2');ui.fill('Base URL','https://new.invalid/v1');ui.fill('模型 ID','second-model');ui.fill('API Key','synthetic-secret');}

test('models open as a saved list and reveal credential fields only after explicit add',()=>{
 let calls=0;const ui=component(ModelProfilesSettings,props({request:async()=>{calls++;}}));
 assert.equal(ui.find(n=>n.type==='form').length,0);assert.equal(ui.input('API Key'),undefined);
 const list=ui.find(n=>n.type==='ul'&&n.props['aria-label']==='已保存的模型')[0];assert.equal(nodes(list,n=>n.type==='li').length,2);assert.equal(nodes(list,n=>n.type==='ul').length,1);assert.match(ui.text(),/legacy.invalid/);assert.match(ui.text(),/new.invalid/);assert.doesNotMatch(ui.text(),/old-model|new-model|继承旧配置；供应商上限未验证/);
 ui.button('＋ 添加模型提供商').props.onClick();ui.refresh();assert.ok(ui.input('API Key'));assert.equal(calls,0);
 ui.fill('API Key','synthetic-secret');ui.button('← 返回模型列表').props.onClick();ui.refresh();ui.button('＋ 添加模型提供商').props.onClick();ui.refresh();assert.equal(ui.input('API Key').props.value,'');
});
test('editing creates a new immutable profile with declared limits, clears key and preserves the original',async()=>{
 const calls=[],changes=[];let resolve;const ui=component(ModelProfilesSettings,props({onProfilesChange:value=>changes.push(value),request:async(path,token,body)=>{calls.push({path,token,body,snapshot:structuredClone(body)});return new Promise(done=>{resolve=done;});}}));
 ui.button('编辑并另存新版本: '+added.base_url).props.onClick();ui.refresh();assert.equal(ui.input('API Key').props.value,'');assert.equal(ui.input('上下文长度').props.value,'200000');
 assert.match(ui.text(),/仅新增或修改的模型另存版本/);assert.match(ui.text(),/留空密钥复用各模型自己的已保存密钥/);ui.fill('模型 ID','updated-model');ui.fill('API Key','synthetic-secret');
 const submit=ui.form().props.onSubmit,pending=submit(event);ui.refresh();await submit(event);assert.equal(calls.length,1);assert.equal(ui.input('API Key').props.value,'');
 assert.deepEqual(calls[0].snapshot,{revision:'r1',action:'create',name:'开发模型',base_url:added.base_url,model:'updated-model',api_key:'synthetic-secret',source_profile_id:added.id,context_window_tokens:200000,max_output_tokens:16000});
 const next={...added,id:'b'.repeat(32),name:'开发模型 · 新版本',model:'updated-model',api_key:'never-render'};resolve(profiles({revision:'r2',profiles:[legacy,added,next],api_key:'never-render'}));await pending;ui.refresh();
 assert.equal(calls[0].body.api_key,'');assert.equal(calls[0].path,'/api/model-profiles');assert.equal(ui.find(n=>n.type==='form').length,0);assert.match(ui.text(),/开发模型/);assert.match(ui.text(),/2 个模型/);assert.doesNotMatch(content(ui.find(n=>n.type==='ul'&&n.props['aria-label']==='已保存的模型')[0]),/updated-model|new-model/);assert.match(ui.text(),/本次已保存的模型/);assert.equal(changes[0].default_profile_id,'legacy');assert.equal(changes[0].profiles.find(row=>row.id===added.id).model,'new-model');assert.equal(changes[0].profiles.find(row=>row.id===legacy.id).model,'old-model');assert.equal(changes[0].profiles[2].model,'updated-model');assert.equal(changes[0].profiles[2].api_key,undefined);assert.doesNotMatch(ui.text(),/synthetic-secret|never-render/);
});
test('legacy edit presents valid proposed limits explicitly and never reads an old key',()=>{
 const ui=component(ModelProfilesSettings,props());ui.button('编辑并另存新版本: '+legacy.base_url).props.onClick();ui.refresh();
 assert.equal(ui.input('上下文长度').props.value,'132096');assert.equal(ui.input('最大输出长度').props.value,'128000');assert.equal(ui.input('API Key').props.value,'');
 assert.match(ui.text(),/供你确认的建议值/);assert.match(ui.text(),/旧配置保持原样/);
});
test('invalid lengths cannot dispatch a mutation and valid optional lengths pass through',async()=>{
 const calls=[],ui=component(ModelProfilesSettings,props({request:async(path,token,body)=>{calls.push(structuredClone(body));return profiles({revision:'r2'});}}));
 ui.button('＋ 添加模型提供商').props.onClick();ui.refresh();fill(ui);ui.fill('上下文长度','4096');ui.fill('最大输出长度','4096');await ui.form().props.onSubmit(event);ui.refresh();assert.equal(calls.length,0);assert.match(ui.text(),/小于上下文长度/);
 ui.fill('上下文长度','200000');ui.fill('最大输出长度','16000');await ui.form().props.onSubmit(event);ui.refresh();assert.equal(calls[0].context_window_tokens,200000);assert.equal(calls[0].max_output_tokens,16000);
});
test('ambiguous saves stop every stale mutation until a successful explicit read',async()=>{
 const calls=[];let failRead=true;const ui=component(ModelProfilesSettings,props({request:async(path,token,body)=>{calls.push({path,body});if(body){const error=Error('synthetic-secret');error.uncertain=true;throw error;}if(failRead)throw Error('offline');return profiles({revision:'r3'});}}));
 ui.button('编辑并另存新版本: '+added.base_url).props.onClick();ui.refresh();const makeDefault=ui.button('设为新会话默认').props.onClick;ui.button('← 返回模型列表').props.onClick();ui.refresh();ui.button('＋ 添加模型提供商').props.onClick();ui.refresh();fill(ui);const submit=ui.form().props.onSubmit;
 await submit(event);ui.refresh();assert.match(ui.text(),/保存结果未知/);assert.equal(calls[0].body.api_key,'');await submit(event);await makeDefault();assert.equal(calls.length,1);
 await ui.button('重新读取模型列表，核对保存结果').props.onClick();ui.refresh();await makeDefault();assert.equal(calls.length,2);
 failRead=false;await ui.button('重新读取模型列表，核对保存结果').props.onClick();ui.refresh();await ui.button('← 返回模型列表').props.onClick();ui.refresh();ui.button('编辑并另存新版本: '+added.base_url).props.onClick();ui.refresh();await ui.button('设为新会话默认').props.onClick();
 assert.equal(calls.length,4);assert.deepEqual(calls[3].body,{revision:'r3',action:'set_default',profile_id:added.id});
});
test('the model editor handles Escape as one-level back and removes the key field',()=>{
 const ui=component(ModelProfilesSettings,props());ui.button('＋ 添加模型提供商').props.onClick();ui.refresh();let stopped=0,prevented=0;
 ui.tree().props.onKeyDown({key:'Escape',preventDefault(){prevented++;},stopPropagation(){stopped++;}});ui.refresh();assert.equal(stopped,1);assert.equal(prevented,1);assert.equal(ui.input('API Key'),undefined);
});
test('English settings render translated controls without permission requests or model mutations',()=>{
 let requests=0,permissions=0;const previous=globalThis.Notification;globalThis.Notification={permission:'default',requestPermission:()=>{permissions++;}};
 try{const html=renderToStaticMarkup(React.createElement(SettingsWindow,{prefs:{language:'en'},onPrefsChange(){},request:()=>{requests++;},onClose(){}}));
  assert.match(html,/General/);assert.match(html,/Models/);assert.match(html,/Notifications/);assert.match(html,/Font size/);assert.match(html,/Close settings/);assert.doesNotMatch(html,/API Key/);assert.equal(requests,0);assert.equal(permissions,0);
 }finally{globalThis.Notification=previous;}
});
test('notification permission needs its explicit button; desktop changes remain drafts until Save settings',async()=>{
 const previous={document:globalThis.document,window:globalThis.window,Notification:globalThis.Notification,localStorage:globalThis.localStorage};let permissions=0;
 const values=new Map(),changes=[];globalThis.document={activeElement:null,documentElement:{dataset:{}}};globalThis.window={addEventListener(){},removeEventListener(){}};
 globalThis.localStorage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)};globalThis.Notification={permission:'default',requestPermission:async()=>{permissions++;return 'granted';}};
 try{const ui=component(SettingsWindow,{prefs:normalizePreferences(null),onPrefsChange:value=>changes.push(value),request:async()=>({}),onClose(){}});
  ui.button('通知').props.onClick();ui.refresh();assert.equal(permissions,0);const desktop=ui.find(n=>n.type==='label'&&content(n)==='桌面通知')[0];
  nodes(desktop,n=>n.type==='input')[0].props.onInput({currentTarget:{checked:true}});ui.refresh();assert.equal(permissions,0);assert.equal(changes.length,0);assert.equal(values.has(PREFERENCES_KEY),false);ui.button('保存设置').props.onClick();ui.refresh();assert.equal(changes[0].notifications.desktop,true);assert.equal(JSON.parse(values.get(PREFERENCES_KEY)).notifications.desktop,true);
  await ui.button('允许桌面通知').props.onClick();ui.refresh();assert.equal(permissions,1);assert.match(ui.text(),/已允许/);ui.unmount();
 }finally{Object.assign(globalThis,previous);}
});
test('language and font edits stay local drafts until the user explicitly saves settings',()=>{
 const previous={document:globalThis.document,window:globalThis.window,localStorage:globalThis.localStorage},values=new Map(),changes=[];
 globalThis.document={activeElement:null,documentElement:{dataset:{},lang:'zh-CN'}};globalThis.window={addEventListener(){},removeEventListener(){}};globalThis.localStorage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)};
 const ui=component(SettingsWindow,{prefs:normalizePreferences(null),onPrefsChange:value=>changes.push(value),request:async()=>({}),onClose(){}});
 try{ui.fill('语言','en');ui.fill('界面字号','17');assert.equal(changes.length,0);assert.equal(values.size,0);assert.equal(globalThis.document.documentElement.lang,'zh-CN');assert.match(ui.text(),/更改仍是草稿/);
  ui.button('保存设置').props.onClick();ui.refresh();assert.equal(changes.length,1);assert.equal(changes[0].language,'en');assert.equal(changes[0].fontSize,17);assert.equal(globalThis.document.documentElement.lang,'en');assert.equal(JSON.parse(values.get(PREFERENCES_KEY)).language,'en');
 }finally{ui.unmount();Object.assign(globalThis,previous);}
});

// Bind actual ModelProfilesSettings-rendered input props and actual local handlers
// to a small DOM adapter. This checks labels, opt-in and K/M text draft behavior;
// it is a component+DOM-adapter check, not a claim of native-browser acceptance.
for(const language of ['zh-CN','en'])test(`registered model fields accept K/M drafts without secret capture or POST (${language})`,async()=>{
 let requests=0,submissions=0;const ui=component(ModelProfilesSettings,props({language,request:async()=>{requests++;throw Error('no form business request allowed');}}));
 ui.button('＋ '+t(language,'models.add')).props.onClick();ui.refresh();ui.fill(t(language,'models.apiKey'),'synthetic-key-kept-out-of-snapshot');
 const controls=new Map();let dom=[];
 class ModelInput{
  constructor(){this._value='';this.events=[];this.isConnected=true;this.tagName='INPUT';this.ownerDocument={defaultView:{Event,getComputedStyle:()=>({display:'block',visibility:'visible'})}};}
  get value(){return this._value;}set value(value){this._value=String(value);}
  getAttribute(name){if(name==='autocomplete')return this.props.autoComplete||null;return this.props[name]??null;}
  getClientRects(){return [{}];}closest(){return null;}matches(){return false;}
  click(){throw Error('helper must not click');}
  dispatchEvent(event){this.events.push(event.type);if(event.type==='input'){(this.props.onInput||this.props.onChange)?.({target:this,currentTarget:this});ui.refresh();sync();}if(event.type==='submit')submissions++;return true;}
 }
 function sync(){dom=ui.find(node=>node.type==='input').map(node=>{const label=node.props['aria-label'];let element=controls.get(label);if(!element){element=new ModelInput();controls.set(label,element);}element.props=node.props;element.type=node.props.type||'text';element.labels=[{textContent:label}];element.min=node.props.min==null?'':String(node.props.min);element.max=node.props.max==null?'':String(node.props.max);element._value=String(node.props.value??'');return element;});}
 sync();const root={isConnected:true,getClientRects:()=>[{}],closest:()=>null,querySelectorAll:()=>dom,contains:node=>dom.includes(node),submit(){submissions++;throw Error('helper must not submit');}};
 const snapshot=await captureForm(root);for(const key of ['models.name','models.baseUrl','models.modelId','models.context','models.output'])assert.ok(snapshot.fields.some(field=>field.label===t(language,key)));assert.doesNotMatch(JSON.stringify(snapshot),/synthetic-key|API Key/);
 const context=snapshot.fields.find(field=>field.label===t(language,'models.context')),output=snapshot.fields.find(field=>field.label===t(language,'models.output'));assert.ok(context);assert.ok(output);assert.equal(context.type,'text');assert.equal(output.type,'text');assert.equal(context.max,undefined);assert.equal(output.max,undefined);
 assert.match(ui.text(),/tokens/);assert.doesNotMatch(context.label,/token/i);assert.doesNotMatch(output.label,/token/i);
 const result=await applyProposal(root,snapshot,{form_id:snapshot.form_id,revision:snapshot.revision,draft_only:true,changes:[{field_id:context.id,value:'2M'},{field_id:output.id,value:'256K'}]});
 assert.equal(result.applied,true);assert.equal(ui.input(t(language,'models.context')).props.value,'2M');assert.equal(ui.input(t(language,'models.output')).props.value,'256K');assert.equal(ui.input(t(language,'models.apiKey')).props.value,'synthetic-key-kept-out-of-snapshot');assert.equal(requests,0);assert.equal(submissions,0);
 ui.unmount();
});

// Bind only rendered, visible input/select props and their actual draft handlers.
// The adapter never clicks controls or submits; native-browser evidence is separate.
function settingsDraftDom(ui,{nativeTimeInput=false}={}){
 const controls=new Map();let dom=[],actions=0;
 const caption=node=>node==null||typeof node==='boolean'?'':typeof node!=='object'?String(node):Array.isArray(node)?node.map(caption).join(''):['input','select','textarea','button'].includes(node.type)?'':caption(node.props?.children);
 class Control{
  constructor(){this._value='';this._checked=false;this.events=[];this.isConnected=true;this.ownerDocument={defaultView:{Event,getComputedStyle:()=>({display:'block',visibility:'visible'})}};}
  get value(){return this._value;}set value(value){this._value=String(value);}
  get checked(){return this._checked;}set checked(value){this._checked=Boolean(value);}
  getAttribute(name){return this.props[name]??null;}getClientRects(){return [{}];}closest(){return null;}matches(){return this.disabled;}
  click(){actions++;throw Error('draft helper must not click');}
  dispatchEvent(event){this.events.push(event.type);if(event.type==='submit')actions++;if(event.type==='input'){(nativeTimeInput&&this.type==='time'?this.props.onInput:this.props.onInput||this.props.onChange)?.({target:this,currentTarget:this});ui.refresh();sync();}return true;}
 }
 function sync(){dom=ui.find(node=>['input','select'].includes(node.type)).map(node=>{
  const parent=ui.find(label=>label.type==='label'&&nodes(label,item=>item===node).length>0)[0],text=caption(parent),key=node.props['aria-label']||text;let element=controls.get(key);if(!element){element=new Control();controls.set(key,element);}
  element.props=node.props;element.tagName=node.type.toUpperCase();element.type=node.props.type||'text';element.labels=[{textContent:text}];element.disabled=Boolean(node.props.disabled);element.readOnly=Boolean(node.props.readOnly);element.min=node.props.min==null?'':String(node.props.min);element.max=node.props.max==null?'':String(node.props.max);element._value=String(node.props.value??'');element._checked=Boolean(node.props.checked);element.options=nodes(node,item=>item.type==='option').map(option=>({value:String(option.props.value??content(option)),label:content(option),disabled:option.props.disabled}));return element;
 });}
 sync();return {root:{isConnected:true,getClientRects:()=>[{}],closest:()=>null,querySelectorAll:()=>dom,contains:node=>dom.includes(node),submit(){actions++;throw Error('draft helper must not submit');}},controls,actions:()=>actions};
}
for(const language of ['zh-CN','en'])test(`visible theme and quiet-hour fields apply only to settings drafts (${language})`,async()=>{
 const previous={document:globalThis.document,window:globalThis.window,localStorage:globalThis.localStorage,Notification:globalThis.Notification},storage=new Map(),changes=[];let requests=0,permissions=0;
 globalThis.document={activeElement:null,documentElement:{dataset:{theme:'system'},lang:language}};globalThis.window={addEventListener(){},removeEventListener(){}};globalThis.localStorage={getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)};globalThis.Notification={permission:'default',requestPermission:async()=>{permissions++;return 'granted';}};
 const prefs=normalizePreferences({language,notifications:{quietHours:{enabled:true}}}),ui=component(SettingsWindow,{prefs,onPrefsChange:value=>changes.push(value),request:async()=>{requests++;return {};},onClose(){}});
 const propose=(snapshot,values)=>({form_id:snapshot.form_id,revision:snapshot.revision,draft_only:true,changes:Object.entries(values).map(([label,value])=>({field_id:snapshot.fields.find(field=>field.label===label)?.id,value}))});
 try{
  let dom=settingsDraftDom(ui),snapshot=await captureForm(dom.root);assert.ok(ui.input(t(language,'settings.theme')));assert.deepEqual(snapshot.fields.map(field=>field.label),['settings.theme','settings.fontSize','settings.language'].map(key=>t(language,key)));assert.deepEqual(snapshot.fields[0].options,['light','dark','system']);
  assert.deepEqual(await applyProposal(dom.root,snapshot,propose(snapshot,{[t(language,'settings.theme')]:'dark'})),{applied:true,reason:'applied'});assert.equal(ui.input(t(language,'settings.theme')).props.value,'dark');assert.equal(globalThis.document.documentElement.dataset.theme,'system');assert.equal(changes.length,0);assert.equal(storage.size,0);
  ui.button(t(language,'settings.notifications')).props.onClick();ui.refresh();dom=settingsDraftDom(ui);snapshot=await captureForm(dom.root);assert.equal(snapshot.fields.length,10);assert.equal(snapshot.fields.find(field=>field.label===t(language,'notifications.start')).type,'text');
  assert.deepEqual(await applyProposal(dom.root,snapshot,propose(snapshot,{[t(language,'notifications.inApp')]:false,[t(language,'notifications.start')]:'25:00'})),{applied:false,reason:'invalid'});assert.equal(dom.controls.get(t(language,'notifications.inApp')).checked,true);assert.equal(dom.controls.get(t(language,'notifications.start')).value,'22:00');assert.ok([...dom.controls.values()].every(control=>control.events.length===0));
  assert.deepEqual(await applyProposal(dom.root,snapshot,propose(snapshot,{[t(language,'notifications.start')]:'23:15',[t(language,'notifications.end')]:'07:30'})),{applied:true,reason:'applied'});assert.equal(ui.input(t(language,'notifications.start')).props.value,'23:15');assert.equal(ui.input(t(language,'notifications.end')).props.value,'07:30');assert.equal(changes.length,0);assert.equal(storage.size,0);assert.equal(requests,0);assert.equal(permissions,0);assert.equal(dom.actions(),0);
  ui.button(language==='en'?'Save settings':'保存设置').props.onClick();ui.refresh();assert.equal(changes.length,1);assert.equal(changes[0].theme,'dark');assert.equal(changes[0].notifications.quietHours.start,'23:15');assert.equal(JSON.parse(storage.get(PREFERENCES_KEY)).notifications.quietHours.end,'07:30');assert.equal(globalThis.document.documentElement.dataset.theme,'dark');assert.equal(requests,0);assert.equal(permissions,0);
 }finally{ui.unmount();Object.assign(globalThis,previous);}
});

test('compact model selector keeps ids internal and staging never applies or refreshes',async()=>{
 let posts=0,reads=0;const a='a'.repeat(32),b='b'.repeat(32);let current={profiles:{profiles:[{id:a,name:'Development',model:'model-a',selectable:true},{id:b,name:'Review',model:'model-b',selectable:true}],default_profile_id:a},value:a,currentId:a,busy:false,budget:100,language:'en',onChange:value=>{current={...current,value};ui.refresh(current);},onApply:()=>{posts++;},onRefresh:()=>{reads++;}};
 const ui=component(ModelTaskSelector,current),dom=settingsDraftDom(ui),snapshot=await captureForm(dom.root);
 assert.equal(snapshot,null,'a custom model picker is not a native draft text field');
 const picker=ui.find(node=>node.type?.name==='ModelReasoningPicker')[0];
 assert.deepEqual(picker.props.profiles.map(row=>[row.id,row.name,row.model]),[[a,'Development','model-a'],[b,'Review','model-b']]);
 picker.props.onModelChange(b);ui.refresh(current);
 assert.equal(current.value,b);assert.equal(posts,0);assert.equal(reads,0);assert.equal(dom.actions(),0);ui.unmount();
});

test('unsaved language selection does not change the model settings subtree or publish preferences',()=>{
 const previous={document:globalThis.document,window:globalThis.window},changes=[];let requests=0;
 globalThis.document={activeElement:null};globalThis.window={addEventListener(){},removeEventListener(){}};
 const ui=component(SettingsWindow,{prefs:normalizePreferences({language:'zh-CN'}),onPrefsChange:value=>changes.push(value),request:async()=>{requests++;return {};},onClose(){}});
 try{
  ui.fill(t('zh-CN','settings.language'),'en');
  ui.button(t('zh-CN','settings.models')).props.onClick();ui.refresh();
  const child=ui.find(node=>node.type?.name==='ModelProfilesSettings')[0];
  assert.ok(child);assert.equal(child.props.language,'zh-CN');
  assert.equal(changes.length,0);assert.equal(requests,0);
 }finally{ui.unmount();Object.assign(globalThis,previous);}
});

for(const language of ['zh-CN','en'])test(`rapid native draft events retain all settings values before a render (${language})`,()=>{
 const previous={document:globalThis.document,window:globalThis.window},changes=[];let requests=0;
 globalThis.document={activeElement:null};globalThis.window={addEventListener(){},removeEventListener(){}};
 const ui=component(SettingsWindow,{prefs:normalizePreferences({language,notifications:{quietHours:{enabled:true}}}),onPrefsChange:value=>changes.push(value),request:async()=>{requests++;return {};},onClose(){}});
 try{
  const theme=ui.input(t(language,'settings.theme')).props.onChange,font=ui.input(t(language,'settings.fontSize')).props.onChange,locale=ui.input(t(language,'settings.language')).props.onChange;
  theme({target:{value:'dark'}});font({target:{value:'17'}});locale({target:{value:language==='en'?'zh-CN':'en'}});ui.refresh();
  assert.equal(ui.input(t(language,'settings.theme')).props.value,'dark');assert.equal(ui.input(t(language,'settings.fontSize')).props.value,17);
  ui.button(t(language,'settings.notifications')).props.onClick();ui.refresh();
  const label=ui.find(node=>node.type==='label'&&content(node)===t(language,'notifications.approval'))[0],pending=nodes(label,node=>node.type==='input')[0].props.onInput;
  const start=ui.input(t(language,'notifications.start')).props.onChange,end=ui.input(t(language,'notifications.end')).props.onChange;
  pending({currentTarget:{checked:false}});start({target:{value:'21:00'}});end({target:{value:'07:30'}});ui.refresh();
  const finalLabel=ui.find(node=>node.type==='label'&&content(node)===t(language,'notifications.approval'))[0];
  assert.equal(nodes(finalLabel,node=>node.type==='input')[0].props.checked,false);assert.equal(ui.input(t(language,'notifications.start')).props.value,'21:00');assert.equal(ui.input(t(language,'notifications.end')).props.value,'07:30');
  assert.equal(changes.length,0);assert.equal(requests,0);
 }finally{ui.unmount();Object.assign(globalThis,previous);}
});

test('native input events keep both time values in React state before any save or permission request',async()=>{
 const previous={document:globalThis.document,window:globalThis.window},changes=[];let requests=0;
 globalThis.document={activeElement:null};globalThis.window={addEventListener(){},removeEventListener(){}};
 const ui=component(SettingsWindow,{prefs:normalizePreferences({language:'en',notifications:{quietHours:{enabled:true}}}),onPrefsChange:value=>changes.push(value),request:async()=>{requests++;return {};},onClose(){}});
 try{
  ui.button('Notifications').props.onClick();ui.refresh();
  const dom=settingsDraftDom(ui,{nativeTimeInput:true}),snapshot=await captureForm(dom.root);
  const proposal={form_id:snapshot.form_id,revision:snapshot.revision,draft_only:true,changes:[['Pending review reminders',false],['Start time','21:00'],['End time','07:30']].map(([label,value])=>({field_id:snapshot.fields.find(field=>field.label===label).id,value}))};
  assert.deepEqual(await applyProposal(dom.root,snapshot,proposal),{applied:true,reason:'applied'});ui.refresh();
  const label=ui.find(node=>node.type==='label'&&content(node)==='Pending review reminders')[0];
  assert.equal(nodes(label,node=>node.type==='input')[0].props.checked,false);assert.equal(ui.input('Start time').props.value,'21:00');assert.equal(ui.input('End time').props.value,'07:30');assert.equal(changes.length,0);assert.equal(requests,0);assert.equal(dom.actions(),0);
 }finally{ui.unmount();Object.assign(globalThis,previous);}
});
