import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
const {ModelProfilesSettings}=await import('../src/ModelProfilesSettings.tsx');
const nodes=(node,check)=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(item=>nodes(item,check)):[...(check(node)?[node]:[]),...nodes(node.props?.children,check)];
const text=node=>node==null||typeof node==='boolean'?'':typeof node!=='object'?String(node):Array.isArray(node)?node.map(text).join(''):text(node.props?.children);
function component(initial){
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;let cursor=0,dirty=false,tree,effects=[];
 const same=(a,b)=>a&&b&&a.length===b.length&&a.every((value,index)=>Object.is(value,b[index]));
 const dispatcher={useState(initial){const index=cursor++;hooks[index]??={value:typeof initial==='function'?initial():initial};return [hooks[index].value,next=>{const value=typeof next==='function'?next(hooks[index].value):next;if(!Object.is(value,hooks[index].value)){hooks[index].value=value;dirty=true;}}];},useRef(value){const index=cursor++;return hooks[index]??={current:value};},useEffect(fn,deps){const index=cursor++;if(!hooks[index]||!same(hooks[index].deps,deps)){const cleanup=hooks[index]?.cleanup;hooks[index]={deps};effects.push(()=>{cleanup?.();hooks[index].cleanup=fn();});}}};
 function refresh(){let rounds=0;do{assert.ok(++rounds<30);dirty=false;cursor=0;effects=[];const previous=internal.H;internal.H=dispatcher;try{tree=ModelProfilesSettings(initial);}finally{internal.H=previous;}for(const effect of effects)effect();}while(dirty);return tree;}
 refresh();return {refresh,tree:()=>tree,text:()=>text(tree),find:check=>nodes(tree,check),input(label){return nodes(tree,node=>['input','select'].includes(node.type)&&node.props['aria-label']===label)[0];},button(label){return nodes(tree,node=>node.type==='button'&&(text(node)===label||node.props['aria-label']===label))[0];},fill(label,value){this.input(label).props.onChange({target:{value}});refresh();},choose(id,checked=true){this.input('Select model: '+id).props.onChange({currentTarget:{checked}});refresh();},form(){return nodes(tree,node=>node.type==='form')[0];},unmount(){for(const hook of hooks)hook?.cleanup?.();}};
}
const event={preventDefault(){}};
const profile={id:'a'.repeat(32),name:'Existing display name',base_url:'https://models.example.invalid/v1',model:'existing-api-id',key_set:true,selectable:true,limits_explicit:true,context_window_tokens:1000000,max_output_tokens:256000};
const catalog=(patch={})=>({revision:'r1',default_profile_id:profile.id,max_profiles:32,profiles:[profile],...patch});
const props=(request,patch={})=>({language:'en',initialProfiles:catalog(),request,...patch});
const directory={models:[{id:'model-a',name:'Model A',context_window_tokens:1500000,max_output_tokens:256000,reasoning_efforts:['low','high']},{id:'model-b',name:'Model B',context_window_tokens:2000000,max_output_tokens:512000,reasoning_efforts:[]},{id:'model-c',name:'Model C'}]};
function open(ui){ui.button('＋ Add model provider').props.onClick();ui.refresh();}
function fillConnection(ui){ui.fill('Base URL',profile.base_url);ui.fill('API Key','synthetic-key');}
const settle=async ui=>{for(let index=0;index<8;index++)await Promise.resolve();ui.refresh();};

test('saved providers use exact-endpoint concise rows and service reuse is available only inside the editor',async()=>{
 const calls=[],second={...profile,id:'b'.repeat(32),name:'Second model',model:'another-model'},third={...profile,id:'c'.repeat(32),name:'Third model',base_url:'https://other.example.invalid/v1'};
 const ui=component(props(async(path,token,body)=>{calls.push({path,body:structuredClone(body)});return directory;},{initialProfiles:catalog({profiles:[profile,second,third]})}));
 const list=ui.find(node=>node.type==='ul'&&node.props['aria-label']==='Saved models')[0];
 assert.equal(nodes(list,node=>node.type==='li').length,2);assert.equal(nodes(list,node=>node.type==='ul').length,1);
 assert.equal(ui.find(node=>node.type==='strong'&&text(node)==='Model service').length,0);assert.equal(calls.length,0);assert.equal(ui.input('API Key'),undefined);
 assert.doesNotMatch(ui.text(),/existing-api-id|Context 1000000|Key saved/);
 assert.equal(ui.find(node=>node.type==='button'&&text(node)==='Edit').length,2);assert.equal(ui.find(node=>node.type==='button'&&text(node)==='Delete').length,2);
 open(ui);ui.fill('Use a saved service',profile.id);await settle(ui);
 assert.deepEqual(calls,[{path:'/api/provider-models',body:{profile_id:profile.id}}]);assert.ok(ui.input('Select model: model-a'));assert.equal(ui.input('API Key').props.value,'');ui.unmount();
});

test('key completion queries once per connection edit, never per character or automatic retry, and directory errors allow manual input',async()=>{
 const calls=[],ui=component(props(async(path,token,body)=>{calls.push({path,body,copy:structuredClone(body)});throw Error('synthetic-key must never render');}));
 open(ui);fillConnection(ui);assert.equal(calls.length,0);
 await ui.input('API Key').props.onBlur();await settle(ui);await ui.input('API Key').props.onBlur();await ui.input('Base URL').props.onBlur();await settle(ui);assert.equal(calls.length,1);
 assert.deepEqual(calls[0].copy,{base_url:profile.base_url,api_key:'synthetic-key'});assert.equal(calls[0].body.api_key,'');assert.match(ui.text(),/may not support \/models/);assert.doesNotMatch(ui.text(),/synthetic-key/);assert.ok(ui.input('Model ID'));
 await ui.button('Fetch available models').props.onClick();await settle(ui);assert.equal(calls.length,2);
 ui.fill('API Key','replacement-key');await ui.input('API Key').props.onBlur();await settle(ui);assert.equal(calls.length,3);ui.unmount();
});

