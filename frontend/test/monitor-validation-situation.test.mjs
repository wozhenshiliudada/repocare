import test from 'node:test';
import assert from 'node:assert/strict';
import {taskSituation} from '../src/task-situation-model.mjs';

const original={id:'event',status:'blocked',root_preparation:{status:'validation_needed'}};
function view(event){return taskSituation({monitors:[{id:'monitor',events:[event]}]},
 {kind:'monitor',monitor_id:'monitor',id:'event'});}

test('monitor missing checks explains business expectations rather than asking for commands',()=>{
 const row=view(original);
 assert.equal(row.status,'validation_needed');assert.match(row.next,/业务期望/);
 assert.equal(row.page,'events');assert.equal(row.monitor_id,'monitor');
});
test('original case proposal review and preparation are distinct from repair approval',()=>{
 for(const [status,expected] of Object.entries({started:'validation_proposing',awaiting_validation_review:'validation_review',
  accepted:'validation_accepted',interrupted_no_replay:'validation_interrupted',
  clarification_needed:'validation_clarification',rejected:'validation_rejected'})){
  const row=view({...original,validation:{status}});
  assert.equal(row.status,expected);assert.equal(row.page,'events');assert.equal(row.attention,true);
 }
 assert.match(view({...original,validation:{status:'awaiting_validation_review'}}).next,/不是修改方案审批/);
 assert.match(view({...original,validation:{status:'interrupted_no_replay'}}).next,/已提交、已结算/);
});
test('original preparation receipt wins after review while stale and execution retain precedence',()=>{
 for(const [status,expected] of [['started','validation_preparing'],['interrupted_no_replay','validation_preparation_interrupted']])
  assert.equal(view({...original,validation:{status:'accepted',resume:{status}}}).status,expected);
 assert.equal(view({...original,root_preparation:{status:'reproduced'},
  validation:{status:'accepted',resume:{status:'finished',result_status:'reproduced'}}}).status,'reproduced');
 assert.equal(view({...original,root_preparation_historical:true,validation:{status:'accepted'}}).status,'stale');
 assert.equal(view({...original,root_execution:{status:'candidate_ready'},display_status:'candidate_ready',
  validation:{status:'accepted'}}).status,'candidate_ready');
 assert.match(view({...original,status:'diagnosing',root_execution:{status:'started'},
  validation:{status:'accepted'}}).title,/调查并规划/);
});
