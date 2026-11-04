import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {MonitorValidationControls}=await import('../src/MonitorValidationControls.tsx');
const {RootMonitorPreparation}=await import('../src/RootMaintenanceControls.tsx');

function nodes(n,check){return !n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(v=>nodes(v,check)):
 [...(check(n)?[n]:[]),...nodes(n.props?.children,check)];}
function text(n){return n==null||typeof n==='boolean'?'':typeof n!=='object'?String(n):Array.isArray(n)?n.map(text).join(''):text(n.props?.children);}
// Execute the real component, hook effects and handlers. No service, browser,
// production model or Vite output build is involved.
function component(initial,Component=MonitorValidationControls){
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
 let p=initial,cursor=0,dirty=false,tree,effects=[];
 const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
 const dispatch={useState(initial){const i=cursor++;hooks[i]??={value:typeof initial==='function'?initial():initial};
  return [hooks[i].value,next=>{const value=typeof next==='function'?next(hooks[i].value):next;
   if(!Object.is(value,hooks[i].value)){hooks[i].value=value;dirty=true;}}];},
  useRef(value){const i=cursor++;return hooks[i]??={current:value};},
  useEffect(fn,deps){const i=cursor++;if(!hooks[i]||!same(hooks[i].deps,deps)){const cleanup=hooks[i]?.cleanup;
   hooks[i]={deps};effects.push(()=>{cleanup?.();hooks[i].cleanup=fn();});}}};
 function render(){let rounds=0;do{assert.ok(++rounds<25);dirty=false;cursor=0;effects=[];
  const old=internal.H;internal.H=dispatch;try{tree=Component(p);}finally{internal.H=old;}
  for(const fn of effects)fn();}while(dirty);return tree;}
 render();return {render,update(value){p=value;return render();},tree:()=>tree,text:()=>text(tree),
  button(label){return nodes(tree,n=>n.type==='button'&&text(n)===label)[0];},
  checkbox(){return nodes(tree,n=>n.type==='input'&&n.props.type==='checkbox')[0];},
  textarea(){return nodes(tree,n=>n.type==='textarea')[0];}};
}
const requestText='空购物车结算时应说明没有商品';
const event={monitor_id:'original-monitor',id:'original-event',sha:'original-sha',can_propose_validation:true,can_prepare_repair:false};
const budget={request_budget:8,requests_used:2,requests_reserved_unknown:0,requests_remaining:6};
const monitor={id:event.monitor_id,spec:{path:'original-repository'},root_rules:{sha256:'original-rules',settings:{mode:'approval'}},...budget};
const proposal={preview:{monitor_id:event.monitor_id,event_id:event.id,request_text:requestText,
 phase:{revision:'phase-revision',budget,runtime_authorized:false}},revision:'original-preview'};
const validation={id:'original-validation',status:'awaiting_validation_review',revision:'original-review',request_text:requestText,runtime_authorized:false,
 proposal:{requirement:requestText,rationale:'从原需求与代码选择',limitations:'一个有界案例'},
 contract:{cases:[{id:'empty',args:[],kwargs:{},expect:{kind:'json',value:'没有商品'},module:'cart.py',callable:'checkout',missing_behavior:true}]}};
const job=(result=proposal,patch={})=>({id:'finished-preview',action:'monitor_validation_preview',status:'finished',result,...patch});
function props(){return {p:{record:{id:'workspace',repository:'original-repository',token:'DO_NOT_SEND_WORKSPACE_TOKEN'},state:null,
 hub:{jobs:[],monitors:[structuredClone(monitor)]},request:async()=>{throw Error('unexpected request');},refresh:async()=>{}},event:structuredClone(event)};}
function ready(){const p=props();p.p.hub.jobs=[job()];const ui=component(p);ui.textarea().props.onChange({target:{value:requestText}});ui.render();return {p,ui};}
function observe(p){const calls=[];p.p.request=async(path,token,body)=>{calls.push({path,token,body});return {id:body.request_id,status:'running'};};return calls;}

