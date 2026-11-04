import {ui} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useEffect,useState} from 'react';
import {Badge,Evidence,useOperation} from './WorkbenchPages';
import {TestTable,CheckComparison} from './EvidenceView';
import {ActivityTimeline} from './ActivityTimeline';
import {RunOperations} from './RunOperations';
type Data=Record<string,any>;
export function TaskReport({record,state,current,request,refresh,openOriginal}:{record:Data;state:Data;current:Data|null;request:any;refresh:()=>Promise<unknown>;openOriginal:()=>void}){
 const op=useOperation(record,state,request,refresh),[confirmed,setConfirmed]=useState(false);
 const run=current?.runs?.at(-1),plan=current?.plans?.findLast((p:Data)=>p.status==='awaiting_approval');
 const review=run?.review,progress=state.jobs?.find((j:Data)=>j.id===state.active)?.progress;
 const recovery=(state.recovery_cards||[]).find((c:Data)=>c.kind==='plan'&&c.id===plan?.id);
 const planBlocked=Boolean(recovery&&!['review_plan','not_bound'].includes(recovery.status));
 useEffect(()=>{setConfirmed(false);},[run?.revision,review?.review_sha256,current?.id]);
 const stale=Boolean(state.runtime?.code_changed),disabled=op.busy||stale;
 const memoryLoads=[run?.planning_origin?.provider_evidence?.memory_load,...(run?.rounds||[]).map((r:Data)=>r.provider_evidence?.memory_load)].filter(value=>value!==undefined&&value!==null);
 return <section className="task-evidence-panel" aria-label={ui("当前任务的审批与检查证据")}>
  {op.status}
  {current&&<Evidence value={{selection:current.selection,plans:current.plans,runs:current.runs}} title={ui("原问题、方案与运行历史（仅当前原记录提供的字段）")}/>}
  {recovery&&<Evidence value={recovery} title={ui("原方案检查点、身份绑定与恢复限制")}/>}
  {(current||progress)&&<ActivityTimeline state={state} run={run} plan={current?.plans?.at(-1)} progress={progress}/>}
  {current?.selection?.kind==='task'&&<Evidence value={(state.recovery_cards||[]).find((c:Data)=>c.kind==='task'&&c.id===current.selection.id)} title={ui("原生任务检查点与当前恢复限制")}/>}
  {state.active&&<div className="inline-notice"><div className="card-foot"><strong>{ui("原任务正在执行")}</strong><Badge value="running"/></div>{progress&&<Evidence value={progress} title={ui("实际阶段与工具活动")}/>}<AsyncButton feedback={op.feedback} disabled={Boolean(op.pending)||!state.task_control?.can_stop} onClick={()=>op.send('stop_job',{job_id:state.task_control.job_id,revision:state.task_control.revision})}>{ui("请求停止当前任务")}</AsyncButton></div>}
  {plan&&<div className="native-card"><header><h2>{ui("审阅这个修改方案")}</h2><Badge value={plan.status}/></header><p>{plan.plan?.risk}</p><code className="exact-fingerprint">{plan.fingerprint}</code><div className="button-row">{!plan.claimed?<AsyncButton feedback={op.feedback} disabled={disabled||planBlocked} onClick={()=>op.send('claim_plan',{plan_id:plan.id,revision:plan.revision})}>{ui("核验并认领此历史方案")}</AsyncButton>:<><AsyncButton feedback={op.feedback} className="primary" disabled={disabled||planBlocked||plan.repeated_failed_patch} onClick={()=>op.send('decision',{plan_id:plan.id,fingerprint:plan.fingerprint,decision:'approve'})}>{ui("批准此方案并测试")}</AsyncButton><AsyncButton feedback={op.feedback} disabled={disabled} onClick={()=>op.send('decision',{plan_id:plan.id,fingerprint:plan.fingerprint,decision:'reject'})}>{ui("拒绝此方案")}</AsyncButton></>}</div><p className="muted">{ui("批准绑定当前方案与指纹，不包含远端发布或合并。")}</p></div>}
  {run&&<div className="native-card"><header><h2>{ui("运行证据")}</h2><Badge value={run.status}/></header><dl className="property-list"><dt>{ui("原运行")}</dt><dd><code>{run.run_id}</code></dd><dt>{ui("源保护")}</dt><dd>{run.source_unchanged===true?ui("已记录源工作区保持"):run.source_unchanged===false?ui("源有变化，请核验"):ui("查看原报告证据")}</dd></dl><TestTable tests={run.baseline_tests||[]} title={ui("修改前基线")}/><TestTable tests={run.candidate_tests||[]} title={ui("候选检查")}/>{(run.rounds||[]).map((round:Data,i:number)=><Evidence key={i} value={round} title={ui("第 ")+(i+1)+ui(" 轮 · 真实检查与模型回执")}/>)}
   <div className="button-row">{!run.claimed?<AsyncButton feedback={op.feedback} disabled={disabled||run.status==='evidence_invalid'} onClick={()=>op.send('claim_run',{run_id:run.run_id,revision:run.revision})}>{ui("核验并认领原运行")}</AsyncButton>:run.status==='candidate_ready'&&!review?<AsyncButton feedback={op.feedback} className="primary" disabled={disabled} onClick={()=>op.send('prepare',{run_id:run.run_id,revision:run.revision,retry_aborted:false})}>{ui("封装候选并重新测试")}</AsyncButton>:null}<button onClick={openOriginal}>{ui("完整报告与恢复操作")}</button></div>
   {review&&<div className="approval-box"><h3>{ui("本地分支交付")}</h3><Badge value={review.status}/><dl className="property-list"><dt>{ui("目标分支")}</dt><dd>{review.core?.target_branch||ui("尚未完成")}</dd><dt>{ui("封装提交")}</dt><dd><code>{review.core?.commit||ui("尚未完成")}</code></dd><dt>{ui("审批指纹")}</dt><dd><code>{review.review_sha256||ui("尚无可批准指纹")}</code></dd></dl><TestTable tests={review.tests||[]} title={ui("封装后复测")}/>{['prepared','awaiting_approval','rejected'].includes(review.status)&&review.hash_matches===true&&review.diff_hash_matches===true&&run.claimed&&<><label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("确认仅创建这个本地分支，保留当前源分支")}</label><AsyncButton feedback={op.feedback} className="primary" disabled={disabled||!confirmed} onClick={()=>{setConfirmed(false);op.send('publish',{run_id:run.run_id,revision:run.revision,fingerprint:review.review_sha256});}}>{ui("批准这个本地分支")}</AsyncButton></>}<Evidence value={review}/></div>}
  </div>}
 {run&&<><CheckComparison baseline={run.baseline_tests||[]} candidate={run.candidate_tests||[]}/>{memoryLoads.length?<Evidence value={memoryLoads} title={ui("本次实际传入的记忆清单（装载不代表正确使用）")}/>:<p className="muted">{ui("原运行未提供实际记忆装载回执，不能据此判断是否使用了记忆。")}</p>}<RunOperations key={run.run_id} record={record} state={state} request={request} refresh={refresh} run={run}/></>}
 </section>;
}
