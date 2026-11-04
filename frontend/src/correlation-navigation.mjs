import {ui} from './ui-language.mjs';
// Read-only projection for "open the original owner's task/report".
// Every answer comes from already-read rows, hub registrations or an exact
// monitor/event pair. Nothing here creates a task, approval, model call,
// request or permission. Unresolvable records are returned as unavailable so
// the caller can show the next step instead of guessing an identity.
import {monitorsForWorkspace,workspaceFromLaunch} from './operation-model.mjs';

const text=value=>typeof value==='string'?value.trim():'';
const list=value=>Array.isArray(value)?value.filter(row=>row&&typeof row==='object'):[];

function unavailable(base,reason){return {...base,available:false,action:null,reason,hint:''};}

/**
 * Pure read-only projection; never sends a request or mutates a task.
 * @param {{record?:any,hub?:any,rows?:any[],link?:any,origin?:string,callbacks?:any}} [input]
 * @returns {any}
 */
export function ownerNavigation({record,hub,rows,link,origin='',callbacks={}}={}){
 const owner=link&&typeof link==='object'&&link.owner_source&&typeof link.owner_source==='object'
  ?link.owner_source:null;
 const kind=owner?.kind==='issue'?'issue':owner?.kind==='monitor'?'monitor':null;
 const base={linkId:text(link?.id),status:text(link?.status),ownerKind:kind,showButton:false,
  buttonLabel:ui("查看原负责人任务"),available:false,action:null,reason:'',hint:''};
 if(!link||typeof link!=='object')
  return unavailable(base,ui("关联记录尚未读取，无法定位原负责人。下一步：核对原证据，不新建替代任务。"));
 if(link.status!=='linked')
  return unavailable(base,ui("关联尚未完成或需要核验。下一步：先核对原关联状态与各来源原账，此状态不提供原负责人跳转。"));
 if(!owner)
  return unavailable(base,ui("尚未读取到原负责人记录。下一步：核对原派发认领后再查看原任务，不新建替代任务。"));
 const shown={...base,showButton:true};
 if(!kind)return unavailable({...shown,buttonLabel:ui("查看原负责人记录")},
  ui("原负责人类型不在当前支持范围。下一步：核对原关联与各来源原账，不猜测任务。"));
 if(kind==='issue')
  return issueNavigation({record,hub,rows,owner,callbacks,base:{...shown,buttonLabel:ui("查看原负责人任务")}});
 return monitorNavigation({record,hub,owner,origin,callbacks,base:{...shown,buttonLabel:ui("查看原负责人报告")}});
}

// Issue owner_source is authoritative only when its workspace and one exact
// execution/run/investigation identity resolve to one already-read task row.
// execution_id is the authoritative claim field; run_id or investigation_id is
// only a fallback when execution_id is absent (the issue row projection does
// not always carry run metadata). A foreign workspace is only switched to when
// the hub has exactly registered that workspace id; this module never invents a
// task id for that workspace.
function issueNavigation({record,hub,rows,owner,callbacks,base}){
 const workspaceId=text(owner.workspace_id),investigationId=text(owner.investigation_id),
  executionId=text(owner.execution_id),runId=text(owner.run_id);
 if(!workspaceId)
  return unavailable(base,ui("原负责人记录缺少明确工作区，无法安全定位原任务。下一步：核对原证据，不新建替代任务。"));
 if(!investigationId&&!executionId&&!runId)
  return unavailable(base,ui("原负责人记录缺少调查、执行或运行标识，无法定位原任务。下一步：核对原证据，不猜测任务。"));
 if(workspaceId!==text(record?.id)){
  if(!text(record?.id))
   return unavailable(base,ui("当前尚未选择有明确身份的工作区。下一步：先连接原仓库，再核对原负责人任务。"));
  const found=list(hub?.workspaces).filter(workspace=>text(workspace?.id)===workspaceId);
  if(!found.length)
   return unavailable(base,ui("原工作区尚未在本机登记，当前不能跨仓库选择任务。下一步：先明确连接原仓库，再核对原负责人任务。"));
  if(found.length>1)
   return unavailable(base,ui("原工作区登记存在多个相同身份，无法唯一确定原工作区。下一步：核对原工作区记录，不切换任意一份。"));
  if(typeof callbacks?.selectWorkspace!=='function')
   return unavailable(base,ui("原工作区已登记，但当前页面没有工作区切换入口。下一步：从仓库列表进入原工作区后核对原任务。"));
  return {...base,available:true,action:{kind:'workspace',id:found[0].id},
   hint:ui("只读切换已登记的原工作区；进入后按原记录核对任务与报告，不新建任务、不调用模型。")};
 }
 let target=null;
 if(executionId){
  const found=list(rows).filter(row=>text(row?.id)&&(
   row.selection?.kind==='issue'&&text(row.selection?.id)===executionId||text(row?.execution_id)===executionId));
  if(!found.length)
   return unavailable(base,ui("当前已读任务中没有该原执行记录，不能猜测任务。下一步：刷新并核对原工作区与原证据。"));
  if(found.length>1)
   return unavailable(base,ui("当前已读任务中存在多个相同原执行记录。下一步：核对原证据，不选择任意一份。"));
  target=found[0];
 }else if(runId){
  const found=list(rows).filter(row=>text(row?.id)&&(
   list(row?.runs).some(run=>text(run?.run_id)===runId)||text(row?.run_id)===runId));
  if(!found.length)
   return unavailable(base,ui("当前已读任务中没有该原运行记录，不能猜测任务。下一步：刷新并核对原工作区与原证据。"));
  if(found.length>1)
   return unavailable(base,ui("当前已读任务中存在多个相同原运行记录。下一步：核对原证据，不选择任意一份。"));
  target=found[0];
 }else{
  const found=list(rows).filter(row=>text(row?.id)&&(
   text(row?.investigation_id)===investigationId||
   row.selection?.kind==='issue'&&text(row.selection?.id)===investigationId));
  if(!found.length)
   return unavailable(base,ui("当前已读任务没有暴露可核对的调查标识，无法安全定位原任务。下一步：核对原工作区与执行记录，不猜测任务。"));
  if(found.length>1)
   return unavailable(base,ui("当前已读任务中存在多个相同调查记录。下一步：核对原证据，不选择任意一份。"));
  target=found[0];
 }
 if(!target)return unavailable(base,ui("未能唯一定位原负责人任务。下一步：核对原证据，不猜测任务。"));
 if(typeof callbacks?.selectTask!=='function')
  return unavailable(base,ui("原任务已唯一定位，但当前页面没有任务选择入口。下一步：从原任务列表进入后核对。"));
 return {...base,available:true,action:{kind:'task',id:target.id},
  hint:ui("只读打开当前已读的原任务；不调用模型、不新建任务、不改变原账。")};
}

