import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {workflows,validateDraft,previewDraft} from '../src/preview-workflows.mjs';
const catalog=JSON.parse(await fs.readFile(new URL('../src/capabilities.json',import.meta.url),'utf8'));
const audit=await fs.readFile(new URL('../../docs/tasks/full-workbench-ui/FRONTEND_ACCEPTANCE_CHECKLIST_20261022.md',import.meta.url),'utf8');
const ids=[...audit.matchAll(/^\| ([A-O]\d{2}) \|/gm)].map(m=>m[1]);
const pages=new Set(['overview','tasks','issues','events','memory','delivery','configuration','service']);

test('all 332 audited capabilities retain their own ID and a reachable page',()=>{
 assert.equal(ids.length,332);assert.deepEqual(catalog.map(c=>c.id),ids);
 assert.equal(new Set(catalog.map(c=>c.id)).size,332);assert.ok(catalog.every(c=>pages.has(c.page)&&c.description));
});
test('every absent/CLI interaction has a real workflow and every local workflow is linked from the index',()=>{
 const workflowIds=new Set(workflows.map(w=>w.id)),linked=new Set(catalog.flatMap(c=>[c.workflow,...c.relatedWorkflows]).filter(Boolean));
 assert.equal(workflowIds.size,workflows.length);assert.deepEqual([...linked].sort(),[...workflowIds].sort());
 for(const c of catalog){if(['后端缺入口','仅说明','无交互'].includes(c.baseline))assert.ok(c.workflow,c.id);for(const id of [c.workflow,...c.relatedWorkflows].filter(Boolean))assert.ok(workflowIds.has(id),c.id);}
 for(const w of workflows)assert.ok(pages.has(w.page)&&w.fields.length>=3,w.id);
});
function validValues(w){return Object.fromEntries(w.fields.map(f=>[f.key,f.type==='number'?String(f.min||1):f.type==='select'?f.options[0]:f.format==='https'?'https://example.invalid/api':f.format==='argv'?'["python", "-m", "unittest"]':'reference-only']));}
test('all local workflows validate inputs and preview without execution outcomes or network calls',()=>{
 let requests=0;const original=globalThis.fetch;globalThis.fetch=()=>{requests++;throw Error('preview attempted network');};
 try{for(const w of workflows){assert.ok(Object.keys(validateDraft(w,{})).length,w.id);const value=previewDraft(w,validValues(w));assert.deepEqual(value.errors,{},w.id);assert.equal(value.preview.executed,false);assert.equal(value.preview.status,'local_preview_only');assert.ok(value.preview.blocking_dependency);assert.equal('success' in value.preview,false);}}finally{globalThis.fetch=original;}
 assert.equal(requests,0);
});
test('local previews reject credential-looking values, credential URLs and malformed argv',()=>{
 const provider=workflows.find(w=>w.id==='provider'),values=validValues(provider);
 values.credential='token=not-a-real-secret-123456789';assert.ok(validateDraft(provider,values).credential);
 values.credential='configuration-reference';values.baseUrl='https://user:password@example.invalid';assert.ok(validateDraft(provider,values).baseUrl);
 const shell=workflows.find(w=>w.id==='shell'),argv=validValues(shell);argv.argv='echo hello';assert.ok(validateDraft(shell,argv).argv);
 argv.argv='["python",null]';assert.ok(validateDraft(shell,argv).argv);
});
test('numeric budgets, invalid select choices and control characters do not produce a preview',()=>{
 const w=workflows.find(w=>w.id==='costs'),values=validValues(w);values.amount='-1';values.window='pretend-unlimited';values.currency='A\u0000B';
 const result=previewDraft(w,values);assert.equal(result.preview,null);for(const key of ['amount','window','currency'])assert.ok(result.errors[key]);
});
test('migrated task receipts and maintenance actions point to their actual native pages',()=>{
 const legacy=catalog.filter(c=>c.baseline==='原控件覆盖');assert.equal(legacy.length,38);
 for(const c of legacy){assert.ok(c.nativeLinks?.length,c.id);for(const link of c.nativeLinks)assert.ok(pages.has(link.page)&&link.label,c.id);}
 for(const id of ['K01','K02','K03','K04','K05','K48','H05','H09'])assert.equal(catalog.find(c=>c.id===id).nativeLinks[0].page,'tasks',id);
 for(const id of ['K37','K38','K39','K40','K41'])assert.equal(catalog.find(c=>c.id===id).nativeLinks[0].page,'events',id);
 assert.deepEqual(catalog.find(c=>c.id==='B25').nativeLinks.map(l=>l.page),['events','memory']);
});
