import {ui,uiLabels} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useEffect,useState} from 'react';
import {Evidence,Props,useOperation} from './WorkbenchPages';
import {latestResult,sameBinding} from './operation-model.mjs';
import {TestTable} from './EvidenceView';
import {CiMonitorDiagnosis} from './CiMonitorDiagnosis';
import {MonitorValidationControls} from './MonitorValidationControls';
type Data=Record<string,any>;
const lines=(text:string)=>text.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
const names:Data=uiLabels({unittest:'Python unittest 测试',pytest:'Python pytest 测试','node-native':'Node 内置测试'});

export function RootMaintenanceControls({p,source}:{p:Props;source:Data}){
 const monitor=(p.hub?.monitors||[]).find((m:Data)=>m.spec.path===source.path),saved=monitor?.root_rules?.settings;
 const [reads,setReads]=useState(saved?.read_roots?.join('\n')||'.'),[writes,setWrites]=useState(saved?.write_roots?.join('\n')||''),
  [families,setFamilies]=useState<string[]>(saved?.check_families||['unittest','pytest','node-native']),
  [mode,setMode]=useState(saved?.mode||'approval'),[local,setLocal]=useState(Boolean(saved?.allow_local_code)),
  [install,setInstall]=useState(Boolean(saved?.allow_install)),[network,setNetwork]=useState(Boolean(saved?.allow_network)),
  [remote,setRemote]=useState(false),[confirmed,setConfirmed]=useState(false);
 const op=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh);
 useEffect(()=>{setConfirmed(false);},[source.revision,reads,writes,families,mode,local,install,network,remote]);
 const settings={read_roots:lines(reads),write_roots:lines(writes),check_families:[...families].sort(),
  mode,allow_local_code:local,allow_install:install,allow_network:network,timeout_seconds:monitor?.spec.timeout_seconds||30};
 const draft={path:source.path,revision:source.revision,branch:monitor?.spec.branch||source.branch,
  github_url:monitor?.spec.github_url??(remote?source.github_url:''),interval_seconds:monitor?.spec.interval_seconds||300,...settings};
 const preview=latestResult(p.hub,'monitor_rules_preview',(r:Data)=>r.preview?.spec.path===source.path);
 const matches=preview&&preview.preview.repository_revision===source.revision&&sameBinding(preview.preview.configuration.settings,settings)
  &&preview.preview.spec.github_url===draft.github_url;
 const config=matches?preview.preview.configuration:null;
 return <section aria-label={ui("目录维护规则")} className="inline-notice"><h3>{ui("一次设置目录维护规则")}</h3>
  <p>{ui("当前仓库：")}<code>{source.path}</code></p>
  <p>{ui("选择可读、可改的目录和允许的检查方式，系统自己找具体文件与测试。这里无需填写文件清单或测试命令。")}</p>{op.status}
  {saved&&<p>{ui("已有第 ")}{monitor.root_rules.version}{ui(" 版规则；修改后保留旧规则、旧方案和原额度，不重新计账。")}</p>}
  <div className="context-fields"><label>{ui("可读取的目录（每行一个，. 表示仓库范围）")}<textarea data-form-assist-draft="true" value={reads} onChange={e=>setReads(e.target.value)}/></label>
   <label>{ui("可修改的目录（每行一个）")}<textarea data-form-assist-draft="true" value={writes} placeholder={ui("例如 src；请按项目目录填写")} onChange={e=>setWrites(e.target.value)}/></label></div>
  <fieldset><legend>{ui("允许系统选择哪些检查")}</legend>{Object.entries(names).map(([id,title])=><label className="check-label" key={id}><input type="checkbox" checked={families.includes(id)} onChange={e=>setFamilies(e.target.checked?[...families,id]:families.filter(n=>n!==id))}/>{String(title)}</label>)}</fieldset>
  <label>{ui("修复模式草稿")}<select data-form-assist-draft="true" value={mode} onChange={e=>setMode(e.target.value)}><option value="approval">{ui("人工审批后修改")}</option><option value="auto" disabled={!p.hub?.allow_auto}>{ui("授权范围内自主候选")}</option></select></label>
  {!p.hub?.allow_auto&&<p>{ui("当前服务未启用自主候选权限，保持人工审批模式。")}</p>}
  <label className="check-label"><input type="checkbox" checked={local} onChange={e=>{setLocal(e.target.checked);if(!e.target.checked){setInstall(false);setNetwork(false);}}}/>{ui("允许在隔离工作区执行本仓库代码和所选检查")}</label>
  <p>{ui("Git 工作区和依赖环境隔离状态，不是操作系统沙箱。只为你信任的项目启用代码执行。")}</p>
  <label className="check-label"><input type="checkbox" disabled={!local} checked={install} onChange={e=>{setInstall(e.target.checked);if(!e.target.checked)setNetwork(false);}}/>{ui("允许准备项目依赖")}</label>
  <label className="check-label"><input type="checkbox" disabled={!install} checked={network} onChange={e=>setNetwork(e.target.checked)}/>{ui("允许联网安装依赖")}</label>
  {!monitor&&<label className="check-label"><input type="checkbox" disabled={!source.github_url} checked={remote} onChange={e=>setRemote(e.target.checked)}/>{ui("读取此仓库 GitHub 失败 CI")}</label>}
  <p>{ui("这些规则允许调查和获准的修改前检查。模型修复默认逐事件确认，也可另外明确授权后续新提交；保存本规则不会开通模型权限，不会推送或合并代码。")}</p>
  <AsyncButton feedback={op.feedback} disabled={op.busy||!source.clean||!settings.read_roots.length||!settings.write_roots.length||!families.length} onClick={()=>{setConfirmed(false);op.send('monitor_rules_preview',draft);}}>{ui("预览目录维护规则")}</AsyncButton>
  {config&&<><p>{ui("第 ")}{config.version}{ui(" 版：可读 ")}{settings.read_roots.join('、')}{ui("；可改 ")}{settings.write_roots.join('、')}{ui("；检查方式 ")}{settings.check_families.map(f=>names[f]).join('、')}。</p>
   <p>{ui("共享模型总额度 ")}{preview.preview.budget.request_budget}{ui("，已用 ")}{preview.preview.budget.requests_used}{ui("，未知占用 ")}{preview.preview.budget.requests_reserved_unknown}{ui("。保存规则不增加额度；本段不调用模型。")}</p>
   <Evidence value={preview.preview} title={ui("查看规则、范围和版本详情")}/>
   <label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("我确认这些目录和环境权限，允许服务运行期间按此规则自动调查和检查")}</label>
   <AsyncButton feedback={op.feedback} disabled={op.busy||!confirmed} onClick={()=>{setConfirmed(false);op.send('monitor_rules_configure',{...preview,confirmed:true});}}>{ui("确认保存并启用目录维护")}</AsyncButton></>}
 </section>;
}

