import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {applyReplacements, materializeEdits, normalizeEdits, readSnapshot, MAX_READ_BYTES, MAX_SOURCE_BYTES} from '../snapshot-edits.mjs';
import {generatePlan} from '../plan.mjs';
import {startMock} from './mock-server.mjs';
import {runCase} from './replay-case.mjs';

const original = '# preserve — ‘text’ 😀   \r\n' + '# filler\r\n'.repeat(4000) +
  'def add(a, b):\n    return a - b\n';
const source = {path:'calculator.py', content:original, before_sha256:'b'.repeat(64)};
const request = {issue:'Repair addition; preserve unrelated bytes', base_sha:'a'.repeat(40),
  files:[source, {path:'tests/test.py', content:'assert add(2,3) == 5\n', editable:false, before_sha256:'c'.repeat(64)}],
  tests:[['{python}','-m','unittest']], request_budget:6};
const proposal = (oldText='return a - b') => ({diagnosis:'addition uses subtraction',risk:'low',
  edits:[{path:'calculator.py',replacements:[{oldText,newText:'return a + b'}]}]});

test('exact replacements preserve all untouched CRLF, Unicode and whitespace bytes', () => {
  assert.equal(applyReplacements(original, proposal().edits[0].replacements), original.replace('a - b','a + b'));
  assert.equal(applyReplacements('first second', [{oldText:'second',newText:'last'},
    {oldText:'first',newText:'start'}]), 'start last');
});

test('empty, ambiguous, overlapping, missing, no-op and oversized replacements fail atomically', () => {
  for (const [content, replacements] of [
    ['abc',[]],['abc',[{oldText:'',newText:'x'}]],['aaaa',[{oldText:'aaa',newText:'x'}]],
    ['abc',[{oldText:'ab',newText:'x'},{oldText:'bc',newText:'y'}]],
    ['abc',[{oldText:'z',newText:'x'}]],['abc',[{oldText:'abc',newText:'abc'}]],
    ['a',[{oldText:'a',newText:'x'.repeat(MAX_SOURCE_BYTES+1)}]],
  ]) assert.throws(() => applyReplacements(content,replacements));
  const files = new Map(request.files.map(f => [f.path, f]));
  assert.throws(() => materializeEdits(files,[{path:source.path,content:'    return a + b\n'}]));
  assert.throws(() => materializeEdits(files,[{...proposal().edits[0],path:'tests/test.py'}]));
  assert.throws(() => materializeEdits(files,[{...proposal().edits[0],path:'../escape.py'}]));
  assert.throws(() => materializeEdits(files,[proposal().edits[0],proposal().edits[0]]));
});

test('duplicate entries only group original-snapshot edits; conflicting or unauthorized proposals remain atomic', () => {
  const file = {path:'a.py',content:'first\r\nsecond 😀\r\n',before_sha256:'b'.repeat(64)};
  const files = new Map([[file.path,file],['ro.py',{...file,path:'ro.py',editable:false}]]);
  const first = {path:file.path,replacements:[{oldText:'first',newText:'start'}]};
  const second = {path:file.path,replacements:[{oldText:'second',newText:'last'}]};
  assert.deepEqual(normalizeEdits(files,[first,second]),[{path:file.path,replacements:[...first.replacements,...second.replacements]}]);
  assert.equal(materializeEdits(files,[first,second])[0].content,'start\r\nlast 😀\r\n');
  for(const tail of [first, {path:'ro.py',replacements:second.replacements},
    {path:'outside.py',replacements:second.replacements},
    {path:file.path,replacements:[{oldText:'start',newText:'chained'}]},
    {path:file.path,replacements:[{oldText:'first\r\nsecond',newText:'overlap'}]},
    {path:file.path,replacements:[]},
    {path:file.path,replacements:Array(32).fill(second.replacements[0])}]) {
    assert.throws(()=>materializeEdits(files,[first,tail]));
    assert.equal(file.content,'first\r\nsecond 😀\r\n');
  }
});

