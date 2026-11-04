import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {build} from 'vite';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import {workflows} from '../src/preview-workflows.mjs';

const out=path.resolve('.demo/render-'+process.pid);
await build({configFile:false,logLevel:'error',build:{ssr:'test/render-entry.ts',outDir:out,emptyOutDir:false,
 rolldownOptions:{external:['react','react-dom','react/jsx-runtime'],output:{entryFileNames:'render.mjs'}}}});
const {TaskReport,DeliveryProposal,TestTable,WorkbenchPages,LocalPreview,IssueEvidence,RunOperations,ContextOperations,NativeRepositoryControls,ActivityTimeline,CheckComparison,IssueManager,CiMonitorDiagnosis,RootMaintenanceControls,RootMonitorPreparation,MonitorRepairControls,MonitorHistory,MonitorHistoryView,MonitorContinuationControls,MonitorModelAuthorization,IssueWatchControl}=await import(pathToFileURL(path.join(out,'render.mjs')));
const render=(component,props)=>renderToStaticMarkup(React.createElement(component,props));

test('behavior receipts render fixed cases and preserve raw evidence without suggesting a shell command',()=>{
 const html=render(TestTable,{title:'行为验收',tests:[{command:['repocare:behavior-v1','a'.repeat(64)],
  command_kind:'controller_behavior_contract',category:'passed',returncode:0,tests_run:1,
  behavior_receipt:{contract:{requirement:'<script>unsafe text</script>'},counts:{total:1,passed:1}}}]});
 assert.match(html,/固定行为案例验收/);assert.match(html,/不是需要复制运行的终端命令/);
 assert.match(html,/行为案例通过/);assert.doesNotMatch(html,/<script>unsafe text/);
});
const {AsyncButton}=await import(pathToFileURL(path.join(out,'render.mjs')));
test('async feedback preserves idle approval, native submit and disabled guards',()=>{
 const html=render(AsyncButton,{type:'submit',disabled:true,'aria-label':'批准固定方案',children:'批准此方案'});
 assert.match(html,/^<button\b/);assert.match(html,/type="submit"/);assert.match(html,/disabled=""/);
 assert.match(html,/aria-label="批准固定方案"/);assert.match(html,/>批准此方案<\/button>$/);
 assert.doesNotMatch(html,/aria-busy|data-pending|button-spinner/);
 const sibling=render(AsyncButton,{disabled:true,feedback:{pending:true},children:'未点击的相邻操作'});
 assert.doesNotMatch(sibling,/aria-busy|data-pending|处理中/);
});
const {AcceptanceWatchControls,MonitorFailureFollowupControls}=await import(pathToFileURL(path.join(out,'render.mjs')));

test('CI successor consent is separate, initially unchecked, and pause remains available while busy',()=>{
 const date=new Date(Date.now()+3600000);date.setSeconds(0,0);const expiry=date.toISOString();
 const binding={monitor_id:'m',root_rules_sha256:'rules',mode:'manual'};
 const budget={request_budget:8,requests_used:2,requests_reserved_unknown:0,requests_remaining:6};
 const event={monitor_id:'m',id:'e',sha:'sha',root_execution:{finished_at:'done',run_id:'run',plan_id:'plan',binding:{ci_failure:{workflow_id:1}},model_authorization:{id:'model-grant'}}};
 const model={id:'model-grant',core_sha256:'model-core',active:true,core:{binding,expires_at:expiry}};
 const preview={revision:'revision',preview:{monitor_id:'m',event_id:'e',max_successors:1,expires_at:expiry,binding,budget,
  parent:{run_id:'run',head_sha:'sha'},model_authorization:{id:model.id,core_sha256:model.core_sha256},same_sha_only:true,may_charge:true}};
 const monitor={id:'m',enabled:true,root_rules:{sha256:'rules'},model_authorization:model,...budget};
 const p={hub:{jobs:[{action:'monitor_failure_preview',status:'finished',result:preview}],monitors:[monitor]},
  request:()=>{throw Error('render issued an action');},refresh:()=>{}};
 const html=render(MonitorFailureFollowupControls,{p,event});
 assert.match(html,/可能收费/);assert.match(html,/人工模式仍需新方案审批/);assert.match(html,/总上限 8/);
 assert.match(html,/同一 run 的更高 attempt 失败/);assert.match(html,/次数只是上限，不保证用满/);
 assert.match(html,/原模型许可无效时不能恢复/);assert.match(html,/不判断错误含义/);
 assert.match(html,/<input type="checkbox"\/>/);assert.match(html,/<button disabled="">确认启用新失败后继<\/button>/);
 const current={id:'successor',revision:'current',core:preview.preview,enabled:true,status:'active',children:[],error:''};
 const busy=render(MonitorFailureFollowupControls,{p:{...p,hub:{...p.hub,active:'job',monitors:[{...monitor,failure_followups:[current]}]}},event});
 assert.match(busy,/<button>暂停新失败后继<\/button>/);assert.match(busy,/剩余后继次数 1 \/ 1/);
 const paused=render(MonitorFailureFollowupControls,{p:{...p,hub:{...p.hub,monitors:[{...monitor,failure_followups:[{...current,enabled:false,status:'paused',effective_status:'restart_paused',error:'重启需要恢复'}]}]}},event});
 assert.match(paused,/重启后已暂停/);assert.match(paused,/停止或阻塞原因：重启需要恢复/);
 assert.match(paused,/<button>恢复原后继授权<\/button>/);
 for(const patch of [{active:false},{id:'replacement'},{core_sha256:'replacement'}]){
  const invalid=render(MonitorFailureFollowupControls,{p:{...p,hub:{...p.hub,monitors:[{...monitor,model_authorization:{...model,...patch},failure_followups:[{...current,enabled:false,status:'restart_paused'}]}]}},event});
  assert.match(invalid,/<button disabled="">恢复原后继授权<\/button>/);
 }
 assert.equal(render(MonitorFailureFollowupControls,{p,event:{monitor_id:'m',id:'new'}}),'');
});
test('continuous acceptance renders explicit read-only consent and original observation status without requests',()=>{
 const row={id:'original-run',origin:{monitor_id:'a'.repeat(16),event_id:'b'.repeat(16),run_id:'c'.repeat(32)},
  delivery:{pr:{receipt:{url:'https://github.com/example/project/pull/9'}}}};
 const p={hub:{jobs:[],active:null},request:()=>{throw Error('render issued an action');},refresh:()=>{}};
 const html=render(AcceptanceWatchControls,{p,row});
 assert.match(html,/持续观察合并后的版本和 CI/);assert.match(html,/预览持续观察范围/);
 assert.match(html,/不调用模型/);assert.doesNotMatch(html,/启用持续只读观察<\/button>/);
 const current={id:'watch',enabled:false,status:'restart_paused',observations:[],
  core:{expires_at:new Date(Date.now()+1800000).toISOString()}};
 const paused=render(AcceptanceWatchControls,{p,row:{...row,delivery:{...row.delivery,acceptance_watch:current}}});
 assert.match(paused,/重启后已暂停/);assert.match(paused,/恢复原范围只读观察/);
});
const record={id:'workspace',repository:'fixture',api_prefix:'/api/workspaces/workspace/',token:'SECRET_TEST_CAPABILITY'};
const state={repository:'fixture',runtime:{code_changed:false},jobs:[],plans:[],runs:[],requests_used:3,requests_remaining:20,requests_reserved_unknown:2,
 policy:{allowed_paths:['src/app.py']},issue_workbench:{configured:false,executions:[],approvals:[]},
 maintenance_memory:{status:'verified',observations:[],expected_revision:'rev'},memory_catalog:{enabled:true}};

