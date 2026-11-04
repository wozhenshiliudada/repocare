import {ui} from './ui-language.mjs';
import React from 'react';
import {CiComparableBaseline} from './CiComparableBaseline';
type Data=Record<string,any>;

export function CiMonitorDiagnosis({event}:{event:Data}){
 const value=event.ci_diagnosis;
 if(!value)return null;
 const ready=value.status==='investigated',evidence=value.ci_evidence||{},proposal=event.ci_preparation;
 return <section aria-label={ui("监控 CI 自动调查")} className="inline-notice">
  <h3>{ui("CI 失败调查")}</h3>
  {event.ci_diagnosis_historical&&<p>{ui("历史调查：当前 CI 已变化，不能沿用为本次授权。")}</p>}
  {evidence.synthetic&&<p>{ui("合成上游样例，不是真实 GitHub CI 结果。")}</p>}
  <p>{ready?ui("只读调查已完成，尚未授权修复。"):value.status==='started'?ui("正在读取固定版本的 CI 证据。"):ui("调查未完成，记录已保留，不会自动重放。")}</p>
  {ready&&<>
   <p>Run {value.selection?.run_id} / Attempt {value.selection?.attempt}{ui(" · 源码 ")}<code>{value.base_sha}</code></p>
   <p>{ui("证据状态：")}{evidence.evidence_status}{ui("；日志：")}{evidence.log_status}{ui("。本调查未运行测试，尚未证明根因。")}</p>
   <CiComparableBaseline value={value.comparable_baseline} selectedJobId={evidence.selected_job_id}
    packet={{repository:evidence.repository,run:evidence.run,synthetic:evidence.synthetic,jobs:evidence.selected_job_id?[{id:evidence.selected_job_id}]:[]}}/>
   <p>{value.authority?.next_step==='reproduce_failure'?ui("下一步：在已授权环境中复现失败。"):ui("下一步：检查环境或补充 CI 失败证据。")}</p>
   {(value.report?.file_candidates||[]).length>0&&<><p>{ui("调查命中的文件（不是自动扩大的修改范围）：")}</p><ul>{value.report.file_candidates.map((file:Data)=><li key={file.path}><code>{file.path}</code></li>)}</ul></>}
   {evidence.failed_steps?.length>0&&<ul>{evidence.failed_steps.map((step:Data)=><li key={step.number}>{step.name}</li>)}</ul>}
   {proposal&&<section aria-label={ui("环境与检查建议")}>
    <h4>{ui("自动选择的环境与检查")}</h4>
    <p>{proposal.status==='scope_review'?ui("已生成建议，新增检查需要授权范围审阅。"):proposal.status==='proposed'?ui("已生成建议，尚未执行或授权修复。"):proposal.status==='environment_blocked'?ui("环境方案受阻，不能视为代码修复失败。"):proposal.status==='evidence_review'?ui("缺少失败步骤证据，先调查 CI 环境。"):ui("建议未完成，保留记录，不会自动重放。")}</p>
    {event.ci_diagnosis_historical&&<p>{ui("此建议也只作历史参考，当前 CI 不能沿用。")}</p>}
    {proposal.environment&&<p>{ui("环境：")}{proposal.environment.runtime}{ui("。尚未安装依赖或运行这些建议检查。")}</p>}
    {(proposal.environment?.checks||[]).map((check:Data)=><div key={check.id}><p>{check.title} · {(check.sources||[]).join('、')}</p><details><summary>{ui("查看检查详情")}</summary><code>{JSON.stringify(check.argv)}</code></details></div>)}
    {(proposal.blockers||[]).map((reason:string,index:number)=><p key={index}>{reason}</p>)}
    {proposal.proposed_policy?.allowed_paths?.length>0&&<><p>{ui("建议修改的实现文件：")}</p><ul>{proposal.proposed_policy.allowed_paths.map((path:string)=><li key={path}><code>{path}</code></li>)}</ul></>}
    {proposal.preserved_registered_tests&&<p>{ui("原授权回归检查继续保留；建议不替换原检查，也不增加模型额度。")}</p>}
   </section>}
  </>}
 </section>;
}