test('late directory responses are ignored after URL/key edits, editor replacement, and Back; keys are cleared on return',async()=>{
 const pending=[],ui=component(props((path,token,body)=>new Promise(resolve=>pending.push({resolve,body}))));open(ui);fillConnection(ui);
 ui.input('API Key').props.onBlur();ui.fill('Base URL','https://changed.example.invalid/v1');assert.equal(ui.input('API Key').props.value,'');ui.fill('API Key','first-replacement-key');pending[0].resolve(directory);await settle(ui);assert.equal(ui.input('Select model: model-a'),undefined);
 ui.input('API Key').props.onBlur();ui.fill('API Key','second-key');pending[1].resolve(directory);await settle(ui);assert.equal(ui.input('Select model: model-a'),undefined);
 ui.input('API Key').props.onBlur();ui.button('← Back to models').props.onClick();ui.refresh();open(ui);pending[2].resolve(directory);await settle(ui);
 assert.equal(ui.input('API Key').props.value,'');assert.equal(ui.input('Select model: model-a'),undefined);assert.equal(ui.text().includes('Found 3 models'),false);ui.unmount();
});

test('directory multi-selection keeps model parameters independent and saves sequentially with fresh revisions and explicit key reuse',async()=>{
 const calls=[],changes=[];let state=catalog();
 const ui=component(props(async(path,token,body)=>{
  calls.push({path,body,copy:structuredClone(body)});if(path==='/api/provider-models')return directory;
  const created={...profile,...body,id:String(calls.length).repeat(32),key_set:true,api_key:'malformed-response-key'};
  state={...state,revision:'r'+calls.length,profiles:[...state.profiles,created],created_profile_id:created.id,api_key:'malformed-root-key'};return state;
 },{onProfilesChange:value=>changes.push(value)}));
 open(ui);fillConnection(ui);await ui.button('Fetch available models').props.onClick();await settle(ui);ui.choose('model-a');ui.choose('model-b');
 assert.equal(ui.input('Context window · 1').props.value,'1500000');assert.equal(ui.input('Maximum output · 2').props.value,'512000');
 ui.fill('Configuration name · 1','My model with spaces');ui.fill('Context window · 1','1.5M');ui.fill('Maximum output · 1','256K');
 ui.fill('Thinking parameter format · 1','deepseek');ui.fill('Default thinking level for new sessions · 1','medium');
 ui.fill('Context window · 2','2M');ui.fill('Maximum output · 2','512K');ui.fill('Default thinking level for new sessions · 2','low');
 await ui.form().props.onSubmit(event);ui.refresh();const writes=calls.filter(call=>call.path==='/api/model-profiles');
 assert.equal(writes.length,2);assert.equal(writes[0].copy.revision,'r1');assert.equal(writes[1].copy.revision,'r2');assert.equal(writes[0].copy.context_window_tokens,1500000);assert.equal(writes[0].copy.max_output_tokens,256000);assert.equal(writes[1].copy.max_output_tokens,512000);
 assert.equal(writes[0].copy.name,'My model with spaces');assert.equal(writes[0].copy.model,'model-a');assert.equal(writes[0].copy.reasoning_effort,'medium');assert.equal(writes[0].copy.reasoning_levels.find(row=>row.level==='medium').api_value,'high');assert.equal(writes[1].copy.reasoning_effort,'low');
 assert.equal(writes[1].copy.source_profile_id,state.profiles[1].id);assert.equal(writes[1].copy.api_key,'');assert.equal(writes[0].body.api_key,'');assert.equal(state.default_profile_id,profile.id);assert.equal(ui.input('API Key'),undefined);
 assert.doesNotMatch(JSON.stringify(changes),/malformed-response-key|malformed-root-key|synthetic-key/);assert.match(ui.text(),/Saved 2 new or changed models/);ui.unmount();
});

test('an unknown result stops a batch immediately and retains saved items plus unsaved drafts behind explicit reconciliation',async()=>{
 const calls=[];let state=catalog(),writes=0,readFails=true;
 const ui=component(props(async(path,token,body)=>{
  calls.push({path,body,copy:body&&structuredClone(body)});if(path==='/api/provider-models')return directory;
  if(!body){if(readFails)throw Error('offline');return state;}
  writes++;if(writes===2){const error=Error('synthetic-key');error.uncertain=true;throw error;}
  const created={...profile,...body,id:'d'.repeat(32),key_set:true};state={...state,revision:'r2',profiles:[profile,created],created_profile_id:created.id};return state;
 }));
 open(ui);fillConnection(ui);await ui.button('Fetch available models').props.onClick();await settle(ui);for(const id of ['model-a','model-b','model-c'])ui.choose(id);
 const submit=ui.form().props.onSubmit;await submit(event);ui.refresh();assert.equal(writes,2);assert.match(ui.text(),/save result is unknown/i);assert.match(ui.text(),/Models saved in this batch/);assert.equal(ui.input('Model ID · 1').props.value,'model-b');assert.equal(ui.input('Model ID · 2').props.value,'model-c');assert.equal(ui.input('API Key').props.value,'');
 await submit(event);assert.equal(writes,2);assert.equal(ui.button('Checked; keep this as an unsaved draft').props.disabled,true);
 await ui.button('Reload models to check the save result').props.onClick();ui.refresh();await submit(event);assert.equal(writes,2);assert.equal(ui.button('Checked; keep this as an unsaved draft').props.disabled,true);
 readFails=false;await ui.button('Reload models to check the save result').props.onClick();ui.refresh();assert.equal(ui.button('Checked; keep this as an unsaved draft').props.disabled,false);await submit(event);ui.refresh();assert.equal(writes,2);
 ui.button('Checked; keep this as an unsaved draft').props.onClick();ui.refresh();assert.equal(writes,2);assert.equal(ui.input('Model ID · 2').props.value,'model-c');assert.doesNotMatch(ui.text(),/synthetic-key/);ui.unmount();
});

