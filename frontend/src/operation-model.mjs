import {ui} from './ui-language.mjs';
// Presentation helpers: authority always comes from the original controller.
export function repositoryContext(record, remoteUrl) {
  return {workspace:record||null, remoteUrl:record?.github_url||remoteUrl||null, canMaintain:Boolean(record)};
}
export function finishedResults(state, action) {
  return (state?.jobs||[]).filter(j=>j.action===action&&j.status==='finished'&&j.result).slice().reverse().map(j=>j.result);
}
function canonical(value){
 if(Array.isArray(value))return value.map(canonical);
 if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])]));
 return value;
}
export function sameBinding(a,b){return Boolean(a&&b&&JSON.stringify(canonical(a))===JSON.stringify(canonical(b)));}
export function issueEvidence(state, number) {
  const data=state?.issue_workbench||{};
  const investigation=(data.investigations||[]).filter(v=>v.snapshot?.issue?.number===number).at(-1);
  const snapshot=finishedResults(state,'issue_read').find(v=>v.issue?.number===number)||investigation?.snapshot;
  const report=finishedResults(state,'issue_investigate').find(v=>v.id===investigation?.id&&v.snapshot?.issue?.number===number);
  const prepared=(data.environments||[]).find(v=>v.id===investigation?.id);
  const environment=finishedResults(state,'issue_environment').find(v=>v.id===prepared?.id&&
    v.config_sha256===prepared?.config_sha256&&sameBinding(v.binding,prepared?.binding));
  return {snapshot,investigation,report,prepared,environment,
    historical:Boolean(investigation&&(investigation.status==='stale'||snapshot?.token&&snapshot.token!==investigation.id))};
}
export function serviceBlocked(service) {
  return !service||Boolean(service.stopping||service.workspace_active||service.history_active||service.monitor_enabled||service.active||service.github_push_active);
}
export function stopOutcome(jobs,id,disconnected=false) {
  if(!id)return {status:'idle',message:''};
  const job=jobs?.find(j=>j.id===id);
  if(job?.status==='failed')return {status:'failed',message:job.error||job.result?.error||ui("停止未完成，请重新核对服务状态。")};
  if(job?.status==='finished'&&job.result?.stopping)return {status:'acknowledged',message:ui("已确认停止回执；连接状态需另行核对。")};
  if(job&&!['running','finished','failed'].includes(job.status))return {status:'unknown',message:ui("原服务操作已中断，停止结果待核对；不会自动重发。")};
  return {status:disconnected?'unknown':'pending',message:disconnected?ui("服务连接已断开，停止结果待核对；不会自动重发。"):ui("停止已受理，正在核对原操作记录。")};
}
export function latestResult(state,action,match=(_value)=>true){return finishedResults(state,action).find(match);}
export function workspaceFromLaunch(hub,url,origin){
 try{const parsed=new URL(url),match=/^#workspace=([a-f0-9]{32})$/.exec(parsed.hash);if(parsed.origin!==origin||!match)return null;return (hub?.workspaces||[]).find(w=>w.id===match[1])||null;}catch{return null;}
}
export function monitorsForWorkspace(hub,record,origin){
 return (hub?.monitors||[]).filter(m=>!record||m.spec?.path===record.repository||
  m.spec?.repository===record.repository||m.repository===record.repository||
  (m.events||[]).some(e=>workspaceFromLaunch(hub,e.launch_url,origin)?.id===record.id));
}
export function monitorPayload(source,policy,{branch,interval,github,confirmed}){
 if(!source?.revision||!source.path||!policy||!confirmed||!source.clean||!branch?.trim()||
   !Number.isInteger(interval)||interval<60||interval>3600||github&&!source.github_url)return null;
 return {path:source.path,revision:source.revision,allowed_paths:policy.allowed_paths,
  readonly_paths:policy.readonly_paths,allowed_tests:policy.allowed_tests,timeout_seconds:policy.timeout_seconds,
  confirmed:true,branch:branch.trim(),github_url:github?source.github_url:'',interval_seconds:interval};
}
