import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {ModelProbeControls}=await import('../src/ServicePanel.tsx');

const record={id:'original-workspace',repository:'original-repository',api_prefix:'/api/workspaces/original/',token:'SECRET_CAPABILITY'};
const base={repository:record.repository,provider:'Original Pi',request_budget:6,requests_used:1,requests_reserved_unknown:2,
 requests_remaining:3,runtime:{build_sha256:'build',code_changed:false},active:null,jobs:[],
 model_connectivity:{available:true,checks:[]}};
const proposal={schema_version:1,endpoint:'https://example.invalid/v1',model:'explicit-model',config_identity:'opaque-config-pin',
 request_count:1,request_budget:6,requests_used:1,requests_reserved_unknown:2,requests_remaining:3,
 revision:'a'.repeat(64),proves:'model_response_only',repair_verified:false};
function props(){return {page:'service',record,state:structuredClone(base),hub:{jobs:[]},rows:[],
 request:async()=>{throw Error('unexpected request');},refresh:async()=>{},connect(){}};}
function nodes(node,predicate){
 if(!node||typeof node!=='object')return [];
 if(Array.isArray(node))return node.flatMap(value=>nodes(value,predicate));
 return [...(predicate(node)?[node]:[]),...nodes(node.props?.children,predicate)];
}
function text(node){
 if(node==null||typeof node==='boolean')return '';
 if(typeof node!=='object')return String(node);
 if(Array.isArray(node))return node.map(text).join('');
 return text(node.props?.children);
}
// Real component and event handlers with deterministic React hook lifecycle.
// This executes no browser, network, Vite output build or production model.
function component(initial){
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
 let p=initial,cursor=0,dirty=false,tree,effects=[];
 const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
 const dispatcher={
  useState(initial){const i=cursor++;if(!hooks[i])hooks[i]={value:typeof initial==='function'?initial():initial};
   return [hooks[i].value,next=>{const value=typeof next==='function'?next(hooks[i].value):next;
    if(!Object.is(value,hooks[i].value)){hooks[i].value=value;dirty=true;}}];},
  useRef(value){const i=cursor++;if(!hooks[i])hooks[i]={current:value};return hooks[i];},
  useEffect(fn,deps){const i=cursor++;if(!hooks[i]||!same(hooks[i].deps,deps)){
   const cleanup=hooks[i]?.cleanup;hooks[i]={deps};effects.push(()=>{cleanup?.();hooks[i].cleanup=fn();});}},
 };
 function render(){let rounds=0;do{
  if(++rounds>25)throw Error('Component did not settle');dirty=false;cursor=0;effects=[];
  const previous=internal.H;internal.H=dispatcher;try{tree=ModelProbeControls(p);}finally{internal.H=previous;}
  const pending=effects;effects=[];for(const effect of pending)effect();
 }while(dirty);return tree;}
 render();
 return {render,update(value){p=value;return render();},tree:()=>tree,
  button(label){return nodes(tree,n=>n.type==='button'&&text(n)===label)[0];},
  checkbox(){return nodes(tree,n=>n.type==='input'&&n.props.type==='checkbox')[0];},
  text:()=>text(tree)};
}

test('render has no model call and no selected original workspace requires connection',()=>{
 let requests=0;
 const p=props();p.request=async()=>{requests++;throw Error('display dispatched');};
 const html=renderToStaticMarkup(React.createElement(ModelProbeControls,p));
 assert.match(html,/预览一次模型请求/);assert.match(html,/可能收费/);assert.match(html,/不证明修复正确/);
 assert.doesNotMatch(html,/SECRET_CAPABILITY/);assert.doesNotMatch(html,/确认发送一次模型请求/);
 const disconnected=renderToStaticMarkup(React.createElement(ModelProbeControls,{...p,record:undefined,state:null}));
 assert.match(disconnected,/请先连接原仓库/);assert.match(disconnected,/<button disabled="">预览一次模型请求/);
 assert.equal(requests,0);
});

test('actual preview, unchecked checkbox and confirm handlers use original path, token and one request identity',async()=>{
 const p=props(),calls=[];
 p.request=async(path,token,body)=>{calls.push({path,token,body});return {id:body.request_id,status:'finished',result:
  path.endsWith('model_probe_preview')?{model_probe_preview:proposal}:{model_connectivity:{status:'completed'}}};};
 const ui=component(p);
 await ui.button('预览一次模型请求').props.onClick();ui.render();
 assert.equal(calls.length,1);assert.equal(calls[0].path,record.api_prefix+'actions/model_probe_preview');
 assert.equal(calls[0].token,record.token);assert.deepEqual(Object.keys(calls[0].body),['request_id']);
 assert.equal(ui.checkbox().props.checked,false);assert.equal(ui.button('确认发送一次模型请求').props.disabled,true);
 assert.match(ui.text(),/https:\/\/example.invalid\/v1/);assert.match(ui.text(),/explicit-model/);
 assert.match(ui.text(),/未知占用 2/);assert.match(ui.text(),/1 次/);
 await ui.button('确认发送一次模型请求').props.onClick();ui.render();assert.equal(calls.length,1);
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();
 const click=ui.button('确认发送一次模型请求').props.onClick;
 await Promise.all([click(),click()]);ui.render();
 assert.equal(calls.length,2);assert.equal(calls[1].path,record.api_prefix+'actions/model_probe');
 assert.equal(calls[1].body.revision,proposal.revision);assert.match(calls[1].body.request_id,/^[a-f0-9]{32}$/);
 assert.deepEqual(Object.keys(calls[1].body).sort(),['request_id','revision']);
 assert.match(ui.text(),/已收到一次模型响应/);assert.equal(ui.checkbox(),undefined);
});

