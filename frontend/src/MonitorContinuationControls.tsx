import {ui} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useEffect,useState} from 'react';
import {Evidence,Props,useOperation} from './WorkbenchPages';
import {latestResult,workspaceFromLaunch} from './operation-model.mjs';
type Data=Record<string,any>;

export function MonitorContinuationControls({p,event}:{p:Props;event:Data}){
 const [mode,setMode]=useState('approval'),[confirmed,setConfirmed]=useState(false);
 const op=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh);
 const inspection=latestResult(p.hub,'monitor_continuation_inspect',(r:Data)=>
  r.preview?.monitor_id===event.monitor_id&&r.preview.event_id===event.id&&r.preview.mode===mode);
 const monitor=(p.hub?.monitors||[]).find((m:Data)=>m.id===event.monitor_id);
 const preview=inspection?.preview;
 const current=preview&&preview.binding.base_sha===event.sha&&preview.binding.preparation_sha256===event.root_preparation?.result_sha256
  &&preview.head?.id===event.continuation?.id&&preview.budget.requests_used===monitor?.requests_used
  &&preview.budget.requests_reserved_unknown===monitor?.requests_reserved_unknown
  &&(monitor?.requests_remaining===undefined||preview.budget.requests_remaining===monitor.requests_remaining);
 const workspace=workspaceFromLaunch(p.hub,op.output?.launch_url,typeof location==='undefined'?'':location.origin);
 useEffect(()=>{setConfirmed(false);},[mode,inspection?.revision,event.continuation?.id]);
 if(!event.root_execution)return null;
 const send=(action:string)=>{setConfirmed(false);op.send(action,{...inspection,confirmed:true});};
 return <section aria-label={ui("原任务安全接续")}><h4>{ui("处理这次失败或中断")}</h4>
  <p>{ui("先核对原进程、候选现场、测试与消费。已完成的结果只关联；需要新一轮时沿原任务和剩余额度处理。")}</p>{op.status}
  <label>{ui("接续方式")}<select value={mode} onChange={e=>setMode(e.target.value)}><option value="approval">{ui("生成新方案，再人工审批")}</option>
   {monitor?.root_rules?.settings.mode==='auto'&&p.hub?.allow_auto&&<option value="auto">{ui("原自主范围内准备候选")}</option>}</select></label>
  <AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>{setConfirmed(false);op.send('monitor_continuation_inspect',{
   monitor_id:event.monitor_id,event_id:event.id,run_id:null,mode});}}>{ui("核查原现场与接续条件")}</AsyncButton>
  {current&&<><p>{preview.message}</p><Evidence value={preview} title={ui("进程、报告、原任务与消费核验")}/>
   {preview.can_continue&&<><p>{ui("可改文件：")}{preview.policy.allowed_paths.join('、')}{ui("；原总额度 ")}{preview.budget.request_budget}{ui("， 已用 ",", used ")}{preview.budget.requests_used}{ui("，未知 ")}{preview.budget.requests_reserved_unknown}{ui("，剩余 ")}{preview.budget.requests_remaining}{ui("。 最多 ",". Up to ")}{preview.max_attempts}{ui(" 轮候选；请求次数不等于费用金额。")}</p>
    <label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("允许本次继续调用已配置模型（可能收费），保留旧运行，沿原任务与额度生成新一轮")}</label>
    <AsyncButton feedback={op.feedback} disabled={op.busy||!confirmed} onClick={()=>send('monitor_continuation_start')}>{ui("确认新一轮接续")}</AsyncButton></>}
   {preview.can_abort&&<><label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("仅安全中止这次自有候选，保留报告、原消费和原源码，不运行模型或测试")}</label>
    <AsyncButton feedback={op.feedback} disabled={op.busy||!confirmed} onClick={()=>send('monitor_continuation_recover')}>{ui("批准中止已核验候选")}</AsyncButton></>}
   {preview.can_reconcile&&<><label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("只关联已完成的原结果，不重新请求模型或测试，不代替审批")}</label>
    <AsyncButton feedback={op.feedback} disabled={op.busy||!confirmed} onClick={()=>send('monitor_continuation_recover')}>{ui("确认关联原结果")}</AsyncButton></>}
  </>}
  {workspace&&<button disabled={!p.selectWorkspace} onClick={()=>p.selectWorkspace?.(workspace.id)}>{ui("查看接续后的原任务")}</button>}
 </section>;
}