test('manual IDs reject whitespace, display names allow spaces, and output larger than 128K is accepted without inference',async()=>{
 const calls=[],ui=component(props(async(path,token,body)=>{calls.push(structuredClone(body));return catalog({revision:'r2'});}));open(ui);fillConnection(ui);
 ui.fill('Configuration name','Friendly display name');ui.fill('Model ID','bad model');ui.fill('Context window','2M');ui.fill('Maximum output','1M');
 await ui.form().props.onSubmit(event);ui.refresh();assert.equal(calls.length,0);assert.match(ui.text(),/without whitespace/);ui.fill('Model ID','provider/model-id');await ui.form().props.onSubmit(event);ui.refresh();
 assert.equal(calls.length,1);assert.equal(calls[0].name,'Friendly display name');assert.equal(calls[0].max_output_tokens,1000000);assert.equal(calls[0].context_window_tokens,2000000);ui.unmount();
});

test('reasoning level rows support deletion, restoration, custom API values and batched independent edits',async()=>{
 const calls=[],ui=component(props(async(path,token,body)=>{calls.push(structuredClone(body));return catalog({revision:'r2'});}));open(ui);fillConnection(ui);ui.fill('Model ID','reasoning-model');ui.fill('Thinking parameter format','deepseek');
 assert.equal(ui.input('API value · minimal').props.value,'low');assert.equal(ui.input('API value · xhigh').props.value,'high');assert.equal(ui.input('API value · off').props.disabled,true);
 ui.fill('Default thinking level for new sessions','medium');ui.button('Remove level · medium').props.onClick();ui.refresh();assert.equal(ui.input('Default thinking level for new sessions').props.value,'off');assert.equal(ui.input('API value · medium'),undefined);
 ui.button('Restore level · medium').props.onClick();ui.refresh();assert.equal(ui.input('API value · medium').props.value,'high');
 const low=ui.input('API value · low'),high=ui.input('API value · high');low.props.onChange({target:{value:'custom_low'}});high.props.onChange({target:{value:''}});ui.refresh();assert.equal(ui.input('API value · low').props.value,'custom_low');assert.equal(ui.input('API value · high').props.value,'');
 ui.fill('Default thinking level for new sessions','medium');await ui.form().props.onSubmit(event);ui.refresh();assert.equal(calls[0].reasoning_levels.length,7);assert.equal(calls[0].reasoning_levels.find(row=>row.level==='low').api_value,'custom_low');assert.equal(calls[0].reasoning_levels.find(row=>row.level==='high').api_value,null);assert.equal(calls[0].reasoning_effort,'medium');ui.unmount();
});

test('stored credentials are reused only for the exact source URL, and legacy profiles do not gain reasoning parameters implicitly',async()=>{
 const calls=[],ui=component(props(async(path,token,body)=>{calls.push(structuredClone(body));return catalog({revision:'r2'});}));ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();
 assert.equal(ui.input('Thinking parameter format').props.value,'inherit');ui.fill('Model ID','updated-api-id');await ui.form().props.onSubmit(event);ui.refresh();assert.equal(calls[0].source_profile_id,profile.id);assert.equal(calls[0].api_key,'');assert.equal(calls[0].reasoning_protocol,undefined);
 open(ui);fillConnection(ui);ui.fill('Model ID','new-model');ui.fill('Base URL','https://different.example.invalid/v1');ui.fill('API Key','');await ui.form().props.onSubmit(event);ui.refresh();assert.equal(calls.length,1);assert.match(ui.text(),/changing the URL requires a new key/);ui.unmount();
});

test('secret controls are never registered as assistant drafts and no browser storage is used',()=>{
 const previous={localStorage:globalThis.localStorage,sessionStorage:globalThis.sessionStorage};const forbidden={setItem(){throw Error('storage write forbidden');},getItem(){throw Error('storage read forbidden');}};Object.assign(globalThis,{localStorage:forbidden,sessionStorage:forbidden});
 try{const ui=component(props(async()=>{throw Error('unexpected request');}));open(ui);fillConnection(ui);assert.equal(ui.input('API Key').props['data-form-assist-draft'],undefined);assert.equal(ui.input('API Key').props.type,'password');assert.equal(ui.input('Context window').props['data-form-assist-draft'],'true');ui.button('← Back to models').props.onClick();ui.refresh();open(ui);assert.equal(ui.input('API Key').props.value,'');ui.unmount();}finally{Object.assign(globalThis,previous);}
});

