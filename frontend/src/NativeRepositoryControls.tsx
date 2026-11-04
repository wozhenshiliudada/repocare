import {ui} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useState} from 'react';
import {Evidence,Props,useOperation} from './WorkbenchPages';
import {latestResult,monitorPayload} from './operation-model.mjs';
import {RootMaintenanceControls} from './RootMaintenanceControls';
export function NativeRepositoryControls(p:Props){
 const op=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh),[branch,setBranch]=useState(''),[interval,setInterval]=useState(300),[github,setGithub]=useState(false),[confirmed,setConfirmed]=useState(false);
 const source=latestResult(p.hub,'local',(r:any)=>!p.record||r.path===p.record.repository);
 const analysis=source&&latestResult(p.hub,'analyze_repo',(r:any)=>r.repository_revision===source.revision);
 const payload=monitorPayload(source,p.state?.policy,{branch:branch||source?.branch,interval,github,confirmed});
 return <section className="native-card"><header><h2>{ui("监控配置与只读项目分析")}</h2></header><p className="muted">{ui("先读取仓库预览，再设置目录维护规则。系统选择具体文件与检查，旧版固定范围入口保留。")}</p>{op.status}
  <AsyncButton feedback={op.feedback} disabled={!p.record||op.busy} onClick={()=>{setConfirmed(false);op.send('local',{path:p.record!.repository});}}>{ui("读取当前仓库范围预览")}</AsyncButton>
  {source&&<><RootMaintenanceControls key={source.path} p={p} source={source}/><details><summary>{ui("旧版固定文件监控与只读分析")}</summary><Evidence value={source} title={ui("当前仓库、建议检查、固定范围与预览指纹")}/><AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>op.send('analyze_repo',{path:source.path,revision:source.revision})}>{ui("只读分析Python语法与检查缺口")}</AsyncButton>
  {analysis&&<><p className="inline-notice">{analysis.summary}</p><Evidence value={analysis} title={ui("语法线索与验证缺口（未运行项目测试）")}/></>}
  <div className="context-fields"><label>{ui("监控目标分支")}<input data-form-assist-draft="true" value={branch||source.branch||''} onChange={e=>{setBranch(e.target.value);setConfirmed(false);}}/></label><label>{ui("监控间隔（60—3600秒）")}<input data-form-assist-draft="true" type="number" min={60} max={3600} value={interval} onChange={e=>{setInterval(Number(e.target.value));setConfirmed(false);}}/></label></div>
  <label className="check-label"><input type="checkbox" checked={github} disabled={!source.github_url} onChange={e=>{setGithub(e.target.checked);setConfirmed(false);}}/>{ui("同时读取此仓库GitHub失败Actions元数据")}</label>
  <Evidence value={p.state?.policy} title={ui("监控将沿用的原文件与精确检查范围")}/><label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("允许服务运行期间自动检查，并按原共享模型额度诊断；修改仍需原审批")}</label>
  <AsyncButton feedback={op.feedback} disabled={op.busy||!payload} onClick={()=>{if(payload){setConfirmed(false);op.send('monitor_start',payload);}}}>{ui("确认启用这份范围的监控")}</AsyncButton>{!source.clean&&<p className="inline-notice">{ui("当前工作区有未提交修改，保留现场；此入口不会启用新的监控。")}</p>}
  </details></>}
  {!p.record&&!source&&<p className="muted">{ui("先明确连接仓库，再读取或配置实际操作。")}</p>}
 </section>;
}
