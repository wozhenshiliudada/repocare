import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {transformSync} from 'rolldown/utils';
import React from 'react';
import {workflows} from '../src/preview-workflows.mjs';

// Expose the original private component for this in-memory handler test only.
// Its code, hooks and operation adapter are retained; no build or server starts.
registerHooks({load(url,context,next){
 if(url.endsWith('/src/capabilities.json'))return {format:'module',source:'export default '+readFileSync(new URL(url),'utf8'),shortCircuit:true};
 if(url.endsWith('/src/WorkbenchPages.tsx')){
  const source=readFileSync(new URL(url),'utf8')+'\nexport {MemoryTools};\n';
  return {format:'module',source:transformSync(fileURLToPath(url),source,{jsx:{runtime:'classic'}}).code,shortCircuit:true};
 }
 return next(url,context);
}});
const {CapabilityDraftProvider,LocalPreview}=await import('../src/CapabilityPanel.tsx');
const {MemoryTools}=await import('../src/WorkbenchPages.tsx');

function nodes(node,predicate){return !node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(child=>nodes(child,predicate)):[...(predicate(node)?[node]:[]),...nodes(node.props?.children,predicate)];}
function text(node){return node==null||typeof node==='boolean'?'':typeof node!=='object'?String(node):Array.isArray(node)?node.map(text).join(''):text(node.props?.children);}
function component(Component,props,readContext=context=>context._currentValue){
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;let cursor=0,tree,effects=[];
 const same=(a,b)=>a&&b&&a.length===b.length&&a.every((value,index)=>Object.is(value,b[index]));
 const dispatcher={
  useState(initial){const index=cursor++;hooks[index]??={value:typeof initial==='function'?initial():initial,queue:[]};const hook=hooks[index];return [hook.value,update=>hook.queue.push(update)];},
  useRef(initial){const index=cursor++;return hooks[index]??={current:initial};},
  useContext:readContext,
  useEffect(fn,deps){const index=cursor++;if(!hooks[index]||!same(hooks[index].deps,deps)){const cleanup=hooks[index]?.cleanup;hooks[index]={deps};effects.push(()=>{cleanup?.();hooks[index].cleanup=fn();});}}
 };
 function render(){
  for(const hook of hooks)if(hook?.queue)for(const update of hook.queue.splice(0))hook.value=typeof update==='function'?update(hook.value):update;
  cursor=0;effects=[];const old=internal.H;internal.H=dispatcher;try{tree=Component(props);}finally{internal.H=old;}
  for(const effect of effects)effect();return tree;
 }
 render();return {tree:()=>tree,flush:render,find:predicate=>nodes(tree,predicate),dispose(){for(const hook of hooks)hook?.cleanup?.();}};
}
function delayedEvent(handler,value){const event={target:{value},currentTarget:{value}};handler(event);event.target.value='event-already-released';event.currentTarget.value='event-already-released';}

test('all same-render local-preview draft handlers accumulate without a preview, submit, next step or request',()=>{
 const provider=component(CapabilityDraftProvider,{children:null}),workflow=workflows.find(item=>item.id==='issue-create');assert.ok(workflow);assert.ok(workflow.fields.length>=3);
 const preview=component(LocalPreview,{repositoryKey:'original-repository',workflow},()=>provider.tree().props.value),beforeWorkflow=JSON.stringify(workflow),originalFetch=globalThis.fetch,calls=[];
 globalThis.fetch=(...args)=>{calls.push(args);throw Error('draft dispatched a network request');};
 try{
  const originalFields=preview.find(node=>['input','select','textarea'].includes(node.type)),expected={};
  for(let index=0;index<originalFields.length;index++){
   const field=workflow.fields[index],value=field.type==='select'?field.options.at(-1):field.type==='number'?'3':'batched draft '+field.key;
   expected[field.key]=value;delayedEvent(originalFields[index].props.onChange,value);
  }
  assert.equal(calls.length,0);provider.flush();preview.flush();
  assert.deepEqual(provider.tree().props.value.drafts['original-repository:'+workflow.id],expected);
  assert.deepEqual(Object.fromEntries(preview.find(node=>['input','select','textarea'].includes(node.type)).map((node,index)=>[workflow.fields[index].key,node.props.value])),expected);
  assert.equal(preview.find(node=>node.props?.['aria-label']==='本地预览结果').length,0);
  const execute=preview.find(node=>node.type==='button'&&text(node)==='提交执行（尚未接通）')[0];assert.ok(execute);assert.equal(execute.props.disabled,true);assert.equal(calls.length,0);assert.equal(JSON.stringify(workflow),beforeWorkflow);
 }finally{globalThis.fetch=originalFetch;provider.dispose();preview.dispose();}
});

