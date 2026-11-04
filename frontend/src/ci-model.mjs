import {ui} from './ui-language.mjs';
// Draft CI-only controller. The caller injects a read function; no default fetch.
const positive=value=>Number.isSafeInteger(value)&&value>0;
export function ciRepository(url){
 if(typeof url!=='string'||!/^https:\/\/github\.com\//.test(url)||/[\x00-\x20\x7f]/.test(url))throw Error('invalid CI repository');
 let value;try{value=new URL(url);}catch{throw Error('invalid CI repository');}
 if(value.protocol!=='https:'||value.host!=='github.com'||value.username||value.password||value.search||value.hash)throw Error('invalid CI repository');
 const parts=value.pathname.replace(/^\/|\/$/g,'').split('/');
 if(parts.length!==2)throw Error('invalid CI repository');
 const [owner,raw]=parts,name=raw.replace(/\.git$/,'');
 if(!/^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(owner)||!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/.test(name)||['.','..'].includes(name))throw Error('invalid CI repository');
 return owner+'/'+name;
}
export function validateCiSelection(value){
 ciRepository(value.url);
 if(!positive(value.run_id)||!positive(value.attempt)||value.attempt>10000||value.job_id!=null&&!positive(value.job_id))throw Error('invalid CI selection');
 if(value.expected_repository_id!=null&&!positive(value.expected_repository_id))throw Error('invalid CI repository ID');
 if(value.expected_head_sha!=null&&(typeof value.expected_head_sha!=='string'||!/^[a-f0-9]{40}$/.test(value.expected_head_sha)))throw Error('invalid CI SHA');
 return value;
}
export function ciSource(selection,jobId){
 validateCiSelection(selection);const repo=ciRepository(selection.url);
 if(jobId!=null&&!positive(jobId))throw Error('invalid CI job ID');
 return 'https://github.com/'+repo+'/actions/runs/'+selection.run_id+(jobId==null?'/attempts/'+selection.attempt:'/job/'+jobId);
}
export function validateCiEvidence(value,selection){
 validateCiSelection(selection);const repo=ciRepository(selection.url).toLowerCase();
 if(!value||value.schema_version!==1||typeof value.synthetic!=='boolean'||value.read_only!==true||value.repair_verified!==false||value.root_cause_proven!==false||
    !['ready','partial','blocked','stale'].includes(value.evidence_status)||!Array.isArray(value.errors)||value.errors.length>16||
    !Array.isArray(value.jobs)||value.jobs.length>500)throw Error('invalid CI evidence');
 if(typeof value.observed_at!=='string'||value.observed_at.length>100||value.errors.some(error=>typeof error?.stage!=='string'||error.stage.length>32||typeof error?.code!=='string'||error.code.length>64))throw Error('invalid CI observation');
 if(value.selection?.repository_slug?.toLowerCase()!==repo||value.selection?.run_id!==selection.run_id||value.selection?.attempt!==selection.attempt||
    (value.selection?.job_id??null)!==(selection.job_id??null))throw Error('CI selection mismatch');
 if(value.run){
  if(!positive(value.repository?.id)||value.repository?.slug?.toLowerCase()!==repo||value.run.id!==selection.run_id||value.run.attempt!==selection.attempt||
     !/^[a-f0-9]{40}$/.test(value.run.head_sha))throw Error('CI run mismatch');
  if(value.evidence_status!=='stale'&&((selection.expected_repository_id!=null&&value.repository.id!==selection.expected_repository_id)||
     (selection.expected_head_sha!=null&&value.run.head_sha!==selection.expected_head_sha)))throw Error('CI expected binding mismatch');
  const ids=new Set();let steps=0;
  for(const job of value.jobs){
   if(!positive(job.id)||ids.has(job.id)||job.run_id!==selection.run_id||job.run_attempt!==selection.attempt||job.head_sha!==value.run.head_sha||!Array.isArray(job.steps)||typeof job.name!=='string'||typeof job.status!=='string'||job.conclusion!=null&&typeof job.conclusion!=='string')throw Error('CI job mismatch');
   const numbers=new Set();for(const step of job.steps){if(!positive(step.number)||numbers.has(step.number)||typeof step.name!=='string'||typeof step.status!=='string'||step.conclusion!=null&&typeof step.conclusion!=='string')throw Error('invalid CI step');numbers.add(step.number);}
   ids.add(job.id);steps+=job.steps.length;
  }
  if(steps>1000)throw Error('CI step limit');
 }else if(!['blocked','stale'].includes(value.evidence_status)||value.jobs.length)throw Error('CI missing run');
 if(value.run&&(typeof value.run.status!=='string'||value.run.conclusion!=null&&typeof value.run.conclusion!=='string'))throw Error('invalid CI state');
 for(const key of ['captured_jobs','total_jobs','omitted_jobs'])if(value.coverage?.[key]!=null&&(!Number.isSafeInteger(value.coverage[key])||value.coverage[key]<0))throw Error('invalid CI coverage');
 if(value.log&&(value.log.job_id!==selection.job_id||typeof value.log.excerpt!=='string'||value.log.excerpt.length>65536||typeof value.log.status!=='string'||value.log.captured_sha256!=null&&(typeof value.log.captured_sha256!=='string'||!/^[a-f0-9]{64}$/.test(value.log.captured_sha256))))throw Error('CI log mismatch');
 return value;
}
export function createCiController(readEvidence,onChange){
 let epoch=0,disposed=false,state={selection:null,value:null,busy:false,error:''};
 const emit=next=>{state=next;if(!disposed)onChange(state);};
 return {
  get busy(){return state.busy;},
  select(selection){epoch++;emit({selection:selection?{...selection}:null,value:null,busy:false,error:''});},
  async read(extra={}){
   if(disposed||state.busy)return;
   const current=++epoch;
   let selection;try{selection=validateCiSelection({...state.selection,...extra});}
   catch{emit({...state,error:ui("请选择有效的仓库、Run ID 和 Attempt。")});return;}
   const previous=state.value?{...state.value,log:null}:null;
   emit({...state,value:previous,busy:true,error:''});
   try{
    const value=validateCiEvidence(await readEvidence({...selection}),selection);
    if(!disposed&&current===epoch)emit({...state,value,busy:false,error:''});
   }catch{
    if(!disposed&&current===epoch)emit({...state,busy:false,error:ui("无法读取 CI 证据，请核对来源、权限后手动重试。")});
   }
  },
  dispose(){disposed=true;epoch++;},
 };
}
