import {ui} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useEffect,useRef,useState} from 'react';
import {Props,Evidence,useOperation} from './WorkbenchPages';
import {workspaceFromLaunch} from './operation-model.mjs';
import {deliveryNames,deliveryNotices,needsDeliveryAttention,notifyDelivery} from './delivery-model.mjs';
import {MonitorDeliveryControls} from './MonitorDeliveryControls';
import {MonitorAcceptanceControls} from './MonitorAcceptanceControls';
import {deliverySituations} from './task-situation-model.mjs';
import {TaskSituationSummary} from './TaskSituationSummary';
type Data=Record<string,any>;
const SEEN='repocare.delivery.seen.v1',DESKTOP='repocare.delivery.desktop.v1';
export function useDeliveryFeed(request:Props['request'],options?:{preferences?:Data;onNotifications?:(events:Data[])=>void}){
 const [snapshot,setSnapshot]=useState<Data|null>(null),[error,setError]=useState(''),[alerts,setAlerts]=useState<Data[]>([]);
 const busy=useRef(false);
 async function refresh(){
  if(busy.current)return;busy.current=true;
  try{
   const value=await request('/api/delivery');
   if(value.schema_version!==1||value.read_only!==true||!Array.isArray(value.items))throw Error(ui("交付待办回执无效，原记录保留。"));
   let stored=null;try{stored=JSON.parse(sessionStorage.getItem(SEEN)||'null');}catch{}
   const next=deliveryNotices(value,stored);
   try{sessionStorage.setItem(SEEN,JSON.stringify(next.state));}catch{}
   if(next.alerts.length){setAlerts(next.alerts);if(options?.preferences)options.onNotifications?.(next.alerts);else try{if(sessionStorage.getItem(DESKTOP)==='true')notifyDelivery(next.alerts,typeof Notification==='undefined'?null:Notification);}catch{}}
   setSnapshot(value);setError('');
  }catch(e){setError((e as Error).message);}finally{busy.current=false;}
 }
 useEffect(()=>{refresh();const timer=setInterval(refresh,15000);return()=>clearInterval(timer);},[]);
 return {snapshot,error,alerts,refresh,dismiss:()=>setAlerts([])};
}
export function DeliveryInbox({p}:{p:Props}){
 const [filter,setFilter]=useState('attention'),[query,setQuery]=useState(''),[message,setMessage]=useState(''),
  [opening,setOpening]=useState<Data|null>(null),[history,setHistory]=useState<Data|null>(null);
 const op=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh),opened=useRef('');
 useEffect(()=>{
  if(!opening||!op.output?.launch_url)return;
  const target=workspaceFromLaunch(p.hub,op.output.launch_url,typeof location==='undefined'?'':location.origin);
  if(target&&opened.current!==opening.id){opened.current=opening.id;p.reviewDelivery?.({...opening,origin:{...opening.origin,workspace_id:target.id}});}
 },[op.output,p.hub,opening]);
 async function enableDesktop(){
  try{if(typeof Notification==='undefined'){setMessage(ui("浏览器不支持系统通知；页内提醒仍可查看。"));return;}
   const result=await Notification.requestPermission();
   if(result==='granted')sessionStorage.setItem(DESKTOP,'true');
   setMessage(result==='granted'?ui("已启用本页面的交付通知；首次载入不弹历史，页面关闭后不继续通知。"):ui("系统通知未允许；页内提醒仍保留。"));
  }catch{setMessage(ui("通知未能启用；请查看本页交付待办。"));}
 }
 async function readHistory(row:Data){try{setHistory(await p.request(`/api/monitors/${row.origin.monitor_id}/events/${row.origin.event_id}/history`));}catch(e){setMessage((e as Error).message);}}
 const data=p.delivery,items=(data?.items||[]).filter((r:Data)=>(filter!=='attention'||needsDeliveryAttention(r))
  &&`${r.title} ${r.origin.repository} ${r.origin.issue_number||''}`.toLowerCase().includes(query.toLowerCase()));
 return <section className="native-card delivery-inbox" aria-label={ui("统一交付待办")}><header><h2>{ui("待审候选与交付待办")}</h2><AsyncButton onClick={()=>p.refreshDelivery?.()}>{ui("刷新交付待办")}</AsyncButton></header>
  <p>{ui("汇总原Issue与提交监控的本地记录。候选通过不等于已交付；进入原入口后重新核验来源与审批，不会在这里自动发布或调用模型。")}</p>
  {p.notificationPreferences?<><button type="button" onClick={()=>p.onOpenNotifications?.()}>{ui("通知设置")}</button><p className="caption">{ui("交付提醒使用通用通知偏好，桌面权限仅在设置中申请。")}</p></>:<><AsyncButton onClick={enableDesktop}>{ui("允许本页面交付通知")}</AsyncButton><p className="caption">{ui("首次载入只登记历史；后续变化在页内提醒。系统通知须另行允许，仅在页面运行时发送。")}</p></>}
  {message&&<p role="status">{message}</p>}{op.status}{p.deliveryError&&<p role="alert">{p.deliveryError}</p>}
  {data&&!data.complete&&<p role="status">{ui("当前结果不完整：")}{data.errors?.length||0}{ui("项来源读取受阻")}{data.truncated?ui("，已达到展示上限"):''}{ui("；未显示的部分不代表没有任务。")}</p>}
  <div className="button-row"><button aria-pressed={filter==='attention'} onClick={()=>setFilter('attention')}>{ui("待关注")}</button><button aria-pressed={filter==='all'} onClick={()=>setFilter('all')}>{ui("全部原记录")}</button></div>
  <label>{ui("筛选交付待办")}<input value={query} onChange={e=>setQuery(e.target.value)} placeholder={ui("仓库、Issue编号或任务标题")}/></label>
  {items.map((row:Data)=><article className="delivery-inbox-row" key={row.id}><h3>{row.title}</h3>
   <p><strong>{(deliveryNames as Data)[row.status]||row.status}</strong> · {row.origin.kind==='issue'?`Issue #${row.origin.issue_number}`:ui("提交监控")} · {row.origin.repository.split(/[\\/]/).at(-1)}</p>
   <p className="caption">{ui("原运行 ")}{row.origin.run_id}{ui("；当前来源尚未重新核验。")}{row.source_changed_observed?ui(" 已观察到来源变化，请先核对。"):''}</p>
   {deliverySituations(row).map((item:Data)=><TaskSituationSummary key={item.stage} value={item} label={ui("交付阶段：")+item.stage}/>)}
   {row.evidence&&<><p>{ui("当时检查：")}{row.evidence.checks_passed?ui("命令通过"):ui("未取得完整通过记录")}；{row.evidence.effective_test_count_known?`${ui("有效测试 ")}${row.evidence.tests_run}${ui(" 项")}`:ui("有效测试数量未知")}{ui("。原提交 ")}{row.evidence.base_sha.slice(0,12)}。</p><Evidence value={row.evidence} title={ui("原报告摘要、检查与Diff")}/></>}
   <p>{ui("原额度 ")}{row.budget.request_budget}{ui("，已用 ")}{row.budget.requests_used}{ui("，未知 ")}{row.budget.requests_reserved_unknown}{ui("，余 ")}{row.budget.requests_remaining}{ui("；不同原账分别显示。")}</p>
   {Object.keys(row.delivery||{}).length>0&&<Evidence value={row.delivery} title={ui("已登记发布、PR与验收阶段（未重新访问GitHub）")}/>}
   {row.origin.workspace_id?<button onClick={()=>p.reviewDelivery?.(row)}>{ui("在原任务中审阅")}{row.origin.kind==='issue'?ui("与准备PR"):ui("与准备本地发布")}</button>
    :row.origin.kind==='monitor'?<AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>{setOpening(row);op.send('monitor_open',{monitor_id:row.origin.monitor_id,event_id:row.origin.event_id});}}>{ui("核验并进入原监控任务")}</AsyncButton>
    :<AsyncButton onClick={p.connect}>{ui("重新连接原仓库后审阅")}</AsyncButton>}
   {row.origin.kind==='monitor'&&<AsyncButton onClick={()=>readHistory(row)}>{ui("只读查看原监控历史")}</AsyncButton>}
   {row.origin.kind==='monitor'&&<MonitorDeliveryControls p={p} row={row}/>}
   {row.origin.kind==='monitor'&&<MonitorAcceptanceControls p={p} row={row}/>}
  </article>)}
  {!items.length&&<p className="muted">{data?ui("当前筛选没有已展示的交付待办。"):ui("正在读取原交付记录。")}</p>}
  {history&&<Evidence value={history} title={ui("原监控历史，只读")}/>}
 </section>;
}
