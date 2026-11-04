import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setUiLanguage} from '../src/ui-language.mjs';
import * as preview from '../src/preview-workflows.mjs';
import {taskSituation, overviewSituations, originalRecoveryActions, deliverySituations, deliveryStageSituation} from '../src/task-situation-model.mjs';

const chinese=/[\u3400-\u9fff]/;
const idle={repository:'用户/仓库',runtime:{code_changed:false},requests_reserved_unknown:0,requests_remaining:5,
 recovery_cards:[],plans:[],runs:[],issue_workbench:{executions:[],approvals:[]}};
const statusCodes=['diagnosing','validation_needed','validation_proposing','validation_review','validation_accepted',
 'validation_preparing','validation_interrupted','validation_preparation_interrupted','validation_clarification',
 'validation_rejected','model_pending','runtime_changed','unknown','budget_exhausted','source_invalid','busy',
 'review_plan','plan_completed','cleanup_pending','feedback_pending','repair_pending','candidate_pending',
 'registration_pending','not_bound','missing','blocked','candidate_ready','awaiting-review','awaiting_review',
 'awaiting_approval','integrated','published','delivered','failed_no_replay','blocked_no_replay',
 'interrupted_no_replay','interrupted','stale','paused','evidence_invalid','failed','running','started','claimed',
 'related_no_dispatch','finished','discarded','rolled_back','rejected','ci_attention','healthy','consumed',
 'environment_blocked','evidence_review','scope_review','execution_consent_required','reproduced','baseline_passes',
 'push_unknown','creation_unknown','close_unknown','record_unknown'];
const stages={local:['prepared','published'],push:['prepared','push_intent','push_unknown','remote-ref-verified','remote-ref-review-required'],
 pr:['prepared','create_intent','creation_unknown','awaiting-review','remote-created-review-required','remote-closed-review'],
 acceptance:['prepared','checking','delivery-verified','not-verified'],
 closure:['prepared','close_intent','close_unknown','closed-verified','closed-review-required'],
 observer:['scheduled','checking','waiting_merge','waiting_ci','checks_missing','checks_failed','verified','network_blocked',
 'scope_changed','closed_without_merge','paused','restart_paused','expired','capacity_reached','blocked']};
function ownedText(item){return [item.title,item.reason,item.next].join('\n');}
function english(item){assert.doesNotMatch(ownedText(item),chinese);return item;}
function withoutCopy(item){const {title,reason,next,...data}=item;return data;}

test('all task and delivery situations switch after import and preserve controller identities',()=>{
 try{
  for(const status of statusCodes){
   const state={...idle,runs:[{run_id:'用户运行',status,reason:'原日志不应翻译'}]};
   setUiLanguage('zh-CN');const zh=taskSituation(state,{kind:'run',id:'用户运行'});
   assert.match(ownedText(zh),chinese);
   setUiLanguage('en');const en=english(taskSituation(state,{kind:'run',id:'用户运行'}));
   assert.deepEqual(withoutCopy(en),withoutCopy(zh));
   assert.equal(en.id,'用户运行');assert.equal(en.status,status);
   setUiLanguage('zh-CN');assert.deepEqual(taskSituation(state,{kind:'run',id:'用户运行'}),zh);
   assert.equal(state.runs[0].reason,'原日志不应翻译');
  }
  for(const [stage,statuses] of Object.entries(stages))for(const status of statuses){
   setUiLanguage('zh-CN');const zh=deliveryStageSituation(stage,{id:'交付原文',status});
   setUiLanguage('en');const en=english(deliveryStageSituation(stage,{id:'交付原文',status}));
   assert.deepEqual(withoutCopy(en),withoutCopy(zh));
   setUiLanguage('zh-CN');assert.deepEqual(deliveryStageSituation(stage,{id:'交付原文',status}),zh);
  }
 }finally{setUiLanguage('zh-CN');}
});

test('recovery, overview and delivery project English copy without changing actions or input records',()=>{
 const actions=[{action:'resume_run',mode:'approval',revision:'中文原始指纹'},{action:'resume_run',mode:'auto',revision:'另一指纹'}];
 const state={...idle,recovery_cards:[{kind:'run',id:'恢复',status:'available',actions}]};
 const delivery={id:'交付',origin:{repository:idle.repository},status:'candidate_review',
  evidence:{local_publication:{integration_branch:'中文分支',integration_commit:'SHA'}},
  delivery:{push:{status:'push_unknown'},pr:{status:'awaiting-review'},acceptance_watch:{status:'waiting_ci'}}};
 const monitors=[{id:'监控',repository:idle.repository,requests_reserved_unknown:1,
  model_authorization:{effective_status:'binding_changed'},
  failure_followups:[{id:'后继',effective_status:'active',core:{event_id:'事件'}}],events:[]}];
 const original=JSON.stringify({state,delivery,monitors});
 try{
  setUiLanguage('zh-CN');const zh=taskSituation(state,{kind:'run',id:'恢复'});
  setUiLanguage('en');const en=english(taskSituation(state,{kind:'run',id:'恢复'}));
  assert.deepEqual(withoutCopy(en),withoutCopy(zh));assert.deepEqual(originalRecoveryActions(state,{kind:'run',id:'恢复'}),actions);
  for(const mode of ['auto','approval'])english(taskSituation({...state,recovery_cards:[{...state.recovery_cards[0],actions:actions.filter(a=>a.mode===mode)}]},{kind:'run',id:'恢复'}));
  const rows=overviewSituations({...state,runtime:{code_changed:true},requests_reserved_unknown:1,requests_remaining:0,
   source_memory:{status:'invalid'},issue_workbench:{executions:[],approvals:[{id:'孤立批准',status:'pending'}]}},
   {repository:idle.repository,monitors,delivery:{items:[delivery],complete:false}});
  rows.forEach(english);deliverySituations(delivery).forEach(english);
  english(overviewSituations(null)[0]);english(overviewSituations(state,{repository:'另一仓库'})[0]);
  assert.equal(JSON.stringify({state,delivery,monitors}),original);
  setUiLanguage('zh-CN');assert.deepEqual(taskSituation(state,{kind:'run',id:'恢复'}),zh);
 }finally{setUiLanguage('zh-CN');}
});

