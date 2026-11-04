import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {SnapshotReads} from '../revision-focus.mjs';
import {generatePlan} from '../plan.mjs';
import {startMock} from './mock-server.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const sha=text=>createHash('sha256').update(text).digest('hex');
const file={path:'calculator.py',before_sha256:'b'.repeat(64),content:'def add(a,b):\n    return a - b\n'};
const request={issue:'Repair addition',base_sha:'a'.repeat(40),files:[file],tests:[['python','test.py']],request_budget:8};
const read={name:'read_source',args:{path:file.path}};
const proposal={name:'propose_repair',args:{diagnosis:'Fix addition',risk:'low',edits:[{path:file.path,
  replacements:[{oldText:'a - b',newText:'a + b'}]}]}};

test('identical read returns a reference, explicit reread restores exact text, new snapshot never uses old text',()=>{
  const reads=new SnapshotReads();
  const first=reads.read(file,{}),second=reads.read(file,{start_line:1,end_line:2});
  assert.equal(second.read_id,first.read_id);assert.equal(second.content_omitted,true);
  assert.equal('content' in second,false);assert.match(second.guidance,/reread=true/);
  const third=reads.read(file,{reread:true});assert.equal(third.content,file.content);
  assert.equal(third.repeat_read,true);
  const next={...file,before_sha256:'c'.repeat(64),content:file.content.replace('a - b','a * b')};
  assert.equal(reads.read(next,{}).content,next.content);
  assert.deepEqual(reads.stats(),{read_requests:4,read_repeats:2,explicit_rereads:1});
  assert.throws(()=>reads.read(file,{start_line:300}),/beyond/);
  assert.throws(()=>reads.read(file,{reread:'yes'}),/boolean/);
  assert.equal(reads.stats().read_requests,4);
  assert.equal(new SnapshotReads().read(file,{}).repeat_read,false);
});

test('different literal queries remain separate even when both have no matches',()=>{
  const reads=new SnapshotReads();
  assert.equal(reads.read(file,{query:'missing-one'}).repeat_read,false);
  assert.equal(reads.read(file,{query:'missing-two'}).repeat_read,false);
  assert.equal(reads.read(file,{query:'missing-one'}).repeat_read,true);
});

test('real Pi receives duplicate guidance, can explicitly reread and then propose a valid local edit',async()=>{
  const observed=[];
  const mock=await startMock({onRequest:body=>observed.push(body),script:(_body,turn)=>
    turn<=2?read:turn===3?{...read,args:{...read.args,reread:true}}:proposal});
  try {
    const result=await generatePlan(request,{mockUrl:mock.url});
    assert.equal(result.evidence.requests,4);
    assert.deepEqual(result.evidence.read_stats,{read_requests:3,read_repeats:2,explicit_rereads:1});
    const repeated=observed[2].messages.filter(message=>message.role==='tool').at(-1).content;
    assert.match(repeated,/"content_omitted":true/);assert.ok(!repeated.includes('return a - b'));
    const reread=observed[3].messages.filter(message=>message.role==='tool').at(-1).content;
    assert.match(reread,/return a - b/);
    assert.equal(result.plan.edits[0].content,file.content.replace('a - b','a + b'));
    assert.equal(result.planning_summary.reads.length,1); // Repeats cannot crowd out useful read positions.
  }finally{await mock.close();}
});

function context() {
  return {schema_version:1,issue_sha256:sha(request.issue),base_sha:request.base_sha,
    snapshot_sha256:sha(JSON.stringify([[file.path,file.before_sha256,true]])),round:1,
    history:[{round:1,plan_sha256:'c'.repeat(64),diagnosis:'先前方案',risk:'low',edited_paths:[file.path],reads:[]}],
    test_focus:{tests:[{failure_ids:['test_add'],assertion_excerpt:'AssertionError: 6 != 5'}]}};
}

test('real Pi sees task-bound Unicode history and failed assertion, returns an exact context receipt',async()=>{
  const revision=context();const body={...request,feedback:{round:1,previous_plan_sha256:'c'.repeat(64)},revision_context:revision};
  const mock=await startMock({script:()=>proposal});
  try {
    const result=await generatePlan(body,{mockUrl:mock.url});
    const raw=mock.requests[0].messages.find(message=>message.role==='user').content;
    const user=JSON.parse(typeof raw==='string'?raw:raw[0].text);
    assert.deepEqual(user.revision_context,revision);
    assert.equal(result.evidence.revision_context_sha256,sha(JSON.stringify(revision)));
    for (const change of [value=>value.issue_sha256='d'.repeat(64),value=>value.snapshot_sha256='d'.repeat(64),
      value=>value.base_sha='d'.repeat(64),value=>value.history.at(-1).plan_sha256='d'.repeat(64)]) {
      const invalid=structuredClone(revision);change(invalid);
      await assert.rejects(generatePlan({...body,revision_context:invalid},{mockUrl:mock.url}),/revision context/);
    }
    assert.equal(mock.requests.length,1);
  }finally{await mock.close();}
});

test('old Python CLI requests still receive their original evidence whitelist',async()=>{
  const mock=await startMock({script:()=>proposal});
  try {
    const output=await new Promise((resolve,reject)=>{
      const child=spawn(process.execPath,[fileURLToPath(new URL('../plan.mjs',import.meta.url)),
        '--mock-url',mock.url],{windowsHide:true,stdio:['pipe','pipe','pipe']});
      let stdout='',stderr='';child.stdout.on('data',data=>stdout+=data);child.stderr.on('data',data=>stderr+=data);
      child.on('error',reject);child.on('close',code=>code===0?resolve(JSON.parse(stdout)):reject(new Error(stderr)));
      child.stdin.end(JSON.stringify(request));
    });
    assert.equal(output.status,'completed');assert.equal(output.plan.edits[0].content,file.content.replace('a - b','a + b'));
    assert.equal('read_stats' in output.evidence,false);assert.equal('planning_summary' in output,false);
    assert.equal('revision_context_sha256' in output.evidence,false);
  }finally{await mock.close();}
});
