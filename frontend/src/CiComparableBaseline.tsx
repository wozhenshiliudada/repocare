import {ui,uiLabels} from './ui-language.mjs';
import React from 'react';
type Data=Record<string,any>;
const reasons:Record<string,string>=uiLabels({
 missing_reviewed_commands_environment_dependency_receipt:'尚未取得经核验的检查命令、实际环境和依赖回执。',
 current_job_has_no_executed_steps:'当前 Job 没有已执行的检查步骤，不能作为已复现的代码失败。',
 select_one_failed_job_for_comparison:'请先选择一个准确的失败 Job。',
 current_job_is_not_completed_failure:'所选 Job 尚不是已结束的检查失败。',
 original_ci_observation_incomplete:'原 CI 观察不完整，需要先核对缺失的证据。',
 original_ci_receipt_missing:'原始检查回执缺失。',original_ci_receipt_changed:'原始检查回执字节已变化。',
 different_branch:'历史检查来自不同分支。',different_environment:'历史检查的实际环境不同。',
 different_dependencies:'历史检查的依赖不同。',different_workflow:'工作流内容或身份不同。',
 different_checks:'检查命令或范围不同。',different_synthetic:'合成证据不能与真实证据混用。',
 original_ci_receipt_not_approved:'原始检查回执尚未核准。',no_historical_observations:'没有足够的历史检查记录。',
 baseline_evidence_unavailable:'历史比较证据暂不可读取。'});
export function CiComparableBaseline({value,packet,selectedJobId}:{value?:Data;packet:Data;selectedJobId?:number}){
 const current=value?.current,base=value?.baseline;
 const repo=current?.repository;
 const bound=value?.status==='comparable'&&current&&base&&/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo||'')&&
  repo.toLowerCase()===String(packet.repository?.slug||'').toLowerCase()&&current.sha===packet.run?.head_sha&&
  current.run_id===packet.run?.id&&current.attempt===packet.run?.attempt&&current.workflow?.id===packet.run?.workflow_id&&
  (packet.jobs||[]).some((j:Data)=>j.id===current.job_id)&&(!selectedJobId||current.job_id===selectedJobId)&&
  base.repository===current.repository&&base.branch===current.branch&&base.synthetic===current.synthetic&&
  typeof current.branch==='string'&&current.branch.length>0&&typeof current.synthetic==='boolean'&&
  current.synthetic===packet.synthetic&&/^[a-f0-9]{64}$/.test(base.evidence_sha256||'')&&
  /^[a-f0-9]{64}$/.test(current.evidence_sha256||'')&&
  /^[a-f0-9]{40}$/.test(base.sha||'')&&/^[a-f0-9]{40}$/.test(current.sha||'')&&
  /^[a-f0-9]{64}$/.test(current.workflow?.content_sha256||'')&&
  typeof current.workflow?.path==='string'&&current.workflow.path.startsWith('.github/workflows/')&&
  base.workflow?.content_sha256===current.workflow?.content_sha256;
 const url=bound?`https://github.com/${repo}/compare/${base.sha}...${current.sha}`:'';
 return <section aria-label={ui("可比较的历史成功检查")} className="ci-evidence">
  <h3>{ui("可比较的历史成功检查")}</h3><p role="status">{bound?ui("已确认本次证据范围内的可比成功检查"):ui("无法确认可比较基线")}</p>
  {!bound&&<ul>{(value?.rejections?.length?value.rejections:[{reason:value?.status==='comparable'?'selection_mismatch':'missing_reviewed_commands_environment_dependency_receipt'}])
   .map((r:Data,i:number)=><li key={i}>{r.reason==='selection_mismatch'?ui("结果与当前 CI 选择不符，须重新核对。"):reasons[r.reason]||`${ui("证据不足：")}${r.reason}`}</li>)}</ul>}
  {bound&&<>
   {current.synthetic&&<p>{ui("合成证据，仅用于工程验证。")}</p>}
   <table><thead><tr><th>{ui("来源")}</th><th>{ui("历史成功")}</th><th>{ui("当前失败")}</th></tr></thead><tbody>
    {['repository','branch','sha','run_id','attempt','job_id','job_name'].map(k=><tr key={k}><th>{({repository:ui("仓库"),branch:ui("分支"),sha:'SHA',run_id:'Run',attempt:'Attempt',job_id:'Job',job_name:ui("检查")} as Data)[k]}</th><td><code>{base[k]}</code></td><td><code>{current[k]}</code></td></tr>)}
    <tr><th>{ui("工作流")}</th><td>{base.workflow.id} · {base.workflow.path}</td><td>{current.workflow.id} · {current.workflow.path}</td></tr>
   </tbody></table>
   <details><summary>{ui("已比较的检查范围、命令、实际环境与依赖")}</summary><pre>{JSON.stringify(value?.comparison,null,2)}</pre></details>
   <a href={url} target="_blank" rel="noopener noreferrer">{ui("查看这两个准确版本的差异")}</a>
   {(value?.diff?.relevant_files||[]).map((file:Data,i:number)=><details key={i}><summary>{file.filename} · {file.status}</summary>
    {file.patch!=null?<pre>{file.patch}</pre>:<p>{ui("此文件的差异正文不可用或超过显示范围，请核对原站。")}</p>}</details>)}
   <p>{ui("相关差异仅为调查线索，不能仅凭差异断言某次提交是根因。本操作不重跑 CI 或执行历史代码。")}</p>
  </>}
 </section>;
}
