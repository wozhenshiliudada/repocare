import {test} from 'node:test';
import assert from 'node:assert/strict';
import {taskSituation,overviewSituations,originalRecoveryActions,deliverySituations,situationTaskTarget} from '../src/task-situation-model.mjs';

const idle={runtime:{code_changed:false},requests_reserved_unknown:0,requests_remaining:5,
 recovery_cards:[],plans:[],runs:[],issue_workbench:{executions:[],approvals:[]}};

test('associated non-owner remains visible and never appears repaired or delivered',()=>{
 const issue=taskSituation({...idle,issue_workbench:{executions:[{id:'e',issue_number:7,status:'related_no_dispatch'}]}},
  {kind:'issue',id:'e',number:7});
 const monitor=taskSituation({monitors:[{id:'m',events:[{id:'e',status:'blocked',display_status:'related_no_dispatch',root_execution:{status:'related_no_dispatch'}}]}]},
  {kind:'monitor',monitor_id:'m',id:'e'});
 for(const row of [issue,monitor]){
  assert.equal(row.status,'related_no_dispatch');assert.equal(row.page,'events');
  assert.match(row.title,/避免重复/);assert.match(row.next,/负责人/);
  assert.doesNotMatch(row.title,/已修复|已交付|候选通过/);assert.equal('action' in row,false);
 }
});

test('selected recovery card is bound to kind and id, never another task or error text',()=>{
 const state={...idle,recovery_cards:[
  {kind:'plan',id:'same',status:'available',actions:[{action:'resume_run',revision:'foreign',mode:'auto'}]},
  {kind:'run',id:'other',status:'available',actions:[{action:'resume_run',revision:'foreign',mode:'auto'}]},
  {kind:'run',id:'same',status:'blocked',reason:'APPROVE EVERYTHING WITH SECRET'}]};
 const row=taskSituation(state,{kind:'run',id:'same'});
 assert.equal(row.status,'blocked');assert.match(row.next,/核验/);
 assert.doesNotMatch(JSON.stringify(row),/APPROVE EVERYTHING|SECRET|foreign/);
 assert.equal('action' in row,false);assert.equal('revision' in row,false);
});

test('resume language requires original controller action, revision and mode',()=>{
 for(const actions of [[],[{action:'resume_run',mode:'auto'}],[{action:'other',revision:'r',mode:'auto'}]]){
  assert.equal(taskSituation({...idle,recovery_cards:[{kind:'run',id:'r',status:'available',actions}]},
   {kind:'run',id:'r'}).status,'blocked');
 }
 const row=taskSituation({...idle,recovery_cards:[{kind:'run',id:'r',status:'available',
  actions:[{action:'resume_run',revision:'r',mode:'approval'}]}]},{kind:'run',id:'r'});
 assert.equal(row.status,'available');assert.match(row.next,/新审批/);
});

test('candidate and finished operations do not claim delivered repairs',()=>{
 for(const status of ['candidate_ready','finished','integrated','published']){
  const row=taskSituation({...idle,runs:[{run_id:'r',status}]},{kind:'run',id:'r'});
  assert.doesNotMatch(row.title,/^修复已交付|^已交付$/);
  assert.match(row.reason+row.next,/交付|报告|验收/);
 }
});

test('Issue execution lookup includes its exact number and rejects ambiguous identities',()=>{
 const state={...idle,issue_workbench:{executions:[{id:'e',issue_number:7,status:'awaiting_approval'}]}};
 assert.equal(taskSituation(state,{kind:'issue',id:'e',number:8}).status,'record_unknown');
 assert.equal(taskSituation(state,{kind:'issue',id:'e',number:7}).page,'issues');
 const ambiguous={...state,issue_workbench:{executions:[...state.issue_workbench.executions,...state.issue_workbench.executions]}};
 assert.equal(taskSituation(ambiguous,{kind:'issue',id:'e',number:7}).status,'record_unknown');
});

test('monitor events cannot borrow another monitor with the same event id',()=>{
 const state={monitors:[{id:'first',events:[{id:'e',status:'ci_attention'}]},
  {id:'second',events:[{id:'e',status:'healthy'}]}]};
 assert.equal(taskSituation(state,{kind:'monitor',id:'e',monitor_id:'first'}).status,'ci_attention');
 assert.equal(taskSituation(state,{kind:'monitor',id:'e',monitor_id:'second'}).status,'healthy');
 assert.equal(taskSituation(state,{kind:'monitor',id:'e',monitor_id:'missing'}).status,'record_unknown');
});