test('official DeepSeek HTTPS directory to new model uses DeepSeek off and canonical mappings, preserving manual overrides',async()=>{
 const calls=[],ui=component(props(async(path,token,body)=>{calls.push({path,copy:structuredClone(body)});return path==='/api/provider-models'?{models:[{id:'deepseek-flash',name:'DeepSeek Flash'}]}:catalog({revision:'r2'});}));open(ui);
 ui.fill('Base URL','https://api.deepseek.com:443/v1');ui.fill('API Key','synthetic-deepseek-key');assert.equal(ui.input('Thinking parameter format').props.value,'deepseek');
 await ui.button('Fetch available models').props.onClick();await settle(ui);ui.choose('deepseek-flash');assert.equal(ui.input('Thinking parameter format').props.value,'deepseek');assert.equal(ui.input('Default thinking level for new sessions').props.value,'off');assert.equal(ui.input('API value · off').props.value,'');
 await ui.form().props.onSubmit(event);ui.refresh();const saved=calls.find(call=>call.path==='/api/model-profiles').copy;assert.equal(saved.reasoning_protocol,'deepseek');assert.equal(saved.reasoning_effort,'off');assert.deepEqual(saved.reasoning_levels.find(row=>row.level==='off'),{level:'off',api_value:null});assert.equal(saved.reasoning_levels.find(row=>row.level==='minimal').api_value,'low');assert.equal(saved.reasoning_levels.find(row=>row.level==='xhigh').api_value,'high');
 open(ui);ui.fill('Thinking parameter format','openai');ui.fill('Base URL','https://api.deepseek.com/');assert.equal(ui.input('Thinking parameter format').props.value,'openai');ui.fill('API Key','replacement-key');await ui.button('Fetch available models').props.onClick();await settle(ui);ui.choose('deepseek-flash');assert.equal(ui.input('Thinking parameter format').props.value,'openai');ui.unmount();
});

test('lookalike hosts, non-HTTPS and non-default ports do not get official DeepSeek defaults',()=>{
 const ui=component(props(async()=>{throw Error('unexpected request');}));open(ui);
 for(const url of ['https://api.deepseek.com.evil.invalid/v1','http://api.deepseek.com/v1','https://api.deepseek.com:444/v1','https://proxy.invalid/deepseek']){ui.fill('Base URL',url);assert.equal(ui.input('Thinking parameter format').props.value,'openai');}
 ui.unmount();
});


test('directory selection expands fields in that same row, with no duplicate generic form and retained collapsed drafts',async()=>{
 const ui=component(props(async()=>directory));open(ui);fillConnection(ui);await ui.button('Fetch available models').props.onClick();await settle(ui);
 let list=ui.find(node=>node.type==='ul'&&node.props.className==='model-directory-list')[0];
 assert.equal(nodes(list,node=>node.type==='li').length,3);assert.equal(ui.find(node=>node.type==='input'&&node.props['aria-label']==='Model ID').length,0);
 ui.choose('model-a');ui.choose('model-b');ui.fill('Configuration name · 1','Custom A');
 list=ui.find(node=>node.type==='ul'&&node.props.className==='model-directory-list')[0];const rows=nodes(list,node=>node.type==='li');
 assert.equal(rows.length,3);assert.equal(nodes(rows[0],node=>node.type==='input'&&node.props['aria-label']==='Model ID · 1')[0].props.value,'model-a');
 assert.equal(nodes(rows[1],node=>node.type==='input'&&node.props['aria-label']==='Model ID · 2')[0].props.value,'model-b');
 assert.equal(nodes(rows[2],node=>node.type==='input').length,1);
 assert.equal(nodes(rows[0],node=>node.type==='input'&&String(node.props['aria-label']).startsWith('API value')).length,7);
 ui.button('Expand model settings: model-a').props.onClick();ui.refresh();assert.equal(ui.input('Model ID · 1'),undefined);assert.equal(ui.button('Expand model settings: model-a').props['aria-expanded'],false);
 ui.button('Expand model settings: model-a').props.onClick();ui.refresh();assert.equal(ui.input('Configuration name · 1').props.value,'Custom A');
 ui.button('Add an unlisted model manually').props.onClick();ui.refresh();assert.ok(ui.input('Model ID · 3'));ui.fill('Model ID · 3','exact-model-id');
 list=ui.find(node=>node.type==='ul'&&node.props.className==='model-directory-list')[0];assert.equal(nodes(list,node=>node.type==='li').length,4);assert.equal(ui.find(node=>node.type==='input'&&String(node.props['aria-label']).startsWith('Model ID')).length,3);ui.unmount();
});

test('delete requires confirmation, writes archive with the current revision, and restore uses its returned revision',async()=>{
 const calls=[],changes=[];const second={...profile,id:'b'.repeat(32),name:'Remaining model',model:'remaining-model'};let state=catalog({profiles:[profile,second]});
 const ui=component(props(async(path,token,body)=>{calls.push({path,body:structuredClone(body)});state=body.action==='archive'?{...state,revision:'r2',default_profile_id:second.id,profiles:[{...profile,archived:true,selectable:false},second]}:{...state,revision:'r3',profiles:[{...profile,archived:false},second]};return state;},{initialProfiles:state,onProfilesChange:value=>changes.push(value)}));
 ui.button('Delete: '+profile.base_url).props.onClick();ui.refresh();ui.button('Delete model: '+profile.name).props.onClick();ui.refresh();assert.match(ui.text(),/existing records, files, and budgets are retained/);assert.equal(calls.length,0);
 ui.button('Cancel').props.onClick();ui.refresh();assert.equal(calls.length,0);assert.equal(ui.button('Confirm delete'),undefined);
 ui.button('Delete model: '+profile.name).props.onClick();ui.refresh();await ui.button('Confirm delete').props.onClick();ui.refresh();
 assert.deepEqual(calls,[{path:'/api/model-profiles',body:{revision:'r1',action:'archive',profile_id:profile.id}}]);
 assert.equal(ui.button('Delete model: '+profile.name),undefined);assert.equal(changes[0].profiles[0].archived,true);assert.equal(changes[0].default_profile_id,second.id);assert.equal(changes[0].profiles.length,2);
 ui.button('← Back to models').props.onClick();ui.refresh();ui.button('Deleted models (1)').props.onClick();ui.refresh();await ui.button('Restore: '+profile.name).props.onClick();ui.refresh();
 assert.deepEqual(calls[1],{path:'/api/model-profiles',body:{revision:'r2',action:'restore',profile_id:profile.id}});ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();assert.ok(ui.button('Delete model: '+profile.name));assert.equal(changes[1].profiles[0].id,profile.id);ui.unmount();
});

