import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createHash} from 'node:crypto';
import {ModelRuntime, SessionManager} from '@earendil-works/pi-coding-agent';
import {effectiveModelLimits, MODEL_LIMIT_FIELDS} from '../model-limits.mjs';
import {generatePlan} from '../plan.mjs';
import {proposeValidation} from '../validation-plan.mjs';
import {reflect} from '../reflect.mjs';
import {compactTask} from '../compact.mjs';
import {checkConnectivity} from '../connectivity.mjs';
import {converse} from '../assistant.mjs';
import {experienceHash} from '../experience-context.mjs';
import {startMock} from './mock-server.mjs';

const sha=value=>createHash('sha256').update(value).digest('hex');
const request={issue:'Repair addition',base_sha:'a'.repeat(40),files:[{path:'calculator.py',
 before_sha256:sha('def add(a,b):\n return a - b\n'),content:'def add(a,b):\n return a - b\n'}],tests:[],request_budget:2};
const proposal={name:'propose_repair',args:{diagnosis:'addition uses subtraction',risk:'low',
 edits:[{path:'calculator.py',replacements:[{oldText:'a - b',newText:'a + b'}]}]}};
async function selected(context,output,run){
 const previous=MODEL_LIMIT_FIELDS.map(field=>process.env[field]);
 process.env[MODEL_LIMIT_FIELDS[0]]=String(context);process.env[MODEL_LIMIT_FIELDS[1]]=String(output);
 try{return await run();}finally{MODEL_LIMIT_FIELDS.forEach((field,index)=>{
  if(previous[index]===undefined)delete process.env[field];else process.env[field]=previous[index];});}
}
async function root(t,prefix='pi-selected-limits-'){
 const value=await fs.mkdtemp(path.join(os.tmpdir(),prefix));
 t.after(async()=>{assert.equal(path.dirname(path.resolve(value)),path.resolve(os.tmpdir()));
  assert.ok(path.basename(value).startsWith(prefix));await fs.rm(value,{recursive:true,force:true});});
 return value;
}
async function nativeTask(t,purpose){
 const directory=await root(t),task='a'.repeat(32),cwd=path.join(directory,task,'workspace'),sessions=path.join(directory,task,'sessions');
 await fs.mkdir(cwd,{recursive:true});await fs.mkdir(sessions);
 return {mode:'create',binding:{schema_version:1,task_id:task,revision:0,cwd,sessions_dir:sessions},
  context:{schema_version:2,purpose,provider:'pi-loopback',model:'mock-maintenance',paths:['calculator.py']}};
}

