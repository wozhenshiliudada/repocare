import {test} from 'node:test';
import assert from 'node:assert/strict';
import {captureForm,applyProposal,describeProposal,invalidateForm,formCaptureInfo} from '../src/form-assist.mjs';
class Field{
 constructor(label,value='',patch={}){this.tagName='INPUT';this.type='text';this.labels=[{textContent:label}];this._value=value;this._checked=false;this.isConnected=true;this.disabled=false;this.readOnly=false;this.min='';this.max='';this.events=[];this.id='dom-id';this.name='form-field';this.attributes={'data-form-assist-draft':'true'};this.ownerDocument={defaultView:{Event,getComputedStyle:()=>({display:'block',visibility:'visible'})}};Object.assign(this,patch);}
 get value(){return this._value;}set value(value){this._value=String(value);}
 get checked(){return this._checked;}set checked(value){this._checked=Boolean(value);}
 getClientRects(){return this.invisible?[]:[{}];}closest(selector){return selector.includes('exclude')?this.excluded?this:null:this.hidden?this:null;}
 getAttribute(name){return this.attributes?.[name]??null;}matches(){return this.disabled;}
 dispatchEvent(event){this.events.push(event.type);return true;}click(){throw Error('click forbidden');}
}
class Root extends Field{
 constructor(fields){super('root');this.fields=fields;this.tagName='FORM';}
 querySelectorAll(){return this.fields;}contains(node){return this.fields.includes(node);}submit(){throw Error('submit forbidden');}
}
const proposal=(snapshot,changes)=>({form_id:snapshot.form_id,revision:snapshot.revision,changes:changes||[{field_id:snapshot.fields[0].id,value:'updated'}],explanation:'draft only',draft_only:true});
test('capture uses opaque stable ids and excludes secrets, permissions, invisible and uneditable fields',async()=>{
 const safe=new Field('配置名称','开发模型'),fields=[safe,new Field('密码','password-secret',{type:'password'}),new Field('Base API Key','key-secret'),new Field('访问令牌','token-secret'),new Field('hidden','hidden-secret',{type:'hidden'}),new Field('文件','',{type:'file'}),new Field('批准执行','',{type:'checkbox'}),new Field('Enable autonomous mode','',{type:'checkbox'}),new Field('普通字段','disabled-secret',{disabled:true}),new Field('普通字段','readonly-secret',{readOnly:true}),new Field('普通字段','invisible-secret',{invisible:true}),new Field('普通字段','assistant-secret',{excluded:true})];
 const root=new Root(fields),snapshot=await captureForm(root),again=await captureForm(root);
 assert.equal(snapshot.fields.length,1);assert.match(snapshot.form_id,/^[a-f0-9]{32}$/);assert.match(snapshot.revision,/^[a-f0-9]{64}$/);assert.match(snapshot.fields[0].id,/^[a-f0-9]{32}$/);assert.equal(snapshot.fields[0].id,again.fields[0].id);assert.equal(snapshot.revision,again.revision);
 assert.doesNotMatch(JSON.stringify(snapshot),/password-secret|key-secret|token-secret|hidden-secret|disabled-secret|readonly-secret|invisible-secret|assistant-secret|dom-id/);
});
test('number/select/checkbox fields retain types, allowed options, and numeric boundaries',async()=>{
 const root=new Root([new Field('上下文长度','200000',{type:'number',min:'4096',max:'2097152'}),new Field('语言','en',{tagName:'SELECT',options:[{value:'zh-CN'},{value:'en'},{value:'blocked',disabled:true}]}),new Field('站内通知','',{type:'checkbox',_checked:true})]);
 const snapshot=await captureForm(root);assert.deepEqual(snapshot.fields.map(({id,...field})=>field),[{label:'上下文长度',type:'number',value:200000,min:4096,max:2097152},{label:'语言',type:'select',value:'en',options:['zh-CN','en'],option_labels:['zh-CN','en']},{label:'站内通知',type:'checkbox',value:true}]);
});
test('showing a proposal has no effect; explicit apply updates drafts through input/change only',async()=>{
 const first=new Field('配置名称','before'),second=new Field('长度','4096',{type:'number',min:'1',max:'10000'}),check=new Field('站内通知','',{type:'checkbox'}),root=new Root([first,second,check]);
 const snapshot=await captureForm(root),draft=proposal(snapshot,[{field_id:snapshot.fields[0].id,value:'after'},{field_id:snapshot.fields[1].id,value:8192},{field_id:snapshot.fields[2].id,value:true}]);
 const diff=describeProposal(snapshot,draft);assert.equal(diff[0].before,'before');assert.equal(diff[0].after,'after');assert.equal(first.value,'before');assert.deepEqual(first.events,[]);
 assert.deepEqual(await applyProposal(root,snapshot,draft),{applied:true,reason:'applied'});assert.equal(first.value,'after');assert.equal(second.value,'8192');assert.equal(check.checked,true);
 for(const field of [first,second,check])assert.deepEqual(field.events,['input','change']);assert.deepEqual(await applyProposal(root,snapshot,draft),{applied:false,reason:'stale'});
});
test('user edits, changed options, replaced nodes, switched roots and explicit navigation expire a proposal',async()=>{
 const field=new Field('名称','before'),root=new Root([field]);let snapshot=await captureForm(root);field.value='typed';assert.equal((await applyProposal(root,snapshot,proposal(snapshot))).reason,'stale');assert.equal(field.value,'typed');
 snapshot=await captureForm(root);root.fields=[new Field('名称','typed')];assert.equal((await applyProposal(root,snapshot,proposal(snapshot))).reason,'stale');
 snapshot=await captureForm(root);assert.equal((await applyProposal(new Root(root.fields),snapshot,proposal(snapshot))).reason,'stale');invalidateForm(root);assert.equal((await applyProposal(root,snapshot,proposal(snapshot))).reason,'stale');
 const select=new Field('模型','a',{tagName:'SELECT',options:[{value:'a'},{value:'b'}]}),selectRoot=new Root([select]),selectSnapshot=await captureForm(selectRoot);select.options=[{value:'a'}];assert.equal((await applyProposal(selectRoot,selectSnapshot,proposal(selectSnapshot,[{field_id:selectSnapshot.fields[0].id,value:'b'}]))).reason,'stale');
});
test('all proposal changes validate before any write; unknown fields, bad ranges and permission flags are rejected',async()=>{
 const field=new Field('长度','10',{type:'number',min:'1',max:'20'}),root=new Root([field]),snapshot=await captureForm(root);
 for(const changes of [[{field_id:snapshot.fields[0].id,value:21}],[{field_id:snapshot.fields[0].id,value:15},{field_id:'f'.repeat(32),value:'unknown'}],[{field_id:snapshot.fields[0].id,value:15},{field_id:snapshot.fields[0].id,value:12}],[{field_id:snapshot.fields[0].id,value:'15'}]]){
  assert.equal((await applyProposal(root,snapshot,proposal(snapshot,changes))).reason,'invalid');assert.equal(field.value,'10');assert.deepEqual(field.events,[]);
 }
 assert.equal((await applyProposal(root,snapshot,{...proposal(snapshot),draft_only:false})).reason,'invalid');
});
test('a disconnected or empty form has no safe snapshot and cloned snapshots cannot grant a DOM binding',async()=>{
 assert.equal(await captureForm(new Root([])),null);const root=new Root([new Field('名称','before')]),snapshot=await captureForm(root);
 assert.equal((await applyProposal(root,structuredClone(snapshot),proposal(snapshot))).reason,'stale');root.isConnected=false;assert.equal(await captureForm(root),null);assert.equal((await applyProposal(root,snapshot,proposal(snapshot))).reason,'stale');
});
test('large Unicode forms keep only fields within the backend 100000-byte limit and report omitted fields',async()=>{
 const fields=Array.from({length:20},(_,index)=>new Field('字段 '+index,'中'.repeat(4000))),root=new Root(fields),snapshot=await captureForm(root);
 assert.ok(snapshot.fields.length>0&&snapshot.fields.length<20);assert.ok(new TextEncoder().encode(JSON.stringify(snapshot)).byteLength<=100000);assert.equal(formCaptureInfo(snapshot).omitted,20-snapshot.fields.length);
 const again=await captureForm(root);assert.equal(again.revision,snapshot.revision);assert.equal(formCaptureInfo(again).omitted,formCaptureInfo(snapshot).omitted);
});
test('unregistered controls cannot be filled even when their labels look safe and their handlers autosave',async()=>{
 let autosaves=0;const field=new Field('普通名称','before',{attributes:{},dispatchEvent(){autosaves++;}}),root=new Root([field]);assert.equal(await captureForm(root),null);assert.equal(autosaves,0);assert.equal(field.value,'before');
});
test('synchronous input handlers cannot repurpose the next node into disabled approval or secret fields',async()=>{
 const first=new Field('流程选择','first'),second=new Field('显示提示','',{type:'checkbox'}),root=new Root([first,second]),snapshot=await captureForm(root);
 first.dispatchEvent=event=>{first.events.push(event.type);second.labels=[{textContent:'我确认以上范围并批准执行'}];second.disabled=true;};
 const draft=proposal(snapshot,[{field_id:snapshot.fields[0].id,value:'other-step'},{field_id:snapshot.fields[1].id,value:true}]),result=await applyProposal(root,snapshot,draft);
 assert.equal(result.applied,false);assert.equal(result.reason,'stale');assert.equal(result.updated,1);assert.equal(second.checked,false);assert.deepEqual(second.events,[]);assert.deepEqual(first.events,['input']);
});
test('synchronous changes to untouched values, options, bounds, or safe-draft registration stop the batch',async()=>{
 for(const mutate of [field=>field.value='changed by handler',field=>field.min='10',field=>field.attributes={},field=>field.labels=[{textContent:'API Key'}]]){
  const first=new Field('名称','old'),second=new Field('长度','5',{type:'number',min:'1',max:'20'}),root=new Root([first,second]),snapshot=await captureForm(root);
  first.dispatchEvent=()=>mutate(second);const result=await applyProposal(root,snapshot,proposal(snapshot,[{field_id:snapshot.fields[0].id,value:'new'},{field_id:snapshot.fields[1].id,value:15}]));
  assert.equal(result.applied,false);assert.equal(result.updated,1);assert.deepEqual(second.events,[]);
 }
});
test('an unchanged aria-label cannot hide a synchronous visible-label change into a secret purpose',async()=>{
 const first=new Field('名称','old'),second=new Field('普通字段','before',{attributes:{'data-form-assist-draft':'true','aria-label':'普通字段'}}),root=new Root([first,second]),snapshot=await captureForm(root);
 first.dispatchEvent=()=>{second.labels=[{textContent:'API Key'}];};const result=await applyProposal(root,snapshot,proposal(snapshot,[{field_id:snapshot.fields[0].id,value:'new'},{field_id:snapshot.fields[1].id,value:'must not write'}]));
 assert.equal(result.applied,false);assert.equal(second.value,'before');assert.deepEqual(second.events,[]);
});
test('select option captions do not become the field label or grant authority to an approval checkbox',async()=>{
 const select=new Field('', 'approval',{tagName:'SELECT',options:[{value:'approval'},{value:'auto'}],labels:[{tagName:'LABEL',childNodes:[{nodeType:3,textContent:'修复模式草稿'},{tagName:'SELECT',childNodes:[{nodeType:3,textContent:'人工审批后修改 授权范围内自主候选'}]}]}]}),confirmation=new Field('我批准执行','',{type:'checkbox'}),snapshot=await captureForm(new Root([select,confirmation]));
 assert.equal(snapshot.fields.length,1);assert.equal(snapshot.fields[0].label,'修复模式草稿');assert.deepEqual(snapshot.fields[0].options,['approval','auto']);
});

