import {usageText} from './request-usage.mjs';
import {ui,uiLabels} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useState} from 'react';
import {useOperation,Evidence} from './WorkbenchPages';
import {latestResult} from './operation-model.mjs';
type Data=Record<string,any>;
const tomorrow=()=>{const d=new Date(Date.now()+86400000);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);};
const names:Data=uiLabels({not_configured:'尚未启用',active:'已启用',paused:'已暂停',revoked:'已撤销',expired:'已到期',
 binding_changed:'原规则或模型已变化',unknown_consumption:'原消费待核验',exhausted:'原额度已耗尽'});
const admissionNames:Data=uiLabels({started:'正在调查或规划',awaiting_validation_review:'等待你核对验收案例',
 validation_interrupted_no_replay:'案例提议中断，核对原会话；不会自动重发',blocked_no_replay:'本次受阻，保留原记录；不会自动重跑',
 existing_work_no_replay:'关联已有工作，不重复执行',clarification_needed:'需求需要补充',
 'awaiting-review':'候选待验收'});
function admissionText(row:Data){const key=row.status==='finished'?row.result_status:row.status;
 return admissionNames[key]||key;}
export function IssueWatchControl({record,state,request,refresh}:{record:Data;state:Data;request:any;refresh:()=>Promise<unknown>}){
 const [expiry,setExpiry]=useState(tomorrow),[interval,setInterval]=useState(60),[attempts,setAttempts]=useState(3),
  [consent,setConsent]=useState(false),[controlling,setControlling]=useState(false),[message,setMessage]=useState('');
 const op=useOperation(record,state,request,refresh),data=state.issue_workbench,watch=data?.watch,grant=watch?.authorization;
 const result=latestResult(state,'issue_watch_preview'),value=result?.preview;
 const iso=(()=>{try{return new Date(expiry).toISOString();}catch{return '';}})();
 const matches=value&&value.binding.configuration_sha256===data?.configuration?.sha256
  &&new Date(value.expires_at).getTime()===new Date(iso).getTime()&&value.interval_seconds===interval
  &&(data.configuration.mode!=='auto'||value.max_attempts===attempts)
  &&value.budget.request_budget===state.request_budget&&value.budget.requests_used===state.requests_used
  &&value.budget.requests_reserved_unknown===state.requests_reserved_unknown
  &&value.previous?.id===grant?.id&&value.previous?.status===grant?.status;
 async function control(action:string){
  if(controlling||!grant)return;setControlling(true);setConsent(false);setMessage('');
  try{await request(record.api_prefix+'actions/'+action,record.token,{grant_id:grant.id,request_id:crypto.randomUUID().replaceAll('-','')});
   setMessage(ui("控制记录已保存；当前接单按原暂停机制协作退出，原消费和候选保留。"));await refresh();}
  catch(e){setMessage((e as Error).message);}finally{setControlling(false);}
 }
 if(!data?.configured||!data.configuration)return null;
 return <section className="issue-section issue-watch-control" aria-label={ui("持续Issue接单")}><h3>{ui("自动接手符合规则的 Issue")}</h3>
  <p>{ui("按可信维护者的 ready-for-agent 标签准入，调查具体文件与检查，再生成方案或隔离候选。只首次接手；其中缺少检查时先提议验收案例，等你核对后沿原记录继续；其他已有任务、失败和变化后的版本不自动重跑。")}</p>
  <p>{ui("模型可能收费。","Model calls may incur charges.")} {usageText(state)}{ui("。不增加额度，不自动推送、合并或关闭 Issue。")}</p>
  <p>{ui("持续接单：")}{names[watch?.status]||watch?.status||ui("尚未启用")}{ui("。服务重启后暂停，需要重新预览确认；服务关闭期间不接单。")}</p>
  {grant&&<><p>{ui("到期：")}{grant.core.expires_at}</p><AsyncButton disabled={controlling||grant.status!=='active'} onClick={()=>control('issue_watch_pause')}>{ui("暂停持续接单")}</AsyncButton>
   <AsyncButton disabled={controlling||grant.status==='revoked'} onClick={()=>control('issue_watch_revoke')}>{ui("撤销持续接单授权")}</AsyncButton></>}
  {message&&<p role="status">{message}</p>}{op.status}
  <label>{ui("接单授权到期时间（本地时间，最长7天）")}<input type="datetime-local" value={expiry} onChange={e=>{setExpiry(e.target.value);setConsent(false);}}/></label>
  <label>{ui("Issue检查间隔（秒，至少60）")}<input type="number" min={60} max={3600} value={interval} onChange={e=>{setInterval(Number(e.target.value));setConsent(false);}}/></label>
  {data.configuration.mode==='auto'&&<label>{ui("每个Issue的候选轮数上限")}<input type="number" min={1} value={attempts} onChange={e=>{setAttempts(Number(e.target.value));setConsent(false);}}/></label>}
  <AsyncButton feedback={op.feedback} disabled={op.busy||!iso} onClick={()=>{setConsent(false);op.send('issue_watch_preview',{expires_at:iso,interval_seconds:interval,max_attempts:attempts});}}>{ui("预览持续接单授权")}</AsyncButton>
  {matches&&<><p>{ui("读目录 ")}{value.configuration.spec.read_roots.join('、')}{ui("；写目录 ")}{value.configuration.write_roots.join('、')}{ui("；可信标签维护者 ")}{value.configuration.spec.trusted_label_actors.join('、')}；{value.configuration.mode==='auto'?ui("自主隔离候选"):ui("方案后人工审批")}。</p>
   <Evidence value={value} title={ui("持续接单范围、原模型与消费绑定")}/>
   <label className="check-label"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>{ui("允许在有效期内执行符合原规则的首次Issue调查、隔离检查及模型调用（可能收费），共用原剩余额度")}</label>
   <AsyncButton feedback={op.feedback} disabled={op.busy||!consent} onClick={()=>{setConsent(false);op.send('issue_watch_configure',{...result,confirmed:true});}}>{ui("确认启用持续接单")}</AsyncButton></>}
  <AsyncButton feedback={op.feedback} disabled={op.busy||!watch?.active} onClick={()=>op.send('issue_watch_check',{})}>{ui("立即检查可接手Issue")}</AsyncButton>
  {watch?.scan&&<p>{ui("最近观察：")}{watch.scan.at} · {watch.scan.error?`${ui("受阻：")}${watch.scan.error}`:watch.scan.complete?ui("分页完整"):ui("分页不完整，不启动修复")}</p>}
  {(watch?.admissions||[]).map((row:Data)=><p key={row.id}>Issue #{row.number} · {admissionText(row)}{row.source_changed?ui(" · 来源已变化，需复核"):''} {row.execution_id&&`${ui("原执行 ")}${row.execution_id.slice(0,12)}`}</p>)}
 </section>;
}
