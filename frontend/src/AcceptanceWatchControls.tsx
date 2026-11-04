import {ui} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useEffect,useState} from 'react';
import {Evidence,Props,useOperation} from './WorkbenchPages';
import {latestResult} from './operation-model.mjs';
import {watchLabel,watchDeadline,watchCanResume} from './acceptance-watch-model.mjs';
type Data=Record<string,any>;

export function AcceptanceWatchControls({p,row}:{p:Props;row:Data}){
 const origin=row.origin,selection={monitor_id:origin.monitor_id,event_id:origin.event_id,run_id:origin.run_id};
 const op=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh);
 const stop=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh);
 const [interval,setInterval]=useState(60),[minutes,setMinutes]=useState(30),[confirmed,setConfirmed]=useState(false),[error,setError]=useState(''),[previewKey,setPreviewKey]=useState('');
 const proposal=latestResult(p.hub,'monitor_delivery_watch_preview',(r:Data)=>r.core?.binding?.monitor_id===selection.monitor_id
  &&r.core.binding.event_id===selection.event_id&&r.core.binding.run_id===selection.run_id);
 const current=row.delivery?.acceptance_watch;
 useEffect(()=>setConfirmed(false),[row.id,proposal?.revision,interval,minutes]);
 useEffect(()=>{if(op.output){setConfirmed(false);p.refreshDelivery?.();}},[op.output]);
 useEffect(()=>{if(stop.output)p.refreshDelivery?.();},[stop.output]);
 const send=(name:string,extra:Data={})=>op.send('monitor_delivery_watch_'+name,{...selection,...extra});
 function preview(){try{setError('');setConfirmed(false);const expires=watchDeadline(minutes);setPreviewKey(`${interval}:${minutes}`);send('preview',{interval_seconds:interval,expires_at:expires});}catch(e){setError((e as Error).message);}}
 const matching=proposal?.core.interval_seconds===interval&&previewKey===`${interval}:${minutes}`;
 return <section aria-label={ui("持续只读交付观察")} className="inline-notice"><h4>{ui("持续观察合并后的版本和 CI")}</h4>
  <p>{ui("一次确认 PR、工作流和到期时间，服务运行时自动检查。只读 GitHub，不调用模型、不重跑 CI、不合并或关闭 Issue。页面关闭后服务仍可观察；浏览器通知需要页面打开。")}</p>
  <div className="context-fields"><label>{ui("检查间隔（秒）")}<input data-form-assist-draft="true" type="number" min={60} max={3600} value={interval} onChange={e=>setInterval(Number(e.target.value))}/></label>
   <label>{ui("观察时长（分钟）")}<input data-form-assist-draft="true" type="number" min={2} max={1440} value={minutes} onChange={e=>setMinutes(Number(e.target.value))}/></label></div>
  <AsyncButton feedback={op.feedback} disabled={op.busy||current?.enabled||!row.delivery?.pr?.receipt?.url} onClick={preview}>{ui("预览持续观察范围")}</AsyncButton>
  {error&&<p role="alert">{error}</p>}{op.status}{stop.status}
  {proposal&&matching&&!current?.enabled&&<><p>PR {proposal.core.pr.url}{ui("；默认分支 ")}{proposal.core.binding.base}{ui("；工作流 ")}{proposal.core.policy.workflow_ids.join('、')}{ui("；到期 ")}{proposal.core.expires_at}。</p>
   <p>{ui("新提交和新 attempt 都重新核验。新出现的工作流不自动纳入，缺检查、失败、取消或跳过不能沿用旧绿。")}</p>
   <label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("确认此范围与到期时间，仅启用只读观察")}</label>
   <AsyncButton feedback={op.feedback} disabled={op.busy||!confirmed} onClick={()=>{setConfirmed(false);send('configure',{preview:proposal.core,revision:proposal.revision,confirmed:true});}}>{ui("启用持续只读观察")}</AsyncButton></>}
  {current&&<><p role="status">{watchLabel(current)}{ui("；到期 ")}{current.core.expires_at}{ui("；最近检查 ")}{current.last_checked||ui("尚未检查")}。{current.error||''}</p>
   <p>{ui("最近记录版本 ")}{current.last_observation?.outcome?.head_sha||ui("尚未取得")}{ui("；历史通过仅对应记录版本，不保证未来持续通过。")}</p>
   <AsyncButton feedback={stop.feedback} disabled={Boolean(stop.pending)||!current.enabled} onClick={()=>stop.send('monitor_delivery_watch_pause',{...selection,watch_id:current.id,revision:current.challenge})}>{ui("暂停只读观察")}</AsyncButton>
   <AsyncButton feedback={op.feedback} disabled={op.busy||!watchCanResume(current)} onClick={()=>send('resume',{watch_id:current.id,revision:current.challenge})}>{ui("恢复原范围只读观察")}</AsyncButton>
   <Evidence value={current.last_observation} title={ui("最近一次观察与准确版本回执")}/></>}
 </section>;
}
