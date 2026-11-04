import {usageText} from './request-usage.mjs';
import {ui,uiLabels} from './ui-language.mjs';
import {IssueValidationStop} from './ValidationClarification';
import {AsyncButton} from './AsyncButton';
import React,{useEffect,useRef,useState} from 'react';
import {DeliveryProposal,TestTable,LocalReview} from './EvidenceView';
import {statusLabel} from './workbench-model.mjs';
import {IssueEvidence} from './IssueEvidence';
import {ActivityTimeline} from './ActivityTimeline';
import {CheckComparison} from './EvidenceView';
import {IssueWatchControl} from './IssueWatchControl';
import {IssueValidation} from './IssueValidation';
import {IssueProgressPanel} from './IssueProgressPanel';

type Data=Record<string,any>;
type Send=(path:string,token:string,body?:Data)=>Promise<Data>;
const statusNames:Record<string,string>=uiLabels({ready:'可接手',triage_only:'仅调查',untrusted_label:'标签来源不在可信名单',
 needs_triage:'等待分诊',ignored_closed:'Issue 已关闭',investigated:'调查完成',stale:'版本已变化',
 awaiting_review:'待验收','awaiting-review':'候选待验收',running:'执行中',claimed:'已认领',
 paused:'已暂停',interrupted:'上次中断',failed_no_replay:'未完成，保留证据',delivered:'已交付',
 awaiting_validation_review:'案例待审阅',clarification_needed:'需求需要补充',accepted:'已确认',rejected:'已拒绝',
 validation_needed:'需要验收案例',validation_review_required:'先核对验收案例',
 interrupted_no_replay:'案例提议中断，保留原账；不会重发'});
const readable=(v:string)=>statusNames[v]||statusLabel(v);
const split=(v:string)=>v.split(/[\n,，]+/).map(s=>s.trim()).filter(Boolean);
function RuleSummary({value}:{value:Data}){return <dl className="property-list"><dt>{ui("读取范围")}</dt><dd>{(value.spec?.read_roots||value.read_roots||[]).join('、')}</dd><dt>{ui("写入范围")}</dt><dd>{(value.write_roots||[]).join('、')}</dd><dt>{ui("可信标签操作者")}</dt><dd>{value.spec?.trusted_label_actors?.join('、')}</dd><dt>{ui("依赖安装 / 联网")}</dt><dd>{value.allow_install?ui("允许安装"):ui("不允许安装")} · {value.allow_network?ui("允许固定源联网"):ui("不允许联网安装")}</dd><dt>{ui("验收工作流 ID")}</dt><dd>{value.workflow_ids?.join('、')||ui("未配置；不能关闭 Issue")}</dd></dl>;}