test('actual SSR exposes missing or green-check acceptance entry without model dispatch or manual command inputs',()=>{
 let calls=0;const p=props();p.p.request=async()=>{calls++;throw Error('render dispatched');};
 for(const root_preparation of [undefined,{status:'passed',model_requests:0,message:'旧检查通过',environment:{checks:[]}}]){
  const html=renderToStaticMarkup(React.createElement(RootMonitorPreparation,{...p,event:{...p.event,root_preparation}}));
  assert.match(html,/监控事件验收案例/);assert.match(html,/已有测试通过但需求仍未满足/);assert.match(html,/<textarea\b[^>]*maxLength="2000"/);
  assert.match(html,/预览案例提议与原请求额度/);assert.doesNotMatch(html,/确认本次模型修复|type="text"|checked=""|DO_NOT_SEND_WORKSPACE_TOKEN/);
 }
 assert.equal(calls,0);assert.equal(renderToStaticMarkup(React.createElement(MonitorValidationControls,{...p,event:{...event,can_propose_validation:false}})),'');
});

test('real preview handler enforces natural-language bounds and original hub API identity',async()=>{
 const p=props(),calls=observe(p),ui=component(p);
 for(const value of ['', '  ', 'a'.repeat(2001)]){ui.textarea().props.onChange({target:{value}});ui.render();
  assert.equal(ui.button('预览案例提议与原请求额度').props.disabled,true);await ui.button('预览案例提议与原请求额度').props.onClick();}
 assert.equal(calls.length,0);ui.textarea().props.onChange({target:{value:requestText}});ui.render();
 const click=ui.button('预览案例提议与原请求额度').props.onClick;await Promise.all([click(),click()]);ui.render();
 assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/actions/monitor_validation_preview');assert.equal(calls[0].token,undefined);
 assert.deepEqual({...calls[0].body,request_id:undefined},{monitor_id:event.monitor_id,event_id:event.id,request_text:requestText,request_id:undefined});
 assert.match(calls[0].body.request_id,/^[a-f0-9]{32}$/);
});

test('preview is selected only from completed original monitor/event/request jobs and starts unchecked',()=>{
 const {p,ui}=ready();assert.equal(ui.checkbox().props.checked,false);assert.match(ui.text(),/可用 6/);assert.match(ui.text(),/可能产生费用/);
 for(const patch of [{status:'running'}, {action:'other_action'}, {result:{...proposal,preview:{...proposal.preview,monitor_id:'foreign'}}},
  {result:{...proposal,preview:{...proposal.preview,event_id:'foreign'}}}, {result:{...proposal,preview:{...proposal.preview,request_text:'foreign'}}}]){
  ui.update({...p,p:{...p.p,hub:{...p.p.hub,jobs:[job(proposal,patch)]}}});assert.equal(ui.checkbox(),undefined);
 }
});

test('existing original proposal in a preview cannot reopen model consent before row projection',()=>{
 const {p,ui}=ready();ui.update({...p,p:{...p.p,hub:{...p.p.hub,jobs:[job({...proposal,preview:{...proposal.preview,
  phase:{...proposal.preview.phase,existing:{status:'interrupted_no_replay'}}}})]}}});
 assert.equal(ui.checkbox(),undefined);assert.equal(ui.button('让Agent提议验收案例'),undefined);assert.match(ui.text(),/不重复调用模型/);
});