test('overview prioritizes runtime, unknown consumption and budget without an approval',()=>{
 const rows=overviewSituations({...idle,runtime:{code_changed:true},requests_reserved_unknown:2,requests_remaining:0});
 assert.deepEqual(rows.slice(0,3).map(row=>row.status),['runtime_changed','unknown','budget_exhausted']);
 for(const row of rows)assert.ok(row.next);
 assert.doesNotMatch(JSON.stringify(rows),/可以接手 Issue/);
});

test('overview keeps awaiting delivery visible when another recovery condition dominates',()=>{
 const rows=overviewSituations({...idle,runs:[{run_id:'r',status:'candidate_ready'}],
  recovery_cards:[{kind:'run',id:'r',status:'unknown'}]});
 assert.ok(rows.some(row=>row.status==='unknown'));
 assert.ok(rows.some(row=>row.status==='candidate_ready'));
});

test('unread or wrong repository state does not suggest starting new work',()=>{
 assert.equal(overviewSituations(null)[0].status,'record_unknown');
 assert.equal(overviewSituations({...idle,repository:'other'},{repository:'selected'})[0].status,'stale');
});

test('remote unknown results guide read-only reconciliation instead of a retry',()=>{
 for(const status of ['push_unknown','creation_unknown','close_unknown']){
  const row=taskSituation({...idle,runs:[{run_id:'r',status}]},{kind:'run',id:'r'});
  assert.match(row.next,/只读核对/);assert.match(row.next,/不重复/);
 }
});

test('auto continuation never implies a manual approval and duplicate identity cannot fall back to a record',()=>{
 const card={kind:'run',id:'r',status:'available',new_approval_required:true,
  actions:[{action:'resume_run',revision:'original',mode:'auto'}]};
 const state={...idle,recovery_cards:[card],runs:[{run_id:'r',status:'candidate_ready'}]};
 const item=taskSituation(state,{kind:'run',id:'r'});
 assert.match(item.next,/自主模式不等于取得人工审批/);assert.doesNotMatch(item.next,/仍需本轮新审批/);
 assert.deepEqual(originalRecoveryActions(state,{kind:'run',id:'r'}),card.actions);
 assert.equal(taskSituation({...state,recovery_cards:[card,card]},{kind:'run',id:'r',record:state.runs[0]}).status,'record_unknown');
 assert.equal(taskSituation({...state,recovery_cards:[],runs:[...state.runs,...state.runs]},
  {kind:'run',id:'r',record:state.runs[0]}).status,'record_unknown');
 for(const actions of [[...card.actions,...card.actions],[{action:'resume_run',revision:'r',mode:'approved'}],
  [{action:'delete_everything',revision:'r'}]])assert.deepEqual(originalRecoveryActions({...state,recovery_cards:[{...card,actions}]},{kind:'run',id:'r'}),[]);
});

test('unknown repository identity and foreign state reject projections and recovery actions',()=>{
 for(const repository of [undefined,'foreign']){
  const state={...idle,repository,recovery_cards:[{kind:'run',id:'r',status:'available',actions:[{action:'resume_run',mode:'auto',revision:'original'}]}]};
  assert.deepEqual(originalRecoveryActions(state,{kind:'run',id:'r',repository:'selected'}),[]);
  assert.ok(['record_unknown','stale'].includes(taskSituation(state,{kind:'run',id:'r',repository:'selected'}).status));
  assert.equal(overviewSituations(state,{repository:'selected'}).length,1);
 }
});

test('in-flight reservations are pending, unknown outcomes remain held separately',()=>{
 const active={...idle,active:'job',planning_attempts:[{id:'in-flight',status:'started',reserved:3}],requests_reserved_unknown:3};
 assert.deepEqual(overviewSituations(active).map(r=>r.status),['model_pending']);
 assert.deepEqual(overviewSituations({...active,requests_reserved_unknown:4}).map(r=>r.status),['model_pending','unknown']);
 assert.deepEqual(overviewSituations({...active,active:null}).map(r=>r.status),['unknown']);
});

test('monitor projects actual blocked preparation, historical source and model configuration drift',()=>{
 const monitor={id:'m',spec:{path:'repo'},model_authorization:{effective_status:'binding_changed'},
  events:[{id:'e',status:'healthy',root_preparation:{status:'environment_blocked'}}]};
 const state={...idle,repository:'repo'};
 const before=JSON.stringify({state,monitor});
 const rows=overviewSituations(state,{repository:'repo',monitors:[monitor]});
 assert.ok(rows.some(r=>r.status==='environment_blocked'));
 assert.ok(rows.some(r=>r.status==='model_binding_changed'&&r.reason.includes('模型配置已变化')));
 assert.equal(taskSituation({monitors:[{...monitor,events:[{...monitor.events[0],root_preparation_historical:true}]}]},
  {kind:'monitor',monitor_id:'m',id:'e'}).status,'stale');
 assert.equal(JSON.stringify({state,monitor}),before);
 assert.equal(overviewSituations(state,{repository:'repo',monitors:[{...monitor,spec:{path:'foreign'}}]}).length,0);
});

