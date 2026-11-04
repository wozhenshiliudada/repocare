import {usageText} from './request-usage.mjs';
import {ui} from './ui-language.mjs';
import {AsyncButton} from './AsyncButton';
import React,{useEffect,useRef,useState} from 'react';
import {IssueManager} from './IssueManager';
import {DeliveryInbox} from './DeliveryInbox';
import {statusLabel,statusTone,summarize,monitorEvents,completedOperation} from './workbench-model.mjs';
import {repositoryContext,workspaceFromLaunch,monitorsForWorkspace} from './operation-model.mjs';
import {TestTable} from './EvidenceView';
import {MonitorNotifications} from './MonitorNotifications';
import {CiMonitorDiagnosis} from './CiMonitorDiagnosis';
import {RootMonitorPreparation} from './RootMaintenanceControls';
import {CorrelationControls} from './CorrelationControls';
import {MonitorHistory} from './MonitorHistory';
import {MonitorModelAuthorization} from './MonitorModelAuthorization';
import {ServicePanel} from './ServicePanel';
import {MaintenancePanel} from './MaintenancePanel';
import {KnowledgeEditor,SourceQueries,ContextOperations,ReferenceSearch} from './MemoryAdvanced';
import {NativeRepositoryControls} from './NativeRepositoryControls';
import {overviewSituations,taskSituation,situationTaskTarget,deliveryStageSituation} from './task-situation-model.mjs';
import {TaskSituationSummary} from './TaskSituationSummary';

type Data=Record<string,any>;
export type Props={page:string;record?:Data;remoteUrl?:string;state:Data|null;hub:Data;request:(path:string,token?:string,body?:Data)=>Promise<Data>;
 refresh:()=>Promise<unknown>;navigate:(page:string)=>void;openOriginal:()=>void;connect:()=>void;selectTask:(id:string)=>void;selectWorkspace?:(id:string)=>void;rows:Data[];focus?:number|null;
 delivery?:Data|null;deliveryError?:string;refreshDelivery?:()=>Promise<unknown>;reviewDelivery?:(row:Data)=>void;deliveryFocus?:Data|null;notificationPreferences?:Data;onOpenNotifications?:()=>void;
 taskFocus?:{workspace_id:string|null;execution_id:string}|null};
export function Badge({value}:{value:string}){return <span className={'badge tone-'+statusTone(value)}><i/>{statusLabel(value)}</span>;}
export function Evidence({value,title=ui("证据与原始字段")}:{value:any;title?:string}){return <details className="evidence"><summary>{title}</summary><pre>{JSON.stringify(value,null,2)}</pre></details>;}
function Empty({title,children}:{title:string;children?:React.ReactNode}){return <div className="page-empty"><span className="empty-symbol">◇</span><h3>{title}</h3><p>{children||ui("新记录出现后会显示在这里。")}</p></div>;}
function Card({title,meta,children}:{title:string;meta?:React.ReactNode;children:React.ReactNode}){return <section className="native-card"><header><h2>{title}</h2>{meta}</header>{children}</section>;}
export function useOperation(record:Data|undefined,state:Data|null,request:Props['request'],refresh:Props['refresh']){
 const [pending,setPending]=useState<string|null>(null),[output,setOutput]=useState<Data|null>(null),[error,setError]=useState(''),[sending,setSending]=useState(false);
 const submitting=useRef(false);
 useEffect(()=>{const job=completedOperation(state?.jobs,pending);if(!job)return;setPending(null);if(job.status==='finished'){setOutput(job.result||{});setError('');}else setError(job.error||job.result?.error||ui("操作未完成；原记录保留。"));},[state?.jobs,pending]);
 async function send(action:string,payload:Data){
  if(!record||submitting.current||pending)return;
  submitting.current=true;setSending(true);setError('');setOutput(null);
  const requestId=crypto.randomUUID().replaceAll('-','');
  try{const job=await request(record.api_prefix+'actions/'+action,record.token,{...payload,request_id:requestId});
   if(job.id)setPending(job.id);else setOutput(job);await refresh();
  }catch(e){setError((e as Error).message);if((e as Data).uncertain){setPending(requestId);await refresh().catch(()=>{});}}finally{submitting.current=false;setSending(false);}
 }
 return {send,pending,output,error,feedback:{pending:Boolean(sending||pending),uncertain:Boolean(error&&pending)},busy:Boolean(sending||pending||state?.active||state?.runtime?.code_changed),status:<>
  {sending&&!pending&&<p className="inline-notice" role="status">{ui("正在提交一次操作，请等待服务回执。")}</p>}
  {pending&&<div className="inline-notice" role="status">{error?ui("操作结果待核验，请核对原记录，勿重复提交。"):ui("已提交操作 · 正在读取原任务回执。")}<code>{pending.slice(0,8)}</code></div>}
  {error&&<div className="inline-notice error-text" role="alert">{error}</div>}
  {output&&<div className="operation-result"><strong>{ui("已收到操作回执")}</strong>{output.status&&<Badge value={output.status}/>}<Evidence value={output}/></div>}</>};
}

