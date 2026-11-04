import {ui,uiLabels} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useState} from 'react';
import {Badge,Evidence,Props,useOperation} from './WorkbenchPages';
import {latestResult} from './operation-model.mjs';
type Data=Record<string,any>;
const lists=uiLabels([['triggers','触发条件'],['preconditions','前提'],['steps','步骤'],['verification','验证方式'],['stop_conditions','停止条件']]);
export function KnowledgeEditor(p:Props&{entry:Data}){
 const entry=p.entry,op=useOperation(p.record,p.state,p.request,p.refresh),[draft,setDraft]=useState<Data>(entry.note||{}),[revision]=useState(entry.revision),[selected,setSelected]=useState<string[]>([]),[consent,setConsent]=useState(false);
 const current=p.state?.knowledge_memory?.entries?.find((e:Data)=>e.id===entry.id),stale=current?.revision!==revision;
 const canUse=entry.freshness==='verified'&&entry.lifecycle==='active';
 const correction=op.output?.knowledge_refresh_preview;
 const valid=draft.title?.trim()&&draft.summary?.trim()&&lists.every(([field])=>Array.isArray(draft[field])&&draft[field].length>=1&&draft[field].length<=8&&draft[field].every((v:string)=>v.trim()&&v.length<=500));
 const changeDraft=(patch:Data)=>setDraft(previous=>({...previous,...patch}));
 return <details className="native-editor"><summary>{ui("修订新版本与来源更正")}</summary>{op.status}{stale&&<p className="inline-notice">{ui("原知识版本已变化；草稿保留，请重新进入当前版本后审阅。")}</p>}
  <label>{ui("知识类型")}<select data-form-assist-draft="true" value={draft.kind||'knowledge'} onChange={e=>changeDraft({kind:e.target.value})}><option value="knowledge">{ui("工程知识")}</option><option value="procedure">{ui("排查方法")}</option></select></label><label>{ui("标题")}<input data-form-assist-draft="true" value={draft.title||''} maxLength={160} onChange={e=>changeDraft({title:e.target.value})}/></label><label>{ui("摘要")}<textarea data-form-assist-draft="true" value={draft.summary||''} maxLength={1200} onChange={e=>changeDraft({summary:e.target.value})}/></label>
  {lists.map(([field,title])=><label key={field}>{title}{ui("（每行一项，1—8项）")}<textarea data-form-assist-draft="true" value={(draft[field]||[]).join('\n')} rows={3} onChange={e=>changeDraft({[field]:e.target.value.split('\n')})}/></label>)}
  <AsyncButton feedback={op.feedback} disabled={op.busy||!canUse||stale||!valid} onClick={()=>op.send('knowledge_revise',{knowledge_id:entry.id,revision,candidate:draft})}>{ui("保存为待审阅新版本")}</AsyncButton><p className="muted">{ui("保留旧版本；新版本须独立审阅，不自动恢复旧版或授予执行权。")}</p>
  {entry.can_refresh&&<><h4>{ui("按新经历更正")}</h4><p className="muted">{ui("选择1—3条已接受且有效的经历；先预览，再单独确认使用原额度。")}</p>{(p.state?.experience_memory?.entries||[]).filter((e:Data)=>e.review==='accepted'&&e.freshness==='verified').map((e:Data)=><label className="check-label" key={e.id}><input type="checkbox" checked={selected.includes(e.id)} disabled={!selected.includes(e.id)&&selected.length>=3} onChange={v=>{setConsent(false);setSelected(v.target.checked?[...selected,e.id]:selected.filter(id=>id!==e.id));}}/>{e.issue||e.id}</label>)}<AsyncButton feedback={op.feedback} disabled={op.busy||stale||!selected.length} onClick={()=>{setConsent(false);op.send('knowledge_refresh_preview',{knowledge_id:entry.id,experience_ids:selected});}}>{ui("预览知识更正来源")}</AsyncButton>
  {correction?.revision&&correction.knowledge_id===entry.id&&JSON.stringify(correction.experience_ids)===JSON.stringify(selected)&&<><Evidence value={correction} title={ui("原版本、新来源与原额度预览")}/><label className="check-label"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>{ui("确认用原额度生成更正候选，结果另行审阅")}</label><AsyncButton feedback={op.feedback} disabled={op.busy||stale||!consent} onClick={()=>{setConsent(false);op.send('knowledge_refresh_propose',{knowledge_id:entry.id,experience_ids:selected,revision:correction.revision});}}>{ui("确认生成更正候选")}</AsyncButton></>}
  </>}
 </details>;
}
export function SourceQueries(p:Props){
 const op=useOperation(p.record,p.state,p.request,p.refresh),[issue,setIssue]=useState(''),[query,setQuery]=useState('');
 const source=op.output?.memory_preview||latestResult(p.state,'memory_preview')?.memory_preview;
 const structure=op.output?.structure_preview||latestResult(p.state,'structure_preview')?.structure_preview;
 return <><section className="native-card"><header><h2>{ui("来源记忆查询")}</h2><Badge value={p.state?.source_memory?.status}/></header><Evidence value={p.state?.source_memory} title={ui("实际绑定与来源状态")}/><form className="query-form" onSubmit={e=>{e.preventDefault();op.send('memory_preview',{issue});}}><input data-form-assist-draft="true" aria-label={ui("来源问题")} value={issue} maxLength={2000} placeholder={ui("输入需要查找出处的问题…")} onChange={e=>setIssue(e.target.value)}/><AsyncButton feedback={op.feedback} disabled={op.busy||!issue.trim()||!p.state?.source_memory?.enabled}>{ui("预览此问题的记忆出处")}</AsyncButton></form>{!p.state?.source_memory?.enabled&&<p className="muted">{ui("当前未绑定来源。建库和案例导出可使用下方已接工具；新会话可使用固定绑定工具；既有任务来源与原账保持，不自动换绑。")}</p>}{source&&<Evidence value={source} title={ui("最近已完成的来源摘录、分类、原字节与替代声明")}/>}</section>
 <section className="native-card"><header><h2>{ui("代码结构查询")}</h2><Badge value={p.state?.structure_index?.status}/></header><Evidence value={p.state?.structure_index} title={ui("结构索引固定绑定")}/><form className="query-form" onSubmit={e=>{e.preventDefault();op.send('structure_preview',{query});}}><input data-form-assist-draft="true" aria-label={ui("结构符号或路径")} value={query} maxLength={128} onChange={e=>setQuery(e.target.value)}/><AsyncButton feedback={op.feedback} disabled={op.busy||!query.trim()||!p.state?.structure_index?.enabled}>{ui("查询结构出处")}</AsyncButton></form>{structure&&<Evidence value={structure} title={ui("最近已完成的符号、关系与源码指纹")}/>}</section>{op.status}</>;
}
export function ContextOperations(p:Props){
 const op=useOperation(p.record,p.state,p.request,p.refresh),[confirmed,setConfirmed]=useState(false),value=p.state?.task_compaction;
 const preview=op.output?.compaction_preview||latestResult(p.state,'compaction_preview')?.compaction_preview;
 const current=(value?.tasks||[]).find((t:Data)=>t.task_id===preview?.task_id);
 const fresh=current?.status==='verified'&&current.can_compact&&current.revision===preview?.revision;
 return <section className="native-card"><header><h2>{ui("原任务整理与核对")}</h2></header>{op.status}
 {(value?.tasks||[]).map((task:Data)=><article className="context-task" key={task.task_id}><strong>{ui("任务 ")}{task.task_id.slice(0,12)}</strong><Badge value={task.status}/><p>{task.reason||ui("保持任务创建时的原设置。")}</p><Evidence value={task} title={ui("原检查点与容量依据")}/><AsyncButton feedback={op.feedback} disabled={op.busy||task.status!=='verified'||!task.can_compact} onClick={()=>{setConfirmed(false);op.send('compaction_preview',{task_id:task.task_id});}}>{ui("预览整理此任务")}</AsyncButton></article>)}
 {preview&&<div className="approval-box"><Evidence value={preview} title={ui("本次整理预览与共享额度")}/><label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("确认消耗原额度整理此任务上下文")}</label><AsyncButton feedback={op.feedback} disabled={op.busy||!fresh||!preview.can_compact||!confirmed} onClick={()=>{setConfirmed(false);op.send('compact_task',{task_id:preview.task_id,revision:preview.revision});}}>{ui("确认消耗原额度整理上下文")}</AsyncButton>{!fresh&&<p className="muted">{ui("此预览不匹配当前检查点或额度，请重新预览。")}</p>}</div>}
 {(value?.pending||[]).map((intent:Data)=><details key={intent.id}><summary>{ui("待核验整理 · ")}{intent.task_id.slice(0,12)}</summary><Evidence value={intent}/><p>{ui("不重发摘要请求，不清除未知消费。")}</p><div className="button-row"><AsyncButton feedback={op.feedback} disabled={op.busy||!intent.can_reconcile} onClick={()=>op.send('resolve_compaction',{intent_id:intent.id,revision:intent.revision,choice:'reconcile'})}>{ui("核验已完成摘要并补登记")}</AsyncButton><AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>op.send('resolve_compaction',{intent_id:intent.id,revision:intent.revision,choice:'abandon'})}>{ui("保留旧检查点并放弃此整理")}</AsyncButton></div></details>)}
 <Evidence value={value?.compactions} title={ui("原历史整理回执")}/>
 </section>;
}
export function ReferenceSearch(p:Props&{kind:'experience'|'knowledge'}){
 const [query,setQuery]=useState(''),op=useOperation(p.record,p.state,p.request,p.refresh),action=p.kind+'_search';
 const result=op.output?.[action]||latestResult(p.state,action)?.[action];
 return <section className="native-card"><h3>{p.kind==='experience'?ui("经历"):ui("工程知识")}{ui("原记录检索")}</h3><form className="query-form" onSubmit={e=>{e.preventDefault();op.send(action,{issue:query});}}><input data-form-assist-draft="true" aria-label={p.kind==='experience'?ui("检索经历的问题"):ui("检索知识的问题")} value={query} maxLength={2000} onChange={e=>setQuery(e.target.value)}/><AsyncButton feedback={op.feedback} disabled={op.busy||!query.trim()}>{ui("查询原")}{p.kind==='experience'?ui("经历"):ui("知识")}{ui("记录")}</AsyncButton></form>{op.status}{result&&<Evidence value={result} title={ui("匹配、排除与原出处")}/>}</section>;
}
