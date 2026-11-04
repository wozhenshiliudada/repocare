import {test} from 'node:test';
import assert from 'node:assert/strict';
import {checkName,checkPresentation,compareCommandReceipts,activityEvidence} from '../src/evidence-model.mjs';

test('controller behavior check is explained as cases, not an executable user command',()=>{
 const value={command:['repocare:behavior-v1','a'.repeat(64)],command_kind:'controller_behavior_contract',
  returncode:0,category:'passed',tests_run:2};
 assert.match(checkName(value),/固定行为案例/);assert.match(checkPresentation(value).label,/行为案例通过/);
 assert.equal(checkName({command:['python','test.py']}),'python test.py');
 assert.match(checkPresentation({...value,category:'inconclusive',returncode:1}).label,/不完整/);
});
test('zero exit cannot turn zero tests, all skips, truncated output or absent counts into effective tests',()=>{
 for(const [stdout,category] of [['Ran 0 tests','zero_tests'],['Ran 3 tests\nOK (skipped=3)','zero_effective_tests'],['some output','inconclusive_no_test_count']]){
  const value=checkPresentation({returncode:0,stdout});assert.equal(value.category,category);assert.equal(value.passed,false);
 }
 assert.equal(checkPresentation({returncode:0,stdout:'Ran 3 tests',output_truncated:true}).passed,false);
 assert.equal(checkPresentation({returncode:1,stderr:'ModuleNotFoundError: fixture'}).category,'environment_blocked');
 assert.equal(checkPresentation({returncode:0,stdout:'Ran 3 tests'}).passed,true);
});
test('command comparison keeps identity, inconclusive timeouts and semantic limits',()=>{
 const row=(code,more={})=>({command:['python','test.py'],returncode:code,...more});
 assert.equal(compareCommandReceipts([row(1)],[row(0)])[0].transition,'repaired');
 assert.equal(compareCommandReceipts([row(1)],[row(0)])[0].effective,false);
 assert.equal(compareCommandReceipts([row(0)],[row(1)])[0].transition,'regressed');
 assert.equal(compareCommandReceipts([row(0)],[row(0,{timed_out:true})])[0].transition,'inconclusive');
 assert.equal(compareCommandReceipts([row(0)],[{command:['other'],returncode:0}]),null);
 assert.equal(compareCommandReceipts([],[]),null);
 assert.equal(compareCommandReceipts([{returncode:0}],[{returncode:0}]),null);
});
test('activity receipts use only bound original attempts and final evidence wins over stale progress',()=>{
 const state={planning_attempts:[{id:'own',status:'finished',evidence:{requests:0,usage:[{input:0}]},progress:{requests:99}},{id:'foreign',evidence:{requests:90}}]};
 const value=activityEvidence(state,{planning_origin:{provider_evidence:{attempt_id:'own'}},rounds:[{number:2,provider_evidence:{requests:1}}]},null);
 assert.equal(value.records.length,2);assert.equal(value.records[0].evidence.requests,0);assert.deepEqual(value.records[0].evidence.usage,[{input:0}]);
 assert.equal(value.records.some(row=>row.evidence.requests===90),false);
 assert.deepEqual(activityEvidence(state,null,{model_evidence:{attempt_id:'absent',requests:null}}).records[0].evidence.requests,null);
});
test('a plan without a run still exposes its original planning receipt',()=>{
 const evidence={attempt_id:'own-plan',requests:1,usage:[{input:0}]};
 const value=activityEvidence({planning_attempts:[]},null,null,{planning_evidence:evidence});
 assert.deepEqual(value.records,[{label:'原方案规划',evidence}]);
});