test('workflow captions switch while schema, original choice values and local preview data stay identical',()=>{
 const originalSchema=JSON.stringify(preview.workflows);
 try{
  setUiLanguage('zh-CN');
  assert.equal(preview.previewChoiceLabel('只读分诊'),'只读分诊');
  const valuesByWorkflow=new Map(preview.workflows.map(workflow=>[workflow.id,Object.fromEntries(workflow.fields.map(field=>[
   field.key,field.type==='select'?field.options[0]:field.type==='number'?String(field.min):field.format==='https'?'https://example.test/callback':field.format==='argv'?'["echo","用户原文"]':'用户原文/仓库 说明'
  ]))]));
  const originalPreviews=new Map(preview.workflows.map(workflow=>[workflow.id,preview.previewDraft(workflow,valuesByWorkflow.get(workflow.id))]));
  for(const result of originalPreviews.values())assert.deepEqual(result.errors,{});
  setUiLanguage('en');
  for(const workflow of preview.workflows){
   assert.doesNotMatch(preview.previewDisplayText(workflow.title),chinese);
   for(const field of workflow.fields){
    assert.doesNotMatch(preview.previewDisplayText(field.label),chinese);
    for(const value of field.options||[])assert.doesNotMatch(preview.previewChoiceLabel(value),chinese);
   }
   const result=preview.previewDraft(workflow,valuesByWorkflow.get(workflow.id));
   assert.deepEqual(result,originalPreviews.get(workflow.id));
   assert.doesNotMatch(preview.previewDisplayText(result.preview.blocking_dependency),chinese);
  }
  assert.equal(preview.previewChoiceLabel('只读分诊'),'Read-only triage');
  assert.equal(preview.previewDisplayText('用户原文/仓库 说明'),'用户原文/仓库 说明');
  assert.equal(preview.previewChoiceLabel('用户自定义'),'用户自定义');
  assert.equal(JSON.stringify(preview.workflows),originalSchema);
  setUiLanguage('zh-CN');assert.equal(preview.previewChoiceLabel('只读分诊'),'只读分诊');
  assert.equal(JSON.stringify(preview.workflows),originalSchema);
 }finally{setUiLanguage('zh-CN');}
});

test('validation uses the same original rules and fields in both languages',()=>{
 const workflow=preview.workflows.find(row=>row.id==='auto-intake');
 const invalid={repository:'原始仓库',target:'原始分支',labels:'原始标签',actors:'原始用户',interval:'0',mode:'Read-only triage'};
 try{
  setUiLanguage('zh-CN');const zh=preview.validateDraft(workflow,invalid);assert.deepEqual(Object.keys(zh),['interval','mode']);
  setUiLanguage('en');const en=preview.validateDraft(workflow,invalid);assert.deepEqual(Object.keys(en),Object.keys(zh));
  for(const text of Object.values(en))assert.doesNotMatch(text,chinese);
  assert.match(en.interval,/30.*86400/);
  const valid={...invalid,interval:'30',mode:'只读分诊'};
  assert.deepEqual(preview.validateDraft(workflow,valid),{});
  assert.equal(preview.previewDraft(workflow,valid).preview.inputs.mode,'只读分诊');
  setUiLanguage('zh-CN');assert.deepEqual(preview.validateDraft(workflow,invalid),zh);
 }finally{setUiLanguage('zh-CN');}
});

test('required, length, control-character, URL, argv and secret errors localize without relaxing rejection',()=>{
 const workflow={fields:[{key:'entry',label:'仓库引用',type:'text',required:true}]};
 const cases=[['',{}],['x'.repeat(4001),{}],['a\u0001b',{}],
  ['https://name:password@example.test',{format:'https'}],
  ['http://example.test',{format:'https'}],
  ['["echo",1]',{format:'argv'}],['[]',{format:'argv'}],
  ['sk-abcdefghijklmnop',{}]];
 try{
  for(const [entry,field] of cases){
   const schema={fields:[{...workflow.fields[0],...field}]};
   const values={entry};const original=JSON.stringify({schema,values});
   setUiLanguage('zh-CN');const zh=preview.validateDraft(schema,values);
   assert.deepEqual(Object.keys(zh),['entry']);assert.match(zh.entry,chinese);
   setUiLanguage('en');const en=preview.validateDraft(schema,values);
   assert.deepEqual(Object.keys(en),['entry']);assert.doesNotMatch(en.entry,chinese);
   assert.equal(preview.previewDraft(schema,values).preview,null);
   assert.equal(JSON.stringify({schema,values}),original);
   setUiLanguage('zh-CN');assert.deepEqual(preview.validateDraft(schema,values),zh);
  }
 }finally{setUiLanguage('zh-CN');}
});
