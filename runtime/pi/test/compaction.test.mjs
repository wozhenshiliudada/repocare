import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createHash} from 'node:crypto';
import {SessionManager} from '@earendil-works/pi-coding-agent';
import {compactTask,completeKeptBoundary} from '../compact.mjs';
import {experienceHash} from '../experience-context.mjs';
import {INVESTIGATION_PURPOSE} from '../investigation-tool.mjs';
import {verifiedContextPolicy,inputUpperEstimate} from '../compaction-settings.mjs';
import {startMock} from './mock-server.mjs';

const config={schema_version:1,enabled:true,context_window_tokens:1048576,trigger_tokens:700000,
 keep_recent_tokens:256,summary_max_output_tokens:4096};
const sha=raw=>createHash('sha256').update(raw).digest('hex');
async function fixture(t,reply,context){
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'pi-compaction-'));
 t.after(async()=>{const target=path.resolve(root),base=path.resolve(os.tmpdir());
  assert.equal(path.dirname(target),base);assert.ok(path.basename(target).startsWith('pi-compaction-'));
  await fs.rm(target,{recursive:true,force:true});});
 const mock=await startMock({script:()=>reply});t.after(()=>mock.close());
 const task='a'.repeat(32),cwd=path.join(root,task,'workspace'),directory=path.join(root,task,'sessions');
 await fs.mkdir(cwd,{recursive:true});await fs.mkdir(directory);
 const manager=SessionManager.create(cwd,directory),provider='pi-loopback',model='mock-maintenance';
 manager.appendModelChange(provider,model);manager.appendThinkingLevelChange('off');
 const usage={input:1000,output:20,cacheRead:0,cacheWrite:0,reasoning:0,totalTokens:1020,
  cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
 for(let n=0;n<3;n++){
  const timestamp=Date.now();manager.appendMessage({role:'user',content:'Inspect signed addition. '.repeat(100),timestamp});
  manager.appendMessage({role:'assistant',api:'openai-completions',provider,model,
   content:[{type:'toolCall',id:`read-${n}`,name:'read_source',arguments:{path:'calculator.py'}}],usage,stopReason:'toolUse',timestamp});
  manager.appendMessage({role:'toolResult',toolCallId:`read-${n}`,toolName:'read_source',
   content:[{type:'text',text:'return a - b\n'+'# historical snapshot\n'.repeat(100)}],isError:false,timestamp});
  manager.appendMessage({role:'assistant',api:'openai-completions',provider,model,
   content:[{type:'toolCall',id:`plan-${n}`,name:'propose_repair',arguments:{edits:[{path:'calculator.py'}]}}],usage,stopReason:'toolUse',timestamp});
  manager.appendMessage({role:'toolResult',toolCallId:`plan-${n}`,toolName:'propose_repair',
   content:[{type:'text',text:'{"accepted":true}'}],isError:false,timestamp});
 }
 const file=manager.getSessionFile(),raw=await fs.readFile(file);
 const binding={schema_version:1,task_id:task,revision:3,session_file:file,cwd,session_id:manager.getSessionId(),
  leaf_id:manager.getLeafId(),file_sha256:sha(raw),file_bytes:raw.length,tail_uncommitted:false};
 const body={schema_version:1,task_id:task,scope_sha256:'b'.repeat(64),checkpoint_sha256:'c'.repeat(64),config,
  task_session:{mode:'open',binding,context:context??{provider,model,paths:['calculator.py']}}};
 return {raw,file,mock,request:{source:{...body,context_sha256:experienceHash(body)},request_budget:50}};
}

test('controlled context settings reject coercions, unknown capacity and output overflow',()=>{
 assert.deepEqual(verifiedContextPolicy(config),config);
 for(const patch of [{enabled:1},{trigger_tokens:true},{context_window_tokens:0},{summary_max_output_tokens:Number.MAX_SAFE_INTEGER+1},
  {trigger_tokens:1048000},{keep_recent_tokens:700000},{commands:['git']}])
  assert.throws(()=>verifiedContextPolicy({...config,...patch}));
 assert.equal(verifiedContextPolicy(undefined),null);
 assert.equal(inputUpperEstimate({text:'中文'}),Buffer.byteLength(JSON.stringify({text:'中文'}))+4096);
});

test('large summary policies use safe integers and reserve context room without a fixed output ceiling',()=>{
 const large={...config,context_window_tokens:4000000,trigger_tokens:2000000,keep_recent_tokens:300000,summary_max_output_tokens:1500000};
 assert.deepEqual(verifiedContextPolicy(large),large);
 for(const patch of [{summary_max_output_tokens:2000000},{keep_recent_tokens:2000000},
  {context_window_tokens:Number.MAX_SAFE_INTEGER+1},{trigger_tokens:Number.MAX_SAFE_INTEGER+1}])
  assert.throws(()=>verifiedContextPolicy({...large,...patch}));
});

test('kept native boundary must be a complete tool pair boundary',()=>{
 const branch=[{id:'u',type:'message',message:{role:'user'}},
  {id:'a',type:'message',message:{role:'assistant',content:[{type:'toolCall',id:'read'}]}},
  {id:'r',type:'message',message:{role:'toolResult',toolCallId:'read'}},
  {id:'next',type:'message',message:{role:'assistant',content:[]}}];
 assert.equal(completeKeptBoundary(branch,'a'),true);
 assert.equal(completeKeptBoundary(branch,'r'),false);
 assert.equal(completeKeptBoundary(branch,'next'),true);
 assert.equal(completeKeptBoundary(branch,'missing'),false);
});

