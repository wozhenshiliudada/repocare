import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createHash} from 'node:crypto';
import {SessionManager} from '@earendil-works/pi-coding-agent';
import {effectiveModelReasoning,providerModel,reasoningStreamOptions,REASONING_FIELDS} from '../provider-model.mjs';
import {preparePayload} from '../provider-payload.mjs';
import {generatePlan} from '../plan.mjs';
import {reflect} from '../reflect.mjs';
import {proposeValidation} from '../validation-plan.mjs';
import {compactTask} from '../compact.mjs';
import {checkConnectivity} from '../connectivity.mjs';
import {converse} from '../assistant.mjs';
import {experienceHash} from '../experience-context.mjs';

const sha=value=>createHash('sha256').update(value).digest('hex');
const repair={issue:'Repair addition',base_sha:'a'.repeat(40),files:[{path:'calculator.py',
 before_sha256:sha('def add(a,b):\n return a - b\n'),content:'def add(a,b):\n return a - b\n'}],tests:[],request_budget:2};
const proposal={name:'propose_repair',args:{diagnosis:'addition uses subtraction',risk:'low',
 edits:[{path:'calculator.py',replacements:[{oldText:'a - b',newText:'a + b'}]}]}};
const mappedLevels=[{level:'off',api_value:null},{level:'minimal',api_value:'low'},{level:'xhigh',api_value:'high'},{level:'max',api_value:'max'}];
async function selected(protocol,effort,run,levels=null){
 const previous=REASONING_FIELDS.map(field=>process.env[field]);
 process.env[REASONING_FIELDS[0]]=protocol;process.env[REASONING_FIELDS[1]]=effort;
 if(levels===null)delete process.env[REASONING_FIELDS[2]];else process.env[REASONING_FIELDS[2]]=JSON.stringify(levels);
 try{return await run();}finally{REASONING_FIELDS.forEach((field,index)=>{
  if(previous[index]===undefined)delete process.env[field];else process.env[field]=previous[index];});}
}
async function root(t){
 const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pi-provider-reasoning-'));
 t.after(async()=>{assert.equal(path.dirname(path.resolve(directory)),path.resolve(os.tmpdir()));
  assert.ok(path.basename(directory).startsWith('pi-provider-reasoning-'));await fs.rm(directory,{recursive:true,force:true});});
 return directory;
}
async function mock(t,script){
 const requests=[];
 const server=http.createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;
  const body=JSON.parse(raw);requests.push(body);const turn=requests.length,reply=script(body,turn);
  const delta=reply.name?{role:'assistant',reasoning_content:`reason ${turn}`,tool_calls:[{index:0,id:`call_${turn}`,
   type:'function',function:{name:reply.name,arguments:JSON.stringify(reply.args)}}]}:
   {role:'assistant',reasoning_content:`reason ${turn}`,content:reply.content??'Completed'};
  res.writeHead(200,{'content-type':'text/event-stream'});
  res.end('data: '+JSON.stringify({id:'local',object:'chat.completion.chunk',created:1,model:body.model,
   choices:[{index:0,delta,finish_reason:reply.name?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n');
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 t.after(()=>new Promise(resolve=>{server.closeAllConnections();server.close(resolve);}));
 return {url:`http://127.0.0.1:${server.address().port}/v1`,requests};
}
function assertDeepSeek(requests,effort='max'){
 assert.ok(requests.length>0);
 for(const body of requests){assert.deepEqual(body.thinking,{type:effort==='off'?'disabled':'enabled'});
  if(effort==='off')assert.equal(body.reasoning_effort,undefined);else assert.equal(body.reasoning_effort,effort);}
}

test('legacy profiles retain old payload behavior; none is explicit and adds no thinking parameters',()=>{
 const legacy=effectiveModelReasoning({});assert.equal(legacy.explicit,false);
 assert.equal(legacy.thinkingLevel,'off');assert.deepEqual(reasoningStreamOptions(legacy),{});
 assert.deepEqual(preparePayload({},'https://api.deepseek.com',legacy),{thinking:{type:'disabled'}});
 assert.deepEqual(preparePayload({},'https://unknown.invalid',legacy),{});
 const none=effectiveModelReasoning({PI_MAINTENANCE_REASONING_PROTOCOL:'none',PI_MAINTENANCE_REASONING_EFFORT:'off'});
 assert.deepEqual(preparePayload({},'https://api.deepseek.com',none),{});
 assert.equal(providerModel({reasoning:false,compat:{}},none).reasoning,false);
});

test('reasoning validates paired fields and canonical protocol-specific levels without model guessing',()=>{
 for(const env of [{PI_MAINTENANCE_REASONING_PROTOCOL:'deepseek'},
  {PI_MAINTENANCE_REASONING_EFFORT:'high'},
  {PI_MAINTENANCE_REASONING_PROTOCOL:'invented',PI_MAINTENANCE_REASONING_EFFORT:'high'},
  {PI_MAINTENANCE_REASONING_PROTOCOL:'none',PI_MAINTENANCE_REASONING_EFFORT:'high'},
  {PI_MAINTENANCE_REASONING_PROTOCOL:'deepseek',PI_MAINTENANCE_REASONING_EFFORT:'medium'}])
  assert.throws(()=>effectiveModelReasoning(env));
 for(const effort of ['off','minimal','low','medium','high','xhigh','max'])
  assert.equal(effectiveModelReasoning({PI_MAINTENANCE_REASONING_PROTOCOL:'openai',PI_MAINTENANCE_REASONING_EFFORT:effort}).effort,effort);
});

test('real Pi repair sends each explicit wire setting unchanged and no retry or heuristic',async t=>{
 for(const [protocol,effort] of [['deepseek','max'],['deepseek','off'],['openai','high'],['none','off']]){
  const server=await mock(t,()=>proposal);
  await selected(protocol,effort,async()=>{
   const result=await generatePlan(repair,{mockUrl:server.url});assert.equal(result.status,'completed');
   assert.equal(server.requests.length,1);const body=server.requests[0];
   if(protocol==='deepseek')assertDeepSeek(server.requests,effort);
   else {assert.equal(body.thinking,undefined);assert.equal(body.reasoning_effort,protocol==='none'?undefined:effort);}
  });
 }
});

test('DeepSeek thinking survives a repair tool round with reasoning_content replay',async t=>{
 const server=await mock(t,(_body,turn)=>turn===1?{name:'read_source',args:{path:'calculator.py'}}:proposal);
 await selected('deepseek','xhigh',async()=>{
  const result=await generatePlan(repair,{mockUrl:server.url});assert.equal(result.status,'completed');
  assert.equal(server.requests.length,2);assertDeepSeek(server.requests,'high');
  const assistant=server.requests[1].messages.find(message=>message.role==='assistant');
  assert.equal(assistant.reasoning_content,'reason 1');assert.ok(assistant.tool_calls.length);
 },mappedLevels);
});

test('DeepSeek assistant retains tool and final-turn reasoning across its original Native session',async t=>{
 const directory=await root(t),server=await mock(t,(_body,turn)=>turn===1?{name:'get_help',args:{}}:{content:'Useful answer'});
 await selected('deepseek','xhigh',async()=>{
  const input={schema_version:1,request_budget:3,directory,text:'How does this work?',help:'Local help',language:'en'};
  const first=await converse(input,{mockUrl:server.url});assert.equal(first.text,'Useful answer');
  const file=path.join(directory,'sessions',first.native_file),previous=await fs.readFile(file);
  const next=await converse({...input,native_file:first.native_file,text:'Explain the next part'},{mockUrl:server.url});
  assert.equal(next.text,'Useful answer');assert.equal(server.requests.length,3);assertDeepSeek(server.requests,'high');
  const assistants=server.requests[2].messages.filter(message=>message.role==='assistant');
  assert.equal(assistants.length,2);assert.deepEqual(assistants.map(message=>message.reasoning_content),['reason 1','reason 2']);
  assert.ok((await fs.readFile(file)).subarray(0,previous.length).equals(previous));
 },mappedLevels);
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
test('real Pi reflection uses explicit thinking in its separate data-only purpose',async t=>{
 const args={kind:'procedure',title:'Signs',summary:'Historical negative-case failure',triggers:['negative case'],preconditions:['Read source'],
  steps:['Check signs'],verification:['Run authorized tests'],stop_conditions:['Changed evidence']};
 const server=await mock(t,()=>({name:'propose_memory',args}));
 await selected('deepseek','xhigh',async()=>{
  const result=await reflect({source:reflectionSource(),request_budget:1},{mockUrl:server.url});
  assert.equal(result.status,'completed');assertDeepSeek(server.requests,'high');
 },mappedLevels);
});

test('real Pi readonly validation uses selected thinking and keeps its Native purpose',async t=>{
 const directory=await root(t),taskId='a'.repeat(32),cwd=path.join(directory,taskId,'workspace'),sessions=path.join(directory,taskId,'sessions');
 await fs.mkdir(cwd,{recursive:true});await fs.mkdir(sessions);
 const task={mode:'create',binding:{schema_version:1,task_id:taskId,revision:0,cwd,sessions_dir:sessions},
  context:{schema_version:2,purpose:'validation-proposal-v1',provider:'pi-loopback',model:'mock-maintenance',paths:['calculator.py'],thinking_level:'xhigh'}};
 const source={schema_version:1,purpose:'validation-proposal-v1',runtime_authorized:false,issue:repair.issue,
  request_sha256:sha(repair.issue),files:repair.files.map(file=>({...file,editable:false}))},sourceJson=JSON.stringify(source);
 const args={requirement:'signed addition',rationale:'addition requirement',limitations:'Python only',clarification:false,
  cases:[{id:'negative',module:'calculator.py',callable:'add',args:[-2,1],kwargs:{},expect:{kind:'json',value:-1},missing_behavior:false}]};
 const server=await mock(t,()=>({name:'propose_validation',args}));
 await selected('deepseek','xhigh',async()=>{
  const result=await proposeValidation({purpose:'validation-proposal-v1',source_json:sourceJson,source_sha256:sha(sourceJson),task_session:task,request_budget:2},{mockUrl:server.url});
  assert.equal(result.status,'completed');assertDeepSeek(server.requests,'high');
 },mappedLevels);
});

test('real Pi native compaction uses explicit thinking and preserves the original history prefix',async t=>{
 const directory=await root(t),id='a'.repeat(32),cwd=path.join(directory,id,'workspace'),sessions=path.join(directory,id,'sessions');
 await fs.mkdir(cwd,{recursive:true});await fs.mkdir(sessions);const manager=SessionManager.create(cwd,sessions);
 const provider='pi-loopback',model='mock-maintenance';manager.appendModelChange(provider,model);manager.appendThinkingLevelChange('xhigh');
 const usage={input:1000,output:20,cacheRead:0,cacheWrite:0,reasoning:0,totalTokens:1020,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
 for(let n=0;n<3;n++){manager.appendMessage({role:'user',content:'Inspect signed addition. '.repeat(100),timestamp:1});
  manager.appendMessage({role:'assistant',api:'openai-completions',provider,model,thinkingLevel:'xhigh',content:[{type:'text',text:'Historical diagnosis. '.repeat(150)}],usage,stopReason:'stop',timestamp:1});}
 const file=manager.getSessionFile(),previous=await fs.readFile(file),config={schema_version:1,enabled:true,context_window_tokens:1048576,
  trigger_tokens:20000,keep_recent_tokens:256,summary_max_output_tokens:2048};
 const binding={schema_version:1,task_id:id,revision:3,session_file:file,cwd,session_id:manager.getSessionId(),leaf_id:manager.getLeafId(),
  file_sha256:sha(previous),file_bytes:previous.length,tail_uncommitted:false};
 const body={schema_version:1,task_id:id,scope_sha256:'b'.repeat(64),checkpoint_sha256:'c'.repeat(64),config,
  task_session:{mode:'open',binding,context:{provider,model,paths:['calculator.py'],thinking_level:'xhigh'}}};
 const server=await mock(t,()=>({content:'Keep original goal and revalidate current source.'}));
 await selected('deepseek','xhigh',async()=>{
  const result=await compactTask({source:{...body,context_sha256:experienceHash(body)},request_budget:2},{mockUrl:server.url});
  assert.equal(result.status,'completed');assertDeepSeek(server.requests,'high');
  assert.ok((await fs.readFile(file)).subarray(0,previous.length).equals(previous));
 },mappedLevels);
});

test('real Pi connectivity carries selected thinking while keeping exactly one invocation',async t=>{
 const server=await mock(t,()=>({content:'REPOCARE_CONNECTIVITY_OK'}));
 await selected('deepseek','xhigh',async()=>{
  const result=await checkConnectivity({protocol_version:1,request_budget:1,preview_revision:'a'.repeat(64)},{mockUrl:server.url});
  assert.equal(result.status,'completed');assert.equal(server.requests.length,1);assertDeepSeek(server.requests,'high');
 },mappedLevels);
});

test('explicit mapping accepts only configured subsets and precise bounded API values',()=>{
 const env={PI_MAINTENANCE_REASONING_PROTOCOL:'deepseek',PI_MAINTENANCE_REASONING_EFFORT:'minimal',PI_MAINTENANCE_REASONING_LEVELS:JSON.stringify(mappedLevels)};
 assert.equal(effectiveModelReasoning(env).apiValue,'low');
 for(const levels of [[],[...mappedLevels,mappedLevels[0]],[{level:'invented',api_value:'low'}],
  [{level:'minimal',api_value:'has whitespace'}],[{level:'off',api_value:'low'}],[{level:'minimal',api_value:'a'.repeat(65)}]])
  assert.throws(()=>effectiveModelReasoning({...env,PI_MAINTENANCE_REASONING_LEVELS:JSON.stringify(levels)}));
 assert.throws(()=>effectiveModelReasoning({...env,PI_MAINTENANCE_REASONING_EFFORT:'medium'}));
 assert.throws(()=>effectiveModelReasoning({...env,PI_MAINTENANCE_REASONING_LEVELS:'invalid-json'}));
});

test('real Pi payload applies custom and null mapping values without canonical fallback',async t=>{
 for(const [protocol,effort,apiValue] of [['deepseek','minimal','low'],['deepseek','max','custom_budget'],
  ['deepseek','high',null],['openai','off',null],['openai','off','none'],['openai','max','custom-effort']]){
  const server=await mock(t,()=>proposal);
  await selected(protocol,effort,async()=>{
   const result=await generatePlan(repair,{mockUrl:server.url});assert.equal(result.status,'completed');
   assert.equal(server.requests.length,1);assert.equal(server.requests[0].reasoning_effort,apiValue===null?undefined:apiValue);
   if(protocol==='deepseek')assert.deepEqual(server.requests[0].thinking,{type:'enabled'});else assert.equal(server.requests[0].thinking,undefined);
  },[{level:effort,api_value:apiValue}]);
 }
});
