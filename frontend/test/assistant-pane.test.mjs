import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import './tsx-register.mjs';
const {ModelReasoningPicker}=await import('../src/ModelReasoningPicker.tsx');
const {AssistantPane}=await import('../src/AssistantPane.tsx');
const {HelpDialog}=await import('../src/HelpDialog.tsx');
const nodes=(n,check)=>!n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(v=>nodes(v,check)):[...(check(n)?[n]:[]),...nodes(n.props?.children??n.children,check)];
const content=n=>n==null||typeof n==='boolean'?'':typeof n!=='object'?String(n):Array.isArray(n)?n.map(content).join(''):content(n.props?.children??n.children);
function component(Component,initialProps){
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;let cursor=0,dirty=false,tree,effects=[],props=initialProps;
 const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
 const dispatcher={useId(){const i=cursor++;return hooks[i]??='component-'+i;},useState(initial){const i=cursor++;hooks[i]??={value:typeof initial==='function'?initial():initial};return [hooks[i].value,next=>{const value=typeof next==='function'?next(hooks[i].value):next;if(!Object.is(value,hooks[i].value)){hooks[i].value=value;dirty=true;}}];},useRef(value){const i=cursor++;return hooks[i]??={current:value};},useEffect(fn,deps){const i=cursor++;if(!hooks[i]||!same(hooks[i].deps,deps)){const cleanup=hooks[i]?.cleanup;hooks[i]={deps};effects.push(()=>{cleanup?.();hooks[i].cleanup=fn();});}}};
 function refresh(nextProps){if(nextProps)props=nextProps;let rounds=0;do{assert.ok(++rounds<30);dirty=false;cursor=0;effects=[];const old=internal.H;internal.H=dispatcher;try{tree=Component(props);}finally{internal.H=old;}for(const fn of effects)fn();}while(dirty);return tree;}
 refresh();return {refresh,tree:()=>tree,text:()=>content(tree),find:check=>nodes(tree,check),button(label){const native=nodes(tree,n=>n.type==='button'&&(content(n)===label||n.props['aria-label']===label))[0];if(native)return native;const picker=nodes(tree,n=>n.type===ModelReasoningPicker)[0];if(picker&&label==='Switch model / thinking depth')return {props:{onClick:picker.props.onApply||(()=>{}),disabled:picker.props.disabled||picker.props.applyDisabled||!picker.props.onApply}};},input(label){const native=nodes(tree,n=>['input','textarea','select'].includes(n.type)&&n.props['aria-label']===label)[0];if(native)return native;const picker=nodes(tree,n=>n.type===ModelReasoningPicker)[0];if(picker&&['Model for new session','Switch to model','Thinking depth'].includes(label)){const depth=label==='Thinking depth';return {props:{value:depth?picker.props.effort:picker.props.value,disabled:picker.props.disabled,onChange:event=>(depth?picker.props.onEffortChange:picker.props.onModelChange)(event.target.value)}};}},fill(label,value){this.input(label).props.onChange({target:{value}});refresh();},form(){return nodes(tree,n=>n.type==='form')[0];},unmount(){for(const hook of hooks)hook?.cleanup?.();}};
}
async function settle(ui){for(let i=0;i<8;i++)await Promise.resolve();ui.refresh();}
function environment(){const previous={sessionStorage:globalThis.sessionStorage,document:globalThis.document,window:globalThis.window},values=new Map();globalThis.sessionStorage={getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};globalThis.document={activeElement:null,body:{nodeType:1},addEventListener(){},removeEventListener(){}};globalThis.window={innerWidth:390,innerHeight:700,addEventListener(){},removeEventListener(){}};return {values,restore(){Object.assign(globalThis,previous);}};}
const profile={id:'p'.repeat(32),name:'Development',model:'fixture-model',selectable:true};
const profiles={default_profile_id:profile.id,profiles:[profile]};
const base={profiles,language:'en',getFormRoot:()=>null,onOpenHelp(){}};
const session={id:'s'.repeat(32),profile_id:profile.id,title:'First session',model:profile.model,messages:[]};
const empty={sessions:[],jobs:[],budgets:{}};
const event={preventDefault(){}};