test('fixed native compactor appends a metered receipt without rewriting original bytes',async t=>{
 const f=await fixture(t,{content:'Goal: fix addition; earlier failures are historical. Verify current source and tests.'});
 const result=await compactTask(f.request,{mockUrl:f.mock.url});
 assert.equal(result.status,'completed');assert.ok(result.evidence.requests>0);
 assert.equal(result.evidence.requests,f.mock.requests.length);assert.deepEqual(result.evidence.active_tools,[]);
 assert.equal(result.evidence.compaction_max_output_tokens,4096);
 const raw=await fs.readFile(f.file);assert.ok(raw.subarray(0,f.raw.length).equals(f.raw));
 const entry=JSON.parse(raw.toString().trimEnd().split('\n').at(-1));assert.equal(entry.type,'compaction');
 assert.equal(result.compaction.entry_sha256,experienceHash(entry));
 assert.ok(f.mock.requests.every(body=>!body.tools&&body.max_tokens>0&&body.max_tokens<=4096));
});

const investigationContext=()=>({schema_version:2,purpose:INVESTIGATION_PURPOSE,
 provider:'pi-loopback',model:'mock-maintenance',paths:['calculator.py'],
 investigation:{schema_version:1,read_roots:['logic'],max_calls:4,max_output_bytes:32768}});

test('native compaction reopens the pinned investigation purpose without enabling tools',async t=>{
 const context=investigationContext();
 const f=await fixture(t,{content:'Keep the original approved investigation and repair boundaries.'},context);
 const result=await compactTask(f.request,{mockUrl:f.mock.url});
 assert.equal(result.status,'completed');assert.ok(result.evidence.requests>0);
 assert.equal(result.evidence.requests,f.mock.requests.length);assert.deepEqual(result.evidence.active_tools,[]);
 assert.equal(result.evidence.tool_calls,0);assert.equal(result.compaction.session_id,f.request.source.task_session.binding.session_id);
 assert.equal(result.compaction.previous_leaf_id,f.request.source.task_session.binding.leaf_id);
 assert.deepEqual(f.request.source.task_session.context,context);
 const after=await fs.readFile(f.file);assert.ok(after.subarray(0,f.raw.length).equals(f.raw));
 assert.equal(JSON.parse(after.toString().trimEnd().split('\n').at(-1)).type,'compaction');
 assert.ok(f.mock.requests.every(body=>!body.tools));
});

for(const [label,change] of [
 ['unknown purpose',context=>({...context,purpose:'unknown-purpose'})],
 ['stripped purpose',context=>{delete context.purpose;return context;}],
 ['legacy downgrade',context=>({...context,purpose:'repair'})],
 ['unknown schema',context=>({...context,schema_version:3})],
 ['expanded read roots',context=>({...context,investigation:{...context.investigation,read_roots:['../secret']}})]
])test(`compaction rejects ${label} before summary requests or Native writes`,async t=>{
 const f=await fixture(t,{content:'unused'},change(investigationContext()));
 await assert.rejects(compactTask(f.request,{mockUrl:f.mock.url}),/Unsupported Pi compaction task purpose|Pi (?:task runtime|investigation) context changed/);
 assert.equal(f.mock.requests.length,0);assert.deepEqual(await fs.readFile(f.file),f.raw);
});

for(const schema_version of [2,3])test(`compaction rejects read-only validation context v${schema_version}`,async t=>{
 const context={schema_version,purpose:'validation-proposal-v1',provider:'pi-loopback',model:'mock-maintenance',
  paths:['calculator.py'],...(schema_version===3?{validation_schema:'data-behavior-v2'}:{})};
 const f=await fixture(t,{content:'unused'},context);
 await assert.rejects(compactTask(f.request,{mockUrl:f.mock.url}),/Unsupported Pi compaction task purpose/);
 assert.equal(f.mock.requests.length,0);assert.deepEqual(await fs.readFile(f.file),f.raw);
});

for(const [label,reply] of [
 ['empty',{content:''}],['truncated',{content:'incomplete summary',finish:'length'}],
 ['tool call',{name:'read_source',args:{path:'.env.secret'}}],
 ['control character',{content:'invalid\u0000summary'}],['secret marker',{content:'sk-syntheticNotARealKey123456'}]
])test(`unsafe summary (${label}) never appends a native entry`,async t=>{
 const f=await fixture(t,reply);const result=await compactTask(f.request,{mockUrl:f.mock.url});
 assert.equal(result.status,'failed');assert.equal(result.evidence.consumption,'unknown');
 assert.ok(f.mock.requests.length>0);assert.deepEqual(await fs.readFile(f.file),f.raw);
});

test('changed controlled source is rejected before sending any summary request',async t=>{
 const f=await fixture(t,{content:'unused'});f.request.source.config={...config,enabled:false};
 await assert.rejects(compactTask(f.request,{mockUrl:f.mock.url}));
 assert.equal(f.mock.requests.length,0);assert.deepEqual(await fs.readFile(f.file),f.raw);
});
