import test from 'node:test';
import assert from 'node:assert/strict';
import {reflect} from '../reflect.mjs';
import {experienceHash} from '../experience-context.mjs';
import {startMock} from './mock-server.mjs';
const note={kind:'procedure',title:'Signed addition',summary:'The historical absolute-value patch failed negative cases.',
 triggers:['Negative addition failure'],preconditions:['Inspect current source'],steps:['Compare expected and actual signs'],
 verification:['Run currently authorized tests'],stop_conditions:['Stop on changed evidence or unavailable budget']};
function source(){
 const episode={schema_version:2,repository_sha256:'a'.repeat(64),run_id:'b'.repeat(32),task_id:'c'.repeat(32),
  classification:'historical',runtime_authorized:false,issue:'addition negative operands',base_sha:'d'.repeat(40),
  recorded_status:'rolled_back',outcome:'test_failed',rounds:[{number:1,plan_sha256:'e'.repeat(64),diagnosis:'negative case failed',
   change_excerpt:'return abs(a)+abs(b)',failure_excerpt:'AssertionError negative signs',verification:'failed',paths:['calculator.py'],test_exits:[1],test_timeouts:[false]}],
  policy_sha256:'f'.repeat(64),source_files:{'calculator.py':'a'.repeat(64)},
  evidence:{'run.json':'b'.repeat(64),'repair-execution.json':'c'.repeat(64),'report.md':'d'.repeat(64)}};
 const value={schema_version:1,repository_sha256:episode.repository_sha256,policy_sha256:episode.policy_sha256,
  source_files:{'calculator.py':'e'.repeat(64)},hits:[{id:episode.run_id,payload_sha256:experienceHash(episode),review_version:2,episode}]};
 return {...value,context_sha256:experienceHash(value)};
}
test('real Pi reflection exposes only data submission and returns original input and candidate receipts',async()=>{
 const mock=await startMock({script:()=>({name:'propose_memory',args:note})});
 try{const input=source();const result=await reflect({source:input,request_budget:2},{mockUrl:mock.url});
  assert.equal(result.status,'completed');assert.deepEqual(result.candidate,note);assert.equal(result.evidence.requests,1);
  assert.equal(result.evidence.purpose,'memory_reflection');assert.equal(result.evidence.reflection_context_sha256,input.context_sha256);
  assert.equal(result.evidence.knowledge_candidate_sha256,experienceHash(note));
  assert.equal(result.candidate_sha256,experienceHash(note));assert.equal(mock.requests[0].max_tokens,128000);
  assert.deepEqual(mock.requests[0].tools.map(t=>t.function.name),['propose_memory']);
 }finally{await mock.close();}
});

test('new version3 episode can reach the existing reflection tool without gaining repair tools',async()=>{
 const input=source();const hit=input.hits[0];hit.episode.schema_version=3;
 hit.episode.rounds[0].failure_excerpt='E AttributeError: original failure';
 hit.payload_sha256=experienceHash(hit.episode);
 const {context_sha256,...body}=input;input.context_sha256=experienceHash(body);
 const mock=await startMock({script:()=>({name:'propose_memory',args:note})});
 try{
  const result=await reflect({source:input,request_budget:1},{mockUrl:mock.url});
  assert.equal(result.status,'completed');assert.deepEqual(result.candidate,note);
  assert.equal(result.evidence.requests,1);
  assert.deepEqual(mock.requests[0].tools.map(t=>t.function.name),['propose_memory']);
 }finally{await mock.close();}
});
test('invalid reflection and truncated summary never become candidates or gain a retry budget',async()=>{
 for(const script of [()=>({name:'propose_memory',args:{...note,commands:['git']}}),()=>({content:'partial candidate',finish:'length'})]){
  const mock=await startMock({script});
  try{const result=await reflect({source:source(),request_budget:1},{mockUrl:mock.url});
   assert.equal(result.status,'failed');assert.equal(result.candidate,undefined);assert.equal(mock.requests.length,1);
   assert.equal(result.evidence.requests,null);assert.equal(result.evidence.consumption,'unknown');
  }finally{await mock.close();}
 }
});
test('malformed original evidence stops reflection before any model request',async()=>{
 const mock=await startMock({script:()=>({name:'propose_memory',args:note})});
 try{const input=source();input.hits[0].episode.runtime_authorized=true;
  await assert.rejects(()=>reflect({source:input,request_budget:1},{mockUrl:mock.url}));assert.equal(mock.requests.length,0);
 }finally{await mock.close();}
});