test('effective model limits preserve every historical default and reject explicit coercions',()=>{
 const generic=effectiveModelLimits({env:{},modelId:'generic',baseUrl:'https://provider.example.invalid/v1'});
 assert.equal(generic.contextWindowTokens,32768);assert.equal(generic.maxOutputTokens,128000);
 assert.equal(generic.enforceContextBudget,false);assert.equal(generic.explicit,false);
 assert.equal(effectiveModelLimits({env:{},modelId:'mimo-v2.6-flash',baseUrl:'https://api.xiaomimimo.com/v1'}).contextWindowTokens,1048576);
 assert.equal(effectiveModelLimits({env:{},mockUrl:'http://127.0.0.1:1/v1'}).contextWindowTokens,1048576);
 assert.equal(effectiveModelLimits({env:{},legacyContextWindowTokens:32768,legacyMaxOutputTokens:64}).maxOutputTokens,64);
 for(const value of [true,2048,0,'0','NaN','2048.0','01','+2048','1e3','128001'])
  assert.throws(()=>effectiveModelLimits({env:{PI_MAINTENANCE_MAX_OUTPUT_TOKENS:value}}));
 const small=effectiveModelLimits({env:{PI_MAINTENANCE_CONTEXT_WINDOW_TOKENS:'65536',PI_MAINTENANCE_MAX_OUTPUT_TOKENS:'2048'},
  contextPolicy:{context_window_tokens:32768}});
 assert.equal(small.contextWindowTokens,65536);assert.equal(small.budgetContextWindowTokens,32768);
 assert.equal(effectiveModelLimits({env:{PI_MAINTENANCE_MAX_OUTPUT_TOKENS:'2048'},contextPolicy:{context_window_tokens:1048576},
  legacyContextWindowTokens:1048576}).contextWindowTokens,32768);
 assert.equal(effectiveModelLimits({env:{PI_MAINTENANCE_CONTEXT_WINDOW_TOKENS:'262144'},legacyMaxOutputTokens:64}).maxOutputTokens,128000);
 assert.equal(effectiveModelLimits({env:{PI_MAINTENANCE_MAX_OUTPUT_TOKENS:'2048',PI_MAINTENANCE_MODEL:'mimo-v2.6-flash',
  PI_MAINTENANCE_BASE_URL:'https://api.xiaomimimo.com'},modelId:'deepseek-flash',baseUrl:'https://api.deepseek.com'}).contextWindowTokens,32768);
 const declared=(context,output)=>effectiveModelLimits({env:{PI_MAINTENANCE_CONTEXT_WINDOW_TOKENS:String(context),PI_MAINTENANCE_MAX_OUTPUT_TOKENS:String(output)}});
 for(const [context,output] of [[4000000,1500000],[Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER-4096]]){
  const result=declared(context,output);assert.equal(result.contextWindowTokens,context);assert.equal(result.maxOutputTokens,output);
 }
 for(const [context,output] of [[4000000,4000000],[4095,1],[Number.MAX_SAFE_INTEGER+1,1000000],[4000000,Number.MAX_SAFE_INTEGER+1]])
  assert.throws(()=>declared(context,output));
});

test('selected profiles including million-token output reach real Pi registration, prompt and payload',async()=>{
 const original=ModelRuntime.prototype.registerProvider;let expected,registrations=0;
 ModelRuntime.prototype.registerProvider=function(name,config){assert.equal(config.models[0].contextWindow,expected[0]);
  assert.equal(config.models[0].maxTokens,expected[1]);registrations++;return original.call(this,name,config);};
 try{for(const limits of [[65536,2048],[131072,4096],[4000000,1500000]]){
  expected=limits;const mock=await startMock({script:(_body,turn)=>turn===1?proposal:{content:'submitted'}});
  try{await selected(...limits,async()=>{
   const result=await generatePlan(request,{mockUrl:mock.url});assert.equal(result.evidence.max_output_tokens,limits[1]);
   assert.equal(mock.requests.length,1);
   for(const body of mock.requests){assert.equal(body.max_tokens,limits[1]);
    const message=body.messages.find(m=>m.role==='user'),packet=JSON.parse(typeof message.content==='string'?message.content:message.content[0].text);
    assert.equal(packet.max_output_tokens,limits[1]);}
  });}finally{await mock.close();}
 }}finally{ModelRuntime.prototype.registerProvider=original;}
 assert.equal(registrations,3);
});

test('a selected small context stops before a provider invocation',async()=>{
 const mock=await startMock();
 try{await selected(4096,1,()=>assert.rejects(generatePlan(request,{mockUrl:mock.url}),error=>{
  assert.equal(error.evidence.failure_category,'context_budget');assert.equal(error.evidence.requests_started,0);
  assert.equal(error.evidence.max_output_tokens,1);return true;}));assert.equal(mock.requests.length,0);
 }finally{await mock.close();}
});

