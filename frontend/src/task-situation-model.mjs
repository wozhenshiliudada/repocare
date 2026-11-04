// Localized presentation and navigation only. This module never creates an
// execution action, approval, revision or permission from a displayed reason.
import {failureNames} from './failure-followup-model.mjs';
import {situationDisplayText} from './situation-preview-language.mjs';
const descriptions={
 diagnosing:['正在原任务中调查并规划','原控制器正在生成修复方案，尚无候选通过或交付结论。','等待对应原操作回执，或进入本事件的原任务查看活动与停止入口。','events'],
 validation_needed:['需要先明确验收标准','未发现可靠的现成检查；当前没有模型修复或测试通过结论。','在本事件填写业务期望，预览原额度后让 Agent 提议案例，再核对输入与预期。','events'],
 validation_proposing:['正在只读提议验收案例','原模型提议尚未结束，此阶段不运行项目或修改源码。','等待原回执，或使用本事件的停止入口；不要重复派发。','events'],
 validation_review:['验收案例等待核对','Agent 已提出输入与预期，尚未接受为本次标准。','在本事件核对案例并接受或拒绝；这不是修改方案审批。','events'],
 validation_accepted:['案例已接受，尚未准备基线','本次标准已固定，接受案例不会自动运行项目或开始修复。','使用本事件的原环境准备入口，按原许可运行修改前检查。','events'],
 validation_preparing:['正在准备原案例基线','原环境准备意图已登记，模型修复尚未启动。','等待本事件的原准备回执，不重复运行基线。','events'],
 validation_interrupted:['案例提议中断，保留原消费','这次只读提议没有完整发布结果，刷新或重启不会自动重发。','使用本事件的只读核对入口；只能取回原已提交、已结算结果。','events'],
 validation_preparation_interrupted:['案例基线准备中断','原准备意图和现场已保留，当前结果不能授权修复。','核对本事件的原环境证据；不要重放基线或清除旧意图。','events'],
 validation_clarification:['业务期望还需明确','现有需求不足以形成可靠的验收案例，不能猜测正确结果。','先明确期望，保留原提议，后续按新意图处理；不改写旧需求或自动重试。','events'],
 validation_rejected:['验收案例已拒绝','这些案例没有成为本次验收标准，不能据此执行修复。','保留拒绝记录，明确后续需求后按新意图处理。','events'],
 model_pending:['模型调用正在结算前占用额度','原请求已登记为进行中，预留占用尚未形成最终消费回执。','查看原活动与调用回执，等待结算；不要重复派发。','tasks'],
 runtime_changed:['运行代码已变化','当前服务尚未加载磁盘上的新代码。','结束当前活动后，保留原状态目录安全重启。','service'],
 unknown:['原模型消费待核对','原请求账仍有无法确认的消费占用，剩余额度继续按原账保留。','查看原调用与消费回执，不能自动重试或重置额度。','configuration'],
 budget_exhausted:['原请求额度暂不可用','当前原账没有可用于新模型请求的余量；已用与未知占用分别保留。','先核对原额度；需要调整时进入原预算预览并另行批准。','configuration'],
 source_invalid:['参考来源已变化','原记忆或结构来源已失效，旧来源不能继续支持新操作。','核对并更新原来源绑定，再检查任务条件。','memory'],
 busy:['当前仓库有操作占用','仓库存在活动操作；这不代表所选任务正在执行。','查看当前活动任务，等待其完成或使用对应原停止入口。','tasks'],
 review_plan:['方案等待审阅','修改方案已记录，尚未取得本方案的执行批准。','核对原方案、范围和检查；历史方案需要先核验认领。','tasks'],
 plan_completed:['方案已有处理结果','此方案已处理，后续情况以它关联的原运行为准。','查看对应运行与实际检查记录。','tasks'],
 cleanup_pending:['失败清理登记待核对','原失败清理过程还需要明确核验。','在下方原恢复入口检查并核对清理登记。','tasks'],
 feedback_pending:['失败反馈等待恢复','原任务已保存失败反馈，恢复条件仍须核验。','在下方原恢复入口核对原失败反馈。','tasks'],
 repair_pending:['修复过程尚未结束','原运行存在未完成的修复过程。','使用原修复中断核验入口，先检查现场。','tasks'],
 candidate_pending:['候选通过，等待审阅交付','原记录已有候选通过的结果；发布与验收尚需分别核验。','核对原检查和差异，再进入原封装或交付审批入口。','delivery'],
 registration_pending:['原生任务登记待核对','此检查点尚未绑定可继续处理的原方案或运行。','查看原调用与任务登记，不能直接开始接续。','tasks'],
 not_bound:['历史记录缺少原生任务绑定','当前记录没有可核验的原生任务接续绑定。','查看原报告；已有发布或恢复仍由各自原入口核验。','tasks'],
 missing:['原生任务检查点未完成','尚无可用于继续此任务的已完成检查点。','查看原调用及中断证据，保留现有记录。','tasks'],
 blocked:['原任务条件需要核验','原控制器没有提供可直接推进的有效条件。','查看原恢复卡与报告，按原核验入口处理。','tasks'],
 candidate_ready:['候选通过，等待审阅交付','原记录已有候选通过的结果；候选通过不等于完成交付。','核对原检查和差异，准备单独的交付审阅。','delivery'],
 'awaiting-review':['候选等待验收','此原任务已有待审候选，交付状态尚需单独核验。','进入交付页核对原候选、分支、PR 与验收记录。','delivery'],
 awaiting_review:['候选等待验收','此原任务已有待审候选，交付状态尚需单独核验。','进入交付页核对原候选、分支、PR 与验收记录。','delivery'],
 awaiting_approval:['方案等待审批','此原任务的修改方案尚待明确批准。','进入对应方案审阅；批准仍绑定原方案和指纹。','tasks'],
 integrated:['本地分支已有发布记录','本地分支发布与远端分支、PR、最终验收分别记录。','进入交付页核对后续阶段，不能据此认定已合并交付。','delivery'],
 published:['本地分支已有发布记录','本地分支发布与远端分支、PR、最终验收分别记录。','进入交付页核对后续阶段，不能据此认定已合并交付。','delivery'],
 delivered:['已有交付记录，待核对验收','当前状态来自原任务记录，最终版本和验收仍需查对应证据。','进入交付页核对准确版本及原验收回执。','delivery'],
 failed_no_replay:['原任务未完成，保留现场','原执行记录为未完成；此状态不会自动重放操作。','查看对应原失败结果，再从原恢复或新方案预览开始。','tasks'],
 blocked_no_replay:['原任务受阻，保留现场','原执行没有完成，系统保留原现场与消费记录。','查看原阻塞证据，不重复派发。','events'],
 interrupted_no_replay:['原任务中断，保留现场','原任务中断结果已保留，不能据此自动重放。','核对原任务及消费回执，再检查可用恢复入口。','events'],
 interrupted:['上次操作中断','原操作未完整结束，当前记录不构成重试许可。','查看原中断证据及恢复核验条件。','tasks'],
 stale:['来源或版本已变化','原记录对应的来源或版本已变化。','重新核对来源与版本，旧审批不能覆盖变化后的任务。','tasks'],
 paused:['原任务已暂停','当前任务处于暂停状态，既有消费与证据保留。','查看原暂停原因与恢复预览，先重新核验条件。','tasks'],
 evidence_invalid:['原运行证据需要核验','原报告或绑定尚未取得有效核验。','使用原中断登记核验入口，保留候选和历史。','tasks'],
 failed:['原操作失败','原记录显示操作失败，具体原因以原报告为准。','核对原失败证据，再检查可用恢复入口。','tasks'],
 running:['原任务记录为执行中','此状态来自所选任务的原执行记录，进度以对应回执为准。','查看对应任务的活动与停止入口。','tasks'],
 started:['原任务已经开始','此状态来自所选任务的原执行记录，是否完成以对应回执为准。','查看对应任务的活动与原结果。','tasks'],
 claimed:['原任务已认领','原记录已登记此任务的接手者，尚不能据此认定执行完成。','查看对应任务的方案与执行回执。','tasks'],
 related_no_dispatch:['已关联，避免重复修复','这份来源保留在原任务中；同一关联故障已有另一条修复链，本来源没有再次调用模型或创建候选。','在监控与待办页查看关联负责人，再核对其原任务、候选和交付结果。','events'],
 finished:['原操作已结束','操作结束与修复交付分别判断；结果以原报告为准。','核对原报告、检查结果与后续交付阶段。','tasks'],
 discarded:['候选已清理','原候选已清理，报告与消费继续保留。','需要继续时先检查原任务的恢复或新方案条件。','tasks'],
 rolled_back:['候选失败并已回滚','原记录保留失败和回滚结果。','核对失败证据，后续新方案仍需原审批流程。','tasks'],
 rejected:['原方案已拒绝','此方案尚未获准执行。','核对拒绝记录，需要修改时使用对应原修订入口。','tasks'],
 ci_attention:['CI 需要关注','所选监控事件记录了需要关注的 CI 状态。','查看这个事件的准确工作流与原调查结果。','events'],
 healthy:['所选监控检查通过','此事件的已选检查通过，尚不能推断整个仓库无缺陷或修复已交付。','查看原事件的检查范围与准确提交。','events'],
 consumed:['原方案已处理','原方案已处理，后续运行与交付需要分别核对。','打开这个事件的原任务与运行报告。','events'],
 environment_blocked:['目标环境阻塞','原环境或检查条件尚未满足，不能认定代码修复已完成。','查看原环境诊断和获准的准备或重试预览。','configuration'],
 evidence_review:['CI 证据等待核对','原调查尚未取得足够的失败步骤证据。','在此事件的原调查入口核对 CI 环境与失败证据。','events'],
 scope_review:['检查范围等待核对','原调查发现当前维护规则没有覆盖的检查，尚不能执行修复。','核对原检查与维护规则，使用原范围调整入口。','events'],
 repair_scope_blocked:['修复范围受阻','原检查已复现失败，但此次调查未形成可执行的修复范围。','核对失败检查和维护规则，后续提交会重新调查。','events'],
 execution_consent_required:['基线检查等待执行确认','原调查已有检查选择，尚未获准执行本仓库代码。','审阅原基线执行范围，再使用原确认入口。','events'],
 reproduced:['已复现失败，等待修复准备','原检查已有失败基线；尚不代表已有修复候选。','查看原调查与维护范围，再进入原修复准备入口。','events'],
 baseline_passes:['当前所选基线检查通过','所选检查没有复现失败，不能据此宣称需求已经满足。','核对原需求与检查覆盖；需要固定行为案例时进入原验收定义流程。','events'],
 push_unknown:['远端发布结果未知','原发布操作的远端结果尚未确认。','在原交付入口只读核对，不重复发布。','delivery'],
 creation_unknown:['PR 创建结果未知','原 PR 创建操作的远端结果尚未确认。','在原交付入口只读核对，不重复创建。','delivery'],
 close_unknown:['关闭结果未知','原关闭操作的远端结果尚未确认。','在原交付入口只读核对，不重复关闭。','delivery'],
};
const quiet=new Set(['plan_completed','discarded','rolled_back','rejected','finished','healthy','consumed']);
function situation(kind,id,status,description){
 const [title,reason,next,page]=description||['原记录仍需核对','尚未取得可清楚说明下一步的原状态。','查看原报告和恢复条件，不根据错误文字推断权限。',kind==='monitor'?'events':'tasks'];
 // Translate only these source-owned descriptions. Record identities, status,
 // navigation and controller actions retain their original values.
 return {kind,id,status,title:situationDisplayText(title),reason:situationDisplayText(reason),
  next:situationDisplayText(next),page,attention:!quiet.has(status)};
}
function matches(items,predicate){return Array.isArray(items)?items.filter(value=>value&&predicate(value)):[];}
function exact(items,predicate){const found=matches(items,predicate);return found.length===1?found[0]:null;}
function repositoryStatus(state,repository){
 if(repository===undefined)return null;
 if(typeof repository!=='string'||!repository||typeof state?.repository!=='string'||!state.repository)return 'record_unknown';
 return repository===state.repository?null:'stale';
}
// Return only the original controller's supported actions. Display text, old
// approval flags, unknown action names and duplicate identities grant nothing.
export function originalRecoveryActions(state,selection){
 if(repositoryStatus(state,selection?.repository))return [];
 const records=selection?.kind==='run'?state?.runs:selection?.kind==='plan'?state?.plans:[];
 if(matches(records,r=>(selection.kind==='run'?r.run_id:r.id)===selection.id).length>1)return [];
 const card=exact(state?.recovery_cards,row=>row.kind===selection?.kind&&row.id===selection?.id);
 if(!card||state?.active||state?.runtime?.code_changed)return [];
 const actions=matches(card.actions,a=>typeof a.revision==='string'&&a.revision.length>0&&(
  card.status==='available'&&a.action==='resume_run'&&['approval','auto'].includes(a.mode)
  ||card.status==='feedback_pending'&&a.action==='recover_feedback'&&a.mode===undefined
  ||card.status==='cleanup_pending'&&a.action==='reconcile_feedback_cleanup'&&a.mode===undefined));
 if(new Set(actions.map(a=>a.action+':'+(a.mode||''))).size!==actions.length)return [];
 return actions;
}
function presentRecord(kind,id,record){
 if(!record)return situation(kind,id,'record_unknown');
 if(record.repair_finished===false)return situation(kind,id,'repair_pending',descriptions.repair_pending);
 return situation(kind,id,record.status,descriptions[record.status]);
}
export function taskSituation(state,selection){
 const kind=selection?.kind,id=selection?.id;
 if(!['run','plan','task','issue','monitor'].includes(kind)||typeof id!=='string'||!id)
  return situation(kind||'task',id||'', 'record_unknown');
 const repositoryProblem=repositoryStatus(state,selection?.repository);
 if(repositoryProblem)return situation(kind,id,repositoryProblem,repositoryProblem==='stale'?
  ['仓库记录尚未对应当前选择','当前状态来自另一仓库，不能用于所选任务。','刷新并核对当前仓库与原任务身份。','tasks']:undefined);
 if(kind==='issue'){
  if(matches(state?.issue_workbench?.executions,row=>row.id===id).length!==1)return situation(kind,id,'record_unknown');
  const value=exact(state?.issue_workbench?.executions,row=>row.id===id
   &&(selection.number===undefined||row.issue_number===selection.number));
  const result=presentRecord(kind,id,value);result.page=result.page==='tasks'?'issues':result.page;return result;
 }
 if(kind==='monitor'){
  const monitor=exact(state?.monitors,row=>row.id===selection.monitor_id);
  const event=exact(monitor?.events,row=>row.id===id);
  if(!event)return situation(kind,id,'record_unknown');
  const prep=event.root_preparation;
  const dispatchBlocked=event.model_dispatch?.status==='blocked_no_replay';
  const status=event.root_preparation_historical?'stale':
   event.root_execution?event.display_status||(dispatchBlocked&&event.root_execution.status)||event.status:
   prep?.status==='reproduced'&&prep.scope_compilation?.status==='needs_investigation'?'repair_scope_blocked':
   dispatchBlocked?'blocked_no_replay':prep?.status||event.display_status||event.status;
  let projected=status;
  if(status!=='stale'&&!event.root_execution&&event.validation){
   const validation=event.validation,resume=validation.resume;
   projected=resume?.status==='started'?'validation_preparing':
    resume?.status==='interrupted_no_replay'?'validation_preparation_interrupted':
    resume?.status==='finished'?status:
    ({started:'validation_proposing',awaiting_validation_review:'validation_review',accepted:'validation_accepted',
      interrupted_no_replay:'validation_interrupted',clarification_needed:'validation_clarification',rejected:'validation_rejected'}[validation.status]||status);
  }
  const result=situation(kind,id,projected,descriptions[projected]);result.monitor_id=monitor.id;result.page='events';return result;
 }
 const records=matches(kind==='run'?state?.runs:kind==='plan'?state?.plans:[],
  row=>(kind==='run'?row.run_id:row.id)===id);
 if(records.length>1)return situation(kind,id,'record_unknown');
 const cards=matches(state?.recovery_cards,row=>row.kind===kind&&row.id===id);
 if(cards.length>1)return situation(kind,id,'record_unknown');
 const card=cards[0];
 if(card){
  if(card.status==='available'){
   const modes=new Set(originalRecoveryActions(state,selection).filter(a=>a.action==='resume_run').map(a=>a.mode));
   const next=modes.size===2?'下方可选择先生成方案并另行审批，或在原自主授权范围内继续修复；两种入口分别核验。':
    modes.has('auto')?'使用下方“在原授权范围内继续修复”，确认原额度与范围；自主模式不等于取得人工审批。':
    '使用下方“继续此任务，先生成方案”；执行修改仍需本轮新审批。';
   return situation(kind,id,modes.size?'available':'blocked',modes.size?
    ['同一任务有可用接续入口','原恢复卡提供了当前模式的接续条件，继续可能调用模型并使用原剩余额度。',next,'tasks']:descriptions.blocked);
  }
  return situation(kind,id,card.status,descriptions[card.status]);
 }
 const stored=records[0];
 const supplied=selection.record&&((kind==='run'?selection.record.run_id:selection.record.id)===id)?selection.record:null;
 return presentRecord(kind,id,stored||supplied);
}