test('unknown deletion does not remove locally or replay and reconciliation requires a new confirmation',async()=>{
 let writes=0,reads=0,readFails=true;const ui=component(props(async(path,token,body)=>{
  if(body){writes++;const error=Error('private provider error');error.uncertain=true;throw error;}
  reads++;if(readFails)throw Error('offline');return catalog({revision:'r2'});
 }));
 ui.button('Delete: '+profile.base_url).props.onClick();ui.refresh();ui.button('Delete model: '+profile.name).props.onClick();ui.refresh();const confirm=ui.button('Confirm delete').props.onClick;
 await confirm();ui.refresh();assert.equal(writes,1);assert.ok(ui.button('Delete model: '+profile.name));assert.equal(ui.button('Confirm delete').props.disabled,true);assert.doesNotMatch(ui.text(),/private provider error|Removed from new choices/);
 await confirm();ui.refresh();assert.equal(writes,1);await ui.button('Reload models to check the save result').props.onClick();ui.refresh();await confirm();assert.equal(writes,1);
 readFails=false;await ui.button('Reload models to check the save result').props.onClick();ui.refresh();assert.equal(reads,2);assert.equal(ui.button('Confirm delete'),undefined);await confirm();assert.equal(writes,1);assert.equal(ui.button('Delete model: '+profile.name).props.disabled,false);ui.unmount();
});

test('a rejected archive preserves the saved list and never renders provider errors as success',async()=>{
 let writes=0;const ui=component(props(async()=>{writes++;throw Error('synthetic-key-do-not-render');}));
 ui.button('Delete: '+profile.base_url).props.onClick();ui.refresh();ui.button('Delete model: '+profile.name).props.onClick();ui.refresh();await ui.button('Confirm delete').props.onClick();ui.refresh();
 assert.equal(writes,1);assert.ok(ui.button('Delete model: '+profile.name));assert.match(ui.text(),/Could not update models/);assert.doesNotMatch(ui.text(),/synthetic-key-do-not-render|Removed from new choices/);ui.unmount();
});

test('archiving at retained-version capacity does not enable creation but restoration remains available',async()=>{
 const calls=[],archived={...profile,id:'b'.repeat(32),name:'Retained history',archived:true,selectable:false};
 const ui=component(props(async(path,token,body)=>{calls.push({path,body:structuredClone(body)});return catalog({revision:'r2',max_profiles:2,profiles:[profile,{...archived,archived:false,selectable:true}]});},{initialProfiles:catalog({max_profiles:2,profiles:[profile,archived]})}));
 assert.equal(ui.button('＋ Add model provider').props.disabled,true);assert.match(ui.text(),/Retained historical versions also count toward capacity; deleting does not erase old records/);
 ui.button('Deleted models (1)').props.onClick();ui.refresh();assert.equal(ui.button('Restore: '+archived.name).props.disabled,false);await ui.button('Restore: '+archived.name).props.onClick();ui.refresh();
 assert.deepEqual(calls,[{path:'/api/model-profiles',body:{revision:'r1',action:'restore',profile_id:archived.id}}]);assert.equal(ui.button('＋ Add model provider').props.disabled,true);ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();assert.ok(ui.button('Delete model: '+archived.name));ui.unmount();
});

test('editing then fetching the same service merges its API ID into one selected directory row without replacing draft fields',async()=>{
 const calls=[],ui=component(props(async(path,token,body)=>{calls.push({path,body:structuredClone(body)});return {models:[{id:profile.model,name:'Directory display name',context_window_tokens:2000000,max_output_tokens:500000},{id:'other-model',name:'Other model'}]};}));
 ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();ui.fill('Configuration name','My edited display name');ui.fill('Context window','1.5M');ui.fill('Thinking parameter format','deepseek');ui.fill('API value · medium','custom-medium');
 await ui.button('Fetch available models').props.onClick();await settle(ui);
 const list=ui.find(node=>node.type==='ul'&&node.props.className==='model-directory-list')[0],rows=nodes(list,node=>node.type==='li');
 assert.equal(rows.length,2);assert.equal(ui.input('Select model: '+profile.model).props.checked,true);assert.equal(ui.find(node=>node.type==='input'&&node.props['aria-label']==='Model ID').length,1);
 assert.equal(nodes(rows[0],node=>node.type==='input'&&node.props['aria-label']==='Model ID')[0].props.value,profile.model);assert.equal(ui.input('Configuration name').props.value,'My edited display name');assert.equal(ui.input('Context window').props.value,'1.5M');assert.equal(ui.input('API value · medium').props.value,'custom-medium');
 assert.deepEqual(calls,[{path:'/api/provider-models',body:{profile_id:profile.id}}]);ui.unmount();
});