test('supplier rejection is reported and never clamped, retried or treated as support',async()=>{
 const requests=[];const server=http.createServer(async(req,res)=>{let raw='';for await(const chunk of req)raw+=chunk;
  requests.push(JSON.parse(raw));res.writeHead(400,{'content-type':'application/json'});
  res.end(JSON.stringify({error:{message:'Configured output limit is unsupported'}}));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{await selected(65536,2048,()=>assert.rejects(generatePlan(request,{mockUrl:`http://127.0.0.1:${server.address().port}/v1`}),error=>{
  assert.equal(error.evidence.failure_category,'provider_error');assert.equal(error.evidence.consumption,'unknown');
  assert.equal(error.evidence.requests_started,1);return true;}));
  assert.equal(requests.length,1);assert.equal(requests[0].max_tokens,2048);
 }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
});

test('selected limits reach readonly validation and preserve its separate Native purpose',async t=>{
 const task=await nativeTask(t,'validation-proposal-v1'),source={schema_version:1,purpose:'validation-proposal-v1',
  runtime_authorized:false,issue:request.issue,request_sha256:sha(request.issue),files:request.files.map(f=>({...f,editable:false}))};
 const sourceJson=JSON.stringify(source),args={requirement:'signed addition',rationale:'addition requirement',limitations:'Python only',
  clarification:false,cases:[{id:'negative',module:'calculator.py',callable:'add',args:[-2,1],kwargs:{},expect:{kind:'json',value:-1},missing_behavior:false}]};
 const mock=await startMock({script:(_body,turn)=>turn===1?{name:'propose_validation',args}:{content:'submitted'}});
 t.after(()=>mock.close());await selected(4000000,1500000,async()=>{
  const result=await proposeValidation({purpose:'validation-proposal-v1',source_json:sourceJson,source_sha256:sha(sourceJson),task_session:task,request_budget:2},{mockUrl:mock.url});
  assert.equal(result.status,'completed');assert.equal(result.evidence.max_output_tokens,1500000);
  assert.ok(mock.requests.every(body=>body.max_tokens===1500000));
 });
});

function reflectionSource(){
 const episode={schema_version:2,repository_sha256:'a'.repeat(64),run_id:'b'.repeat(32),task_id:'c'.repeat(32),
  classification:'historical',runtime_authorized:false,issue:'signed addition',base_sha:'d'.repeat(40),recorded_status:'rolled_back',outcome:'test_failed',
  rounds:[{number:1,plan_sha256:'e'.repeat(64),diagnosis:'negative case failed',change_excerpt:'abs(a)+abs(b)',failure_excerpt:'AssertionError',verification:'failed',
   paths:['calculator.py'],test_exits:[1],test_timeouts:[false]}],policy_sha256:'f'.repeat(64),source_files:{'calculator.py':'a'.repeat(64)},
  evidence:{'run.json':'b'.repeat(64),'repair-execution.json':'c'.repeat(64),'report.md':'d'.repeat(64)}};
 const value={schema_version:1,repository_sha256:episode.repository_sha256,policy_sha256:episode.policy_sha256,
  source_files:{'calculator.py':'e'.repeat(64)},hits:[{id:episode.run_id,payload_sha256:experienceHash(episode),review_version:2,episode}]};
 return {...value,context_sha256:experienceHash(value)};
}
test('selected reflection limits are sent through the original data-only Pi session',async()=>{
 const args={kind:'procedure',title:'Signs',summary:'Historical negative-case failure',triggers:['negative case'],preconditions:['Read source'],
  steps:['Check signs'],verification:['Run authorized tests'],stop_conditions:['Changed evidence']};
 const mock=await startMock({script:()=>({name:'propose_memory',args})});
 try{await selected(4000000,1500000,async()=>{
  const result=await reflect({source:reflectionSource(),request_budget:1},{mockUrl:mock.url});
  assert.equal(result.status,'completed');assert.equal(result.evidence.max_output_tokens,1500000);
  assert.equal(mock.requests[0].max_tokens,1500000);
 });}finally{await mock.close();}
});

test('selected compaction respects the model output ceiling and keeps old Native prefix',async t=>{
 const directory=await root(t),id='a'.repeat(32),cwd=path.join(directory,id,'workspace'),sessions=path.join(directory,id,'sessions');
 await fs.mkdir(cwd,{recursive:true});await fs.mkdir(sessions);const manager=SessionManager.create(cwd,sessions);
 const provider='pi-loopback',model='mock-maintenance';manager.appendModelChange(provider,model);manager.appendThinkingLevelChange('off');
 const usage={input:1000,output:20,cacheRead:0,cacheWrite:0,reasoning:0,totalTokens:1020,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
 for(let n=0;n<3;n++){manager.appendMessage({role:'user',content:'Inspect signed addition. '.repeat(100),timestamp:1});
  manager.appendMessage({role:'assistant',api:'openai-completions',provider,model,content:[{type:'text',text:'Historical diagnosis. '.repeat(150)}],usage,stopReason:'stop',timestamp:1});}
 const file=manager.getSessionFile(),previous=await fs.readFile(file),config={schema_version:1,enabled:true,context_window_tokens:4000000,
  trigger_tokens:2000000,keep_recent_tokens:256,summary_max_output_tokens:1500000};
 const binding={schema_version:1,task_id:id,revision:3,session_file:file,cwd,session_id:manager.getSessionId(),leaf_id:manager.getLeafId(),
  file_sha256:sha(previous),file_bytes:previous.length,tail_uncommitted:false};
 const body={schema_version:1,task_id:id,scope_sha256:'b'.repeat(64),checkpoint_sha256:'c'.repeat(64),config,
  task_session:{mode:'open',binding,context:{provider,model,paths:['calculator.py']}}};
 const mock=await startMock({script:()=>({content:'Keep original goal and revalidate current source.'})});t.after(()=>mock.close());
 await selected(4000000,1500000,async()=>{const result=await compactTask({source:{...body,context_sha256:experienceHash(body)},request_budget:2},{mockUrl:mock.url});
  assert.equal(result.status,'completed');assert.equal(result.evidence.max_output_tokens,1500000);assert.equal(result.evidence.compaction_max_output_tokens,1500000);
  // The pinned SDK uses 80% of the reserve for history and 50% for a split-turn prefix.
  assert.ok(mock.requests.some(body=>body.max_tokens===1500000));
  assert.ok(mock.requests.every(body=>[1500000,937500].includes(body.max_tokens)),JSON.stringify(mock.requests.map(body=>body.max_tokens)));
  assert.ok((await fs.readFile(file)).subarray(0,previous.length).equals(previous));});
});

test('selected connectivity keeps its smaller single-response operation cap',async()=>{
 const mock=await startMock({script:()=>({content:'REPOCARE_CONNECTIVITY_OK'})});
 try{await selected(65536,32,async()=>{
  const result=await checkConnectivity({protocol_version:1,request_budget:1,preview_revision:'a'.repeat(64)},{mockUrl:mock.url});
  assert.equal(result.status,'completed');assert.equal(mock.requests[0].max_tokens,32);
 });}finally{await mock.close();}
});

test('million-token assistant declarations reach a real Pi session and exact local payload',async t=>{
 const directory=await root(t),mock=await startMock({script:()=>({content:'Local synthetic response.'})});t.after(()=>mock.close());
 await selected(4000000,1500000,async()=>{
  const result=await converse({schema_version:1,request_budget:1,directory,text:'Hello',help:'Local guide',language:'en'},{mockUrl:mock.url});
  assert.equal(result.text,'Local synthetic response.');assert.equal(result.max_output_tokens,1500000);
  assert.equal(mock.requests.length,1);assert.equal(mock.requests[0].max_tokens,1500000);
 });
});

test('million-token connectivity model registration preserves the deliberately small probe payload',async()=>{
 const original=ModelRuntime.prototype.registerProvider;let registrations=0;
 ModelRuntime.prototype.registerProvider=function(name,config){
  assert.equal(config.models[0].contextWindow,4000000);assert.equal(config.models[0].maxTokens,1500000);registrations++;
  return original.call(this,name,config);
 };
 const mock=await startMock({script:()=>({content:'REPOCARE_CONNECTIVITY_OK'})});
 try{await selected(4000000,1500000,async()=>{
  const result=await checkConnectivity({protocol_version:1,request_budget:1,preview_revision:'a'.repeat(64)},{mockUrl:mock.url});
  assert.equal(result.status,'completed');assert.equal(mock.requests.length,1);assert.equal(mock.requests[0].max_tokens,64);
 });assert.equal(registrations,1);
 }finally{ModelRuntime.prototype.registerProvider=original;await mock.close();}
});
