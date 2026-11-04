import {readinessDisplayText} from './readiness-ui-copy.mjs';
import {ui,getUiLanguage} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useEffect,useRef,useState} from 'react';
import {Evidence,Props} from './WorkbenchPages';
import {serviceBlocked,stopOutcome} from './operation-model.mjs';
import {ProviderSettings} from './ProviderSettings';
import {GitHubPushStatus} from './GitHubPushStatus';
import {readinessGroups,serviceReadiness,serviceSituation} from './service-readiness-model.mjs';
type Data=Record<string,any>;
export function ServicePanel(p:Props){
 const [service,setService]=useState<Data|null>(null),[error,setError]=useState(''),[confirmed,setConfirmed]=useState(false),[stopping,setStopping]=useState(false),[requestId,setRequestId]=useState<string|null>(null),[disconnected,setDisconnected]=useState(false);
 const [reading,setReading]=useState(false);
 const [inspection,setInspection]=useState<Data|null>(null),[inspecting,setInspecting]=useState(false),[inspectionError,setInspectionError]=useState('');
 const [,setAgeRevision]=useState(0);
 const inspectionBusy=useRef(false);
 const inFlight=useRef(false),alive=useRef(true),latest=useRef(p);latest.current=p;
 async function read(){
  if(inFlight.current)return;inFlight.current=true;setReading(true);
  try{const value=await latest.current.request('/api/service');if(alive.current){setService(value);setDisconnected(false);setError('');
   if(value.readiness)setInspection(old=>!old?.checked_at||!value.readiness.checked_at||value.readiness.checked_at>=old.checked_at?value.readiness:old);}}
  catch(e){if(alive.current){setError((e as Error).message);setDisconnected(true);}}
  finally{inFlight.current=false;if(alive.current)setReading(false);}
 }
 useEffect(()=>{alive.current=true;read();const poll=()=>{if(!document.hidden)read();};const timer=setInterval(poll,3000);document.addEventListener('visibilitychange',poll);return()=>{alive.current=false;clearInterval(timer);document.removeEventListener('visibilitychange',poll);};},[]);
 // Age cached evidence even if disconnected requests repeat the same error and
 // React skips those state updates. This timer performs no server/model calls.
 useEffect(()=>{const timer=setInterval(()=>setAgeRevision(value=>value+1),1000);return()=>clearInterval(timer);},[]);
 const outcome=stopOutcome(p.hub.jobs,requestId,disconnected);
 const readiness=serviceReadiness(inspection),situation=serviceSituation(service,disconnected);
 async function inspect(){
  if(inspectionBusy.current)return;inspectionBusy.current=true;setInspecting(true);setInspectionError('');
  try{const value=await latest.current.request('/api/readiness');if(alive.current)setInspection(value);}
  catch(e){if(alive.current)setInspectionError((e as Error).message);}
  finally{inspectionBusy.current=false;if(alive.current)setInspecting(false);}
 }
 useEffect(()=>{if(outcome.status==='failed'){setStopping(false);setConfirmed(false);read();}},[outcome.status]);
 async function stop(){
  if(stopping||!confirmed||serviceBlocked(service))return;
  const id=crypto.randomUUID().replaceAll('-','');setRequestId(id);setStopping(true);setConfirmed(false);
  try{await p.request('/api/actions/stop_workbench',undefined,{request_id:id,revision:service!.stop_revision});await p.refresh().catch(()=>{});}
  catch(e){setError((e as Error).message);if(!(e as Data).uncertain){setStopping(false);setRequestId(null);}else setDisconnected(true);}
 }
 return <><div className="page-intro compact"><div><span className="eyebrow">SERVICE & RUNTIME</span><h1>{ui("清楚知道，当前运行着什么。")}</h1><p>{ui("服务信息每3秒刷新；后台页面暂停读取。停止结果依据原操作回执。")}</p></div><AsyncButton feedback={{pending:reading}} onClick={read}>{ui("刷新服务状态")}</AsyncButton></div>
  {error&&<p className="inline-notice error-text" role="alert">{error}</p>}
  <section className="native-card"><ProviderSettings request={p.request} language={getUiLanguage()}/></section>
  <GitHubPushStatus value={p.hub.github_push} navigate={p.navigate}/>
  <div className="overview-grid"><section className="native-card"><header><h2>{ui("当前服务")}</h2><span className="caption">{situation.label}</span></header><p>{situation.detail}</p><p><strong>{ui("下一步：")}</strong>{situation.next}</p><dl className="property-list"><dt>{ui("服务状态")}</dt><dd>{situation.label}</dd><dt>{ui("模型配置")}</dt><dd>{p.state?.provider||ui("启动时明确指定")}</dd><dt>{ui("请求总上限")}</dt><dd>{service?.request_budget??'—'}</dd></dl><Evidence value={service} title={ui("实际服务信息与版本")}/></section>
  <section className="native-card"><header><h2>{ui("安全停止工作台")}</h2></header><p>{ui("先结束维护和历史补取，并暂停监控。当前连接不能单独证明服务已停止。")}</p><label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("我确认停止这个工作台服务")}</label><AsyncButton feedback={{pending:stopping&&!['acknowledged','failed'].includes(outcome.status),uncertain:outcome.status==='unknown'}} className="danger" disabled={!confirmed||stopping||disconnected||serviceBlocked(service)} onClick={stop}>{ui("停止当前工作台")}</AsyncButton>{outcome.message&&<p className={'inline-notice '+(outcome.status==='failed'?'error-text':'')} role="status">{outcome.message}</p>}{serviceBlocked(service)&&<p className="muted">{ui("当前活动条件尚不允许停止，完成后可刷新核对。")}</p>}</section></div>
  <section className="native-card" aria-label={ui("本地安装与模型配置检查")}><header><h2>{ui("安装与模型配置")}</h2><AsyncButton feedback={{pending:inspecting}} disabled={inspecting||disconnected||stopping} onClick={inspect}>{ui("检查本地运行条件")}</AsyncButton></header>
   <p><strong>{readiness.label}</strong></p><p>{ui("检查程序依赖、Pi 运行时、网页文件，以及新任务默认模型配置格式；既有任务和监控继续原配置。本项不联网、不调用模型，不消耗模型请求额度。")}</p>
   <p><strong>{readiness.modelLabel}</strong>{ui("。配置格式通过后，实际模型响应仍需另行明确授权验证。")}</p>
   {readiness.checkedAt&&<p className="caption">{ui("最近本地检查：")}{readiness.checkedAt}{readiness.stale?ui(" · 结果已过期"):''}</p>}
   {inspectionError&&<p className="inline-notice error-text" role="alert">{ui("本地检查未完成：")}{inspectionError}</p>}
   {readinessGroups.map(([group,title])=><div key={group} className="issue-section"><h3>{title}</h3>{readiness.checks.filter((row:Data)=>row.group===group).length?readiness.checks.filter((row:Data)=>row.group===group).map((row:Data)=><div key={row.id}><p><strong>{readinessDisplayText(row,'label')}</strong> · {row.status==='ready'?ui("本地检查通过"):row.status==='blocked'?ui("需要处理"):ui("尚未核验")}</p><p>{readinessDisplayText(row,'detail')}</p>{row.next_action&&<p><strong>{ui("下一步：")}</strong>{readinessDisplayText(row,'next_action')}</p>}</div>):<p className="muted">{ui("尚未取得此项检查结果。点击“检查本地运行条件”读取当前本地条件。")}</p>}</div>)}
  </section>
  <ModelProbeControls key={inspection?.checked_at||'unchecked'} {...p}/>
  <section className="native-card"><header><h2>{ui("独立维护服务")}</h2></header><p>{ui("嵌入会话仅在原服务声明可停止时提供入口；它与整个工作台分别管理。")}</p><StandaloneStop {...p}/></section>
  <section className="native-card"><header><h2>{ui("支持范围")}</h2></header><p>{ui("原 Pi、隔离候选、人工审批、预算恢复和显式 Issue 交付沿用原控制器。目录提交监控与持续 Issue 首次接单需要先确认维护规则、执行权限和有效模型许可；发布与合并另行批准。模型热切换、工具扩展及通用 OS 沙箱仍以各页的接入说明为准。")}</p><button onClick={p.openOriginal}>{ui("打开原任务运行信息")}</button></section>
 </>;
}
export function ModelProbeControls(p:Props){
 const [preview,setPreview]=useState<Data|null>(null),[confirmed,setConfirmed]=useState(false),[operation,setOperation]=useState<Data|null>(null),[message,setMessage]=useState('');
 const sending=useRef(false),latest=useRef(p);latest.current=p;
 const scope=JSON.stringify([p.record?.id,p.record?.api_prefix,p.record?.token,p.state?.repository,p.state?.provider,
  p.state?.request_budget,p.state?.requests_used,p.state?.requests_reserved_unknown,p.state?.runtime?.build_sha256,
  p.state?.runtime?.code_changed,p.state?.model_connectivity?.available]);
 const currentScope=useRef(scope);currentScope.current=scope;
 const available=Boolean(p.record&&p.state&&p.state.repository===p.record.repository&&p.state.model_connectivity?.available);
 const busy=Boolean(operation||p.state?.active||p.state?.runtime?.code_changed);
 useEffect(()=>{setPreview(null);setConfirmed(false);},[scope]);
 // A queue receipt belongs to its original workspace; changing selection never
 // carries a preview, checkbox, pending request or async response into another.
 useEffect(()=>{setOperation(null);setMessage('');},[p.record?.id,p.record?.api_prefix,p.record?.token]);
 function complete(job:Data,op:Data){
  if(job.status==='running')return;
  setOperation(null);setConfirmed(false);
  if(job.status==='finished'&&op.action==='model_probe_preview'){
   const value=job.result?.model_probe_preview;
   if(value?.request_count===1&&value?.proves==='model_response_only'&&value?.repair_verified===false){
    setPreview({...value,ui_scope:op.scope});setMessage(ui("预览已读取。请核对配置和原额度，再单独确认。"));return;
   }
  }
  setPreview(null);
  if(job.status==='finished'&&job.result?.model_connectivity?.status==='completed')setMessage(ui("已收到一次模型响应。此结果不证明修复正确。"));
  else setMessage(job.error||ui("探测未完成；原额度与未知结果保留，不会自动重发。"));
 }
 useEffect(()=>{
  if(!operation)return;
  const job=p.state?.jobs?.find((value:Data)=>value.id===operation.id);
  if(job)complete(job,operation);
 },[p.state?.jobs,operation]);
 const fresh=Boolean(preview&&preview.ui_scope===scope&&preview.request_budget===p.state?.request_budget
  &&preview.requests_used===p.state?.requests_used&&preview.requests_reserved_unknown===p.state?.requests_reserved_unknown);
 async function send(action:string){
  if(!available||busy||sending.current||(action==='model_probe'&&(!fresh||!confirmed)))return;
  sending.current=true;const id=crypto.randomUUID().replaceAll('-',''),op={id,action,scope};
  setOperation(op);setConfirmed(false);setMessage('');
  if(action==='model_probe_preview')setPreview(null);
  const record=p.record!;
  try{
   const job=await p.request(record.api_prefix+'actions/'+action,record.token,
    {request_id:id,...(action==='model_probe'?{revision:preview!.revision}:{})});
   if(currentScope.current===op.scope){complete(job,op);await latest.current.refresh();}
  }catch(e){
   if(currentScope.current===op.scope){
    setPreview(null);setConfirmed(false);
    if((e as Data).uncertain){setMessage(ui("提交结果未知，正在读取原操作记录；不会自动重发。"));await latest.current.refresh().catch(()=>{});}
    else{setOperation(null);setMessage((e as Error).message);}
   }
  }finally{sending.current=false;}
 }
 const checks=p.state?.model_connectivity?.checks||[];
 return <section className="native-card" aria-label={ui("单次模型连通性检查")}><header><h2>{ui("单次模型响应检查")}</h2><button disabled={!available||busy} onClick={()=>send('model_probe_preview')}>{ui("预览一次模型请求")}</button></header>
  <p>{ui("仅使用当前仓库原会话、原队列和原模型额度。预览不调用模型；单独确认后最多请求一次，可能收费，不重试。仅证明模型响应，不证明修复正确。")}</p>
  {!p.record?<p className="inline-notice">{ui("请先连接原仓库，再检查模型响应。")}<button onClick={p.connect}>{ui("连接仓库")}</button></p>:!available&&<p className="inline-notice">{ui("当前原会话未提供独立探测许可。监控或共享额度需回到原控制器处理。")}</p>}
  {fresh&&<div className="issue-section"><dl className="property-list"><dt>{ui("明确配置的地址")}</dt><dd>{preview!.endpoint}</dd><dt>{ui("模型")}</dt><dd>{preview!.model}</dd><dt>{ui("配置标识")}</dt><dd>{preview!.config_identity||ui("显式本机合成端点")}</dd><dt>{ui("本次请求")}</dt><dd>{ui("1 次")}</dd><dt>{ui("原额度")}</dt><dd>{ui("总上限 ")}{preview!.request_budget}{ui(" · 已用 ")}{preview!.requests_used}{ui(" · 未知占用 ")}{preview!.requests_reserved_unknown}{ui(" · 剩余 ")}{preview!.requests_remaining}</dd></dl>
   <label className="check-label"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e=>setConfirmed(e.target.checked)}/>{ui("我确认使用当前配置和原额度，发送这一次可能收费的模型请求")}</label><button disabled={!confirmed||busy} onClick={()=>send('model_probe')}>{ui("确认发送一次模型请求")}</button>
   <p className="muted">{ui("切换仓库、额度、服务或重新检查配置后，需重新预览并确认。配置在后台变化时，服务端拒绝旧预览。")}</p></div>}
  {operation&&<p role="status">{operation.action==='model_probe_preview'?ui("正在读取原配置与额度…"):ui("原请求已提交，正在核对模型响应与消费回执…")}</p>}
  {message&&<p role="status">{message}</p>}
  {checks.map((check:Data)=><div key={check.id} className="issue-section"><p><strong>{check.status==='completed'?ui("已收到模型响应"):check.status==='unknown'?ui("模型结果未知"):ui("模型检查尚未完成")}</strong> · {check.model} · {check.started_at}</p><p>{check.status==='completed'?ui("原账已计入一次请求；不证明修复正确。"):ui("保留原请求与占用额度，不会自动重发。")}</p><Evidence value={check} title={ui("原模型检查回执")}/></div>)}
 </section>;
}
function StandaloneStop(p:Props){
 const [confirmed,setConfirmed]=useState(false),[message,setMessage]=useState(''),[id,setId]=useState<string|null>(null),sending=useRef(false);
 const [unknown,setUnknown]=useState(false);
 const control=p.state?.service_control;
 const job=p.state?.jobs?.find((j:Data)=>j.id===id);
 const status=job?.status==='failed'?(job.error||job.result?.error||ui("独立服务停止未完成")):job?.status==='finished'?ui("已确认独立服务停止回执；请另行核对连接。"):message;
 async function stop(){if(!control?.can_stop||!p.record||!confirmed||sending.current||id)return;sending.current=true;setUnknown(false);const requestId=crypto.randomUUID().replaceAll('-','');setId(requestId);try{await p.request(p.record.api_prefix+'actions/stop_service',p.record.token,{request_id:requestId,revision:control.stop_revision});setMessage(ui("已受理，正在核对独立服务的原回执。"));await p.refresh();}catch(e){setMessage((e as Error).message);setUnknown(Boolean((e as Data).uncertain));if(!(e as Data).uncertain)setId(null);}finally{sending.current=false;setConfirmed(false);}}
 return <><label className="check-label"><input type="checkbox" disabled={!control?.can_stop} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("确认停止当前独立维护服务")}</label><AsyncButton feedback={{pending:Boolean(id&&!['finished','failed'].includes(job?.status)),uncertain:unknown}} disabled={!control?.can_stop||Boolean(p.state?.active)||Boolean(id&&job?.status!=='failed')||(!confirmed&&job?.status!=='failed')} onClick={()=>{if(job?.status==='failed'){setId(null);setConfirmed(false);p.refresh();}else stop();}}>{job?.status==='failed'?ui("重新核对独立服务停止条件"):ui("停止独立维护服务")}</AsyncButton>{!control?.can_stop&&<p className="muted">{ui("当前会话未提供独立停止能力。")}</p>}{status&&<p role="status">{status}</p>}</>;
}