test('assistant works without a repository and rendering never creates sessions or calls a model',()=>{
 let calls=0;const html=renderToStaticMarkup(React.createElement(AssistantPane,{...base,request:()=>{calls++;}}));assert.equal(calls,0);assert.match(html,/without connecting a repository/);assert.match(html,/Create new session/);assert.match(html,/Sending uses the selected model/);assert.doesNotMatch(html,/dangerouslySetInnerHTML/);
});
test('explicit create and multi-turn send each submit once and retain the session model',async()=>{
 const env=environment(),calls=[];let state=empty,resolve;const request=async(path,token,body)=>{calls.push({path,token,body:body&&structuredClone(body)});if(!body)return state;if(path.endsWith('/create'))return new Promise(done=>{resolve=()=>{state={sessions:[session],jobs:[{id:body.request_id,status:'finished',result:{session_id:session.id}}],budgets:{}};done(state.jobs[0]);};});state={...state,sessions:[{...session,messages:[{id:'user',role:'user',content:body.text},{id:'answer',role:'assistant',content:'Here is the guide.'}]}],jobs:[{id:body.request_id,status:'finished',result:{session_id:session.id,message_id:'answer'}}]};return state.jobs[0];};
 const ui=component(AssistantPane,{...base,request});
 try{await settle(ui);const create=ui.button('Create new session').props.onClick,pending=create();ui.refresh();await create();assert.equal(calls.filter(c=>c.body).length,1);assert.equal(calls.find(c=>c.body).body.profile_id,profile.id);resolve();await pending;await settle(ui);
  assert.equal(ui.input('Message to assistant').props.disabled,false);ui.fill('Message to assistant','How do I begin?');await ui.form().props.onSubmit(event);await settle(ui);
  const writes=calls.filter(c=>c.body);assert.equal(writes.length,2);assert.equal(writes[1].path,'/api/assistant/actions/send');assert.deepEqual(writes[1].body,{request_id:writes[1].body.request_id,session_id:session.id,text:'How do I begin?',form_context:null,language:'en'});assert.match(writes[1].body.request_id,/^[a-f0-9]{32}$/);assert.equal(ui.input('Message to assistant').props.value,'');assert.match(ui.text(),/Here is the guide/);
  ui.fill('Message to assistant','And then?');await ui.form().props.onSubmit(event);await settle(ui);assert.equal(calls.filter(c=>c.body).length,3);assert.equal(calls.filter(c=>c.body)[2].body.session_id,session.id);
 }finally{ui.unmount();env.restore();}
});
test('a lost send response retains only request identifiers and blocks replay until exact read-only reconciliation',async()=>{
 const env=environment(),calls=[];let ownId='',ownJob=null;const request=async(path,token,body)=>{calls.push({path,body});if(body){ownId=body.request_id;const error=Error('transport failed');error.uncertain=true;throw error;}return {sessions:[session],jobs:[{id:'unrelated',status:'finished',result:{session_id:session.id}},...(ownJob?[ownJob]:[])],budgets:{}};};
 const ui=component(AssistantPane,{...base,request});
 try{await settle(ui);ui.fill('Message to assistant','private text must not persist in browser storage');const submit=ui.form().props.onSubmit;await submit(event);ui.refresh();assert.match(ui.text(),/result is unknown/);assert.equal(ui.button('Send'),undefined);await submit(event);assert.equal(calls.filter(c=>c.body).length,1);assert.doesNotMatch([...env.values.values()].join(' '),/private text/);
  await ui.button('Check this operation again').props.onClick();ui.refresh();assert.match(ui.text(),/result is unknown/);assert.equal(calls.filter(c=>c.body).length,1);
  ownJob={id:ownId,status:'finished',result:{session_id:session.id,message_id:'answer'}};await ui.button('Check this operation again').props.onClick();ui.refresh();assert.equal(ui.input('Message to assistant').props.disabled,false);assert.equal(ui.input('Message to assistant').props.value,'');assert.equal(env.values.size,0);assert.equal(calls.filter(c=>c.body).length,1);
 }finally{ui.unmount();env.restore();}
});
test('known rejection preserves the unsent text and never falls back to a different model',async()=>{
 const env=environment(),calls=[],ui=component(AssistantPane,{...base,request:async(path,token,body)=>{calls.push({path,body});if(body)throw Error('rejected');return {sessions:[session],jobs:[],budgets:{}};}});
 try{await settle(ui);ui.fill('Message to assistant','Keep this draft');await ui.form().props.onSubmit(event);ui.refresh();assert.equal(ui.input('Message to assistant').props.value,'Keep this draft');assert.match(ui.text(),/did not complete/);assert.equal(calls.filter(c=>c.body).length,1);assert.equal(calls.find(c=>c.body).body.session_id,session.id);}finally{ui.unmount();env.restore();}
});
class DraftField{
 constructor(){this.tagName='INPUT';this.type='text';this.labels=[{textContent:'Configuration name'}];this._value='Before';this.isConnected=true;this.id='name';this.events=[];this.ownerDocument={defaultView:{Event,getComputedStyle:()=>({display:'block',visibility:'visible'})}};}
 get value(){return this._value;}set value(value){this._value=String(value);}getClientRects(){return [{}];}closest(){return null;}matches(){return false;}getAttribute(name){return name==='data-form-assist-draft'?'true':null;}dispatchEvent(event){this.events.push(event.type);}click(){throw Error('forbidden');}
}
test('displaying a model proposal never changes a form; user application updates only the captured draft',async()=>{
 const env=environment(),field=new DraftField(),root={isConnected:true,getClientRects:()=>[{}],closest:()=>null,querySelectorAll:()=>[field],contains:node=>node===field,submit(){throw Error('forbidden');}},calls=[];let state={sessions:[session],jobs:[],budgets:{}};
 const request=async(path,token,body)=>{calls.push({path,body});if(!body)return state;const s=body.form_context,p={form_id:s.form_id,revision:s.revision,changes:[{field_id:s.fields[0].id,value:'Suggested'}],draft_only:true,explanation:'A clearer draft name.'};state={...state,sessions:[{...session,messages:[{id:'proposal-message',role:'assistant',content:'Review this draft.',proposal:p}]}],jobs:[{id:body.request_id,status:'finished',result:{session_id:session.id,message_id:'proposal-message'}}]};return state.jobs[0];};
 const ui=component(AssistantPane,{...base,getFormRoot:()=>root,request});
 try{await settle(ui);ui.fill('Message to assistant','Help with this form');await ui.form().props.onSubmit(event);await settle(ui);assert.equal(field.value,'Before');assert.match(ui.text(),/Suggested/);assert.deepEqual(field.events,[]);const writes=calls.filter(c=>c.body).length;await ui.button('Apply suggestion to draft').props.onClick();ui.refresh();assert.equal(field.value,'Suggested');assert.deepEqual(field.events,['input','change']);assert.equal(calls.filter(c=>c.body).length,writes);assert.match(ui.text(),/Draft updated/);}finally{ui.unmount();env.restore();}
});
test('help searches catalog and Markdown sections, renders hostile text safely, and downloads the same source',async()=>{
 const env=environment(),calls=[],markdown='# Start\nConnect a model.\n\n## Memory\nReview memory. <script>not executable</script>\n',ui=component(HelpDialog,{language:'en',onClose(){},request:async(path,token,body)=>{calls.push({path,body});return {title:'Guide',content:markdown,sha256:'a'.repeat(64),illustrated_guide:true,catalog:[{id:'models',title:'Models',page:'configuration',availability:'implemented',description:'Connect a model.'},{id:'memory',title:'Memory',page:'memory',availability:'partial',description:'Review memory.'}]};}});
 try{await settle(ui);assert.deepEqual(calls,[{path:'/api/help?language=en',body:undefined}]);const links=ui.find(n=>n.type==='a'),link=links.find(n=>n.props.download==='RepoCare-en.md');assert.equal(await fetch(link.props.href).then(response=>response.text()),markdown);assert.equal(links.find(n=>n.props.href==='/tutorial/index.html').props.target,'_blank');assert.equal(links.find(n=>n.props.href==='/tutorial/repocare-guide.zip').props.download,true);const html=renderToStaticMarkup(ui.tree());assert.doesNotMatch(html,/<script>/);assert.match(html,/&lt;script&gt;/);ui.fill('Search the guide and features','Memory');assert.match(ui.text(),/Review memory/);assert.doesNotMatch(ui.text(),/Connect a model/);assert.equal(calls.length,1);}finally{ui.unmount();env.restore();}
});

