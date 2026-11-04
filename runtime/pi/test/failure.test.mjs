import test from 'node:test';
import assert from 'node:assert/strict';
import {generatePlan} from '../plan.mjs';
import {safeEvent,safeUsage,PiPlanningFailure} from '../evidence.mjs';
import {startMock} from './mock-server.mjs';

const request={issue:'addition bug',base_sha:'a'.repeat(40),files:[{path:'calculator.py',
  content:'def add(a,b):\n return a-b\n',before_sha256:'b'.repeat(64)}],tests:[['{python}','-m','unittest']],request_budget:2};
for(const [failure,category] of [[true,'provider_error'],['truncated','provider_error'],['no_plan','no_plan'],['stall','runtime_timeout']])
 test('real Pi safe failure evidence: '+failure,async()=>{
  const mock=await startMock({failure});const progress=[];
  try {
   // Only the stall case tests a short deadline. Other cases must reach the
   // synthetic response even when the full CI suite is competing for CPU.
   await assert.rejects(generatePlan(request,{mockUrl:mock.url,timeoutMs:failure==='stall'?100:5000,onEvidence:e=>progress.push(e)}),error=>{
    assert.ok(error instanceof PiPlanningFailure);const evidence=error.evidence;
    assert.equal(evidence.schema_version,1);assert.equal(evidence.status,'failed');assert.equal(evidence.failure_category,category);
    const expectedRequests=failure==='no_plan'?2:1;
    assert.equal(evidence.requests,failure==='no_plan'?2:null);assert.equal(evidence.requests_started,expectedRequests);
    assert.equal(evidence.consumption,failure==='no_plan'?'reported':'unknown');
    if(failure==='no_plan')assert.deepEqual(evidence.terminal_count,{schema_version:1,completed_streams:2});
    else assert.equal(evidence.terminal_count,undefined);
    assert.equal(evidence.events_complete,true);assert.ok(evidence.events.some(e=>e.type==='agent_end'));
    assert.ok(evidence.events.some(e=>e.type==='assistant_end'));
    assert.doesNotMatch(JSON.stringify(evidence)+error.message,/syntheticNotARealKey|untrusted provider|no structured plan|Bearer/);
    return true;
   });
   assert.equal(mock.requests.length,failure==='no_plan'?2:1);assert.ok(progress.some(p=>p.requests_started===1));
   assert.ok(progress.every(p=>p.requests===null&&p.events_complete===false));
  }finally{await mock.close();}
 });
test('local budget exhaustion reports completed streams while the planning task still fails',async()=>{
 const mock=await startMock();
 try{await assert.rejects(generatePlan({...request,request_budget:1},{mockUrl:mock.url}),e=>{
  assert.equal(e.evidence.failure_category,'request_budget');assert.equal(e.evidence.requests_started,1);
  assert.equal(e.evidence.requests,1);assert.equal(e.evidence.consumption,'reported');
  assert.deepEqual(e.evidence.terminal_count,{schema_version:1,completed_streams:1});return true;
 });assert.equal(mock.requests.length,1);}finally{await mock.close();}
});
test('event projection excludes message bodies, tool input/output, arbitrary tools and cost',()=>{
 assert.deepEqual(safeEvent({type:'message_end',message:{role:'assistant',stopReason:'error',errorMessage:'private',content:'private'}}),{type:'assistant_end',stop_reason:'error'});
 assert.equal(safeEvent({type:'text_delta',delta:'private'}),null);
 assert.equal(safeEvent({type:'tool_execution_start',toolName:'unknown-private-tool'}),null);
 assert.deepEqual(safeEvent({type:'tool_execution_end',toolName:'read_source',isError:true,result:'private',args:'private'}),{type:'tool_execution_end',tool:'read_source',is_error:true});
 assert.deepEqual(safeUsage([{role:'assistant',usage:{input:1,output:2,totalTokens:3,cost:{total:999},raw:'private',cacheRead:NaN}}]),[{input:1,output:2,totalTokens:3}]);
});
