import {usageText} from './request-usage.mjs';
import {ui} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useEffect,useRef,useState} from 'react';
import {Evidence,Props,useOperation} from './WorkbenchPages';
import {latestResult} from './operation-model.mjs';
import {failureEligible,failureExpiry,failurePreviewMatches,failureRemaining,failureLabel,failureCanResume} from './failure-followup-model.mjs';

type Data=Record<string,any>;
function localExpiry(value:string|undefined){const date=new Date(value||'');return Number.isFinite(date.getTime())?new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16):'';}

export function MonitorFailureFollowupControls({p,event}:{p:Props;event:Data}){
 const monitor=(p.hub.monitors||[]).find((m:Data)=>m.id===event.monitor_id),grant=monitor?.model_authorization;
 const rows=(monitor?.failure_followups||[]).filter((r:Data)=>r.core?.event_id===event.id);
 const [maximum,setMaximum]=useState(1),[expiry,setExpiry]=useState(()=>localExpiry(grant?.core?.expires_at)),
  [consent,setConsent]=useState(''),[pausing,setPausing]=useState(''),[message,setMessage]=useState('');
 const pausePending=useRef(false),op=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh);
 const proposal=latestResult(p.hub,'monitor_failure_preview',(r:Data)=>r.preview?.monitor_id===event.monitor_id&&r.preview.event_id===event.id);
 const iso=failureExpiry(expiry,grant?.core?.expires_at),matching=failurePreviewMatches(proposal,monitor,event,maximum,iso);
 const consentKey=JSON.stringify([proposal?.revision,maximum,iso,grant?.id,grant?.core_sha256,grant?.active,
  monitor?.root_rules?.sha256,monitor?.requests_used,monitor?.requests_reserved_unknown,monitor?.requests_remaining,
  event.root_execution,rows.map((r:Data)=>r.revision)]);
 useEffect(()=>setConsent(''),[consentKey]);
 async function pause(row:Data){
  if(pausePending.current)return;pausePending.current=true;setPausing(row.id);setConsent('');setMessage('');
  try{await p.request('/api/actions/monitor_failure_pause',undefined,{request_id:crypto.randomUUID().replaceAll('-',''),
   monitor_id:event.monitor_id,grant_id:row.id,revision:row.revision});
   setMessage(ui("已收到暂停回执；请核对刷新后的状态。在途操作沿原停止机制收尾，已用额度保留。"));await p.refresh();}
  catch(e){setMessage((e as Error).message);}finally{pausePending.current=false;setPausing('');}
 }
 if(!failureEligible(event)&&!rows.length)return null;
 return <section aria-label={ui("同一提交新 CI 失败后继")} className="inline-notice"><h4>{ui("为同一提交的新 CI 失败建立后继任务")}</h4>
  <p>{ui("只处理原修复结束后，同一提交、同一工作流、同一 run 的更高 attempt 失败。不同 run 的先后顺序尚不能可靠核验，遇到时停止待核对。只读观察的许可不包含模型调用；这里需要单独确认，可能收费。人工模式仍需新方案审批。")}</p>
  <p>{ui("共用原模型授权、维护规则和原剩余额度，不增加预算、不重跑 CI、不推送或合并。旧任务和报告保留。已有后继方案或候选尚未交付时停止；次数只是上限，不保证用满。")}</p>
  <p>{ui("失败步骤和报错文本重复时停止；比较时忽略空白差异，不判断错误含义。重启会暂停后继和原模型许可。原模型许可无效时不能恢复，不会将旧许可迁移到新许可。")}</p>
  {monitor&&<p>{usageText(monitor)}{ui("。原模型授权到期 ")}{grant?.core?.expires_at||ui("尚无有效授权")}。</p>}
  {failureEligible(event)&&<><label>{ui("最多建立的后继任务数")}<input type="number" min={1} max={3} value={maximum} onChange={e=>{setMaximum(Number(e.target.value));setConsent('');}}/></label>
   <label>{ui("后继授权到期时间（本地时间，不晚于原模型授权）")}<input type="datetime-local" value={expiry} max={localExpiry(grant?.core?.expires_at)} onChange={e=>{setExpiry(e.target.value);setConsent('');}}/></label>
   <AsyncButton feedback={op.feedback} disabled={op.busy||!grant?.active||!monitor?.enabled||!iso||!Number.isInteger(maximum)||maximum<1||maximum>3||rows.length>0} onClick={()=>{setConsent('');op.send('monitor_failure_preview',{
    monitor_id:event.monitor_id,event_id:event.id,max_successors:maximum,expires_at:iso});}}>{ui("预览新失败后继授权")}</AsyncButton>
   {matching&&!rows.length&&<><p>{ui("原提交 ")}<code>{proposal.preview.parent.head_sha}</code>{ui("；最多 ")}{proposal.preview.max_successors}{ui(" 个后继；到期 ")}{proposal.preview.expires_at}{ui("。每次继续前重新核对原授权和额度。")}</p>
    <Evidence value={proposal.preview} title={ui("原任务、模型授权、规则与共享预算")}/>
    <label className="check-label"><input type="checkbox" checked={consent===consentKey} onChange={e=>setConsent(e.target.checked?consentKey:'')}/>{ui("允许此范围内的新 CI 失败调用原模型（可能收费），共用原剩余额度")}</label>
    <AsyncButton feedback={op.feedback} disabled={op.busy||consent!==consentKey} onClick={()=>{setConsent('');op.send('monitor_failure_configure',{preview:proposal.preview,revision:proposal.revision,confirmed:true});}}>{ui("确认启用新失败后继")}</AsyncButton></>}
  </>}{op.status}{message&&<p role="status">{message}</p>}
  {rows.map((row:Data)=><div key={row.id}><p role="status">{failureLabel(row)}{ui("；剩余后继次数 ")}{failureRemaining(row)} / {row.core.max_successors}{ui("；到期 ")}{row.core.expires_at}。</p>
   {row.error&&<p className="error-text">{ui("停止或阻塞原因：")}{row.error}</p>}
   <div className="button-row"><AsyncButton disabled={Boolean(pausing)||!row.enabled} onClick={()=>pause(row)}>{ui("暂停新失败后继")}</AsyncButton>
    <AsyncButton feedback={op.feedback} disabled={op.busy||!failureCanResume(row,grant)} onClick={()=>{setConsent('');op.send('monitor_failure_resume',{monitor_id:event.monitor_id,grant_id:row.id,revision:row.revision});}}>{ui("恢复原后继授权")}</AsyncButton></div>
   <Evidence value={row} title={ui("后继任务与停止记录")}/></div>)}
 </section>;
}
