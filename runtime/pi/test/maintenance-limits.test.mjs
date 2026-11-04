import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {MAINTENANCE_LIMITS as limits,validateMaintenanceLimits as validate} from '../maintenance-limits.mjs';
import {generatePlan} from '../plan.mjs';
import {startMock} from './mock-server.mjs';

test('Pi uses the exact controller resource config and rejects malformed overrides',()=>{
  assert.deepEqual(limits,JSON.parse(fs.readFileSync(new URL('../../../src/repo_maintainer/maintenance_limits.json',import.meta.url),'utf8')));
  assert.ok(Object.isFrozen(limits));
  for(const invalid of [null,[],{...limits,unknown:1},{...limits,workspace_max_bytes:true},
    {...limits,source_context_max_files:129},{...limits,source_file_max_bytes:0},
    {...limits,planning_packet_max_bytes:1},{...limits,discovery_catalog_max_entries:1}])
    assert.throws(()=>validate(invalid),/Invalid maintenance/);
});

test('CLI packet preserves multibyte source across input chunks',async()=>{
  const content='# 😀 中文\n'.repeat(14000)+'def value(): return 1\n';
  const request={issue:'Return two',base_sha:'a'.repeat(40),request_budget:6,
    files:[{path:'code.py',content,before_sha256:'b'.repeat(64)}],tests:[['{python}','-m','unittest']]};
  const mock=await startMock({script:()=>({name:'propose_repair',args:{diagnosis:'wrong value',risk:'low',
    edits:[{path:'code.py',replacements:[{oldText:'return 1',newText:'return 2'}]}]}})});
  try{
    const result=await new Promise((resolve,reject)=>{
      const child=spawn(process.execPath,[fileURLToPath(new URL('../plan.mjs',import.meta.url)),'--mock-url',mock.url],{windowsHide:true});
      const output=[];let error='';
      child.stdout.on('data',part=>output.push(part));child.stderr.on('data',part=>error+=part);
      child.on('error',reject);
      child.on('close',code=>code===0?resolve(JSON.parse(Buffer.concat(output).toString('utf8'))):reject(new Error(error.slice(-1000))));
      const raw=Buffer.from(JSON.stringify(request));
      for(let offset=0;offset<raw.length;offset+=4093)child.stdin.write(raw.subarray(offset,offset+4093));
      child.stdin.end();
    });
    assert.equal(result.plan.edits[0].content,content.replace('return 1','return 2'));
    assert.equal(mock.requests.length,1);
  }finally{await mock.close();}
});

test('larger source and more than twenty paths pass the real Pi boundary; configured excess makes no request',async()=>{
  const content='# large source\n'.repeat(9000)+'def value(): return 1\n';
  const request={issue:'Return two',base_sha:'a'.repeat(40),request_budget:6,
    files:Array.from({length:21},(_,i)=>({path:`file${i}.py`,content:i===0?content:'x = 1\n',before_sha256:'b'.repeat(64)})),
    tests:[['{python}','-m','unittest']]};
  const mock=await startMock({script:()=>({name:'propose_repair',args:{diagnosis:'wrong value',risk:'low',
    edits:[{path:'file0.py',replacements:[{oldText:'return 1',newText:'return 2'}]}]}})});
  try{
    const result=await generatePlan(request,{mockUrl:mock.url});
    assert.ok(result.plan.edits[0].content.length>100000);
    assert.ok(result.plan.edits[0].content.endsWith('return 2\n'));
    const before=mock.requests.length;
    await assert.rejects(generatePlan({...request,files:Array.from({length:limits.source_context_max_files+1},(_,i)=>({...request.files[1],path:`f${i}.py`}))},{mockUrl:mock.url}));
    await assert.rejects(generatePlan({...request,files:[{...request.files[0],content:'x'.repeat(limits.source_file_max_bytes+1)}]},{mockUrl:mock.url}));
    assert.equal(mock.requests.length,before);
  }finally{await mock.close();}
});
