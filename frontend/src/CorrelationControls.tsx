import {ui} from './ui-language.mjs';
import React,{useEffect,useRef,useState} from 'react';
import {Evidence,Props,useOperation} from './WorkbenchPages';
import {monitorsForWorkspace,sameBinding} from './operation-model.mjs';
import {ownerNavigation} from './correlation-navigation.mjs';
import {statusLabel} from './workbench-model.mjs';

type Data=Record<string,any>;
type Source={descriptor:Data;label:string;value:string;original:Data};
type ConfirmAttempt={request_id:string;preview_id:string;workspace_id:string;repository:string;binding:string;previewKey:string};
const key=(value:any)=>JSON.stringify(value);
const textValue=(value:any)=>typeof value==='string'?value.trim():'';
const linkSources=(link:Data):Data[]=>{
 const value=link.sources||link.origins||link.observations||link.core?.observations||[link.left,link.right];
 return Array.isArray(value)?value.filter((source:any)=>source&&typeof source==='object'):[];
};
// Readable names come from the original records' optional metadata. A hash is
// never promoted into a guessed Issue number or a human owner name.
const sourceLabel=(source:Data)=>{
 const label=textValue(source?.label);if(label)return label;
 return source?.kind==='issue'?ui("Issue 来源（详见证据）"):source?.kind==='monitor'?ui("监控事件来源（详见证据）"):ui("原维护来源（详见证据）");
};
const ownerLabel=(link:Data)=>{
 const label=textValue(link.owner_source?.label);if(label)return label;
 if(typeof link.owner==='string'&&link.owner.trim())return link.owner.trim();
 if(link.owner_source||link.owner!=null||link.owner_reference!=null)return ui("原任务负责人（详见证据）");
 return ui("尚未认领");
};
const ownerReference=(link:Data)=>{
 const fields=['origin_id','execution_id','run_id','monitor_id','event_id'];
 if(link.owner_source&&typeof link.owner_source==='object'){
  for(const field of fields){const value=textValue(link.owner_source[field]);if(value)return value;}
 }
 if(link.owner&&typeof link.owner==='object'){
  for(const field of [...fields,'id']){const value=textValue(link.owner[field]);if(value)return value;}
 }
 return typeof link.owner==='string'?'':textValue(link.owner_reference);
};

