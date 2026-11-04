import {ui} from './ui-language.mjs';
import React from 'react';
import {checkName,checkPresentation,compareCommandReceipts} from './evidence-model.mjs';
type Data=Record<string,any>;
export function TestTable({tests,title}:{tests:Data[];title:string}){
 return <section className="test-report"><h4>{title} <span className="count-pill">{tests.length}</span></h4>{tests.length?<><div className="test-table-scroll"><table><thead><tr><th>{ui("实际检查命令")}</th><th>{ui("退出码")}</th><th>{ui("状态与有效测试")}</th><th>{ui("用时")}</th></tr></thead><tbody>{tests.map((t,i)=>{const outcome=checkPresentation(t);return <tr key={i}><td><code>{checkName(t)}</code>{t.command_kind==='controller_behavior_contract'&&<details><summary>{ui("查看固定案例与原始标记")}</summary><p>{ui("这是控制器执行的验收，不是需要复制运行的终端命令。通过仅覆盖这些固定案例。这里展示已保存的案例摘要，不表示页面重新运行或重新核验了工件。")}</p><pre>{JSON.stringify({command:t.command,receipt:t.behavior_receipt},null,2)}</pre></details>}{(t.stdout||t.stderr)&&<details><summary>{ui("查看输出")}</summary><pre>{t.stdout}{t.stderr}</pre></details>}</td><td>{t.returncode??ui("未知")}</td><td className={outcome.passed?'test-passed':'test-failed'}>{outcome.label}<small>{ui(" · 测试计数 ")}{outcome.count??ui("未知")}</small></td><td>{typeof t.duration_seconds==='number'?t.duration_seconds.toFixed(2)+'s':'—'}</td></tr>;})}</tbody></table></div><p className="muted">{ui("原分类优先；未提供分类时仅提示已保存输出中的线索。退出0不能单独证明有有效测试或业务正确。")}</p></>:<p className="muted">{ui("尚无该阶段检查回执，不视为通过。")}</p>}</section>;
}
export function CheckComparison({baseline,candidate}:{baseline:Data[];candidate:Data[]}){
 const rows=compareCommandReceipts(baseline,candidate),labels:Record<string,string>={repaired:ui("命令由失败转通过"),maintained:ui("命令保持通过"),regressed:ui("命令由通过转失败"),still_failing:ui("命令仍失败"),inconclusive:ui("超时或退出码不完整，无法比较")};
 return <section className="test-report"><h4>{ui("相同检查的退出码对照")}</h4>{rows?<ul>{rows.map((row:Data,i:number)=><li key={i}><code>{row.command?.join(' ')||ui("未提供命令")}</code> · {String(row.before??ui("未知"))} → {String(row.after??ui("未知"))} · {labels[row.transition]}{!row.effective&&ui("；有效测试范围尚未证明")}</li>)}</ul>:<p className="muted">{ui("缺少同数量、同顺序、同命令的两阶段回执，不能作可比结论。")}</p>}<p className="muted">{ui("这是页面按原命令回执展示的对照，不改变原控制器裁决，也不证明语义正确。")}</p></section>;
}
export function DeliveryProposal({result}:{result:Data}){
 const core=result.core||{},proof=core.proof||{},binding=core.binding||proof.binding||{};
 return <div className="proposal-summary"><h4>{ui("本次具体影响")}</h4><dl className="property-list">
  <dt>{ui("操作")}</dt><dd>{{create_remote_branch_only:ui("仅创建指定远端新分支"),create_draft_pr_only:ui("仅创建这份 Draft PR"),close_completed_issue_only:ui("仅关闭已验收的原 Issue")}[core.operation as string]||core.operation||ui("见完整提案")}</dd>
  <dt>{ui("仓库 / Issue")}</dt><dd>{binding.repository||ui("见原绑定")} {binding.issue_number?'#'+binding.issue_number:''}</dd>
  <dt>{ui("来源执行")}</dt><dd><code>{binding.execution_id||result.id||ui("见原记录")}</code></dd>
  {core.remote_ref&&<><dt>{ui("远端分支")}</dt><dd><code>{core.remote_ref}</code></dd></>}
  {binding.commit&&<><dt>{ui("候选提交")}</dt><dd><code>{binding.commit}</code></dd></>}
  {core.payload?.head&&<><dt>{ui("PR 来源 / 目标")}</dt><dd>{core.payload.head} → {core.payload.base}</dd><dt>{ui("PR 标题")}</dt><dd>{core.payload.title}</dd></>}
  {proof.default_head_sha&&<><dt>{ui("已验收默认分支")}</dt><dd><code>{proof.default_head_sha}</code></dd><dt>{ui("合并提交")}</dt><dd><code>{proof.pr?.merge_commit_sha}</code></dd><dt>{ui("工作流证据")}</dt><dd>{proof.workflows?.length??ui("见完整提案")}{ui(" 项")}</dd></>}
  {result.approval_sha256&&<><dt>{ui("审批指纹")}</dt><dd><code>{result.approval_sha256}</code></dd></>}
 </dl>{core.payload?.body&&<details className="evidence"><summary>{ui("完整 PR 正文")}</summary><pre>{core.payload.body}</pre></details>}</div>;
}
export function LocalReview({result}:{result:Data}){const review=result.review||{};return <div className="proposal-summary"><h4>{ui("封装与本地交付提案")}</h4><dl className="property-list"><dt>{ui("目标本地分支")}</dt><dd>{review.target_branch||ui("尚未完成")}</dd><dt>{ui("封装提交")}</dt><dd><code>{review.commit||ui("尚未完成")}</code></dd><dt>{ui("审批指纹")}</dt><dd><code>{result.review_sha256||ui("尚无可批准指纹")}</code></dd></dl><TestTable tests={review.tests||[]} title={ui("封装后独立复测")}/><p className="muted">{ui("此操作只创建指定本地分支，不推送、不合并、不关闭 Issue。")}</p></div>;}