test('draft-provider full replacement, clear and original snapshots retain their existing semantics',()=>{
 const provider=component(CapabilityDraftProvider,{children:null});try{
  provider.tree().props.value.setDraft('repository:workflow',{first:'original',second:'keep only until replaced'});provider.tree().props.value.setDraft('other:workflow',{other:'original'});provider.flush();
  const snapshot=provider.tree().props.value.drafts,originalFirst=snapshot['repository:workflow'];
  provider.tree().props.value.setDraft('repository:workflow',{replacement:'exact full replacement'});provider.flush();assert.deepEqual(provider.tree().props.value.drafts['repository:workflow'],{replacement:'exact full replacement'});assert.deepEqual(snapshot['repository:workflow'],{first:'original',second:'keep only until replaced'});assert.equal(snapshot['repository:workflow'],originalFirst);
  provider.tree().props.value.setDraft('repository:workflow',{});provider.flush();assert.deepEqual(provider.tree().props.value.drafts['repository:workflow'],{});assert.deepEqual(provider.tree().props.value.drafts['other:workflow'],{other:'original'});assert.deepEqual(snapshot['repository:workflow'],{first:'original',second:'keep only until replaced'});
 }finally{provider.dispose();}
});

test('four same-render context-policy draft events retain each earlier number and the original authority without POST or next step',()=>{
 const settings={schema_version:1,enabled:true,context_window_tokens:32768,trigger_tokens:16000,keep_recent_tokens:2000,summary_max_output_tokens:1024},original=JSON.stringify(settings),calls=[];
 const state={active:null,jobs:[],runtime:{code_changed:false},task_compaction:{settings,settings_revision:'original-settings-revision'}};
 const ui=component(MemoryTools,{tab:'compaction',record:{api_prefix:'/api/workspaces/original/',token:'original-capability'},state,request:async(...args)=>{calls.push(args);throw Error('draft issued a POST');},refresh:async()=>{throw Error('draft refreshed business state');}});
 try{
  const originalInputs=ui.find(node=>node.type==='input'&&node.props.type==='number');assert.equal(originalInputs.length,4);const expected=[65536,32000,4000,2048];
  for(let index=0;index<4;index++)delayedEvent(originalInputs[index].props.onChange,String(expected[index]));
  assert.equal(calls.length,0);ui.flush();
  assert.deepEqual(ui.find(node=>node.type==='input'&&node.props.type==='number').map(node=>node.props.value),expected);
  assert.equal(ui.find(node=>node.type==='input'&&node.props.type==='checkbox')[0].props.checked,true);assert.equal(JSON.stringify(settings),original);assert.equal(state.task_compaction.settings_revision,'original-settings-revision');assert.equal(calls.length,0);
  const save=ui.find(node=>node.type?.name==='AsyncButton'&&text(node)==='保存为新任务设置')[0];assert.ok(save);assert.equal(save.props.disabled,false);assert.equal(ui.find(node=>node.props?.['aria-label']==='本地预览结果').length,0);
 }finally{ui.dispose();}
});