const stageDescriptions={
 local:{prepared:['本地封装等待批准','封装已有原审阅记录，尚未发布本地分支。','核对封装差异、复测与指纹，再使用原本地发布审批。'],
  published:['本地分支已有发布记录','原报告登记了本地分支与提交，远端 PR 和默认分支验收仍分别判断。','核对准确本地提交，再进入原远端交付预览。']},
 push:{prepared:['远端分支等待批准','已有远端分支发布提案，尚无成功发布回执。','审阅原目标分支与提交，再按原入口单独批准。'],
  push_intent:['远端发布意图已登记','原发布操作已登记，尚无最终远端结果。','核对原进程与远端回执，不重复发布。'],
  push_unknown:descriptions.push_unknown,
  'remote-ref-verified':['远端分支已有核验回执','原远端引用已核验，尚不能据此认定 PR 已合并或交付通过。','核对原远端版本，再进入原 PR 提案。'],
  'remote-ref-review-required':['远端分支需要复核','原发布回执还需要核查，不能视为成功交付。','从原交付入口只读核对远端引用与提交。']},
 pr:{prepared:['PR 提案等待批准','原 PR 内容已有提案，尚无已创建回执。','审阅原仓库、目标分支和内容，再使用原创建审批。'],
  create_intent:['PR 创建意图已登记','原创建操作尚无最终回执。','只读核对原远端结果，不重复创建 PR。'],
  creation_unknown:descriptions.creation_unknown,
  'awaiting-review':['远端 PR 等待审阅','已有原 PR 创建回执；尚无默认分支合并与最新 CI 验收证明。','审阅原 PR；合并后从原验收入口核对准确默认版本与最新检查。'],
  'remote-created-review-required':['远端 PR 已创建，需要复核','原创建回执需要复核，不能当作交付完成。','在原入口只读核对 PR 的分支、提交和当前状态。'],
  'remote-closed-review':['远端 PR 已关闭，需要核对','关闭可能没有合并；现有回执不证明默认分支包含候选。','核对原 PR 合并记录和默认版本，再检查原验收入口。']},
 acceptance:{prepared:['合并版本与 CI 等待核验','原验收提案固定了默认分支版本和工作流，尚未完成核验。','审阅原验收范围，再使用原确认核验入口。'],
  checking:['正在核验合并版本与最新 CI','原验收操作尚未结束，历史成功不代替当前结果。','等待原验收回执，不重复提交核验。'],
  'delivery-verified':['指定默认版本与 CI 已核验','原验收回执核对了合并包含关系及指定版本的最新工作流和 Job；仅适用于记录版本。','查看准确默认提交和原验收回执；未来版本需重新核验。'],
  'not-verified':['合并版本或 CI 未通过验收','本次原验收没有取得完整通过证明，不能沿用旧 CI 成绩。','核对原默认版本、最新 run/attempt 与全部 Job 的阻塞证据。']},
 closure:{prepared:['Issue 关闭等待批准','原关闭提案已有版本与 CI 证据，尚未执行关闭。','审阅原 Issue 与验收范围，再使用原关闭审批。'],
  close_intent:['Issue 关闭意图已登记','原关闭操作尚无最终回执。','只读核对原 Issue 状态，不重复关闭。'],close_unknown:descriptions.close_unknown,
  'closed-verified':['Issue 已有交付验收关闭记录','原关闭回执登记了指定版本的交付核验；历史记录不保证未来版本持续通过。','查看原关闭依据、默认提交与 CI 回执。'],
  'closed-review-required':['Issue 已关闭，需要复核','原关闭操作需要进一步核对，尚不能算作验收完成。','只读核对原 Issue 关闭与交付证据。']},
 observer:{scheduled:['只读观察已安排','原观察授权已登记，尚未取得本轮版本与 CI 结果。','在原观察入口查看范围、到期时间和后续回执。'],
  checking:['只读观察正在核验','正在读取原 PR、默认版本与最新 CI，尚无本轮结论。','等待原观察回执，暂停仍由原观察入口处理。'],
  waiting_merge:['只读观察：等待 PR 合并','原观察尚未确认 PR 合并，候选和 PR 不等于默认分支交付。','查看原 PR 和只读观察状态。'],
  waiting_ci:['已观察到合并，等待最新 CI','原观察尚未取得准确默认版本的完整最新检查结果。','等待对应版本最新 run/attempt 和全部 Job 的原核验回执。'],
  checks_missing:['默认版本缺少必要检查','原观察发现准确默认版本没有完整的已批准工作流，不能沿用旧绿。','核对原工作流范围和对应版本的检查记录。'],
  checks_failed:['默认版本最新 CI 未通过','最新工作流失败、取消或跳过，旧成功不能作为当前验收。','查看原最新 run/attempt 与失败 Job，再核对原失败处理入口。'],
  verified:['只读观察：指定版本已核验','最近原观察回执核验了准确默认版本及最新 CI；未来提交或 attempt 仍需重查。','查看原观察的版本、工作流与 Job 回执。'],
  network_blocked:['只读观察网络受阻','本轮远端读取没有形成可靠验收结论，历史回执保留。','查看原观察网络错误和下次检查安排，不推断当前 CI 已通过。'],
  scope_changed:['只读观察范围已变化','发现未获原观察范围批准的工作流，观察已停止。','在原观察入口重新预览并确认范围。'],
  closed_without_merge:['PR 已关闭但没有合并','原观察停止，候选尚无默认分支交付证明。','查看原 PR 与交付记录，不能按修复成功关闭任务。'],
  paused:['只读观察已暂停','当前不继续观察；历史结果仅对应原记录版本。','核对原范围与到期时间，再使用原恢复入口。'],
  restart_paused:['只读观察重启后暂停','服务重启后原观察需要明确恢复，历史通过不代表当前版本。','核对原观察范围与有效期，再使用原恢复入口。'],
  expired:['只读观察已到期','原观察许可已到期，当前版本不再由这份许可继续核验。','查看最后原回执；需要新观察时重新预览并确认。'],
  capacity_reached:['只读观察历史容量已满','原观察已停止，不能继续签收新版本。','核对原历史记录和可用观察入口。'],
  blocked:['只读观察来源或验证受阻','本轮没有可靠的版本与 CI 验收结果。','查看原观察阻塞原因与原恢复条件。']},
};

