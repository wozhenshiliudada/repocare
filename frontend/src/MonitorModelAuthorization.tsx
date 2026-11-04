import {usageText} from './request-usage.mjs';
import {ui,uiLabels} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useState} from 'react';
import {Props,Evidence,useOperation} from './WorkbenchPages';
import {latestResult} from './operation-model.mjs';
type Data=Record<string,any>;
const localTomorrow=()=>{const d=new Date(Date.now()+86400000);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);};
const states:Data=uiLabels({active:'已启用',paused:'已暂停',revoked:'已撤销',expired:'已到期',monitor_paused:'监控已暂停',
 binding_changed:'规则或模型已变化',unknown_consumption:'原消费待核验',exhausted:'原额度已耗尽',clock_changed:'系统时间变化，需核验'});
export function MonitorModelAuthorization({p,monitor}:{p:Props;monitor:Data}){
 const [expiry,setExpiry]=useState(localTomorrow),[attempts,setAttempts]=useState(3),[confirmed,setConfirmed]=useState(false),
  [controlling,setControlling]=useState(false),[controlMessage,setControlMessage]=useState('');
 const op=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh);
 const grant=monitor.model_authorization,mode=monitor.root_rules?.settings.mode;
 const grantLabel=grant?.effective_status==='unknown_consumption'&&monitor.requests_reserved_active>0&&monitor.requests_ended_unknown===0
  ?ui("进行中 · 请求额度已预留","In progress · Request allowance reserved")
  :states[grant?.effective_status]||grant?.effective_status;
 const result=latestResult(p.hub,'monitor_model_preview',(v:Data)=>v.preview?.binding.monitor_id===monitor.id);
 const value=result?.preview,iso=(()=>{try{return new Date(expiry).toISOString();}catch{return '';}})();
 const matches=value&&new Date(value.expires_at).getTime()===new Date(iso).getTime()
  &&value.binding.root_rules_sha256===monitor.root_rules?.sha256&&value.binding.mode===mode
  &&value.budget.request_budget===monitor.request_budget&&value.budget.requests_used===monitor.requests_used
  &&value.budget.requests_reserved_unknown===monitor.requests_reserved_unknown
  &&value.previous?.id===grant?.id&&value.previous?.status===grant?.status
  &&(mode!=='auto'||value.max_attempts===attempts);
 async function control(action:string){
  if(controlling||!grant)return;setControlling(true);setConfirmed(false);setControlMessage('');
  try{await p.request('/api/actions/'+action,undefined,{request_id:crypto.randomUUID().replaceAll('-',''),monitor_id:monitor.id,grant_id:grant.id});
   setControlMessage(ui("已保存控制记录；运行中的模型任务按原停止机制协作退出，消费不退回。"));await p.refresh();}
  catch(e){setControlMessage((e as Error).message);}finally{setControlling(false);}
 }
 if(!monitor.root_rules)return null;
 return <section aria-label={ui("未来提交的模型授权")} className="inline-notice"><h3>{ui("让后续新提交自动进入修复流程")}</h3>
  <p>{ui("只处理授权后新观察到的版本；不重跑当前提交或旧失败。先自动调查并复现，人工模式生成待审方案，自主模式准备隔离候选。")}</p>
  <p>{ui("模型调用可能收费，请求次数不等于费用金额。沿用原目录、检查和模型配置。","Model calls may incur charges; request counts are not costs. The existing directories, checks, and model configuration still apply.")} {usageText(monitor)}{ui("。不会增加额度、推送或合并。")}</p>
  {grant&&<><p>{ui("模型授权：")}{grantLabel}{ui("；到期 ")}{grant.core.expires_at}。</p>
   <div className="button-row"><AsyncButton disabled={controlling||grant.status!=='active'} onClick={()=>control('monitor_model_pause')}>{ui("暂停自动模型调用")}</AsyncButton>
    <AsyncButton disabled={controlling||grant.status==='revoked'} onClick={()=>control('monitor_model_revoke')}>{ui("撤销这份模型授权")}</AsyncButton></div></>}
  {controlMessage&&<p role="status">{controlMessage}</p>}{op.status}
  <p>{ui("暂停后恢复监控只恢复检查；服务重启后模型授权也会暂停。需要重新预览确认，才授权之后的新版本。已有结果仍保留，人工审批和发布分别处理。")}</p>
  <label>{ui("授权到期时间（本地时间，最长7天）")}<input type="datetime-local" value={expiry} onChange={e=>{setExpiry(e.target.value);setConfirmed(false);}}/></label>
  {mode==='auto'&&<label>{ui("每个新事件的候选轮数上限")}<input type="number" min={1} value={attempts} onChange={e=>{setAttempts(Number(e.target.value));setConfirmed(false);}}/></label>}
  <AsyncButton feedback={op.feedback} disabled={op.busy||!monitor.enabled||!iso} onClick={()=>{setConfirmed(false);op.send('monitor_model_preview',{
   monitor_id:monitor.id,expires_at:iso,max_attempts:attempts});}}>{ui("预览未来提交的模型授权")}</AsyncButton>
  {matches&&<><p>{ui("可读目录 ")}{value.read_roots.join('、')}{ui("；可改目录 ")}{value.write_roots.join('、')}{ui("；模式 ")}{mode==='auto'?ui("自主候选"):ui("方案后人工审批")}。</p>
   <Evidence value={value} title={ui("模型身份、原额度、版本与有效期")}/>
   <label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("允许在有效期内，为后续新版本调用已配置模型（可能收费），共用原剩余额度；请求次数不等于费用金额")}</label>
   <AsyncButton feedback={op.feedback} disabled={op.busy||!confirmed} onClick={()=>{setConfirmed(false);op.send('monitor_model_configure',{...result,confirmed:true});}}>{ui("确认这份未来模型授权")}</AsyncButton></>}
 </section>;
}
