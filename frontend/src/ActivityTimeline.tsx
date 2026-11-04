import {ui,uiLabels} from './ui-language.mjs';
import React,{useState} from 'react';
import {activityEvidence} from './evidence-model.mjs';
type Data=Record<string,any>;
const eventNames:Record<string,string>=uiLabels({agent_start:'开始Agent回合',agent_end:'Agent回合结束',assistant_end:'模型响应结束',tool_execution_start:'工具开始',tool_execution_end:'工具结束',maintenance_tool:'维护工具回执'});
export function ActivityTimeline({state,run,execution,plan,progress}:{state:Data|null|undefined;run?:Data;execution?:Data;plan?:Data;progress?:Data}){
 const [view,setView]=useState('events'),value=activityEvidence(state,run,execution,plan);
 if(progress)value.records=[...value.records,{label:ui("当前仓库活动快照（不推定属于所选任务）"),evidence:progress}];
 return <section className="native-card" aria-label={ui("原任务活动与用量")}><header><h3>{ui("原任务活动与用量")}</h3></header><p className="muted">{ui("规划调用与执行轮次分别显示。仅使用此任务已关联的回执；未提供的时间、token或工具活动保持未知。")}</p>
 <div className="page-tabs"><button aria-pressed={view==='events'} onClick={()=>setView('events')}>{ui("阶段与工具")}</button><button aria-pressed={view==='usage'} onClick={()=>setView('usage')}>{ui("请求与token")}</button></div>
 {execution&&<p>{ui("Issue执行：")}{execution.status}{ui(" · 开始 ")}{execution.created_at||ui("未提供")}{ui(" · 结束 ")}{execution.finished_at||ui("未提供")}</p>}
 {progress&&<p>{ui("当前仓库阶段：")}{progress.phase||ui("未提供")}{ui(" · 快照更新 ")}{progress.updated_at||ui("未提供")}{ui("。活动尚未完成时不把观察计数当最终结算。")}</p>}
 {view==='events'?<><ol className="receipt-timeline">{value.events.map((event:Data,i:number)=><li key={i}><strong>{event.stage||ui("原记录事件")}</strong><span> · {event.time||ui("时间未提供")}</span>{(event.reason||event.error)&&<p>{event.reason||event.error}</p>}</li>)}</ol>
 {value.records.map((row:Data,i:number)=><details key={i} className="evidence"><summary>{row.label} · {row.evidence.events?.length??0}{ui(" 条已提供工具/Agent事件")}</summary>{(row.evidence.events||[]).map((event:Data,n:number)=><p key={n}>{n+1}. {eventNames[event.type]||event.type} {event.tool||''} {event.path||''} {event.is_error?ui("· 工具报告错误"):''}</p>)}{(!row.evidence.events_complete||row.evidence.events_dropped>0)&&<p>{ui("活动记录不完整或未提供完整性证明；省略 ")}{row.evidence.events_dropped??ui("未知")}{ui(" 条，不能据此推断完整过程。")}</p>}<pre>{JSON.stringify(row.evidence,null,2)}</pre></details>)}
 {!value.events.length&&!value.records.length&&<p className="muted">{ui("当前没有此任务的阶段或调用回执。Issue可显式读取原运行结果；此页面不会自动重放操作。")}</p>}</>:<>{value.records.map((row:Data,i:number)=><article key={i}><h4>{row.label}</h4><dl className="property-list"><dt>{ui("请求次数")}</dt><dd>{row.evidence.requests??ui("未知")}</dd><dt>{ui("已开始请求")}</dt><dd>{row.evidence.requests_started??ui("未提供")}</dd><dt>{ui("工具次数")}</dt><dd>{row.evidence.tool_calls??ui("未提供")}</dd></dl>{row.evidence.usage?.length?<div className="test-table-scroll"><table><thead><tr><th>{ui("原usage条目")}</th><th>{ui("输入")}</th><th>{ui("输出")}</th><th>{ui("缓存读取")}</th><th>{ui("缓存写入")}</th><th>{ui("总token")}</th></tr></thead><tbody>{row.evidence.usage.map((usage:Data,n:number)=><tr key={n}><td>{n+1}</td>{['input','output','cacheRead','cacheWrite','totalTokens'].map(key=><td key={key}>{usage[key]??ui("未提供")}</td>)}</tr>)}</tbody></table></div>:<p className="muted">{ui("该调用没有提供token回执，不记作0。")}</p>}</article>)}{!value.records.length&&<p>{ui("尚无该任务的已关联模型调用回执。")}</p>}<p className="muted">{ui("这些是原供应商usage记录，不是已核验账单金额。投影可能缺少部分轮次字段；不跨记录汇总来推断总费用。")}</p></>}
 </section>;
}