test('editing across a URL or key change ignores stale directories and never merges the original draft into another service with the same ID',async()=>{
 const pending=[],ui=component(props((path,token,body)=>new Promise(resolve=>pending.push({resolve,body:structuredClone(body)}))));
 const sameId={models:[{id:profile.model,name:'Same ID at another service'}]};
 ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();ui.input('API Key').props.onBlur();
 ui.fill('Base URL','https://different-service.invalid/v1');ui.fill('API Key','replacement-service-key');pending[0].resolve(sameId);await settle(ui);assert.equal(ui.input('Select model: '+profile.model),undefined);assert.equal(ui.input('Model ID').props.value,profile.model);
 ui.input('API Key').props.onBlur();ui.fill('API Key','newer-service-key');pending[1].resolve(sameId);await settle(ui);assert.equal(ui.input('Select model: '+profile.model),undefined);
 ui.input('API Key').props.onBlur();pending[2].resolve(sameId);await settle(ui);assert.equal(ui.input('Select model: '+profile.model).props.checked,false);
 const list=ui.find(node=>node.type==='ul'&&node.props.className==='model-directory-list')[0];assert.equal(nodes(list,node=>node.type==='li').length,2);assert.equal(ui.input('Model ID').props.value,profile.model);assert.equal(ui.input('API Key').props.value,'newer-service-key');ui.unmount();
});

const savedModel=(id,model,patch={})=>({...profile,id:id.repeat(32),model,name:model+' display',reasoning_explicit:true,reasoning_protocol:'openai',reasoning_effort:'off',reasoning_levels:['off','minimal','low','medium','high','xhigh','max'].map(level=>({level,api_value:level==='off'?'none':level})),...patch});

test('saving four provider choices restores all checks on remount from persisted catalog without a directory request',async()=>{
 const old=savedModel('b','old-huoshan',{name:'火山方舟'}),legacy=savedModel('c','deepseek-default',{base_url:'https://other-provider.invalid/v1'}),calls=[];
 let state=catalog({default_profile_id:legacy.id,profiles:[legacy,old]});
 const offered=['kimi-k3','deepseek-economy','deepseek-flash','glm-flash'].map(id=>({id,name:id+' friendly',context_window_tokens:2000000,max_output_tokens:256000}));
 const request=async(path,token,body)=>{calls.push({path,copy:body&&structuredClone(body)});if(path==='/api/provider-models')return {models:offered};
  const created=savedModel(String(calls.length),body.model,{...body,id:String(calls.length).repeat(32),key_set:true,api_key:'never-render-key'});
  state={...state,revision:'r'+calls.length,profiles:[...state.profiles,created],created_profile_id:created.id};return state;};
 const ui=component(props(request,{initialProfiles:state}));ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();
 assert.equal(ui.input('Select model: old-huoshan').props.checked,true);await ui.button('Fetch available models').props.onClick();await settle(ui);for(const model of offered)ui.choose(model.id);
 await ui.form().props.onSubmit(event);ui.refresh();assert.equal(calls.filter(row=>row.path==='/api/model-profiles').length,4);assert.equal(state.default_profile_id,legacy.id);assert.equal(state.profiles[1].id,old.id);assert.match(ui.text(),/current session keeps its original model/);ui.unmount();
 const writes=calls.length,remount=component(props(request,{initialProfiles:state}));remount.button('Edit and save a new version: '+profile.base_url).props.onClick();remount.refresh();
 assert.equal(calls.length,writes);assert.equal(remount.find(node=>node.type==='input'&&node.props.type==='checkbox').length,5);for(const model of offered)assert.equal(remount.input('Select model: '+model.id).props.checked,true);assert.equal(remount.input('Select model: old-huoshan').props.checked,true);assert.match(remount.text(),/5 selected/);assert.doesNotMatch(remount.text(),/never-render-key/);
 await remount.form().props.onSubmit(event);remount.refresh();assert.equal(calls.length,writes);assert.match(remount.text(),/No new version was created/);remount.unmount();
});

test('unchanged saved models including implicit limits need zero writes and changing one model uses exactly its own source ID',async()=>{
 const first=savedModel('b','model-first',{name:'Provider title',limits_explicit:false,context_window_tokens:32768,max_output_tokens:128000,reasoning_explicit:false,reasoning_protocol:null,reasoning_levels:null}),second=savedModel('c','model-second'),calls=[];
 let state=catalog({default_profile_id:first.id,profiles:[first,second]});
 const ui=component(props(async(path,token,body)=>{calls.push({path,copy:structuredClone(body)});const created={...second,...body,id:'d'.repeat(32),api_key:'response-secret'};state={...state,revision:'r2',profiles:[...state.profiles,created],created_profile_id:created.id};return state;},{initialProfiles:state}));
 ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();await ui.form().props.onSubmit(event);ui.refresh();assert.equal(calls.length,0);
 ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();ui.button('Expand model settings: model-second').props.onClick();ui.refresh();ui.fill('Configuration name · 2','Edited second');
 await ui.form().props.onSubmit(event);ui.refresh();assert.equal(calls.length,1);assert.equal(calls[0].copy.action,'create');assert.equal(calls[0].copy.source_profile_id,second.id);assert.equal(calls[0].copy.api_key,'');assert.equal(calls[0].copy.model,second.model);assert.equal(state.default_profile_id,first.id);assert.equal(state.profiles[0].context_window_tokens,32768);assert.equal(state.profiles[0].max_output_tokens,128000);
 ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();assert.equal(ui.find(node=>node.type==='input'&&node.props['aria-label']==='Select model: model-second').length,1);assert.ok(ui.button('Delete model: Edited second'));assert.ok(ui.button('Delete model: model-second display'));assert.match(ui.text(),/Retained versions \(1\)/);ui.unmount();
});

