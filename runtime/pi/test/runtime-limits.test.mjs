import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {generatePlan} from '../plan.mjs';
import {safeUsage} from '../evidence.mjs';
import {startMock} from './mock-server.mjs';
import {MAX_OUTPUT_TOKENS,MAX_REQUESTS,PLANNING_TIMEOUT_MS} from '../runtime-limits.mjs';

const request={issue:'Repair addition',base_sha:'a'.repeat(40),files:[{path:'calculator.py',
  before_sha256:'b'.repeat(64),content:'def add(a,b):\n    return a - b\n'}],tests:[['{python}','-m','unittest']]};
const read={name:'read_source',args:{path:'calculator.py'}};
const proposal={name:'propose_repair',args:{diagnosis:'addition uses subtraction',risk:'low',
  edits:[{path:'calculator.py',replacements:[{oldText:'a - b',newText:'a + b'}]}]}};

test('real repair planning uses the pinned Pi dispatcher and releases it after the request',async t=>{
  const {Agent}=createRequire(import.meta.resolve('@earendil-works/pi-coding-agent'))('undici');
  const dispatch=Agent.prototype.dispatch;
  const dispatchers=[];
  t.mock.method(Agent.prototype,'dispatch',function (options,handler) {
    dispatchers.push(this);
    return dispatch.call(this,options,handler);
  });
  const mock=await startMock({script:()=>proposal});
  try {
    const result=await generatePlan({...request,request_budget:1},{mockUrl:mock.url});
    assert.equal(result.evidence.requests,1);
    assert.equal(mock.requests.length,1);
    assert.equal(dispatchers.length,1);
    assert.equal(dispatchers[0].destroyed,true);
  }finally{await mock.close();}
});

test('configured default permits all configured Pi rounds and sends 128000 output tokens',async()=>{
  const mock=await startMock({script:(_body,turn)=>turn<MAX_REQUESTS?read:proposal});
  try {
    const result=await generatePlan(request,{mockUrl:mock.url});
    assert.equal(mock.requests.length,MAX_REQUESTS);assert.equal(result.evidence.requests,MAX_REQUESTS);
    assert.equal(result.evidence.tool_budget,MAX_REQUESTS*4);assert.equal(result.evidence.usage.length,MAX_REQUESTS);
    assert.equal(result.evidence.max_output_tokens,128000);
    assert.equal(result.evidence.planning_timeout_seconds,PLANNING_TIMEOUT_MS/1000);
    assert.equal(result.plan.edits[0].content,request.files[0].content.replace('a - b','a + b'));
    for(const body of mock.requests) {
      assert.equal(body.max_tokens,128000);
      const user=body.messages.find(m=>m.role==='user');
      const context=JSON.parse(typeof user.content==='string'?user.content:user.content[0].text);
      assert.equal(context.request_budget,MAX_REQUESTS);assert.equal(context.planning_timeout_seconds,PLANNING_TIMEOUT_MS/1000);
    }
  }finally{await mock.close();}
});

test('configured request guard stops before the next request with complete bounded usage',async()=>{
  const mock=await startMock({script:()=>read});
  try {
    await assert.rejects(generatePlan(request,{mockUrl:mock.url}),error=>{
      assert.equal(error.evidence.failure_category,'request_budget');
      assert.equal(error.evidence.requests_started,MAX_REQUESTS);assert.equal(error.evidence.requests,MAX_REQUESTS);
      assert.equal(error.evidence.consumption,'reported');
      assert.equal(error.evidence.terminal_count.completed_streams,MAX_REQUESTS);
      assert.equal(error.evidence.usage.length,MAX_REQUESTS+1);assert.ok(error.evidence.events_dropped>0);
      assert.ok(error.evidence.events.length<=128);return true;
    });
    assert.equal(mock.requests.length,MAX_REQUESTS);
  }finally{await mock.close();}
});

test('over-limit request and short injected session timeout stop without transport retries',async()=>{
  assert.equal(MAX_REQUESTS,500);assert.equal(MAX_OUTPUT_TOKENS,128000);assert.equal(PLANNING_TIMEOUT_MS,3600000);
  const mock=await startMock({failure:'stall'});
  try {
    await assert.rejects(generatePlan({...request,request_budget:MAX_REQUESTS+1},{mockUrl:mock.url}),/Invalid model request budget/);
    assert.equal(mock.requests.length,0);
    await assert.rejects(generatePlan(request,{mockUrl:mock.url,timeoutMs:100}),error=>{
      assert.equal(error.evidence.failure_category,'runtime_timeout');assert.equal(error.evidence.requests_started,1);return true;
    });
    assert.equal(mock.requests.length,1);
  }finally{await mock.close();}
});

test('long-session usage keeps configured replies and remains bounded',()=>{
  const messages=Array.from({length:MAX_REQUESTS+10},()=>({role:'assistant',usage:{input:1,output:2,cost:{total:999}}}));
  const usage=safeUsage(messages);assert.equal(usage.length,MAX_REQUESTS+1);
  assert.ok(usage.every(row=>row.input===1&&row.output===2&&!('cost' in row)));
});

test('an explicit legacy six-call budget still stops at six',async()=>{
  const mock=await startMock({script:()=>read});
  try{
    await assert.rejects(generatePlan({...request,request_budget:6},{mockUrl:mock.url}),error=>{
      assert.equal(error.evidence.requests_started,6);return true;
    });
    assert.equal(mock.requests.length,6);
  }finally{await mock.close();}
});
