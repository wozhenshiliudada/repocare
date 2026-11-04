import {ui} from './ui-language.mjs';
import React,{useState} from 'react';
import {previewDisplayText} from './preview-workflows.mjs';
import {AsyncButton} from './AsyncButton';
import {useOperation,type Props} from './WorkbenchPages';
import {latestResult} from './operation-model.mjs';
import {connectionLabel,reportInputs} from './existing-capability-model.mjs';

type Data=Record<string,any>;
export type ExistingServices={hub:Data;record?:Data;openProvider?:()=>void;request:Props['request'];refresh:()=>Promise<unknown>;
 inspect:(path:string)=>Promise<void>;connect:()=>void;download:(artifact:Data)=>Promise<void>;};
export function ExistingCapability({workflow,services}:{workflow:Data;services:ExistingServices}){
 const {hub,request,refresh}=services;
 const op=useOperation({api_prefix:'/api/'},hub,request,refresh);
 const [inputs,setInputs]=useState(''),[error,setError]=useState('');
 const id=workflow.id,action=id==='recent-projects'?'saved_projects':id==='dashboard'?'export_dashboard':'export_comparison';
 const result=op.output||(!op.feedback.pending&&!op.error?latestResult(hub,action):null);
 const execute=async(payload:Data)=>{setError('');await op.send(action,payload);};
 return <section className="local-preview" aria-label={previewDisplayText(workflow.title)+ui("实际操作")}>
  <header><h3>{previewDisplayText(workflow.title)}</h3><span className="small-tag">{connectionLabel(workflow)}</span></header>
  {id==='large-repository'?<><p>{ui("已有仓库检查、文件过滤和有限范围连接。代码浏览和范围选择已按页显示，沿同一目录与单文件容量；建仓库目录仍整体枚举。")}</p><p>{ui("当前会话最多选择 ")}{hub.resource_limits?.source_context_max_files??ui("配置上限内的")}{ui(" 个文件；目录上限 ")}{hub.resource_limits?.catalog_max_files??ui("以实际配置为准")}{ui(" 项。")}</p><AsyncButton onClick={services.connect}>{ui("打开已有仓库连接与范围选择")}</AsyncButton></>
  :id==='recent-projects'?<><p>{ui("列出当前工作台状态目录中保存的项目，按配置标识排列。重新检查后展示原范围，由你确认连接；不会调用模型或重放旧批准。")}</p>
   <AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>execute({page:1})}>{ui("读取已有项目")}</AsyncButton>
   {result?.projects&&<><ul>{result.projects.map((project:Data)=><li key={project.path}><strong>{project.name}</strong><p><code>{project.path}</code></p><AsyncButton disabled={op.busy} onClick={async()=>{try{await services.inspect(project.path);}catch(e){setError((e as Error).message);}}}>{ui("重新检查此项目")}</AsyncButton></li>)}</ul>{result.projects.length===0&&<p>{ui("当前状态目录还没有保存的项目。可以通过“连接仓库”选择本地项目。")}</p>}<div className="button-row"><AsyncButton feedback={op.feedback} disabled={op.busy||result.page<=1} onClick={()=>execute({page:result.page-1})}>{ui("上一页项目")}</AsyncButton><span>{ui("第 ")}{result.page}{ui(" 页")}</span><AsyncButton feedback={op.feedback} disabled={op.busy||!result.has_next} onClick={()=>execute({page:result.page+1})}>{ui("下一页项目")}</AsyncButton></div></>}
  </>:<><p>{id==='dashboard'?ui("读取已有 run.json 运行目录或运行集合，生成可离线打开的 HTML 仪表盘。"):ui("读取已有 compare/showcase 的 summary.json，复用原校验导出摘要 JSON 和 Markdown；不重新运行对照实验。")}</p>
   <div className="preview-fields"><label>{id==='dashboard'?ui("已有运行目录（完整路径，每行一项）"):ui("已有 summary.json 完整路径")}<textarea data-form-assist-draft="true" aria-label={id==='dashboard'?ui("仪表盘运行目录"):ui("对照摘要文件")} value={inputs} onChange={e=>setInputs(e.target.value)} maxLength={12000}/></label></div>
   <p className="caption">{ui("导出写入当前工作台的独立新目录，不覆盖已有文件。不会调用模型、执行测试或改写原始证据。")}</p>
   <AsyncButton feedback={op.feedback} disabled={op.busy||!reportInputs(inputs).length||(id==='comparison'&&reportInputs(inputs).length!==1)} onClick={()=>execute({inputs:reportInputs(inputs)})}>{id==='dashboard'?ui("生成离线仪表盘"):ui("读取并导出对照摘要")}</AsyncButton>
   {result?.artifacts&&<section aria-label={ui("已完成的导出")}><h4>{ui("已完成的导出")}</h4><p>{ui("保存位置：")}<code>{result.directory}</code></p>{result.status==='partial'&&<p>{ui("原对照结果不完整（partial），导出不代表修复成功。")}</p>}{id==='comparison'&&<p>{ui("固定样例或本机合成响应的对照结果，不代表真实模型准确率或真实费用。")}</p>}<div className="button-row">{result.artifacts.map((artifact:Data)=><AsyncButton key={artifact.url} onClick={async()=>{setError('');try{await services.download(artifact);}catch(e){setError((e as Error).message);}}}>{ui("下载 ")}{artifact.name}</AsyncButton>)}</div></section>}
  </>}
  {id!=='large-repository'&&op.status}{error&&<p role="alert" className="error-text">{error}</p>}
 </section>;
}
