import {ui,uiLabels} from './ui-language.mjs';
import React,{useEffect,useRef,useState} from 'react';
import {AsyncButton} from './AsyncButton';
import {ciSource,createCiController,validateCiSelection} from './ci-model.mjs';
import {CiComparableBaseline} from './CiComparableBaseline';
type Data=Record<string,any>;
type Selection={url:string;run_id:number;attempt:number;job_id?:number;expected_repository_id?:number;expected_head_sha?:string};
export type CiProps={repositoryUrl:string;runs?:Data[];initialSelection?:Selection;readEvidence:(selection:Selection)=>Promise<Data>;readRuns?:(url:string)=>Promise<Data>};
const states:Record<string,string>=uiLabels({ready:'本次范围读取完整',partial:'读取不完整',blocked:'读取受阻',stale:'来源已变化'});

export function CiEvidenceView({value,selection,selectedJobId}:{value:Data;selection:Selection;selectedJobId?:number}){
 const log=value.log?.job_id===selectedJobId?value.log:null;
 return <section className="ci-evidence" aria-label={ui("CI只读证据")}>
  <p role="status">{states[value.evidence_status]||ui("未知读取状态")} · CI {value.run?.status||ui("未取得")} / {value.run?.conclusion||ui("尚无结论")}</p>
  <p className="inline-notice">{ui("日志仅提供线索；尚未复现，未证明根因或修复成功。")}</p>
  {value.synthetic&&<p className="inline-notice">{ui("合成样例，不是实网 CI 结果。")}</p>}
  <a href={ciSource(selection)} target="_blank" rel="noopener noreferrer">{ui("在 GitHub 查看此 Run / Attempt")}</a>
  <dl className="property-list"><dt>{ui("源码 SHA")}</dt><dd><code>{value.run?.head_sha||ui("未取得")}</code></dd><dt>{ui("观察时间")}</dt><dd>{value.observed_at||ui("未取得")}</dd><dt>{ui("Jobs 覆盖")}</dt><dd>{value.coverage?.captured_jobs??0} / {value.coverage?.total_jobs??ui("未知")}{ui("；遗漏 ")}{value.coverage?.omitted_jobs??ui("未知")}</dd></dl>
  {value.coverage?.steps_truncated&&<p>{ui("步骤列表已截断。")}</p>}
  {(value.jobs||[]).map((job:Data)=><details key={job.id} open={job.id===selectedJobId}><summary>{job.name} · {job.status} / {job.conclusion||ui("尚无结论")}</summary><a href={ciSource(selection,job.id)} target="_blank" rel="noopener noreferrer">GitHub Job #{job.id}</a><ol>{(job.steps||[]).map((step:Data)=><li key={step.number}>{step.number}. {step.name} · {step.status} / {step.conclusion||ui("尚无结论")}</li>)}</ol></details>)}
  {log&&<section aria-label={ui("选定Job日志")}><h3>Job #{log.job_id} · {log.status}</h3>{['capture_truncated','excerpt_truncated','line_truncated','decoding_replacement','tail_line_omitted'].some(key=>log[key])&&<p className="inline-notice">{ui("日志捕获或显示不完整，请结合原站核查。")}</p>}<p>{log.captured_bytes??0}{ui(" 字节 · SHA256 仅对应实际捕获的原始字节，不代表完整远端日志。")}</p><code>{log.captured_sha256||ui("未捕获")}</code><pre>{log.excerpt}</pre></section>}
  {value.errors?.length>0&&<ul aria-label={ui("CI读取限制")}>{value.errors.map((error:Data,index:number)=><li key={index}>{error.stage} · {error.code}</li>)}</ul>}
  <CiComparableBaseline value={value.comparable_baseline} packet={value} selectedJobId={selectedJobId}/>
 </section>;
}

export function CiInvestigation({repositoryUrl,runs=[],initialSelection,readEvidence,readRuns}:CiProps){
 const [listed,setListed]=useState<Data>({url:repositoryUrl,items:[],error:''});
 useEffect(()=>{
  if(!readRuns)return;
  let stopped=false;setListed({url:repositoryUrl,items:[],error:''});
  readRuns(repositoryUrl).then(value=>{if(!stopped)setListed({url:repositoryUrl,items:value.items||[],error:''});})
   .catch(()=>{if(!stopped)setListed({url:repositoryUrl,items:[],error:ui("Actions 列表暂不可读，可在高级定位中指定 Run / Attempt。")});});
  return()=>{stopped=true;};
 },[repositoryUrl,readRuns]);
 const choices=readRuns?(listed.url===repositoryUrl?listed.items:[]):runs;
 const [runId,setRunId]=useState(''),[attempt,setAttempt]=useState(''),[selectedJob,setSelectedJob]=useState('');
 const [snapshot,setSnapshot]=useState<Data>({value:null,busy:false,error:'',repositoryUrl});
 const callback=useRef(readEvidence);callback.current=readEvidence;
 const controller=useRef<ReturnType<typeof createCiController>|null>(null);
 useEffect(()=>{
  const next=createCiController((value:Selection)=>callback.current(value),(state:Data)=>setSnapshot({...state,repositoryUrl}));
  let initial:Selection|null=null;try{if(initialSelection?.url===repositoryUrl){validateCiSelection(initialSelection);initial=initialSelection;}}catch{/* Invalid external selection stays empty. */}
  controller.current=next;next.select(initial);setRunId(initial?String(initial.run_id):'');setAttempt(initial?String(initial.attempt):'');setSelectedJob('');
  return()=>{next.dispose();if(controller.current===next)controller.current=null;};
 },[repositoryUrl,initialSelection?.run_id,initialSelection?.attempt]);
 const current=snapshot.repositoryUrl===repositoryUrl?snapshot:{value:null,busy:false,error:''};
 const value=current.value;
 function reset(){controller.current?.select(null);setSelectedJob('');}
 function selection():Selection{return {url:repositoryUrl,run_id:Number(runId),attempt:Number(attempt)};}
 function read(){if(!controller.current||controller.current.busy)return;controller.current.select(selection());void controller.current.read();}
 function readJob(){
  if(!controller.current||!value?.repository||!value?.run||!selectedJob)return;
  void controller.current.read({job_id:Number(selectedJob),expected_repository_id:value.repository.id,expected_head_sha:value.run.head_sha});
 }
 let valid=false;try{validateCiSelection(selection());valid=true;}catch{/* Keep the form editable. */}
 return <section aria-label={ui("CI精确证据调查")} className="native-card ci-investigation"><h2>{ui("CI 精确证据调查")}</h2><p className="muted">{ui("只读；不会调用模型、创建修复任务或更改审批。")}</p>
  {readRuns&&listed.url===repositoryUrl&&listed.error&&<p role="alert">{listed.error}</p>}
  <label>{ui("选择运行")}<select data-form-assist-draft="true" aria-label={ui("选择CI运行")} value={runId} disabled={current.busy} onChange={event=>{const run=choices.find((item:Data)=>String(item.id)===event.target.value);reset();setRunId(event.target.value);setAttempt(run?.run_attempt?String(run.run_attempt):'');}}><option value="">{ui("从 Actions 运行中选择…")}</option>{choices.map((run:Data)=><option key={run.id} value={String(run.id)}>{run.title||run.name||run.id}</option>)}</select></label>
  <details><summary>{ui("高级定位")}</summary><label>Run ID<input data-form-assist-draft="true" aria-label="CI Run ID" inputMode="numeric" value={runId} onChange={event=>{reset();setRunId(event.target.value);}}/></label></details>
  <label>Attempt<input data-form-assist-draft="true" aria-label="CI Attempt" inputMode="numeric" value={attempt} onChange={event=>{reset();setAttempt(event.target.value);}}/></label>
  <AsyncButton feedback={{pending:current.busy}} disabled={!valid||current.busy} onClick={read}>{ui("读取 / 刷新只读证据")}</AsyncButton>
  {current.error&&<p role="alert">{current.error}</p>}
  {value&&<><label>{ui("选择 Job")}<select data-form-assist-draft="true" aria-label={ui("选择CI Job")} value={selectedJob} disabled={current.busy} onChange={event=>setSelectedJob(event.target.value)}><option value="">{ui("选择一个已核验 Job…")}</option>{(value.jobs||[]).map((job:Data)=><option key={job.id} value={String(job.id)}>{job.name} · #{job.id}</option>)}</select></label><AsyncButton feedback={{pending:current.busy}} disabled={current.busy||!selectedJob||value.evidence_status==='stale'} onClick={readJob}>{ui("读取选定 Job 日志")}</AsyncButton><CiEvidenceView value={value} selection={selection()} selectedJobId={selectedJob?Number(selectedJob):undefined}/></>}
 </section>;
}
