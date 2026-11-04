import {ui} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useEffect,useState} from 'react';
import {Evidence,Props,useOperation} from './WorkbenchPages';
import {latestResult} from './operation-model.mjs';
import {AcceptanceWatchControls} from './AcceptanceWatchControls';
type Data=Record<string,any>;

export function MonitorAcceptanceControls({p,row}:{p:Props;row:Data}){
 const origin=row.origin,selection={monitor_id:origin.monitor_id,event_id:origin.event_id,run_id:origin.run_id};
 const op=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh),[confirmed,setConfirmed]=useState(false);
 const proposal=latestResult(p.hub,'monitor_delivery_acceptance_preview',(r:Data)=>r.core?.binding?.origin_kind==='monitor'
  &&r.core.binding.monitor_id===selection.monitor_id&&r.core.binding.event_id===selection.event_id&&r.run_id===selection.run_id);
 const current=row.delivery?.acceptance,used=current?.id===proposal?.id&&current?.status!=='prepared';
 useEffect(()=>setConfirmed(false),[row.id,proposal?.id]);
 useEffect(()=>{if(op.output){setConfirmed(false);p.refreshDelivery?.();}},[op.output]);
 const send=(action:string,extra:Data={})=>op.send(action,{...selection,...extra});
 return <section className="inline-notice" aria-label={ui("监控交付版本验收")}><h4>{ui("核验 PR 合并后的实际版本")}</h4>
  <p>{ui("只读取 GitHub 的合并提交、默认分支和准确 CI 运行；不合并、不重跑 CI、不执行本地测试、不关闭 Issue、不调用模型。")}</p>
  <AsyncButton feedback={op.feedback} disabled={op.busy||!row.delivery?.pr||row.delivery.pr.status==='prepared'} onClick={()=>send('monitor_delivery_acceptance_preview')}>{ui("预览已合并版本与 CI 验收范围")}</AsyncButton>
  {op.status}{op.output&&<Evidence value={op.output} title={ui("本次交付验收原回执")}/>}
  {proposal&&!used&&<><p>{ui("验收版本 ")}{proposal.core.default_head_sha}；PR {proposal.core.pr.url}{ui("。工作流：")}{proposal.core.acceptance_policy.workflow_ids.join('、')}。</p>
   <p>{ui("覆盖此提交已观察到的全部工作流，并要求原失败工作流（若有）出现；不代表尚未触发的其他检查或全仓库正确。")}</p>
   <Evidence value={proposal.core} title={ui("准确版本、合并关系与工作流范围")}/>
   <label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("确认验收这个版本与列出的 CI 范围")}</label>
   <AsyncButton feedback={op.feedback} disabled={op.busy||!confirmed} onClick={()=>{setConfirmed(false);send('monitor_delivery_acceptance_verify',{acceptance_id:proposal.id,revision:proposal.challenge,approval_sha256:proposal.approval_sha256});}}>{ui("确认核验交付版本")}</AsyncButton></>}
  {current&&<p>{ui("最近一次验收：")}{current.status==='delivery-verified'?ui("指定版本与 CI 已核验"):current.status==='not-verified'?ui("未通过本次验收"):current.status}。{current.receipt?.default_head_sha||''} {current.error||''}{ui(" 历史回执不代表未来版本持续通过。")}</p>}
 <AcceptanceWatchControls p={p} row={row}/></section>;
}