test('registered time drafts use the text protocol and valid minute values update without actions',async()=>{
 const start=new Field('开始时间','22:00',{type:'time'}),end=new Field('结束时间','08:00',{type:'time'}),root=new Root([start,end]),snapshot=await captureForm(root);
 assert.ok(snapshot);assert.deepEqual(snapshot.fields.map(({id,...field})=>field),[{label:'开始时间',type:'text',value:'22:00'},{label:'结束时间',type:'text',value:'08:00'}]);
 assert.deepEqual(await applyProposal(root,snapshot,proposal(snapshot,[{field_id:snapshot.fields[0].id,value:'23:15'},{field_id:snapshot.fields[1].id,value:'07:30'}])),{applied:true,reason:'applied'});
 assert.equal(start.value,'23:15');assert.equal(end.value,'07:30');assert.deepEqual(start.events,['input','change']);assert.deepEqual(end.events,['input','change']);
});

test('every time value validates before the first draft write and time registration cannot bypass secrets',async()=>{
 for(const value of ['24:00','25:00','08:60','8:00','08:00:00','','noon']){
  const name=new Field('名称','unchanged'),time=new Field('开始时间','22:00',{type:'time'}),root=new Root([name,time]),snapshot=await captureForm(root);
  assert.equal(snapshot.fields.length,2);const result=await applyProposal(root,snapshot,proposal(snapshot,[{field_id:snapshot.fields[0].id,value:'must stay unchanged'},{field_id:snapshot.fields[1].id,value}]));
  assert.deepEqual(result,{applied:false,reason:'invalid'});assert.equal(name.value,'unchanged');assert.equal(time.value,'22:00');assert.deepEqual(name.events,[]);assert.deepEqual(time.events,[]);
 }
 assert.equal(await captureForm(new Root([new Field('授权到期时间','22:00',{type:'time'}),new Field('API token','08:00',{type:'time'}),new Field('普通时间','12:00',{type:'time',attributes:{}})])),null);
});

