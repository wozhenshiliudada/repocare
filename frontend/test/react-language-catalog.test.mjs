import './tsx-register.mjs';
import {registerHooks} from 'node:module';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {setUiLanguage} from '../src/ui-language.mjs';
import {workflows} from '../src/preview-workflows.mjs';
registerHooks({load(url,context,next){
 if(url.endsWith('/src/capabilities.json'))return {format:'module',source:'export default '+readFileSync(new URL(url),'utf8'),shortCircuit:true};
 return next(url,context);
}});
const {CapabilityPanel,LocalPreview}=await import('../src/CapabilityPanel.tsx');
const catalog=JSON.parse(readFileSync(new URL('../src/capabilities.json',import.meta.url),'utf8'));
const render=(type,props)=>renderToStaticMarkup(React.createElement(type,props));
function nodes(node,predicate){return !node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(n=>nodes(n,predicate)):
 [...(predicate(node)?[node]:[]),...nodes(node.props?.children,predicate)];}

test('all capability-page captions render in English while catalog data stays original',()=>{
 const before=JSON.stringify(catalog);
 try{
  setUiLanguage('en');
  for(const page of new Set(catalog.map(entry=>entry.page))){
   const html=render(CapabilityPanel,{page,repositoryKey:'fixture',navigate(){throw Error('render navigated');}});
   assert.doesNotMatch(html,/\p{Script=Han}/u,page+' has untranslated catalog/interface captions');
  }
  assert.equal(JSON.stringify(catalog),before);
 }finally{setUiLanguage('zh-CN');}
});

test('local workflow fields translate captions while select options preserve their original actual values',()=>{
 const workflow=workflows.find(value=>value.id==='issue-create'),before=JSON.stringify(workflow),seen=[];
 const internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
 try{for(const language of ['zh-CN','en']){
  setUiLanguage(language);const old=internal.H;
  internal.H={useContext:context=>context._currentValue,useState:value=>[value,()=>{}]};
  let tree;try{tree=LocalPreview({workflow,repositoryKey:'fixture'});}finally{internal.H=old;}
  const options=nodes(tree,node=>node.type==='option');
  const fields=nodes(tree,node=>['input','select','textarea'].includes(node.type));
  assert.ok(fields.length>0);assert.ok(fields.every(node=>node.props['data-form-assist-draft']==='true'));
  for(const field of fields)field.props.onChange({target:{value:'draft-reference-only'}});
  assert.ok(nodes(tree,node=>node.type==='button').every(node=>node.props['data-form-assist-draft']===undefined));
  seen.push(options.map(node=>node.props.value));
  const idea=options.find(node=>node.props.value==='想法');assert.ok(idea);
  assert.equal(idea.props.children,language==='en'?'Idea':'想法');
  assert.equal(JSON.stringify(workflow),before);
 }}finally{setUiLanguage('zh-CN');}
 assert.deepEqual(seen[0],seen[1]);assert.ok(seen[0].includes('想法'));
});
