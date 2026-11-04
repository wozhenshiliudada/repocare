import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {setUiLanguage} from '../src/ui-language.mjs';
const {MaintenancePanel}=await import('../src/MaintenancePanel.tsx');
const {KnowledgeEditor}=await import('../src/MemoryAdvanced.tsx');

function nodes(node,predicate){return !node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(value=>nodes(value,predicate)):
 [...(predicate(node)?[node]:[]),...nodes(node.props?.children,predicate)];}

// Execute the actual component and its handlers, deferring every state update
// until an explicit render. All fields in a batch use handlers from one render.
function component(Component,props){
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
 let cursor=0,tree,effects=[],renders=0;
 const same=(left,right)=>left&&right&&left.length===right.length&&left.every((value,index)=>Object.is(value,right[index]));
 const dispatcher={
  useState(initial){const index=cursor++;hooks[index]??={value:typeof initial==='function'?initial():initial,queue:[]};
   return [hooks[index].value,update=>hooks[index].queue.push(update)];},
  useRef(value){const index=cursor++;return hooks[index]??={current:value};},
  useEffect(fn,deps){const index=cursor++;if(!hooks[index]||!same(hooks[index].deps,deps)){
   const cleanup=hooks[index]?.cleanup;hooks[index]={deps};effects.push(()=>{cleanup?.();hooks[index].cleanup=fn();});}}
 };
 function render(){let rounds=0;do{
  assert.ok(++rounds<25,'component did not settle');
  for(const hook of hooks)if(hook?.queue){const queued=hook.queue.splice(0);for(const update of queued)hook.value=typeof update==='function'?update(hook.value):update;}
  cursor=0;effects=[];const previous=internal.H;internal.H=dispatcher;
  try{tree=Component(props);renders++;}finally{internal.H=previous;}
  for(const effect of effects)effect();
 }while(hooks.some(hook=>hook?.queue?.length));return tree;}
 render();
 return {render,renders:()=>renders,fields:()=>nodes(tree,node=>node.props?.['data-form-assist-draft']==='true'),
  checkboxes:()=>nodes(tree,node=>node.type==='input'&&node.props.type==='checkbox'),
  unmount(){for(const hook of hooks)hook?.cleanup?.();}};
}
function props(state){let requests=0,refreshes=0;return {value:{record:{id:'original-workspace',api_prefix:'/original/',token:'do-not-use'},
 state:{runtime:{code_changed:false},jobs:[],...state},hub:{},rows:[],
 request:async()=>{requests++;throw Error('draft dispatched an operation');},refresh:async()=>{refreshes++;}},
 requests:()=>requests,refreshes:()=>refreshes};}
function enqueue(field,value){
 const event={target:{value}};field.props.onChange(event);
 // Deferred updater callbacks must use the value captured during the event,
 // rather than reading a later mutable event/DOM target.
 event.target.value='changed-after-the-handler';
}

test('maintenance draft fields from one render all survive a delayed commit without saving or consent',()=>{
 try{for(const language of ['zh-CN','en']){
  setUiLanguage(language);const original={status:'verified',expected_revision:'original-revision',enabled:false,observations:[]};
  const fixture=props({maintenance_memory:original}),ui=component(MaintenancePanel,fixture.value);
  try{
   const fields=ui.fields(),count=ui.renders();assert.equal(fields.length,6);
   enqueue(fields[1],'New title');enqueue(fields[2],'New detail');enqueue(fields[3],'deferred');enqueue(fields[4],'3');
   assert.equal(ui.renders(),count,'handlers unexpectedly caused an intermediate render');
   ui.render();
   assert.deepEqual(ui.fields().slice(1,5).map(field=>field.props.value),['New title','New detail','deferred',3]);
   assert.deepEqual(ui.checkboxes().map(field=>field.props.checked),[false]);
   assert.equal(fixture.requests(),0);assert.equal(fixture.refreshes(),0);
   assert.equal(fixture.value.state.maintenance_memory,original);assert.equal(original.expected_revision,'original-revision');
  }finally{ui.unmount();}
 }}finally{setUiLanguage('zh-CN');}
});

test('knowledge kind, title, summary and every list merge from one render without creating a candidate',()=>{
 try{for(const language of ['zh-CN','en']){
  setUiLanguage(language);
  const note={kind:'knowledge',title:'Original title',summary:'Original summary',triggers:['old trigger'],preconditions:['old prerequisite'],
   steps:['old step'],verification:['old check'],stop_conditions:['old stop']};
  const entry={id:'original-knowledge',revision:'original-revision',freshness:'verified',lifecycle:'active',can_refresh:true,note};
  const fixture=props({knowledge_memory:{entries:[entry]},experience_memory:{entries:[{id:'source',review:'accepted',freshness:'verified',issue:'Original source'}]}});
  const original=JSON.stringify(entry),ui=component(KnowledgeEditor,{...fixture.value,entry});
  try{
   const fields=ui.fields(),count=ui.renders();assert.equal(fields.length,8);
   const values=['procedure','New title','New summary','new trigger\nsecond trigger','new prerequisite','new step','new check','new stop'];
   fields.forEach((field,index)=>enqueue(field,values[index]));
   assert.equal(ui.renders(),count,'handlers unexpectedly caused an intermediate render');
   ui.render();assert.deepEqual(ui.fields().map(field=>field.props.value),values);
   assert.deepEqual(ui.checkboxes().map(field=>field.props.checked),[false]);
   assert.equal(fixture.requests(),0);assert.equal(fixture.refreshes(),0);assert.equal(JSON.stringify(entry),original);
  }finally{ui.unmount();}
 }}finally{setUiLanguage('zh-CN');}
});
