import {ui} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useEffect,useState} from 'react';
import {Evidence,Props,useOperation} from './WorkbenchPages';
import {TestTable} from './EvidenceView';
import {MonitorContinuationControls} from './MonitorContinuationControls';
import {MonitorFailureFollowupControls} from './MonitorFailureFollowupControls';
import {latestResult} from './operation-model.mjs';
type Data=Record<string,any>;

export function MonitorHistoryView({value}:{value:Data}){
 return <section aria-label={ui("监控历史只读证据")}><h4>{ui("历史记录 · 只读")}</h4>
  <p>{ui("对应提交 ")}<code>{value.base_sha}</code>{ui("。这些是当时记录的证据，不代表当前版本通过，也不授予修改权限。")}</p>
  <p>{ui("原共享总额度 ")}{value.budget.request_budget}{ui("；已用 ")}{value.budget.requests_used}{ui("，未知占用 ")}{value.budget.requests_reserved_unknown}{ui("，剩余 ")}{value.budget.requests_remaining}{ui("。查看历史不调用模型。")}</p>
  {(value.errors||[]).map((error:string,i:number)=><p className="error-text" key={i}>{error}</p>)}
  {value.truncated&&<p>{ui("仅展示前20份方案与运行；其余保留在原记录中，不能把本页当完整历史。")}</p>}
  {(value.plans||[]).map((plan:Data)=><details key={plan.id}><summary>{ui("原方案 ")}{plan.id.slice(0,12)} · {plan.status}</summary>
   <p>{plan.plan?.diagnosis}</p><p>{plan.plan?.risk}</p><pre>{plan.diff}</pre><Evidence value={plan} title={ui("原方案版本与字节回执")}/></details>)}
  {(value.runs||[]).map((run:Data)=><details key={run.run_id}><summary>{ui("原运行 ")}{run.run_id.slice(0,12)} · {run.status}</summary>
   <TestTable tests={run.baseline_tests||[]} title={ui("原运行修改前检查")}/><TestTable tests={run.candidate_tests||[]} title={ui("原候选检查")}/>
   <pre>{run.diff}</pre><Evidence value={run} title={ui("原运行与恢复记录")}/></details>)}
  <Evidence value={value.preparation} title={ui("原修改前调查与基线记录")}/>
  <Evidence value={value.recent_shared_attempts||[]} title={ui("原仓库共享请求账（最近20条，非仅此事件）")}/>
  <Evidence value={value.continuations||[]} title={ui("本事件后继与原结果关联记录")}/>
  {!value.plans?.length&&!value.runs?.length&&<p>{ui("尚无可核验的方案或运行；原事件与中断记录仍保留。")}</p>}
 </section>;
}

export function MonitorHistory({p,event}:{p:Props;event:Data}){
 const [value,setValue]=useState<Data|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
 const op=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh);
 const recovery=latestResult(p.hub,'monitor_recovery_preview',(r:Data)=>r.monitor_id===event.monitor_id&&r.event_id===event.id);
 useEffect(()=>{setValue(null);setError('');},[event.monitor_id,event.id]);
 async function read(){
  if(loading)return;setLoading(true);setError('');
  try{setValue(await p.request('/api/monitors/'+encodeURIComponent(event.monitor_id)+'/events/'+encodeURIComponent(event.id)+'/history'));}
  catch(e){setError((e as Error).message);setValue(null);}finally{setLoading(false);}
 }
 return <section aria-label={ui("历史与恢复入口")} className="inline-notice">
  <p>{ui("提交或规则变化后仍可查看旧证据。重新调查和继续原任务各自核验，不能用旧审批覆盖新版本。")}</p>
  <div className="button-row"><AsyncButton disabled={loading} onClick={read}>{loading?ui("正在读取原历史…"):ui("只读查看历史方案、Diff与测试")}</AsyncButton>
   <AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>op.send('monitor_recovery_preview',{monitor_id:event.monitor_id,event_id:event.id})}>{ui("检查接下来能做什么")}</AsyncButton></div>
  {error&&<p className="error-text" role="alert">{error}</p>}{op.status}
  {value&&<MonitorHistoryView value={value}/>}
  <MonitorContinuationControls p={p} event={event}/>
  <MonitorFailureFollowupControls p={p} event={event}/>
  {recovery&&<div><p>{recovery.message}</p>
   {recovery.can_open_original&&<AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>op.send('monitor_open',{monitor_id:event.monitor_id,event_id:event.id})}>{ui("核验并进入原任务（需认领与新审批）")}</AsyncButton>}
   {recovery.can_check_latest&&<AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>op.send('monitor_check',{monitor_id:event.monitor_id})}>{ui("按现有规则检查最新提交")}</AsyncButton>}
   {recovery.status==='paused'&&<p>{ui("请使用上方“恢复监控”，然后重新检查接续条件。")}</p>}
   <Evidence value={recovery} title={ui("接续条件核验回执")}/></div>}
 </section>;
}
