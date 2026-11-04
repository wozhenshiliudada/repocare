import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {verifiedStructure as parseStructure,structureHash} from '../structure-context.mjs';
import {generatePlan} from '../plan.mjs';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {startMock} from './mock-server.mjs';
import {python,project,removeOwnFixture} from './ui-case.mjs';
import {verifiedMemory} from '../source-memory.mjs';
const hash=s=>createHash('sha256').update(s).digest('hex');
const seal=value=>{const {context_sha256,...data}=value;return {...data,context_sha256:structureHash(data)};};
const issue='addition 结构',base='b'.repeat(40);
const files=[{path:'calculator.py',content:'def add(a,b): return a-b\n'}];
const verifiedStructure=(value,query=issue,planningBase=base,inputFiles=files)=>parseStructure(value,query,planningBase,inputFiles);
function sample(){
 const node=(id,kind='function')=>({id,kind,name:id,qualified_name:id,path:'calculator.py',language:'python',
  start_line:1,end_line:2,source_sha256:'a'.repeat(64)});
 return seal({schema_version:1,engine:{name:'@colbymchenry/codegraph',version:'1.6.2'},
  source:{root_sha256:'c'.repeat(64),common_dir_sha256:'d'.repeat(64),head:'e'.repeat(40),branch:'main',
   files:[{path:'calculator.py',source_sha256:'a'.repeat(64),lines:3}]},
  manifest_sha256:'f'.repeat(64),manifest_file_sha256:'1'.repeat(64),graph_sha256:'2'.repeat(64),
  planning_base_sha:base,planning_files_sha256:structureHash(files.map(f=>({path:f.path,source_sha256:hash(f.content)}))),
  issue_sha256:hash(issue),query:'add',query_sha256:hash('add'),
  hits:[node('add')],edges:[{source:'file',target:'add',kind:'contains'},{source:'add',target:'add',kind:'calls'}],
  related_nodes:[node('file','file')],structural_claims_only:true,runtime_authorized:false});
}
const reject=mutator=>{const data=sample();mutator(data);assert.throws(()=>verifiedStructure(seal(data),issue,base),/Invalid structure context/);};
test('explicit context permits recursive code and a separate original indexed baseline',()=>{
 const data=sample();assert.equal(verifiedStructure(data,issue,base),data);
 assert.notEqual(data.source.head,data.planning_base_sha);assert.equal(verifiedStructure(undefined,issue,base),null);
 const empty=sample();empty.hits=[];empty.edges=[];empty.related_nodes=[];
 assert.equal(verifiedStructure(seal(empty),issue,base).hits.length,0);
});
test('strict envelope rejects authority upgrades, unknown fields, versions and engine changes',()=>{
 for(const patch of [{schema_version:true},{schema_version:2},{runtime_authorized:true},{structural_claims_only:false},
                     {approved:true},{policy:'all'},{structure_index:'C:/private'}])reject(d=>Object.assign(d,patch));
 reject(d=>d.engine.version='latest');reject(d=>d.engine.extra='private');
 assert.throws(()=>verifiedStructure(null,issue,base));
});
test('receipts bind Unicode issue query current candidate and entire context',()=>{
 const data=sample();assert.throws(()=>verifiedStructure(data,'other',base));
 assert.throws(()=>verifiedStructure(data,issue,'3'.repeat(40)));
 assert.throws(()=>verifiedStructure(data,issue,base,[{...files[0],content:'changed'}]));
 assert.throws(()=>verifiedStructure({...data,context_sha256:'0'.repeat(64)},issue,base));
 for(const key of ['manifest_sha256','manifest_file_sha256','graph_sha256','issue_sha256','query_sha256'])reject(d=>d[key]='bad');
 reject(d=>d.query='different');reject(d=>d.query='a\n');reject(d=>d.query='a'.repeat(129));
 reject(d=>d.planning_base_sha='invalid');reject(d=>d.source.head='invalid');
});
test('selected source fields and safe bounded paths are mandatory',()=>{
 for(const path of ['../calculator.py','C:/calculator.py','a\\calculator.py','.git/calculator.py','.env.local/a.py',
                    'node_modules/a.py','ui-state/a.py','secrets/a.py','calculator.md'])reject(d=>d.source.files[0].path=path);
 for(const lines of [0,true,1.5,65538])reject(d=>d.source.files[0].lines=lines);
 reject(d=>d.source.private_root='private');reject(d=>d.source.files=[]);
 reject(d=>d.source.files=Array(21).fill(d.source.files[0]));
 reject(d=>d.source.files.push({...d.source.files[0],path:'CALCULATOR.py'}));
 reject(d=>d.source.files[0].source_sha256='bad');
});
test('nodes require exact source hashes languages ranges and unique identities',()=>{
 for(const patch of [{start_line:0},{start_line:true},{end_line:4},{end_line:0},{path:'unselected.py'},
  {language:'typescript'},{kind:'instruction'},{source_sha256:'9'.repeat(64)},{name:'a\n'},{excerpt:'private'}])
  reject(d=>Object.assign(d.hits[0],patch));
 reject(d=>d.hits.push(structuredClone(d.hits[0])));
 reject(d=>d.related_nodes[0].id='add');
 reject(d=>d.hits=Array(6).fill(d.hits[0]));
 reject(d=>d.related_nodes=Array(51).fill(d.related_nodes[0]));
});
test('only bounded direct edges with all endpoints and no duplicate or detached rows are accepted',()=>{
 reject(d=>d.edges.push(structuredClone(d.edges[0])));
 reject(d=>d.edges[0].source='missing');reject(d=>d.edges[0].kind='execute');
 reject(d=>d.edges[0].approved=true);reject(d=>d.edges=Array(51).fill(d.edges[0]));
 reject(d=>d.edges=[]);
 reject(d=>{d.related_nodes.push({...d.related_nodes[0],id:'other'});d.edges.push({source:'file',target:'other',kind:'calls'});});
});
test('malformed context is rejected before credentials or SDK activity',async()=>{
 await assert.rejects(generatePlan({issue,base_sha:base,files:[{path:'calculator.py',content:'def add(a,b): return a-b\n'}],
  structure_context:{...sample(),runtime_authorized:true}}),/Invalid structure context/);
});
test('real Pi receives separate v3 decision and CodeGraph metadata and retains approvals',{
 skip: !existsSync(path.join(project,'runtime/codegraph/node_modules/@colbymchenry/codegraph/package.json'))
  && 'Optional pinned CodeGraph runtime not installed; see runtime/codegraph/README.md',
},async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'pi-ui-structure-memory-')),mock=await startMock();
 try{
  await promisify(execFile)(python,[path.join(project,'tests/pi_structure_memory_trial.py'),path.join(root,'sample'),mock.url],
   {cwd:project,windowsHide:true,timeout:90000});
  const proof=JSON.parse(await fs.readFile(path.join(root,'sample/summary.json'),'utf8'));
  assert.equal(proof.passed,true);assert.equal(mock.requests.length,2);
  for(const request of mock.requests){
   const message=request.messages.find(m=>m.role==='user');
   const data=JSON.parse(typeof message.content==='string'?message.content:message.content[0].text);
   const snapshot=await Promise.all(data.authorized_paths.map(async name=>({path:name,
    content:await fs.readFile(path.join(root,'sample/repository',name),'utf8')})));
   assert.equal(data.files,undefined); // Source remains lazy read_source, not initial prompt text.
   assert.equal(verifiedMemory(data.source_memory,data.issue).schema_version,3);
   assert.equal(parseStructure(data.structure_context,data.issue,data.base_sha,snapshot).context_sha256,proof.structure_context_sha256);
   assert.equal(data.source_memory.context_sha256,proof.memory_context_sha256);
   assert.deepEqual(request.tools.map(t=>t.function.name).sort(),['propose_repair','read_source']);
  }
 }finally{await mock.close();await removeOwnFixture(root,os.tmpdir());}
});