test('select suggestions show the human option captions while applying only the original opaque value',async()=>{
 const env=environment(),first='a'.repeat(32),second='b'.repeat(32),field=new DraftField();
 Object.assign(field,{tagName:'SELECT',type:'select-one',labels:[{textContent:'Model selection'}],options:[{value:first,label:'Friendly model A'},{value:second,label:'Friendly model B'}]});field.value=first;
 const root={isConnected:true,getClientRects:()=>[{}],closest:()=>null,querySelectorAll:()=>[field],contains:node=>node===field,submit(){throw Error('forbidden');}},calls=[];let state={sessions:[session],jobs:[],budgets:{}};
 const request=async(path,token,body)=>{calls.push({path,body});if(!body)return state;const form=body.form_context;assert.deepEqual(form.fields[0].options,[first,second]);assert.deepEqual(form.fields[0].option_labels,['Friendly model A','Friendly model B']);state={...state,sessions:[{...session,messages:[{id:'select-proposal',role:'assistant',content:'Review the selected model.',proposal:{form_id:form.form_id,revision:form.revision,changes:[{field_id:form.fields[0].id,value:second}],explanation:'Draft only.',draft_only:true}}]}]};return {id:body.request_id,status:'finished',result:{session_id:session.id}};};
 const ui=component(AssistantPane,{...base,getFormRoot:()=>root,request});
 try{await settle(ui);ui.fill('Message to assistant','Choose the second model.');await ui.form().props.onSubmit(event);await settle(ui);assert.match(ui.text(),/Friendly model A/);assert.match(ui.text(),/Friendly model B/);assert.doesNotMatch(ui.text(),new RegExp(second));assert.equal(field.value,first);const writes=calls.filter(call=>call.body).length;await ui.button('Apply suggestion to draft').props.onClick();await settle(ui);assert.equal(field.value,second);assert.deepEqual(field.events,['input','change']);assert.equal(calls.filter(call=>call.body).length,writes);}finally{ui.unmount();env.restore();}
});
test('two-session selection, model and unsent text survive actual AssistantPane remounts without POST or browser text storage',async()=>{
 const env=environment(),calls=[],memory={current:{model:'',selected:'',text:''}},alternate={...profile,id:'q'.repeat(32),name:'Alternate model'},older={...session,id:'b'.repeat(32),title:'Older selected session',messages:[{id:'older-answer',role:'assistant',content:'The selected older conversation.'}]},newest={...session,messages:[{id:'newest-answer',role:'assistant',content:'Newest conversation should not replace the chosen one.'}]};
 const props={...base,memory,profiles:{default_profile_id:profile.id,profiles:[profile,alternate]},request:async(path,token,body)=>{calls.push({path,body});if(body)throw Error('remount must not send');return {sessions:[newest,older],jobs:[],budgets:{}};}};
 let current;
 try{
  current=component(AssistantPane,props);await settle(current);current.fill('Select session',older.id);current.fill('Model for new session',alternate.id);current.fill('Message to assistant','Unsent form question');
  assert.deepEqual(memory.current,{model:alternate.id,selected:older.id,text:'Unsent form question'},'shared memory must be updated before unmount, not by cleanup');current.unmount();
  current=component(AssistantPane,props);await settle(current);assert.equal(current.input('Select session').props.value,older.id);assert.equal(memory.current.model,alternate.id);assert.equal(current.input('Message to assistant').props.value,'Unsent form question');assert.match(current.text(),/The selected older conversation/);assert.doesNotMatch(current.text(),/Newest conversation should not replace/);
  current.fill('Message to assistant','Edited while inside settings');assert.equal(memory.current.text,'Edited while inside settings');current.unmount();
  current=component(AssistantPane,props);await settle(current);assert.equal(current.input('Select session').props.value,older.id);assert.equal(memory.current.model,alternate.id);assert.equal(current.input('Message to assistant').props.value,'Edited while inside settings');assert.equal(calls.filter(call=>call.body).length,0);assert.equal(env.values.size,0);assert.deepEqual(Object.keys(memory.current).sort(),['model','selected','text']);
 }finally{current?.unmount();env.restore();}
});