test('overview explains unknown use, version drift and pending delivery without a misleading start prompt',()=>{
 const value={...state,runtime:{code_changed:true},requests_remaining:0,
  runs:[{run_id:'exact-run',status:'candidate_ready'}],recovery_cards:[]};
 const html=render(WorkbenchPages,{page:'overview',record,state:value,hub:{monitors:[]},rows:[],
  request:()=>{throw Error('render sent an action');},refresh:()=>{},navigate:()=>{},openOriginal:()=>{},
  connect:()=>{},selectTask:()=>{}});
 for(const phrase of ['运行代码已变化','原模型消费待核对','原请求额度暂不可用','候选通过，等待审阅交付','下一步'])
  assert.ok(html.includes(phrase),phrase);
 assert.doesNotMatch(html,/可以接手 Issue/);
});

test('selected run explains its recovery condition in Chinese and retains raw evidence separately',()=>{
 const value={...state,recovery_cards:[{kind:'run',id:'r',status:'budget_exhausted',
  reason:'Original budget exhausted',actions:[]}]};
 const html=render(RunOperations,{record,state:value,run:{run_id:'r',status:'failed',revision:'rev'},
  request:()=>{throw Error('render sent an action');},refresh:()=>{}});
 assert.match(html,/原请求额度暂不可用/);assert.match(html,/下一步/);
 assert.match(html,/原恢复卡、失败反馈与能力门/);
 assert.doesNotMatch(html,/<button[^>]*>.*继续修复/);
});

