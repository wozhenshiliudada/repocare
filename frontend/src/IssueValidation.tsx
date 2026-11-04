import {ui} from './ui-language.mjs';
import {ValidationClarification} from './ValidationClarification';
import React,{useEffect,useRef,useState} from 'react';
type Data=Record<string,any>;
export function IssueValidation({investigation,validation,preview,clarificationPreview,disabled,prepared,send}:{
 investigation:Data;validation?:Data;preview?:Data;clarificationPreview?:Data;disabled:boolean;prepared:boolean;
 send:(action:string,payload:Data)=>Promise<unknown>;
}){
 const [allowProposal,setAllowProposal]=useState(false),[acceptCases,setAcceptCases]=useState(false);
 const sending=useRef(false);
 useEffect(()=>{setAllowProposal(false);setAcceptCases(false);},[investigation.id,preview?.revision,validation?.revision]);
 const original={investigation_id:investigation.id};
 async function submit(action:string,payload:Data,allowed=true){
  if(disabled||sending.current||!allowed)return;
  sending.current=true;setAllowProposal(false);setAcceptCases(false);
  try{return await send(action,payload);}finally{sending.current=false;}
 }
 return <section className="issue-proposal" aria-label={ui("让Agent提出验收案例")}><h4>{ui("先说清楚，怎样才算修好")}</h4>
  <p>{ui("Agent读取授权范围内的代码，根据问题描述提议输入和预期结果。你不需要填写文件名或测试命令；需求不清楚时会停下来说明。")}</p>
  {!prepared&&!validation&&<button disabled={disabled} onClick={()=>submit('issue_validation_preview',original)}>{ui("预览案例提议与原请求额度")}</button>}
  {!prepared&&!validation&&preview&&!preview.existing&&<div><p>{ui("将使用当前模型和原账，可能产生费用；这次提议可多轮调用，最多使用原剩余 ")}{preview.budget?.requests_remaining??'—'}{ui(" 次请求。此阶段只读，不运行项目代码或修改文件。")}</p>
   <label className="check-label"><input type="checkbox" checked={allowProposal} disabled={disabled} onChange={e=>setAllowProposal(e.target.checked)}/>{ui("允许Agent在上述原额度内提议验收案例")}</label>
   <button className="primary" disabled={disabled||!allowProposal} onClick={()=>submit('issue_validation_propose',{...original,revision:preview.revision},allowProposal)}>{ui("让Agent提议验收案例")}</button></div>}
  {validation&&<div><p><strong>{validation.status==='accepted'?ui("案例已审阅，可以继续准备环境"):validation.status==='awaiting_validation_review'?ui("案例已提出，等待你核对"):validation.status==='clarification_needed'?ui("需求需要补充"):validation.status==='rejected'?ui("案例已拒绝"):validation.status==='started'?ui("提议已登记，查看原任务进度"):ui("提议未完整结束，保留原调用与消费记录")}</strong></p>
   {validation.status==='clarification_needed'&&<ValidationClarification key={validation.id} validation={validation} preview={clarificationPreview} original={original} prefix="issue_validation" disabled={disabled} send={send}/>}
   {validation.proposal&&<><p>{ui("要验证：")}{validation.proposal.requirement}</p><p>{ui("依据：")}{validation.proposal.rationale}</p><p>{ui("限制或待澄清：")}{validation.proposal.limitations}</p></>}
   {validation.contract?.cases?.map((c:Data,index:number)=><article key={c.id} className="issue-section"><h5>{ui("案例 ")}{index+1}</h5>
    <p>{ui("输入：")}<code>{JSON.stringify(c.args)}</code>{Object.keys(c.kwargs||{}).length>0&&<>{ui("；命名参数 ")}<code>{JSON.stringify(c.kwargs)}</code></>}</p>
    <p>{ui("预期：")}{c.expect.kind==='exception'?<>{ui("应报告 ")}{c.expect.name}</>:<code>{JSON.stringify(c.expect.value)}</code>}</p>
    {c.missing_behavior&&<p>{ui("这是需求中明确要求、当前尚未提供的行为。")}</p>}
    <details><summary>{ui("查看Agent选择的代码位置")}</summary><p>{c.module} · {c.callable}</p></details></article>)}
   {validation.status==='awaiting_validation_review'&&<><p>{ui("确认案例只固定本次验收标准。项目执行、修改方案和发布仍受原维护规则及各自审批控制。")}</p>
    <label className="check-label"><input type="checkbox" checked={acceptCases} disabled={disabled} onChange={e=>setAcceptCases(e.target.checked)}/>{ui("这些输入与预期准确表达了我的需求")}</label>
    <button className="primary" disabled={disabled||!acceptCases} onClick={()=>submit('issue_validation_review',{...original,revision:validation.revision,decision:'accept'},acceptCases)}>{ui("确认本次验收案例")}</button>
    <button disabled={disabled} onClick={()=>submit('issue_validation_review',{...original,revision:validation.revision,decision:'reject'})}>{ui("拒绝这些案例")}</button></>}
   {['started','interrupted_no_replay'].includes(validation.status)&&<p>{ui("不会因为刷新、重启或没有收到结果而自动再次调用模型。请查看原任务和请求记录。")}</p>}
   {['started','interrupted_no_replay'].includes(validation.status)&&<><p>{ui("如果原会话已经提交、原请求已经结算，可以核对并取回案例；未知或未提交的结果会继续保留，不重新调用模型。")}</p>
    <button disabled={disabled} onClick={()=>submit('issue_validation_reconcile',{...original,revision:validation.revision})}>{ui("核对原会话并取回案例")}</button></>}
  </div>}
  {prepared&&!validation&&<p>{ui("本次环境与原检查已经固定。保留其原审批，不在执行后替换验收标准。")}</p>}
 </section>;
}