test('English help translates fixed catalog captions and keeps partial availability separate from draft warnings',async()=>{
 const env=environment(),source={title:'Guide',content:'# Guide\nOriginal evidence: 中文原始测试结果\n',catalog:[{id:'A01',title:'选择本地 Git 仓库',description:'现有动作沿用原控制器与权限；可定位本页现有操作。',availability:'partial',page:'overview'}]},before=JSON.stringify(source),calls=[];
 const ui=component(HelpDialog,{language:'en',onClose(){},request:async(path,token,body)=>{calls.push({path,body});return source;}});
 try{await settle(ui);assert.match(ui.text(),/Select a local Git repository/);assert.match(ui.text(),/Partially implemented/);assert.doesNotMatch(ui.text(),/Filling stopped/);assert.match(ui.text(),/中文原始测试结果/);ui.fill('Search the guide and features','local Git');assert.match(ui.text(),/Select a local Git repository/);assert.equal(calls.length,1);assert.equal(JSON.stringify(source),before);}finally{ui.unmount();env.restore();}
});

test('explicit assistant thinking switch creates a variant and linked conversation without sending or losing the draft',async()=>{
 const env=environment(),calls=[],source={...profile,base_url:'https://provider.invalid/v1',reasoning_protocol:'deepseek',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'max'}]},variant={...source,id:'v'.repeat(32),reasoning_effort:'max'},next={...session,id:'n'.repeat(32),profile_id:variant.id,continuation:{source_session_id:session.id,source_profile_id:source.id,source_message_count:1},history_messages:[{role:'assistant',content:'Original history, read only',proposal:{changes:[]}}]},catalog={revision:'r1',default_profile_id:source.id,profiles:[source]};let state={sessions:[session],jobs:[],budgets:{}};
 const request=async(path,token,body)=>{
  calls.push({path,body:body&&structuredClone(body)});if(!body)return path==='/api/model-profiles'?catalog:state;
  if(path==='/api/model-profiles')return {...catalog,revision:'r2',profiles:[source,variant],created_profile_id:variant.id};
  assert.equal(path,'/api/assistant/actions/switch_model');state={...state,sessions:[session,next],jobs:[{id:body.request_id,status:'finished',result:{session_id:next.id}}]};return state.jobs[0];
 };
 const ui=component(AssistantPane,{...base,profiles:catalog,request});
 try{await settle(ui);ui.fill('Message to assistant','Unsent draft survives');ui.fill('Thinking depth','max');assert.equal(calls.filter(call=>call.body).length,0);
  const switchClick=ui.button('Switch model / thinking depth').props.onClick;const operation=switchClick();await switchClick();await operation;await settle(ui);
  const writes=calls.filter(call=>call.body);assert.equal(writes.length,2);assert.deepEqual(writes[0],{path:'/api/model-profiles',body:{action:'reasoning_variant',revision:'r1',profile_id:source.id,reasoning_effort:'max'}});
  assert.deepEqual(writes[1].body,{request_id:writes[1].body.request_id,session_id:session.id,profile_id:variant.id});
  assert.equal(ui.input('Select session').props.value,next.id);assert.equal(ui.input('Message to assistant').props.value,'Unsent draft survives');assert.match(ui.text(),/Original history, read only/);assert.match(ui.text(),/Linked history \(read only\)/);assert.equal(ui.button('Apply suggestion to draft'),undefined);assert.equal(env.values.size,0);
 }finally{ui.unmount();env.restore();}
});

