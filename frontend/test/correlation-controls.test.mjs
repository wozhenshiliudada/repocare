import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {CorrelationControls}=await import('../src/CorrelationControls.tsx');

function nodes(n,check){return !n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(v=>nodes(v,check)):
 [...(check(n)?[n]:[]),...nodes(n.props?.children,check)];}
function text(n){return n==null||typeof n==='boolean'?'':typeof n!=='object'?String(n):Array.isArray(n)?n.map(text).join(''):text(n.props?.children);}
// Run the actual component, useOperation, effects and handlers in memory.
function component(initial){
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
 let props=initial,cursor=0,dirty=false,tree,effects=[];
 const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
 const dispatch={useState(initial){const i=cursor++;hooks[i]??={value:typeof initial==='function'?initial():initial};
  return [hooks[i].value,next=>{const value=typeof next==='function'?next(hooks[i].value):next;
   if(!Object.is(value,hooks[i].value)){hooks[i].value=value;dirty=true;}}];},
  useRef(value){const i=cursor++;return hooks[i]??={current:value};},
  useEffect(fn,deps){const i=cursor++;if(!hooks[i]||!same(hooks[i].deps,deps)){const cleanup=hooks[i]?.cleanup;
   hooks[i]={deps};effects.push(()=>{cleanup?.();hooks[i].cleanup=fn();});}}};
 function render(){let rounds=0;do{assert.ok(++rounds<25);dirty=false;cursor=0;effects=[];
  const old=internal.H;internal.H=dispatch;try{tree=CorrelationControls(props);}finally{internal.H=old;}
  for(const fn of effects)fn();}while(dirty);return tree;}
 render();return {render,update(value){props=value;return render();},tree:()=>tree,text:()=>text(tree),
  unmount(){for(const hook of hooks)hook?.cleanup?.();},
  button(label){return nodes(tree,n=>n.type==='button'&&text(n)===label)[0];},
  buttons(label){return nodes(tree,n=>n.type==='button'&&text(n)===label);},
  checkbox(){return nodes(tree,n=>n.type==='input'&&n.props.type==='checkbox')[0];},
  selects(){return nodes(tree,n=>n.type==='select');},
  choose(index,value){this.selects()[index].props.onChange({target:{value}});render();}};
}
const left={kind:'issue',workspace_id:'workspace',investigation_id:'investigation'},
 right={kind:'monitor',monitor_id:'monitor',event_id:'event'};
const envelope={preview:{id:'a'.repeat(64),left,right,origins:[{label:'Issue #7 原调查',head_sha:'abcdef1234567890',request:{issue:'购物车空值'},origin_id:'issue-origin',basis_sha256:'issue-basis'},
 {label:'原监控事件',head_sha:'abcdef1234567890',request:{issue:'购物车空值'},origin_id:'monitor-origin',basis_sha256:'monitor-basis'}],
 comparison:{comparable:true,differences:[]},budgets:[{request_budget:8,requests_used:2,requests_remaining:6}]},revision:'original-revision'};
function props(){return {p:{page:'events',record:{id:'workspace',repository:'C:/original',token:'DO_NOT_SEND_TOKEN',api_prefix:'/workspace-api/'},
 state:{runtime:{code_changed:false},budget:{requests_remaining:6},issue_workbench:{configuration:{sha256:'issue-rules'},
  environments:[{id:'investigation',binding:{binding_sha256:'issue-binding'}}],
  investigations:[{id:'investigation',status:'investigated',snapshot:{head_sha:'abcdef1234567890',issue:{number:7,title:'购物车空值'}}}],executions:[]}},
 hub:{runtime:{code_changed:false},jobs:[],monitors:[{id:'monitor',spec:{path:'C:/original'},root_rules:{sha256:'monitor-rules'},
  request_budget:8,requests_used:2,requests_remaining:6,events:[{id:'event',sha:'abcdef1234567890',message:'购物车失败',
   root_preparation:{result_sha256:'original-preparation'}}]},
  {id:'foreign',spec:{path:'C:/foreign'},events:[{id:'foreign-event',sha:'foreign'}]}]},
 request:async()=>{throw Error('unexpected request');},refresh:async()=>{}}};}
function observe(p,handler){const calls=[];p.p.request=async(path,token,body)=>{calls.push({path,token,body});
 return handler?handler(path,token,body):{id:body.request_id,status:'running'};};return calls;}
