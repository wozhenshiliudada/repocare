import {ui,uiLabels} from './ui-language.mjs';
import React,{createContext,useContext,useState} from 'react';
import catalog from './capabilities.json';
import {catalogDisplayText} from './catalog-language.mjs';
import {workflows,previewDraft,previewDisplayText,previewChoiceLabel} from './preview-workflows.mjs';
import {CiInvestigation,type CiProps} from './CiInvestigation';
import {ExistingCapability,type ExistingServices} from './ExistingCapability';
import {connectionLabel,isExistingWorkflow} from './existing-capability-model.mjs';
import {capabilityRoute} from './capability-routing.mjs';
import {ToolCapability} from './ToolCapability';
type Data=Record<string,any>;
type DraftUpdate=Data|((previous:Data)=>Data);
type Store={drafts:Record<string,Data>;setDraft:(key:string,value:DraftUpdate)=>void};
const Drafts=createContext<Store>({drafts:{},setDraft:()=>{}});
export function CapabilityDraftProvider({children}:{children:React.ReactNode}){
 const [drafts,setDrafts]=useState<Record<string,Data>>({});
 return <Drafts.Provider value={{drafts,setDraft:(key,value)=>setDrafts(old=>({...old,[key]:typeof value==='function'?value(old[key]||{}):value}))}}>{children}</Drafts.Provider>;
}
const connectionNames:Record<string,string>=uiLabels({connected:'已接HTTP/现有交互',mixed:'部分已接 · 另有本地预览',preview:'仅本地预览',internal:'随原流程自动执行',engineering:'工程交付项',limitation:'效果与支持限制'});
export function CapabilityPanel({page,repositoryKey,navigate,ci,existing}:{page:string;repositoryKey:string;navigate:(page:string)=>void;ci?:CiProps;existing?:ExistingServices}){
 const store=useContext(Drafts),selectionKey='__selection:'+repositoryKey+':'+page,selected=store.drafts[selectionKey]?.id||'';
 const setSelected=(id:string)=>store.setDraft(selectionKey,{id});
 const [query,setQuery]=useState('');
 const entries=catalog.filter(c=>c.page===page),forms=workflows.filter(w=>w.page===page),workflow=forms.find(w=>w.id===selected);
 function openNative(entry:Data){navigate(entry.page);window.dispatchEvent(new CustomEvent('repocare:capability',{detail:{page:entry.page,tab:entry.tab}}));requestAnimationFrame(()=>document.getElementById('native-'+entry.page)?.scrollIntoView({behavior:'smooth'}));}
 function openWorkflow(id:string){const target=workflows.find(w=>w.id===id);if(!target)return;store.setDraft('__selection:'+repositoryKey+':'+target.page,{id});if(target.page!==page)navigate(target.page);}
 const route=workflow?capabilityRoute(workflow.id):null;
 const actual=workflow&&route?.kind==='ci'?(ci?<CiInvestigation key={repositoryKey+':ci'} {...ci}/>:<p>{ui("请选择 GitHub 仓库以调查 CI。")}</p>):
  workflow&&route?.kind==='existing'?(existing?<ExistingCapability key={repositoryKey+':'+workflow.id} workflow={workflow} services={existing}/>:<p>{ui("请从统一工作台打开此能力。")}</p>):
  workflow&&route?.kind==='tool'?(existing?<ToolCapability key={repositoryKey+':'+workflow.id} id={workflow.id} services={existing}/>:<p>{ui("请从统一工作台打开此能力。")}</p>):
  workflow&&route?.kind==='native'?<section className="local-preview"><h3>{previewDisplayText(workflow.title)}</h3><p>{route.detail}</p><button onClick={()=>{if(route.provider){existing?.openProvider?.();}else if(route.workflow){openWorkflow(route.workflow);}else{navigate(route.page);}}}>{route.button}</button></section>:
  workflow?<LocalPreview key={repositoryKey+':'+workflow.id} repositoryKey={repositoryKey} workflow={workflow}/>:null;
 return <section className="capability-panel" aria-label={ui("功能目录与接入状态")}><header className="card-foot"><div><span className="eyebrow">CAPABILITIES</span><h2>{ui("功能目录与接入状态")}</h2></div><span className="count-pill">{ui("本页 ")}{entries.length}{ui(" / 全部 ")}{catalog.length}</span></header>
  {forms.length>0&&<><p className="muted">{ui("已接入的流程可直接读取或导出；其余流程保留本地预览与缺口说明。")}</p><label>{ui("选择交互流程")}<select aria-label={ui("选择本地预览流程")} value={selected} onChange={e=>setSelected(e.target.value)}><option value="">{ui("选择一个流程…")}</option>{forms.map(w=><option key={w.id} value={w.id}>{previewDisplayText(w.title)} · {connectionLabel(w)}</option>)}</select></label>{actual}</>}
  <details className="coverage-index"><summary>{ui("本页能力索引与验收说明（")}{entries.length}{ui("项）")}</summary><label>{ui("查找能力")}<input aria-label={ui("查找功能编号或名称")} value={query} placeholder={ui("例如 K32、来源、恢复…")} onChange={e=>setQuery(e.target.value)}/></label>
  {entries.filter(c=>(c.id+' '+c.title+' '+catalogDisplayText(c.title)).toLowerCase().includes(query.toLowerCase())).map(entry=><article className="capability-row" key={entry.id}><div><strong>{entry.id} · {catalogDisplayText(entry.title)}</strong><span className="small-tag">{connectionNames[entry.availability]}</span><p>{catalogDisplayText(entry.description)}</p><div className="button-row">{['connected','mixed'].includes(entry.availability)&&(('nativeLinks' in entry?entry.nativeLinks:undefined)||[{page:entry.page,tab:entry.tab,label:ui("定位现有交互")}]).map((link:Data)=><button key={link.page+link.tab} onClick={()=>openNative(link)}>{catalogDisplayText(link.label)}</button>)}{[entry.workflow,...entry.relatedWorkflows||[]].filter(Boolean).map(id=><button key={id} onClick={()=>openWorkflow(id)}>{capabilityRoute(id).kind==='preview'?ui("填写与预览"):ui("打开功能")} · {previewDisplayText(workflows.find(w=>w.id===id)?.title)}</button>)}</div></div></article>)}
  </details>
 </section>;
}
export function LocalPreview({repositoryKey,workflow}:{repositoryKey:string;workflow:Data}){
 const store=useContext(Drafts),key=repositoryKey+':'+workflow.id,values=store.drafts[key]||{};
 const [result,setResult]=useState<Data|null>(null),[errors,setErrors]=useState<Data>({});
 function change(field:string,value:string){store.setDraft(key,previous=>({...previous,[field]:value}));setResult(null);setErrors({});}
 return <form className="local-preview" aria-label={previewDisplayText(workflow.title)+ui("本地预览")} onSubmit={e=>{e.preventDefault();const next=previewDraft(workflow,values);setErrors(next.errors);setResult(next.preview);}}>
  <header><h3>{previewDisplayText(workflow.title)}</h3><span className="small-tag">{workflow.connection==='cli'?ui("此页尚未提供原CLI执行入口"):ui("此扩展尚无专用执行接口")}</span></header><p className="inline-notice">{ui("草稿仅在当前页面会话内按仓库保存。请使用配置引用；本地预览不会发出网络请求、调用模型或执行命令。")}</p>
  <div className="preview-fields">{workflow.fields.map((field:Data)=>{const id='draft-'+workflow.id+'-'+field.key;return <label htmlFor={id} key={field.key}>{previewDisplayText(field.label)}{field.required?' *':''}{field.type==='textarea'?<textarea data-form-assist-draft="true" id={id} rows={3} value={values[field.key]||''} maxLength={4000} aria-invalid={Boolean(errors[field.key])} onChange={e=>change(field.key,e.target.value)}/>:field.type==='select'?<select data-form-assist-draft="true" id={id} value={values[field.key]||''} aria-invalid={Boolean(errors[field.key])} onChange={e=>change(field.key,e.target.value)}><option value="">{ui("请选择…")}</option>{field.options.map((v:string)=><option key={v} value={v}>{previewChoiceLabel(v)}</option>)}</select>:<input data-form-assist-draft="true" id={id} type={field.type==='number'?'number':'text'} value={values[field.key]||''} min={field.min} max={field.max} maxLength={4000} aria-invalid={Boolean(errors[field.key])} onChange={e=>change(field.key,e.target.value)}/>} {errors[field.key]&&<small className="error-text" role="alert">{previewDisplayText(errors[field.key])}</small>}</label>;})}</div>
  <div className="button-row"><button type="submit">{ui("校验并生成本地预览")}</button><button type="button" onClick={()=>{store.setDraft(key,{});setResult(null);setErrors({});}}>{ui("清除此流程草稿")}</button><button type="button" disabled title={ui("执行能力尚未接入")}>{ui("提交执行（尚未接通）")}</button></div>
  {result&&<section className="approval-box" aria-label={ui("本地预览结果")}><h4>{ui("本地预览，尚未执行")}</h4><p>{previewDisplayText(result.blocking_dependency)}</p><dl className="property-list">{workflow.fields.map((field:Data)=><React.Fragment key={field.key}><dt>{previewDisplayText(field.label)}</dt><dd>{result.inputs[field.key]||ui("未填写可选项")}</dd></React.Fragment>)}</dl><details><summary>{ui("查看本地预览字段")}</summary><pre>{JSON.stringify(result,null,2)}</pre></details></section>}
 </form>;
}
