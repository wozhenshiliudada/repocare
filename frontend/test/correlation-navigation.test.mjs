import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ownerNavigation} from '../src/correlation-navigation.mjs';

const origin='http://127.0.0.1:28555';
const workspaceId='a'.repeat(32),otherId='b'.repeat(32);
const record={id:workspaceId,repository:'C:/original'};
const other={id:otherId,repository:'C:/other'};
function hub(){
 return {workspaces:[record,other],monitors:[
  {id:'monitor',spec:{path:'C:/original'},events:[{id:'event',launch_url:origin+'/#workspace='+workspaceId}]},
  {id:'foreign',spec:{path:'C:/other'},events:[{id:'foreign-event'}]},
 ]};
}
const issueRow={id:'issue-row',selection:{kind:'issue',id:'execution',number:7}};
const callbacks={selectTask(){},selectWorkspace(){},navigate(){}};
const issue=patch=>({id:'issue-link',status:'linked',owner_source:{kind:'issue',workspace_id:workspaceId,
 investigation_id:'investigation',execution_id:'execution',...patch}});
const monitor=patch=>({id:'monitor-link',status:'linked',owner_source:{kind:'monitor',
 monitor_id:'monitor',event_id:'event',...patch}});
function resolve(link,{rows=[issueRow],hubs=hub(),cbs=callbacks,rec=record}={}){
 return ownerNavigation({record:rec,hub:hubs,rows,link,origin,callbacks:cbs});
}

