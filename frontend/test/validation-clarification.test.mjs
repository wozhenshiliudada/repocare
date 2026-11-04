import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {ValidationClarification:IssueValidation,IssueValidationStop}=await import('../src/ValidationClarification.tsx');
function nodes(n,check){return !n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(v=>nodes(v,check)):
 [...(check(n)?[n]:[]),...nodes(n.props?.children,check)];}
function text(n){return n==null||typeof n==='boolean'?'':typeof n!=='object'?String(n):
 Array.isArray(n)?n.map(text).join(''):text(n.props?.children);}
function component(props,Component=IssueValidation){
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
 let p=props,cursor=0,tree,dirty=false,effects=[];
 const dispatch={useState(initial){const i=cursor++;hooks[i]??={value:initial};return [hooks[i].value,v=>{
  if(!Object.is(v,hooks[i].value)){hooks[i].value=v;dirty=true;}}];},
  useRef(value){const i=cursor++;return hooks[i]??=( {current:value});},
  useEffect(fn,deps){const i=cursor++,old=hooks[i];if(!old||deps.some((v,j)=>!Object.is(v,old.deps[j]))){
   hooks[i]={deps};effects.push(fn);}}};
 function render(){let rounds=0;do{assert.ok(++rounds<20);dirty=false;cursor=0;effects=[];
  const old=internal.H;internal.H=dispatch;try{tree=Component(p);}finally{internal.H=old;}
  for(const effect of effects)effect();
 }while(dirty);return tree;}
 render();return {render,update(value){p=value;render();},tree:()=>tree,text:()=>text(tree),
  button(label){return nodes(tree,n=>n.type==='button'&&text(n)===label)[0];},
  checkbox(){return nodes(tree,n=>n.type==='input'&&n.props.type==='checkbox')[0];}};
}

const parent={id:'parent',revision:'original-parent-revision',status:'clarification_needed'};
const original={investigation_id:'investigation'};
test('clarification renders a natural-language answer and separate original-budget confirmation',()=>{
 let calls=0;const html=renderToStaticMarkup(React.createElement(IssueValidation,{validation:parent,original,
 prefix:'issue_validation',disabled:false,send:async()=>calls++}));
 assert.match(html,/补充业务期望/);assert.match(html,/<textarea/);assert.match(html,/原提议与消费保留/);
 assert.equal(calls,0);assert.doesNotMatch(html,/checked=""/);
});
test('supplement preview and explicit confirmation bind exact parent and clear on text change',async()=>{
 const calls=[],send=async(action,payload)=>calls.push({action,payload});
 const props={validation:parent,original,prefix:'issue_validation',disabled:false,send};
 const ui=component(props);nodes(ui.tree(),n=>n.type==='textarea')[0].props.onChange({target:{value:'Return five'}});ui.render();
 await ui.button('预览补充后的提议与原额度').props.onClick();
 assert.deepEqual(calls[0],{action:'issue_validation_clarification_preview',payload:{...original,revision:parent.revision,answer:'Return five'}});
 const preview={preview:{parent_id:'parent',answer:'Return five',child_id:'child',phase:{budget:{requests_remaining:2}}},revision:'fresh'};
 ui.update({...props,preview});assert.equal(ui.checkbox().props.checked,false);
 assert.equal(ui.button('补充后继续提议').props.disabled,true);
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();
 await ui.button('补充后继续提议').props.onClick();ui.render();
 assert.deepEqual(calls[1],{action:'issue_validation_clarify',payload:{...original,...preview,confirmed:true}});
 assert.equal(ui.checkbox().props.checked,false);
 nodes(ui.tree(),n=>n.type==='textarea')[0].props.onChange({target:{value:'Other behavior'}});ui.render();
 assert.equal(ui.button('补充后继续提议'),undefined);
});
test('monitor uses original event identity for the same clarification flow',async()=>{
 const calls=[],origin={monitor_id:'monitor',event_id:'event'};
 const ui=component({validation:parent,original:origin,prefix:'monitor_validation',disabled:false,
 send:async(action,payload)=>calls.push({action,payload})});
 nodes(ui.tree(),n=>n.type==='textarea')[0].props.onChange({target:{value:'sum'}});ui.render();
 await ui.button('预览补充后的提议与原额度').props.onClick();
 assert.deepEqual(calls,[{action:'monitor_validation_clarification_preview',payload:{...origin,revision:parent.revision,answer:'sum'}}]);
});

test('changing the original parent invalidates previous confirmation and preview',()=>{
 const props={validation:parent,original,prefix:'issue_validation',disabled:false,send:async()=>{}};
 const ui=component(props);nodes(ui.tree(),n=>n.type==='textarea')[0].props.onChange({target:{value:'sum'}});ui.render();
 const preview={preview:{parent_id:'parent',answer:'sum'},revision:'preview'};ui.update({...props,preview});
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();assert.equal(ui.checkbox().props.checked,true);
 ui.update({...props,preview,validation:{...parent,id:'new-parent',revision:'new-version'}});
 assert.equal(ui.button('补充后继续提议'),undefined);
});
test('original task stop stays visible during a clarification call and binds exact job once',async()=>{
 const calls=[],record={id:'workspace',api_prefix:'/workspaces/original/',token:'original-token'},
 control={can_stop:true,job_id:'exact-child-job',revision:'stop-revision'};
 const ui=component({record,control,request:async(...args)=>calls.push(args),refresh:async()=>{}},IssueValidationStop);
 assert.equal(ui.button('停止当前只读案例提议').props.disabled,false);
 const click=ui.button('停止当前只读案例提议').props.onClick;await Promise.all([click(),click()]);ui.render();
 assert.equal(calls.length,1);assert.equal(calls[0][0],record.api_prefix+'actions/stop_job');
 assert.equal(calls[0][1],record.token);assert.equal(calls[0][2].job_id,control.job_id);
 assert.equal(calls[0][2].revision,control.revision);assert.equal(ui.button('停止当前只读案例提议').props.disabled,true);
});
