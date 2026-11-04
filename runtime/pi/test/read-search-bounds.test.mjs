import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readSnapshot, MAX_READ_BYTES} from '../snapshot-edits.mjs';
import {SnapshotReads} from '../revision-focus.mjs';
import {generatePlan} from '../plan.mjs';
import {startMock} from './mock-server.mjs';

const content = 'outside converter\n' + 'filler\n'.repeat(509) + 'converter = {}\n' +
  'def add(a, b):\n    return a - b\n' + 'filler\n'.repeat(15) + 'outside converter\n';
const file = {path:'code.py',content,before_sha256:createHash('sha256').update(content).digest('hex')};

test('observed query plus end_line works and respects the search interval',()=>{
  const result=readSnapshot(file,{query:'converter',start_line:500,end_line:520});
  assert.deepEqual(result.match_lines,[511]);
  assert.ok(result.start_line>=500 && result.end_line<=520);
  assert.match(result.content,/converter =/);
  assert.ok(!result.content.includes('outside converter'));
  assert.ok(Buffer.byteLength(result.content)<=MAX_READ_BYTES && result.end_line-result.start_line<200);
  const endOnly=readSnapshot(file,{query:'converter =',end_line:520});
  assert.deepEqual(endOnly.match_lines,[511]);
  assert.match(endOnly.content,/return a - b/);
});

test('query misses do not widen bounds and malformed bounds remain rejected',()=>{
  assert.deepEqual(readSnapshot(file,{query:'converter =',end_line:500}).match_lines,[]);
  for(const params of [{query:'',end_line:520},{query:'x'.repeat(201)},
    {query:'converter',start_line:0},{query:'converter',end_line:1.5},
    {query:'converter',start_line:520,end_line:500},{query:'converter',start_line:9999},
    {query:'converter',end_line:true},{start_line:1,end_line:201}])
    assert.throws(()=>readSnapshot(file,params));
  assert.deepEqual(readSnapshot({...file,content:''},{query:'converter'}).match_lines,[]);
});

test('combined queries preserve CRLF, Unicode and byte bounds without splitting lines',()=>{
  const text='头部\r\n'+('汉字 😀\r\n'.repeat(80))+'needle\r\n'+('后续\r\n'.repeat(80));
  const result=readSnapshot({...file,content:text},{query:'needle',start_line:20,end_line:140});
  assert.equal(result.content,text.match(/[^\n]*\n|[^\n]+$/g).slice(result.start_line-1,result.end_line).join(''));
  assert.ok(Buffer.byteLength(result.content)<=MAX_READ_BYTES);
  assert.throws(()=>readSnapshot({...file,content:'needle'+'x'.repeat(12001)}, {query:'needle'}),/bounded read size/);
});

test('different search intervals are not mistaken for the same cached miss',()=>{
  const reads=new SnapshotReads();
  assert.equal(reads.read(file,{query:'missing',start_line:1,end_line:10}).repeat_read,false);
  assert.equal(reads.read(file,{query:'missing',start_line:11,end_line:20}).repeat_read,false);
  assert.equal(reads.read(file,{query:'missing',start_line:1,end_line:10}).repeat_read,true);
});

test('real Pi accepts combined reads, corrects a read-only edit and submits on its last allowed request',async()=>{
  const readonly={path:'context.py',content:'reference = 1\n',editable:false,before_sha256:'b'.repeat(64)};
  const proposal={diagnosis:'Synthetic adapter check only',risk:'fixture only',edits:[{
    path:file.path,replacements:[{oldText:'return a - b',newText:'return a + b'}]}]};
  const mock=await startMock({script:(body,turn)=>{
    if(turn===1)return {name:'read_source',args:{path:file.path,query:'converter =',end_line:520}};
    if(turn===2){
      assert.match(body.messages.filter(m=>m.role==='tool').at(-1).content,/converter =/);
      return {name:'propose_repair',args:{...proposal,edits:[{path:readonly.path,replacements:[{oldText:'reference = 1',newText:'reference = 2'}]}]}};
    }
    const feedback=body.messages.filter(m=>m.role==='tool').at(-1).content;
    assert.match(feedback,/"editable_paths":\["code.py"\]/);
    assert.match(feedback,/Read-only context is not editable/);
    return {name:'propose_repair',args:proposal};
  }});
  try{
    const result=await generatePlan({issue:'Fix fixture addition',base_sha:'a'.repeat(40),
      files:[file,readonly],tests:[['python','test.py']],request_budget:3},{mockUrl:mock.url});
    assert.equal(result.evidence.requests,3);
    assert.equal(mock.requests.length,3);
    assert.equal(result.plan.edits[0].path,file.path);
    assert.equal(result.plan.edits[0].content,content.replace('a - b','a + b'));
    assert.equal(readonly.content,'reference = 1\n');
  }finally{await mock.close();}
});

test('real Pi coalesces disjoint duplicate-path replacements on the final allowed request',async()=>{
  const readonly={path:'context.py',content:'reference = 1\n',editable:false,before_sha256:'b'.repeat(64)};
  const replacements=[{oldText:'return a - b',newText:'return a + b'},
    {oldText:'converter = {}',newText:'converter = {"checked": True}'}];
  const base={diagnosis:'Synthetic duplicate-path recovery',risk:'fixture only'};
  const mock=await startMock({script:(body,turn)=>{
    if(turn===1)return {name:'read_source',args:{path:file.path,query:'converter =',end_line:520}};
    if(turn===2)return {name:'propose_repair',args:{...base,
      edits:replacements.map(r=>({path:file.path,replacements:[r]}))}};
    throw new Error('Coalescing must not require another model request');
  }});
  try{
    const result=await generatePlan({issue:'Repair two independent fixture lines',base_sha:'a'.repeat(40),
      files:[file,readonly],tests:[['python','test.py']],request_budget:2},{mockUrl:mock.url});
    assert.equal(result.evidence.requests,2);
    assert.equal(mock.requests.length,2);
    assert.equal(result.plan.edits.length,1);
    assert.equal(result.patches.length,1);
    assert.equal(result.patches[0].replacements.length,2);
    assert.equal(result.plan.edits[0].content,content.replace('a - b','a + b').replace('converter = {}','converter = {"checked": True}'));
    assert.equal(readonly.content,'reference = 1\n');
  }finally{await mock.close();}
});