test('multiple changed saved models at one endpoint keep each exact credential source despite sequential version saves',async()=>{
 const first=savedModel('b','model-first',{name:'Multi-key provider'}),second=savedModel('c','model-second'),calls=[];let state=catalog({profiles:[first,second]});
 const ui=component(props(async(path,token,body)=>{calls.push(structuredClone(body));const created={...profile,...body,id:String(calls.length+3).repeat(32),key_set:true};state={...state,revision:'r'+(calls.length+1),profiles:[...state.profiles,created],created_profile_id:created.id};return state;},{initialProfiles:state}));
 ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();for(const id of [first.model,second.model]){ui.button('Expand model settings: '+id).props.onClick();ui.refresh();}
 ui.fill('Configuration name · 1','First changed');ui.fill('Configuration name · 2','Second changed');await ui.form().props.onSubmit(event);ui.refresh();
 assert.equal(calls.length,2);assert.deepEqual(calls.map(body=>body.source_profile_id),[first.id,second.id]);assert.deepEqual(calls.map(body=>body.api_key),['','']);assert.deepEqual(calls.map(body=>body.revision),['r1','r2']);assert.ok(calls.every(body=>body.action==='create'));ui.unmount();
});

test('persisted choices stay checked without directory metadata, merge once, and explicitly unchecked models are disabled only on Save',async()=>{
 const first=savedModel('b','model-first',{name:'Saved provider'}),second=savedModel('c','model-second',{context_window_tokens:undefined,max_output_tokens:undefined,limits_explicit:false}),calls=[];let state=catalog({profiles:[first,second],default_profile_id:first.id});
 const request=async(path,token,body)=>{calls.push({path,copy:body&&structuredClone(body)});if(path==='/api/provider-models')return {models:[{id:first.model},{id:second.model},{id:'new-model'}]};state={...state,revision:'r2',default_profile_id:second.id,profiles:[{...first,archived:true,selectable:false},second]};return state;};
 const ui=component(props(request,{initialProfiles:state}));ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();assert.equal(calls.length,0);assert.equal(ui.input('Select model: '+first.model).props.checked,true);assert.equal(ui.input('Select model: '+second.model).props.checked,true);
 await ui.button('Fetch available models').props.onClick();await settle(ui);for(const model of [first.model,second.model]){assert.equal(ui.find(node=>node.type==='input'&&node.props['aria-label']==='Select model: '+model).length,1);assert.equal(ui.input('Select model: '+model).props.checked,true);}
 ui.choose(first.model,false);assert.match(ui.text(),/models to disable on Save/);assert.match(ui.text(),/all saved versions/);assert.equal(calls.length,1);await ui.form().props.onSubmit(event);ui.refresh();assert.equal(calls.length,2);assert.deepEqual(calls[1].copy,{revision:'r1',action:'archive',profile_id:first.id});ui.unmount();
 const remount=component(props(request,{initialProfiles:state}));remount.button('Edit and save a new version: '+profile.base_url).props.onClick();remount.refresh();assert.equal(remount.input('Select model: '+first.model),undefined);assert.equal(remount.input('Select model: '+second.model).props.checked,true);remount.unmount();
});

test('an unknown saved-model edit stops the batch without retrying unchanged models after explicit reconciliation',async()=>{
 const first=savedModel('b','model-first',{name:'Unknown provider'}),second=savedModel('c','model-second'),calls=[];const state=catalog({profiles:[first,second],default_profile_id:first.id});
 const ui=component(props(async(path,token,body)=>{calls.push({path,copy:body&&structuredClone(body)});if(body){const error=Error('private-key');error.uncertain=true;throw error;}return {...state,revision:'r2'};},{initialProfiles:state}));
 ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();ui.button('Expand model settings: '+second.model).props.onClick();ui.refresh();ui.fill('Configuration name · 2','Changed second');const submit=ui.form().props.onSubmit;
 await submit(event);ui.refresh();assert.equal(calls.length,1);assert.equal(calls[0].copy.source_profile_id,second.id);await submit(event);assert.equal(calls.length,1);
 await ui.button('Reload models to check the save result').props.onClick();ui.refresh();await submit(event);ui.refresh();assert.equal(calls.length,2);assert.equal(ui.input('Configuration name · 2').props.value,'Changed second');assert.equal(ui.input('Select model: '+first.model).props.checked,true);assert.equal(ui.button('Checked; keep this as an unsaved draft').props.disabled,false);assert.doesNotMatch(ui.text(),/private-key/);ui.unmount();
});