export function MonitorRepairControls({p,event}:{p:Props;event:Data}){
 const [attempts,setAttempts]=useState(3),[confirmed,setConfirmed]=useState(false);
 const op=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh);
 const preview=latestResult(p.hub,'monitor_repair_preview',(r:Data)=>r.preview?.binding.monitor_id===event.monitor_id&&r.preview.binding.event_id===event.id);
 const mode=(p.hub?.monitors||[]).find((m:Data)=>m.id===event.monitor_id)?.root_rules?.settings.mode;
 const matches=preview&&preview.preview.binding.base_sha===event.sha&&preview.preview.binding.preparation_sha256===event.root_preparation.result_sha256&&
  (mode!=='auto'||preview.preview.max_attempts===attempts);
 useEffect(()=>{setConfirmed(false);},[attempts,event.root_preparation.result_sha256,preview?.revision]);
 if(!event.can_prepare_repair)return null;
 return <section aria-label={ui("本次模型修复确认")}><h4>{ui("把这次失败交给 Agent 修复")}</h4>{op.status}
  <p>{ui("复用原模型、额度与隔离流程；人工模式先审方案，自主模式在已授权范围内准备候选。不会自动发布。")}</p>
  {mode==='auto'&&<label>{ui("候选轮数上限")}<input data-form-assist-draft="true" type="number" min="1" value={attempts} onChange={e=>setAttempts(Number(e.target.value))}/></label>}
  <AsyncButton feedback={op.feedback} disabled={op.busy} onClick={()=>{setConfirmed(false);op.send('monitor_repair_preview',{monitor_id:event.monitor_id,event_id:event.id,max_attempts:attempts});}}>{ui("预览本次修复范围与额度")}</AsyncButton>
  {matches&&<><p>{ui("可改文件：")}{preview.preview.policy.allowed_paths.join('、')}{ui("；原总额度 ")}{preview.preview.budget.request_budget}{ui("，已用 ")}{preview.preview.budget.requests_used}{ui("，未知占用 ")}{preview.preview.budget.requests_reserved_unknown}{ui("，剩余 ")}{preview.preview.budget.requests_remaining}{ui("。最多 ")}{preview.preview.max_attempts}{ui(" 轮候选；请求数不等于费用金额。")}</p>
   <Evidence value={preview.preview} title={ui("本次模型、环境与执行绑定")}/>
   <label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("允许本次调用已配置模型（可能收费），按所示范围、模式和原剩余额度处理")}</label>
   <AsyncButton feedback={op.feedback} disabled={op.busy||!confirmed} onClick={()=>{setConfirmed(false);op.send('monitor_repair_start',{...preview,confirmed:true});}}>{ui("确认本次模型修复")}</AsyncButton></>}
 </section>;
}