test('explicit proposal uses exact preview envelope once; confirmation clears on repository/event/request/preview changes',async()=>{
 const {p,ui}=ready(),calls=observe(p);await ui.button('让Agent提议验收案例').props.onClick();assert.equal(calls.length,0);
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();const click=ui.button('让Agent提议验收案例').props.onClick;
 await Promise.all([click(),click()]);ui.render();assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/actions/monitor_validation_propose');
 assert.equal(calls[0].token,undefined);assert.deepEqual(calls[0].body.preview,proposal.preview);assert.equal(calls[0].body.revision,proposal.revision);
 assert.equal(calls[0].body.confirmed,true);assert.equal('cases' in calls[0].body,false);
 for(const change of [
  p=>({...p,p:{...p.p,record:{...p.p.record,id:'other'}}}),
  p=>({...p,event:{...p.event,id:'other'}}),
  p=>({...p,p:{...p.p,hub:{...p.p.hub,jobs:[job({...proposal,revision:'new-preview'})]}}}),
  p=>({...p,p:{...p.p,hub:{...p.p.hub,monitors:[{...monitor,requests_used:3}]}}}),
 ]){
  const {p,ui}=ready();ui.checkbox().props.onChange({target:{checked:true}});ui.render();ui.update(change(p));assert.notEqual(ui.checkbox()?.props.checked,true);
 }
 const changed=ready();changed.ui.checkbox().props.onChange({target:{checked:true}});changed.ui.render();
 changed.ui.textarea().props.onChange({target:{value:'新的业务期望'}});changed.ui.render();assert.equal(changed.ui.checkbox(),undefined);
});

test('queued original completion reveals preview; unrelated job completion cannot clear a pending request',async()=>{
 const p=props(),calls=observe(p),ui=component(p);ui.textarea().props.onChange({target:{value:requestText}});ui.render();
 await ui.button('预览案例提议与原请求额度').props.onClick();ui.render();assert.equal(ui.checkbox(),undefined);
 ui.update({...p,p:{...p.p,hub:{...p.p.hub,jobs:[job()]}}});assert.equal(ui.button('让Agent提议验收案例').props.disabled,true);
 ui.update({...p,p:{...p.p,hub:{...p.p.hub,jobs:[job(proposal,{id:calls[0].body.request_id})]}}});
 assert.equal(ui.checkbox().props.checked,false);assert.equal(ui.button('预览案例提议与原请求额度').props.disabled,false);
});

test('unknown proposal never automatically retries and offers reconciliation only after original row projection',async()=>{
 const {p,ui}=ready(),calls=[];p.p.request=async(path,_token,body)=>{calls.push({path,body});const e=Error('Unknown response');e.uncertain=true;throw e;};
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();await ui.button('让Agent提议验收案例').props.onClick();ui.render();
 for(let i=0;i<3;i++)ui.update({...p,p:{...p.p,hub:{...p.p.hub,jobs:[job()]}}});
 assert.equal(calls.length,1);assert.equal(ui.button('停止本次只读案例提议'),undefined);assert.equal(ui.button('让Agent提议验收案例').props.disabled,true);
 const later={...p,event:{...event,validation:{...validation,status:'interrupted_no_replay'}},p:{...p.p,hub:{...p.p.hub,jobs:[{id:calls[0].body.request_id,status:'interrupted'}]}}};
 ui.update(later);assert.equal(ui.button('让Agent提议验收案例'),undefined);assert.match(ui.text(),/未知消费继续保留/);
 const seen=[];later.p.request=async(path,token,body)=>{seen.push({path,token,body});return {id:body.request_id};};
 ui.update(later);
 await ui.button('核对原会话并取回案例').props.onClick();assert.equal(seen.length,1);assert.equal(seen[0].path,'/api/actions/monitor_validation_reconcile');
 assert.equal(seen[0].body.revision,validation.revision);assert.equal(seen[0].body.event_id,event.id);
});

