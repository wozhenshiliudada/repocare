import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {ModelReasoningPicker}=await import('../src/ModelReasoningPicker.tsx');
const {ModelTaskSelector}=await import('../src/ModelTaskSelector.tsx');
const original={id:'legacy',name:'原配置',model:'original-model',legacy:true,selectable:true};
const added={id:'a'.repeat(32),name:'开发模型',model:'second-model',legacy:false,selectable:true};
const profiles={revision:'r1',default_profile_id:added.id,profiles:[original,added]};
function props(patch={}){return {profiles,value:added.id,currentId:'legacy',busy:false,budget:50,onChange(){},onApply(){},...patch};}
function nodes(n,check){return !n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(v=>nodes(v,check)):
 [...(check(n)?[n]:[]),...nodes(n.props?.children,check)];}
function text(n){return n==null||typeof n==='boolean'?'':typeof n!=='object'?String(n):Array.isArray(n)?n.map(text).join(''):text(n.props?.children);}
function button(tree,label){const native=nodes(tree,n=>n.type==='button'&&text(n)===label)[0];if(native)return native;const picker=nodes(tree,n=>n.type===ModelReasoningPicker)[0];if(picker&&label==='应用到新任务')return {props:{onClick:picker.props.onApply||(()=>{}),disabled:picker.props.disabled||picker.props.applyDisabled||!picker.props.onApply}};}
function component(initial){
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;let cursor=0,dirty=false,tree,effects=[],input=initial;
 const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
 const dispatcher={useState(initial){const i=cursor++;hooks[i]??={value:typeof initial==='function'?initial():initial};return [hooks[i].value,next=>{const value=typeof next==='function'?next(hooks[i].value):next;if(!Object.is(value,hooks[i].value)){hooks[i].value=value;dirty=true;}}];},useRef(value){const i=cursor++;return hooks[i]??={current:value};},useEffect(fn,deps){const i=cursor++;if(!hooks[i]||!same(hooks[i].deps,deps)){const cleanup=hooks[i]?.cleanup;hooks[i]={deps};effects.push(()=>{cleanup?.();hooks[i].cleanup=fn();});}}};
 function refresh(next){if(next)input=next;let rounds=0;do{assert.ok(++rounds<30);dirty=false;cursor=0;effects=[];const old=internal.H;internal.H=dispatcher;try{tree=ModelTaskSelector(input);}finally{internal.H=old;}for(const fn of effects)fn();}while(dirty);return tree;}
 refresh();return {refresh,tree:()=>tree,find:check=>nodes(tree,check),button:label=>button(tree,label),fill(label,value){const picker=nodes(tree,n=>n.type===ModelReasoningPicker)[0];(label==='新任务思考深度'?picker.props.onEffortChange:picker.props.onModelChange)(value);refresh();},unmount(){for(const hook of hooks)hook?.cleanup?.();}};
}

test('selector explains independent persistent allowance and original approval/recovery without any render call',()=>{
 let actions=0;const html=renderToStaticMarkup(React.createElement(ModelTaskSelector,props({onChange(){actions++;},onApply(){actions++;}})));
 assert.equal(actions,0);assert.match(html,/aria-label="新任务使用的模型"/);assert.match(html,/开发模型/);assert.doesNotMatch(html,/<select/);assert.match(html,/<details/);
 assert.match(html,/旧任务的审批、重试和恢复始终使用原模型、原会话与原账/);assert.match(html,/独立、持久的模型请求额度（上限 50 次）/);
 assert.match(html,/回到已用过的配置时复用原会话和原余额/);assert.match(html,/不补充或重置旧额度/);
 assert.match(html,/应用只准备或重开对应会话，不调用模型/);assert.match(html,/当前任务会话：原配置 · original-model/);
});

test('selecting emits only selection and explicit apply invokes context preparation',async()=>{
 const changes=[];let applied=0,refreshed=0;
 const tree=component(props({onChange:value=>changes.push(value),onApply:()=>{applied++;},onRefresh:()=>{refreshed++;}})).tree();
 nodes(tree,n=>n.type===ModelReasoningPicker)[0].props.onModelChange(added.id);
 assert.deepEqual(changes,[added.id]);assert.equal(applied,0);
 const apply=button(tree,'应用到新任务');assert.equal(apply.props.disabled,false);await apply.props.onClick();assert.equal(applied,1);
 await button(tree,'刷新可选模型').props.onClick();assert.equal(refreshed,1);
});

