import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createHash} from 'node:crypto';
import {SessionManager} from '@earendil-works/pi-coding-agent';
import {generatePlan} from '../plan.mjs';
import {startMock} from './mock-server.mjs';

const content='def add(a, b):\n    return a - b\n';
const sha=raw=>createHash('sha256').update(raw).digest('hex');
const request={issue:'Repair addition',base_sha:'a'.repeat(40),request_budget:5,
  files:[{path:'calculator.py',content,before_sha256:sha(content)}],tests:[['python','test.py']]};

test('reopened accepted history cannot replace a new proposal; one plain answer gets a bounded reminder',async t=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'pi-fresh-turn-'));
  t.after(async()=>{
    assert.equal(path.dirname(path.resolve(root)),path.resolve(os.tmpdir()));
    assert.ok(path.basename(root).startsWith('pi-fresh-turn-'));
    await fs.rm(root,{recursive:true,force:true});
  });
  const task='a'.repeat(32),cwd=path.join(root,task,'workspace'),directory=path.join(root,task,'sessions');
  await fs.mkdir(cwd,{recursive:true});await fs.mkdir(directory);
  const manager=SessionManager.create(cwd,directory),provider='pi-loopback',model='mock-maintenance';
  manager.appendModelChange(provider,model);manager.appendThinkingLevelChange('off');
  const timestamp=Date.now(),usage={input:1,output:1,cacheRead:0,cacheWrite:0,reasoning:0,totalTokens:2,
    cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
  manager.appendMessage({role:'user',content:'Repair addition',timestamp});
  manager.appendMessage({role:'assistant',api:'openai-completions',provider,model,timestamp,usage,
    stopReason:'toolUse',content:[{type:'toolCall',id:'historical-proposal',name:'propose_repair',arguments:{}}]});
  manager.appendMessage({role:'toolResult',toolCallId:'historical-proposal',toolName:'propose_repair',
    content:[{type:'text',text:'{"accepted":true}'}],isError:false,timestamp});
  manager.appendMessage({role:'assistant',api:'openai-completions',provider,model,timestamp,usage,
    stopReason:'stop',content:[{type:'text',text:'The proposal was accepted and applied.'}]});
  const file=manager.getSessionFile(),raw=await fs.readFile(file);
  const binding={schema_version:1,task_id:task,revision:1,session_file:file,cwd,session_id:manager.getSessionId(),
    leaf_id:manager.getLeafId(),file_sha256:sha(raw),file_bytes:raw.length,tail_uncommitted:false};
  const mock=await startMock({script:(body,n)=>{
    assert.ok(body.messages.some(m=>m.role==='tool'&&m.content.includes('"accepted":true')));
    if(n===1)return {content:'The previous patch is already applied. Please run the tests.'};
    const user=body.messages.findLast(m=>m.role==='user').content;
    const packet=JSON.parse(typeof user==='string'?user:user[0].text);
    assert.equal(packet.planning_turn.phase,'submission_reminder');
    assert.equal(packet.planning_turn.historical_proposals_are_current,false);
    if(n===2)return {name:'read_source',args:{path:'calculator.py'}};
    return {name:'propose_repair',args:{diagnosis:'Current subtraction needs addition',risk:'low',
      edits:[{path:'calculator.py',replacements:[{oldText:'return a - b',newText:'return a + b'}]}]}};
  }});t.after(()=>mock.close());
  const result=await generatePlan({...request,task_session:{mode:'open',binding,
    context:{provider,model,paths:['calculator.py']}}},{mockUrl:mock.url});
  assert.equal(result.status,'completed');assert.equal(result.evidence.requests,3);
  assert.equal(mock.requests.length,3);assert.equal(result.plan.edits[0].content,content.replace('a - b','a + b'));
  assert.equal(result.task_checkpoint.task_id,task);assert.equal(result.task_checkpoint.session_id,binding.session_id);
  assert.notEqual(result.task_checkpoint.leaf_id,binding.leaf_id);
  const after=await fs.readFile(file);assert.ok(after.subarray(0,raw.length).equals(raw));
  assert.equal(request.files[0].content,content);
});

for(const budget of [1,5])test(`plain answers stop after at most one reminder within budget ${budget}`,async()=>{
  const mock=await startMock({failure:'no_plan'});
  try{
    await assert.rejects(generatePlan({...request,request_budget:budget},{mockUrl:mock.url}),e=>{
      assert.equal(e.evidence.failure_category,'no_plan');assert.equal(e.evidence.requests,Math.min(budget,2));
      assert.equal(e.evidence.consumption,'reported');assert.equal(e.evidence.tool_calls,0);return true;
    });
    assert.equal(mock.requests.length,Math.min(budget,2));
  }finally{await mock.close();}
});

test('a turn that used tools and reports insufficient scope is not prompted to invent a patch',async()=>{
  const mock=await startMock({script:(_body,n)=>n===1?{name:'read_source',args:{path:'calculator.py'}}:
    {content:'Insufficient authorized evidence; no repair proposed.'}});
  try{
    await assert.rejects(generatePlan(request,{mockUrl:mock.url}),e=>{
      assert.equal(e.evidence.failure_category,'no_plan');assert.equal(e.evidence.requests,2);
      assert.equal(e.evidence.tool_calls,1);return true;
    });
    assert.equal(mock.requests.length,2);
  }finally{await mock.close();}
});
