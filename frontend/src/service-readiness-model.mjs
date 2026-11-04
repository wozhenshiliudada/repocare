import {ui,uiLabels} from './ui-language.mjs';
// Presentation only: local checks and a reachable service do not authorize work.
const labels=uiLabels({ready:'本地条件齐备',blocked:'本地条件待补齐',unknown:'尚未完成本地检查'});
export const readinessGroups=uiLabels([['installation','程序安装'],['runtime','Pi 本地运行时'],['assets','网页文件'],['provider','模型配置']]);
const requiredChecks={python:'installation',git:'installation',node:'runtime',pi_lock:'runtime',
 ui_index:'assets',python_dependencies:'installation',pi_dependencies:'runtime',ui_assets:'assets',provider_config:'provider'};
const knownChecks={...requiredChecks,pi_runtime:'runtime',pi_resources:'runtime',
 installation:'installation',installation_report:'installation',installation_extra:'installation'};
const safeText=(text,max)=>typeof text==='string'&&text.length<=max&&!/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(text);
export function serviceReadiness(value,now=Date.now()){
 const valid=value?.schema_version===1&&value?.scope==='local_only'&&value?.network_checked===false
  &&value?.model_calls===0&&Array.isArray(value?.checks)&&value.checks.length<=100
  &&typeof value?.stale==='boolean'&&typeof value?.valid_for_seconds==='number'
  &&Number.isFinite(value.valid_for_seconds)&&value.valid_for_seconds>=1&&value.valid_for_seconds<=3600
  &&['ready','blocked','unknown'].includes(value?.status)
  &&value.checks.every(row=>row&&typeof row==='object'&&!Array.isArray(row)
   &&typeof row.id==='string'&&Object.hasOwn(knownChecks,row.id)&&knownChecks[row.id]===row.group
   &&['ready','blocked','unknown'].includes(row.status)&&safeText(row.label,200)
   &&safeText(row.detail,2000)&&safeText(row.next_action,2000))
  &&new Set(value.checks.map(row=>row.id)).size===value.checks.length;
 if(!valid)return {status:'unknown',label:labels.unknown,stale:false,checkedAt:null,checks:[],modelLabel:ui("模型请求尚未验证")};
 const checked=typeof value.checked_at==='string'&&value.checked_at.length<=64
  &&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value.checked_at)
  ?Date.parse(value.checked_at):NaN;
 const clockValid=Number.isFinite(now)&&Number.isFinite(checked)&&checked<=now;
 const present=new Set(value.checks.map(row=>row.id));
 const statuses=new Set(value.checks.map(row=>row.status));
 const complete=Object.keys(requiredChecks).every(id=>present.has(id));
 const stale=value.stale||clockValid&&now-checked>=value.valid_for_seconds*1000;
 const status=!clockValid?'unknown':statuses.has('blocked')?'blocked':!complete
  ||statuses.has('unknown')||value.status!=='ready'||stale?'unknown':'ready';
 return {status,label:stale?ui("本地检查已过期，请重新检查"):labels[status],stale,
  checkedAt:clockValid?value.checked_at:null,checks:value.checks,
  modelLabel:ui("模型请求尚未验证")};
}

export function gatewayRecoveryText(){
 return ui("若服务已停止或重启，请在终端用原 --home 运行 repocare gateway status 核对。服务运行后，用同一 --home 执行 repocare gateway open 打开当前入口。","If the service stopped or restarted, run repocare gateway status in a terminal with the original --home. Once it is running, use repocare gateway open with the same --home for the current entry.");
}

export function serviceSituation(service,disconnected=false){
 if(disconnected)return {label:ui("连接中断"),detail:ui("当前页面未取得服务回执，请重新核对连接。"),next:gatewayRecoveryText()};
 if(!service)return {label:ui("正在读取服务状态"),detail:ui("尚未取得当前服务的运行信息。"),next:ui("等待服务回执，或刷新服务状态。")};
 if(service.stopping)return {label:ui("服务正在停止","Service is stopping"),detail:ui("已取得停止中的状态，仍需核对原进程退出。","The service reports that it is stopping. The original process exit still needs verification."),next:gatewayRecoveryText()};
 if(service.runtime?.code_changed)return {label:ui("代码版本已变化"),detail:ui("运行中的服务尚未加载磁盘上的新代码。"),next:ui("先结束当前活动，再保留原状态目录安全重启。")};
 if(service.workspace_active||service.history_active||service.active)return {label:ui("有操作正在执行"),detail:ui("当前服务仍有维护任务或资料读取操作。"),next:ui("进入对应任务查看进度；需要中止时使用原停止入口。")};
 if(service.monitor_enabled||service.acceptance_watch_active)return {label:ui("监控或验收观察已启用"),detail:ui("服务正在按已有规则观察仓库或交付状态。"),next:ui("查看监控记录；停止服务前先暂停监控与观察。")};
 return {label:ui("服务已连接 · 当前闲置"),detail:ui("已取得服务回执，本地执行条件需单独核对。"),next:ui("检查下方本地条件，再进入原仓库任务。")};
}
