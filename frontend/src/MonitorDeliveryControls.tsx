import {ui} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useEffect,useState} from 'react';
import {Evidence,Props,useOperation} from './WorkbenchPages';
import {latestResult} from './operation-model.mjs';
type Data=Record<string,any>;

export function MonitorDeliveryControls({p,row}:{p:Props;row:Data}){
 const origin=row.origin,selection={monitor_id:origin.monitor_id,event_id:origin.event_id,run_id:origin.run_id};
 const op=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh);
 const [local,setLocal]=useState(false),[push,setPush]=useState(false),[pr,setPr]=useState(false);
 const match=(r:Data)=>r.core?.binding?.origin_kind==='monitor'&&r.core.binding.monitor_id===selection.monitor_id
  &&r.core.binding.event_id===selection.event_id&&r.core.binding.run_id===selection.run_id;
 const prepared=latestResult(p.hub,'monitor_delivery_prepare_local',(r:Data)=>r.review?.run_id===selection.run_id);
 const pushProposal=latestResult(p.hub,'monitor_delivery_push_preview',match);
 const prProposal=latestResult(p.hub,'monitor_delivery_pr_preview',match);
 const published=Boolean(row.evidence?.local_publication?.integration_commit),pushState=row.delivery?.push?.status,prState=row.delivery?.pr?.status;
 const stale=row.source_changed_observed||row.status==='evidence_unavailable';
 useEffect(()=>{setLocal(false);setPush(false);setPr(false);},[row.id,row.notice_id,prepared?.review_sha256,pushProposal?.challenge,prProposal?.challenge]);
 useEffect(()=>{if(op.output){setLocal(false);setPush(false);setPr(false);p.refreshDelivery?.();}},[op.output]);
 const send=(action:string,extra:Data={})=>op.send(action,{...selection,...extra});
 return <section className="inline-notice" aria-label={ui("监控候选交付")}><h4>{ui("把这个监控候选交付到 GitHub")}</h4>
  <p>{ui("先封装并复测，再确认本地独立分支。推送新分支与创建 Draft PR 各需一次单独批准；不合并、不关闭 Issue、不调用模型。")}</p>
  {op.status}{op.output&&<Evidence value={op.output} title={ui("本次原交付回执")}/>}
  {stale&&<p role="status">{ui("已知来源变化或证据缺失；保留原回执，请先核查。不会继续旧交付批准。")}</p>}
  {!published&&<>
   <AsyncButton feedback={op.feedback} disabled={op.busy||stale||Boolean(prepared)} onClick={()=>send('monitor_delivery_prepare_local')}>{ui("封装并复测监控候选")}</AsyncButton>
   {prepared&&<><Evidence value={prepared} title={ui("原本地封装、实际检查与独立分支提案")}/>
    <label className="check-label"><input type="checkbox" checked={local} onChange={e=>setLocal(e.target.checked)}/>{ui("批准将此准确候选发布为本地独立分支")}</label>
    <AsyncButton feedback={op.feedback} disabled={op.busy||stale||!local} onClick={()=>{setLocal(false);send('monitor_delivery_publish_local',{revision:prepared.review_sha256});}}>{ui("确认发布本地独立分支")}</AsyncButton></>}
  </>}
  {published&&<p>{ui("已记录本地分支 ")}{row.evidence.local_publication.integration_branch}{ui("；当前来源与封装结果会在每次交付前重新核验。")}</p>}
  <AsyncButton feedback={op.feedback} disabled={op.busy||stale||!published||Boolean(pushState&&pushState!=='prepared')} onClick={()=>send('monitor_delivery_push_preview')}>{ui("预览 GitHub 新分支")}</AsyncButton>
  {pushProposal&&(!pushState||pushState==='prepared')&&<>
   <p>{ui("将向 ")}{pushProposal.core.url}{ui(" 新建 ")}{pushProposal.core.remote_ref}{ui("，提交 ")}{pushProposal.core.binding.commit}{ui("。不覆盖现有远端分支。")}</p>
   <Evidence value={pushProposal} title={ui("准确远端新分支提案")}/>
   <label className="check-label"><input type="checkbox" checked={push} onChange={e=>setPush(e.target.checked)}/>{ui("批准推送此准确提交到所示 GitHub 新分支")}</label>
   <AsyncButton feedback={op.feedback} disabled={op.busy||stale||!push} onClick={()=>{setPush(false);send('monitor_delivery_push',{revision:pushProposal.challenge,approval_sha256:pushProposal.approval_sha256});}}>{ui("确认推送 GitHub 新分支")}</AsyncButton></>}
  <AsyncButton feedback={op.feedback} disabled={op.busy||stale||!published||Boolean(prState&&prState!=='prepared')} onClick={()=>send('monitor_delivery_pr_preview')}>{ui("预览监控 Draft PR")}</AsyncButton>
  {prProposal&&(!prState||prState==='prepared')&&<>
   <p>{ui("在 ")}{prProposal.core.binding.repository}{ui(" 创建 Draft PR：")}{prProposal.core.payload.head} → {prProposal.core.payload.base}。</p>
   <Evidence value={prProposal.core.payload} title={ui("将提交的PR标题和正文")}/>
   <label className="check-label"><input type="checkbox" checked={pr} onChange={e=>setPr(e.target.checked)}/>{ui("批准按所示完整内容创建一份 Draft PR")}</label>
   <AsyncButton feedback={op.feedback} disabled={op.busy||stale||!pr} onClick={()=>{setPr(false);send('monitor_delivery_pr',{revision:prProposal.challenge,approval_sha256:prProposal.approval_sha256});}}>{ui("确认创建监控 Draft PR")}</AsyncButton></>}
  {['push_intent','push_unknown'].includes(pushState)&&<AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>send('monitor_delivery_reconcile',{kind:'push'})}>{ui("只读核对远端分支结果")}</AsyncButton>}
  {['create_intent','creation_unknown'].includes(prState)&&<AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>send('monitor_delivery_reconcile',{kind:'pr'})}>{ui("只读核对PR创建结果")}</AsyncButton>}
  {(pushState||prState)&&<p>{ui("原远端分支状态：")}{pushState||ui("尚无记录")}{ui("；PR状态：")}{prState||ui("尚无记录")}{ui("。网络结果未知时只核对，不重复写入。")}</p>}
 </section>;
}
