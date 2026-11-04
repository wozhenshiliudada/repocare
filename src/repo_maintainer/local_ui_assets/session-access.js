'use strict';
// Per-tab access only. No API key, approval, request body or model transcript is cached.
window.RepoCareTabSession = (() => {
 const tokenPattern=/^[a-f0-9]{64}$/,viewPattern=/^[a-f0-9]{32}$/;
 function open(kind){
  if(!['workbench','maintenance'].includes(kind))throw new Error('Unsupported session surface');
  const key='repo-care.tab-session.v1.'+kind;
  function read(){
   try{
    const raw=window.sessionStorage.getItem(key);
    if(!raw||raw.length>256)return null;
    const value=JSON.parse(raw);
    if(!value||Array.isArray(value)||Object.keys(value).sort().join(',')!=='token,version,view'
      ||value.version!==1||typeof value.token!=='string'||!tokenPattern.test(value.token)
      ||!(value.view===null||typeof value.view==='string'&&viewPattern.test(value.view)))return null;
    return value;
   }catch{return null;}
  }
  function remove(){try{window.sessionStorage.removeItem(key);}catch{}}
  function write(value){
   try{const raw=JSON.stringify(value);window.sessionStorage.setItem(key,raw);return window.sessionStorage.getItem(key)===raw;}
   catch{return false;}
  }
  const params=new URLSearchParams(window.location.hash.slice(1));
  const supplied=params.getAll('session');
  let record=read(),expired=false;
  if(supplied.length){
   if(supplied.length===1&&tokenPattern.test(supplied[0])){
    record={version:1,token:supplied[0],view:record?.token===supplied[0]?record.view:null};
   }else{record=null;remove();}
  }
  const token=record?.token||'';
  // Remove the fragment only when it can be recovered after refresh.
  // If storage is blocked, retain the original private fragment as the fallback.
  if(token&&write(record)&&supplied.length)window.history.replaceState(null,'','/');
  return {
   token,
   selectedView(){return !expired&&record?.view||null;},
   saveView(view){
    if(expired||!token||!(view===null||typeof view==='string'&&viewPattern.test(view)))return false;
    const current=read();
    if(current&&current.token!==token)return false;
    record={version:1,token,view};return write(record);
   },
   expire(){
    expired=true;
    if(read()?.token===token)remove();
    if(new URLSearchParams(window.location.hash.slice(1)).get('session')===token)
      window.history.replaceState(null,'','/');
   }
  };
 }
 return {open};
})();
