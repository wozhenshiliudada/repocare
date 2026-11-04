import {ui,uiLabels} from './ui-language.mjs';
export const pages=uiLabels([['overview','工作概览','overview'],['tasks','维护任务','branch'],['issues','Issue 工作流','issues'],
  ['events','监控与待办','actions'],['memory','记忆与知识','memory'],['delivery','交付与验收','pulls'],['configuration','维护设置','settings'],['service','服务管理','service']]);
export const names=uiLabels({investigated:'调查完成',neutral:'无需操作',decision_recorded:'审批回执已登记',duplicate_no_replay:'已登记，读取原记录',ready:'可接手',needs_triage:'等待分诊',triage_only:'仅调查',untrusted_label:'准入待核对',
  awaiting_approval:'等待审批','awaiting-review':'候选待验收',candidate_ready:'候选已验证',running:'执行中',claimed:'已认领',related_no_dispatch:'已关联 · 不重复修复',
  linked:'已关联',link_pending:'关联待完成',
  paused:'已暂停',interrupted:'上次中断',stale:'版本已变化',failed_no_replay:'未完成 · 不重放',delivered:'已交付',
  published:'已发布本地分支',integrated:'已发布本地分支',prepared:'等待批准',finished:'已结束',failed:'操作失败',
  rolled_back:'失败已回滚',discarded:'候选已清理',rejected:'已拒绝',consumed:'方案已处理',blocked:'需要核验',
  verified:'已核验',invalid:'已失效',pending:'待审阅',accepted:'已接受参考',approved:'已批准',
  push_intent:'发布意图已登记',push_unknown:'发布结果未知',create_intent:'PR 意图已登记',creation_unknown:'PR 结果未知',
  close_intent:'关闭意图已登记',close_unknown:'关闭结果未知','closed-verified':'交付已核验','closed-review-required':'已关闭 · 需复核',
  'remote-created-review-required':'远端已创建 · 需复核','remote-closed-review':'远端已关闭 · 需复核',
  healthy:'所选检查通过',ci_attention:'CI 需要关注',checking:'正在检查',diagnosing:'正在调查',open:'跟进中',deferred:'暂缓',closed_by_user:'已关闭跟进'});
export const statusLabel=value=>names[value]||value||ui("尚无记录");
export function statusTone(value='') {
  if(/unknown|interrupted|stale|blocked|review-required|attention/.test(value))return 'warning';
  if(/failed|rejected|rolled_back|invalid/.test(value))return 'danger';
  if(['delivered','closed-verified','healthy','verified','accepted','candidate_ready'].includes(value))return 'success';
  if(['running','claimed','checking','diagnosing'].includes(value))return 'info';
  return 'neutral';
}
export function summarize(state) {
  const manual=(state?.plans||[]).filter(p=>p.status==='awaiting_approval'),issues=(state?.issue_workbench?.approvals||[]).filter(p=>p.status==='pending');
  return {approvals:manual.length+issues.length,approvalPage:issues.length?'issues':'tasks',executions:state?.issue_workbench?.executions||[],
    knowledge:(state?.knowledge_memory?.entries||[]).length,experiences:(state?.experience_memory?.entries||[]).length,
    used:state?.requests_used??null,remaining:state?.requests_remaining??null,unknown:state?.requests_reserved_unknown??null};
}
export function monitorEvents(monitors,repository) {
  return monitors.filter(m=>!repository||m.spec?.path===repository||m.spec?.repository===repository||m.repository===repository)
    .flatMap(m=>(m.events||[]).map(e=>({...e,monitor_id:m.id,repository:m.spec?.path||m.spec?.repository||m.repository,
      key:m.id+':'+e.id,status:e.display_status||e.status}))).reverse();
}
export function completedOperation(jobs,id) {
  const job=jobs?.find(j=>j.id===id);
  return job&&job.status!=='running'?job:null;
}