test('merged Issue page retains frontend execution history and guarded backend environment retry without actions',()=>{
 const value={...state,runtime:{...state.runtime,resource_limits:{workspace_max_bytes:1073741824,workspace_max_entries:50000,source_context_max_files:40},default_request_budget:500,max_output_tokens:128000,planning_timeout_seconds:3600},
  issue_workbench:{configured:true,investigations:[],environments:[],approvals:[],executions:[
   {id:'old-execution',run_id:'old-run',issue_number:7,status:'failed_no_replay',created_at:'2026-10-22'},
   {id:'new-execution',run_id:'new-run',issue_number:7,status:'failed_no_replay',created_at:'2026-10-23'}]}};
 const props={record,state:value,focus:7,items:[],request:()=>{throw Error('render sent an action');},refresh:()=>{}};
 const html=render(IssueManager,props);
 for(const text of ['原执行记录','old-execut','new-execut','检查环境阻塞后的续跑条件','运行容量与上限','50000','128000'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/确认环境已处理，使用原会话与额度续跑|批准创建这个远端新分支/);
 const changed=render(IssueManager,{...props,state:{...value,runtime:{...value.runtime,code_changed:true}}});
 assert.match(changed,/disabled=""[^>]*>检查环境阻塞后的续跑条件/);
 const delivery=render(IssueManager,{...props,view:'delivery'});assert.match(delivery,/交付的 Issue 执行/);
});

test('an unclaimed historical candidate offers claim before preparation',()=>{
 const html=render(TaskReport,{record,state,current:{id:'task',runs:[{run_id:'run',revision:'rev',status:'candidate_ready',claimed:false}]},request:()=>{},refresh:()=>{},openOriginal:()=>{}});
 assert.match(html,/核验并认领原运行/);assert.doesNotMatch(html,/封装候选并重新测试/);
});

test('Issue review UI preserves saved feedback by exact execution and never starts a revision while rendering',()=>{
 const execution={id:'execution-new',run_id:'run-new',issue_number:7,status:'failed_no_replay'};
 const approvals=[{id:'approval-old',execution_id:'execution-old',decision_kind:'request_changes',review_reason:'FOREIGN_REVIEW',status:'rejected'},
  {id:'approval-new',execution_id:'execution-new',decision_kind:'request_changes',review_reason:'Recheck Windows behavior',status:'rejected'}];
 const value={...state,issue_workbench:{configured:true,executions:[execution],approvals}};
 const props={record,state:value,focus:7,items:[],request:()=>{throw Error('render sent action');},refresh:()=>{}};
 const html=render(IssueManager,props);
 assert.match(html,/Recheck Windows behavior/);assert.doesNotMatch(html,/FOREIGN_REVIEW/);
 assert.match(html,/预览原任务的反馈修订条件/);
 assert.doesNotMatch(html,/确认使用原会话与额度生成修订方案/);
 const a={id:'pending',revision:'r',status:'pending',plan:{diagnosis:'review',risk:'low',edits:[]}};
 const pending=render(IssueManager,{...props,state:{...value,issue_workbench:{...value.issue_workbench,approvals:[a]}}});
 assert.match(pending,/批准此方案并测试/);assert.match(pending,/拒绝此方案/);assert.match(pending,/要求修改此方案/);
 assert.match(pending,/disabled=""[^>]*>要求修改此方案/);
});
test('missing memory receipt is unknown while an explicit empty receipt remains inspectable',()=>{
 const props={record,state,current:{id:'task',runs:[{run_id:'run',revision:'rev',status:'candidate_ready',claimed:false}]},request:()=>{},refresh:()=>{},openOriginal:()=>{}};
 assert.match(render(TaskReport,props),/未提供实际记忆装载回执/);
 props.current.runs[0].planning_origin={provider_evidence:{memory_load:[]}};
 const html=render(TaskReport,props);assert.match(html,/本次实际传入的记忆清单/);assert.doesNotMatch(html,/未提供实际记忆装载回执/);
});
test('local publication approval is absent if evidence hashes are not verified',()=>{
 const run={run_id:'run',revision:'rev',status:'candidate_ready',claimed:true,review:{status:'prepared',hash_matches:false,diff_hash_matches:true,review_sha256:'sha',tests:[]}};
 const html=render(TaskReport,{record,state,current:{id:'task',runs:[run]},request:()=>{},refresh:()=>{},openOriginal:()=>{}});
 assert.doesNotMatch(html,/批准这个本地分支/);
});
test('an actual prepared review exposes the distinct local approval with an unchecked confirmation',()=>{
 const run={run_id:'run',revision:'rev',status:'candidate_ready',claimed:true,review:{status:'prepared',hash_matches:true,diff_hash_matches:true,review_sha256:'sha',tests:[]}};
 const html=render(TaskReport,{record,state,current:{id:'task',runs:[run]},request:()=>{},refresh:()=>{},openOriginal:()=>{}});
 assert.match(html,/批准这个本地分支/);assert.match(html,/disabled=""[^>]*>批准这个本地分支/);
});

test('native approval respects the original repeated-failed-patch and recovery gates',()=>{
 const plan={id:'plan',claimed:true,status:'awaiting_approval',plan:{},repeated_failed_patch:true};
 let html=render(TaskReport,{record,state,current:{id:'task',plans:[plan]},request:()=>{},refresh:()=>{},openOriginal:()=>{}});
 assert.match(html,/disabled=""[^>]*>批准此方案并测试/);
 html=render(TaskReport,{record,state:{...state,recovery_cards:[{kind:'plan',id:'plan',status:'blocked'}]},current:{id:'task',plans:[{...plan,repeated_failed_patch:false}]},request:()=>{},refresh:()=>{},openOriginal:()=>{}});
 assert.match(html,/disabled=""[^>]*>批准此方案并测试/);
});
test('closure proposal shows its original Issue and default branch evidence, not the currently selected row',()=>{
 const html=render(DeliveryProposal,{result:{id:'execution-original',approval_sha256:'exact',core:{operation:'close_completed_issue_only',proof:{binding:{execution_id:'execution-original',repository:'owner/repo',issue_number:7},default_head_sha:'default-sha',pr:{merge_commit_sha:'merge-sha'},workflows:[{id:1}]}}}});
 for(const value of ['owner/repo','#7','execution-original','default-sha','merge-sha','exact'])assert.ok(html.includes(value));
});
test('test results distinguish zero exit, nonzero exit, timeout and missing evidence',()=>{
 const html=render(TestTable,{title:'checks',tests:[{argv:['python','test.py'],returncode:0},{argv:['pytest'],returncode:1},{argv:['node'],returncode:0,timed_out:true},{argv:['unknown']}]});
 for(const value of ['命令通过','命令失败','超时','未知'])assert.ok(html.includes(value));
 assert.match(render(TestTable,{title:'empty',tests:[]}),/不视为通过/);
});
test('readable check evidence keeps ineffective, blocked and incomparable receipts distinct',()=>{
 const tests=[{command:['pytest'],returncode:0,stdout:'collected 0 items'},{command:['python'],returncode:0,stdout:'Ran 1 test\nOK (skipped=1)'},{command:['node'],returncode:1,stderr:'MODULE_NOT_FOUND'},{command:['other'],returncode:0,output_truncated:true}];
 const html=render(TestTable,{title:'checks',tests});
 for(const value of ['零测试','全跳过或无有效测试','环境阻塞','输出截断'])assert.ok(html.includes(value));
 assert.match(render(CheckComparison,{baseline:tests.slice(0,1),candidate:[{command:['different'],returncode:0}]}),/不能作可比结论/);
 const timeline=render(ActivityTimeline,{state:{planning_attempts:[]},run:{events:[{time:'fixed-time',stage:'baseline'}],rounds:[{number:1,provider_evidence:{requests:0,events_dropped:2}}]}});
 for(const value of ['fixed-time','baseline','执行阶段第 1 轮','活动记录不完整'])assert.ok(timeline.includes(value));
 const active=render(TaskReport,{record,state:{...state,active:'current-job',jobs:[{id:'current-job',progress:{phase:'planning',tool_calls:1,events:[{type:'tool_execution_start',tool:'read_source',path:'fixture.py'}]}}]},current:null,request:()=>{},refresh:()=>{},openOriginal:()=>{}});
 for(const value of ['当前仓库阶段：planning','不推定属于所选任务','fixture.py'])assert.ok(active.includes(value));
});
test('all eight page states render without exposing the workspace capability or issuing render-time requests',()=>{
 let calls=0;
 for(const page of ['overview','tasks','issues','events','memory','delivery','configuration','service']){
  const html=render(WorkbenchPages,{page,record,state,hub:{monitors:[]},request:()=>{calls++;},refresh:()=>{},navigate:()=>{},openOriginal:()=>{},connect:()=>{},selectTask:()=>{},rows:[]});
  assert.ok(html.length>30);assert.doesNotMatch(html,/SECRET_TEST_CAPABILITY/);
 }
 assert.equal(calls,0);
});

test('a local preview renders real editable fields and an explicitly disabled execution button',()=>{
 const html=render(LocalPreview,{repositoryKey:'repo',workflow:{id:'example',title:'前置复现',connection:'cli',fields:[{key:'target',label:'目标版本',type:'text',required:true}]}});
 assert.match(html,/目标版本/);assert.match(html,/校验并生成本地预览/);assert.match(html,/disabled=""[^>]*>提交执行（尚未接通）/);assert.doesNotMatch(html,/已执行成功/);
 for(const workflow of workflows){
  const form=render(LocalPreview,{repositoryKey:'repo',workflow});
  for(const field of workflow.fields)assert.ok(form.includes('draft-'+workflow.id+'-'+field.key),workflow.id+':'+field.key);
  assert.match(form,/disabled=""[^>]*>提交执行（尚未接通）/);assert.match(form,/校验并生成本地预览/);
 }
});
test('restored Issue investigation receipts expose their report without reissuing an action',()=>{
 const value={...state,issue_workbench:{investigations:[{id:'version',status:'investigated',snapshot:{issue:{number:7},token:'version'}}]},jobs:[{action:'issue_investigate',status:'finished',result:{id:'version',snapshot:{issue:{number:7}},report:{partial:true,search_hit:'fixed/source.py'}}}]};
 const html=render(IssueEvidence,{state:value,number:7});assert.match(html,/fixed\/source.py/);assert.match(html,/partial/);assert.doesNotMatch(html,/SECRET_TEST_CAPABILITY/);
});
test('new recovery UI does not expose destructive candidate actions before original claim and valid evidence',()=>{
 for(const run of [{run_id:'r',claimed:false,status:'candidate_ready'},{run_id:'r',claimed:true,status:'evidence_invalid'},{run_id:'r',claimed:true,status:'candidate_ready',repair_finished:false}]){
  const html=render(RunOperations,{record,state,run,request:()=>{},refresh:()=>{}});assert.doesNotMatch(html,/清理此候选工作区/);assert.doesNotMatch(html,/拒绝这个本地发布提案/);
 }
});
test('stale compaction preview never grants a new compact action',()=>{
 const value={...state,task_compaction:{tasks:[{task_id:'t',status:'verified',can_compact:true,revision:'new'}]},jobs:[{action:'compaction_preview',status:'finished',result:{compaction_preview:{task_id:'t',revision:'old',can_compact:true}}}]};
 const html=render(ContextOperations,{record,state:value,request:()=>{},refresh:()=>{}});assert.match(html,/不匹配当前检查点/);assert.match(html,/disabled=""[^>]*>确认消耗原额度整理上下文/);
});
test('native monitor and analysis need a matching source preview and monitoring consent',()=>{
 const props={record,state,hub:{jobs:[]},request:()=>{throw Error('render sent an action');},refresh:()=>{}};
 let html=render(NativeRepositoryControls,props);assert.doesNotMatch(html,/确认启用这份范围的监控/);
 html=render(NativeRepositoryControls,{...props,hub:{jobs:[{action:'local',status:'finished',result:{path:'fixture',revision:'fixed',clean:true,branch:'main'}}]}});
 assert.match(html,/只读分析Python语法与检查缺口/);assert.match(html,/disabled=""[^>]*>确认启用这份范围的监控/);
 html=render(NativeRepositoryControls,{...props,hub:{jobs:[{action:'local',status:'finished',result:{path:'other',revision:'foreign',clean:true,branch:'main'}}]}});
 assert.doesNotMatch(html,/确认启用这份范围的监控/);
});
test('native monitoring exposes original event checks and guarded review while retaining notification status',()=>{
 const event={id:'e',sha:'fixed',message:'existing-event',status:'awaiting_approval',can_open:true,notification:'original-notification',tests:[{command:['python'],returncode:0,stdout:'Ran 1 test'}],ci_failures:[{name:'invalid',url:'https://example.invalid/unsafe'},{name:'CI',url:'https://github.com/example/fixture/actions/runs/1',conclusion:'failure'}]};
 const props={page:'events',record,state,hub:{monitors:[{id:'m',spec:{path:'fixture'},enabled:false,events:[event]}]},request:()=>{},refresh:()=>{},navigate:()=>{},openOriginal:()=>{},connect:()=>{},selectTask:()=>{},rows:[]};
 const html=render(WorkbenchPages,props);
 for(const text of ['本页面维护通知','本次监控检查','原通知标识','审阅这个监控事件的原方案'])assert.ok(html.includes(text));
 assert.doesNotMatch(html,/href="https:\/\/example.invalid/);assert.match(html,/href="https:\/\/github.com/);
});


test('monitor CI investigation distinguishes read-only, historical and incomplete evidence without actions',()=>{
 const event={ci_diagnosis_historical:true,ci_diagnosis:{status:'investigated',base_sha:'a'.repeat(40),selection:{run_id:101,attempt:1},ci_evidence:{synthetic:true,evidence_status:'partial',log_status:'blocked',failed_steps:[{number:13,name:'<script>unsafe</script>'}]},authority:{next_step:'reproduce_failure'},report:{file_candidates:[{path:'src/calc.py'}]}}};
 const html=render(CiMonitorDiagnosis,{event});
 assert.match(html,/历史调查/);assert.match(html,/尚未授权修复/);assert.match(html,/partial/);assert.match(html,/src\/calc.py/);assert.match(html,/&lt;script&gt;/);
 assert.doesNotMatch(html,/<script>|<button|onclick=/i);
});
test('an interrupted CI investigation cannot render success or a repair approval',()=>{
 const html=render(CiMonitorDiagnosis,{event:{ci_diagnosis:{status:'interrupted_no_replay'}}});
 assert.match(html,/不会自动重放/);assert.doesNotMatch(html,/只读调查已完成|<button/);
 assert.equal(render(CiMonitorDiagnosis,{event:{}}),'');
});

test('CI environment suggestion preserves old checks and never renders an execute action',()=>{
 const event={ci_diagnosis:{status:'investigated',selection:{},ci_evidence:{}},ci_preparation:{status:'scope_review',environment:{runtime:'python',checks:[{id:'test',title:'功能检查',argv:['{python}','-m','unittest','<script>bad</script>']}]},proposed_policy:{allowed_paths:['src/calc.py']},preserved_registered_tests:[['old']],blockers:['Need review']}};
 const html=render(CiMonitorDiagnosis,{event});
 for(const text of ['自动选择的环境与检查','新增检查需要授权范围审阅','尚未安装依赖','原授权回归检查继续保留','src/calc.py','Need review','查看检查详情'])assert.ok(html.includes(text));
 assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>|<button|onclick=/i);
});
test('CI environment blockage and historical suggestions are not repair failures or grants',()=>{
 const event={ci_diagnosis_historical:true,ci_diagnosis:{status:'investigated',selection:{},ci_evidence:{}},ci_preparation:{status:'environment_blocked',blockers:['Unsupported lock']}};
 const html=render(CiMonitorDiagnosis,{event});
 assert.match(html,/不能视为代码修复失败/);assert.match(html,/当前 CI 不能沿用/);assert.doesNotMatch(html,/<button/);
 event.ci_preparation={status:'interrupted_no_replay'};
 assert.match(render(CiMonitorDiagnosis,{event}),/建议未完成，保留记录/);
});

