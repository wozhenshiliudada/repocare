import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {verifiedBehavior} from '../behavior-context.mjs';
const sha=s=>createHash('sha256').update(s).digest('hex');
const body={schema_version:1,kind:'python-behavior-v1',repository_id:'c'.repeat(64),
 base_sha:'a'.repeat(40),request_sha256:sha('加法需求'),requirement:'按明确输入返回总和',runtime_authorized:false,
 cases:[{id:'sum',module:'calc.py',callable:'add',args:[1,2],kwargs:{},
  expect:{kind:'json',value:3},missing_behavior:false}]};
const seal=(b=body)=>{const raw=JSON.stringify(b);return {body_json:raw,contract_sha256:sha(raw)};};
const request=v=>({issue:'加法需求',base_sha:'a'.repeat(40),files:[{path:'calc.py'}],
 tests:[['repocare:behavior-v1',v.contract_sha256]]});

test('raw Python float and Unicode contract bytes survive without JS reserialization',()=>{
 const v=seal();v.body_json=v.body_json.replace('"args":[1,2]','"args":[1.0,2]');v.contract_sha256=sha(v.body_json);
 const accepted=verifiedBehavior(v,request(v));
 assert.equal(accepted.contract_sha256,v.contract_sha256);
 assert.deepEqual(JSON.parse(accepted.body_json).cases[0].args,[1,2]);
 assert.ok(accepted.body_json.includes('"args":[1.0,2]'));
 assert.equal(accepted.runtime_authorized,false);
 const negative=seal();negative.body_json=negative.body_json.replace('"args":[1,2]','"args":[-0.0,2]');negative.contract_sha256=sha(negative.body_json);
 assert.ok(verifiedBehavior(negative,request(negative)).body_json.includes('-0.0'));
 const exponent=seal();exponent.body_json=exponent.body_json.replace('"args":[1,2]',
   '"args":['+Array(1500).fill('1e+20').join(',')+']');exponent.contract_sha256=sha(exponent.body_json);
 assert.equal(verifiedBehavior(exponent,request(exponent)).body_json,exponent.body_json);
});

test('request and exact controller marker must match; plain old requests need no behavior',()=>{
 const v=seal();assert.equal(verifiedBehavior(undefined,{tests:[['python','-m','unittest']]}),null);
 for(const patch of [{issue:'changed'},{base_sha:'b'.repeat(40)},{files:[]},{tests:[]},
  {tests:[...request(v).tests,...request(v).tests]},{tests:[['repocare:behavior-v1','d'.repeat(64)]]}])
  assert.throws(()=>verifiedBehavior(v,{...request(v),...patch}));
 assert.throws(()=>verifiedBehavior(undefined,request(v)));
 assert.throws(()=>verifiedBehavior({...v,body_json:v.body_json+' '},request(v)));
});

test('cases remain bounded data; commands, zero cases, source drift and invalid values reject',()=>{
 for(const patch of [{runtime_authorized:true},{schema_version:true},{cases:[]},
  {commands:['git push']},{cases:[{...body.cases[0],module:'secrets.py'}]},
  {cases:[{...body.cases[0],callable:'_internal'}]},
  {cases:[{...body.cases[0],expect:{kind:'code',value:'1+2'}}]},
  {cases:[body.cases[0],body.cases[0]]}]){
  const v=seal({...body,...patch});assert.throws(()=>verifiedBehavior(v,request(v)));
 }
 const v=seal();v.body_json=v.body_json.replace('"args":[1,2]','"args":[1e400,2]');v.contract_sha256=sha(v.body_json);
 assert.throws(()=>verifiedBehavior(v,request(v)));
});

test('node contract keeps exact Python-issued bytes with one runtime and positional arguments',()=>{
 const node={...body,kind:'node-behavior-v1',cases:[{...body.cases[0],module:'calc.mjs'}]};
 const value=seal(node),req={...request(value),files:[{path:'calc.mjs'}]};
 assert.equal(verifiedBehavior(value,req).body_json,value.body_json);
 for(const change of [c=>c.module='calc.py',c=>c.module='calc.tsx',c=>c.module='calc.d.ts',
   c=>c.kwargs={a:1},c=>c.args=[Number.MAX_SAFE_INTEGER+1]]){
  const changed=structuredClone(node);change(changed.cases[0]);const v=seal(changed);
  assert.throws(()=>verifiedBehavior(v,{...req,tests:[['repocare:behavior-v1',v.contract_sha256]],
    files:[{path:changed.cases[0].module}]}));
 }
});
