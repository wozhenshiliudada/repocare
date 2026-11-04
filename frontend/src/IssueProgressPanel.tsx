import {ui,uiLabels} from './ui-language.mjs';
import React,{useEffect,useState} from 'react';
import {AsyncButton,type ButtonFeedback} from './AsyncButton';
type Data=Record<string,any>;
const phases:Record<string,string>=uiLabels({accepted:'已接手',investigated:'调查结果',blocked:'信息不足或阻塞',
 candidate_passed:'候选通过，尚未交付',awaiting_review:'待审阅，尚未交付',delivered:'已交付'});
const states:Record<string,string>=uiLabels({prepared:'待批准发送',permission_denied:'权限不足，未发送',
 post_intent:'写入结果待核对',post_unknown:'网络结果未知，先核对',posted:'评论已确认',
 'posted-review-required':'评论已创建，来源变化待核查'});
export function IssueProgressPanel({execution,sourceKind='execution',comments,output,disabled,send,feedback}:{execution:Data;
 sourceKind?:'execution'|'investigation';comments:Data[];
 output:Data|null;disabled:boolean;send:(action:string,payload:Data)=>Promise<void>;feedback?:ButtonFeedback}){
 const [stage,setStage]=useState('accepted'),[consent,setConsent]=useState(false);
 // Execution rows also carry investigation_id as provenance. Only the original
 // controller branch selecting this panel decides which authority it operates.
 const investigation=sourceKind==='investigation';
 const preview=output?.action===(investigation?'issue_progress_investigation_preview':'issue_progress_preview')&&
  output.result?.core?.run_id===execution.run_id&&(investigation||output.execution_id===execution.id)?output.result:null;
 useEffect(()=>{setConsent(false);},[execution.id,sourceKind,preview?.id,preview?.approval_sha256,stage]);
 const rows=comments.filter(r=>r.core?.run_id===execution.run_id);
 const request=(action:string,payload:Data={})=>send(investigation?action.replace('issue_progress_','issue_progress_investigation_'):action,
  {...(investigation?{investigation_id:execution.id}:{execution_id:execution.id}),...payload});
 return <section className="issue-section" aria-label={ui("Issue 进度评论")}>
  <h3>{ui("回写 GitHub Issue")}</h3><p>{ui("先预览实际正文，再单独批准发送。候选通过、待审阅和已交付分别记录。")}</p>
  <label>{ui("回写内容")}<select aria-label={ui("Issue 评论阶段")} value={stage} disabled={disabled} onChange={e=>{setStage(e.target.value);setConsent(false);}}>
   {Object.entries(phases).filter(([id])=>!investigation||['accepted','investigated','blocked'].includes(id)).map(([id,label])=><option key={id} value={id}>{label}</option>)}
  </select></label>
  <AsyncButton feedback={feedback} disabled={disabled} onClick={()=>request('issue_progress_preview',{stage})}>{ui("预览此阶段的评论")}</AsyncButton>
  <p className="caption">{ui("只发送原记录能证明的阶段。运行繁忙时等待当前操作结束；不会自动发送或重试评论。")}</p>
  {preview&&<div className="issue-proposal"><p>{ui("目标：")}{preview.core.repository} #{preview.core.issue_number}{ui(" · 账号：")}{preview.github_actor?.login}</p>
   <p>{ui("运行：")}<code>{preview.core.run_id}</code></p><pre>{preview.core.body}</pre>
   <p>{states[preview.status]||preview.status}</p>
   {preview.status==='prepared'&&<><label className="check-label"><input type="checkbox" checked={consent} disabled={disabled}
    onChange={e=>setConsent(e.target.checked)}/>{ui("我批准使用上述账号，向这个 Issue 发送这份具体正文")}</label>
   <AsyncButton feedback={feedback} className="primary" disabled={disabled||!consent||preview.core.stage!==stage}
    onClick={()=>{setConsent(false);return request('issue_progress_send',{progress_id:preview.id,
      approval_sha256:preview.approval_sha256,expected_actor_id:preview.github_actor.id});}}>{ui("批准发送这条评论")}</AsyncButton></>}
  </div>}
  {rows.map(row=><div className="issue-proposal" key={row.id}><strong>{phases[row.core.stage]}</strong>
   <p>{states[row.status]||row.status}</p>{row.receipt?.url&&<a href={row.receipt.url} target="_blank" rel="noreferrer">{ui("查看 GitHub 评论")}</a>}
   {row.continuity_error&&<p role="alert">{ui("评论已创建，但任务来源发生变化；请核查，不要再次发送。")}</p>}
   <details><summary>{ui("已固定的评论正文")}</summary><pre>{row.core.body}</pre></details>
   {['post_intent','post_unknown','posted','posted-review-required'].includes(row.status)&&
    <AsyncButton feedback={feedback} disabled={disabled} onClick={()=>request('issue_progress_reconcile',{progress_id:row.id})}>{ui("只读核对这条评论，不重发")}</AsyncButton>}
  </div>)}
 </section>;
}
