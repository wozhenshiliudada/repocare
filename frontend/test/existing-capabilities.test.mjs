import test from 'node:test';
import assert from 'node:assert/strict';
import {downloadArtifact,connectionLabel,isExistingWorkflow,reportInputs} from '../src/existing-capability-model.mjs';
import {workflows} from '../src/preview-workflows.mjs';

const artifact={name:'dashboard.html',url:'/api/exports/'+'a'.repeat(32)+'/dashboard.html',bytes:2};
test('existing workflows distinguish real adapters, partial connection and missing aggregation',()=>{
 for(const id of ['recent-projects','large-repository','dashboard','comparison']){
  assert.ok(isExistingWorkflow(id));assert.doesNotMatch(connectionLabel(workflows.find(w=>w.id===id)),/后端未实现|仅CLI/);
 }
 assert.match(connectionLabel(workflows.find(w=>w.id==='large-repository')),/已接分页/);
 assert.equal(isExistingWorkflow('global-metrics'),false);
 assert.equal(connectionLabel(workflows.find(w=>w.id==='global-metrics')),'尚无专用执行接口');
 assert.deepEqual(reportInputs(' C:/one\r\n\n C:/two '),['C:/one','C:/two']);
});
test('download uses the current bearer and a fixed artifact URL, preserving name and bytes',async()=>{
 const calls=[];
 await downloadArtifact(artifact,'memory-only',{fetcher:async(url,options)=>{
  calls.push([url,options]);return {ok:true,blob:async()=>new Blob(['ok'])};
 },save:async(blob,name)=>{assert.equal(await blob.text(),'ok');assert.equal(name,'dashboard.html');}});
 assert.deepEqual(calls,[[artifact.url,{headers:{Authorization:'Bearer memory-only'}}]]);
});
test('foreign URLs, wrong filenames, unauthorized, changed and failed downloads never save',async()=>{
 let saved=0,fetched=0;
 const options={fetcher:async()=>{fetched++;return {ok:true,blob:async()=>new Blob(['ok'])};},save:()=>{saved++;}};
 for(const row of [{...artifact,url:'https://evil.invalid/file'},{...artifact,name:'secret.txt'},
                    {...artifact,url:artifact.url+'?token=secret'}])await assert.rejects(downloadArtifact(row,'token',options));
 assert.equal(fetched,0);
 await assert.rejects(downloadArtifact({...artifact,bytes:3},'token',options),/不一致/);
 await assert.rejects(downloadArtifact(artifact,'token',{...options,fetcher:async()=>({ok:false,status:401})}),/失效/);
 await assert.rejects(downloadArtifact(artifact,'token',{...options,fetcher:async()=>({ok:false,status:409})}),/不可用/);
 assert.equal(saved,0);
});