test('root maintenance config selects directories and check families without a file policy or JSON input',()=>{
 const props={hub:{jobs:[],allow_auto:false},request:()=>{throw Error('No render action');},refresh:()=>{}};
 const html=render(RootMaintenanceControls,{p:props,source:{path:'fixture',revision:'fixed',branch:'main',clean:true}});
 for(const text of ['一次设置目录维护规则','可读取的目录','可修改的目录','Python unittest 测试','预览目录维护规则','无需填写文件清单或测试命令'])assert.ok(html.includes(text),text);
 assert.match(html,/当前服务未启用自主候选权限/);
 assert.doesNotMatch(html,/textarea[^>]*>\[\[/);
 assert.doesNotMatch(html,/确认保存并启用目录维护/);
});
test('root baseline result distinguishes real red tests from an unimplemented repair candidate',()=>{
 const event={root_preparation:{status:'reproduced',message:'已复现测试失败',model_requests:0,base_sha:'fixed',
  proposed_policy:{allowed_paths:['src/<unsafe>.py']},baseline:{run_id:'run',source_unchanged:true,checks:[{command:['python','test'],returncode:1,timed_out:false,category:'test_failed',stdout:'Ran 4 tests'}]}}};
 const html=render(RootMonitorPreparation,{event});
 for(const text of ['已复现测试失败','修改前基线检查','修复需要本次单独确认','模型请求','src/&lt;unsafe&gt;.py'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/<unsafe>|<button|onclick=/i);
});

test('a local repository preview opens directory rules before a legacy file policy exists',()=>{
 const hub={jobs:[{action:'local',status:'finished',result:{path:'author-fixture',revision:'fixed',branch:'main',clean:true}}]};
 const html=render(NativeRepositoryControls,{hub,request:()=>{},refresh:()=>{}});
 assert.match(html,/一次设置目录维护规则/);assert.match(html,/author-fixture/);
 assert.doesNotMatch(html,/先明确连接仓库，再读取或配置实际操作/);
});

test('a reproduced failure offers an explicit model preview without implicitly granting execution',()=>{
 const event={id:'e',monitor_id:'m',sha:'fixed',can_prepare_repair:true,root_preparation:{result_sha256:'p'}};
 const html=render(MonitorRepairControls,{p:{hub:{jobs:[]},request:()=>{},refresh:()=>{}},event});
 assert.match(html,/预览本次修复范围与额度/);assert.doesNotMatch(html,/确认本次模型修复/);
});
test('the model consent shows original consumption and is disabled before checking the fee consent',()=>{
 const event={id:'e',monitor_id:'m',sha:'fixed',can_prepare_repair:true,root_preparation:{result_sha256:'p'}};
 const hub={jobs:[{action:'monitor_repair_preview',status:'finished',result:{revision:'challenge',preview:{
  binding:{event_id:'e',monitor_id:'m',base_sha:'fixed',preparation_sha256:'p'},policy:{allowed_paths:['calculator.py']},
  budget:{request_budget:6,requests_used:2,requests_reserved_unknown:0,requests_remaining:4},max_attempts:1}}}]};
 const html=render(MonitorRepairControls,{p:{hub,request:()=>{},refresh:()=>{}},event});
 for(const text of ['calculator.py','可能收费','原剩余额度','剩余 4'])assert.ok(html.includes(text),text);
 assert.match(html,/disabled=""[^>]*>确认本次模型修复/);
 assert.equal(render(MonitorRepairControls,{p:{hub,request:()=>{},refresh:()=>{}},event:{...event,can_prepare_repair:false}}),'');
});

test('the monitor displays the original completed run instead of its earlier planning intent',()=>{
 const html=render(RootMonitorPreparation,{event:{display_status:'candidate_ready',root_execution:{status:'awaiting_approval'},root_preparation:{status:'reproduced',base_sha:'fixed'}}});
 assert.match(html,/本次修复状态：candidate_ready/);assert.doesNotMatch(html,/本次修复状态：awaiting_approval/);
});


test('historical monitor view shows old diff and tests with unknown consumption but no executable controls',()=>{
 const value={base_sha:'old-fixed-sha',budget:{request_budget:6,requests_used:2,requests_reserved_unknown:4,requests_remaining:0},
  errors:['原证据待核验'],truncated:true,plans:[{id:'original-plan',status:'consumed',diff:'<script>unsafe</script>',plan:{diagnosis:'original diagnosis'}}],
  runs:[{run_id:'old-run',status:'candidate_ready',candidate_tests:[{command:['python'],returncode:0,stdout:'old test result'}]}]};
 const html=render(MonitorHistoryView,{value});
 for(const text of ['只读','old-fixed-sha','未知占用 4','original diagnosis','原候选检查','原证据待核验','前20份'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/<button|<script>|批准此方案|重新请求/);assert.match(html,/&lt;script&gt;/);
});

test('monitor history entry renders read and recovery actions without sending requests or inheriting approval',()=>{
 const event={monitor_id:'m',id:'e'};
 const p={hub:{jobs:[{action:'monitor_recovery_preview',status:'finished',result:{monitor_id:'m',event_id:'e',status:'unknown_consumption',message:'先核对原模型请求',can_open_original:false,can_check_latest:false}}]},
  request:()=>{throw Error('render sent request');},refresh:()=>{}};
 const html=render(MonitorHistory,{p,event});
 for(const text of ['只读查看历史方案、Diff与测试','检查接下来能做什么','先核对原模型请求'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/核验并进入原任务（需认领与新审批）|按现有规则检查最新提交/);
 const newer={...p,hub:{jobs:[{action:'monitor_recovery_preview',status:'finished',result:{monitor_id:'m',event_id:'e',status:'new_commit',message:'分支已有新提交',can_check_latest:true}}]}};
 assert.match(render(MonitorHistory,{p:newer,event}),/按现有规则检查最新提交/);
 assert.doesNotMatch(render(MonitorHistory,{p:newer,event:{monitor_id:'other',id:'e'}}),/按现有规则检查最新提交/);
});


test('monitor continuation has separate consent for new paid work, safe abort and original-result association',()=>{
 const event={monitor_id:'m',id:'e',sha:'base',root_execution:{status:'failed_no_replay'},root_preparation:{result_sha256:'prep'}};
 const make=(flags)=>({preview:{monitor_id:'m',event_id:'e',mode:'approval',binding:{base_sha:'base',preparation_sha256:'prep'},
  budget:{request_budget:8,requests_used:2,requests_reserved_unknown:0,requests_remaining:6},policy:{allowed_paths:['calculator.py']},max_attempts:1,message:'原现场已核验',...flags},revision:'fixed'});
 const props=(value)=>({hub:{monitors:[{id:'m',requests_used:2,requests_reserved_unknown:0,root_rules:{settings:{mode:'approval'}}}],
  jobs:[{action:'monitor_continuation_inspect',status:'finished',result:value}]},request:()=>{throw Error('render sent request');},refresh:()=>{}});
 let html=render(MonitorContinuationControls,{p:props(make({can_continue:true})),event});
 for(const text of ['原现场已核验','calculator.py','可能收费','保留旧运行','确认新一轮接续'])assert.ok(html.includes(text),text);
 assert.match(html,/disabled=""[^>]*>确认新一轮接续/);assert.doesNotMatch(html,/原自主范围内准备候选/);
 html=render(MonitorContinuationControls,{p:props(make({can_abort:true})),event});
 assert.match(html,/批准中止已核验候选/);assert.doesNotMatch(html,/确认新一轮接续/);
 html=render(MonitorContinuationControls,{p:props(make({can_reconcile:true})),event});
 assert.match(html,/确认关联原结果/);assert.match(html,/不重新请求模型或测试/);assert.doesNotMatch(html,/确认新一轮接续/);
 html=render(MonitorContinuationControls,{p:props(make({status:'needs_inspection',message:'原进程仍在运行'})),event});
 assert.match(html,/原进程仍在运行/);assert.doesNotMatch(html,/批准中止已核验候选|确认关联原结果|确认新一轮接续/);
});

test('stale monitor continuation receipts cannot keep a paid confirmation visible',()=>{
 const event={monitor_id:'m',id:'e',sha:'new',root_execution:{status:'failed_no_replay'},root_preparation:{result_sha256:'prep'}};
 const result={preview:{monitor_id:'m',event_id:'e',mode:'approval',binding:{base_sha:'old',preparation_sha256:'prep'},
  budget:{requests_used:2,requests_reserved_unknown:0},can_continue:true},revision:'old'};
 const p={hub:{monitors:[{id:'m',requests_used:2,requests_reserved_unknown:0}],jobs:[{action:'monitor_continuation_inspect',status:'finished',result}]},request:()=>{},refresh:()=>{}};
 assert.doesNotMatch(render(MonitorContinuationControls,{p,event}),/确认新一轮接续/);
 assert.doesNotMatch(render(MonitorContinuationControls,{p,event:{...event,sha:'old',continuation:{id:'newer'}}}),/确认新一轮接续/);
 assert.doesNotMatch(render(MonitorContinuationControls,{p:{...p,hub:{...p.hub,monitors:[{id:'m',requests_used:4,requests_reserved_unknown:0}]}},event:{...event,sha:'old'}}),/确认新一轮接续/);
});


test('future model authorization is separate, bounded and never inferred from root rules',()=>{
 const monitor={id:'m',enabled:true,request_budget:8,requests_used:2,requests_reserved_unknown:0,requests_remaining:6,
  root_rules:{sha256:'rules',settings:{mode:'approval'}}};
 const p={hub:{jobs:[]},request:()=>{throw Error('render sent a request');},refresh:()=>{}};
 const html=render(MonitorModelAuthorization,{p,monitor});
 for(const text of ['后续新提交','不重跑当前提交或旧失败','可能收费','总上限 8','服务重启后模型授权也会暂停','预览未来提交的模型授权'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/确认这份未来模型授权|checked=""|每个新事件的候选轮数上限/);
 assert.equal(render(MonitorModelAuthorization,{p,monitor:{id:'legacy'}}),'');
});

test('a live monitor hold is shown as in progress without changing the authorization gate',()=>{
 const monitor={id:'m',request_budget:8,requests_used:2,requests_reserved_unknown:6,requests_remaining:0,
  requests_reserved_active:6,requests_ended_unknown:0,root_rules:{settings:{mode:'auto'}},
  model_authorization:{id:'g',status:'active',effective_status:'unknown_consumption',core:{expires_at:'fixed UTC'}}};
 const p={hub:{jobs:[]},request:()=>{throw Error('render sent a request');},refresh:()=>{}};
 const html=render(MonitorModelAuthorization,{p,monitor});
 assert.match(html,/模型授权：进行中 · 请求额度已预留/);
 assert.doesNotMatch(html,/原消费待核验/);
 const ended={...monitor,requests_reserved_active:0,requests_ended_unknown:6};
 assert.match(render(MonitorModelAuthorization,{p,monitor:ended}),/模型授权：原消费待核验/);
 assert.equal(monitor.model_authorization.effective_status,'unknown_consumption');
});

test('pause and revoke remain available while an unrelated hub job is busy',()=>{
 const monitor={id:'m',enabled:true,root_rules:{settings:{mode:'auto'}},model_authorization:{id:'g',status:'active',effective_status:'active',core:{expires_at:'fixed UTC'}}};
 const p={hub:{jobs:[{id:'running',status:'running'}]},request:()=>{},refresh:()=>{}};
 const html=render(MonitorModelAuthorization,{p,monitor});
 assert.match(html,/<button>暂停自动模型调用<\/button>/);
 assert.match(html,/<button>撤销这份模型授权<\/button>/);
 assert.match(html,/每个新事件的候选轮数上限/);
});

test('continuous Issue intake is opt-in and shows original scope and shared consumption without sending',()=>{
 const value={...state,request_budget:25,issue_workbench:{configured:true,configuration:{sha256:'config',mode:'approval'},watch:{status:'not_configured',admissions:[]}}};
 const html=render(IssueWatchControl,{record,state:value,request:()=>{throw Error('render sent request');},refresh:()=>{}});
 for(const text of ['只首次接手','ready-for-agent','模型可能收费','服务重启后暂停','总上限 25','预览持续接单授权'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/确认启用持续接单|checked=""|每个Issue的候选轮数上限/);
 assert.equal(render(IssueWatchControl,{record,state,request:()=>{},refresh:()=>{}}),'');
});

test('continuous Issue controls remain available during pending original approval and retain changed sources',()=>{
 const value={...state,active:'running',issue_workbench:{configured:true,configuration:{mode:'auto'},watch:{status:'active',authorization:{id:'grant',status:'active',core:{expires_at:'fixed'}},admissions:[{id:'old',number:7,status:'finished',result_status:'awaiting-review',source_changed:true}]}}};
 const html=render(IssueWatchControl,{record,state:value,request:()=>{},refresh:()=>{}});
 assert.match(html,/<button>暂停持续接单<\/button>/);assert.match(html,/<button>撤销持续接单授权<\/button>/);
 assert.ok(html.includes('来源已变化，需复核'));assert.ok(html.includes('每个Issue的候选轮数上限'));
});

test('unified delivery renders original handles and evidence without claiming publishing authority',async()=>{
 const {DeliveryInbox}=await import(pathToFileURL(path.join(out,'render.mjs')));
 const row={id:'item',notice_id:'notice',status:'candidate_review',title:'Fixture candidate',origin:{kind:'issue',repository:'repo',issue_number:7,execution_id:'original-execution',run_id:'original-run',workspace_id:'workspace'},budget:{request_budget:8,requests_used:2,requests_reserved_unknown:0,requests_remaining:6},delivery:{pr:{status:'creation_unknown'}},evidence:{checks_passed:true,effective_test_count_known:true,tests_run:4,base_sha:'a'.repeat(40)}};
 const html=render(DeliveryInbox,{p:{hub:{jobs:[]},delivery:{complete:true,items:[row]},request:()=>{throw Error('render submitted an operation');},refresh:()=>{}}});
 for(const text of ['待审候选与交付待办','原运行 original-run','当前来源尚未重新核验','有效测试 4 项','在原任务中审阅与准备PR','creation_unknown'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/确认推送|合并main|关闭Issue/);
});

test('monitor delivery renders three separate unchecked approvals and ignores another origin proposal',async()=>{
 const {MonitorDeliveryControls}=await import(pathToFileURL(path.join(out,'render.mjs')));
 const origin={kind:'monitor',monitor_id:'m',event_id:'e',run_id:'r'};
 const row={id:'item',notice_id:'notice',origin,status:'candidate_review',delivery:{},evidence:{}};
 const p={hub:{jobs:[]},request:()=>{throw Error('render sent a write');},refresh:()=>{}};
 let html=render(MonitorDeliveryControls,{p,row});
 assert.match(html,/封装并复测监控候选/);assert.doesNotMatch(html,/checked=""|确认推送 GitHub 新分支<\/button>|确认创建监控 Draft PR<\/button>/);
 const proposal={core:{binding:{origin_kind:'monitor',monitor_id:'another',event_id:'e',run_id:'r'},url:'WRONG_TARGET'},challenge:'c'};
 html=render(MonitorDeliveryControls,{p:{...p,hub:{jobs:[{action:'monitor_delivery_push_preview',status:'finished',result:proposal}]}},row});
 assert.doesNotMatch(html,/WRONG_TARGET|批准推送此准确提交/);
});

test('monitor unknown results expose only read-only reconciliation and never enable replay',async()=>{
 const {MonitorDeliveryControls}=await import(pathToFileURL(path.join(out,'render.mjs')));
 const row={id:'item',notice_id:'unknown',origin:{monitor_id:'m',event_id:'e',run_id:'r'},status:'candidate_review',
  evidence:{local_publication:{integration_commit:'a'.repeat(40),integration_branch:'candidate'}},
  delivery:{push:{status:'push_unknown'},pr:{status:'creation_unknown'}}};
 const html=render(MonitorDeliveryControls,{row,p:{hub:{jobs:[]},request:()=>{throw Error('render sent a write');},refresh:()=>{}}});
 assert.match(html,/<button>只读核对远端分支结果<\/button>/);assert.match(html,/<button>只读核对PR创建结果<\/button>/);
 assert.match(html,/<button disabled="">预览 GitHub 新分支<\/button>/);assert.match(html,/<button disabled="">预览监控 Draft PR<\/button>/);
 assert.doesNotMatch(html,/批准推送此准确提交|批准按所示完整内容|checked=""/);
});

test('monitor acceptance discloses CI coverage and never executes or merges on render',async()=>{
 const {MonitorAcceptanceControls}=await import(pathToFileURL(path.join(out,'render.mjs')));
 const origin={monitor_id:'m',event_id:'e',run_id:'r'};
 const row={id:'item',origin,delivery:{pr:{status:'awaiting-review'}}};
 const proposal={id:'aid',run_id:'r',challenge:'c',approval_sha256:'h',core:{binding:{origin_kind:'monitor',...origin},
  default_head_sha:'accurate-head',pr:{url:'https://github.com/example/project/pull/9'},acceptance_policy:{workflow_ids:[1001]}}};
 const p={hub:{jobs:[{action:'monitor_delivery_acceptance_preview',status:'finished',result:proposal}]},
  request:()=>{throw Error('render sent operation');},refresh:()=>{}};
 const html=render(MonitorAcceptanceControls,{p,row});
 for(const text of ['accurate-head','1001','不执行本地测试','不代表尚未触发的其他检查','确认验收这个版本与列出的 CI 范围'])assert.ok(html.includes(text),text);
 assert.match(html,/<button disabled="">确认核验交付版本<\/button>/);assert.doesNotMatch(html,/checked=""/);
 const done=render(MonitorAcceptanceControls,{p,row:{...row,delivery:{...row.delivery,acceptance:{id:'aid',status:'delivery-verified',receipt:{default_head_sha:'accurate-head'}}}}});
 assert.ok(done.includes('指定版本与 CI 已核验'));assert.doesNotMatch(done,/确认核验交付版本<\/button>/);
});
