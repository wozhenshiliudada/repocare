import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {setUiLanguage} from '../src/ui-language.mjs';
import {capabilityRoute} from '../src/capability-routing.mjs';

const sourceUrl=new URL('../src/capabilities.json',import.meta.url);
const originalBytes=readFileSync(sourceUrl);
const catalog=JSON.parse(originalBytes.toString('utf8'));
const copy=[...new Set(catalog.flatMap(entry=>[entry.title,entry.description,...(entry.nativeLinks||[]).map(link=>link.label)]).filter(Boolean))];
const chinese=/[\u3400-\u9fff]/;
function routeData(id){const {label,detail,button,...data}=capabilityRoute(id);return data;}

test('all 435 source-owned catalog captions switch both ways after module import',async()=>{
 const {catalogDisplayText}=await import('../src/catalog-language.mjs');
 assert.equal(copy.length,435);
 try{
  for(const value of copy){
   setUiLanguage('zh-CN');assert.equal(catalogDisplayText(value),value);
   setUiLanguage('en');const en=catalogDisplayText(value);
   assert.equal(typeof en,'string');assert.ok(en.length);assert.doesNotMatch(en,chinese);
   setUiLanguage('zh-CN');assert.equal(catalogDisplayText(value),value);
  }
  assert.deepEqual(readFileSync(sourceUrl),originalBytes);
 }finally{setUiLanguage('zh-CN');}
});

test('English catalog search leaves original identifiers, routing and native selection payloads intact',async()=>{
 const {catalogDisplayText}=await import('../src/catalog-language.mjs');
 const snapshot=JSON.stringify(catalog);
 const payload=entry=>({page:entry.page,tab:entry.tab});
 const snapshots=catalog.map(entry=>({id:entry.id,workflow:entry.workflow,relatedWorkflows:entry.relatedWorkflows,
  availability:entry.availability,native:payload(entry),route:entry.workflow?routeData(entry.workflow):null}));
 try{
  setUiLanguage('en');
  const found=catalog.filter(entry=>(entry.id+' '+catalogDisplayText(entry.title)).toLowerCase().includes('local git repository'));
  assert.ok(found.some(entry=>entry.id==='A01'));
  assert.deepEqual(catalog.map(entry=>({id:entry.id,workflow:entry.workflow,relatedWorkflows:entry.relatedWorkflows,
   availability:entry.availability,native:payload(entry),route:entry.workflow?routeData(entry.workflow):null})),snapshots);
  assert.equal(JSON.stringify(catalog),snapshot);assert.deepEqual(readFileSync(sourceUrl),originalBytes);
  assert.equal(catalogDisplayText('用户原文和模型输出'),'用户原文和模型输出');
  assert.equal(catalogDisplayText('A01'),'A01');assert.equal(catalogDisplayText('overview'),'overview');
  setUiLanguage('zh-CN');assert.equal(catalogDisplayText(catalog[0].title),catalog[0].title);
 }finally{setUiLanguage('zh-CN');}
});
