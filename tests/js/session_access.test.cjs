const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../src/repo_maintainer/local_ui_assets/session-access.js'),'utf8');
const A='a'.repeat(64),B='b'.repeat(64),VIEW='c'.repeat(32);
const key=kind=>'repo-care.tab-session.v1.'+kind;
test('restoring tab access preserves ordinary view fragments',()=>{
 const store=storage();boot(store,'#session='+A);const restored=boot(store,'#view=issues');
 assert.equal(restored.access.token,A);assert.equal(restored.location.hash,'#view=issues');
});
function storage(){const values=new Map();return {values,getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};}
function boot(store,hash='',kind='workbench'){
 const location={hash},history={replaceState(){location.hash='';}};
 const window={location,history,sessionStorage:store};
 vm.runInNewContext(source,{window,URLSearchParams});
 return {access:window.RepoCareTabSession.open(kind),location};
}
test('private fragment is retained per tab and survives repeated reloads without storing actions',()=>{
 const store=storage(),first=boot(store,'#session='+A);
 assert.equal(first.access.token,A);assert.equal(first.location.hash,'');
 for(let i=0;i<5;i++)assert.equal(boot(store).access.token,A);
 const saved=JSON.parse(store.getItem(key('workbench')));
 assert.deepEqual(Object.keys(saved).sort(),['token','version','view']);
 assert.equal(boot(storage()).access.token,'');
});
test('workbench and standalone access do not inherit each other',()=>{
 const store=storage();boot(store,'#session='+A,'workbench');
 assert.equal(boot(store,'','maintenance').access.token,'');
 boot(store,'#session='+B,'maintenance');
 assert.equal(boot(store).access.token,A);assert.equal(boot(store,'','maintenance').access.token,B);
});
test('only a bounded workspace reference survives refresh; returning to picker clears it',()=>{
 const store=storage(),first=boot(store,'#session='+A).access;
 assert.equal(first.saveView(VIEW),true);
 assert.equal(boot(store).access.selectedView(),VIEW);
 assert.equal(first.saveView('../outside'),false);
 assert.equal(first.saveView({approve:true}),false);
 assert.equal(first.saveView(null),true);assert.equal(boot(store).access.selectedView(),null);
});
test('an explicit new server capability replaces old access and old selected view',()=>{
 const store=storage(),old=boot(store,'#session='+A).access;old.saveView(VIEW);
 const current=boot(store,'#session='+B).access;
 assert.equal(current.token,B);assert.equal(current.selectedView(),null);
 old.expire();assert.equal(boot(store).access.token,B);
 assert.equal(old.saveView(VIEW),false);assert.equal(boot(store).access.selectedView(),null);
});
test('401 expiry removes only matching access and cannot revive it with view changes',()=>{
 const store=storage();store.setItem('unrelated','preserve');
 const access=boot(store,'#session='+A).access;
 access.saveView(VIEW);access.expire();
 assert.equal(boot(store).access.token,'');assert.equal(access.saveView(VIEW),false);
 assert.equal(store.getItem('unrelated'),'preserve');
});
test('malformed or duplicate private fragments cannot fall back to previously valid access',()=>{
 for(const hash of ['#session=wrong','#session='+A+'&session='+B]){
  const store=storage();boot(store,'#session='+A);
  assert.equal(boot(store,hash).access.token,'');assert.equal(boot(store).access.token,'');
 }
});
test('corrupt, oversized, unknown or action-bearing records grant no access',()=>{
 for(const raw of ['{','x'.repeat(257),JSON.stringify({version:2,token:A,view:null}),
  JSON.stringify({version:1,token:A,view:null,approve:true}),
  JSON.stringify({version:1,token:A,view:'../other'}),JSON.stringify({version:1,token:1,view:null})]){
  const store=storage();store.setItem(key('workbench'),raw);
  assert.equal(boot(store).access.token,'');
 }
});
test('blocked storage leaves a valid private fragment usable on refresh',()=>{
 const store={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');},removeItem(){throw Error('blocked');}};
 const first=boot(store,'#session='+A);
 assert.equal(first.access.token,A);assert.equal(first.location.hash,'#session='+A);
 assert.equal(boot(store,first.location.hash).access.token,A);
 first.access.expire();assert.equal(first.location.hash,'');
});
test('a silent failed storage write must not erase the only refresh credential',()=>{
 const store={getItem:()=>null,setItem(){},removeItem(){}};
 const page=boot(store,'#session='+A);
 assert.equal(page.access.token,A);assert.equal(page.location.hash,'#session='+A);
});