test('selection, session, budget and runtime changes discard preview and checked confirmation',async()=>{
 for(const change of [
  p=>({...p,record:{...record,id:'other',repository:'other'},state:{...base,repository:'other'}}),
  p=>({...p,record:{...record,token:'new-service-token'}}),
  p=>({...p,state:{...base,requests_used:2}}),
  p=>({...p,state:{...base,requests_reserved_unknown:3}}),
  p=>({...p,state:{...base,request_budget:7}}),
  p=>({...p,state:{...base,runtime:{...base.runtime,code_changed:true}}}),
 ]){
  const p=props();let requests=0;
  p.request=async(_path,_token,body)=>{requests++;return {id:body.request_id,status:'finished',result:{model_probe_preview:proposal}};};
  const ui=component(p);await ui.button('预览一次模型请求').props.onClick();ui.render();
  ui.checkbox().props.onChange({target:{checked:true}});ui.render();assert.equal(ui.checkbox().props.checked,true);
  ui.update(change(p));assert.equal(ui.checkbox(),undefined);assert.equal(requests,1);
 }
});

test('uncertain submission is held in original job identity and never automatically retried',async()=>{
 const p=props(),calls=[];
 p.request=async(path,_token,body)=>{calls.push({path,body});if(path.endsWith('model_probe')){
  const error=Error('Disconnected after submission');error.uncertain=true;throw error;
 }return {id:body.request_id,status:'finished',result:{model_probe_preview:proposal}};};
 const ui=component(p);await ui.button('预览一次模型请求').props.onClick();ui.render();
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();
 await ui.button('确认发送一次模型请求').props.onClick();ui.render();
 assert.equal(calls.length,2);assert.match(ui.text(),/提交结果未知/);
 assert.equal(ui.button('预览一次模型请求').props.disabled,true);
 for(let i=0;i<3;i++)ui.update({...p,state:{...base,jobs:[]}});
 assert.equal(calls.length,2);
 const check={id:calls[1].body.request_id,status:'unknown',model:'explicit-model',started_at:'original-time'};
 ui.update({...p,state:{...base,requests_reserved_unknown:3,model_connectivity:{available:true,checks:[check]},
  jobs:[{id:check.id,status:'interrupted',error:'原操作中断，保留消费'}]}});
 assert.match(ui.text(),/模型结果未知/);assert.match(ui.text(),/不会自动重发/);assert.equal(calls.length,2);
 assert.equal(ui.checkbox(),undefined);
});

test('server rejection clears confirmation and a monitor-owned unavailable session cannot dispatch',async()=>{
 const p=props();let calls=0;
 p.request=async(path,_token,body)=>{calls++;if(path.endsWith('model_probe'))throw Error('Configuration changed; preview again');
  return {id:body.request_id,status:'finished',result:{model_probe_preview:proposal}};};
 const ui=component(p);await ui.button('预览一次模型请求').props.onClick();ui.render();
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();
 await ui.button('确认发送一次模型请求').props.onClick();ui.render();
 assert.equal(ui.checkbox(),undefined);assert.match(ui.text(),/Configuration changed/);assert.equal(calls,2);
 ui.update({...p,state:{...base,model_connectivity:{available:false,checks:[]}}});
 assert.equal(ui.button('预览一次模型请求').props.disabled,true);
 await ui.button('预览一次模型请求').props.onClick();assert.equal(calls,2);
 assert.match(ui.text(),/原控制器/);
});

test('queued completion reads the exact original job and late foreign workspace responses are discarded',async()=>{
 const p=props();let identity;
 p.request=async(_path,_token,body)=>{identity=body.request_id;return {id:identity,status:'running'};};
 const ui=component(p);await ui.button('预览一次模型请求').props.onClick();ui.render();
 assert.equal(ui.checkbox(),undefined);assert.match(ui.text(),/正在读取原配置/);
 ui.update({...p,state:{...base,jobs:[{id:'foreign',status:'finished',result:{model_probe_preview:proposal}}]}});
 assert.equal(ui.checkbox(),undefined);
 ui.update({...p,state:{...base,jobs:[{id:identity,status:'finished',result:{model_probe_preview:proposal}}]}});
 assert.equal(ui.checkbox().props.checked,false);
 let resolve,refreshes=0,calls=0;
 const delayed=props();delayed.refresh=async()=>{refreshes++;};
 delayed.request=async()=>{calls++;return new Promise(done=>{resolve=done;});};
 const other=component(delayed),pending=other.button('预览一次模型请求').props.onClick();other.render();
 other.update({...delayed,record:{...record,id:'other',repository:'other',token:'other'},state:{...base,repository:'other'}});
 resolve({id:'old',status:'finished',result:{model_probe_preview:proposal}});await pending;other.render();
 assert.equal(other.checkbox(),undefined);assert.equal(refreshes,0);assert.equal(calls,1);
});
