import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {RunOperations}=await import('../src/RunOperations.tsx');
const {WorkbenchPages}=await import('../src/WorkbenchPages.tsx');
const {DeliveryInbox}=await import('../src/DeliveryInbox.tsx');
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
const record={id:'workspace',repository:'fixture',api_prefix:'/api/workspaces/workspace/',token:'SECRET_CAPABILITY'};
const idle={repository:'fixture',runtime:{code_changed:false},jobs:[],runs:[],plans:[],requests_remaining:4,
 requests_reserved_unknown:0,issue_workbench:{executions:[],approvals:[]},recovery_cards:[]};
function props(state=idle){return {page:'overview',record,state,hub:{monitors:[]},rows:[],
 request:()=>{throw Error('display issued a request');},refresh:async()=>{},navigate:()=>{},openOriginal:()=>{},connect:()=>{},selectTask:()=>{}};}
function nodes(node,predicate){
 if(!node||typeof node!=='object')return [];
 if(Array.isArray(node))return node.flatMap(n=>nodes(n,predicate));
 return [...(predicate(node)?[node]:[]),...nodes(node.props?.children,predicate)];
}

test('actual run summary and original buttons agree for manual, auto-only and dual continuation',()=>{
 const run={run_id:'run',status:'rolled_back',revision:'run-revision'};
 for(const modes of [['approval'],['auto'],['approval','auto']]){
  const actions=modes.map(mode=>({action:'resume_run',mode,revision:'original-revision'}));
  const state={...idle,runs:[run],recovery_cards:[{kind:'run',id:'run',status:'available',actions}]};
  const html=render(RunOperations,{...props(state),run});
  assert.equal(html.includes('>继续此任务，先生成方案</button>'),modes.includes('approval'));
  assert.equal(html.includes('>在原授权范围内继续修复</button>'),modes.includes('auto'));
  if(modes.length===1&&modes[0]==='auto'){
   assert.match(html,/自主模式不等于取得人工审批/);assert.doesNotMatch(html,/继续后仍需本轮新审批/);
  }else assert.match(html,/审批/);
  assert.match(html,/当前运行的状况与下一步/);assert.doesNotMatch(html,/SECRET_CAPABILITY/);
 }
});

test('ambiguous cards, unsupported actions and foreign or unread repository cannot show recovery controls',()=>{
 const run={run_id:'run',status:'failed',revision:'run-revision'};
 const card={kind:'run',id:'run',status:'available',actions:[{action:'resume_run',mode:'auto',revision:'original'}]};
 for(const patch of [
  {recovery_cards:[card,card]},
  {recovery_cards:[{...card,actions:[{action:'delete_everything',revision:'invented'}]}]},
  {repository:'foreign',recovery_cards:[card]},
  {repository:undefined,recovery_cards:[card]},
 ]){
  const html=render(RunOperations,{...props({...idle,runs:[run],...patch}),run});
  const buttons=[...html.matchAll(/<button[^>]*>(.*?)<\/button>/g)].map(m=>m[1]);
  assert.ok(!buttons.some(s=>/继续|delete_everything/.test(s)),buttons.join(','));
 }
});

test('actual overview record buttons navigate to exact task and delivery without sending operations',()=>{
 let calls=0;const selected=[],navigated=[];
 const run={run_id:'run',status:'candidate_ready'};
 const state={...idle,runs:[run]};
 const p={...props(state),rows:[{id:'task-row',runs:[run],plans:[],selection:{kind:'run',id:'run'}}],
  request:()=>{calls++;throw Error('unexpected operation');},selectTask:id=>selected.push(id),navigate:page=>navigated.push(page)};
 // A run in recovery selects its exact task; a candidate opens original delivery.
 p.state={...state,recovery_cards:[{kind:'run',id:'run',status:'repair_pending',actions:[]}]};
 let tree=WorkbenchPages(p);
 let buttons=nodes(tree,n=>n.type==='button'&&n.props.children==='查看对应记录');
 assert.equal(buttons.length,2);buttons[0].props.onClick();buttons[1].props.onClick();
 assert.deepEqual(selected,['task-row']);assert.deepEqual(navigated,['delivery']);
 // Duplicate matches cannot silently select the first task.
 tree=WorkbenchPages({...p,rows:[...p.rows,{...p.rows[0],id:'duplicate-row'}]});
 buttons=nodes(tree,n=>n.type==='button'&&n.props.children==='查看对应记录');buttons[0].props.onClick();
 assert.deepEqual(selected,['task-row']);assert.equal(navigated.at(-1),'tasks');
 assert.equal(calls,0);
 const html=render(WorkbenchPages,p);assert.match(html,/下一步/);assert.match(html,/查看对应记录/);
});

test('actual delivery renders local, remote PR, merged CI and observer stages separately and read-only',()=>{
 const row={id:'delivery',title:'原任务',status:'candidate_review',origin:{kind:'issue',repository:'fixture',run_id:'run',issue_number:7},
  budget:{request_budget:4,requests_used:1,requests_remaining:3,requests_reserved_unknown:0},
  evidence:{checks_passed:true,effective_test_count_known:true,tests_run:1,base_sha:'original-sha',local_publication:{integration_branch:'integration/run',integration_commit:'local-sha'}},
  delivery:{push:{status:'remote-ref-verified'},pr:{status:'awaiting-review'},acceptance:{status:'delivery-verified'},
   acceptance_watch:{status:'checks_failed'}}};
 const html=render(DeliveryInbox,{p:{...props(),delivery:{complete:true,items:[row]}}});
 for(const phrase of ['本地分支已有发布记录','远端 PR 等待审阅','指定默认版本与 CI 已核验','默认版本最新 CI 未通过','未来版本需重新核验'])assert.ok(html.includes(phrase),phrase);
 assert.doesNotMatch(html,/>修复已交付</);assert.doesNotMatch(html,/SECRET_CAPABILITY/);
});

test('overview shows pending settlement separately from unknown outcomes and rejects missing identity',()=>{
 const html=render(WorkbenchPages,{...props({...idle,active:'job',requests_reserved_unknown:3,
  planning_attempts:[{id:'attempt',status:'started',reserved:2}]})});
 assert.match(html,/模型调用正在结算前占用额度/);assert.match(html,/原模型消费待核对/);
 const unread=render(WorkbenchPages,{...props({...idle,repository:undefined})});
 assert.match(unread,/仓库记录尚未对应当前选择/);assert.doesNotMatch(unread,/当前没有已展示的待处理项/);
});

test('overview delivery button opens its unique original workspace and never borrows a duplicate feed identity',()=>{
 const opened=[],navigated=[];
 const row={id:'d',origin:{repository:'fixture',workspace_id:'original-workspace'},status:'needs_review'};
 const p={...props(),delivery:{complete:true,items:[row]},reviewDelivery:value=>opened.push(value),navigate:page=>navigated.push(page)};
 const button=nodes(WorkbenchPages(p),n=>n.type==='button'&&n.props.children==='查看对应记录')[0];
 button.props.onClick();assert.deepEqual(opened,[row]);
 const duplicate=nodes(WorkbenchPages({...p,delivery:{complete:true,items:[row,row]}}),n=>n.type==='button'&&n.props.children==='查看对应记录')[0];
 duplicate.props.onClick();assert.deepEqual(opened,[row]);assert.deepEqual(navigated,['delivery']);
});