test('bounded reads expose exact line ranges and literal search without truncating a line', () => {
  const first = readSnapshot(source,{});
  assert.equal(first.end_line,200); assert.equal(first.next_line,201); assert.equal(first.more,true);
  assert.ok(Buffer.byteLength(first.content) <= MAX_READ_BYTES);
  const match = readSnapshot(source,{query:'def add'});
  assert.ok(match.content.includes('return a - b')); assert.deepEqual(match.match_lines,[4002]);
  assert.equal(readSnapshot(source,{start_line:4003,end_line:4003}).content,'    return a - b\n');
  assert.deepEqual(readSnapshot(source,{query:'not present'}).match_lines,[]);
  assert.throws(() => readSnapshot(source,{start_line:1,end_line:201}));
  assert.deepEqual(readSnapshot(source,{query:'add',start_line:1,end_line:200}).match_lines,[]);
  assert.throws(() => readSnapshot({...source,content:'x'.repeat(12001)},{}));
});

test('real Pi runs five model rounds including read and edit error recovery under one total budget', async () => {
  const mock = await startMock({script:(body, turn) => {
    if (turn === 1) return {name:'read_source',args:{path:'unauthorized.py'}};
    if (turn === 2) {
      return {name:'read_source',args:{path:'tests/test.py'}};
    }
    if (turn === 3) return {name:'read_source',args:{path:'calculator.py',query:'def add'}};
    if (turn === 4) return {name:'propose_repair',args:proposal('not in snapshot')};
    return {name:'propose_repair',args:proposal()};
  }});
  try {
    const result = await generatePlan(request,{mockUrl:mock.url});
    assert.equal(result.evidence.requests,5); assert.equal(mock.requests.length,5);
    assert.equal(result.plan.edits[0].content,original.replace('a - b','a + b'));
    assert.deepEqual(result.patches,proposal().edits);
    for (const [index, body] of mock.requests.entries()) {
      const user = body.messages.find(m => m.role === 'user');
      const context = JSON.parse(typeof user.content === 'string' ? user.content : user.content[0].text);
      assert.equal(context.request_budget,6);
      if (index) {
        const message = body.messages.filter(m => m.role === 'tool').at(-1);
        // Pi marks errored tool content for the provider; its text still contains the safe JSON payload.
        assert.match(message.content,new RegExp('"requests_remaining":'+(6-index)));
      }
    }
    assert.equal(result.evidence.events.filter(e=>e.type==='tool_execution_end'&&e.is_error).length,2);
  } finally {await mock.close();}
});

for (const malformed of [false,true]) test('length-truncated repair stops with no accepted plan or extra request: '+malformed, async () => {
  const mock = await startMock({script:(_body,turn)=>turn===1 ?
    {name:'read_source',args:{path:'calculator.py',query:'def add'}} :
    {name:'propose_repair',args:proposal(),finish:'length',
      ...(malformed?{rawArguments:JSON.stringify(proposal()).slice(0,-10)}:{})}});
  try {
    await assert.rejects(generatePlan(request,{mockUrl:mock.url}),error=>{
      assert.equal(error.evidence.failure_category,'output_truncated');
      assert.equal(error.evidence.requests_started,2); assert.equal(error.evidence.requests,null);
      assert.equal(error.evidence.tool_calls,1); return true;
    });
    assert.equal(mock.requests.length,2);
  } finally {await mock.close();}
});

test('repeated reading can use more than eight tools while staying inside the declared total', async () => {
  const mock = await startMock({script:(_body,turn)=>turn<10 ?
    {name:'read_source',args:{path:'calculator.py',query:'def add'}} : {name:'propose_repair',args:proposal()}});
  try {
    const result=await generatePlan({...request,request_budget:12},{mockUrl:mock.url});
    assert.equal(result.evidence.requests,10); assert.equal(result.evidence.tool_calls,10);
  } finally {await mock.close();}
});

for (const [scenario, expected, largeSource] of [['local_fix','candidate_ready',true],['fragment','failed',false]])
  test('real Git workflow: '+scenario,{timeout:45000},async()=>{
    const temp=await fs.mkdtemp(path.join(os.tmpdir(),'pi-local-edit-'));
    try {
      const result=await runCase(temp,scenario,'approve',expected,{largeSource});
      assert.equal(result.status,expected); assert.equal(result.original_bytes_preserved,true);
      assert.equal(result.source_unchanged,true);
      if (largeSource) {
        assert.ok(result.original_bytes>40000); assert.equal(result.candidate_unchanged_bytes_preserved,true);
        assert.equal(result.candidate_exit[0],0); assert.notEqual(result.baseline_exit[0],0);
        assert.equal(result.cleanup_status,'discarded');
      } else assert.deepEqual(result.baseline_exit,[]);
    } finally {await fs.rm(temp,{recursive:true,force:true});}
  });