export function RootMonitorPreparation({event,p}:{event:Data;p?:Props}){
 const controls=p&&<MonitorValidationControls p={p} event={event}/>;
 const value=event.root_preparation;if(!value)return controls||null;
 return <>{controls}<section aria-label={ui("目录维护检查结果")} className="inline-notice"><h3>{ui("目录维护检查")}</h3>
  {value.status==='reproduced'?(value.scope_compilation?.status!=='needs_investigation'&&event.model_dispatch?.status!=='blocked_no_replay'&&
    <p>{event.model_observation?ui("已复现测试失败；自动修复以当前模型授权与原运行回执为准。"):ui("已复现测试失败；修复需要本次单独确认。")}</p>):
    <p>{value.message||ui("检查未完成，原记录保留，不会自动重放。")}</p>}
  {event.root_preparation_historical&&<p>{ui("CI 已变化；这份检查属于此前固定版本的证据。")}</p>}
  {value.status==='interrupted_no_replay'&&<p>{ui("上次操作中断；不会自动重做安装、测试或模型请求。")}</p>}
  {event.root_preparation_history?.length>0&&<p>{ui("保留了 ")}{event.root_preparation_history.length}{ui(" 份旧规则下的检查记录。")}</p>}
  <p>{ui("源码 ")}<code>{value.base_sha}</code>{ui("；基线阶段模型请求 ")}{value.model_requests||0}。</p>
  {event.root_execution&&<p>{ui("本次修复状态：")}{event.display_status||event.root_execution.status}{ui("。方案、候选测试与实际消费以原运行报告为准；中断与失败不会重放。")}</p>}
  {value.ci_diagnosis&&<CiMonitorDiagnosis event={{ci_diagnosis:value.ci_diagnosis,ci_diagnosis_historical:event.root_preparation_historical}}/>}
  {value.environment?.checks?.map((c:Data)=><div key={c.id}><p>{c.title} · {(c.sources||[]).join('、')}</p><details><summary>{ui("查看所选检查详情")}</summary><code>{JSON.stringify(c.argv)}</code></details></div>)}
  {(value.environment?.blockers||[]).map((v:string,i:number)=><p key={i}>{v}</p>)}
  {value.proposed_policy?.allowed_paths&&<p>{ui("调查命中的可改实现文件：")}{value.proposed_policy.allowed_paths.join('、')}{ui("。尚未修改。")}</p>}
  {value.baseline&&<><p>{ui("基线运行：")}{value.baseline.run_id}{ui("；原源码保持：")}{value.baseline.source_unchanged?ui("是"):ui("待核查")}。</p><TestTable tests={value.baseline.checks||[]} title={ui("修改前基线检查")}/></>}
  {p&&<MonitorRepairControls p={p} event={event}/>}
 </section></>;
}