function select(ui){ui.choose(0,JSON.stringify(left));ui.choose(1,JSON.stringify(right));}
function finished(id,result=envelope,patch={}){return {id,action:'maintenance_link_preview',status:'finished',result:structuredClone(result),...patch};}
async function ready(result=envelope){const p=props(),calls=observe(p),ui=component(p);select(ui);
 await ui.button('预览来源关联').props.onClick();ui.render();const job=finished(calls[0].body.request_id,result);
 p.p.hub={...p.p.hub,jobs:[job]};ui.update(p);return {p,ui,calls,job};}
function finishConfirm(id,previewId,patch={}){
 const base={id,action:'maintenance_link_confirm',status:'finished',result:{status:'linked',id:previewId}};
 return {...base,...patch,result:{...base.result,...(patch.result||{})}};
}
async function confirmed(){const {p,ui,calls}=await ready();
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();
 await ui.button('确认关联原来源').props.onClick();ui.render();
 return {p,ui,calls,requestId:calls[1].body.request_id,previewId:calls[1].body.preview.id};}
function linkRow(id,status='linked',patch={}){return {id,status,sources:[{kind:'issue',origin_id:'issue-origin'}],...patch};}
function receipt(state,{jobId=state.requestId,jobPatch={},links=[linkRow(state.previewId)],record}={}){
 if(record)state.p.p.record={...state.p.p.record,...record};
 const job=finishConfirm(jobId,state.previewId,jobPatch);
 state.p.p.hub={...state.p.p.hub,jobs:[...(state.p.p.hub.jobs||[]).filter(row=>row.id!==jobId),job],correlation:{links}};
 state.ui.update(state.p);return state;
}

