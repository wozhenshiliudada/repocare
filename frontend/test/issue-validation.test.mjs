import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {IssueValidation}=await import('../src/IssueValidation.tsx');
const {TestTable}=await import('../src/EvidenceView.tsx');
const {IssueManager}=await import('../src/IssueManager.tsx');
const {IssueWatchControl}=await import('../src/IssueWatchControl.tsx');
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
const base={investigation:{id:'original-issue'},disabled:false,prepared:false,send:async()=>{throw Error('unexpected action');}};
const preview={revision:'original-preview',budget:{requests_remaining:4}};
const validation={status:'awaiting_validation_review',revision:'original-review',proposal:{requirement:'sum(2,3)=5',rationale:'Issue requirement',limitations:'one case'},
 contract:{cases:[{id:'sum',module:'calculator.py',callable:'add',args:[2,3],kwargs:{},expect:{kind:'json',value:5}}]}};
test('natural-language UI renders case oracle without requiring file or command input',()=>{
 let calls=0;const html=renderToStaticMarkup(React.createElement(IssueValidation,{...base,validation,send:async()=>{calls++;}}));
 assert.match(html,/输入/);assert.match(html,/预期/);assert.match(html,/2,3/);assert.match(html,/Issue requirement/);
 assert.doesNotMatch(html,/<textarea|type="text"|checked=""/);assert.equal(calls,0);
});
test('proposal and review require separate explicit confirmation and preserve original revision',async()=>{
 const calls=[],send=async(action,payload)=>{calls.push({action,payload});};
 const ui=component({...base,preview,send});
 await ui.button('让Agent提议验收案例').props.onClick();assert.equal(calls.length,0);
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();
 const click=ui.button('让Agent提议验收案例').props.onClick;await Promise.all([click(),click()]);ui.render();
 assert.deepEqual(calls,[{action:'issue_validation_propose',payload:{investigation_id:'original-issue',revision:preview.revision}}]);
 ui.update({...base,validation,send});assert.equal(ui.checkbox().props.checked,false);
 await ui.button('确认本次验收案例').props.onClick();assert.equal(calls.length,1);
 ui.checkbox().props.onChange({target:{checked:true}});ui.render();
 await ui.button('确认本次验收案例').props.onClick();
 assert.deepEqual(calls[1],{action:'issue_validation_review',payload:{investigation_id:'original-issue',revision:validation.revision,decision:'accept'}});
});
test('changed Issue or review resets confirmation; uncertain result offers only original reconciliation',async()=>{
 const calls=[],send=async(action,payload)=>{calls.push({action,payload});};
 const ui=component({...base,validation,send});ui.checkbox().props.onChange({target:{checked:true}});ui.render();
 ui.update({...base,investigation:{id:'changed'},validation:{...validation,revision:'changed'},send});
 assert.equal(ui.checkbox().props.checked,false);
 ui.update({...base,validation:{status:'interrupted_no_replay',revision:'interrupted'},send});
 assert.equal(ui.button('让Agent提议验收案例'),undefined);assert.match(ui.text(),/不会.*自动再次调用模型/);
 await ui.button('核对原会话并取回案例').props.onClick();
 assert.deepEqual(calls,[{action:'issue_validation_reconcile',payload:{investigation_id:'original-issue',revision:'interrupted'}}]);
});
test('prepared environment and clarification never offer an executable approval',()=>{
 for(const p of [{...base,prepared:true},{...base,validation:{status:'clarification_needed',proposal:{limitations:'Need expected output'}}}]){
  const ui=component(p);assert.equal(ui.button('让Agent提议验收案例'),undefined);assert.equal(ui.button('确认本次验收案例'),undefined);
 }
});
test('behavior table explicitly identifies historical fixed-case evidence',()=>{
 const html=renderToStaticMarkup(React.createElement(TestTable,{title:'candidate',tests:[{command:['controller-marker'],returncode:0,
 category:'passed',tests_run:1,command_kind:'controller_behavior_contract',behavior_receipt:{scope:'recorded_summary_only'}}]}));
 assert.match(html,/固定案例/);assert.match(html,/已保存/);assert.match(html,/不表示.*重新运行/);
});
test('actual IssueManager queued preview is bound to investigation, not absent issue-number argument',async()=>{
 const record={id:'workspace',api_prefix:'/api/workspaces/original/',token:'original-token'};
 const investigation={id:'a'.repeat(64),status:'investigated',snapshot:{status:'ready',head_sha:'b'.repeat(40),issue:{number:7}}};
 const state={jobs:[],runtime:{code_changed:false},issue_workbench:{configured:true,investigations:[investigation],executions:[],approvals:[],validations:[]}};
 let jobId;const calls=[];
 const props={record,state,focus:7,items:[],request:async(path,token,body)=>{calls.push({path,token,body});jobId=body.request_id;return {id:jobId,status:'running'};},refresh:async()=>{}};
 const ui=component(props,IssueManager);
 let child=nodes(ui.tree(),n=>n.type===IssueValidation)[0];
 await child.props.send('issue_validation_preview',{investigation_id:investigation.id});ui.render();
 ui.update({...props,state:{...state,jobs:[{id:jobId,status:'finished',result:preview}]}});
 child=nodes(ui.tree(),n=>n.type===IssueValidation)[0];assert.deepEqual(child.props.preview,preview);
 assert.equal(calls.length,1);assert.equal(calls[0].path,record.api_prefix+'actions/issue_validation_preview');
 assert.equal(calls[0].token,record.token);assert.equal(calls[0].body.investigation_id,investigation.id);
 assert.equal('number' in calls[0].body,false);
});
test('actual watch waiting and interrupted states explain next step without stale result status winning',()=>{
 const configuration={mode:'approval',spec:{read_roots:['.'],trusted_label_actors:['maintainer']},write_roots:['.']};
 for(const [status,expected] of [['awaiting_validation_review','等待你核对验收案例'],
  ['validation_interrupted_no_replay','案例提议中断'],['blocked_no_replay','本次受阻']]){
  const html=renderToStaticMarkup(React.createElement(IssueWatchControl,{record:{},
   state:{issue_workbench:{configured:true,configuration,watch:{status:'paused',admissions:[{
    id:'original',number:7,status,result_status:'awaiting-review'}]}}},request:async()=>{throw Error('display call');},refresh:async()=>{}}));
  assert.ok(html.includes(expected),html);assert.doesNotMatch(html,/Issue #7 · 候选待验收/);
 }
});
