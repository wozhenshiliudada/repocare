import {ui} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useState} from 'react';
import {Badge,Evidence,Props,useOperation} from './WorkbenchPages';
type Data=Record<string,any>;
export function MaintenancePanel(p:Props){
 const memory=p.state?.maintenance_memory,op=useOperation(p.record,p.state,p.request,p.refresh);
 const [draft,setDraft]=useState<Data>({note_id:null,title:'',detail:'',status:'open',priority:1}),[revision,setRevision]=useState<string|null>(null),[kind,setKind]=useState('all');
 const [search,setSearch]=useState('');
 const [enabled,setEnabled]=useState(Boolean(memory?.enabled));
 const stale=Boolean(revision&&revision!==memory?.expected_revision);
 const observations=memory?.observations||[];
 const save=()=>op.send('maintenance_note',{...draft,revision:revision||memory?.expected_revision});
 const changeDraft=(patch:Data)=>{setRevision(revision||memory?.expected_revision);setDraft(previous=>({...previous,...patch}));};
 return <section className="native-card"><header><h2>{ui("维护事实与人工待办")}</h2></header>{op.status}
  <form className="query-form" onSubmit={e=>{e.preventDefault();op.send('maintenance_search',{issue:search});}}><input data-form-assist-draft="true" aria-label={ui("维护待办搜索")} value={search} maxLength={2000} onChange={e=>setSearch(e.target.value)} placeholder={ui("搜索原维护事实与待办…")}/><AsyncButton feedback={op.feedback} disabled={op.busy||memory?.status!=='verified'||!search.trim()}>{ui("搜索维护待办")}</AsyncButton></form>{op.output?.maintenance_search&&<Evidence value={op.output.maintenance_search} title={ui("原维护待办搜索结果")}/>}
  <label className="check-label"><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>{ui("之后的新任务参考相关维护待办")}</label><AsyncButton feedback={op.feedback} disabled={op.busy||memory?.status!=='verified'} onClick={()=>op.send('maintenance_settings',{enabled,revision:memory?.expected_revision})}>{ui("保存维护参考设置")}</AsyncButton>
  <form className="note-form" onSubmit={e=>{e.preventDefault();save();}}><h3>{draft.note_id?ui("编辑原待办"):ui("新增人工待办")}</h3><label>{ui("待办标题")}<input data-form-assist-draft="true" value={draft.title} maxLength={200} onChange={e=>changeDraft({title:e.target.value})}/></label><label>{ui("需要跟进什么")}<textarea data-form-assist-draft="true" value={draft.detail} maxLength={1200} onChange={e=>changeDraft({detail:e.target.value})}/></label><div className="context-fields"><label>{ui("跟进状态")}<select data-form-assist-draft="true" value={draft.status} onChange={e=>changeDraft({status:e.target.value})}><option value="open">{ui("继续跟进")}</option><option value="deferred">{ui("暂缓")}</option><option value="closed_by_user">{ui("关闭跟进")}</option></select></label><label>{ui("优先级")}<select data-form-assist-draft="true" value={draft.priority} onChange={e=>changeDraft({priority:Number(e.target.value)})}>{[ui("低"),ui("普通"),ui("优先"),ui("最高")].map((s,i)=><option key={i} value={i}>{s}</option>)}</select></label></div>
  {stale&&<div className="inline-notice" role="alert">{op.output?.note_id?ui("保存回执已确认，原记录已有新版本。"):ui("原记录版本已变化。")}{ui("草稿保留，请核对下方当前事实，或点击对应记录的“编辑这条待办”。")}<button type="button" onClick={()=>setRevision(memory?.expected_revision)}>{ui("我已核对当前版本，保留草稿继续编辑")}</button></div>}
  <div className="button-row"><AsyncButton feedback={op.feedback} className="primary" disabled={op.busy||stale||memory?.status!=='verified'||!draft.title.trim()||!draft.detail.trim()}>{ui("保存待办")}</AsyncButton><button type="button" onClick={()=>{setDraft({note_id:null,title:'',detail:'',status:'open',priority:1});setRevision(null);}}>{ui("重置为新待办")}</button></div></form>
  <p className="muted">{ui("关闭跟进仅表示人工安排，不能当作代码已修复或已交付。")}</p><label>{ui("维护事实类型")}<select data-form-assist-draft="true" value={kind} onChange={e=>setKind(e.target.value)}><option value="all">{ui("全部事实")}</option>{[...new Set<string>(observations.map((v:Data)=>v.body.kind))].map(k=><option key={k}>{k}</option>)}</select></label>
  {observations.filter((v:Data)=>kind==='all'||v.body.kind===kind).map((row:Data)=><article className="event-row" key={row.id}><div><div className="card-foot"><strong>{row.body.title}</strong><Badge value={row.body.stage}/></div><p>{row.body.detail}</p><Evidence value={row} title={ui("原版本与出处")}/>{row.body.kind==='note'&&<button onClick={()=>{setDraft({note_id:row.source_ref.id,title:row.body.title,detail:row.body.detail,status:row.body.stage,priority:row.body.priority});setRevision(memory.expected_revision);}}>{ui("编辑这条待办")}</button>}</div></article>)}
  <Evidence value={memory} title={ui("维护事项、修复、检查、游标与完成动作")}/>
 </section>;
}
