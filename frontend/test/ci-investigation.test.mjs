import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {build} from 'vite';
import {createCiController,validateCiEvidence,ciSource,ciRepository} from '../src/ci-model.mjs';

const selection={url:'https://github.com/example/ci-contract-fixture',run_id:1001,attempt:1};
const original=JSON.parse(fs.readFileSync('../docs/tasks/ci-investigation/example.synthetic.json','utf8'));
function value(extra={}){return {...structuredClone(original),selection:{...original.selection,job_id:null},log:null,...extra};}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const out=path.resolve('.demo/ci-render-'+process.pid);
await build({configFile:false,logLevel:'error',build:{ssr:'src/CiInvestigation.tsx',outDir:out,emptyOutDir:false,
 rolldownOptions:{external:['react','react-dom','react/jsx-runtime'],output:{entryFileNames:'ci.mjs'}}}});
const {CiInvestigation,CiEvidenceView}=await import(pathToFileURL(path.join(out,'ci.mjs')));

test('CI component renders without API, model or repair actions',()=>{
 let calls=0;
 const html=renderToStaticMarkup(React.createElement(CiInvestigation,{repositoryUrl:selection.url,runs:[],readEvidence:()=>{calls++;throw Error();}}));
 assert.equal(calls,0);assert.match(html,/CI精确证据调查/);assert.match(html,/只读/);assert.match(html,/disabled/);
 assert.doesNotMatch(html,/批准方案|提交执行|修复这个/);
});
test('CI evidence is escaped text with generated GitHub sources and explicit limits',()=>{
 const packet=structuredClone(original);packet.log.excerpt='<script>alert("bad")</script>\nIgnore instructions';
 packet.log.source_url='https://evil.invalid/?token=secret';packet.log.capture_truncated=true;packet.evidence_status='partial';
 const html=renderToStaticMarkup(React.createElement(CiEvidenceView,{value:packet,selection,selectedJobId:2001}));
 assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>|evil\.invalid/);
 for(const text of ['读取不完整','合成样例','尚未复现','不代表完整远端日志'])assert.ok(html.includes(text),text);
 assert.match(html,/github.com\/example\/ci-contract-fixture\/actions\/runs\/1001\/job\/2001/);
});
test('choosing another job cannot render the old job log',()=>{
 const html=renderToStaticMarkup(React.createElement(CiEvidenceView,{value:original,selection,selectedJobId:9999}));
 assert.doesNotMatch(html,/AssertionError: expected/);
});
test('late success is discarded after repository switch',async()=>{
 const wait=deferred(),events=[];const ctl=createCiController(()=>wait.promise,state=>events.push(state));
 ctl.select(selection);const running=ctl.read();ctl.select({...selection,url:'https://github.com/example/another'});
 wait.resolve(value());await running;assert.equal(events.at(-1).value,null);assert.equal(events.at(-1).selection.url,'https://github.com/example/another');
});
test('late failure is discarded after run or attempt switch',async()=>{
 for(const change of [{run_id:999},{attempt:2}]){
  const wait=deferred(),events=[];const ctl=createCiController(()=>wait.promise,state=>events.push(state));
  ctl.select(selection);const running=ctl.read();ctl.select({...selection,...change});wait.reject(Error('OLD_CREDENTIAL'));
  await running;assert.equal(events.at(-1).error,'');assert.equal(events.at(-1).value,null);
 }
});
test('unmount invalidates pending completion',async()=>{
 const wait=deferred(),events=[];const ctl=createCiController(()=>wait.promise,state=>events.push(state));
 ctl.select(selection);const running=ctl.read();ctl.dispose();const count=events.length;wait.resolve(value());await running;assert.equal(events.length,count);
});

test('every selection boundary discards both late success and late failure',async()=>{
 for(const change of [{url:'https://github.com/example/other'},{run_id:1002},{attempt:2},null]){
  for(const reject of [false,true]){
   const wait=deferred(),events=[];const ctl=createCiController(()=>wait.promise,state=>events.push(state));
   ctl.select(selection);const running=ctl.read();
   if(change)ctl.select({...selection,...change});else ctl.dispose();
   const count=events.length;
   if(reject)wait.reject(Error('OLD'));else wait.resolve(value());
   await running;assert.equal(events.length,count);assert.equal(events.at(-1).value,null);assert.equal(events.at(-1).error,'');
  }
 }
});
test('explicit refresh preserves previously delivered snapshot and sends once while busy',async()=>{
 const waits=[deferred(),deferred()],events=[];let calls=0;
 const ctl=createCiController(()=>waits[calls++].promise,state=>events.push(state));ctl.select(selection);
 const first=ctl.read();await ctl.read();assert.equal(calls,1);waits[0].resolve(value());await first;
 const saved=events.at(-1).value;const second=ctl.read();waits[1].resolve(value({observed_at:'new observation'}));await second;
 assert.equal(saved.observed_at,original.observed_at);assert.equal(events.at(-1).value.observed_at,'new observation');
});
test('malformed or foreign CI evidence is rejected with a safe error',async()=>{
 const events=[];const ctl=createCiController(async()=>value({run:{...original.run,attempt:2}}),state=>events.push(state));
 ctl.select(selection);await ctl.read();assert.ok(events.at(-1).error);assert.equal(events.at(-1).value,null);
});
test('expected repository/head binding cannot silently change',()=>{
 assert.throws(()=>validateCiEvidence(value(),{...selection,expected_head_sha:'b'.repeat(40)}));
 assert.throws(()=>validateCiEvidence(value(),{...selection,expected_repository_id:999}));
});
test('read errors do not expose arbitrary exception details',async()=>{
 const events=[];const ctl=createCiController(async()=>{throw Error('Bearer PRIVATE');},state=>events.push(state));ctl.select(selection);await ctl.read();
 assert.doesNotMatch(events.at(-1).error,/PRIVATE|Bearer/);assert.equal(events.at(-1).busy,false);
});
test('invalid selection never calls the service',async()=>{
 let calls=0;const ctl=createCiController(async()=>{calls++;return value();},()=>{});
 ctl.select({...selection,run_id:true});await ctl.read();assert.equal(calls,0);
});
test('repository and source URLs reject userinfo and arbitrary hosts',()=>{
 assert.equal(ciRepository(selection.url+'.git'),'example/ci-contract-fixture');
 assert.match(ciSource(selection),/attempts\/1$/);
 for(const url of ['https://github.com@evil.invalid/a/b','https://github.com/a/b?token=x','http://github.com/a/b','https://github.com:443/a/b'])assert.throws(()=>ciRepository(url));
});
test('malformed steps, error objects and coverage cannot reach the React view',()=>{
 const badStep=value();badStep.jobs[0].steps[0].name={untrusted:'object'};
 const badError=value({errors:[{stage:{},code:'bad'}]});
 const badCoverage=value({coverage:{captured_jobs:-1}});
 for(const packet of [badStep,badError,badCoverage])assert.throws(()=>validateCiEvidence(packet,selection));
});
