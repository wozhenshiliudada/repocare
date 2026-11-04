import {ui} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useEffect,useState} from 'react';
import {Badge,Evidence,Props,useOperation} from './WorkbenchPages';
import {taskSituation,originalRecoveryActions} from './task-situation-model.mjs';
import {TaskSituationSummary} from './TaskSituationSummary';
type Data=Record<string,any>;
export function RunOperations(p:Pick<Props,'record'|'state'|'request'|'refresh'>&{run:Data}){
 const op=useOperation(p.record,p.state,p.request,p.refresh),run=p.run,[confirmed,setConfirmed]=useState(false),[approval,setApproval]=useState(false);
 useEffect(()=>{setConfirmed(false);setApproval(false);},[run.revision,op.output]);
 const cards=(p.state?.recovery_cards||[]).filter((c:Data)=>c.kind==='run'&&c.id===run.run_id),recovery=cards.length===1?cards[0]:null;
 const selection={kind:'run',id:run.run_id,record:run,repository:p.record?.repository||''};
 const situation=taskSituation(p.state,selection),actions=originalRecoveryActions(p.state,selection);
 const args={run_id:run.run_id,revision:run.revision},review=run.review;
 const inspection=op.output?.repair_inspection,reconciliation=op.output?.reconciliation;
 const bound=p.record?.repository&&p.state?.repository===p.record.repository&&cards.length<=1
  &&(p.state?.runs||[]).filter((r:Data)=>r.run_id===run.run_id).length===1;
 const safeRun=bound&&run.status!=='evidence_invalid'&&!run.resolution&&run.repair_finished!==false;
 return <section className="native-card"><header><h3>{ui("原运行恢复与候选管理")}</h3></header>{op.status}
  <TaskSituationSummary value={situation} label={ui("当前运行的状况与下一步")}/>
  {recovery&&<><Evidence value={recovery} title={ui("原恢复卡、失败反馈与能力门")}/>{actions.map((a:Data)=><AsyncButton feedback={op.feedback} key={a.action+String(a.mode)} disabled={op.busy} onClick={()=>{if(a.mode!=='auto'||window.confirm(ui("确认使用原剩余额度，在原自主授权范围内继续同一任务？")))op.send(a.action,{run_id:run.run_id,revision:a.revision,...(a.mode?{mode:a.mode}:{})});}}>{{resume_run:a.mode==='auto'?ui("在原授权范围内继续修复"):ui("继续此任务，先生成方案"),recover_feedback:ui("核验并恢复失败反馈"),reconcile_feedback_cleanup:ui("核验并完成清理登记")}[a.action as string]}</AsyncButton>)}</>}
  {bound&&(run.status==='evidence_invalid'||run.resolution)&&<AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>op.send('inspect_interruption',{run_id:run.run_id})}>{ui("核验中断登记")}</AsyncButton>}
  {bound&&run.repair_finished===false&&!run.resolution&&<AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>op.send('inspect_repair',{run_id:run.run_id})}>{ui("检查修复中断条件")}</AsyncButton>}
  {(inspection?.can_abort||reconciliation?.can_close_registration)&&<label className="check-label"><input type="checkbox" checked={approval} onChange={e=>setApproval(e.target.checked)}/>{ui("确认已核对此次中断核验对象、影响与原版本")}</label>}
  {inspection&&<><Evidence value={inspection} title={ui("原修复中断核验")}/>{inspection.can_abort&&<AsyncButton feedback={op.feedback} disabled={op.busy||!approval} onClick={()=>{setApproval(false);op.send('recover_repair',{run_id:run.run_id,revision:inspection.session_revision});}}>{ui("批准安全中止这个修复候选")}</AsyncButton>}</>}
  {reconciliation&&<><Evidence value={reconciliation} title={ui("原中断登记核验")}/>{reconciliation.can_close_registration&&<AsyncButton feedback={op.feedback} disabled={op.busy||!approval} onClick={()=>{setApproval(false);op.send('close_interruption',{run_id:run.run_id,revision:reconciliation.revision});}}>{ui("仅关闭已核验中断登记，保留未知结果")}</AsyncButton>}</>}
  {safeRun&&p.state?.experience_memory?.settings_revision&&<AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>op.send('experience_capture',{run_id:run.run_id})}>{ui("登记此运行为待审阅经历")}</AsyncButton>}
  {safeRun&&run.claimed&&<><div className="button-row">{review&&['prepared','awaiting_approval','rejected'].includes(review.status)&&review.hash_matches===true&&review.diff_hash_matches===true&&<AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>op.send('reject_publish',{...args,fingerprint:review.review_sha256})}>{ui("拒绝这个本地发布提案")}</AsyncButton>}{['preparing','recovering','cleanup_required','failed'].includes(review?.status)&&<AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>op.send('recover',args)}>{ui("核验并中止中断封装")}</AsyncButton>}{review?.status==='aborted'&&<AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>op.send('prepare',{...args,retry_aborted:true})}>{ui("归档旧封装并重新验证")}</AsyncButton>}</div>
  {!['discarded','repair_aborted'].includes(run.status)&&!['preparing','recovering','cleanup_required','failed'].includes(review?.status)&&<><label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("仅清理本次自有候选，保留报告与已发布分支")}</label><AsyncButton feedback={op.feedback} className="danger" disabled={op.busy||!confirmed} onClick={()=>{setConfirmed(false);op.send('cleanup',args);}}>{ui("清理此候选工作区")}</AsyncButton></>}
  </>}
 </section>;
}