// Each stage is retained separately: a local branch, a PR and an observation
// never become a synthetic "delivered" state or an executable action.
export function deliverySituations(row){
 if(!row?.id||!row.origin)return [];
 const result=[];
 const add=(stage,status,description)=>{const item=situation('delivery',row.id,status,
  description?[...description.slice(0,3),'delivery']:undefined);item.page='delivery';item.stage=stage;result.push(item);};
 if(row.source_changed_observed)add('source','stale',descriptions.stale);
 if(row.status==='evidence_unavailable')add('evidence','evidence_invalid',descriptions.evidence_invalid);
 if(row.status==='in_progress')add('evidence','running',descriptions.running);
 if(row.status==='candidate_review')add('candidate','candidate_ready',descriptions.candidate_ready);
 if(row.status==='needs_review')add('evidence','blocked',descriptions.blocked);
 if(row.evidence?.local_publication?.integration_branch&&row.evidence?.local_publication?.integration_commit)
  add('local','published',stageDescriptions.local.published);
 for(const stage of ['push','pr','acceptance','closure','observer']){
  const value=row.delivery?.[stage==='observer'?'acceptance_watch':stage];
  if(value?.status)add(stage,value.status,stageDescriptions[stage]?.[value.status]);
 }
 return result;
}

export function deliveryStageSituation(stage,record,kind='delivery',id=record?.id){
 const description=stageDescriptions[stage]?.[record?.status];
 const item=situation(kind,id,record?.status,description?[...description.slice(0,3),'delivery']:undefined);
 item.page='delivery';item.stage=stage;return item;
}

