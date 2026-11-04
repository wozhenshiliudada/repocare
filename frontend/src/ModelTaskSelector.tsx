import React,{useEffect,useRef,useState} from 'react';
import {reasoningChoices,selectedReasoning} from './model-reasoning.mjs';
import {ModelReasoningPicker} from './ModelReasoningPicker';

type Props={profiles:any|null;value:string;currentId:string;busy:boolean;budget:number;onChange:(id:string)=>void;onApply:(profileId?:string)=>void|Promise<void>;onRefresh?:()=>void|Promise<any>;onReasoningVariant?:(profileId:string,effort:string)=>Promise<string>;onPendingChange?:(pending:boolean)=>void;contextKey?:string|number;variantState?:'inflight'|'unknown'|null;language?:string};

export function ModelTaskSelector({profiles,value,currentId,busy,budget,onChange,onApply,onRefresh,onReasoningVariant,onPendingChange,contextKey,variantState=null,language='zh-CN'}:Props){
 const ui=(zh:string,en:string)=>language==='en'?en:zh;
 const rows=Array.isArray(profiles?.profiles)?profiles.profiles:[];
 const selected=rows.find((p:any)=>p.id===value),current=rows.find((p:any)=>p.id===currentId);
 const [effort,setEffort]=useState(()=>selectedReasoning(selected)),[working,setWorking]=useState(false),[unknown,setUnknown]=useState(false),[error,setError]=useState('');
 const lock=useRef(false),previousModel=useRef(value),choices=reasoningChoices(selected),blocked=busy||working||unknown||Boolean(variantState);
 const alive=useRef(true),generation=useRef({key:JSON.stringify([contextKey,value,currentId]),version:0}),unknownRef=useRef(false);
 const key=JSON.stringify([contextKey,value,currentId]);if(generation.current.key!==key)generation.current={key,version:generation.current.version+1};
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;generation.current.version++;};},[]);
 useEffect(()=>{if(previousModel.current!==value){previousModel.current=value;setEffort(selectedReasoning(selected));}
  else setEffort(current=>choices.some(row=>row.level===current)?current:selectedReasoning(selected));},[value,profiles?.revision]);
 const available=Boolean(selected?.selectable&&!selected?.archived),variant=Boolean(choices.length&&effort!==selected?.reasoning_effort),changed=Boolean(value&&(value!==currentId||variant));
 useEffect(()=>{onPendingChange?.(changed);},[contextKey,value,currentId,effort,changed,onPendingChange]);
 async function apply(){
  if(lock.current||unknownRef.current||blocked||!available||!changed||choices.length&&!choices.some(row=>row.level===effort))return;
  const version=generation.current.version;
  lock.current=true;setWorking(true);setError('');
  try{let profileId=value;
   if(variant){if(!onReasoningVariant)throw Error('No reasoning variant handler');profileId=await onReasoningVariant(value,effort);if(!alive.current||generation.current.version!==version)return;if(typeof profileId!=='string'||!profileId)throw Error('Invalid variant result');onChange(profileId);}
   if(!alive.current||generation.current.version!==version)return;
   await onApply(profileId);
  }catch(e){if(alive.current&&generation.current.version===version){if((e as any)?.uncertain){unknownRef.current=true;setUnknown(true);}setError(ui('模型会话尚未完成，请刷新核对后再操作。','The model session did not finish. Refresh to check before another action.'));}}
  finally{lock.current=false;if(alive.current)setWorking(false);}
 }
 async function refresh(){
  if(!onRefresh||lock.current||busy||working||variantState==='inflight')return;
  try{const result=onRefresh();if(result&&typeof result.then==='function'){await result;if(alive.current){unknownRef.current=false;setUnknown(false);setError('');}}}
  catch{if(alive.current)setError(ui('未能刷新模型目录，原选择与未知结果保持。','The model directory could not be refreshed. The original selection and unknown result are retained.'));}
 }
 return <section className="task-model-selector-compact" aria-label={ui('新任务使用的模型','Model for new maintenance tasks')}>
  <ModelReasoningPicker profiles={rows} value={value} effort={effort} pending={changed} defaultId={profiles?.default_profile_id} language={language} label={ui('新任务使用的模型','Model for new maintenance tasks')} disabled={blocked||!profiles||rows.length===0} onModelChange={id=>{if(!blocked&&rows.some((row:any)=>row.id===id&&row.selectable&&!row.archived))onChange(id);}} onEffortChange={level=>{if(!blocked&&choices.some(row=>row.level===level))setEffort(level);}} onApply={changed?apply:undefined} applyDisabled={blocked||!available} applyLabel={busy||working?ui('正在准备模型会话…','Preparing model session…'):ui('应用到新任务','Apply to new tasks')}/>
  <details><summary>{ui('模型会话说明','Model session details')}</summary>
  {!choices.length&&<p className="caption">{ui('此模型尚未明确配置思考协议和可选档位，请到设置 → 模型中配置。','This model has no explicit thinking protocol and levels. Configure them in Settings → Models.')}</p>}
  <p className="caption">{ui('仅为新任务选择模型；旧任务的审批、重试和恢复始终使用原模型、原会话与原账。默认模型变化也不改变原监控。','This choice applies to new tasks only. Existing approvals, retries, recovery, and monitors keep their original model, session, and account.')}</p>
  <p className="caption">{ui('同一仓库首次使用不同模型配置时，建立独立、持久的模型请求额度','The first use of another model configuration for this repository creates a separate persistent allowance')}{Number.isFinite(budget)&&budget>0?ui(`（上限 ${budget} 次）`,` (up to ${budget} requests)`):''}{ui('；回到已用过的配置时复用原会话和原余额，不补充或重置旧额度。','; returning to a previous configuration reuses its original session and remaining allowance, without a reset.')}</p>
  {currentId&&<p className="caption">{ui('当前任务会话：','Current task session: ')}{current?.name||currentId}{current?.model?` · ${current.model}`:''}</p>}
  <p className="caption">{ui('应用只准备或重开对应会话，不调用模型。提交新任务时将使用所选配置。','Applying only prepares or reopens the corresponding session. It makes no model request. New tasks use the selected configuration.')}</p>
  {onRefresh&&<button type="button" disabled={busy||working||variantState==='inflight'} onClick={refresh}>{ui('刷新可选模型','Refresh models')}</button>}
  </details>
  {!profiles&&<p role="status">{ui('模型列表尚未读取，请刷新后选择。','Refresh the model list before choosing a configuration.')}</p>}
  {profiles&&rows.length===0&&<p className="inline-notice">{ui('尚无模型配置，请先在设置中添加模型。','Add a model in Settings first.')}</p>}
  {value&&!available&&<p role="alert" className="error-text">{ui('所选模型当前不可用，请刷新模型列表或选择其他配置。','This model is unavailable. Refresh the list or choose another configuration.')}</p>}
  {variantState==='inflight'&&<p role="status">{ui('正在保存思考配置，请等待原操作结果。','Saving the thinking configuration. Wait for the original result.')}</p>}
  {(unknown||variantState==='unknown')&&<p role="status">{ui('保存或准备结果未知。请先只读刷新模型目录核对，再手动应用；不会自动重试。','The save or preparation result is unknown. Refresh the model directory to check, then apply manually. Nothing is retried automatically.')}</p>}
  {error&&<p role="alert" className="error-text">{error}</p>}
 </section>;
}