test('missing, unavailable, unchanged and busy selection cannot prepare another context',()=>{
 for(const patch of [{profiles:null},{profiles:{profiles:[]}},{value:''},{value:'unknown'},{value:'legacy'},
  {profiles:{profiles:[{...added,selectable:false}]}},{busy:true}]){
  let actions=0;const tree=component(props({...patch,onApply(){actions++;}})).tree();
  const apply=button(tree,'应用到新任务');assert.equal(apply.props.disabled,true);apply.props.onClick();assert.equal(actions,0);
 }
 const loading=renderToStaticMarkup(React.createElement(ModelTaskSelector,props({profiles:null})));
 assert.match(loading,/模型列表尚未读取/);assert.match(loading,/<button\b[^>]*aria-label="新任务使用的模型"[^>]*disabled/);
 const unavailable=renderToStaticMarkup(React.createElement(ModelTaskSelector,props({profiles:{profiles:[{...added,selectable:false}]}})));
 assert.match(unavailable,/所选模型当前不可用/);assert.doesNotMatch(unavailable,/<option/);
});

test('configured effort subsets keep explicit mappings and apply an immutable variant only after a click',async()=>{
 const model={...added,reasoning_protocol:'deepseek',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'max'}]},calls=[];
 const ui=component(props({profiles:{...profiles,profiles:[original,model]},value:model.id,currentId:model.id,
  onReasoningVariant:async(id,effort)=>{calls.push(['variant',id,effort]);return 'v'.repeat(32);},onChange:id=>calls.push(['change',id]),onApply:id=>calls.push(['apply',id])}));
 assert.equal(ui.button('应用到新任务').props.disabled,true);ui.fill('新任务思考深度','max');assert.deepEqual(calls,[]);
 const picker=ui.find(n=>n.type===ModelReasoningPicker)[0];assert.deepEqual(picker.props.profiles.find(row=>row.id===model.id).reasoning_levels.map(row=>row.level),['high','max']);
 await ui.button('应用到新任务').props.onClick();ui.refresh();assert.deepEqual(calls,[['variant',model.id,'max'],['change','v'.repeat(32)],['apply','v'.repeat(32)]]);
 assert.match(text(ui.tree()),/旧任务的审批、重试和恢复始终使用原模型/);
});

test('unknown effort save cannot be replayed until an explicit successful read refresh',async()=>{
 const model={...added,reasoning_protocol:'openai',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'max'}]},calls=[];let failRefresh=true;
 const ui=component(props({profiles:{...profiles,profiles:[model]},value:model.id,currentId:model.id,
  onReasoningVariant:async()=>{calls.push('variant');const error=Error('unknown');error.uncertain=true;throw error;},onApply:()=>calls.push('apply'),onRefresh:async()=>{calls.push('read');if(failRefresh)throw Error('read unavailable');}}));
 ui.fill('新任务思考深度','max');await ui.button('应用到新任务').props.onClick();ui.refresh();
 assert.equal(ui.button('应用到新任务').props.disabled,true);await ui.button('应用到新任务').props.onClick();assert.deepEqual(calls,['variant']);
 await ui.button('刷新可选模型').props.onClick();ui.refresh();assert.equal(ui.button('应用到新任务').props.disabled,true);
 failRefresh=false;await ui.button('刷新可选模型').props.onClick();ui.refresh();assert.equal(ui.button('应用到新任务').props.disabled,false);assert.deepEqual(calls,['variant','read','read']);
});

test('OpenAI omitted off is labeled as provider default, and unknown protocol never guesses levels',()=>{
 const model={...added,reasoning_protocol:'openai',reasoning_effort:'off',reasoning_levels:[{level:'off',api_value:null},{level:'high',api_value:'custom'}]};
 const html=renderToStaticMarkup(React.createElement(ModelTaskSelector,props({profiles:{profiles:[model]}})));
 assert.match(html,/省略（服务商默认）/);assert.doesNotMatch(html,/high → custom/,'closed picker does not dump all mapped options into the page');
 const unconfigured=renderToStaticMarkup(React.createElement(ModelTaskSelector,props()));
 assert.match(unconfigured,/请到设置 → 模型中配置/);assert.doesNotMatch(unconfigured,/<select/);
});

test('returning to a previously used model remains selectable and original budget is never presented as renewed',()=>{
 const html=renderToStaticMarkup(React.createElement(ModelTaskSelector,props({value:'legacy',currentId:added.id,budget:100})));
 assert.match(html,/上限 100 次/);assert.match(html,/原配置 · original-model/);assert.doesNotMatch(html,/<button disabled="">应用到新任务/);
 assert.match(html,/复用原会话和原余额/);assert.match(html,/默认模型变化也不改变原监控/);
});

test('non-positive or invalid budget does not invent a request ceiling',()=>{
 for(const budget of [NaN,0,-1,undefined]){
  const html=renderToStaticMarkup(React.createElement(ModelTaskSelector,props({budget})));
  assert.match(html,/独立、持久的模型请求额度/);assert.doesNotMatch(html,/上限|NaN|undefined/);
 }
});