// Exact navigation to existing records only. Ambiguous rows remain on their
// original page and cannot select an arbitrary task with the same identity.
export function situationTaskTarget(rows,item){
 if(!['run','plan','task','issue'].includes(item?.kind))return null;
 return exact(rows,row=>row.selection?.kind===item.kind&&row.selection?.id===item.id
  ||item.kind==='run'&&row.runs?.some(run=>run.run_id===item.id)
  ||item.kind==='plan'&&row.plans?.some(plan=>plan.id===item.id));
}

/**
 * @param {any} state
 * @param {{repository?: string, monitors?: any[], delivery?: any}} [options]
 */
export function overviewSituations(state,{repository,monitors=[],delivery=null}={}){
 if(!state)return [situation('workspace','', 'record_unknown',
  ['正在读取当前仓库记录','尚未取得当前仓库的任务与原额度信息。','等待或刷新原仓库记录后，再核对下一步。','tasks'])];
 const repositoryProblem=repositoryStatus(state,repository);
 if(repositoryProblem)return [situation('workspace','',repositoryProblem,
  ['仓库记录尚未对应当前选择','当前显示数据与选中仓库的身份尚未匹配。','刷新并核对选中仓库，不能使用另一仓库的任务状态。','tasks'])];
 const rows=[];
 const add=(value)=>{if(value.attention&&!rows.some(row=>row.kind===value.kind&&row.id===value.id
  &&row.monitor_id===value.monitor_id&&row.status===value.status&&row.stage===value.stage))rows.push(value);};
 if(state.runtime?.code_changed)add(situation('workspace','runtime','runtime_changed',descriptions.runtime_changed));
 const pending=(state.planning_attempts||[]).filter(a=>a.status==='started');
 const pendingHeld=pending.reduce((sum,a)=>sum+(Number.isInteger(a.reserved)&&a.reserved>0?a.reserved:0),0);
 if((state.requests_reserved_active??(state.active?pendingHeld:0))>0)add(situation('workspace','budget','model_pending',descriptions.model_pending));
 if((state.requests_ended_unknown??(state.requests_reserved_unknown-(state.active?pendingHeld:0)))>0)
  add(situation('workspace','budget','unknown',descriptions.unknown));
 if(Number.isInteger(state.requests_remaining)&&state.requests_remaining<=0)add(situation('workspace','budget','budget_exhausted',descriptions.budget_exhausted));
 if(state.source_memory?.status==='invalid'||state.structure_index?.status==='invalid')add(situation('workspace','sources','source_invalid',descriptions.source_invalid));
 for(const card of state.recovery_cards||[])add(taskSituation(state,{kind:card.kind,id:card.id}));
 for(const plan of state.plans||[])if(plan.status==='awaiting_approval')add(taskSituation(state,{kind:'plan',id:plan.id}));
 for(const run of state.runs||[])if(!quiet.has(run.status)){
  const original=taskSituation(state,{kind:'run',id:run.run_id});add(original);
  if(['candidate_ready','integrated','published'].includes(run.status)&&!['record_unknown','candidate_pending',run.status].includes(original.status))
   add(presentRecord('run',run.run_id,run));
 }
 for(const execution of state.issue_workbench?.executions||[]){
  const item=taskSituation(state,{kind:'issue',id:execution.id,number:execution.issue_number});
  if(item.attention&&execution.status!=='running'&&execution.status!=='claimed')add(item);
 }
 const awaiting=(state.issue_workbench?.approvals||[]).filter(row=>row.status==='pending');
 for(const approval of awaiting){
  const execution=exact(state.issue_workbench?.executions,row=>row.id===approval.execution_id);
  if(execution)add(situation('issue',execution.id,'awaiting_approval',
   [descriptions.awaiting_approval[0],descriptions.awaiting_approval[1],descriptions.awaiting_approval[2],'issues']));
  else add(situation('approval',approval.id,'record_unknown',
   ['待审方案的执行归属待核对','原审批记录尚未唯一关联到当前执行，不能借用其他任务的审批入口。','进入 Issue 页核对原审批与执行身份。','issues']));
 }
 for(const [key,stage] of [['publications','push'],['pull_requests','pr'],['closures','closure']]){
  for(const record of state.issue_workbench?.[key]||[]){
   if(matches(state.issue_workbench[key],r=>r.id===record.id).length!==1){
    add(situation('issue',record.id,'record_unknown',['交付阶段归属待核对','存在身份重复的原交付阶段记录。','进入交付页核对原记录，不选择任意一份推进。','delivery']));continue;
   }
   const execution=exact(state.issue_workbench?.executions,r=>r.id===record.id);
   if(execution)add(deliveryStageSituation(stage,record,'issue',execution.id));
   else add(situation('issue',record.id,'record_unknown',['交付阶段归属待核对','该原发布记录未唯一关联到当前执行。','进入交付页核对原执行与阶段身份。','delivery']));
  }
 }
 for(const monitor of monitors){
  if(repository&&!(monitor.spec?.path===repository||monitor.spec?.repository===repository||monitor.repository===repository))continue;
  if(matches(monitors,m=>m.id===monitor.id).length!==1){const item=situation('monitor',monitor.id,'record_unknown');item.page='events';add(item);continue;}
  if((monitor.requests_ended_unknown??monitor.requests_reserved_unknown)>0)add(situation('monitor',monitor.id,'unknown',
   ['监控原账存在未知消费','这项监控使用自己的原请求账，不能与其他任务额度混算。','进入监控页核对该监控的原调用回执。','events']));
  const grant=monitor.model_authorization;
  const inFlight=grant?.effective_status==='unknown_consumption'&&monitor.requests_reserved_active>0&&monitor.requests_ended_unknown===0;
  if(inFlight)add(situation('monitor',monitor.id,'model_pending',[...descriptions.model_pending.slice(0,3),'events']));
  else if(grant&&grant.effective_status!=='active')add(situation('monitor',monitor.id,'model_'+grant.effective_status,
   ['监控模型授权需要核对',grant.effective_status==='binding_changed'?'原规则或模型配置已变化，不能沿用旧模型授权。':
    grant.effective_status==='unknown_consumption'?'原模型授权存在未结清消费，不能自动重试。':'原模型授权当前未启用；恢复监控检查不会自动恢复模型调用。',
    '进入监控页核对原模型授权状态、范围和额度，使用原预览确认入口。','events']));
  for(const followup of monitor.failure_followups||[]){
   const status=followup.effective_status||followup.status;
   const item=situation('followup',followup.id,status,
    [situationDisplayText('失败后继：')+(failureNames[status]||situationDisplayText('原状态待核对')),status==='active'?
     '只在原后继范围、模型授权和剩余额度内检查新失败；后继上限不保证实际执行。':
     status==='binding_changed'?'原后继绑定的规则或模型配置已变化，旧许可不能迁移。':
     status==='unknown_consumption'?'原后继消费尚未结清，不能重放已有意图。':
     '原后继已暂停、停止或受阻，旧任务与消费保留；具体原因查看原停止回执。',
     ['paused','restart_paused'].includes(status)?'进入对应监控事件核对原后继、模型许可和有效期，再检查原恢复入口。':
     status==='active'?'进入对应监控事件查看原后继范围、已有子任务与暂停入口。':
     '进入对应监控事件核对原停止或阻塞回执；停止状态不构成重试或重新派发许可。','events']);
   item.monitor_id=monitor.id;item.event_id=followup.core?.event_id;add(item);
  }
  for(const event of monitor.events||[]){
   const item=taskSituation({monitors},{kind:'monitor',monitor_id:monitor.id,id:event.id});
   if(item.attention)add(item);
  }
 }
 for(const row of delivery?.items||[]){
  if(!repository||row.origin?.repository!==repository)continue;
  if(matches(delivery.items,r=>r.id===row.id).length!==1){const item=situation('delivery',row.id,'record_unknown');item.page='delivery';add(item);continue;}
  const stages=deliverySituations(row);
  for(const item of stages.slice().reverse())add(item);
 }
 if(delivery&&delivery.complete===false)add(situation('workspace','delivery','record_unknown',
  ['交付记录读取尚不完整','部分原来源读取受阻或已达到展示上限，未显示不等于没有任务。','进入交付页核对原读取错误与已有记录。','delivery']));
 return rows;
}