test('changing the assistant model switches only on the button and keeps the original send context until then',async()=>{
 const env=environment(),other={...profile,id:'b'.repeat(32),model:'other-api-id'},calls=[];let state={sessions:[session],jobs:[],budgets:{}};
 const ui=component(AssistantPane,{...base,profiles:{...profiles,profiles:[profile,other]},request:async(path,token,body)=>{calls.push({path,body});if(!body)return state;state={...state,sessions:[session,{...session,id:'c'.repeat(32),profile_id:other.id,model:other.model}],jobs:[{id:body.request_id,status:'finished',result:{session_id:'c'.repeat(32)}}]};return state.jobs[0];}});
 try{await settle(ui);ui.fill('Switch to model',other.id);assert.equal(calls.filter(call=>call.body).length,0);assert.equal(ui.find(node=>node.type===ModelReasoningPicker)[0].props.profiles.find(row=>row.id===other.id).reasoning_protocol,undefined);await ui.button('Switch model / thinking depth').props.onClick();await settle(ui);
  assert.equal(calls.filter(call=>call.body).length,1);assert.equal(calls.find(call=>call.body).body.profile_id,other.id);assert.equal(ui.input('Select session').props.value,'c'.repeat(32));
 }finally{ui.unmount();env.restore();}
});

test('an unknown variant result blocks new operations until a successful read-only model refresh',async()=>{
 const env=environment(),source={...profile,reasoning_protocol:'deepseek',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'max'}]},calls=[];let failRead=true;
 const catalog={revision:'r1',default_profile_id:source.id,profiles:[source]},ui=component(AssistantPane,{...base,profiles:catalog,request:async(path,token,body)=>{
  calls.push({path,body});if(body){const error=Error('unknown result');error.uncertain=true;throw error;}if(path==='/api/model-profiles'){if(failRead)throw Error('read failed');return catalog;}return {sessions:[session],jobs:[],budgets:{}};
 }});
 try{await settle(ui);ui.fill('Thinking depth','max');ui.fill('Message to assistant','Keep the draft');const click=ui.button('Switch model / thinking depth').props.onClick;await click();await settle(ui);
  assert.match(ui.text(),/thinking configuration result is unknown/);assert.equal(ui.button('Create new session').props.disabled,true);assert.equal(ui.input('Message to assistant').props.disabled,true);await click();assert.equal(calls.filter(call=>call.body).length,1);
  assert.doesNotMatch([...env.values.values()].join(' '),/Keep the draft/);await ui.button('Check this operation again').props.onClick();await settle(ui);assert.equal(ui.input('Message to assistant').props.disabled,true);
  failRead=false;await ui.button('Check this operation again').props.onClick();await settle(ui);assert.equal(ui.input('Message to assistant').props.disabled,false);assert.equal(ui.input('Message to assistant').props.value,'Keep the draft');assert.equal(calls.filter(call=>call.body).length,1);assert.equal(env.values.size,0);
 }finally{ui.unmount();env.restore();}
});

test('assistant failed jobs show only fixed localized categories and preserve the unsent draft',async()=>{
 for(const category of ['invalid_model','auth','network','rate_limit','unsupported_parameters','provider_error','timeout','budget_blocked','unknown-raw-category']){
  const env=environment();let state={sessions:[session],jobs:[],budgets:{}};
  const ui=component(AssistantPane,{...base,request:async(path,token,body)=>{if(!body)return state;const job={id:body.request_id,status:'failed',error_category:category,error:'Bearer synthetic-private-credential do not render'};state={...state,jobs:[job]};return job;}});
  try{await settle(ui);ui.fill('Message to assistant','Keep this failed draft');await ui.form().props.onSubmit(event);await settle(ui);assert.equal(ui.input('Message to assistant').props.value,'Keep this failed draft');assert.doesNotMatch(ui.text(),/synthetic-private-credential|unknown-raw-category|Bearer/);assert.ok(ui.find(node=>node.props?.role==='alert').length);if(category==='invalid_model')assert.match(ui.text(),/API model ID/);
   if(category==='timeout')assert.match(ui.text(),/model request timed out/);
   if(category==='budget_blocked')assert.match(ui.text(),/no remaining assistant allowance/);
  }finally{ui.unmount();env.restore();}
 }
});