export function CorrelationControls({p}:{p:Props}){
 const connected=Boolean(p.record?.id&&p.record?.repository?.trim());
 const issues=p.state?.issue_workbench,sources:Source[]=[];
 if(connected){
  for(const env of issues?.environments||[]){
   const investigation=(issues?.investigations||[]).find((row:Data)=>row.id===env.id);
   if(!env.id||!investigation||env.executed||investigation.executed||
    (issues?.executions||[]).some((row:Data)=>row.investigation_id===env.id))continue;
   const descriptor={kind:'issue',workspace_id:p.record!.id,investigation_id:env.id},snapshot=investigation.snapshot;
   sources.push({descriptor,value:key(descriptor),original:{env,investigation},
    label:`Issue #${snapshot?.issue?.number??'—'} · ${snapshot?.issue?.title||ui("原调查目标")} · HEAD ${(snapshot?.head_sha||ui("未知")).slice(0,12)}`});
  }
  for(const monitor of monitorsForWorkspace(p.hub,p.record,typeof location==='undefined'?'':location.origin)){
   for(const event of monitor.events||[]){
    if(!monitor.id||!event.id)continue;
    const descriptor={kind:'monitor',monitor_id:monitor.id,event_id:event.id};
    sources.push({descriptor,value:key(descriptor),original:{monitor,event},
     label:`${ui("监控 ")}${monitor.id.slice(0,12)} / ${event.id.slice(0,12)} · ${event.request_text||event.message||ui("原事件目标")} · HEAD ${(event.sha||ui("未知")).slice(0,12)}`});
   }
  }
 }
 const [left,setLeft]=useState(''),[right,setRight]=useState(''),[consent,setConsent]=useState('');
 const [requested,setRequested]=useState<Data|null>(null);
 // The exact confirmation this component submitted, not any later receipt.
 const [attempt,setAttempt]=useState<ConfirmAttempt|null>(null);
 const sending=useRef(false),submitted=useRef(new Set<string>()),current=useRef<Data>({}),
  ownerCurrent=useRef<{key:string;items:Data[];callbacks:Data}>({key:'',items:[],callbacks:{}});
 const a=sources.find(s=>s.value===left),b=sources.find(s=>s.value===right);
 const pairValid=Boolean(connected&&a&&b&&left!==right);
 // Pin the original records, rules, environments, executions, budgets and runtime.
 // hub.correlation stays here: even our own successful link changes this scope and
 // must end the old preview consent. Jobs stay separate below.
 const {jobs:_jobs,...state}=p.state||{};
 const binding=key([p.record,state,a?.original,b?.original,p.hub?.runtime,p.hub?.active,p.hub?.correlation]);
 const requestedJob=requested?.binding===binding?(p.hub?.jobs||[]).find((row:Data)=>row.id===requested.id&&
  row.action==='maintenance_link_preview'&&row.status==='finished'):undefined;
 const result=requestedJob?.result,preview=result?.preview;
 const matches=Boolean(pairValid&&preview&&typeof preview.id==='string'&&/^[a-f0-9]{64}$/.test(preview.id)&&typeof result.revision==='string'&&result.revision&&
  sameBinding(preview.left,a?.descriptor)&&sameBinding(preview.right,b?.descriptor)&&
  sameBinding(preview.left,requested?.left)&&sameBinding(preview.right,requested?.right));
 const previewKey=key([binding,requestedJob?.id,result]);
 const links:Data[]=Array.isArray(p.hub?.correlation?.links)?p.hub.correlation.links:[];
 // Owner navigation is derived only from the exact rows/registrations read for
 // this render. The saved key lets a click from a previous render (e.g. after a
 // repository switch) do nothing instead of reusing old rows or consent.
 const ownerKey=key([p.record?.id,p.record?.repository,links,p.rows,
  (Array.isArray(p.hub?.workspaces)?p.hub.workspaces:[]).map((w:Data)=>[w?.id,w?.repository,w?.repository_id]),
  (Array.isArray(p.hub?.monitors)?p.hub.monitors:[]).map((m:Data)=>[m?.id,m?.repository,m?.spec?.path,m?.spec?.repository,
   (Array.isArray(m?.events)?m.events:[]).map((e:Data)=>[e?.id,e?.launch_url])])]);
 const ownerOrigin=typeof location==='undefined'?'':location.origin;
 const ownerNavigations:Data[]=links.map((link:Data)=>ownerNavigation({record:p.record,hub:p.hub,rows:p.rows,
  link,origin:ownerOrigin,callbacks:{selectTask:p.selectTask,selectWorkspace:p.selectWorkspace,navigate:p.navigate}}));
 const ownerSnapshot={key:ownerKey,items:ownerNavigations,
  callbacks:{selectTask:p.selectTask,selectWorkspace:p.selectWorkspace,navigate:p.navigate}};
 ownerCurrent.current=ownerSnapshot;
 useEffect(()=>{
  // Restore the committed mount snapshot if React replays effect setup/cleanup.
  ownerCurrent.current=ownerSnapshot;
  return()=>{ownerCurrent.current={key:'',items:[],callbacks:{}};};
 },[ownerKey,p.selectTask,p.selectWorkspace,p.navigate]);
 const confirmJob=attempt?(p.hub?.jobs||[]).find((row:Data)=>row.id===attempt.request_id&&
  row.action==='maintenance_link_confirm'):undefined;
 const attemptPreviewId=attempt?.preview_id;
 // A linked preview id is not enough: the original confirm request must have
 // finished as linked here, in this workspace/repository, and the reader must
 // still see that exact linked row. A 202/running job or a foreign receipt is
 // shown as pending/unknown, never as this association's success.
 const confirmed=Boolean(attempt&&attemptPreviewId&&p.record?.id===attempt.workspace_id&&
  p.record?.repository===attempt.repository&&confirmJob?.status==='finished'&&
  confirmJob?.result?.status==='linked'&&confirmJob?.result?.id===attemptPreviewId&&
  links.some((link:Data)=>link?.id===attemptPreviewId&&link.status==='linked'));
 const confirmPending=Boolean(attempt&&(!confirmJob||confirmJob.status==='running'));
 const stale=Boolean(requested&&requested.binding!==binding);
 const op=useOperation({api_prefix:'/api/'},p.hub,async(path,token,body)=>{
  const requestId=body?.request_id,previewId=body?.preview?.id;
  if(path==='/api/actions/maintenance_link_preview'&&typeof requestId==='string'){
   setRequested({id:requestId,binding,left:a!.descriptor,right:b!.descriptor});
  }
  if(path==='/api/actions/maintenance_link_confirm'&&typeof requestId==='string'&&typeof previewId==='string'){
   setAttempt({request_id:requestId,preview_id:previewId,workspace_id:p.record?.id||'',
    repository:p.record?.repository||'',binding,previewKey});
  }
  return p.request(path,token,body);
 },p.refresh);
 const busy=op.busy||Boolean(p.state?.active||p.state?.runtime?.code_changed);
 const comparable=matches&&preview.comparison?.comparable===true;
 const allowed=Boolean(comparable&&!busy&&consent===previewKey&&!submitted.current.has(previewKey));
 current.current={binding,previewKey,allowed,pairValid,busy};
 useEffect(()=>{setConsent('');},[binding,left,right,previewKey]);
 async function loadPreview(){
  if(sending.current||busy||current.current.busy||!pairValid||current.current.binding!==binding||!current.current.pairValid)return;
  sending.current=true;setConsent('');setRequested(null);setAttempt(null);
  try{await op.send('maintenance_link_preview',{left:a!.descriptor,right:b!.descriptor});}finally{sending.current=false;}
 }
 async function confirm(){
  if(sending.current||submitted.current.has(previewKey)||!allowed||!current.current.allowed||current.current.previewKey!==previewKey)return;
  sending.current=true;submitted.current.add(previewKey);setConsent('');
  try{await op.send('maintenance_link_confirm',{preview,revision:result.revision,confirmed:true});}finally{sending.current=false;}
 }
 // Local selection only: no request, model, repair, monitor_open or new task.
 // A handler saved from an earlier render is accepted only while the exact
 // record/link/rows/registration snapshot is still current.
 function openOwner(index:number,capturedKey:string){
  const latest=ownerCurrent.current;
  if(latest.key!==capturedKey)return;
  const nav=latest.items[index],action=nav?.action;
  if(!nav?.available||!action)return;
  if(action.kind==='task'&&typeof latest.callbacks.selectTask==='function')latest.callbacks.selectTask(action.id);
  else if(action.kind==='workspace'&&typeof latest.callbacks.selectWorkspace==='function')
   latest.callbacks.selectWorkspace(action.id);
  else if(action.kind==='events'&&typeof latest.callbacks.navigate==='function')latest.callbacks.navigate('events');
 }
 return <section className="native-card" aria-label={ui("显式关联原维护来源")}><header><h2>{ui("关联同一故障的原来源")}</h2></header>
  <p>{ui("分别选择当前仓库的两个原来源。预览不调用模型，也不代表同一故障已经得到证明。")}</p>
  <p>{ui("关联只共用派发负责人；各自权限和原账保持，不合账、不继承批准。已有两份派发不能事后合并。")}</p>
  {!connected&&<p>{ui("先选择有明确本地仓库的工作区，再预览关联。")}</p>}
  <label>{ui("第一个原来源")}<select data-form-assist-draft="true" aria-label={ui("第一个原来源")} value={a?left:''} disabled={!connected||busy}
   onChange={e=>{setLeft(e.target.value);setConsent('');setRequested(null);setAttempt(null);}}><option value="">{ui("选择原来源")}</option>
   {sources.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}</select></label>
  <label>{ui("第二个原来源")}<select data-form-assist-draft="true" aria-label={ui("第二个原来源")} value={b?right:''} disabled={!connected||busy}
   onChange={e=>{setRight(e.target.value);setConsent('');setRequested(null);setAttempt(null);}}><option value="">{ui("选择原来源")}</option>
   {sources.map(s=><option key={s.value} value={s.value} disabled={s.value===left}>{s.label}</option>)}</select></label>
  {a&&<p>{ui("当前调查目标：")}{a.label}</p>}{b&&<p>{ui("当前调查目标：")}{b.label}</p>}
  <button disabled={busy||!pairValid} onClick={loadPreview}>{ui("预览来源关联")}</button>
  {confirmed&&<p className="inline-notice" role="status">{ui("本次关联已记录，原预览已结束。")}</p>}
  {stale&&!confirmed&&!confirmPending&&<p role="status">{ui("原来源、规则、额度或运行状态已变化，请重新预览；原同意已失效。")}</p>}
  {matches&&<><p>{comparable?ui("两份来源在当前范围内可比较；仍需你核对是否为同一故障。"):ui("两份来源当前不可比较，不能确认关联。")}</p>
   {(preview.comparison?.differences||[]).map((difference:string,index:number)=><p className="error-text" key={index}>{difference}</p>)}
   {(preview.origins||[]).map((origin:Data,index:number)=><article key={origin.origin_id||index}><h4>{origin.label||ui("原来源")}</h4>
    <p>HEAD {(origin.head_sha||ui("未知")).slice(0,12)}</p><Evidence value={origin.request} title={ui("原调查目标与请求")}/></article>)}
   <Evidence value={preview.budgets} title={ui("各来源原额度（分别保持）")}/>
   <Evidence value={preview} title={ui("关联预览与完整来源证据")}/>
   <label className="check-label"><input type="checkbox" checked={consent===previewKey} disabled={busy||!comparable}
    onChange={e=>{if(current.current.previewKey===previewKey&&!current.current.busy&&comparable)setConsent(e.target.checked?previewKey:'');}}/>{ui("我确认这两个来源指向同一故障；各自权限和原账保持")}</label>
   <button className="primary" disabled={!allowed} onClick={confirm}>{ui("确认关联原来源")}</button></>}
  {!(attempt&&confirmJob?.status==='finished')&&op.status}
  {attempt&&confirmJob?.status==='finished'&&!confirmed&&<p role="status">{ui("已收到原确认回执，但当前工作区、仓库或关联记录未能核对；请核对原证据，不要重发。")}</p>}
  <h3>{ui("已记录的来源关联")}</h3>{links.length?links.map((link:Data,index:number)=>{
   const ownerDetail=textValue(link.owner_source?.label)?'':ownerReference(link),owner=ownerNavigations[index];
   return <article key={link.id||index}>
    <p>{ui("状态：")}{statusLabel(link.status)}</p>
    {linkSources(link).length?<ul className="link-sources">{linkSources(link).map((source:Data,sourceIndex:number)=>
     <li key={source.origin_id||sourceIndex}><span>{sourceLabel(source)}</span>
      {!textValue(source.label)&&textValue(source.origin_id)&&<small>{ui(" · 原记录 ")}{source.origin_id}</small>}</li>)}</ul>
     :<p className="muted">{ui("原来源明细尚未读取；请核对原证据。")}</p>}
    <p>{ui("派发负责人：")}{ownerLabel(link)}{ownerDetail&&<small>{ui(" · 原负责人记录 ")}{ownerDetail}</small>}</p>
    {textValue(link.reason)&&<p className="error-text">{link.reason}</p>}
    {owner.showButton&&<button disabled={!owner.available} onClick={()=>openOwner(index,ownerKey)}>{owner.buttonLabel}</button>}
    {owner.reason&&<p className="muted">{owner.reason}</p>}
    {owner.available&&owner.hint&&<p className="muted">{owner.hint}</p>}
    <Evidence value={link} title={ui("关联、负责人和原始证据")}/></article>;})
  :<p className="muted">{ui("尚无已读取的关联记录。")}</p>}
  <p className="muted">{ui("当前接线仅覆盖显式关联且检查、环境和权限精确可比的修复入口；只读提议和独立手工任务等入口尚未全部覆盖。")}</p>
 </section>;
}