test('SSR uses current unexecuted Issue environments and original monitor events, displays targets without calls',()=>{
 const p=props();let calls=0;p.p.request=async()=>{calls++;throw Error('render dispatched');};
 p.p.state.issue_workbench.environments.push({id:'executed'},{id:'orphan'});
 p.p.state.issue_workbench.investigations.push({id:'executed',snapshot:{issue:{number:88}}});
 p.p.state.issue_workbench.executions.push({id:'old-execution',investigation_id:'executed'});
 const html=renderToStaticMarkup(React.createElement(CorrelationControls,p));
 assert.match(html,/Issue #7/);assert.match(html,/HEAD abcdef123456/);assert.match(html,/购物车空值/);
 assert.match(html,/预览不调用模型/);assert.match(html,/不合账、不继承批准/);assert.match(html,/尚未全部覆盖/);
 assert.doesNotMatch(html,/foreign-event|Issue #88|orphan|DO_NOT_SEND_TOKEN|type="text"|checked=""/);assert.equal(calls,0);
 for(const record of [undefined,{id:'workspace'},{repository:'C:/original'},{id:'workspace',repository:' '}]){
  const empty=component({p:{...p.p,record}});assert.equal(empty.button('预览来源关联').props.disabled,true);
  assert.equal(nodes(empty.tree(),n=>n.type==='option').length,2);
 }
});

test('real preview requires distinct original sources and sends only descriptors through hub authentication',async()=>{
 const p=props(),calls=observe(p),ui=component(p);
 await ui.button('预览来源关联').props.onClick();ui.choose(0,JSON.stringify(left));ui.choose(1,JSON.stringify(left));
 await ui.button('预览来源关联').props.onClick();assert.equal(calls.length,0);select(ui);
 const click=ui.button('预览来源关联').props.onClick;await Promise.all([click(),click()]);ui.render();
 assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/actions/maintenance_link_preview');assert.equal(calls[0].token,undefined);
 assert.deepEqual({...calls[0].body,request_id:undefined},{left,right,request_id:undefined});
 assert.match(calls[0].body.request_id,/^[a-f0-9]{32}$/);assert.equal(ui.checkbox(),undefined);
});

test('only the exact completed requested preview can open unchecked confirmation',async()=>{
 const p=props(),calls=observe(p),ui=component(p);select(ui);
 p.p.hub.jobs=[finished('unsolicited')];ui.update(p);assert.equal(ui.checkbox(),undefined);
 await ui.button('预览来源关联').props.onClick();ui.render();const id=calls[0].body.request_id;
 for(const patch of [{id:'foreign-request'},{action:'other-action'},{status:'running'},
  {result:{...envelope,preview:{...envelope.preview,left:{...left,workspace_id:'foreign'}}}},
  {result:{...envelope,preview:{...envelope.preview,right:{...right,event_id:'foreign'}}}},
  {result:{...envelope,revision:undefined}},
  {result:{...envelope,preview:{...envelope.preview,id:undefined}}},
  {result:{...envelope,preview:{...envelope.preview,id:'not-a-pinned-id'}}}]){
  p.p.hub={...p.p.hub,jobs:[finished(id,envelope,patch)]};ui.update(p);assert.equal(ui.checkbox(),undefined);
 }
 p.p.hub={...p.p.hub,jobs:[finished(id)]};ui.update(p);assert.equal(ui.checkbox().props.checked,false);
 assert.equal(ui.button('确认关联原来源').props.disabled,true);assert.match(ui.text(),/同一故障已经得到证明/);
});

test('explicit confirmation sends the exact preview and revision once, including saved handler double clicks',async()=>{
 const {p,ui,calls}=await ready();await ui.button('确认关联原来源').props.onClick();assert.equal(calls.length,1);
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();const click=ui.button('确认关联原来源').props.onClick;
 await Promise.all([click(),click()]);await click();ui.render();assert.equal(calls.length,2);
 assert.equal(calls[1].path,'/api/actions/maintenance_link_confirm');assert.equal(calls[1].token,undefined);
 assert.deepEqual({...calls[1].body,request_id:undefined},{...envelope,confirmed:true,request_id:undefined});
 p.p.hub={...p.p.hub,jobs:[...p.p.hub.jobs,{id:calls[1].body.request_id,action:'maintenance_link_confirm',status:'finished',result:{linked:true}}]};
 ui.update(p);ui.checkbox().props.onChange({target:{checked:true}});ui.render();await click();
 await ui.button('确认关联原来源').props.onClick();assert.equal(calls.length,2);
});

test('source selection clears consent and old handlers cannot confirm a previous preview',async()=>{
 const {ui,calls}=await ready();ui.checkbox().props.onChange({target:{checked:true}});ui.render();
 const stale=ui.button('确认关联原来源').props.onClick;ui.choose(1,'');assert.equal(ui.checkbox(),undefined);
 await stale();assert.equal(calls.length,1);ui.choose(1,JSON.stringify(right));assert.equal(ui.checkbox(),undefined);
});

test('repository, rules, environments, execution, preparation, budgets and runtime changes invalidate preview and saved handlers',async()=>{
 const changes=[
  p=>{p.record={...p.record,repository:'C:/changed'};},p=>{p.record={...p.record,id:'other'};},
  p=>{p.record={...p.record,token:'new-token'};},
  p=>{p.state.issue_workbench.configuration.sha256='changed';},
  p=>{p.state.issue_workbench.environments[0].binding.binding_sha256='changed';},
  p=>{p.state.issue_workbench.investigations[0].snapshot.head_sha='changed';},
  p=>{p.state.issue_workbench.executions.push({investigation_id:'investigation'});},
  p=>{p.hub.monitors[0].root_rules.sha256='changed';},
  p=>{p.hub.monitors[0].events[0].root_preparation.result_sha256='changed';},
  p=>{p.hub.monitors[0].requests_remaining=5;},p=>{p.state.budget.requests_remaining=5;},
  p=>{p.hub.runtime={code_changed:true};},p=>{p.state.runtime={code_changed:true};},
  p=>{p.hub.active={id:'active-original-job'};},
 ];
 for(const change of changes){const {p,ui,calls}=await ready();ui.checkbox().props.onChange({target:{checked:true}});ui.render();
  const stale=ui.button('确认关联原来源').props.onClick;change(p.p);ui.update(p);assert.notEqual(ui.checkbox()?.props.checked,true);
  assert.equal(ui.button('确认关联原来源'),undefined);await stale();assert.equal(calls.length,1);
 }
});

test('a replaced or no-longer-completed job cannot retain approval for a matching descriptor',async()=>{
 for(const change of [job=>{job.result.revision='replaced';},job=>{job.result.preview.origins[0].basis_sha256='replaced';},
  job=>{job.status='running';},job=>{job.action='foreign';}]){
  const {p,ui,calls}=await ready();ui.checkbox().props.onChange({target:{checked:true}});ui.render();
  const stale=ui.button('确认关联原来源').props.onClick;change(p.p.hub.jobs[0]);ui.update(p);
  assert.notEqual(ui.checkbox()?.props.checked,true);await stale();assert.equal(calls.length,1);
 }
});

test('incomparable preview exposes reasons, origins and original budgets with confirmation disabled',async()=>{
 const result=structuredClone(envelope);result.preview.comparison={comparable:false,differences:['实际检查不一致','环境权限不同']};
 const {ui,calls}=await ready(result);assert.match(ui.text(),/当前不可比较/);assert.match(ui.text(),/实际检查不一致/);
 assert.match(ui.text(),/环境权限不同/);assert.equal(ui.checkbox().props.checked,false);assert.equal(ui.checkbox().props.disabled,true);
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();await ui.button('确认关联原来源').props.onClick();assert.equal(calls.length,1);
 const budgets=nodes(ui.tree(),n=>n.type?.name==='Evidence'&&n.props.title==='各来源原额度（分别保持）');
 assert.equal(budgets.length,1);assert.deepEqual(budgets[0].props.value,result.preview.budgets);
});

test('workspace and hub active or changed runtime block preview even when already present on entry',async()=>{
 for(const change of [p=>{p.state.active={id:'workspace-job'};},p=>{p.state.runtime.code_changed=true;},
  p=>{p.hub.active={id:'hub-job'};},p=>{p.hub.runtime.code_changed=true;}]){
  const p=props();change(p.p);const calls=observe(p),ui=component(p);select(ui);
  assert.equal(ui.button('预览来源关联').props.disabled,true);await ui.button('预览来源关联').props.onClick();assert.equal(calls.length,0);
 }
});

test('unknown confirmation remains held without automatic or manual replay',async()=>{
 const {p,ui,calls}=await ready();p.p.request=async(path,token,body)=>{calls.push({path,token,body});const error=Error('结果未知；核对原回执');error.uncertain=true;throw error;};
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();await ui.button('确认关联原来源').props.onClick();ui.render();
 for(let index=0;index<3;index++){ui.update(p);await ui.button('确认关联原来源').props.onClick();}
 assert.equal(calls.length,2);assert.equal(ui.button('确认关联原来源').props.disabled,true);
 assert.match(ui.text(),/结果未知/);
});

test('existing links expose distinct sources, dispatch owner, status and evidence without posting',()=>{
 const p=props();p.p.hub.correlation={links:[{id:'link',sources:[{kind:'issue',origin_id:'issue-origin'},
  {kind:'monitor',origin_id:'monitor-origin'}],owner:{run_id:'original-run'},status:'claimed'}]};
 const calls=observe(p),ui=component(p);assert.match(ui.text(),/issue-origin/);assert.match(ui.text(),/monitor-origin/);
 assert.match(ui.text(),/original-run/);assert.match(ui.text(),/已认领/);assert.doesNotMatch(ui.text(),/claimed/);assert.equal(calls.length,0);
 assert.equal(nodes(ui.tree(),n=>n.type?.name==='Evidence'&&n.props.title==='关联、负责人和原始证据').length,1);
});

test('only this exact finished linked confirm with the current linked row is success',async()=>{
 const state=await confirmed();
 // A queued/running 202 is pending, never this association's success.
 receipt(state,{jobPatch:{status:'running'}});
 assert.doesNotMatch(state.ui.text(),/本次关联已记录/);
 assert.doesNotMatch(state.ui.text(),/原来源、规则、额度或运行状态已变化/);
 // A finished linked receipt for another request cannot substitute.
 receipt(state,{jobId:'foreign-request'});
 assert.doesNotMatch(state.ui.text(),/本次关联已记录/);
 // The exact request, preview id, workspace and current linked row are required.
 receipt(state,{links:[linkRow(state.previewId,'linked',{owner_source:{label:'原任务负责人 · 示例'}})]});
 const text=state.ui.text();
 assert.match(text,/本次关联已记录，原预览已结束/);
 assert.doesNotMatch(text,/原来源、规则、额度或运行状态已变化/);
 assert.match(text,/已关联/);assert.match(text,/原任务负责人 · 示例/);
 assert.equal(state.ui.checkbox(),undefined);assert.equal(state.calls.length,2);
 // Another recorded link does not erase this exact completed confirmation.
 receipt(state,{links:[linkRow(state.previewId,'linked',{owner_source:{label:'原任务负责人 · 示例'}}),linkRow('other-link')]});
 assert.match(state.ui.text(),/本次关联已记录，原预览已结束/);
 assert.doesNotMatch(state.ui.text(),/原来源、规则、额度或运行状态已变化/);
});

test('wrong request, id, workspace, row, failed, unknown or admission receipts never show success',async()=>{
 const variants=[
  ['foreign request job',{jobId:'foreign-request'}],
  ['other action',{jobPatch:{action:'other'}}],
  ['foreign result id',{jobPatch:{result:{id:'foreign-preview'}}}],
  ['link_pending result',{jobPatch:{result:{status:'link_pending'}}}],
  ['failed job',{jobPatch:{status:'failed'}}],
  ['unknown job',{jobPatch:{status:'unknown'}}],
  ['running admission',{jobPatch:{status:'running'}}],
  ['no linked row',{links:[]}],
  ['other row only',{links:[linkRow('foreign-link')]}],
  ['blocked row',{links:[]}],
  ['other workspace',{record:{id:'other-workspace'}}],
  ['other repository',{record:{repository:'C:/changed'}}],
 ];
 for(const [name,options] of variants){
  const state=await confirmed();
  if(name==='blocked row')options.links=[linkRow(state.previewId,'blocked')];
  receipt(state,options);
  assert.doesNotMatch(state.ui.text(),/本次关联已记录/,name);
 }
});

test('an external correlation change ends the preview without claiming this component success',async()=>{
 const {p,ui,calls}=await ready();
 p.p.hub={...p.p.hub,correlation:{links:[linkRow('other-link')]}};ui.update(p);
 assert.match(ui.text(),/原来源、规则、额度或运行状态已变化，请重新预览；原同意已失效/);
 assert.doesNotMatch(ui.text(),/本次关联已记录/);
 assert.equal(ui.checkbox(),undefined);assert.equal(calls.length,1);
});

test('links use readable source and owner labels while preserving exact evidence and legacy fallbacks',()=>{
 const p=props();
 p.p.hub.correlation={links:[
  {id:'readable',status:'linked',sources:[
   {kind:'issue',origin_id:'issue-origin',label:'Issue #7 购物车空值',workspace_id:'workspace',investigation_id:'investigation'},
   {kind:'monitor',origin_id:'monitor-origin',workspace_id:'workspace',monitor_id:'monitor',event_id:'event'}],
   owner_source:{kind:'issue',origin_id:'origin',label:'原任务负责人 · 示例',workspace_id:'workspace',
    investigation_id:'investigation',execution_id:'execution',run_id:'run'}},
  {id:'legacy',status:'claimed',sources:[{kind:'issue',origin_id:'legacy-origin'}],owner:{run_id:'legacy-run'}},
  {id:'pending',status:'link_pending',sources:[],owner:null}]};
 const calls=observe(p),ui=component(p),text=ui.text();
 assert.match(text,/Issue #7 购物车空值/);assert.doesNotMatch(text,/issue-origin/);
 assert.match(text,/监控事件来源（详见证据）/);assert.match(text,/monitor-origin/);
 assert.match(text,/原任务负责人 · 示例/);assert.match(text,/已关联/);
 assert.match(text,/Issue 来源（详见证据）/);assert.match(text,/legacy-origin/);
 assert.match(text,/原任务负责人（详见证据）/);assert.match(text,/legacy-run/);
 assert.match(text,/已认领/);assert.match(text,/关联待完成/);assert.match(text,/尚未认领/);
 assert.doesNotMatch(text,/\{"run_id"/);assert.equal(calls.length,0);
 const evidence=nodes(ui.tree(),n=>n.type?.name==='Evidence'&&n.props.title==='关联、负责人和原始证据');
 assert.equal(evidence.length,3);
 assert.equal(evidence[0].props.value.owner_source.execution_id,'execution');
});

function ownerLink(owner_source,status='linked'){
 return {id:'owner-link',status,sources:[{kind:owner_source?.kind||'issue',origin_id:'owner-origin'}],owner_source};
}
test('a saved owner click is inert after real effect cleanup and a new workspace mount',()=>{
 const p=props();p.p.rows=[{id:'old-row',selection:{kind:'issue',id:'execution',number:7}}];
 p.p.hub={...p.p.hub,correlation:{links:[ownerLink({kind:'issue',workspace_id:'workspace',execution_id:'execution'})]}};
 const selected=[];p.p.selectTask=id=>selected.push(id);
 const calls=observe(p),old=component(p),stale=old.button('查看原负责人任务').props.onClick;
 old.unmount();
 const fresh=props();fresh.p.record={...fresh.p.record,id:'new-workspace',repository:'C:/new'};
 fresh.p.selectTask=id=>selected.push('new:'+id);component(fresh);
 stale();assert.deepEqual(selected,[]);assert.equal(calls.length,0);
});
test('linked issue owner opens exactly one already-read task without any request',()=>{
 const p=props();p.p.rows=[{id:'issue-row',selection:{kind:'issue',id:'execution',number:7}}];
 p.p.hub={...p.p.hub,correlation:{links:[ownerLink({kind:'issue',label:'Issue #7 原负责人',
  workspace_id:'workspace',investigation_id:'investigation',execution_id:'execution',run_id:'advisory-run'})]}};
 const selected=[];p.p.selectTask=id=>selected.push(id);
 const calls=observe(p),ui=component(p),buttons=ui.buttons('查看原负责人任务');
 assert.equal(buttons.length,1);assert.equal(buttons[0].props.disabled,false);
 buttons[0].props.onClick();assert.deepEqual(selected,['issue-row']);assert.equal(calls.length,0);
 assert.match(ui.text(),/只读打开当前已读的原任务/);
 assert.equal(nodes(ui.tree(),n=>n.type?.name==='Evidence'&&n.props.title==='关联、负责人和原始证据').length,1);
});
test('linked monitor owner opens the existing monitor page, never a guessed task',()=>{
 const p=props();p.p.hub={...p.p.hub,correlation:{links:[ownerLink({kind:'monitor',label:'原监控事件',
  monitor_id:'monitor',event_id:'event',run_id:'original-run'})]}};
 const pages=[],selected=[];p.p.navigate=page=>pages.push(page);p.p.selectTask=id=>selected.push(id);
 const calls=observe(p),ui=component(p),button=ui.button('查看原负责人报告');
 assert.equal(button.props.disabled,false);button.props.onClick();
 assert.deepEqual(pages,['events']);assert.deepEqual(selected,[]);assert.equal(calls.length,0);
 assert.match(ui.text(),/不代表已打开一个原任务/);
});
test('a foreign issue owner only switches to a hub-registered original workspace',()=>{
 const p=props();p.p.hub={...p.p.hub,workspaces:[{id:'other',repository:'C:/other'}],
  correlation:{links:[ownerLink({kind:'issue',label:'Issue #8 原负责人',workspace_id:'other',
   investigation_id:'investigation',execution_id:'execution'})]}};
 const selected=[],workspaces=[];p.p.selectTask=id=>selected.push(id);
 p.p.selectWorkspace=id=>workspaces.push(id);
 const calls=observe(p),ui=component(p),button=ui.button('查看原负责人任务');
 assert.equal(button.props.disabled,false);button.props.onClick();
 assert.deepEqual(workspaces,['other']);assert.deepEqual(selected,[]);assert.equal(calls.length,0);
 assert.match(ui.text(),/只读切换已登记的原工作区/);
});
test('missing, ambiguous, unregistered and foreign-monitor owners stay disabled with next steps and zero requests',()=>{
 const variants=[
  ['missing workspace',{kind:'issue',investigation_id:'investigation',execution_id:'execution'},'查看原负责人任务',/工作区/],
  ['ambiguous execution',{kind:'issue',workspace_id:'workspace',investigation_id:'investigation',execution_id:'execution'},'查看原负责人任务',/多个相同原执行/],
  ['unregistered workspace',{kind:'issue',workspace_id:'ghost',investigation_id:'investigation',execution_id:'execution'},'查看原负责人任务',/尚未在本机登记/],
  ['foreign monitor without workspace',{kind:'monitor',monitor_id:'foreign',event_id:'foreign-event'},'查看原负责人报告',/另一仓库或原工作区尚未登记/],
 ];
 for(const [name,owner,label,expected] of variants){
  const p=props();p.p.rows=[{id:'issue-row',selection:{kind:'issue',id:'execution'}},
   ...(name==='ambiguous execution'?[{id:'issue-row-2',selection:{kind:'issue',id:'execution'}}]:[])];
  p.p.hub={...p.p.hub,correlation:{links:[ownerLink(owner)]}};
  const selected=[];p.p.selectTask=id=>selected.push(id);
  const calls=observe(p),ui=component(p),button=ui.button(label);
  assert.equal(button?.props.disabled,true,name);button?.props.onClick();
  assert.deepEqual(selected,[],name);assert.equal(calls.length,0,name);
  assert.match(ui.text(),/下一步/,name);assert.match(ui.text(),expected,name);
  assert.equal(nodes(ui.tree(),n=>n.type?.name==='Evidence'&&n.props.title==='关联、负责人和原始证据').length,1,name);
 }
});
test('blocked or missing-owner links show a next step, keep evidence and offer no owner jump',()=>{
 const variants=[
  ['blocked',{id:'blocked-link',status:'blocked',reason:'关联未完成，请核对原账',sources:[{kind:'issue',origin_id:'blocked-origin'}]}],
  ['stale',{id:'stale-link',status:'stale',reason:'来源或版本已变化',sources:[{kind:'issue',origin_id:'stale-origin'}]}],
  ['linked without owner source',{id:'no-owner-link',status:'linked',sources:[{kind:'issue',origin_id:'no-owner-origin'}]}],
 ];
 for(const [name,link] of variants){
  const p=props();p.p.hub={...p.p.hub,correlation:{links:[link]}};
  const calls=observe(p),ui=component(p);
  assert.equal(ui.button('查看原负责人任务'),undefined,name);
  assert.match(ui.text(),/下一步/,name);assert.equal(calls.length,0,name);
  assert.equal(nodes(ui.tree(),n=>n.type?.name==='Evidence'&&n.props.title==='关联、负责人和原始证据').length,1,name);
 }
});
test('a click saved before a repository switch cannot reuse old rows or callbacks',()=>{
 const p=props();p.p.rows=[{id:'old-row',selection:{kind:'issue',id:'execution'}}];
 p.p.hub={...p.p.hub,correlation:{links:[ownerLink({kind:'issue',workspace_id:'workspace',
  investigation_id:'investigation',execution_id:'execution'})]}};
 const selected=[],workspaces=[];p.p.selectTask=id=>selected.push(id);
 p.p.selectWorkspace=id=>workspaces.push(id);
 const calls=observe(p),ui=component(p),stale=ui.button('查看原负责人任务').props.onClick;
 p.p.record={id:'changed',repository:'C:/changed'};
 p.p.rows=[{id:'new-row',selection:{kind:'issue',id:'execution'}}];
 p.p.hub={...p.p.hub,correlation:{links:[ownerLink({kind:'issue',workspace_id:'changed',
  investigation_id:'investigation',execution_id:'execution'})]}};
 ui.update(p);
 const fresh=ui.button('查看原负责人任务');assert.equal(fresh.props.disabled,false);
 stale();assert.deepEqual(selected,[]);assert.deepEqual(workspaces,[]);assert.equal(calls.length,0);
 fresh.props.onClick();assert.deepEqual(selected,['new-row']);assert.deepEqual(workspaces,[]);assert.equal(calls.length,0);
});
test('a missing task/workspace/page callback disables the owner button with a next step',()=>{
 const variants=[
  ['task',{kind:'issue',workspace_id:'workspace',investigation_id:'investigation',execution_id:'execution'},
   '查看原负责人任务',/任务选择入口/,{rows:[{id:'issue-row',selection:{kind:'issue',id:'execution'}}]}],
  ['workspace',{kind:'issue',workspace_id:'other',investigation_id:'investigation',execution_id:'execution'},
   '查看原负责人任务',/工作区切换入口/,{workspaces:[{id:'other',repository:'C:/other'}]}],
  ['events',{kind:'monitor',monitor_id:'monitor',event_id:'event'},
   '查看原负责人报告',/监控导航入口/,{}],
 ];
 for(const [name,owner,label,expected,patch] of variants){
  const p=props();p.p.rows=patch.rows||[];p.p.hub={...p.p.hub,workspaces:patch.workspaces,
   correlation:{links:[ownerLink(owner)]}};
  p.p.navigate=undefined;
  if(name==='task')p.p.selectTask=undefined;
  if(name==='workspace')p.p.selectWorkspace=undefined;
  const calls=observe(p),ui=component(p),button=ui.button(label);
  assert.equal(button?.props.disabled,true,name);button?.props.onClick();
  assert.match(ui.text(),expected,name);assert.equal(calls.length,0,name);
 }
});