test('a delayed reasoning variant cannot change or apply after the selector context changes',async()=>{
 const first={...added,reasoning_protocol:'openai',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'max'}]},other={...first,id:'b'.repeat(32)},calls=[];let resolve;
 const common={profiles:{...profiles,profiles:[first,other]},onReasoningVariant:()=>new Promise(done=>{resolve=done;}),onChange:id=>calls.push(['change',id]),onApply:id=>calls.push(['apply',id])};
 const ui=component(props({...common,value:first.id,currentId:first.id,contextKey:'repository-a/tasks'}));ui.fill('新任务思考深度','max');const pending=ui.button('应用到新任务').props.onClick();
 ui.refresh(props({...common,value:other.id,currentId:other.id,contextKey:'repository-b/tasks'}));resolve('v'.repeat(32));await pending;ui.refresh();assert.deepEqual(calls,[]);
});

test('root variant unknown state blocks a remounted selector while leaving explicit read-only refresh available',async()=>{
 const model={...added,reasoning_protocol:'openai',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'max'}]},calls=[];
 const ui=component(props({profiles:{...profiles,profiles:[model]},variantState:'unknown',onApply:()=>calls.push('apply'),onRefresh:async()=>calls.push('read')}));
 assert.equal(ui.button('应用到新任务').props.disabled,true);await ui.button('应用到新任务').props.onClick();assert.deepEqual(calls,[]);assert.equal(ui.button('刷新可选模型').props.disabled,false);await ui.button('刷新可选模型').props.onClick();assert.deepEqual(calls,['read']);
});

test('an explicitly configured none/off model is shown as configured with one fixed level',()=>{
 const model={...added,reasoning_protocol:'none',reasoning_effort:'off',reasoning_levels:[{level:'off',api_value:null}]};
 const html=renderToStaticMarkup(React.createElement(ModelTaskSelector,props({profiles:{profiles:[model]}})));
 assert.doesNotMatch(html,/尚未明确配置|请到设置 → 模型中配置/);assert.match(html,/off/);assert.match(html,/省略（服务商默认）/);
});

test('a variant resolving after selector unmount cannot change selection or prepare a task context',async()=>{
 const model={...added,reasoning_protocol:'openai',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'max'}]},calls=[];let resolve;
 const ui=component(props({profiles:{...profiles,profiles:[model]},value:model.id,currentId:model.id,onReasoningVariant:()=>new Promise(done=>{resolve=done;}),onChange:id=>calls.push(['change',id]),onApply:id=>calls.push(['apply',id])}));
 ui.fill('新任务思考深度','max');const pending=ui.button('应用到新任务').props.onClick();ui.unmount();resolve('v'.repeat(32));await pending;assert.deepEqual(calls,[]);
});


test('task model control is compact and excludes archived selections even when an old selectable flag is true',async()=>{
 const archived={...added,archived:true},calls=[],ui=component(props({profiles:{...profiles,profiles:[original,archived]},onChange:id=>calls.push(['change',id]),onApply:id=>calls.push(['apply',id])}));
 try{const picker=ui.find(node=>node.type===ModelReasoningPicker)[0];assert.equal(ui.tree().props.className,'task-model-selector-compact');assert.equal(ui.find(node=>node.type==='select').length,0);assert.equal(ui.find(node=>node.type==='details').length,1);assert.equal(picker.props.applyDisabled,true);picker.props.onModelChange(archived.id);await picker.props.onApply();assert.deepEqual(calls,[]);assert.match(text(ui.tree()),/所选模型当前不可用/);}
 finally{ui.unmount();}
});


test('pending notification follows internal thinking edits, context changes and the actually applied model',()=>{
 const source={...added,reasoning_protocol:'openai',reasoning_effort:'high',reasoning_levels:[{level:'high',api_value:'high'},{level:'max',api_value:'provider-max'}]},variant={...source,id:'v'.repeat(32),reasoning_effort:'max'},reports=[],common={profiles:{...profiles,profiles:[source,variant]},onPendingChange:value=>reports.push(value)},ui=component(props({...common,value:source.id,currentId:source.id,contextKey:'repo-a'}));
 try{assert.equal(reports.at(-1),false);ui.fill('新任务思考深度','max');assert.equal(reports.at(-1),true);assert.equal(ui.find(node=>node.type===ModelReasoningPicker)[0].props.pending,true);ui.fill('新任务思考深度','high');assert.equal(reports.at(-1),false);ui.refresh(props({...common,value:variant.id,currentId:source.id,contextKey:'repo-a'}));assert.equal(reports.at(-1),true);ui.refresh(props({...common,value:variant.id,currentId:variant.id,contextKey:'repo-b'}));assert.equal(reports.at(-1),false);assert.equal(ui.find(node=>node.type===ModelReasoningPicker)[0].props.pending,false);}
 finally{ui.unmount();}
});
