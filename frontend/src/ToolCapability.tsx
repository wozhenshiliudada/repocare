import {toolDefinitionDisplayText} from './tool-definition-language.mjs';
import {ui} from './ui-language.mjs';
import React,{useEffect,useState} from 'react';
import {AsyncButton} from './AsyncButton';
import {useOperation,Evidence} from './WorkbenchPages';
import {latestResult,sameBinding} from './operation-model.mjs';
import type {ExistingServices} from './ExistingCapability';
type Data=Record<string,any>;
export function ToolCapability({id,services}:{id:string;services:ExistingServices}){
 const [definition,setDefinition]=useState<Data|null>(null),[params,setParams]=useState<Data>({}),[error,setError]=useState(''),[confirmed,setConfirmed]=useState(false);
 const op=useOperation({api_prefix:'/api/'},services.hub,services.request,services.refresh);
 useEffect(()=>{let alive=true;services.request('/api/capabilities').then(value=>{
  if(!alive)return;const entry=value.capabilities.find((v:Data)=>v.id===id);if(!entry)throw Error(ui("此服务未提供所选能力，请核对实际服务版本。"));
  setDefinition(entry);setParams(Object.fromEntries(entry.fields.map((f:Data)=>[f.key,f.kind==='workspace'?services.record?.id||'':f.key==='repository'?services.record?.repository||f.default:f.default])));
 }).catch(e=>{if(alive)setError(e.message);});return()=>{alive=false;};},[id,services.record?.id]);
 useEffect(()=>setConfirmed(false),[params,op.output,op.error]);
 const proposal=op.output?.preview_id&&!op.output?.artifacts?op.output:latestResult(services.hub,'capability_preview',(r:Data)=>r.capability===id);
 const result=op.output?.artifacts?op.output:!op.feedback.pending&&!op.error?latestResult(services.hub,'capability_execute',(r:Data)=>r.capability===id):null;
 // Git refs are normalized by the server; show and explicitly approve the pinned result.
 const matching=proposal&&sameBinding(proposal.params,params);
 const ready=matching&&!op.error&&result?.preview_id!==proposal.preview_id;
 const complete=definition?.fields.every((f:Data)=>!f.required||String(params[f.key]??'').trim());
 const change=(key:string,value:any)=>{setParams(old=>({...old,[key]:value}));setError('');};
 return <section className="local-preview" aria-label={ui("已有本地工具实际操作")}><h3>{toolDefinitionDisplayText(definition?.title)||ui("读取实际服务能力")}</h3>
  <p>{toolDefinitionDisplayText(definition?.impact)}</p>{definition?.unavailable_reason&&<p role="status">{toolDefinitionDisplayText(definition.unavailable_reason)}</p>}<p className="caption">{ui("复用原库和限制；预览不执行。确认只针对这份输入，不包含模型、推送或合并授权。")}</p>
  {definition&&<div className="preview-fields">{definition.fields.map((f:Data)=><label key={f.key}>{toolDefinitionDisplayText(f.label)}{f.required?' *':''}
   {f.kind==='workspace'?<input value={services.record?.repository||ui("请先连接一个维护仓库")} readOnly/>:f.kind==='select'?<select value={params[f.key]} onChange={e=>change(f.key,e.target.value)}>{f.options.map((v:string)=><option key={v} value={v}>{toolDefinitionDisplayText(v)}</option>)}</select>:f.kind==='lines'?<textarea value={params[f.key]} onChange={e=>change(f.key,e.target.value)}/>:<input type={f.kind==='integer'?'number':'text'} min={f.kind==='integer'?1:undefined} value={params[f.key]} onChange={e=>change(f.key,f.kind==='integer'?Number(e.target.value):e.target.value)}/>}
  </label>)}</div>}
  <AsyncButton feedback={op.feedback} disabled={op.busy||!complete||Boolean(definition?.unavailable_reason)} onClick={async()=>{
   setConfirmed(false);await op.send('capability_preview',{capability:id,params});
  }}>{ui("预览原能力与实际影响")}</AsyncButton>
  {proposal&&<><Evidence value={proposal} title={ui("原输入、固定版本与执行影响")}/>{!matching&&<p>{ui("输入已变更或 Git 引用已固定为提交SHA；请核对预览，并使用固定值重新预览。")}</p>}
   {id==='git-compare'&&!matching&&<button onClick={()=>setParams(proposal.params)}>{ui("采用这份固定提交与范围")}</button>}
   <label className="check-label"><input type="checkbox" checked={confirmed} disabled={!ready||op.busy} onChange={e=>setConfirmed(e.target.checked)}/>{ui("确认执行此预览说明的只读、导出、登记或原检查操作")}</label>
   <AsyncButton feedback={op.feedback} disabled={op.busy||!ready||!confirmed} onClick={()=>{setConfirmed(false);op.send('capability_execute',{preview_id:proposal.preview_id,revision:proposal.revision,confirmed:true});}}>{ui("执行已确认的原能力")}</AsyncButton></>}
  {op.status}{error&&<p role="alert">{error}</p>}
  {result?.artifacts&&<><h4>{ui("最近已完成的原操作")}</h4>{result.params&&!sameBinding(result.params,params)&&<p>{ui("下面是此前已完成结果，当前输入尚未执行。")}</p>}<p>{ui("实际输出：")}<code>{result.directory}</code>{ui("。原库的失败、非零检查或不完整状态保持，不等于维护成功。")}</p><Evidence value={{params:result.params,result:result.result}} title={ui("原输入与原库实际结果")}/><div className="button-row">{result.artifacts.map((a:Data)=><AsyncButton key={a.url} onClick={async()=>{try{await services.download(a);}catch(e){setError((e as Error).message);}}}>{ui("下载 ")}{a.name}</AsyncButton>)}</div>{id==='source-bind'&&result.result?.repository&&<><p>{ui("新会话仓库：")}<code>{result.result.repository}</code></p><AsyncButton onClick={()=>services.inspect(result.result.repository)}>{ui("检查并连接这个新会话仓库")}</AsyncButton></>}</>}
 </section>;
}