test('an exact issue owner resolves one already-read task row',()=>{
 const value=resolve(issue());
 assert.equal(value.showButton,true);assert.equal(value.available,true);
 assert.deepEqual(value.action,{kind:'task',id:'issue-row'});
 assert.equal(value.buttonLabel,'查看原负责人任务');assert.match(value.hint,/不调用模型/);
});
test('issue resolution falls back to one exact run row or an exposed investigation row',()=>{
 const run=resolve(issue({execution_id:undefined,run_id:'run'}),
  {rows:[{id:'run-row',runs:[{run_id:'run'}]}]});
 assert.equal(run.available,true);assert.deepEqual(run.action,{kind:'task',id:'run-row'});
 const investigation=resolve(issue({execution_id:undefined,run_id:undefined}),
  {rows:[{id:'investigation-row',selection:{kind:'issue',id:'execution-2'},investigation_id:'investigation'}]});
 assert.equal(investigation.available,true);
 assert.deepEqual(investigation.action,{kind:'task',id:'investigation-row'});
 const bySelection=resolve(issue({execution_id:undefined,run_id:undefined}),
  {rows:[{id:'selection-row',selection:{kind:'issue',id:'investigation'}}]});
 assert.equal(bySelection.available,true);assert.deepEqual(bySelection.action,{kind:'task',id:'selection-row'});
});
test('an owner in another workspace only switches to that exact hub workspace',()=>{
 const value=resolve(issue({workspace_id:otherId}));
 assert.equal(value.available,true);assert.deepEqual(value.action,{kind:'workspace',id:otherId});
 assert.match(value.hint,/只读切换已登记的原工作区/);
});
test('missing or unregistered workspace and missing execution never invent a target',()=>{
 for(const [owner,rows,expected] of [
  [issue({workspace_id:''}),[issueRow],/缺少明确工作区/],
  [issue({workspace_id:'ghost'}),[issueRow],/尚未在本机登记/],
  [issue(),[],/没有该原执行记录/],
  [issue({execution_id:undefined,run_id:'missing'}),[issueRow],/没有该原运行记录/],
  [issue({execution_id:undefined,run_id:undefined}),[issueRow],/没有暴露可核对的调查标识/],
 ]){
  const value=resolve(owner,{rows});
  assert.equal(value.available,false,expected);assert.equal(value.action,null,expected);
  assert.match(value.reason,/下一步/,expected);assert.match(value.reason,expected);
 }
});
test('multiple exact issue matches stay unavailable instead of selecting one',()=>{
 const value=resolve(issue(),{rows:[issueRow,{...issueRow,id:'issue-row-2'}]});
 assert.equal(value.available,false);assert.equal(value.action,null);
 assert.match(value.reason,/多个相同原执行记录/);assert.match(value.reason,/不选择任意一份/);
});
test('duplicate registered workspaces remain ambiguous',()=>{
 const hubs=hub();hubs.workspaces=[other,{...other}];
 const value=resolve(issue({workspace_id:otherId}),{hubs});
 assert.equal(value.available,false);assert.match(value.reason,/多个相同身份/);
});
test('monitor owner uses only the exact event and enters a registered workspace',()=>{
 const current=resolve(monitor());
 assert.equal(current.available,true);assert.deepEqual(current.action,{kind:'events'});
 assert.match(current.hint,/不代表已打开一个原任务/);
 const hubs=hub();hubs.monitors.push({id:'other-monitor',spec:{path:'C:/other'},
  events:[{id:'other-event',launch_url:origin+'/#workspace='+otherId}]});
 const foreign=resolve(monitor({monitor_id:'other-monitor',event_id:'other-event'}),{hubs});
 assert.equal(foreign.available,true);assert.deepEqual(foreign.action,{kind:'workspace',id:otherId});
 assert.equal(foreign.action.id,otherId);
});
test('monitor events without a workspace stay on the current page only when they belong here',()=>{
 const hubs=hub();hubs.monitors.push({id:'plain',spec:{path:'C:/original'},events:[{id:'plain-event'}]});
 const visible=resolve(monitor({monitor_id:'plain',event_id:'plain-event'}),{hubs});
 assert.equal(visible.available,true);assert.deepEqual(visible.action,{kind:'events'});
 const foreign=resolve(monitor({monitor_id:'foreign',event_id:'foreign-event'}));
 assert.equal(foreign.available,false);assert.equal(foreign.action,null);
 assert.match(foreign.reason,/另一仓库或原工作区尚未登记/);
});
test('missing or ambiguous monitor identities are unavailable',()=>{
 for(const [owner,hubs,expected] of [
  [monitor({event_id:''}),hub(),/缺少监控或事件标识/],
  [monitor({monitor_id:'missing'}),hub(),/没有该原监控记录/],
  [monitor({event_id:'missing'}),hub(),/没有该原事件记录/],
  [monitor(),(()=>{const value=hub();value.monitors[0].events.push({...value.monitors[0].events[0]});return value;})(),/多个相同事件标识/],
  [monitor(),(()=>{const value=hub();value.monitors.push({...value.monitors[0]});return value;})(),/多个相同标识/],
 ]){
  const value=resolve(owner,{hubs});
  assert.equal(value.available,false,expected);assert.equal(value.action,null,expected);
  assert.match(value.reason,expected);
 }
});
test('non-linked, owner-less and unknown-owner links never expose a usable jump',()=>{
 const blocked=resolve({id:'link',status:'blocked',owner_source:undefined});
 assert.equal(blocked.showButton,false);assert.equal(blocked.available,false);
 assert.match(blocked.reason,/下一步/);assert.match(blocked.reason,/不提供原负责人跳转/);
 const stale=resolve({id:'link',status:'stale',owner_source:{kind:'issue',workspace_id:workspaceId,
  investigation_id:'investigation',execution_id:'execution'}});
 assert.equal(stale.showButton,false);assert.equal(stale.available,false);
 assert.match(stale.reason,/不提供原负责人跳转/);
 const noOwner=resolve({id:'link',status:'linked'});
 assert.equal(noOwner.showButton,false);assert.equal(noOwner.available,false);
 assert.match(noOwner.reason,/尚未读取到原负责人记录/);
 const unknown=resolve({id:'link',status:'linked',owner_source:{kind:'other',workspace_id:workspaceId}});
 assert.equal(unknown.showButton,true);assert.equal(unknown.available,false);
 assert.equal(unknown.buttonLabel,'查看原负责人记录');assert.match(unknown.reason,/不在当前支持范围/);
});
test('missing callbacks disable an otherwise exact target and explain the next step',()=>{
 const task=resolve(issue(),{cbs:{selectWorkspace(){},navigate(){}}});
 assert.equal(task.available,false);assert.match(task.reason,/任务选择入口/);
 const workspace=resolve(issue({workspace_id:otherId}),{cbs:{selectTask(){},navigate(){}}});
 assert.equal(workspace.available,false);assert.match(workspace.reason,/工作区切换入口/);
 const events=resolve(monitor(),{cbs:{selectTask(){},selectWorkspace(){}}});
 assert.equal(events.available,false);assert.match(events.reason,/监控导航入口/);
});
test('without a current workspace identity neither issue nor monitor owner is opened',()=>{
 for(const value of [resolve(issue(),{rec:null}),resolve(monitor(),{rec:null})]){
  assert.equal(value.available,false);assert.equal(value.action,null);
  assert.match(value.reason,/当前尚未选择有明确身份的工作区/);
 }
});