export function WorkbenchPages(p:Props){
 const {page,record,state,hub,navigate,openOriginal}=p;
 const summary=summarize(state);
 const overviewMonitors=page==='overview'&&record?monitorsForWorkspace(hub,record,typeof location==='undefined'?'':location.origin):[];
 const overviewLedger=state?.repository===record?.repository?state:null;
 const attention=page==='overview'?overviewSituations(state,{repository:record?.repository,
  monitors:overviewMonitors,delivery:p.delivery}):[];
 function inspectSituation(item:Data){
  if(item.kind==='delivery'){
   const matches=(p.delivery?.items||[]).filter((value:Data)=>value.id===item.id&&value.origin?.repository===record?.repository);
   if(matches.length===1&&matches[0].origin.workspace_id&&p.reviewDelivery){p.reviewDelivery(matches[0]);return;}
  }
  const row=situationTaskTarget(p.rows,item);
  if(row&&['tasks','issues'].includes(item.page))p.selectTask(row.id);
  else if(row&&item.kind==='issue'&&item.page==='delivery'){p.selectTask(row.id);navigate('delivery');}
  else navigate(item.page);
 }
 if(page==='overview')return <>
  <div className="page-intro"><div><span className="eyebrow">REPOSITORY CARE</span><h1>{ui("把维护交给流程，把决定留给你。")}</h1><p>{ui("从问题发现到交付验收，每一步都能回到原始记录。")}</p></div><AsyncButton className="primary" onClick={()=>record?navigate(attention[0]?.page||'issues'):p.connect()}>{record?(attention.length?ui("查看当前待办"):ui("查看 Issue 工作流")):ui("连接仓库")} <span>↗</span></AsyncButton></div>
  <div className="metric-grid">
   {[[ui("待审批"),summary.approvals,ui("需要你的决定"),summary.approvalPage],[ui("维护任务"),p.rows.length,ui("原任务与 Issue 执行"),'tasks'],[ui("记忆资料"),summary.knowledge+summary.experiences,ui("经历与知识版本"),'memory']].map(([name,value,detail,target])=><button className="metric" key={name} onClick={()=>navigate(String(target))}><span>{name}</span><strong>{value}</strong><small>{detail}</small></button>)}
  </div>
  {record&&<Card title={ui("模型请求用量","Model request usage")}>
   <p className="muted">{ui("按原账分别显示；可用额度不能相加或转用。","Each original ledger is shown separately; available allowances cannot be pooled or transferred.")}</p>
   {!overviewLedger?.budget_monitor_id&&<div className="card-foot"><span><strong>{ui("手工任务 · 当前模型会话","Manual tasks · Current model session")}</strong><br/>{usageText(overviewLedger)}</span><button onClick={()=>navigate('configuration')}>{ui("查看手工额度 →","View manual allowance →")}</button></div>}
   {overviewMonitors.map((m:Data)=><div className="card-foot" key={m.id}><span><strong>{ui("监控任务 · ","Monitoring · ")}{m.spec?.branch||m.branch||m.id}</strong><br/>{usageText(m)}</span><button onClick={()=>navigate('events')}>{ui("查看监控额度 →","View monitor allowance →")}</button></div>)}
   {overviewLedger?.budget_monitor_id&&!overviewMonitors.some((m:Data)=>m.id===overviewLedger.budget_monitor_id)&&<div className="card-foot"><span><strong>{ui("当前会话的监控共享账","Shared monitor ledger for this session")}</strong><br/>{usageText(overviewLedger)}</span><button onClick={()=>navigate('events')}>{ui("查看监控额度 →","View monitor allowance →")}</button></div>}
   {!overviewMonitors.length&&!overviewLedger?.budget_monitor_id&&<p className="muted">{ui("尚未读取当前仓库的监控账。","No monitor ledger loaded for this repository.")}</p>}
  </Card>}
  <div className="overview-grid"><Card title={ui("维护流水线")} meta={<span className="caption">{ui("按真实状态推进")}</span>}><div className="pipeline">{[['01',ui("调查"),ui("固定来源与版本")],['02',ui("规划"),ui("Pi 提出具体方案")],['03',ui("验证"),ui("隔离候选与实际检查")],['04',ui("交付"),ui("审阅分支、PR 与验收")]].map(([n,title,description])=><div className="pipeline-step" key={n}><span>{n}</span><strong>{title}</strong><small>{description}</small></div>)}</div><div className="card-foot"><span>{ui("当前仓库：")}{record?.repository?.split(/[\\/]/).at(-1)||ui("尚未连接")}</span><button onClick={()=>navigate('configuration')}>{ui("查看维护规则 →")}</button></div></Card>
  <Card title={ui("需要关注")} meta={<span className="caption">{attention.length}{ui(" 项已读取的待办")}</span>}>{!record?<Empty title={ui("先连接一个项目")}>{ui("选择本地 Git 仓库，或从 GitHub 取得本地副本。")}</Empty>:attention.length?<>{attention.slice(0,4).map(item=><div className="attention-item" key={item.kind+':'+(item.monitor_id||'')+':'+item.id+':'+item.status}><span className="attention-mark">!</span><div><strong>{item.title}</strong><p>{item.reason}</p><p><strong>{ui("下一步：")}</strong>{item.next}</p>{item.id&&<small>{ui("对应原记录：")}{item.id.slice(0,12)}</small>}</div><button onClick={()=>inspectSituation(item)}>{ui("查看对应记录")}</button></div>)}{attention.length>4&&<p className="muted">{ui("另有 ")}{attention.length-4}{ui(" 项待办，请进入任务、监控或交付页继续核对。")}</p>}</>:<Empty title={ui("当前没有已展示的待处理项")}>{ui("本页只反映已读取的记录。开始新任务前，仍需核对维护规则、来源与模型许可。")}</Empty>}</Card></div>
  <Card title={ui("最近维护任务")} meta={<button onClick={()=>navigate('tasks')}>{ui("全部任务 →")}</button>}>{p.rows.length?<div className="record-list">{p.rows.slice(0,6).map(row=><button className="record-row" key={row.id} onClick={()=>p.selectTask(row.id)}><span className="record-icon">⌁</span><div><strong>{row.title}</strong><small>{row.selection.kind==='issue'?ui("Issue 工作流"):ui("手工维护任务")} · {row.id.slice(0,12)}</small></div><Badge value={row.status}/><span>→</span></button>)}</div>:<Empty title={ui("维护历史从这里开始")}>{ui("实际方案、运行、恢复记录会保存在同一仓库中。")}</Empty>}</Card>
  <div className="quick-grid">{[['issues',ui("查看 Issue 工作流"),ui("读取详情，调查实现与检查。")],['memory',ui("查看记忆与知识"),ui("审阅经历，查询来源与适用性。")],['events',ui("查看监控与待办"),ui("关注失败、阻塞与人工跟进。")]].map(([id,title,description])=><button className="quick-card" key={id} onClick={()=>navigate(id)}><span>↗</span><strong>{title}</strong><small>{description}</small></button>)}</div>
 </>;
 if(page==='delivery')return <><DeliveryInbox p={p}/><IssueWorkspace key={record?.id||'none'} {...p}/></>;
 if(page==='issues'||page==='configuration')return <IssueWorkspace key={record?.id||'none'} {...p}/>;
 if(page==='service')return <ServicePanel {...p}/>;
 if(page==='events')return <EventsWorkspace {...p}/>;
 if(!record||!state)return <Empty title={ui("连接仓库后使用此页面")}><AsyncButton className="primary" onClick={p.connect}>{ui("连接仓库")}</AsyncButton></Empty>;
 if(page==='memory')return <MemoryWorkspace {...p}/>;
 return <Card title={ui("原任务详情")}><button onClick={openOriginal}>{ui("打开当前任务与审批")}</button></Card>;
}

