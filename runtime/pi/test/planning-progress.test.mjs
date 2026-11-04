import test from 'node:test';
import assert from 'node:assert/strict';
import {generatePlan} from '../plan.mjs';
import {PlanningProgress} from '../planning-progress.mjs';
import {startMock} from './mock-server.mjs';

const file={path:'calculator.py',content:'def add(a,b):\n    return a - b\n'+'# source context\n'.repeat(60),before_sha256:'b'.repeat(64)};
const request={issue:'Repair addition',base_sha:'a'.repeat(40),files:[file],tests:[['python','test.py']],request_budget:25};
const read={name:'read_source',args:{path:file.path,reread:true}};
const proposal={name:'propose_repair',args:{diagnosis:'Fix addition',risk:'low',
  edits:[{path:file.path,replacements:[{oldText:'a - b',newText:'a + b'}]}]}};

test('real Pi keeps every prior wire message stable while tool results advance progress',async()=>{
  const mock=await startMock({script:(_body,turn)=>turn<4?{name:'read_source',
    args:{path:file.path,start_line:turn,end_line:turn}}:proposal});
  try{
    const result=await generatePlan(request,{mockUrl:mock.url});
    assert.equal(result.evidence.requests,4);
    const bodies=mock.requests;
    assert.ok(bodies.every(body=>body.messages.filter(m=>m.role==='system').length===1));
    assert.ok(bodies.every(body=>!body.messages[0].content.includes('Controller planning progress:')));
    for(let index=1;index<bodies.length;index++){
      const previous=bodies[index-1].messages,current=bodies[index].messages;
      assert.deepEqual(current.slice(0,previous.length),previous);
      const receipt=JSON.parse(current.findLast(message=>message.role==='tool').content);
      assert.equal(receipt.requests_remaining,25-index);
      assert.equal(receipt.planning_progress.requests_remaining,25-index);
    }
  }finally{await mock.close();}
});

test('real Pi checkpoints long diagnosis before the final call; SDK rejects string edits, allows targeted read and correction',async()=>{
  const mock=await startMock({script:(body,turn)=>{
    if(turn<23){return {name:'read_source',
      args:{path:file.path,start_line:2*turn-1,end_line:2*turn}};}
    if(turn===23){
      return {...proposal,args:{...proposal.args,edits:proposal.args.edits.map(JSON.stringify)}};
    }
    if(turn===24)return {name:'read_source',args:{path:file.path,query:'return'}};
    return proposal;
  }});
  try{
    const result=await generatePlan(request,{mockUrl:mock.url});
    assert.equal(result.evidence.requests,25);assert.equal(mock.requests.length,25);
    assert.equal(result.plan.edits[0].content,file.content.replace('a - b','a + b'));
    assert.equal(result.evidence.consumption,'reported');
    assert.deepEqual(result.evidence.active_tools,['propose_repair','read_source']);
    assert.ok(mock.requests.slice(0,22).every(body=>body.tool_choice===undefined));
    assert.deepEqual(mock.requests[22].tool_choice,{type:'function',function:{name:'propose_repair'}});
    const checkpoint=JSON.parse(mock.requests[22].messages.findLast(m=>m.role==='tool').content);
    assert.equal(checkpoint.planning_progress.phase,'proposal_checkpoint');
    assert.equal(mock.requests[23].tool_choice,undefined);
    assert.equal(mock.requests[24].tool_choice,undefined);
    assert.match(JSON.stringify(mock.requests[23].messages),/must be object/);
  }finally{await mock.close();}
});

test('real Pi detects three repeated ranges including explicit rereads and requests a proposal early',async()=>{
  const mock=await startMock({script:(body,turn)=>{
    if(turn<=4)return read;
    return proposal;
  }});
  try{
    const result=await generatePlan(request,{mockUrl:mock.url});
    assert.equal(result.evidence.requests,5);assert.equal(result.evidence.read_stats.read_repeats,3);
    assert.equal(result.plan.edits.length,1);
    assert.equal(mock.requests[4].tool_choice.function.name,'propose_repair');
    const checkpoint=JSON.parse(mock.requests[4].messages.findLast(m=>m.role==='tool').content);
    assert.equal(checkpoint.planning_progress.repeated_read_streak,3);
  }finally{await mock.close();}
});

test('a provider ignoring checkpoint still fails safely at the original cap without a fabricated proposal',async()=>{
  const mock=await startMock({script:()=>read});
  try{
    await assert.rejects(generatePlan({...request,request_budget:5},{mockUrl:mock.url}),error=>{
      assert.equal(error.evidence.failure_category,'request_budget');
      assert.equal(error.evidence.requests,5);assert.equal(error.evidence.status,'failed');return true;
    });
    assert.equal(mock.requests.length,5);
    assert.ok(mock.requests.some(body=>body.tool_choice?.function?.name==='propose_repair'));
  }finally{await mock.close();}
});

test('small allowances and absent source do not force ungrounded proposals; original context is immutable',()=>{
  for(const budget of [1,2,3,25,100]){
    const progress=new PlanningProgress(budget);
    assert.equal(progress.provider({systemPrompt:'original',messages:[]},budget-1).toolChoice,undefined);
    progress.read({content:'source',repeat_read:false});
    const original={systemPrompt:'original',messages:[]};
    const result=progress.provider(original,budget-1);
    assert.equal(Boolean(result.toolChoice),budget>2);
    assert.equal(original.systemPrompt,'original');assert.deepEqual(original.messages,[]);
    assert.equal(result.context,original);
    assert.equal(progress.state(budget-1).requests_remaining,1);
  }
});
