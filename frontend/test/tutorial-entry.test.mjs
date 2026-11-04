import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {WorkbenchPages}=await import('../src/WorkbenchPages.tsx');
const {RootMonitorPreparation}=await import('../src/RootMaintenanceControls.tsx');
import {setUiLanguage} from '../src/ui-language.mjs';

const render=(component,props)=>renderToStaticMarkup(React.createElement(component,props));
function pageProps(total){return {page:'configuration',record:{id:'original-workspace',repository:'C:/tutorial',api_prefix:'/workspace/original/api/',token:'NEVER_RENDER_CAPABILITY'},
 state:{request_budget:total,requests_used:13,requests_reserved_unknown:7,requests_remaining:total-20,policy:{allowed_paths:['calculator.py'],allowed_tests:[]}},
 hub:{request_budget:500,monitors:[],jobs:[]},rows:[],request:()=>{throw Error('render must not dispatch');},refresh:()=>{},navigate:()=>{},openOriginal:()=>{},connect:()=>{},selectTask:()=>{}};}

test('configuration explains the no-description commit route while retaining original allowance and unknown values',()=>{
 for(const total of [50,125,500]){
  const p=pageProps(total),before=JSON.stringify(p.state),html=render(WorkbenchPages,p);
  assert.match(html,/无需先填写问题描述/);assert.match(html,/设置提交自动维护 →/);
  assert.match(html,new RegExp('aria-label="新的请求总上限"[^>]*value="'+total+'"'));
  assert.doesNotMatch(html,/aria-label="新的请求总上限"[^>]*max="100"/);
  assert.match(html,/已用 13/);assert.match(html,/预留或待核对 7/);
  assert.match(html,/合法上限由服务的共享请求配置校验/);
  assert.doesNotMatch(html,/NEVER_RENDER_CAPABILITY/);assert.equal(JSON.stringify(p.state),before);
 }
});

test('reproduced failure exposes repair directly when optional business expectations are empty',()=>{
 const p={hub:{monitors:[],jobs:[]},request:()=>{throw Error('unexpected dispatch');},refresh:()=>{}};
 const event={id:'original-event',monitor_id:'original-monitor',sha:'original-sha',can_propose_validation:true,can_prepare_repair:true,
  root_preparation:{status:'reproduced',model_requests:0,proposed_policy:{allowed_paths:['calculator.py']},environment:{checks:[]}}};
 const before=JSON.stringify(event),html=render(RootMonitorPreparation,{p,event});
 assert.match(html,/补充业务验收案例（可选）/);assert.match(html,/无需你先描述问题/);
 assert.match(html,/预览本次修复范围与额度/);assert.match(html,/<textarea[^>]*><\/textarea>/);
 assert.doesNotMatch(html,/<textarea[^>]*required/);assert.equal(JSON.stringify(event),before);
});

test('the same entry guidance has explicit English copy without changing execution fields',()=>{
 try{
  setUiLanguage('en');const html=render(WorkbenchPages,pageProps(125));
  assert.match(html,/Configure automatic commit maintenance/);assert.match(html,/no problem description is required/);
  assert.match(html,/shared request configuration/);assert.doesNotMatch(html,/NEVER_RENDER_CAPABILITY/);
 }finally{setUiLanguage('zh-CN');}
});

test('repository overview separates manual and monitoring allowances and shows both hold states',()=>{
 const p=pageProps(100);
 p.page='overview';Object.assign(p.state,{repository:p.record.repository,requests_used:0,
  requests_remaining:100,requests_reserved_unknown:0,requests_reserved_active:0,requests_ended_unknown:0});
 p.hub.monitors=[
  {id:'monitor',spec:{path:p.record.repository,branch:'main'},request_budget:100,requests_used:2,
   requests_remaining:95,requests_reserved_unknown:3,requests_reserved_active:2,requests_ended_unknown:1},
  {id:'unrelated',spec:{path:'C:/other',branch:'foreign'},request_budget:100,requests_used:99,requests_remaining:1}];
 const html=render(WorkbenchPages,p);
 assert.match(html,/手工任务 · 当前模型会话.*已用 0 .*可用 100/);
 assert.match(html,/监控任务 · main.*已用 2 · 进行中预留 2 · 已结束待核对 1 · 可用 95/);
 assert.match(html,/查看监控额度/);assert.doesNotMatch(html,/foreign|已用 99/);
 const active=p.hub.monitors[0];
 Object.assign(active,{requests_reserved_active:3,requests_ended_unknown:0,
  model_authorization:{effective_status:'unknown_consumption'}});
 const inFlight=render(WorkbenchPages,p);
 assert.match(inFlight,/模型调用正在结算前占用额度/);
 assert.doesNotMatch(inFlight,/监控模型授权需要核对|监控原账存在未知消费/);
 p.state.budget_monitor_id='monitor';
 assert.doesNotMatch(render(WorkbenchPages,p),/手工任务 · 当前模型会话/);
});
