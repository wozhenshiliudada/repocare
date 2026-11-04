import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {taskRows} from '../src/model.mjs';
import {ownerNavigation} from '../src/correlation-navigation.mjs';
const {WorkbenchPages}=await import('../src/WorkbenchPages.tsx');
const {IssueManager}=await import('../src/IssueManager.tsx');
function nodes(n,check){return !n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(v=>nodes(v,check)):
 [...(check(n)?[n]:[]),...nodes(n.props?.children,check)];}
// Execute actual hooks/effect updates, keeping descendant components as elements.
function mount(Component,initial){
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
 let props=initial,cursor=0,dirty=false,tree,effects=[];
 const dispatch={useState(initial){const i=cursor++;hooks[i]??={value:typeof initial==='function'?initial():initial};
  return [hooks[i].value,next=>{const value=typeof next==='function'?next(hooks[i].value):next;
   if(!Object.is(value,hooks[i].value)){hooks[i].value=value;dirty=true;}}];},
  useRef(value){const i=cursor++;return hooks[i]??={current:value};},
  useEffect(fn,deps){const i=cursor++,old=hooks[i];if(!old||deps.some((v,j)=>!Object.is(v,old.deps[j]))){
   hooks[i]={deps};effects.push(()=>{old?.cleanup?.();hooks[i].cleanup=fn();});}}};
 function render(){let rounds=0;do{assert.ok(++rounds<25);dirty=false;cursor=0;effects=[];
  const old=internal.H;internal.H=dispatch;try{tree=Component(props);}finally{internal.H=old;}
  for(const effect of effects)effect();}while(dirty);return tree;}
 render();return {tree:()=>tree,update(value){props=value;return render();}};
}
const old={id:'old-execution',investigation_id:'old-investigation',issue_number:7,
 created_at:'2026-10-28',status:'finished',run_id:'old-run'};
const latest={...old,id:'latest-execution',investigation_id:'latest-investigation',created_at:'2026-10-29',run_id:'latest-run'};
function fixture(){
 const state={issue_workbench:{executions:[old,latest],investigations:[]}},rows=taskRows(state);
 const record={id:'workspace',repository:'C:/original'};
 const nav=ownerNavigation({record,rows,link:{status:'linked',owner_source:{kind:'issue',workspace_id:record.id,
  investigation_id:old.investigation_id,execution_id:old.id}},callbacks:{selectTask(){}}});
 assert.equal(nav.available,true);
 const current=rows.find(row=>row.id===nav.action.id);
 return {page:'issues',record,state,hub:{},rows,focus:current.selection.number,
  taskFocus:{workspace_id:record.id,execution_id:current.selection.id},
  request:async()=>{throw Error('unexpected operation');},refresh:async()=>{},navigate(){},selectTask(){}};
}
function managerProps(p){const workspace=WorkbenchPages(p),ui=mount(workspace.type,workspace.props);
 return nodes(ui.tree(),n=>n.type===IssueManager)[0].props;}
function activeExecution(ui){return nodes(ui.tree(),n=>n.type?.name==='ActivityTimeline')[0]?.props.execution?.id;}
test('a real taskRows owner selection reaches the older exact IssueManager execution',()=>{
 const p=fixture(),input=managerProps(p),ui=mount(IssueManager,input);
 assert.equal(activeExecution(ui),old.id);assert.equal(input.executionFocus,old.id);
 const next=managerProps({...p,taskFocus:{workspace_id:p.record.id,execution_id:latest.id}});
 ui.update(next);assert.equal(activeExecution(ui),latest.id);
 ui.update(input);assert.equal(activeExecution(ui),old.id);
});
test('same-workspace Issue delivery focus retains precedence over the selected task',()=>{
 const p=fixture(),input=managerProps({...p,deliveryFocus:{kind:'issue',workspace_id:p.record.id,
  execution_id:latest.id,issue_number:7}});
 assert.equal(input.executionFocus,latest.id);assert.equal(activeExecution(mount(IssueManager,input)),latest.id);
});
test('foreign delivery focus cannot replace the current workspace selected execution',()=>{
 const p=fixture(),input=managerProps({...p,deliveryFocus:{kind:'issue',workspace_id:'foreign',
  execution_id:latest.id,issue_number:7}});
 assert.equal(input.executionFocus,old.id);assert.equal(activeExecution(mount(IssueManager,input)),old.id);
});
test('foreign or absent task execution identities are not forwarded to IssueManager',()=>{
 const p=fixture();
 for(const taskFocus of [{workspace_id:'foreign',execution_id:old.id},
  {workspace_id:p.record.id,execution_id:'missing-execution'}]){
  assert.equal(managerProps({...p,taskFocus}).executionFocus,undefined);
 }
});
