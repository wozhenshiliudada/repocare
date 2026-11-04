import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {capabilityRoute,toolWorkflows,nativeWorkflows} from '../src/capability-routing.mjs';
import {workflows} from '../src/preview-workflows.mjs';
import {connectionLabel,downloadArtifact} from '../src/existing-capability-model.mjs';
const catalog=JSON.parse(await fs.readFile(new URL('../src/capabilities.json',import.meta.url),'utf8'));
const backend=await fs.readFile(new URL('../../src/repo_maintainer/workbench_tools.py',import.meta.url),'utf8');
const runtimeLimits=JSON.parse(await fs.readFile(new URL('../../src/repo_maintainer/pi_runtime_limits.json',import.meta.url),'utf8'));

test('every existing CLI workflow routes to the actual server tool registry, never LocalPreview',()=>{
 const ids=[...backend.matchAll(/^    '([a-z-]+)': \('/gm)].map(m=>m[1]);
 assert.deepEqual([...toolWorkflows].sort(),ids.sort());
 for(const flow of workflows.filter(w=>w.connection==='cli')){
  assert.notEqual(capabilityRoute(flow.id).kind,'preview',flow.id);
  assert.doesNotMatch(connectionLabel(flow),/仅CLI|后端未实现/,flow.id);
 }
 for(const id of toolWorkflows){
  assert.ok(workflows.some(w=>w.id===id),id);
  assert.ok(catalog.some(c=>[c.workflow,...c.relatedWorkflows].includes(id)),id);
 }
});
test('real settings, intake, comments and CI evidence no longer claim backend absence',()=>{
 for(const id of ['B33','C15','C18','D16']){
  const row=catalog.find(c=>c.id===id);assert.ok(['connected','mixed'].includes(row.availability),id);
  assert.doesNotMatch(row.description,/执行能力尚未接通|后端未实现/,id);
 }
 assert.equal(capabilityRoute('provider').kind,'native');
 assert.ok(nativeWorkflows.provider.provider);
 assert.equal(capabilityRoute('auto-intake').page,'issues');
 assert.equal(capabilityRoute('issue-comment').page,'issues');
 assert.equal(capabilityRoute('last-success').workflow,'ci-investigation');
 assert.equal(capabilityRoute('global-metrics').kind,'preview');
 assert.equal(capabilityRoute('merge').kind,'preview');
});
test('new local tool artifacts use fixed authenticated downloads without executable URLs',async()=>{
 for(const name of ['tool-result.json','tool-report.md']){
  let saved=false;
  await downloadArtifact({name,url:'/api/exports/'+'a'.repeat(32)+'/'+name,bytes:2},'session-only',{
   fetcher:async(url,opts)=>{assert.equal(opts.headers.Authorization,'Bearer session-only');return {ok:true,blob:async()=>new Blob(['ok'])};},
   save:async(blob,file)=>{assert.equal(file,name);saved=true;}
  });assert.ok(saved);
 }
});
test('catalog separates live chat and outward MCP from unimplemented streaming, hot switching and fallback',()=>{
 const row=id=>catalog.find(entry=>entry.id===id);
 assert.equal(row('B35').availability,'mixed');assert.match(row('B35').description,/无仓库普通聊天/);assert.match(row('B35').description,/流式显示尚未实现/);
 assert.equal(row('M16').availability,'mixed');assert.match(row('M16').description,/标准stdio服务/);assert.match(row('M16').description,/--allow-plan/);assert.match(row('M16').description,/未提供导入任意外部MCP工具/);
 assert.equal(row('F18').availability,'preview');assert.match(row('F18').description,/中途换模型.*仍未实现/);assert.equal(row('F19').availability,'preview');assert.equal(row('C20').availability,'preview');
});
test('catalog numeric request and planning defaults agree with the current shared runtime configuration',()=>{
 assert.match(catalog.find(row=>row.id==='J03').title,new RegExp(String(runtimeLimits.max_requests)));
 assert.match(catalog.find(row=>row.id==='J05').title,new RegExp(String(runtimeLimits.planning_timeout_seconds/60)+'分钟'));
 assert.match(catalog.find(row=>row.id==='J04').description,new RegExp(String(runtimeLimits.max_output_tokens)));
 assert.match(catalog.find(row=>row.id==='J03').description,/不因.*重置旧额度/);
});
