import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {randomUUID} from 'node:crypto';
import {captureForm,applyProposal} from '../src/form-assist.mjs';
const code=await fs.readFile(new URL('../../src/repo_maintainer/workbench_assets/app.js',import.meta.url),'utf8');
class Element {
 constructor(tag='DIV'){this.tagName=tag;this.children=[];this.dataset={};this.hidden=false;this.value='';this.textContent='';this.checked=false;this.classList={toggle(){}};this.nodes=new Map();this.attributes=new Map();this.isConnected=true;this.labels=[];this.type='text';this.events=[];}
 get value(){return this._value;}set value(value){this._value=value;}
 get checked(){return this._checked;}set checked(value){this._checked=Boolean(value);}
 get options(){return this.children;}getClientRects(){return this.hidden?[]:[{}];}closest(){return null;}matches(){return false;}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=[...children];}
 querySelector(selector){if(!this.nodes.has(selector))this.nodes.set(selector,new Element());return this.nodes.get(selector);}
 querySelectorAll(selector){return selector==='input,textarea,select'?this.assistFields||[]:[];}
 setAttribute(key,value){this.attributes.set(key,String(value));}getAttribute(key){return this.attributes.get(key)??null;}
 dispatchEvent(event){this.events.push(event.type);this['on'+event.type]?.({target:this,currentTarget:this});return true;}
 scrollIntoView(){}
}
function setup({saved=false,fail=false,fileCount=0,unified=false}={}){
 const root=new Element(),calls=[],selected=[];
 const id='a'.repeat(32),url='https://github.com/example/private.git';
 const policy={allowed_paths:['calculator.py'],readonly_paths:['tests/test_calculator.py'],allowed_tests:[['{python}','-m','unittest']],timeout_seconds:30};
 const source={path:'C:/fixture/clone',branch:'main',head:'b'.repeat(40),revision:'fixed',clean:true,files:['calculator.py','tests/test_calculator.py'],checks:{recommendations:[],warnings:[],suggested_editable:[],suggested_readonly:[]},...(saved?{saved_scope:{policy,allow_auto:false}}:{})};
 if(fileCount)source.files=Array.from({length:fileCount},(_,i)=>'file'+String(i).padStart(5,'0')+'.py');
 const state={workspaces:[],sessions:[],monitors:[],allow_auto:false,request_budget:6};
 let notificationSettings=0;
 const sandbox={window:{RepoCareTabSession:{open:()=>({token:'fixture-only',selectedView:()=>null,saveView(){}})}},document:{getElementById:()=>null,createElement:tag=>new Element(tag.toUpperCase()),createTextNode:text=>({textContent:text}),querySelectorAll:()=>[]},location:{origin:'http://127.0.0.1:12345'},URL,crypto:{randomUUID},setInterval:()=>1,clearInterval(){},fetch:async(path,options)=>{
  if(path==='/api/state')return {ok:true,status:200,json:async()=>structuredClone(state)};
  const body=JSON.parse(options.body),action=path.split('/').at(-1);calls.push({action,body});
  if(action==='clone'||action==='local')return {ok:true,status:202,json:async()=>({status:fail?'failed':'finished',result:fail?{error:'Clone failed'}:structuredClone(source)})};
  assert.equal(action,'connect');
  state.workspaces=[{id,token:'c'.repeat(64),api_prefix:'/api/workspaces/'+id+'/',repository:source.path}];
  return {ok:true,status:202,json:async()=>({status:'finished',result:{repository:source.path,launch_url:'http://127.0.0.1:12345/#workspace='+id}})};
 }};
 vm.runInNewContext(code,sandbox);
 const controller=sandbox.window.mountWorkbenchOnboarding(root,{onSelectWorkspace:value=>selected.push(value),unifiedNotifications:unified,onOpenNotifications:()=>notificationSettings++});
 const get=id=>root.querySelector('[id="'+id+'"]');
 return {controller,calls,selected,get,url,policy,root,notificationSettings:()=>notificationSettings};
}
test('selected remote goes straight to its clone and first scope without granting maintenance',async()=>{
 const f=setup();await f.controller.connectRepository(f.url);
 assert.deepEqual(f.calls.map(c=>c.action),['clone']);assert.equal(f.calls[0].body.url,f.url);
 assert.equal(f.get('github-url').value,f.url);assert.equal(f.get('local-path').value,'C:/fixture/clone');assert.equal(f.get('scope').hidden,false);assert.equal(f.get('confirmed').checked,false);assert.deepEqual(f.selected,[]);
 f.controller.showPicker();assert.equal(f.get('onboarding').querySelector('.sources').hidden,false);f.controller.dispose();
});
test('explicit connect restores exact saved scope then opens the original workspace',async()=>{
 const f=setup({saved:true});await f.controller.connectRepository(f.url);
 assert.deepEqual(f.calls.map(c=>c.action),['clone','connect']);
 const {request_id,...body}=f.calls[1].body;
 assert.deepEqual(body,{path:'C:/fixture/clone',revision:'fixed',...f.policy,allow_auto:false,confirmed:true});assert.deepEqual(f.selected,['a'.repeat(32)]);f.controller.dispose();
});
test('failed clone retains selected address for retry and never connects stale scope',async()=>{
 const f=setup({fail:true});await f.controller.connectRepository(f.url);
 assert.deepEqual(f.calls.map(c=>c.action),['clone']);assert.equal(f.get('scope').hidden,true);assert.equal(f.get('github-url').value,f.url);assert.equal(f.get('notice').textContent,'Clone failed');assert.equal(f.get('onboarding').querySelector('.sources').hidden,false);assert.deepEqual(f.selected,[]);f.controller.dispose();
});


