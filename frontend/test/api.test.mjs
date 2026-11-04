import {test} from 'node:test';
import assert from 'node:assert/strict';
import {apiRequest,ApiError} from '../src/api.mjs';
import {summarize,statusTone,monitorEvents,completedOperation} from '../src/workbench-model.mjs';

test('a lost mutation response is unknown and never sent twice',async()=>{
 let calls=0;
 await assert.rejects(apiRequest('/api/actions/issue_push','cap',{request_id:'original'},
  {fetcher:async()=>{calls++;throw Error('connection lost');}}),e=>e instanceof ApiError&&e.uncertain&&e.code==='transport_unknown');
 assert.equal(calls,1);
});
test('non-JSON mutation response retains uncertainty instead of claiming failure',async()=>{
 await assert.rejects(apiRequest('/api/actions/issue_pr','cap',{},
  {fetcher:async()=>({status:202,json:async()=>{throw Error();}})}),e=>e.uncertain&&e.status===202);
});
test('null or list responses are rejected as malformed, retaining mutation uncertainty',async()=>{
 for(const value of [null,[]])await assert.rejects(apiRequest('/api/actions/issue_push','cap',{},
  {fetcher:async()=>({ok:true,status:202,json:async()=>value})}),e=>e.code==='invalid_response'&&e.uncertain);
});
test('a server-side error after a mutation does not assert that the effect was absent',async()=>{
 await assert.rejects(apiRequest('/api/actions/issue_close','cap',{},
  {fetcher:async()=>({ok:false,status:500,json:async()=>({error:'failed'})})}),e=>e.status===500&&e.uncertain);
});
test('structured permissions and freshness errors survive the request adapter',async()=>{
 await assert.rejects(apiRequest('/api/state','cap',undefined,
  {fetcher:async()=>({status:409,ok:false,json:async()=>({error:'changed',code:'stale'})})}),e=>e.status===409&&e.code==='stale'&&!e.uncertain);
});
test('GitHub 401 errors do not expire the workbench capability',async()=>{
 let expired=0;await assert.rejects(apiRequest('/api/github/issues','cap',undefined,{expire:()=>expired++,
  fetcher:async()=>({status:401,ok:false,json:async()=>({code:'read_error',error:'login'})})}));assert.equal(expired,0);
});
test('workbench authentication rejection expires only through the supplied callback',async()=>{
 let expired=0;await assert.rejects(apiRequest('/api/state','cap',undefined,{expire:()=>expired++,
  fetcher:async()=>({status:401,ok:false,json:async()=>({error:'session'})})}));assert.equal(expired,1);
});
test('GET carries no request body and no hidden mutation',async()=>{
 let observed;await apiRequest('/api/state','cap',undefined,{fetcher:async(path,init)=>{observed=init;return {ok:true,status:200,json:async()=>({})};}});
 assert.equal(observed.method,'GET');assert.equal(observed.body,undefined);assert.equal(observed.headers.Authorization,'Bearer cap');
});
test('unknown usage is not projected as zero or returned to the remaining budget',()=>{
 const value=summarize({requests_used:3,requests_remaining:5,requests_reserved_unknown:2});
 assert.deepEqual([value.used,value.remaining,value.unknown],[3,5,2]);assert.equal(summarize(null).used,null);
});
test('approval counts distinguish native plans and pending Issue approvals from historical decisions',()=>{
 assert.equal(summarize({plans:[{status:'awaiting_approval'},{status:'consumed'}],issue_workbench:{approvals:[{status:'pending'},{status:'approved'},{status:'interrupted'}]}}).approvals,2);
});
test('monitor events respect actual spec.path and preserve same event IDs in different monitors',()=>{
 const input=[{id:'m1',spec:{path:'repo1'},events:[{id:'same',status:'healthy'}]},{id:'m2',spec:{path:'repo2'},events:[{id:'same',status:'healthy'}]}];
 assert.equal(monitorEvents(input,'repo1').length,1);assert.equal(new Set(monitorEvents(input).map(e=>e.key)).size,2);
});
test('unknown and review-required external outcomes are never styled as verified success',()=>{
 for(const status of ['push_unknown','creation_unknown','close_unknown','closed-review-required','remote-created-review-required'])assert.equal(statusTone(status),'warning');
 assert.equal(statusTone('closed-verified'),'success');assert.equal(statusTone('stale'),'warning');
});
test('only the original completed operation matches a pending UI request',()=>{
 const jobs=[{id:'mine',status:'running'},{id:'other',status:'finished',result:{}}];assert.equal(completedOperation(jobs,'mine'),null);
 assert.equal(completedOperation(jobs,'lost'),null);assert.equal(completedOperation(jobs,'other'),jobs[1]);
});
test('overview review links reach manual approvals when no Issue proposal is pending',()=>{
 assert.equal(summarize({plans:[{status:'awaiting_approval'}]}).approvalPage,'tasks');
 assert.equal(summarize({plans:[{status:'awaiting_approval'}],issue_workbench:{approvals:[{status:'pending'}]}}).approvalPage,'issues');
});