test('select snapshots expose display captions for opaque values and expire on caption-only changes',async()=>{
 const select=new Field('模型配置','a'.repeat(32),{tagName:'SELECT',options:[{value:'a'.repeat(32),label:'Development · model-a'},{value:'b'.repeat(32),textContent:'Review · model-b'},{value:'c'.repeat(32),label:'Disabled model',disabled:true}]}),root=new Root([select]),snapshot=await captureForm(root);
 assert.deepEqual(snapshot.fields[0].option_labels,['Development · model-a','Review · model-b']);assert.deepEqual(snapshot.fields[0].options,['a'.repeat(32),'b'.repeat(32)]);
 select.options[1].textContent='Different model';assert.deepEqual(await applyProposal(root,snapshot,proposal(snapshot,[{field_id:snapshot.fields[0].id,value:'b'.repeat(32)}])),{applied:false,reason:'stale'});assert.equal(select.value,'a'.repeat(32));assert.deepEqual(select.events,[]);
});

test('select captions enforce per-caption/count/UTF8 limits while plain options remain supported',async()=>{
 const name=new Field('名称','draft'),select=new Field('选择项','a',{tagName:'SELECT',options:[{value:'a',label:'中'.repeat(256)}]}),root=new Root([name,select]);let snapshot=await captureForm(root);assert.equal(snapshot.fields.length,2);assert.equal(snapshot.fields[1].option_labels[0].length,256);
 select.options[0].label+='中';snapshot=await captureForm(root);assert.equal(snapshot.fields.length,1);assert.equal(formCaptureInfo(snapshot).omitted,1);
 select.options=Array.from({length:101},(_,i)=>({value:String(i),label:'Choice '+i}));select.value='0';snapshot=await captureForm(root);assert.equal(snapshot.fields.length,1);assert.equal(formCaptureInfo(snapshot).omitted,1);
 const huge=Array.from({length:2},(_,i)=>new Field('选项 '+i,'0',{tagName:'SELECT',options:Array.from({length:100},(_,n)=>({value:String(n),label:'中'.repeat(256)}))}));snapshot=await captureForm(new Root(huge));assert.equal(snapshot.fields.length,1);assert.equal(formCaptureInfo(snapshot).omitted,1);assert.ok(new TextEncoder().encode(JSON.stringify(snapshot)).byteLength<=100000);
 const plainSnapshot=await captureForm(new Root([new Field('原始选项','a',{tagName:'SELECT',options:[{value:'a'},{value:'b'}]})]));assert.deepEqual(plainSnapshot.fields[0].options,['a','b']);assert.deepEqual(plainSnapshot.fields[0].option_labels,['a','b']);
});