test('variant intent is persisted before POST and survives settings remount without another write until explicit catalog reconciliation',async()=>{
 const env=environment(),calls=[],source={...profile,base_url:'https://provider.invalid/v1',reasoning_protocol:'openai',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'max'}]},variant={...source,id:'v'.repeat(32),reasoning_effort:'max'},catalog={revision:'r1',default_profile_id:source.id,profiles:[source]},memory={current:{model:source.id,selected:session.id,text:'Keep this unsent draft'}};let resolve;
 const request=async(path,token,body)=>{calls.push({path,body:body&&structuredClone(body)});if(body)return new Promise(done=>{resolve=done;});return path==='/api/model-profiles'?{...catalog,revision:'r2',profiles:[source,variant]}:{sessions:[session],jobs:[],budgets:{}};};
 const p={...base,profiles:catalog,request,memory};let first,second;
 try{
  first=component(AssistantPane,p);await settle(first);first.fill('Thinking depth','max');const pending=first.button('Switch model / thinking depth').props.onClick();
  assert.equal(calls.filter(call=>call.body).length,1);assert.match([...env.values.values()].join(' '),/"stage":"variant"/);assert.doesNotMatch([...env.values.values()].join(' '),/Keep this unsent draft/);first.unmount();
  second=component(AssistantPane,p);await settle(second);assert.equal(second.button('Switch model / thinking depth').props.disabled,true);assert.equal(second.input('Message to assistant').props.disabled,true);
  await second.button('Switch model / thinking depth').props.onClick();assert.equal(calls.filter(call=>call.body).length,1);
  resolve({...catalog,revision:'r2',profiles:[source,variant],created_profile_id:variant.id});await pending;await settle(second);assert.equal(calls.some(call=>call.path==='/api/assistant/actions/switch_model'),false);
  await second.button('Check this operation again').props.onClick();await settle(second);assert.ok(calls.some(call=>call.path==='/api/model-profiles'&&!call.body));assert.equal(calls.filter(call=>call.body).length,1);assert.equal(second.input('Message to assistant').props.value,'Keep this unsent draft');assert.equal(env.values.size,0);
 }finally{second?.unmount();env.restore();}
});

test('a malformed variant changing limits is treated as unknown and cannot start a linked conversation',async()=>{
 const env=environment(),calls=[],source={...profile,base_url:'https://provider.invalid/v1',context_window_tokens:1000000,max_output_tokens:256000,reasoning_protocol:'openai',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'max'}]},derived={...source,id:'v'.repeat(32),reasoning_effort:'max',max_output_tokens:512000},catalog={revision:'r1',profiles:[source]};
 const ui=component(AssistantPane,{...base,profiles:catalog,request:async(path,token,body)=>{calls.push({path,body});return body?{...catalog,revision:'r2',profiles:[source,derived],created_profile_id:derived.id}:{sessions:[session],jobs:[],budgets:{}};}});
 try{await settle(ui);ui.fill('Thinking depth','max');await ui.button('Switch model / thinking depth').props.onClick();await settle(ui);assert.equal(calls.filter(call=>call.body).length,1);assert.equal(calls.some(call=>call.path==='/api/assistant/actions/switch_model'),false);assert.match(ui.text(),/thinking configuration result is unknown/);assert.equal(ui.input('Message to assistant').props.disabled,true);}finally{ui.unmount();env.restore();}
});


test('assistant has one compact picker immediately below its textarea, with no duplicate model selects or unchanged Apply',async()=>{
 const env=environment(),calls=[],ui=component(AssistantPane,{...base,request:async(path,token,body)=>{calls.push({path,body});return {sessions:[session],jobs:[],budgets:{}};}});
 try{await settle(ui);const pickers=ui.find(node=>node.type===ModelReasoningPicker);assert.equal(pickers.length,1);assert.equal(pickers[0].props.onApply,undefined);const composer=ui.form(),children=React.Children.toArray(composer.props.children),textarea=children.findIndex(node=>node.type==='textarea');assert.equal(children[textarea+1].props.className,'assistant-composer-bottom');assert.equal(nodes(children[textarea+1],node=>node.type===ModelReasoningPicker).length,1);assert.deepEqual(ui.find(node=>node.type==='select').map(node=>node.props['aria-label']),['Select session']);assert.equal(calls.filter(call=>call.body).length,0);}
 finally{ui.unmount();env.restore();}
});

test('new assistant conversation prepares the chosen thinking variant once and never sends a message',async()=>{
 const env=environment(),calls=[],source={...profile,base_url:'https://provider.invalid/v1',reasoning_protocol:'openai',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'custom-max'}]},variant={...source,id:'v'.repeat(32),reasoning_effort:'max'},catalog={revision:'r1',default_profile_id:source.id,profiles:[source]};let state=empty;
 const ui=component(AssistantPane,{...base,profiles:catalog,request:async(path,token,body)=>{calls.push({path,body:body&&structuredClone(body)});if(!body)return state;if(path==='/api/model-profiles')return {...catalog,revision:'r2',profiles:[source,variant],created_profile_id:variant.id};assert.equal(path,'/api/assistant/actions/create');state={sessions:[{...session,profile_id:variant.id}],jobs:[{id:body.request_id,status:'finished',result:{session_id:session.id}}],budgets:{}};return state.jobs[0];}});
 try{await settle(ui);ui.fill('Thinking depth','max');assert.equal(calls.filter(call=>call.body).length,0);const click=ui.button('Create new session').props.onClick,pending=click();await click();await pending;await settle(ui);const writes=calls.filter(call=>call.body);assert.equal(writes.length,2);assert.equal(writes[0].body.reasoning_effort,'max');assert.deepEqual(writes[1].body,{request_id:writes[1].body.request_id,profile_id:variant.id});assert.equal(calls.some(call=>call.path.endsWith('/send')||call.path.endsWith('/switch_model')),false);assert.equal(ui.input('Message to assistant').props.disabled,false);}
 finally{ui.unmount();env.restore();}
});