test('actual review displays fixed cases and keeps request immutable, with separate unchecked accept and original revision',async()=>{
 const p=props();p.event.validation=structuredClone(validation);const calls=observe(p),ui=component(p);
 const html=renderToStaticMarkup(React.createElement(MonitorValidationControls,p));assert.match(html,/输入/);assert.match(html,/预期/);assert.match(html,/没有商品/);
 assert.match(html,/尚未提供的行为/);assert.match(html,/cart.py/);assert.equal(ui.textarea().props.readOnly,true);assert.equal(ui.checkbox().props.checked,false);
 ui.textarea().props.onChange({target:{value:'tampered'}});ui.render();assert.equal(ui.textarea().props.value,requestText);
 await ui.button('确认本次验收案例').props.onClick();assert.equal(calls.length,0);
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();await ui.button('确认本次验收案例').props.onClick();
 assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/actions/monitor_validation_review');assert.equal(calls[0].body.revision,validation.revision);
 assert.equal(calls[0].body.decision,'accept');assert.equal(calls[0].body.monitor_id,event.monitor_id);
 const reject=props();reject.event.validation=validation;const rejects=observe(reject),rejected=component(reject);
 await rejected.button('拒绝这些案例').props.onClick();assert.equal(rejects[0].body.decision,'reject');assert.equal(rejects[0].body.revision,validation.revision);
 const changed=props();changed.event.validation=validation;const review=component(changed);review.checkbox().props.onChange({target:{checked:true}});review.render();
 review.update({...changed,event:{...changed.event,validation:{...validation,revision:'replacement'}}});assert.equal(review.checkbox().props.checked,false);
});

test('clarification never offers executable approval; accepted original row prepares only before any resume',async()=>{
 const p=props();p.event.validation={...validation,status:'clarification_needed'};const ui=component(p);
 assert.equal(ui.button('确认本次验收案例'),undefined);assert.equal(ui.button('按原许可准备环境与基线'),undefined);
 const accepted={...validation,status:'accepted'},next={...p,event:{...event,validation:accepted}};const calls=observe(next);ui.update(next);
 assert.match(ui.text(),/按原目录维护许可/);assert.match(ui.text(),/不会自动启动模型修复/);
 const click=ui.button('按原许可准备环境与基线').props.onClick;await Promise.all([click(),click()]);assert.equal(calls.length,1);
 assert.equal(calls[0].path,'/api/actions/monitor_validation_prepare');assert.equal(calls[0].body.revision,validation.revision);
 for(const status of ['started','failed_no_replay','finished']){
  ui.update({...next,event:{...event,validation:{...accepted,resume:{status,result_status:'reproduced'}}}});
  assert.equal(ui.button('按原许可准备环境与基线'),undefined);assert.match(ui.text(),/不重复运行/);
 }
});

test('busy original proposal remains independently stoppable and uncertain stop is held without replay',async()=>{
 const p=props();p.p.hub.active={id:'busy'};p.event.validation={...validation,status:'started',stop_revision:'original-stop-revision'};
 let resolve;const calls=[];p.p.request=async(path,token,body)=>{calls.push({path,token,body});return new Promise(done=>{resolve=done;});};
 const ui=component(p);assert.equal(ui.button('核对原会话并取回案例').props.disabled,true);assert.equal(ui.button('停止本次只读案例提议').props.disabled,false);
 const click=ui.button('停止本次只读案例提议').props.onClick,pending=click();await click();ui.render();assert.equal(calls.length,1);
 assert.equal(calls[0].path,'/api/actions/monitor_validation_stop');assert.equal(calls[0].token,undefined);assert.equal(calls[0].body.revision,'original-stop-revision');
 assert.equal(calls[0].body.event_id,event.id);resolve({id:calls[0].body.request_id});await pending;ui.render();assert.match(ui.text(),/只读提议阶段将协作取消/);
 const uncertain=props();uncertain.event.validation={...validation,status:'started'};const unknown=[];
 uncertain.p.request=async(path,_token,body)=>{unknown.push({path,body});const e=Error('unknown stop');e.uncertain=true;throw e;};
 const held=component(uncertain);await held.button('停止本次只读案例提议').props.onClick();held.render();
 assert.match(held.text(),/停止结果未知/);assert.equal(held.button('停止本次只读案例提议').props.disabled,true);
 await held.button('停止本次只读案例提议').props.onClick();held.update({...uncertain});assert.equal(unknown.length,1);
});
