import {ui} from './ui-language.mjs';
import React,{useRef,useState} from 'react';
type Data=Record<string,any>;
export function ValidationClarification({validation,preview,original,prefix,disabled,send}:{
 validation:Data;preview?:Data;original:Data;prefix:string;disabled:boolean;
 send:(action:string,payload:Data)=>Promise<unknown>;
}){
 const [answer,setAnswer]=useState(''),[consent,setConsent]=useState('');
 const matching=preview?.preview?.parent_id===validation.id&&preview?.preview?.answer===answer;
 const key=JSON.stringify([validation.id,validation.revision,preview,answer]);
 return <section aria-label={ui("补充业务期望")}><p>{ui("请回答上方待澄清的问题。原提议与消费保留；补充后产生新的只读提议，案例和修改方案仍须分别确认。")}</p>
 <label>{ui("补充业务期望")}<textarea data-form-assist-draft="true" value={answer} maxLength={2000} disabled={disabled}
 onChange={e=>{setAnswer(e.target.value);setConsent('');}}/></label>
 <button disabled={disabled||!answer.trim()||answer!==answer.trim()} onClick={()=>send(prefix+'_clarification_preview',
 {...original,revision:validation.revision,answer})}>{ui("预览补充后的提议与原额度")}</button>
 {matching&&<><p>{ui("可能收费；使用原剩余 ")}{preview?.preview?.phase?.budget?.requests_remaining??'—'}{ui(" 次请求，不增加总额度。不修改原需求或旧审批。")}</p>
 <label><input type="checkbox" checked={consent===key} disabled={disabled} onChange={e=>setConsent(e.target.checked?key:'')}/>{ui("确认按上述原额度提出补充后的验收案例")}</label>
 <button disabled={disabled||consent!==key} onClick={()=>{setConsent('');return send(prefix+'_clarify',
 {...original,preview:preview?.preview,revision:preview?.revision,confirmed:true});}}>{ui("补充后继续提议")}</button></>}
 </section>;
}

export function IssueValidationStop({record,control,request,refresh}:{record:Data;control:Data;
 request:(path:string,token:string,body:Data)=>Promise<unknown>;refresh:()=>Promise<unknown>}){
 const sending=useRef(false),[held,setHeld]=useState(''),[message,setMessage]=useState('');
 const key=JSON.stringify([record.id,record.api_prefix,record.token,control.job_id,control.revision]);
 async function stop(){
  if(sending.current||held===key||!control.can_stop)return;
  sending.current=true;setHeld(key);
  try{await request(record.api_prefix+'actions/stop_job',record.token,{request_id:crypto.randomUUID().replaceAll('-',''),
   job_id:control.job_id,revision:control.revision});setMessage(ui("停止已提交；保留原案例提议、会话与请求消费。"));await refresh();}
  catch(error){if(!(error as Data).uncertain)setHeld('');setMessage((error as Data).uncertain?ui("停止结果未知，请核对原任务；不会自动重发。"):(error as Error).message);}
  finally{sending.current=false;}
 }
 return <div><button disabled={!control.can_stop||held===key} onClick={stop}>{ui("停止当前只读案例提议")}</button>{message&&<p role="status">{message}</p>}</div>;
}