test('archived assistant model remains in historical display but cannot create or become the draft selection',async()=>{
 const env=environment(),calls=[],archived={...profile,archived:true},other={...profile,id:'b'.repeat(32),name:'Available model'},catalog={...profiles,profiles:[archived,other]},ui=component(AssistantPane,{...base,profiles:catalog,request:async(path,token,body)=>{calls.push({path,body});return {sessions:[session],jobs:[],budgets:{}};}});
 try{await settle(ui);const picker=ui.find(node=>node.type===ModelReasoningPicker)[0];assert.equal(picker.props.value,archived.id);assert.equal(ui.button('Create new session').props.disabled,true);picker.props.onModelChange(archived.id);ui.refresh();assert.equal(ui.find(node=>node.type===ModelReasoningPicker)[0].props.value,archived.id);await ui.button('Create new session').props.onClick();assert.equal(calls.filter(call=>call.body).length,0);picker.props.onModelChange(other.id);ui.refresh();assert.equal(ui.find(node=>node.type===ModelReasoningPicker)[0].props.value,other.id);assert.equal(ui.button('Create new session').props.disabled,false);}
 finally{ui.unmount();env.restore();}
});


test('pending assistant thinking selection blocks Send and keyboard submission until explicit application, preserving the draft',async()=>{
 const env=environment(),calls=[],source={...profile,reasoning_protocol:'openai',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'max'}]},variant={...source,id:'v'.repeat(32),reasoning_effort:'max'},next={...session,id:'n'.repeat(32),profile_id:variant.id},catalog={revision:'r1',default_profile_id:source.id,profiles:[source]};let state={sessions:[session],jobs:[],budgets:{}};
 const ui=component(AssistantPane,{...base,profiles:catalog,request:async(path,token,body)=>{calls.push({path,body});if(!body)return state;if(path==='/api/model-profiles')return {...catalog,revision:'r2',profiles:[source,variant],created_profile_id:variant.id};assert.equal(path,'/api/assistant/actions/switch_model');state={sessions:[session,next],jobs:[{id:body.request_id,status:'finished',result:{session_id:next.id}}],budgets:{}};return state.jobs[0];}});
 try{await settle(ui);ui.fill('Message to assistant','Do not lose this draft');assert.equal(ui.button('Send').props.disabled,false);ui.fill('Thinking depth','max');assert.equal(ui.button('Send').props.disabled,true);assert.equal(ui.find(node=>node.type===ModelReasoningPicker)[0].props.pending,true);assert.match(ui.text(),/Apply the model selection before sending/);await ui.form().props.onSubmit(event);ui.input('Message to assistant').props.onKeyDown({ctrlKey:true,key:'Enter',preventDefault(){}});await settle(ui);assert.equal(calls.filter(call=>call.body).length,0);assert.equal(ui.input('Message to assistant').props.value,'Do not lose this draft');await ui.button('Switch model / thinking depth').props.onClick();await settle(ui);assert.equal(ui.button('Send').props.disabled,false);assert.equal(ui.find(node=>node.type===ModelReasoningPicker)[0].props.pending,false);assert.equal(ui.input('Message to assistant').props.value,'Do not lose this draft');assert.equal(calls.some(call=>call.path.endsWith('/send')),false);}
 finally{ui.unmount();env.restore();}
});