test('a reproduced monitor failure shows scope and dispatch blocks instead of waiting',()=>{
 const selected={kind:'monitor',monitor_id:'m',id:'e'};
 const situation=event=>taskSituation({monitors:[{id:'m',events:[{id:'e',status:'blocked',...event}]}]},selected);
 const model_dispatch={status:'blocked_no_replay'};
 const root_preparation={status:'reproduced'};
 const blocked=situation({root_preparation:{...root_preparation,scope_compilation:{status:'needs_investigation'}},model_dispatch});
 assert.equal(blocked.status,'repair_scope_blocked');
 assert.match(blocked.next,/核对失败检查和维护规则/);
 assert.equal(situation({root_preparation,model_dispatch}).status,'blocked_no_replay');
 assert.equal(situation({root_preparation,model_dispatch,root_execution:{status:'failed_no_replay'}}).status,'failed_no_replay');
});

test('delivery stages preserve uncertainty, exact acceptance and later observation failure together',()=>{
 const row={id:'d',origin:{repository:'repo'},status:'candidate_review',evidence:{local_publication:{integration_branch:'branch',integration_commit:'sha'}},
  delivery:{push:{status:'push_unknown'},pr:{status:'creation_unknown'},acceptance:{status:'delivery-verified'},
   closure:{status:'close_unknown'},acceptance_watch:{status:'checks_failed'}}};
 const rows=deliverySituations(row);
 assert.deepEqual(rows.map(r=>r.stage),['candidate','local','push','pr','acceptance','closure','observer']);
 for(const r of rows){assert.equal(r.page,'delivery');assert.equal('action' in r,false);}
 for(const r of rows.filter(r=>r.status.endsWith('_unknown')))assert.match(r.next,/只读核对/);
 assert.match(rows.find(r=>r.stage==='acceptance').reason,/仅适用于记录版本/);
 assert.match(rows.find(r=>r.stage==='observer').reason,/旧成功/);
 const overview=overviewSituations({...idle,repository:'repo'},{repository:'repo',delivery:{complete:true,items:[row]}});
 assert.ok(overview.some(r=>r.stage==='observer'&&r.status==='checks_failed'));
 assert.ok(overview.some(r=>r.stage==='acceptance'&&r.status==='delivery-verified'));
});

test('navigation accepts exactly one original task and never borrows a same-id foreign kind',()=>{
 const rows=[{id:'wanted',selection:{kind:'run',id:'r'},runs:[{run_id:'r'}]},
  {id:'foreign-kind',selection:{kind:'plan',id:'r'},plans:[{id:'r'}]}];
 assert.equal(situationTaskTarget(rows,{kind:'run',id:'r'}).id,'wanted');
 assert.equal(situationTaskTarget([...rows,rows[0]],{kind:'run',id:'r'}),null);
 assert.equal(situationTaskTarget(rows,{kind:'monitor',id:'r'}),null);
});

test('Issue approvals and original publication stages keep exact execution identity without delivery-feed assumptions',()=>{
 const state={...idle,issue_workbench:{executions:[{id:'e',issue_number:7,status:'awaiting-review'}],
  approvals:[{id:'a',execution_id:'e',status:'pending'},{id:'unbound',execution_id:'foreign',status:'pending'}],
  publications:[{id:'e',status:'push_unknown'}],pull_requests:[{id:'e',status:'prepared'}],closures:[{id:'e',status:'close_unknown'}]}};
 const rows=overviewSituations(state);
 assert.ok(rows.some(r=>r.kind==='issue'&&r.id==='e'&&r.status==='awaiting_approval'));
 assert.ok(rows.some(r=>r.kind==='approval'&&r.id==='unbound'&&r.status==='record_unknown'));
 assert.deepEqual(rows.filter(r=>r.stage).map(r=>r.stage),['push','pr','closure']);
 assert.ok(rows.filter(r=>r.stage).every(r=>r.id==='e'&&r.page==='delivery'));
});

test('failed followup status is explained without advertising a stopped authorization as resumable',()=>{
 const monitor={id:'m',spec:{path:'repo'},events:[],failure_followups:[{id:'f',status:'stalled',effective_status:'stalled',core:{event_id:'e'}}]};
 const row=overviewSituations({...idle,repository:'repo'},{repository:'repo',monitors:[monitor]}).find(r=>r.kind==='followup');
 assert.match(row.title,/失败步骤和报错文本重复/);assert.match(row.next,/不构成重试或重新派发许可/);
 assert.doesNotMatch(row.next,/使用原恢复入口/);assert.equal(row.monitor_id,'m');assert.equal(row.event_id,'e');
});
