import React,{useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AcceptanceWatchControls} from '../src/AcceptanceWatchControls';
type Data=Record<string,any>;
function Fixture(){
 const origin={kind:'monitor',monitor_id:'a'.repeat(16),event_id:'b'.repeat(16),run_id:'c'.repeat(32)};
 const initial={id:'synthetic-candidate',origin,delivery:{pr:{receipt:{url:'https://github.com/example/project/pull/9'}}}};
 const store=useRef<Data>({jobs:[],calls:[],row:initial}),[hub,setHub]=useState<Data>({jobs:[],active:null}),[row,setRow]=useState<Data>(initial);
 function sync(){setHub({jobs:[...store.current.jobs],active:null});setRow({...store.current.row});}
 async function request(path:string,_token:string|undefined,body:Data={}){
  const action=path.split('/').at(-1)!;store.current.calls.push(action);let result:Data;
  if(action==='monitor_delivery_watch_preview')result={core:{binding:{...origin,origin_kind:'monitor',base:'main'},
   pr:{url:'https://github.com/example/project/pull/9'},policy:{workflow_ids:[1001]},interval_seconds:body.interval_seconds,
   expires_at:body.expires_at},revision:'f'.repeat(64)};
  else if(action==='monitor_delivery_watch_configure'){
   if(body.confirmed!==true)throw Error('Explicit confirmation required');
   result={id:'d'.repeat(32),run_id:origin.run_id,enabled:true,status:'waiting_ci',core:body.preview,
    observations:[],challenge:'e'.repeat(64),last_checked:new Date().toISOString(),last_observation:null};
   store.current.row={...store.current.row,delivery:{...store.current.row.delivery,acceptance_watch:result}};
  }else{
   const previous=store.current.row.delivery.acceptance_watch;
   result={...previous,enabled:action.endsWith('_resume'),status:action.endsWith('_resume')?'waiting_ci':'paused'};
   store.current.row={...store.current.row,delivery:{...store.current.row.delivery,acceptance_watch:result}};
  }
  const job={id:body.request_id,action,status:'finished',result};store.current.jobs.push(job);sync();return job;
 }
 const props:any={hub,request,refresh:async()=>{},refreshDelivery:()=>{},rows:[],navigate:()=>{}};
 return <main style={{maxWidth:1000,margin:'24px auto',padding:24}}><h1>持续只读验收 · 组件交互测试</h1>
  <p>明确使用合成远端回执；这是原产品组件，不是已连接真实GitHub。真实模型0请求，真实GitHub写入0。</p>
  <AcceptanceWatchControls p={props} row={row}/><output aria-label="合成操作记录">操作数：{store.current.calls.length}；{store.current.calls.join(' → ')}</output></main>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