test('saving four models updates the same assistant picker immediately without changing its original binding, budget or draft',async()=>{
 const env=environment(),calls=[],memory={current:{model:profile.id,selected:session.id,text:'Keep the original unsent draft'}},older={...profile,name:'火山',reasoning_protocol:'openai',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'}]},initialCatalog={revision:'before-save',default_profile_id:profile.id,profiles:[older]},four=Array.from({length:4},(_,index)=>({...older,id:String(index+1).repeat(32),name:'火山 · saved '+(index+1),model:index<2?'same-api-id':'new-api-'+index,context_window_tokens:100000+index})),savedCatalog={revision:'after-save',default_profile_id:four[3].id,profiles:[older,...four]},originalBudget={requests_used:7,requests_remaining:17,requests_reserved_unknown:2},savedState={sessions:[{...session,messages:[{id:'old-message',role:'assistant',content:'The original conversation remains.'}]}],jobs:[],budgets:{[older.id]:originalBudget}};
 const request=async(path,token,body)=>{calls.push({path,body});assert.equal(body,undefined,'saving profiles must never replay an assistant action');assert.equal(path,'/api/assistant/state');return savedState;},props={...base,profiles:initialCatalog,memory,request},ui=component(AssistantPane,props);let picker;
 try{await settle(ui);const first=ui.find(node=>node.type===ModelReasoningPicker)[0];assert.equal(first.props.value,older.id);assert.equal(first.props.profiles.length,1);const readsBefore=calls.length;
  ui.refresh({...props,profiles:savedCatalog});await settle(ui);const next=ui.find(node=>node.type===ModelReasoningPicker)[0];assert.equal(next.props.profiles.length,5);assert.deepEqual(next.props.profiles.map(row=>row.id),[older.id,...four.map(row=>row.id)]);assert.equal(next.props.value,older.id);assert.equal(next.props.effort,'high');assert.equal(next.props.pending,false);assert.equal(next.props.onApply,undefined);assert.equal(ui.input('Select session').props.value,session.id);assert.equal(ui.input('Message to assistant').props.value,'Keep the original unsent draft');assert.deepEqual(memory.current,{model:older.id,selected:session.id,text:'Keep the original unsent draft'});assert.match(ui.text(),/7 used · 17 remaining · 2 unknown/);assert.match(ui.text(),/The original conversation remains/);assert.deepEqual(savedState.budgets[older.id],originalBudget);assert.equal(calls.length,readsBefore,'updating supplied catalog needs neither state refresh nor another catalog request');
  picker=component(ModelReasoningPicker,next.props);picker.button('Assistant model and thinking depth').props.onClick();picker.refresh();picker.button('Models').props.onClick();picker.refresh();const rows=picker.find(node=>node.props?.role==='radio');assert.equal(rows.length,5);assert.deepEqual(rows.map(row=>row.key),[older.id,...four.map(row=>row.id)]);assert.equal(rows.find(row=>row.props['aria-checked']).key,older.id);assert.equal(rows.filter(row=>content(row).includes('same-api-id')).length,2,'distinct stored configurations keep their exact IDs even with the same API model name');assert.equal(calls.filter(call=>call.body).length,0);
 }finally{picker?.unmount();ui.unmount();env.restore();}
});

test('a settings catalog update and failed explicit catalog read retain the unknown assistant intent and all draft choices',async()=>{
 const env=environment(),calls=[],source={...profile,reasoning_protocol:'openai',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'max'}]},initialCatalog={revision:'before-save',default_profile_id:profile.id,profiles:[source]},four=Array.from({length:4},(_,index)=>({...source,id:String(index+1).repeat(32),name:'Saved '+(index+1)})),savedCatalog={...initialCatalog,revision:'after-save',profiles:[source,...four]},request=async(path,token,body)=>{calls.push({path,body});if(body){const error=Error('lost variant receipt');error.uncertain=true;throw error;}if(path==='/api/model-profiles')throw Error('catalog read failed');return {sessions:[session],jobs:[],budgets:{[source.id]:{used:3,remaining:21,unknown:1}}};},props={...base,profiles:initialCatalog,request},ui=component(AssistantPane,props);
 try{await settle(ui);ui.fill('Message to assistant','Retain this unknown-operation draft');ui.fill('Thinking depth','max');await ui.button('Switch model / thinking depth').props.onClick();await settle(ui);const intent=[...env.values.values()][0];assert.match(intent,/"stage":"variant"/);assert.equal(ui.input('Message to assistant').props.disabled,true);
  ui.refresh({...props,profiles:savedCatalog});await settle(ui);let picker=ui.find(node=>node.type===ModelReasoningPicker)[0];assert.equal(picker.props.profiles.length,5);assert.equal(picker.props.value,source.id);assert.equal(picker.props.effort,'max');assert.equal(picker.props.disabled,true);assert.equal([...env.values.values()][0],intent);assert.equal(calls.filter(call=>call.body).length,1);
  await ui.button('Check this operation again').props.onClick();await settle(ui);picker=ui.find(node=>node.type===ModelReasoningPicker)[0];assert.deepEqual(picker.props.profiles.map(row=>row.id),savedCatalog.profiles.map(row=>row.id));assert.equal(picker.props.value,source.id);assert.equal(picker.props.effort,'max');assert.equal(picker.props.disabled,true);assert.equal(ui.input('Select session').props.value,session.id);assert.equal(ui.input('Message to assistant').props.value,'Retain this unknown-operation draft');assert.match(ui.text(),/3 used · 21 remaining · 1 unknown/);assert.equal([...env.values.values()][0],intent);assert.equal(calls.filter(call=>call.body).length,1);await ui.button('Switch model / thinking depth').props.onClick();await ui.form().props.onSubmit(event);assert.equal(calls.filter(call=>call.body).length,1,'catalog refresh cannot replay a variant, switch or send payload');
 }finally{ui.unmount();env.restore();}
});
