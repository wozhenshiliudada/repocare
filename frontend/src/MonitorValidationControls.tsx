import {usageText} from './request-usage.mjs';
import {ui} from './ui-language.mjs';
import {ValidationClarification} from './ValidationClarification';
import React,{useEffect,useRef,useState} from 'react';
import {Evidence,Props,useOperation} from './WorkbenchPages';
import {latestResult} from './operation-model.mjs';

type Data=Record<string,any>;
export function MonitorValidationControls({p,event}:{p:Props;event:Data}){
 const validation=event.validation,monitor=(p.hub?.monitors||[]).find((m:Data)=>m.id===event.monitor_id);
 const identity=JSON.stringify([p.record?.id,p.record?.repository,p.record?.api_prefix,p.record?.token,event.monitor_id,event.id,event.sha,monitor?.spec?.path]);
 const [draft,setDraft]=useState({identity,text:validation?.request_text||''}),[proposalConsent,setProposalConsent]=useState(''),
  [reviewConsent,setReviewConsent]=useState(''),[stopPending,setStopPending]=useState(false),[stopMessage,setStopMessage]=useState(''),[stopHeld,setStopHeld]=useState('');
 const sending=useRef(false),stopping=useRef(false),submitted=useRef('');
 const op=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh);
 const requestText=validation?.request_text??(draft.identity===identity?draft.text:'');
 const validRequest=requestText.trim().length>0&&requestText.length<=2000;
 const result=latestResult(p.hub,'monitor_validation_preview',(r:Data)=>r.preview?.monitor_id===event.monitor_id&&
  r.preview.event_id===event.id&&r.preview.request_text===requestText);
 const preview=result?.preview;
 const clarificationPreview=latestResult(p.hub,'monitor_validation_clarification_preview',
  (r:Data)=>r.preview?.parent_id===validation?.id);
 const proposalKey=JSON.stringify([identity,result,monitor?.root_rules?.sha256,monitor?.model_authorization,
  monitor?.request_budget,monitor?.requests_used,monitor?.requests_reserved_unknown,monitor?.requests_remaining,p.hub?.runtime]);
 const reviewKey=JSON.stringify([identity,validation?.id,validation?.revision,validation?.status,validation?.contract]);
 const stopKey=JSON.stringify([identity,validation?.stop_revision||validation?.revision]);
 useEffect(()=>{setProposalConsent('');setReviewConsent('');},[proposalKey,reviewKey,requestText]);
 useEffect(()=>{setStopMessage('');},[identity]);
 const original={monitor_id:event.monitor_id,event_id:event.id};
 async function send(action:string,payload:Data,allowed=true){
  if(op.busy||sending.current||!allowed)return;
  sending.current=true;setProposalConsent('');setReviewConsent('');
  try{return await op.send(action,payload);}finally{sending.current=false;}
 }
 async function propose(){
  if(!preview||preview.phase?.existing||validation||!event.can_propose_validation||proposalConsent!==proposalKey||submitted.current===proposalKey)return;
  if(op.busy||sending.current)return;
  submitted.current=proposalKey;
  return send('monitor_validation_propose',{preview,revision:result.revision,confirmed:true});
 }
 async function stop(){
  if(stopping.current||stopHeld===stopKey||!validation||validation.status!=='started')return;
  stopping.current=true;setStopPending(true);setStopHeld(stopKey);setStopMessage('');setProposalConsent('');setReviewConsent('');
  try{
   await p.request('/api/actions/monitor_validation_stop',undefined,{...original,revision:validation.stop_revision||validation.revision,
    request_id:crypto.randomUUID().replaceAll('-','')});
   setStopMessage(ui("停止已提交；只读提议阶段将协作取消，请核对原任务回执。已经发生的调用与消费保留。"));
   await p.refresh();
  }catch(e){if(!(e as Data).uncertain)setStopHeld('');setStopMessage((e as Data).uncertain?ui("停止结果未知，请核对原任务回执；不会自动重发。"):(e as Error).message);}
  finally{stopping.current=false;setStopPending(false);}
 }
 if(!event.can_propose_validation&&!validation)return null;
 const status=validation?.status,phase=preview?.phase,budget=phase?.budget||preview?.budget;
 return <section aria-label={ui("监控事件验收案例")} className="inline-notice"><h4>{ui("补充业务验收案例（可选）","Add business acceptance cases (optional)")}</h4>
  {event.can_prepare_repair&&!validation&&<p>{ui("已有检查失败时，可以留空这里，直接在下方预览修复范围。Agent会使用已发现的失败证据调查，无需你先描述问题。","When existing checks fail, leave this blank and preview the repair scope below. The agent investigates the detected failure evidence; a problem description is not required.")}</p>}
  <p>{ui("没有现成检查，或已有测试通过但需求仍未满足时，写下业务期望。Agent会在原授权范围内自己寻找实现位置，提出输入和预期结果；无需填写文件名、JSON或测试命令。")}</p>
  <label>{ui("本次业务期望（1–2000字）")}<textarea data-form-assist-draft="true" maxLength={2000} value={requestText} readOnly={Boolean(validation)} disabled={Boolean(validation)||op.busy}
   placeholder={ui("例如：空购物车结算时应说明没有商品；折扣后金额不得小于零。")}
   onChange={e=>{if(validation)return;setDraft({identity,text:e.target.value});setProposalConsent('');setReviewConsent('');}}/></label>
  {validation&&<p>{ui("原需求已固定在本次提议中，后续审阅和环境准备沿用这一版本。")}</p>}
  {!validation&&<button disabled={op.busy||!validRequest||!event.can_propose_validation} onClick={()=>send('monitor_validation_preview',
   {...original,request_text:requestText},validRequest&&event.can_propose_validation&&!validation)}>{ui("预览案例提议与原请求额度")}</button>}
  {!validation&&phase?.existing&&<p>{ui("已有本次原案例提议；请核对刷新后的原事件记录，不重复调用模型。")}</p>}
  {!validation&&preview&&!phase?.existing&&<><p>{ui("预览和展示不调用模型。确认提议将使用当前模型和原账，可能产生费用；只读阶段不运行项目代码或修改文件。")}</p>
   <p>{usageText(budget)}{ui("。请求数不等于费用金额。",". Request counts are not costs.")}</p>
   <Evidence value={phase} title={ui("原模型、只读范围与共享额度")}/>
   <label className="check-label"><input type="checkbox" checked={proposalConsent===proposalKey} disabled={op.busy}
    onChange={e=>setProposalConsent(e.target.checked?proposalKey:'')}/>{ui("允许Agent在上述原额度内提议验收案例（可能收费）")}</label>
   <button className="primary" disabled={op.busy||proposalConsent!==proposalKey||submitted.current===proposalKey} onClick={propose}>{ui("让Agent提议验收案例")}</button></>}
  {op.status}{stopMessage&&<p role="status">{stopMessage}</p>}
  {validation&&<><p><strong>{status==='accepted'?ui("案例已审阅"):status==='awaiting_validation_review'?ui("案例已提出，等待你核对"):status==='clarification_needed'?ui("需求需要补充"):status==='rejected'?ui("案例已拒绝"):status==='started'?ui("只读案例提议进行中"):ui("案例提议未完整结束，原调用与消费保留")}</strong></p>
   {status==='clarification_needed'&&<ValidationClarification key={validation.id} validation={validation} preview={clarificationPreview} original={original} prefix="monitor_validation" disabled={op.busy} send={send}/>}
   {validation.proposal&&<><p>{ui("要验证：")}{validation.proposal.requirement}</p><p>{ui("依据：")}{validation.proposal.rationale}</p><p>{ui("限制或待澄清：")}{validation.proposal.limitations}</p></>}
   {validation.contract?.cases?.map((c:Data,index:number)=><article key={c.id||index} className="issue-section"><h5>{ui("案例 ")}{index+1}</h5>
    <p>{ui("输入：")}<code>{JSON.stringify(c.args)}</code>{Object.keys(c.kwargs||{}).length>0&&<>{ui("；命名参数 ")}<code>{JSON.stringify(c.kwargs)}</code></>}</p>
    <p>{ui("预期：")}{c.expect?.kind==='exception'?<>{ui("应报告 ")}{c.expect.name}</>:<code>{JSON.stringify(c.expect?.value)}</code>}</p>
    {c.missing_behavior&&<p>{ui("这是需求中明确要求、当前尚未提供的行为。")}</p>}
    <details><summary>{ui("查看Agent选择的代码位置")}</summary><p>{c.module} · {c.callable}</p></details></article>)}
   {status==='awaiting_validation_review'&&<><p>{ui("确认只固定本次输入与预期。执行项目、修改方案与发布仍沿原维护规则和各自审批。")}</p>
    <label className="check-label"><input type="checkbox" checked={reviewConsent===reviewKey} disabled={op.busy} onChange={e=>setReviewConsent(e.target.checked?reviewKey:'')}/>{ui("这些输入与预期准确表达了我的需求")}</label>
    <button className="primary" disabled={op.busy||reviewConsent!==reviewKey} onClick={()=>send('monitor_validation_review',{...original,revision:validation.revision,decision:'accept'},reviewConsent===reviewKey)}>{ui("确认本次验收案例")}</button>
    <button disabled={op.busy} onClick={()=>send('monitor_validation_review',{...original,revision:validation.revision,decision:'reject'})}>{ui("拒绝这些案例")}</button></>}
   {['started','interrupted_no_replay'].includes(status)&&<><p>{ui("不会因为刷新、重启或结果未知而自动再次调用模型。核对只取回原已提交且已结算的Native结果；未知消费继续保留。")}</p>
    <button disabled={op.busy} onClick={()=>send('monitor_validation_reconcile',{...original,revision:validation.revision})}>{ui("核对原会话并取回案例")}</button></>}
   {status==='started'&&<button disabled={stopPending||stopHeld===stopKey} onClick={stop}>{ui("停止本次只读案例提议")}</button>}
   {status==='accepted'&&!validation.resume&&<><p>{ui("下一步会按原目录维护许可准备环境并运行修改前基线；此按钮不会自动启动模型修复。")}</p>
    <button disabled={op.busy} onClick={()=>send('monitor_validation_prepare',{...original,revision:validation.revision})}>{ui("按原许可准备环境与基线")}</button></>}
   {validation.resume&&<p>{ui("原环境准备状态：")}{validation.resume.status}{ui("；结果：")}{validation.resume.result_status||ui("尚待原回执")}{ui("。保留原准备记录，不重复运行；后续修复使用独立确认。")}</p>}
  </>}
 </section>;
}