test('saved local project is inspected with original scope and never auto-connects',async()=>{
 const f=setup({saved:true});await f.controller.inspectRepository('C:/fixture/clone');
 assert.deepEqual(f.calls.map(c=>c.action),['local']);assert.equal(f.get('scope').hidden,false);
 assert.equal(f.get('confirmed').checked,false);assert.equal(f.get('test-timeout').value,30);
 assert.deepEqual(f.selected,[]);f.controller.dispose();
});
test('failed saved-project inspection hides stale scope and cannot reconnect it',async()=>{
 const f=setup({saved:true,fail:true});await f.controller.inspectRepository('C:/fixture/missing');
 assert.deepEqual(f.calls.map(c=>c.action),['local']);assert.equal(f.get('scope').hidden,true);
 assert.equal(f.get('confirmed').checked,false);assert.deepEqual(f.selected,[]);f.controller.dispose();
});


test('original range picker paginates and keeps selected permissions outside the visible page',async()=>{
 const f=setup({fileCount:405});await f.controller.inspectRepository('C:/fixture/clone');
 assert.equal(f.get('files').children.length,200);f.get('file-next').onclick();
 assert.equal(f.get('files').children.length,200);
 const select=f.get('files').children[0].children.at(-1);select.value='edit';select.onchange();
 f.get('file-next').onclick();assert.equal(f.get('files').children.length,5);
 f.get('file-filter').value='file00200.py';f.get('file-filter').oninput();
 assert.equal(f.get('files').children.length,1);assert.equal(f.get('files').children[0].children.at(-1).value,'edit');
 assert.match(f.get('file-page').textContent,/第 1 \/ 1 页/);f.controller.dispose();
});
test('assistant drafts follow the real path → inspect → file/test scope → explicit confirmation flow without triggering a step',async()=>{
 const f=setup({unified:true});
 const field=(id,tag,label,type='text')=>{const node=f.get(id);node.tagName=tag;node.id=id;node.type=type;node.labels=[{textContent:label}];return node;};
 try{
  const path=field('local-path','INPUT','文件夹路径');f.root.assistFields=[path];const initial=await captureForm(f.root);assert.ok(initial);
  const draft=(snapshot,changes)=>({form_id:snapshot.form_id,revision:snapshot.revision,draft_only:true,changes});
  assert.equal((await applyProposal(f.root,initial,draft(initial,[{field_id:initial.fields[0].id,value:'C:/fixture/clone'}]))).applied,true);assert.equal(f.calls.length,0);assert.equal(f.get('confirmed').checked,false);
  await f.get('inspect').onclick();assert.deepEqual(f.calls.map(call=>call.action),['local']);
  const select=f.get('files').children[0].children.at(-1),command=field('test-command','TEXTAREA','自定义检查命令（JSON 参数数组）'),timeout=field('test-timeout','INPUT','每条测试超时（秒）','number'),confirmed=field('confirmed','INPUT','我确认维护范围并允许模型计费','checkbox');timeout.min='1';timeout.max='1800';
  f.root.assistFields=[select,command,timeout,confirmed];const scope=await captureForm(f.root);assert.equal(scope.fields.length,3);assert.equal(scope.fields.some(item=>item.type==='checkbox'),false);
  const expected=[['{python}','-m','unittest']],changes=scope.fields.map(item=>({field_id:item.id,value:item.type==='select'?'edit':item.type==='number'?90:JSON.stringify(expected)}));
  assert.equal((await applyProposal(f.root,scope,draft(scope,changes))).applied,true);assert.deepEqual(f.calls.map(call=>call.action),['local']);assert.equal(confirmed.checked,false);assert.equal(f.get('auto-mode').checked,false);
  confirmed.checked=true;await f.get('connect').onclick();assert.deepEqual(f.calls.map(call=>call.action),['local','connect']);assert.deepEqual(f.calls[1].body.allowed_tests,expected);assert.equal(f.calls[1].body.timeout_seconds,90);assert.deepEqual(f.calls[1].body.allowed_paths,['calculator.py']);assert.equal(f.calls[1].body.allow_auto,false);
  await f.get('notify').onclick();assert.equal(f.notificationSettings(),1);
 }finally{f.controller.dispose();}
});
