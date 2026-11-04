import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {setUiLanguage} from '../src/ui-language.mjs';

const sourceUrl=new URL('../../src/repo_maintainer/workbench_tools.py',import.meta.url);
const originalBytes=readFileSync(sourceUrl);
const source=originalBytes.toString('utf8');
const definitionSection=source.split('def parameters(')[0];
const chinese=/[\u3400-\u9fff]/;
const ownCopy=[...new Set([
 ...[...definitionSection.matchAll(/'([^'\\]*(?:\\.[^'\\]*)*)'/g)].map(match=>match[1]).filter(value=>chinese.test(value)),
 ...[...source.matchAll(/return '([^']*原可选[^']*)'/g)].map(match=>match[1])
])];
const definitionHash=value=>createHash('sha256').update(value.split('def parameters(')[0]).digest('hex');

test('all 66 fixed service definition captions switch both ways without changing original definitions',async()=>{
 const {toolDefinitionDisplayText}=await import('../src/tool-definition-language.mjs');
 assert.equal(ownCopy.length,66);
 const originalHash=definitionHash(source);
 try{
  for(const value of ownCopy){
   setUiLanguage('zh-CN');assert.equal(toolDefinitionDisplayText(value),value);
   setUiLanguage('en');const en=toolDefinitionDisplayText(value);
   assert.equal(typeof en,'string');assert.ok(en.length);assert.doesNotMatch(en,chinese);
   setUiLanguage('zh-CN');assert.equal(toolDefinitionDisplayText(value),value);
  }
  const after=readFileSync(sourceUrl);
  assert.deepEqual(after,originalBytes);
  assert.equal(definitionHash(after.toString('utf8')),originalHash);
 }finally{setUiLanguage('zh-CN');}
});

test('unknown service diagnostics, user text and actual option values remain original',async()=>{
 const {toolDefinitionDisplayText}=await import('../src/tool-definition-language.mjs');
 try{
  setUiLanguage('en');
  for(const value of ['用户自己的描述','服务临时诊断：目标 Git 仓库完整路径 不可用',
   'git-compare','repository','workspace_id','commit','range','branch','import','verify','backup',
   'no','yes','Windows','Linux','Darwin','JSON','Markdown'])assert.equal(toolDefinitionDisplayText(value),value);
  assert.equal(toolDefinitionDisplayText(undefined),undefined);
  assert.equal(toolDefinitionDisplayText(null),null);
  setUiLanguage('zh-CN');assert.equal(toolDefinitionDisplayText('目标 Git 仓库完整路径'),'目标 Git 仓库完整路径');
 }finally{setUiLanguage('zh-CN');}
});