function IssueWorkspace(p:Props){
 const [items,setItems]=useState<Data[]>([]),[page,setPage]=useState(1),[next,setNext]=useState(false),[error,setError]=useState(''),[loading,setLoading]=useState(false),[query,setQuery]=useState(''),[reload,setReload]=useState(0);
 const url=repositoryContext(p.record,p.remoteUrl).remoteUrl;
 const load=()=>setReload(v=>v+1);
 // Only an explicit page change performs this read; no POST or model call on entry.
 useEffect(()=>{let active=true;setError('');setItems([]);setNext(false);if(!url){setLoading(false);return;}setLoading(true);p.request('/api/github/issues?url='+encodeURIComponent(url)+'&page='+page).then(v=>{if(active){setItems(v.items||[]);setNext(v.has_next);}}).catch(e=>{if(active)setError(e.message);}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};},[url,page,reload]);
 const titles:Data={issues:[ui("Issue 工作流"),ui("从可信问题开始，调查、规划、验证并保留交付证据。")],delivery:[ui("交付与验收"),ui("候选、本地分支、远端分支、Draft PR 与关闭分别核验。")],configuration:[ui("维护设置"),ui("一次确认仓库维护边界，具体文件与检查由调查产生。")]};
 const records=p.state?.issue_workbench;
 const deliveryFocus=p.record?.id&&p.deliveryFocus?.workspace_id===p.record.id&&p.deliveryFocus?.kind==='issue'
  ?p.deliveryFocus:null;
 // A task choice carries its workspace and resolves an already-read Issue row.
 // Delivery keeps its own exact focus and takes precedence within that workspace.
 const taskExecution=p.record?.id&&p.taskFocus?.workspace_id===p.record.id
  ?p.rows.find(row=>row.selection?.kind==='issue'&&row.selection.id===p.taskFocus?.execution_id):undefined;
 return <><div className="page-intro compact"><div><span className="eyebrow">{p.page==='delivery'?'DELIVERY':'MAINTENANCE'}</span><h1>{titles[p.page][0]}</h1><p>{titles[p.page][1]}</p></div><AsyncButton feedback={{pending:loading}} disabled={loading||!url} onClick={load}>{ui("刷新 Issues")}</AsyncButton></div>
  {p.page==='configuration'&&p.state&&<div className="metric-grid settings-metrics"><div className="metric"><span>{ui("手工任务范围")}</span><strong>{p.state.policy?.allowed_paths?.length||0}<small>{ui(" 个文件")}</small></strong><small>{ui("与 Issue 长期规则独立")}</small></div><div className="metric"><span>{ui("Issue 维护规则")}</span><strong>{records?.configured?ui("已固定"):ui("待设置")}</strong><small>{ui("变更受原日志绑定约束")}</small></div><div className="metric"><span>{ui("执行模式")}</span><strong>{records?.configuration?.mode==='auto'?ui("授权内自主"):ui("人工审批")}</strong><small>{ui("执行前由控制器再次核验")}</small></div></div>}
  {p.page==='configuration'&&<section className="inline-notice" aria-label={ui("选择维护入口","Choose a maintenance entry")}><h2>{ui("让 Agent 自己发现问题","Let the agent discover problems")}</h2><p>{ui("本地或 GitHub 仓库的提交维护，在“监控与待办”设置目录规则；无需先填写问题描述。下方是 Issue 接手规则与原任务额度。","Configure directory rules in Monitoring & follow-up for local or GitHub commit maintenance; no problem description is required. Issue intake rules and the original task allowance are below.")}</p><button onClick={()=>p.navigate('events')}>{ui("设置提交自动维护 →","Configure automatic commit maintenance →")}</button></section>}
  {p.page==='delivery'&&<div className="delivery-lanes">{[['publications',ui("远端分支")],['pull_requests','Draft PR'],['closures',ui("验收关闭")]].map(([key,title])=><Card key={key} title={title} meta={<span className="count-pill">{records?.[key]?.length||0}</span>}>{records?.[key]?.length?records[key].map((r:Data)=><div className="delivery-record" key={r.id}><Badge value={r.status}/><code>{r.id.slice(0,12)}</code>{r.receipt?.url&&/^https:\/\/github\.com\//.test(r.receipt.url)&&<a href={r.receipt.url} target="_blank" rel="noopener noreferrer">{ui("查看成果 ↗")}</a>}<TaskSituationSummary value={deliveryStageSituation(({publications:"push",pull_requests:"pr",closures:"closure"} as Data)[key],r)} label={ui("原交付阶段的状况与下一步")}/><Evidence value={r}/></div>):<p className="muted">{ui("尚无此阶段记录")}</p>}</Card>)}</div>}
  {error&&<p className="inline-notice error-text" role="alert">{error}</p>}
  <div className={'native-issue '+(p.page==='configuration'?'rules-view':p.page==='delivery'?'delivery-view':'')}>
   {p.page==='issues'&&<label className="search-field"><span>⌕</span><input data-form-assist-draft="true" aria-label={ui("筛选当前页 Issues")} placeholder={ui("搜索当前页的标题或编号…")} value={query} onChange={e=>setQuery(e.target.value)}/></label>}
   <IssueManager record={p.record} state={p.state} focus={deliveryFocus?.issue_number??taskExecution?.selection.number??p.focus} executionFocus={deliveryFocus?.execution_id??taskExecution?.selection.id} items={p.page==='issues'?items.filter(v=>`${v.number} ${v.title}`.toLowerCase().includes(query.toLowerCase())):[]} request={p.request} refresh={p.refresh} view={p.page}/>
  </div>
  {p.page==='issues'&&url&&<div className="pagination native-pagination"><AsyncButton feedback={{pending:loading}} disabled={page===1||loading} onClick={()=>setPage(page-1)}>{ui("上一页")}</AsyncButton><span>{ui("第 ")}{page}{ui(" 页 · 搜索限当前页")}</span><AsyncButton feedback={{pending:loading}} disabled={!next||loading} onClick={()=>setPage(page+1)}>{ui("下一页")}</AsyncButton></div>}
  {p.page==='configuration'&&<BudgetPanel {...p}/>}
 </>;
}

function MemoryWorkspace(p:Props){
 const [tab,setTab]=useState('catalog'),[query,setQuery]=useState('');const op=useOperation(p.record,p.state,p.request,p.refresh),s=p.state!;
 const tabs=[['catalog',ui("统一检索")],['experience',ui("运行经历")],['knowledge',ui("知识与方法")],['sources',ui("来源与结构")],['compaction',ui("上下文整理")]];
 useEffect(()=>{const focus=(event:Event)=>{const detail=(event as CustomEvent).detail;if(detail?.page==='memory'&&tabs.some(([id])=>id===detail.tab))setTab(detail.tab);};window.addEventListener('repocare:capability',focus);return()=>window.removeEventListener('repocare:capability',focus);},[]);
 const entries=(tab==='experience'?s.experience_memory:s.knowledge_memory)?.entries||[];
 return <><div className="page-intro compact"><div><span className="eyebrow">REPOSITORY MEMORY</span><h1>{ui("让每次维护，成为下一次的参考。")}</h1><p>{ui("内容分类、审阅状态与来源有效性分别保留；接受参考不产生执行权限。")}</p></div><button onClick={p.openOriginal}>{ui("高级记忆操作")}</button></div>
  <div className="page-tabs" role="group" aria-label={ui("记忆分类")}>{tabs.map(([id,title])=><button key={id} aria-pressed={tab===id} onClick={()=>setTab(id)}>{title}</button>)}</div>
  {op.status}
  {tab!=='sources'&&<MemoryTools key={tab} {...p} tab={tab}/>}
  {(tab==='experience'||tab==='knowledge')&&<ReferenceSearch key={tab} {...p} kind={tab}/>}
  {tab==='catalog'&&op.output?.memory_query&&<div className="memory-grid">{op.output.memory_query.items?.map((item:Data)=><Card key={item.id} title={item.title||item.topic||item.id} meta={<Badge value={item.freshness}/>}><div className="tag-row"><span className="small-tag">{item.classification||ui("未声明分类")}</span><span className="small-tag">{item.review_state||ui("此类资料无单独审阅")}</span><span className="small-tag">{item.in_context?ui("已装载，仅供参考"):ui("查询结果，尚不代表入模")}</span></div><Evidence value={item.summary} title={ui("匹配内容")}/>{(item.source_refs||[]).map((ref:Data,i:number)=><p className="muted" key={i}>{ref.kind} · {ref.path||ref.id}{ref.start_line?':'+ref.start_line:''} · {ref.sha256?.slice(0,12)}</p>)}</Card>)}</div>}
  {tab==='catalog'&&op.output?.memory_query&&<QueryFeedback value={op.output.memory_query}/>}
  {tab==='catalog'&&<><Card title={ui("统一记忆查询")} meta={<span className="caption">{ui("只读查询 · 不调用模型")}</span>}><form className="query-form" onSubmit={e=>{e.preventDefault();op.send('memory_query',{query,limit:10});}}><input data-form-assist-draft="true" aria-label={ui("记忆查询")} placeholder={ui("输入问题、标识符或曾经遇到的错误…")} maxLength={2000} value={query} onChange={e=>setQuery(e.target.value)}/><AsyncButton feedback={op.feedback} className="primary" disabled={op.busy||!query.trim()}>{ui("查询记忆")}</AsyncButton></form><Evidence value={s.memory_catalog} title={ui("已登记的记忆来源与查询范围")}/></Card><div className="quick-grid">{[[ui("来源资料"),'source_memory'],[ui("运行经历"),'experience_memory'],[ui("知识方法"),'knowledge_memory']].map(([title,key])=><Card key={key} title={title}><span className="small-tag">{key==='source_memory'?(s.source_memory?.enabled?ui("已绑定"):ui("未绑定")):ui("原记录可查询")}</span><p className="muted">{key==='source_memory'?ui("显式绑定的文档、案例与决策出处"):ui("沿用当前仓库的原始日志与独立审阅记录")}</p></Card>)}</div></>}
  {['experience','knowledge'].includes(tab)&&<div className="memory-grid">{entries.length?entries.map((entry:Data)=><Card key={entry.id} title={entry.issue||entry.note?.title||entry.id.slice(0,12)} meta={<Badge value={entry.review}/>}><div className="tag-row"><Badge value={entry.freshness}/><span className="small-tag">{entry.classification||'historical'}</span></div>{entry.reason&&<p className="inline-notice">{entry.reason}</p>}{entry.note&&<p>{entry.note.summary||entry.note.detail||entry.note.description}</p>}<Evidence value={entry} title={ui("出处、适用性与完整记录")}/><div className="button-row"><AsyncButton feedback={op.feedback} disabled={op.busy||entry.freshness!=='verified'||(tab==='knowledge'&&entry.lifecycle!=='active')||entry.review==='accepted'} onClick={()=>op.send(tab==='experience'?'experience_review':'knowledge_review',{[tab==='experience'?'run_id':'knowledge_id']:entry.id,revision:entry.revision,review_status:'accepted'})}>{ui("接受供未来任务参考")}</AsyncButton><AsyncButton feedback={op.feedback} disabled={op.busy||entry.review==='rejected'} onClick={()=>op.send(tab==='experience'?'experience_review':'knowledge_review',{[tab==='experience'?'run_id':'knowledge_id']:entry.id,revision:entry.revision,review_status:'rejected'})}>{entry.review==='accepted'?ui("撤销参考"):ui("拒绝候选")}</AsyncButton></div>{tab==='knowledge'&&entry.note&&<KnowledgeEditor {...p} entry={entry}/>}</Card>):<Empty title={tab==='experience'?ui("尚无运行经历"):ui("尚无知识候选")}>{ui("安全结束的运行可形成经历；知识提炼仍需显式操作和独立审阅。")}</Empty>}</div>}
  {tab==='sources'&&<SourceQueries {...p}/>}
  {tab==='compaction'&&<ContextOperations {...p}/>}
 </>;
}

export function QueryFeedback({value}:{value:Data}){
 return <section className="native-card" aria-label={ui("记忆查询说明")}><h3>{ui("查询结果：")}{value.returned_items??value.items?.length??0}{ui(" 项")}</h3>{!value.items?.length&&<p className="inline-notice">{ui("没有匹配资料。可核对来源绑定、审阅与当前适用范围，再调整查询；这不表示仓库没有问题。")}</p>}
 {(value.excluded||[]).map((item:Data,i:number)=><p className="muted" key={i}>{ui("未提供：")}{item.adapter||item.id} · {item.reason}</p>)}
 {value.topic_peers?.available_groups>0&&<><h4>{ui("同主题资料对照")}</h4><p className="muted">{ui("相同主题不代表结论一致；各自的分类、审阅和新鲜度保持独立。")}</p>{(value.topic_peers.groups||[]).map((group:Data,i:number)=><Evidence key={i} value={group} title={group.topic+' · '+group.total_items+ui("项资料")}/>)}{value.topic_peers.omitted_groups>0&&<p>{ui("还有")}{value.topic_peers.omitted_groups}{ui("个分组未展开，请缩小查询。")}</p>}</>}
 <Evidence value={value.retrieval} title={ui("本次检索方式")}/></section>;
}

function MemoryTools(p:Props&{tab:string}){
 const s=p.state!,op=useOperation(p.record,p.state,p.request,p.refresh);
 const [enabled,setEnabled]=useState(Boolean(p.tab==='catalog'?s.memory_retrieval?.enabled:s.experience_memory?.enabled));
 const [selected,setSelected]=useState<string[]>([]),[consent,setConsent]=useState(false);
 const [config,setConfig]=useState<Data>(s.task_compaction?.settings||{});
 const preview=op.output?.knowledge_preview||op.output?.compaction_preview;
 if(p.tab==='catalog')return <Card title={ui("检索方式")}><p className="muted">{ui("默认使用词面检索。本地混合检索需要已登记的模型包，不会自动下载。")}</p><label className="check-label"><input type="checkbox" checked={enabled} disabled={!s.memory_retrieval?.available} onChange={e=>setEnabled(e.target.checked)}/>{ui("启用已登记的本地混合检索")}</label><AsyncButton feedback={op.feedback} disabled={op.busy||!s.memory_retrieval?.revision||!s.memory_retrieval?.available} onClick={()=>op.send('retrieval_settings',{enabled,revision:s.memory_retrieval.revision})}>{ui("保存检索设置")}</AsyncButton>{op.status}</Card>;
 if(p.tab==='experience')return <Card title={ui("未来任务的经历参考")}><label className="check-label"><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>{ui("之后的新任务可检索已接受经历")}</label><p className="muted">{ui("不会向旧任务补入资料，也不会继承历史批准。")}</p><AsyncButton feedback={op.feedback} disabled={op.busy||!s.experience_memory?.settings_revision} onClick={()=>op.send('experience_settings',{enabled,revision:s.experience_memory.settings_revision})}>{ui("保存新任务设置")}</AsyncButton>{op.status}</Card>;
 if(p.tab==='knowledge')return <Card title={ui("从经历中提炼知识或方法")}><p className="muted">{ui("先选择1—3条已接受且来源有效的经历。预览不调用模型，确认提炼会使用原共享额度。")}</p><div className="reflection-sources">{(s.experience_memory?.entries||[]).filter((e:Data)=>e.review==='accepted'&&e.freshness==='verified').map((entry:Data)=><label className="check-label" key={entry.id}><input type="checkbox" checked={selected.includes(entry.id)} disabled={!selected.includes(entry.id)&&selected.length>=3} onChange={e=>{setConsent(false);setSelected(e.target.checked?[...selected,entry.id]:selected.filter(id=>id!==entry.id));}}/>{entry.issue||entry.id.slice(0,12)}</label>)}</div><AsyncButton feedback={op.feedback} disabled={op.busy||!selected.length||selected.length>3} onClick={()=>{setConsent(false);op.send('knowledge_preview',{experience_ids:selected});}}>{ui("预览知识提炼")}</AsyncButton>{op.status}{preview?.revision&&JSON.stringify(preview.experience_ids)===JSON.stringify(selected)&&<div className="approval-box"><label className="check-label"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>{ui("确认使用当前模型与原额度提炼，结果另行审阅")}</label><AsyncButton feedback={op.feedback} disabled={op.busy||!consent} onClick={()=>{setConsent(false);op.send('knowledge_propose',{experience_ids:selected,revision:preview.revision});}}>{ui("确认提炼这组经历")}</AsyncButton></div>}</Card>;
 if(p.tab==='compaction')return <Card title={ui("新任务的上下文整理策略")}><p className="muted">{ui("仅影响之后的新任务。摘要使用原请求账，已有任务保持原配置。")}</p><label className="check-label"><input type="checkbox" checked={Boolean(config.enabled)} onChange={e=>setConfig({...config,enabled:e.target.checked})}/>{ui("为新任务开启原生上下文整理")}</label><div className="context-fields">{[['context_window_tokens',ui("声明的模型上下文容量")],['trigger_tokens',ui("开始整理的输入阈值")],['keep_recent_tokens',ui("保留近期原文量")],['summary_max_output_tokens',ui("摘要输出上限")]].map(([field,title])=><label key={field}>{title}<input data-form-assist-draft="true" type="number" min={0} step={1} value={config[field]??0} onChange={e=>{const value=Number(e.target.value);setConfig(previous=>({...previous,[field]:value}));}}/></label>)}</div><AsyncButton feedback={op.feedback} disabled={op.busy||!s.task_compaction?.settings_revision} onClick={()=>op.send('context_settings',{config,revision:s.task_compaction.settings_revision})}>{ui("保存为新任务设置")}</AsyncButton>{op.status}</Card>;
 return null;
}

function BudgetPanel(p:Props){
 const s=p.state;const [total,setTotal]=useState(s?.request_budget??p.hub?.request_budget??1),[confirmed,setConfirmed]=useState(false);const op=useOperation(p.record,p.state,p.request,p.refresh);
 const preview=op.output?.budget_preview||op.output;
 return <Card title={s?.budget_monitor_id?ui("监控共享额度与固定范围","Monitor allowance and fixed scope"):ui("手工任务额度与固定范围","Manual allowance and fixed scope")} meta={<button onClick={p.openOriginal}>{ui("原范围与高级控件 →")}</button>}><p className="muted">{usageText(s)}{ui("。调整上限不会清零原消费或自动开跑。")}</p><p className="muted">{ui("合法上限由服务的共享请求配置校验；这里只填写草稿，预览和确认不会清除原消费。","The service validates limits against its shared request configuration. Editing this draft, previewing, and confirming never clear original consumption.")}</p><div className="query-form"><label>{ui("新的请求总上限")}<input aria-label={ui("新的请求总上限")} type="number" min={1} step={1} value={total} onChange={e=>{setTotal(Number(e.target.value));setConfirmed(false);}}/></label><AsyncButton feedback={op.feedback} disabled={op.busy||!p.record} onClick={()=>{setConfirmed(false);op.send('budget_preview',{new_total:total});}}>{ui("预览额度调整")}</AsyncButton></div>{op.status}{preview?.revision&&preview.new_total===total&&<div className="approval-box"><label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{ui("确认按此预览调整总上限，保留已用和未知占用")}</label><AsyncButton feedback={op.feedback} disabled={!confirmed||op.busy} onClick={()=>{setConfirmed(false);op.send('extend_budget',{new_total:total,revision:preview.revision});}}>{ui("确认此额度预览")}</AsyncButton></div>}<Evidence value={s?.policy} title={ui("原固定手工维护范围")}/></Card>;
}

function EventsWorkspace(p:Props){
 const [filter,setFilter]=useState('all'),[query,setQuery]=useState('');
 const monitorOp=useOperation({api_prefix:'/api/'},p.hub,p.request,p.refresh);
 const monitors=monitorsForWorkspace(p.hub,p.record,typeof location==='undefined'?'':location.origin);
 const events=monitorEvents(monitors);
 const opened=workspaceFromLaunch(p.hub,monitorOp.output?.launch_url,typeof location==='undefined'?'':location.origin);
 return <><div className="page-intro compact"><div><span className="eyebrow">EVENTS & FOLLOW-UP</span><h1>{ui("把需要关注的事，放在一起。")}</h1><p>{ui("监控事件与原维护事实分别保留来源和版本。")}</p></div><AsyncButton onClick={p.connect}>{ui("连接与监控配置")}</AsyncButton></div>{monitorOp.status}
  <NativeRepositoryControls {...p}/>
  <CorrelationControls p={p}/>
  <MonitorNotifications/>
  {opened&&<button onClick={()=>p.selectWorkspace?.(opened.id)} disabled={!p.selectWorkspace}>{ui("进入原监控方案与运行报告")}</button>}
  <section className="native-card"><header><h2>{ui("当前仓库监控")}</h2></header>{monitors.length?monitors.map((m:Data)=><div key={m.id} className="monitor-record"><div className="card-foot"><strong>{m.spec?.branch||m.branch||ui("指定分支")}</strong><Badge value={m.enabled?'running':'paused'}/></div><p>{usageText(m)}</p><p>{m.error||ui("沿用原检查、轮询间隔与额度授权。")}</p><div className="button-row"><AsyncButton feedback={monitorOp.feedback} disabled={monitorOp.busy} onClick={()=>monitorOp.send(m.enabled?'monitor_pause':'monitor_resume',{monitor_id:m.id})}>{m.enabled?ui("暂停监控"):ui("恢复监控")}</AsyncButton><AsyncButton feedback={monitorOp.feedback} disabled={monitorOp.busy||!m.enabled} onClick={()=>monitorOp.send('monitor_check',{monitor_id:m.id})}>{ui("立即检查")}</AsyncButton></div></div>):<Empty title={ui("尚未配置监控")}>{ui("通过连接与监控配置选择分支、间隔、检查和授权。")}</Empty>}</section>
  {monitors.map((m:Data)=><MonitorModelAuthorization key={m.id} p={p} monitor={m}/>)}
  <div className="page-tabs"><button aria-pressed={filter==='all'} onClick={()=>setFilter('all')}>{ui("全部")}</button><button aria-pressed={filter==='events'} onClick={()=>setFilter('events')}>{ui("监控事件")}</button><button aria-pressed={filter==='notes'} onClick={()=>setFilter('notes')}>{ui("维护事实与人工待办")}</button></div>
  {filter!=='notes'&&<section className="native-card"><header><h2>{ui("监控事件")}</h2></header><input data-form-assist-draft="true" aria-label={ui("筛选事件")} value={query} placeholder={ui("筛选当前监控事件…")} onChange={e=>setQuery(e.target.value)}/>{events.filter((e:Data)=>JSON.stringify(e).toLowerCase().includes(query.toLowerCase())).map((e:Data)=>{const target=workspaceFromLaunch(p.hub,e.launch_url,typeof location==='undefined'?'':location.origin);return <article className="event-row" key={e.key}><div><strong>{e.message||ui("监控事件")}</strong><Badge value={e.status}/><TaskSituationSummary value={taskSituation({monitors},{kind:"monitor",id:e.id,monitor_id:e.monitor_id})} label={ui("当前监控事件的状况与下一步")}/>{e.ci_error&&<p className="error-text">{ui("CI信息未取得：")}{e.ci_error}</p>}{e.tests?.length>0&&<TestTable tests={e.tests} title={ui("本次监控检查")}/>}{(e.ci_failures||[]).filter((f:Data)=>/^https:\/\/github\.com\//.test(f.url)).map((f:Data)=><p key={f.url}><a href={f.url} target="_blank" rel="noopener noreferrer">{f.name} · {f.conclusion}</a></p>)}{e.notification&&<p className="muted">{ui("原通知标识：")}{e.notification}</p>}{e.can_open&&<AsyncButton feedback={monitorOp.feedback} disabled={monitorOp.busy} onClick={()=>monitorOp.send('monitor_open',{monitor_id:e.monitor_id,event_id:e.id})}>{ui("审阅这个监控事件的原方案")}</AsyncButton>}{target&&<button disabled={!p.selectWorkspace} onClick={()=>p.selectWorkspace?.(target.id)}>{ui("查看此事件原运行报告")}</button>}{!e.root_preparation&&<CiMonitorDiagnosis event={e}/>}<RootMonitorPreparation event={e} p={p}/><MonitorHistory p={p} event={e}/>{e.root_late_ci?.result&&<CiMonitorDiagnosis event={{ci_diagnosis:e.root_late_ci.result,ci_diagnosis_historical:e.root_late_ci_historical}}/>}<Evidence value={e}/></div></article>;})}{!events.length&&<p className="muted">{ui("当前没有监控事件。")}</p>}</section>}
  {filter!=='events'&&p.state&&p.record&&<MaintenancePanel {...p}/>}
 </>;
}
