import {ui,uiLabels} from './ui-language.mjs';
import {sameBinding} from './operation-model.mjs';

export const failureNames=uiLabels({active:'已启用',paused:'已暂停',restart_paused:'重启后已暂停',expired:'已到期',
 model_authorization_inactive:'原模型授权已暂停或失效',
 exhausted:'原额度或后继次数已用尽',unknown_consumption:'原消费待核验',binding_changed:'原规则或模型授权已变化',
 monitor_paused:'监控已暂停',clock_changed:'系统时间变化，需核验',blocked:'来源核验受阻',
 stopped:'已停止',stalled:'失败步骤和报错文本重复',capacity_reached:'记录容量已满'});

export function failureEligible(event){
 const execution=event?.root_execution;
 return Boolean(!event?.failure_successor&&execution?.finished_at&&(execution.run_id||event.failure_parent_run_id)
  &&execution.plan_id&&execution.binding?.ci_failure&&execution.model_authorization);
}
export function failureExpiry(local,ceiling,now=Date.now()){
 const epoch=new Date(local).getTime(),limit=new Date(ceiling).getTime();
 if(!Number.isFinite(epoch)||!Number.isFinite(limit)||epoch<now+60000||epoch>limit)return '';
 return new Date(epoch).toISOString().replace(/\.\d{3}Z$/,'Z');
}
export function failurePreviewMatches(proposal,monitor,event,maximum,expiry){
 const core=proposal?.preview,grant=monitor?.model_authorization;
 return Boolean(proposal?.revision&&core&&expiry&&Number.isInteger(maximum)&&maximum>=1&&maximum<=3
  &&core.monitor_id===monitor?.id&&core.event_id===event?.id&&core.max_successors===maximum
  &&Date.parse(core.expires_at)===Date.parse(expiry)&&core.parent?.run_id===(event.root_execution?.run_id||event.failure_parent_run_id)
  &&core.parent?.head_sha===event.sha&&core.model_authorization?.id===grant?.id
  &&core.model_authorization?.core_sha256===grant?.core_sha256&&grant?.active
  &&sameBinding(core.binding,grant.core?.binding)&&core.binding?.root_rules_sha256===monitor.root_rules?.sha256
  &&core.budget?.request_budget===monitor.request_budget&&core.budget?.requests_used===monitor.requests_used
  &&core.budget?.requests_reserved_unknown===monitor.requests_reserved_unknown
  &&core.budget?.requests_remaining===monitor.requests_remaining&&core.same_sha_only===true&&core.may_charge===true);
}
export function failureRemaining(row){return Math.max(0,(row?.core?.max_successors||0)-(row?.children?.length||0));}
export function failureLabel(row){return failureNames[row?.effective_status||row?.status]||row?.effective_status||row?.status||ui("尚未启用");}
export function failureCanResume(row,grant,now=Date.now()){
 return Boolean(row&&!row.enabled&&['paused','restart_paused'].includes(row.status)&&failureRemaining(row)>0
  &&Date.parse(row.core?.expires_at)>now&&grant?.active&&Date.parse(grant.core?.expires_at)>now
  &&row.core?.model_authorization?.id===grant.id&&row.core?.model_authorization?.core_sha256===grant.core_sha256
  &&sameBinding(row.core?.binding,grant.core?.binding));
}
