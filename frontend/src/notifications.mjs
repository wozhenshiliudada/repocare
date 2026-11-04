import {notificationDecision} from './preferences.mjs';
const failures=new Set(['failed','rolled_back','interrupted','blocked','unknown']);
const completions=new Set(['candidate_ready','published','integrated','delivered','finished','completed']);
const rows=value=>Array.isArray(value)?value:[];
const identity=(workspace,kind,id,status)=>[workspace||'hub',kind,id,status].join(':');
/** The first successful read of each workspace records a baseline; only later changes produce alerts. */
export function createNotificationTracker(){
 const baselines=new Map(),seen=new Set();let serviceState=null,serviceEpisode=0;
 function workspace(workspace_id,state){
  const current=new Map(),events=[];
  const add=(kind,entity_id,status,type,extra={})=>{if(typeof entity_id!=='string'||!entity_id)return;const key=kind+':'+entity_id;current.set(key,status);
   const eventId=identity(workspace_id,kind,entity_id,status),previous=baselines.get(workspace_id);
   if(type&&previous&&previous.get(key)!==status&&!seen.has(eventId)){seen.add(eventId);events.push({id:eventId,type,kind,entity_id,status,workspace_id,...extra});}
  };
  for(const plan of rows(state?.plans))add('plan',plan.id,plan.status,plan.status==='awaiting_approval'?'approval':null);
  for(const approval of rows(state?.issue_workbench?.approvals))add('issue-approval',approval.id,approval.status,approval.status==='pending'?'approval':null,{execution_id:approval.execution_id,issue_number:approval.issue_number});
  for(const run of rows(state?.runs))add('run',run.run_id||run.id,run.status,failures.has(run.status)?'failure':completions.has(run.status)?'completion':null,{task_id:run.task_id});
  for(const job of rows(state?.jobs))add('job',job.id,job.status,job.status==='failed'?'failure':null);
  baselines.set(workspace_id,current);return events;
 }
 function hub(value){return workspace('hub', {jobs:rows(value?.jobs)}).map(event=>({...event,workspace_id:undefined}));}
 function service(reachable){
  if(reachable===serviceState)return [];
  const previous=serviceState;serviceState=reachable;
  if(reachable&&previous===null)return [];
  if(!reachable)serviceEpisode++;
  return [{id:'service:'+serviceEpisode+':'+(reachable?'connected':'disconnected'),type:'service',kind:'service',status:reachable?'connected':'disconnected'}];
 }
 return {workspace,hub,service};
}
export function notificationText(event,language='zh-CN'){
 const english=language==='en',repository=event.repository?String(event.repository).split(/[\\/]/).at(-1):'',prefix=repository?repository+' · ':'';
 let title,body;
 if(event.kind==='service'){title=english?'Service connection':'服务连接';body=event.status==='connected'?(english?'Connection restored. Check the original operation before retrying.':'连接已恢复，请先核对原操作记录再继续。'):(english?'Connection lost. Pending operations are unchanged; check their original records.':'连接已中断。未完成操作保持原样，请核对原记录。');}
 else if(event.type==='approval'){title=english?'Approval required':'需要审批';body=english?'A new plan is waiting for your review.':'有新方案等待你审阅。';}
 else if(event.type==='failure'){title=english?'Needs attention':'需要关注';body=english?'An operation did not complete. Open its original record to review the result.':'有操作未完成，请打开原记录核对结果。';}
 else if(event.status==='candidate_ready'){title=english?'Candidate verified':'候选已验证';body=english?'The candidate is ready for review. Delivery still requires acceptance.':'候选已完成验证，等待审阅与交付验收。';}
 else {title=english?'Task status changed':'任务状态变化';body=english?'An operation reached a new result. Review the original evidence.':'有操作进入新的结果状态，请查看原始证据。';}
 return {title:prefix+title,body};
}
export function playNotificationSound(AudioContextClass=globalThis.AudioContext||globalThis.webkitAudioContext){
 if(!AudioContextClass)return false;
 try{const context=new AudioContextClass(),oscillator=context.createOscillator(),gain=context.createGain();
  oscillator.type='sine';oscillator.frequency.value=660;gain.gain.setValueAtTime(0.035,context.currentTime);gain.gain.exponentialRampToValueAtTime(0.001,context.currentTime+0.14);
  oscillator.connect(gain);gain.connect(context.destination);oscillator.onended=()=>{void context.close();};oscillator.start();oscillator.stop(context.currentTime+0.15);return true;
 }catch{return false;}
}
/** Permission is read only. Only the explicit Settings action may request it. */
export function dispatchNotification(event,preferences,options={}){
 const NotificationClass=options.NotificationClass??globalThis.Notification,permission=NotificationClass?.permission||'unsupported';
 const decision=notificationDecision(preferences,event.type,options.now||new Date(),permission),text=notificationText(event,preferences?.language);
 if(decision.inApp)options.onInApp?.({...event,...text});
 let desktop=false,sound=false;
 if(decision.desktop&&NotificationClass){try{const notification=new NotificationClass(text.title,{body:text.body,tag:event.id});notification.onclick=()=>{options.onOpen?.(event);notification.close?.();};desktop=true;}catch{}}
 if(decision.sound)try{sound=(options.playSound||playNotificationSound)()!==false;}catch{}
 return {inApp:decision.inApp,desktop,sound,quiet:decision.quiet};
}
