import {ui,uiLabels} from './ui-language.mjs';
export const watchNames=uiLabels({scheduled:'已安排',checking:'正在核验',waiting_merge:'等待 PR 合并',waiting_ci:'等待 CI 完成',
 checks_missing:'缺少必要检查',checks_failed:'最新检查未通过',verified:'当前指定版本已核验',network_blocked:'网络读取受阻',
 blocked:'来源或验证受阻',scope_changed:'工作流范围变化，需重新确认',closed_without_merge:'PR 已关闭但未合并',
 paused:'已暂停',restart_paused:'重启后已暂停',expired:'观察已到期',capacity_reached:'历史容量已满'});
const terminalStatuses=new Set(['scope_changed','closed_without_merge','expired','capacity_reached']);
function terminalStatus(row){
 if(terminalStatuses.has(row?.status))return row.status;
 const observed=row?.last_observation?.outcome?.status;
 return terminalStatuses.has(observed)?observed:null;
}
export function watchDeadline(minutes,now=Date.now()){
 if(!Number.isInteger(minutes)||minutes<2||minutes>1440)throw Error(ui("观察时长应为2至1440分钟"));
 return new Date(now+minutes*60000).toISOString().replace(/\.\d{3}Z$/,'Z');
}
export function watchCanResume(row,now=Date.now()){
 return Boolean(row&&!terminalStatus(row)&&!row.enabled&&row.observations?.length<100&&Date.parse(row.core?.expires_at)>now
  &&['paused','restart_paused','blocked'].includes(row.status));
}
export function watchLabel(row,now=Date.now()){
 const terminal=terminalStatus(row);
 if(terminal)return watchNames[terminal];
 if(row?.enabled&&Date.parse(row.core?.expires_at)<=now)return ui("观察已到期，等待后台收尾");
 return watchNames[row?.status]||row?.status||ui("尚未启用");
}