// Monitor owner_source is resolved only by its exact monitor_id/event_id. A
// registered launch workspace is entered; a current-repository event opens the
// existing monitoring page. No synthetic task or run id is produced.
function monitorNavigation({record,hub,owner,origin,callbacks,base}){
 const monitorId=text(owner.monitor_id),eventId=text(owner.event_id);
 if(!monitorId||!eventId)
  return unavailable(base,ui("原负责人记录缺少监控或事件标识，无法定位原监控资料。下一步：核对原证据，不猜测事件。"));
 if(!text(record?.id))
  return unavailable(base,ui("当前尚未选择有明确身份的工作区，不能把监控资料当成本仓库任务。下一步：先连接原仓库，再核对原监控资料。"));
 const monitors=list(hub?.monitors).filter(monitor=>text(monitor?.id)===monitorId);
 if(monitors.length!==1)
  return unavailable(base,monitors.length?
   ui("原监控登记存在多个相同标识，无法唯一确定原事件。下一步：核对原监控记录，不打开任意一份。"):
   ui("当前已读监控中没有该原监控记录，不能打开猜测的事件。下一步：刷新并核对原工作区与原证据。"));
 const events=list(monitors[0].events).filter(event=>text(event?.id)===eventId);
 if(events.length!==1)
  return unavailable(base,events.length?
   ui("原监控中存在多个相同事件标识，无法唯一确定原事件。下一步：核对原事件记录，不打开任意一份。"):
   ui("当前已读监控中没有该原事件记录，不能打开猜测的事件。下一步：刷新并核对原监控资料与原证据。"));
 const event=events[0],workspace=workspaceFromLaunch(hub,event.launch_url,origin);
 if(workspace){
  if(!text(workspace.id))
   return unavailable(base,ui("原监控事件登记的工作区身份无效。下一步：核对原证据，不打开猜测的工作区。"));
  const registered=list(hub?.workspaces).filter(row=>text(row?.id)===text(workspace.id));
  if(registered.length!==1)
   return unavailable(base,ui("原监控事件登记的工作区存在多个相同身份，无法唯一确定原工作区。下一步：核对原工作区记录，不切换任意一份。"));
  if(text(record?.id)&&text(workspace.id)===text(record.id)){
   if(typeof callbacks?.navigate!=='function')
    return unavailable(base,ui("原监控事件属于当前工作区，但当前页面没有监控导航入口。下一步：从监控页核对原事件。"));
   return {...base,available:true,action:{kind:'events'},
    hint:ui("打开当前工作区的原监控资料；这是只读导航，不代表已打开一个原任务。")};
  }
  if(typeof callbacks?.selectWorkspace!=='function')
   return unavailable(base,ui("原监控事件已有登记工作区，但当前页面没有工作区切换入口。下一步：从仓库列表进入原工作区后再核对。"));
  return {...base,available:true,action:{kind:'workspace',id:workspace.id},
   hint:ui("只读进入原监控工作区；进入后按原监控资料核对事件与报告，不新建任务。")};
 }
 const visible=monitorsForWorkspace(hub,record,origin).some(monitor=>text(monitor?.id)===monitorId);
 if(!visible)
  return unavailable(base,ui("该原监控事件属于另一仓库或原工作区尚未登记，当前页面无法安全打开。下一步：先明确连接原仓库，再核对原监控资料。"));
 if(typeof callbacks?.navigate!=='function')
  return unavailable(base,ui("当前页面没有监控导航入口。下一步：从监控页核对原事件资料。"));
 return {...base,available:true,action:{kind:'events'},
  hint:ui("打开当前仓库的原监控资料；这是只读导航，不代表已打开一个原任务。")};
}