export function IssueManager({record,state,focus,executionFocus,items,request,refresh,view='issues'}:{record?:Data;state?:Data|null;focus?:number|null;executionFocus?:string;items:Data[];request:Send;refresh:()=>Promise<unknown>;view?:string}){
 const data=state?.issue_workbench;
 const [number,setNumber]=useState(focus?String(focus):''),[selected,setSelected]=useState<number|null>(focus||null);
 const [executionChoice,setExecutionChoice]=useState(executionFocus||'');
 useEffect(()=>{if(executionFocus)setExecutionChoice(executionFocus);},[executionFocus]);
 const [reviewReason,setReviewReason]=useState('');
 useEffect(()=>{setReviewReason('');},[record?.id,selected,executionChoice]);
 useEffect(()=>{if(focus){setSelected(focus);setNumber(String(focus));}},[focus]);
 const [readRoots,setReadRoots]=useState('.'),[writeRoots,setWriteRoots]=useState('.'),[actors,setActors]=useState('');
 const [mode,setMode]=useState('approval'),[install,setInstall]=useState(false),[network,setNetwork]=useState(false);
 const [workflows,setWorkflows]=useState(''),[consent,setConsent]=useState(false);
 const [pending,setPending]=useState<Data|null>(null),[output,setOutput]=useState<Data|null>(null);
 const [error,setError]=useState(''),[posting,setPosting]=useState(false),[config,setConfig]=useState<Data|null>(null);
 useEffect(()=>{if(selected||number||view==='configuration')return;const last=(data?.investigations||[]).at(-1)?.snapshot?.issue?.number;if(last){setSelected(last);setNumber(String(last));}},[data?.investigations,selected,number,view]);
 const [proposalConsent,setProposalConsent]=useState(false);
 useEffect(()=>{setProposalConsent(false);},[output]);
 const seen=useRef('');
 const inFlight=useRef(false);
 const investigation=(data?.investigations||[]).filter((v:Data)=>v.snapshot?.issue?.number===selected).at(-1);
 const prepared=(data?.environments||[]).find((v:Data)=>v.id===investigation?.id);
 const executions=(data?.executions||[]).filter((v:Data)=>v.issue_number===selected).slice().sort((a:Data,b:Data)=>String(a.created_at||'').localeCompare(String(b.created_at||'')));
 const activeExecution=executions.find((v:Data)=>v.id===executionChoice)||executions.at(-1);
 const originalRun=activeExecution&&(state?.runs||[]).find((run:Data)=>run.run_id===activeExecution.run_id);
 const resultRun=activeExecution&&(state?.jobs||[]).filter((job:Data)=>job.action==='issue_result'&&job.status==='finished'&&job.result?.run_id===activeExecution.run_id).at(-1)?.result;
 const waiting=(data?.approvals||[]).filter((v:Data)=>v.status==='pending');
 const disabled=posting||Boolean(pending?.uncertain)||Boolean(state?.active)||Boolean(state?.runtime?.code_changed);
 const eligible=investigation?.status==='investigated'&&investigation.snapshot?.status==='ready';
 useEffect(()=>{
  if(!pending)return;
  const job=(state?.jobs||[]).find((j:Data)=>j.id===pending.id);
  if(!job||job.status==='running'||seen.current===job.id)return;
  seen.current=job.id;setPending(null);
  if(job.status!=='finished'){setError(job.error||job.result?.error||ui("本次操作未完成，请检查原记录"));return;}
  setError('');
  setOutput({action:pending.action,result:job.result,execution_id:pending.execution_id,number:pending.number,investigation_id:pending.investigation_id});
  if(pending.action==='issue_config_preview'){setConfig(job.result);setConsent(false);}
 },[state?.jobs,pending]);
 async function send(action:string,payload:Data){
  if(!record||inFlight.current||pending?.uncertain)return;inFlight.current=true;setPosting(true);setError('');
  const requestId=crypto.randomUUID().replaceAll('-','');
  try{
   const job=await request(record.api_prefix+'actions/'+action,record.token,{request_id:requestId,...payload});
   if(job.id)setPending({id:job.id,action,execution_id:payload.execution_id,number:payload.number,investigation_id:payload.investigation_id});else setOutput({action,result:job,execution_id:payload.execution_id,number:payload.number,investigation_id:payload.investigation_id});
   await refresh(); // Wake the original state projection even if a fast job finished between hub polls.
  }catch(e){setError((e as Error).message);if((e as Data).uncertain){setPending({id:requestId,action,execution_id:payload.execution_id,number:payload.number,investigation_id:payload.investigation_id,uncertain:true});await refresh().catch(()=>{});}}finally{inFlight.current=false;setPosting(false);}
 }
 function choose(value:number){setSelected(value);setNumber(String(value));setOutput(null);setExecutionChoice('');setReviewReason('');}
 const byExecution=(action:string,extra:Data={})=>activeExecution&&send(action,{execution_id:activeExecution.id,...extra});
 return <section className="issue-manager" aria-label={ui("Issue 接手与交付")}>
  {record&&state&&view!=='delivery'&&<IssueWatchControl record={record} state={state} request={request} refresh={refresh}/>}
  {state?.runtime?.resource_limits&&<details className="issue-section"><summary>{ui("运行容量与上限")}</summary>
   <p>{ui("候选工作区：")}{Math.round(state.runtime.resource_limits.workspace_max_bytes/1024/1024)}{ui(" MiB，最多 ")}{state.runtime.resource_limits.workspace_max_entries}{ui(" 项。单任务最多 ")}{state.runtime.resource_limits.source_context_max_files}{ui(" 个明确授权文件。")}</p>
   <p>{usageText(state)}{ui("。系统新会话上限 ",". System limit for new sessions: ")}{state.runtime.default_request_budget}{ui(" 次，不会自动增加本会话额度。")}</p>
   <p>{ui("未声明模型长度时的默认输出 ","Default output for profiles without declared limits: ")}{state.runtime.max_output_tokens}{ui(" token；规划时限 ")}{Math.round(state.runtime.planning_timeout_seconds/60)}{ui(" 分钟。显式模型配置使用自己的长度，服务商实际限制仍适用。"," minutes. Explicit model configurations use their own lengths, subject to provider limits.")}</p>
   <p className="caption">{ui("容量由本机配置控制，修改后需要重启；既有维护规则、历史消费及审批保持原记录。")}</p>
  </details>}
  {!record?<p>{ui("先连接此仓库的本地副本，再设置维护规则和接手 Issue。")}</p>:<>
   {(view==='configuration'||!data?.configured)&&<details open={view==='configuration'||!data?.configured} className="issue-section issue-rules"><summary>{ui("Issue 维护规则 ")}{data?.configured?ui("· 已固定"):ui("· 首次设置")}</summary>
    {data?.configured?<><p>{ui("目标分支：")}<code>{data.configuration.spec.branch}</code> · {data.configuration.mode==='auto'?ui("预授权自主"):ui("人工审批")}</p><p className="caption">{ui("可信准入维护者：")}{data.configuration.spec.trusted_label_actors.join('、')}{ui("。具体文件和测试由调查选择。原账本与已用/未知额度保留。")}</p><RuleSummary value={data.configuration}/></>:<>
     <p className="caption">{ui("一次确认维护范围；Issue 内容和标签不产生额外权限。此设置不会调用模型。")}</p>
     <label>{ui("读取目录或文件草稿（每行一项）")}<textarea data-form-assist-draft="true" value={readRoots} onChange={e=>{setReadRoots(e.target.value);setConfig(null);}} rows={2}/></label>
     <label>{ui("修改实现目录草稿（每行一项）")}<textarea data-form-assist-draft="true" value={writeRoots} onChange={e=>{setWriteRoots(e.target.value);setConfig(null);}} rows={2}/></label>
     <label>{ui("谁可以添加 ready-for-agent 标签")}<input data-form-assist-draft="true" placeholder={ui("维护者的 GitHub 用户名")} value={actors} onChange={e=>{setActors(e.target.value);setConfig(null);}}/></label>
     <label>{ui("执行方式草稿")}<select data-form-assist-draft="true" value={mode} onChange={e=>{setMode(e.target.value);setConfig(null);}}><option value="approval">{ui("方案完成后，等我批准修改")}</option><option value="auto" disabled={!data?.permit_auto}>{ui("在规则内自主修复候选")}</option></select></label>
     <label><input type="checkbox" checked={install} onChange={e=>{setInstall(e.target.checked);if(!e.target.checked)setNetwork(false);setConfig(null);}}/>{ui("允许在独立目标环境安装依赖")}</label>
     <label><input type="checkbox" checked={network} disabled={!install} onChange={e=>{setNetwork(e.target.checked);setConfig(null);}}/>{ui("允许从固定官方源联网安装")}</label>
     <label>{ui("交付验收的 Actions 工作流 ID（可留空）")}<input data-form-assist-draft="true" value={workflows} placeholder={ui("可留空；留空时不能自动关闭 Issue")} onChange={e=>{setWorkflows(e.target.value);setConfig(null);}}/></label>
     <AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled||!actors.trim()} onClick={()=>send('issue_config_preview',{read_roots:split(readRoots),write_roots:split(writeRoots),trusted_actors:split(actors),mode,allow_install:install,allow_network:network,workflow_ids:split(workflows).map(Number)})}>{ui("预览维护规则")}</AsyncButton>
     {config&&<div className="issue-proposal"><p>{ui("分支 ")}{config.configuration.spec.branch}{ui("；写入范围 ")}{config.configuration.write_roots.join('、')}。</p><RuleSummary value={config.configuration}/><p>{ui("修复会使用当前模型和本仓库原共享额度，可能计费。本地工作区隔离不等于系统沙箱；项目测试及获准的安装可能执行第三方代码。")}</p><label><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>{ui("我确认以上维护范围，并允许在本机隔离候选中运行所选检查")}</label><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} className="primary" disabled={disabled||!consent} onClick={()=>send('issue_configure',{config:config.configuration,revision:config.revision})}>{ui("确认规则，保留原额度")}</AsyncButton></div>}
    </>}
   </details>}
   {view!=='configuration'&&<div className="issue-number"><label>{ui("Issue 编号")}<input data-form-assist-draft="true" inputMode="numeric" value={number} onChange={e=>setNumber(e.target.value)} placeholder={ui("例如 6")}/></label><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled||!data?.configured||!/^\d+$/.test(number)} onClick={()=>{choose(Number(number));send('issue_read',{number:Number(number)});}}>{ui("读取详情")}</AsyncButton></div>}
  </>}
  {view!=='configuration'&&<div className="issue-items">{items.map(item=><div className={'issue-list-row '+(selected===item.number?'selected':'')} key={item.id}><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} className="issue-select" disabled={posting||Boolean(pending?.uncertain)} onClick={()=>{choose(item.number);if(record&&data?.configured&&!state?.active)send('issue_read',{number:item.number});}}><strong>#{item.number} {item.title}</strong><span className="caption">{item.state} · {item.updated_at}</span></AsyncButton><a href={item.url} target="_blank" rel="noopener noreferrer" aria-label={ui("在 GitHub 查看 Issue ")+item.number}>↗</a></div>)}</div>}
  {view==='delivery'&&<div className="issue-record-selector"><label>{ui("选择已登记的 Issue 执行")}<select aria-label={ui("交付的 Issue 执行")} value={selected||''} onChange={e=>choose(Number(e.target.value))}><option value="">{ui("选择一个 Issue…")}</option>{[...new Set<number>((data?.executions||[]).map((e:Data)=>e.issue_number))].map(n=><option value={n} key={n}>Issue #{n}</option>)}</select></label></div>}
  {view!=='configuration'&&selected&&record&&<section className="issue-section"><h3>Issue #{selected}</h3>
   {executions.length>1&&<label>{ui("原执行记录")}<select value={activeExecution.id} onChange={e=>{setExecutionChoice(e.target.value);setOutput(null);}}>{executions.map((e:Data)=><option value={e.id} key={e.id}>{e.id.slice(0,12)} · {readable(e.status)}</option>)}</select></label>}
   <IssueEvidence state={state} number={selected}/>
   {activeExecution&&<ActivityTimeline state={state} execution={activeExecution} run={resultRun||originalRun}/>}
   <AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled||!data?.configured} onClick={()=>send('issue_investigate',{number:selected})}>{ui("只读调查，寻找文件和检查")}</AsyncButton>
   {investigation&&<><p>{ui("调查：")}{readable(investigation.status)}{ui(" · 准入：")}{readable(investigation.snapshot?.status)}</p><p className="caption">{ui("目标提交 ")}{investigation.snapshot?.head_sha?.slice(0,12)}{ui("。重复点击读取原记录，不重新调用模型。")}</p></>}
   {state?.task_control?.can_stop&&['issue_validation_propose','issue_validation_clarify'].includes((state?.jobs||[]).find((j:Data)=>j.id===state.active)?.action)&&record&&<IssueValidationStop record={record} control={state.task_control} request={request} refresh={refresh}/>}
   {eligible&&<IssueValidation key={record?.id+':'+investigation.id} investigation={investigation}
    validation={(data?.validations||[]).filter((row:Data)=>row.investigation_id===investigation.id).at(-1)}
    preview={output?.action==='issue_validation_preview'&&output.investigation_id===investigation.id?output.result:undefined}
    clarificationPreview={output?.action==='issue_validation_clarification_preview'&&output.investigation_id===investigation.id?output.result:undefined}
    disabled={disabled} prepared={Boolean(prepared)} send={send}/>}
   {output?.action==='issue_environment'&&output.investigation_id===investigation?.id&&output.result.status==='validation_needed'&&<p role="status">{ui("本次尚未找到可用检查。请先预览案例提议，让Agent根据问题描述提出验收案例，再由你核对预期。")}</p>}
   {eligible&&<AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled||Boolean((data?.validations||[]).find((row:Data)=>row.investigation_id===investigation.id&&['started','awaiting_validation_review','clarification_needed','interrupted_no_replay'].includes(row.status)))} onClick={()=>send('issue_environment',{investigation_id:investigation.id})}>{ui("预览自动选择的环境与范围")}</AsyncButton>}
   {prepared&&<div className="issue-proposal"><h4>{ui("本次自动选集")}</h4><p>{ui("可修改：")}{prepared.policy.allowed_paths.join('、')}</p><p>{ui("只读：")}{prepared.policy.readonly_paths.join('、')||ui("无额外文件")}</p><p>{ui("检查 ")}{prepared.policy.allowed_tests.length}{ui(" 项 · 同条件基线与候选复测；未选测试不计作已验证。")}</p><details><summary>{ui("查看实际检查")}</summary><pre>{prepared.policy.allowed_tests.map((c:string[])=>c.join(' ')).join('\n')}</pre></details><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} className="primary" disabled={disabled||Boolean(activeExecution)} onClick={()=>send('issue_execute',{investigation_id:prepared.id,revision:prepared.revision})}>{data?.configuration?.mode==='auto'?ui("开始自主候选修复"):ui("生成方案，等待我审批")}</AsyncButton></div>}
   {activeExecution&&<div className="issue-execution"><h4>{readable(activeExecution.status)}</h4><p className="caption">{ui("执行 ")}{activeExecution.id.slice(0,12)}{ui(" · 原运行 ")}{activeExecution.run_id.slice(0,12)}</p>
    {['claimed','running'].includes(activeExecution.status)&&<AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={posting||Boolean(pending?.uncertain)||activeExecution.pause_requested} onClick={()=>byExecution('issue_pause',{revision:activeExecution.challenge})}>{ui("请求暂停，保留原账与历史")}</AsyncButton>}
    {['paused','interrupted'].includes(activeExecution.status)&&<AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled} onClick={()=>byExecution('issue_resume',{revision:activeExecution.challenge})}>{ui("重新规划续接（需要新审批）")}</AsyncButton>}
    {activeExecution.status==='failed_no_replay'&&<AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled} onClick={()=>byExecution('issue_environment_retry_preview')}>{ui("检查环境阻塞后的续跑条件")}</AsyncButton>}
    {activeExecution.status==='failed_no_replay'&&<div>{(data.approvals||[]).filter((a:Data)=>a.execution_id===activeExecution.id&&a.decision_kind==='request_changes').map((a:Data)=><div key={a.id}><p>{ui("已保存的修改意见：")}{a.review_reason}</p><button disabled={disabled} onClick={()=>{setReviewReason(a.review_reason);setOutput(null);}}>{ui("使用这份已保存的意见")}</button></div>)}<label>{ui("修订意见（仅供诊断，不增加权限）")}<textarea aria-label={ui("Issue 修订意见")} maxLength={2000} value={reviewReason} onChange={e=>{setReviewReason(e.target.value);setOutput(null);}}/></label><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled||!reviewReason.trim()} onClick={()=>byExecution('issue_review_revision_preview',{reason:reviewReason.trim()})}>{ui("预览原任务的反馈修订条件")}</AsyncButton><p className="caption">{ui("只支持候选准备前已安全结束的拒绝或无提案；预览不调用模型。")}</p></div>}
    {['claimed','running','interrupted'].includes(activeExecution.status)&&<AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled} onClick={()=>byExecution('issue_takeover',{revision:activeExecution.challenge})}>{ui("核对已停止的旧执行者")}</AsyncButton>}
    <AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled} onClick={()=>byExecution('issue_result')}>{ui("查看原运行结果")}</AsyncButton>
    {['paused','interrupted','failed_no_replay'].includes(activeExecution.status)&&<AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled} onClick={()=>byExecution('issue_recover_preview')}>{ui("检查中断恢复条件")}</AsyncButton>}
    {activeExecution.status==='awaiting-review'&&<div className="issue-delivery"><p>{ui("候选通过后仍待验收。发布和关闭分别展示实际影响，绝不自动合并 main。")}</p><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled} onClick={()=>byExecution('issue_prepare_local')}>{ui("封装并复测本地候选")}</AsyncButton><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled} onClick={()=>byExecution('issue_push_preview')}>{ui("预览 GitHub 新分支发布")}</AsyncButton><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled} onClick={()=>byExecution('issue_pr_preview')}>{ui("预览 Draft PR")}</AsyncButton><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled||!data?.configuration?.workflow_ids.length} onClick={()=>byExecution('issue_close_preview')}>{ui("核对默认分支交付与关闭")}</AsyncButton></div>}
    <details><summary>{ui("外部结果未知时只读核对")}</summary>{[['push',ui("分支")],['pr','PR'],['close',ui("关闭")]].map(([kind,name])=><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled} key={kind} onClick={()=>byExecution('issue_reconcile',{kind})}>{ui("核对原")}{name}{ui("意图")}</AsyncButton>)}</details>
   </div>}
  </section>}
  {activeExecution&&<IssueProgressPanel sourceKind="execution" execution={activeExecution} comments={data?.progress_comments||[]} output={output}
   disabled={disabled} send={send} feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}}/>}
  {!activeExecution&&investigation&&<IssueProgressPanel sourceKind="investigation" execution={{id:investigation.id,run_id:investigation.id}}
   comments={data?.progress_comments||[]} output={output} disabled={disabled} send={send} feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}}/>}
  {waiting.map((a:Data)=><section className="issue-proposal" key={a.id} aria-label={ui("待批准的 Issue 修复方案")}><h3>{ui("等待批准修改")}</h3><p>{a.plan.diagnosis}</p><p>{ui("风险：")}{a.plan.risk}</p>{a.plan.edits.map((e:Data)=><details key={e.path}><summary>{e.path}</summary><pre>{e.content}</pre></details>)}<p className="caption">{ui("仅批准此方案；不批准推送、合并或关闭 Issue。")}</p><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} className="primary" disabled={posting||Boolean(pending?.uncertain)} onClick={()=>send('issue_decide',{approval_id:a.id,revision:a.revision,decision:'approve'})}>{ui("批准此方案并测试")}</AsyncButton><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={posting||Boolean(pending?.uncertain)} onClick={()=>send('issue_decide',{approval_id:a.id,revision:a.revision,decision:'reject'})}>{ui("拒绝此方案")}</AsyncButton></section>)}
  {waiting.length>0&&<section className="issue-section"><label>{ui("具体修改意见")}<textarea aria-label={ui("待审批方案的修改意见")} maxLength={2000} value={reviewReason} onChange={e=>setReviewReason(e.target.value)}/></label><p>{ui("要求修改将结束本次审批并保存意见；确认后继修订前不会调用模型。拒绝此方案仍表示直接结束。")}</p>{waiting.map((a:Data)=><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} key={a.id} disabled={posting||Boolean(pending?.uncertain)||!reviewReason.trim()} onClick={()=>send('issue_request_changes',{approval_id:a.id,revision:a.revision,reason:reviewReason.trim()})}>{ui("要求修改此方案 ")}{a.id.slice(0,8)}</AsyncButton>)}</section>}
  {pending&&<p role="status">{pending.uncertain?ui("操作结果未知，请核对原记录，勿重复提交。"):ui("操作已提交，刷新页面仍保留原执行记录。")}</p>}
  {error&&<p role="alert" className="error-text">{error}</p>}
  {output&&!['issue_read','issue_config_preview','issue_configure','issue_investigate','issue_environment'].includes(output.action)&&<div className="issue-section issue-output"><h4>{ui("实际结果")}</h4><p>{readable(output.result.status)}</p>{output.result.diff&&<pre>{output.result.diff}</pre>}{output.result.candidate_tests&&<p>{ui("候选检查 ")}{output.result.candidate_tests.length}{ui(" 项；实际退出码：")}{output.result.candidate_tests.map((t:Data)=>t.returncode).join("、")}</p>}
   {output.action==='issue_environment_retry_preview'&&output.result.revision&&<><p>{ui("原失败保留；从原会话重新规划，使用原额度，可能计费。原已用 ")}{output.result.budget.requests_used}{ui(" 次，剩余 ")}{output.result.budget.requests_remaining}{ui(" 次；新方案必须重新审批。当前仅支持测试开始前已安全回滚的环境阻塞，不重放旧批准。")}</p><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} className="primary" disabled={disabled} onClick={()=>send('issue_retry_environment',{execution_id:output.result.execution_id,revision:output.result.revision})}>{ui("确认环境已处理，使用原会话与额度续跑")}</AsyncButton></>}
   {output.action==='issue_review_revision_preview'&&output.result.revision&&<><p>{ui("原失败保留；本次意见：")}{output.result.review_context.reason}</p><p>{ui("原已用 ")}{output.result.budget.requests_used}{ui(" 次、剩余 ")}{output.result.budget.requests_remaining}{ui(" 次。确认后可能计费；新方案仍须新审批，不继承旧批准。")}</p><AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} className="primary" disabled={disabled} onClick={()=>send('issue_revise_review',{execution_id:output.result.execution_id,revision:output.result.revision,reason:output.result.review_context.reason})}>{ui("确认使用原会话与额度生成修订方案")}</AsyncButton></>}
   {['issue_push_preview','issue_pr_preview','issue_close_preview'].includes(output.action)&&<><DeliveryProposal result={output.result}/><label className="check-label"><input type="checkbox" checked={proposalConsent} onChange={e=>setProposalConsent(e.target.checked)}/>{ui("我已核对此具体提案的对象、影响与审批指纹")}</label></>}
   {output.result.baseline_tests&&<TestTable tests={output.result.baseline_tests} title={ui("修改前基线")}/>}{output.result.candidate_tests&&<TestTable tests={output.result.candidate_tests} title={ui("候选检查")}/>}{output.result.baseline_tests&&output.result.candidate_tests&&<CheckComparison baseline={output.result.baseline_tests} candidate={output.result.candidate_tests}/>}
   {output.action==='issue_prepare_local'&&<LocalReview result={output.result}/>}
   {output.action==='issue_prepare_local'&&output.result.review_sha256&&<AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled} onClick={()=>send('issue_publish_local',{execution_id:output.execution_id,revision:output.result.review_sha256})}>{ui("仅批准创建这个本地发布分支")}</AsyncButton>}
   {output.action==='issue_recover_preview'&&output.result.can_abort&&<AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} disabled={disabled} onClick={()=>send('issue_recover',{execution_id:output.execution_id,revision:output.result.revision})}>{ui("批准安全终止这个中断候选")}</AsyncButton>}
   {['issue_push_preview','issue_pr_preview','issue_close_preview'].includes(output.action)&&output.result.approval_sha256&&<AsyncButton feedback={{pending:posting||Boolean(pending),uncertain:Boolean(pending?.uncertain)}} className="primary" disabled={disabled||!proposalConsent||!output.execution_id} onClick={()=>send(output.action.replace('_preview',''),{execution_id:output.execution_id,revision:output.result.challenge,approval_sha256:output.result.approval_sha256})}>{output.action==='issue_push_preview'?ui("批准创建这个远端新分支"):output.action==='issue_pr_preview'?ui("批准创建这个 Draft PR"):ui("批准关闭这个已交付 Issue")}</AsyncButton>}
   <details><summary>{ui("证据与具体提案")}</summary><pre>{JSON.stringify(output.result,null,2)}</pre></details></div>}
 </section>;
}
