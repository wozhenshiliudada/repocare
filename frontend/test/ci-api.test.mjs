import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createCiReadEvidence} from '../src/ci-api.mjs';
import {apiRequest} from '../src/api.mjs';

const selection={url:'https://github.com/example/repo',run_id:101,attempt:2,job_id:201,expected_repository_id:901,expected_head_sha:'a'.repeat(40)};
test('CI read uses authenticated request and only fixed selection parameters',async()=>{
 let seen;
 const read=createCiReadEvidence(path=>apiRequest(path,'local-capability',undefined,{fetcher:async(url,options)=>{
  seen={url,options};return {ok:true,status:200,json:async()=>({read_only:true})};
 }}));
 await read({...selection,download_url:'https://evil.invalid'});
 const url=new URL(seen.url,'http://localhost');
 assert.equal(url.pathname,'/api/ci/evidence');
 assert.deepEqual(Object.fromEntries(url.searchParams),Object.fromEntries(Object.entries(selection).map(([k,v])=>[k,String(v)])));
 assert.equal(seen.options.method,'GET');assert.equal(seen.options.headers.Authorization,'Bearer local-capability');
});
test('GitHub errors in evidence never expire local login; local middleware 401 still does',async()=>{
 let expired=0;
 for(const status of [401,403,429]){
  const packet={evidence_status:'blocked',errors:[{code:'upstream',http_status:status}]};
  const read=createCiReadEvidence(path=>apiRequest(path,'local',undefined,{expire:()=>expired++,fetcher:async()=>({ok:true,status:200,json:async()=>packet})}));
  assert.deepEqual(await read(selection),packet);
 }
 assert.equal(expired,0);
 const read=createCiReadEvidence(path=>apiRequest(path,'local',undefined,{expire:()=>expired++,fetcher:async()=>({ok:false,status:401,json:async()=>({error:'Local login required'})})}));
 await assert.rejects(read(selection));assert.equal(expired,1);
});
test('invalid CI selection fails before any request',()=>{
 let called=false;const read=createCiReadEvidence(()=>{called=true;});
 assert.throws(()=>read({...selection,attempt:0}));assert.equal(called,false);
});