function correction(){
 const {context_sha256,...value}=source();value.schema_version=2;
 value.previous={id:'8'.repeat(32),payload_sha256:'9'.repeat(64),source_sha256:'7'.repeat(64),note:structuredClone(note),
  review:'accepted',review_version:2,classification:'proposed',runtime_authorized:false};
 return {...value,context_sha256:experienceHash(value)};
}
test('real Pi correction receives historical old note and new episodes without new tools or inherited approval',async()=>{
 const mock=await startMock({script:()=>({name:'propose_memory',args:note})});
 try{const input=correction(),result=await reflect({source:input,request_budget:1},{mockUrl:mock.url});
  assert.equal(result.status,'completed');assert.equal(result.evidence.reflection_context_sha256,input.context_sha256);
  assert.deepEqual(mock.requests[0].tools.map(t=>t.function.name),['propose_memory']);
  const user=mock.requests[0].messages.find(m=>m.role==='user');
  const data=JSON.parse(typeof user.content==='string'?user.content:user.content[0].text);
  assert.deepEqual(data.source.previous,input.previous);assert.deepEqual(data.source.hits,input.hits);
  assert.equal(result.evidence.requests,1);
 }finally{await mock.close();}
});
test('rehashed malformed correction references stop before SDK or credentials',async()=>{
 const mock=await startMock({script:()=>({name:'propose_memory',args:note})});
 try{for(const mutate of [v=>v.previous.runtime_authorized=true,v=>v.previous.review_version=true,
    v=>v.previous.commands=['git'],v=>v.previous.note.approved=true,v=>delete v.previous]){
   const {context_sha256,...body}=correction();mutate(body);
   await assert.rejects(()=>reflect({source:{...body,context_sha256:experienceHash(body)},request_budget:1},{mockUrl:mock.url}));
  }assert.equal(mock.requests.length,0);
 }finally{await mock.close();}
});

test('plain prose receives one metered format clarification in the same Pi session',async()=>{
 const mock=await startMock({script:(_body,number)=>number===1?{content:'I can describe a lesson in prose.'}:
  {name:'propose_memory',args:note}});
 try{const result=await reflect({source:source(),request_budget:2},{mockUrl:mock.url});
  assert.equal(result.status,'completed');assert.deepEqual(result.candidate,note);
  assert.equal(result.evidence.requests,2);assert.equal(mock.requests.length,2);
  assert.match(JSON.stringify(mock.requests[1].messages),/format_clarification/);
  assert.deepEqual(mock.requests[1].tools.map(t=>t.function.name),['propose_memory']);
 }finally{await mock.close();}
});

test('plain prose never becomes memory or gains requests beyond its existing allowance',async()=>{
 for(const allowance of [1,2]){
  const mock=await startMock({script:()=>({content:JSON.stringify(note)})});
  try{const result=await reflect({source:source(),request_budget:allowance},{mockUrl:mock.url});
   assert.equal(result.status,'failed');assert.equal(result.candidate,undefined);
   assert.equal(result.evidence.failure_category,'no_plan');
   assert.equal(result.evidence.requests,null);assert.equal(mock.requests.length,allowance);
   assert.equal(result.evidence.consumption,'unknown');
  }finally{await mock.close();}
 }
});

test('provider errors and truncated prose never trigger a format retry or expose model text',async()=>{
 for(const [finish,category] of [['network_error','provider_error'],['length','output_truncated']]){
  const mock=await startMock({script:()=>({content:'PRIVATE_PROVIDER_MESSAGE_DO_NOT_LOG',finish})});
  try{const result=await reflect({source:source(),request_budget:3},{mockUrl:mock.url});
   assert.equal(result.status,'failed');assert.equal(result.evidence.failure_category,category);
   assert.equal(result.evidence.requests_started,1);assert.equal(mock.requests.length,1);
   assert.equal(JSON.stringify(result).includes('PRIVATE_PROVIDER_MESSAGE_DO_NOT_LOG'),false);
  }finally{await mock.close();}
 }
});
