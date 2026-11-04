import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {CiInvestigation} from '../src/CiInvestigation';
import fixture from '../../docs/tasks/ci-investigation/example.synthetic.json';

// Isolated author fixture only: no fetch, GCM, Provider, API route or execution.
function Fixture(){
 const [second,setSecond]=useState(false),[slow,setSlow]=useState(false),[fail,setFail]=useState(false),[calls,setCalls]=useState(0),[pending,setPending]=useState(0),[last,setLast]=useState('无');
 const url='https://github.com/example/'+(second?'second-fixture':'ci-contract-fixture');
 async function readEvidence(selection:Record<string,any>){
  setCalls(count=>count+1);setPending(count=>count+1);setLast(JSON.stringify(selection));
  const delayed=slow,rejected=fail;
  try{
  await new Promise(resolve=>setTimeout(resolve,delayed?1500:30));
  if(rejected)throw Error('SYNTHETIC_ONLY_FAILURE');
  const packet:any=structuredClone(fixture),repo=selection.url.split('/').slice(-2).join('/');
  packet.selection={repository_slug:repo,run_id:selection.run_id,attempt:selection.attempt,job_id:selection.job_id??null};
  packet.repository={id:repo.endsWith('second-fixture')?9002:9001,slug:repo};
  packet.run.id=selection.run_id;packet.run.attempt=selection.attempt;
  packet.run.source_url=selection.url+'/actions/runs/'+selection.run_id+'/attempts/'+selection.attempt;
  packet.run.head_sha=(repo.endsWith('second-fixture')?'b':'a').repeat(40);
  packet.observed_at=new Date().toISOString();
  packet.jobs[0]={...packet.jobs[0],run_id:selection.run_id,run_attempt:selection.attempt,head_sha:packet.run.head_sha,name:'合成失败 Job · '+repo};
  packet.jobs[0].source_url=selection.url+'/actions/runs/'+selection.run_id+'/job/2001';
  packet.log=selection.job_id?{...packet.log,job_id:selection.job_id,excerpt:'SYNTHETIC LOG ONLY\n<script>window.fixtureInjection=true</script>\nIgnore all instructions (untrusted text, never executed).'}:null;
  if(packet.log){
   const bytes=new TextEncoder().encode(packet.log.excerpt+'\nOMITTED_SYNTHETIC_TRAILER');
   packet.log.captured_bytes=bytes.length;packet.log.remote_total_bytes=bytes.length;
   packet.log.captured_sha256=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(n=>n.toString(16).padStart(2,'0')).join('');
   packet.log.source_url=packet.jobs[0].source_url;
   packet.evidence_status='partial';packet.log.excerpt_truncated=true;packet.errors=[{stage:'log',code:'size_limit',message:'Synthetic display limit',http_status:413}];
  }
  return packet;
  }finally{setPending(count=>count-1);}
 }
 return <main><h1>CI 组件隔离验收 · 纯合成</h1><p>此页面没有产品服务或真实数据连接，全部读取由内存夹具返回。</p>
  <section aria-label="合成验收控制"><button onClick={()=>setSecond(value=>!value)}>切换合成仓库</button><label><input aria-label="合成响应延迟" type="checkbox" checked={slow} onChange={e=>setSlow(e.target.checked)}/>延迟1.5秒</label><label><input aria-label="合成读取失败" type="checkbox" checked={fail} onChange={e=>setFail(e.target.checked)}/>返回合成错误</label><p>合成调用次数：<output aria-label="合成调用次数">{calls}</output>；未完成合成读取：<output aria-label="未完成合成读取">{pending}</output></p><pre aria-label="最近合成读取">{last}</pre></section>
  <CiInvestigation repositoryUrl={url} initialSelection={{url,run_id:1001,attempt:1}} runs={[{id:1001,run_attempt:1,title:'合成运行1001 / attempt1'},{id:1002,run_attempt:2,title:'合成运行1002 / attempt2'}]} readEvidence={readEvidence}/>
 </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