test('explicit disable archives every active version after creation and uses fresh revisions without reviving old model IDs',async()=>{
 const old=savedModel('b','retire-me',{name:'Old provider record'}),latest=savedModel('c','retire-me',{name:'Latest to retire'}),remaining=savedModel('d','keep-me'),calls=[];
 let state=catalog({default_profile_id:remaining.id,profiles:[old,latest,remaining]});
 const ui=component(props(async(path,token,body)=>{calls.push({path,copy:body&&structuredClone(body)});if(path==='/api/provider-models')return {models:[{id:'brand-new',name:'Brand new',context_window_tokens:1000000,max_output_tokens:128000}]};
  if(body.action==='create'){const created={...savedModel('e',body.model),...body,id:'e'.repeat(32)};state={...state,revision:'r2',profiles:[...state.profiles,created],created_profile_id:created.id};}
  else state={...state,revision:'r'+(calls.length),profiles:state.profiles.map(profile=>profile.id===body.profile_id?{...profile,archived:true,selectable:false}:profile)};return state;
 },{initialProfiles:state}));
 ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();ui.choose('retire-me',false);assert.match(ui.text(),/2 versions pending removal/);await ui.button('Fetch available models').props.onClick();await settle(ui);ui.choose('brand-new');await ui.form().props.onSubmit(event);ui.refresh();
 const writes=calls.filter(call=>call.path==='/api/model-profiles');assert.deepEqual(writes.map(call=>call.copy.action),['create','archive','archive']);assert.equal(writes[0].copy.source_profile_id,old.id);assert.deepEqual(writes.slice(1).map(call=>call.copy.profile_id),[old.id,latest.id]);assert.deepEqual(writes.map(call=>call.copy.revision),['r1','r2','r3']);assert.equal(state.default_profile_id,remaining.id);assert.ok(state.profiles.filter(item=>item.model==='retire-me').every(item=>item.archived));
 ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();assert.equal(ui.input('Select model: retire-me'),undefined);assert.equal(ui.input('Select model: brand-new').props.checked,true);assert.equal(ui.input('Select model: keep-me').props.checked,true);ui.unmount();
});

test('unknown removal of a model with multiple versions keeps the endpoint/model intent and never replays after read until explicit review',async()=>{
 const old=savedModel('b','retire-me'),latest=savedModel('c','retire-me',{name:'Latest'}),remaining=savedModel('d','keep-me'),calls=[];
 let state=catalog({default_profile_id:remaining.id,profiles:[old,latest,remaining]}),archives=0;
 const ui=component(props(async(path,token,body)=>{calls.push({path,copy:body&&structuredClone(body)});if(!body)return {...state,revision:'r3'};
  archives++;if(archives===2){const error=Error('private-archive-key');error.uncertain=true;throw error;}
  state={...state,revision:archives===1?'r2':'r4',profiles:state.profiles.map(profile=>profile.id===body.profile_id?{...profile,archived:true,selectable:false}:profile)};return state;
 },{initialProfiles:state}));
 ui.button('Edit and save a new version: '+profile.base_url).props.onClick();ui.refresh();ui.choose('retire-me',false);const submit=ui.form().props.onSubmit;await submit(event);ui.refresh();assert.equal(archives,2);assert.equal(ui.input('Select model: retire-me').props.checked,false);assert.equal(ui.button('Checked; keep remaining removal intent').props.disabled,true);
 await submit(event);assert.equal(archives,2);await ui.button('Reload models to check the save result').props.onClick();ui.refresh();assert.equal(ui.input('Select model: retire-me').props.checked,false);assert.equal(ui.button('Checked; keep remaining removal intent').props.disabled,false);assert.match(ui.text(),/1 versions pending removal/);await submit(event);assert.equal(archives,2);
 ui.button('Checked; keep remaining removal intent').props.onClick();ui.refresh();await ui.form().props.onSubmit(event);ui.refresh();assert.equal(archives,3);assert.deepEqual(calls.filter(call=>call.copy?.action==='archive').map(call=>[call.copy.profile_id,call.copy.revision]),[[old.id,'r1'],[latest.id,'r2'],[latest.id,'r3']]);assert.ok(state.profiles.filter(item=>item.model==='retire-me').every(item=>item.archived));assert.doesNotMatch(ui.text(),/private-archive-key/);ui.unmount();
});

test('archiving the provider credential representative chooses a remaining active directory source and keeps a stable provider label',async()=>{
 const endpoint='https://ark.cn-beijing.volces.com/api/v3',first=savedModel('b','old-huoshan',{name:'火山方舟',base_url:endpoint}),second=savedModel('c','kimi-k3',{base_url:endpoint}),calls=[];let state=catalog({profiles:[first,second],default_profile_id:second.id});
 const ui=component(props(async(path,token,body)=>{calls.push({path,copy:body&&structuredClone(body)});if(path==='/api/provider-models')return {models:[{id:'new-catalog-model',context_window_tokens:1000000,max_output_tokens:128000}]};
  if(body.action==='archive')state={...state,revision:'r2',profiles:[{...first,archived:true,selectable:false},second]};
  else {const created={...savedModel('d',body.model),...body,base_url:endpoint,id:'d'.repeat(32)};state={...state,revision:'r3',profiles:[...state.profiles,created],created_profile_id:created.id};}return state;
 },{initialProfiles:state}));
 assert.ok(ui.find(node=>node.type==='strong'&&text(node)==='Volcengine Ark').length);ui.button('Delete: '+endpoint).props.onClick();ui.refresh();ui.button('Delete model: '+first.name).props.onClick();ui.refresh();await ui.button('Confirm delete').props.onClick();ui.refresh();
 await ui.button('Fetch available models').props.onClick();await settle(ui);assert.deepEqual(calls[1].copy,{profile_id:second.id});ui.choose('new-catalog-model');await ui.form().props.onSubmit(event);ui.refresh();assert.equal(calls[2].copy.source_profile_id,second.id);assert.equal(calls[2].copy.api_key,'');assert.ok(ui.find(node=>node.type==='strong'&&text(node)==='Volcengine Ark').length);ui.unmount();
});
