// Deterministic UI-only receipts. No API, Provider or real GitHub writes.
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AsyncButton} from '../src/AsyncButton';
import {WorkbenchPages,useOperation} from '../src/WorkbenchPages';
import {IssueManager} from '../src/IssueManager';
import {IssueProgressPanel} from '../src/IssueProgressPanel';
import {CiInvestigation} from '../src/CiInvestigation';
import packet from '../../docs/tasks/ci-investigation/example.synthetic.json';
import '../src/style.css';
import '../src/workspace.css';

const fixture:any={calls:[],resolve:null,reject:null,complete:null};
(window as any).buttonFixture=fixture;
const record={id:'synthetic',repository:'C:/fixture',repository_id:'synthetic',api_prefix:'/fixture/',token:'SYNTHETIC_ONLY'};
function Fixture(){
 const [state,setState]=useState<any>({runtime:{code_changed:false},jobs:[],plans:[],runs:[],
  requests_used:0,requests_remaining:10,policy:{allowed_paths:[]},memory_catalog:{enabled:true},
  issue_workbench:{configured:true,configuration:{workflow_ids:[]},investigations:[],environments:[],approvals:[],executions:[]}});
 const [message,setMessage]=useState('');
 async function request(path:string,_token?:string,body?:any):Promise<any>{
  const id=body?.request_id||'synthetic-read';fixture.calls.push({path,id});
  return new Promise((resolve,reject)=>{
   fixture.resolve=(queued=true)=>{
    if(queued){setState((s:any)=>({...s,jobs:[{id,status:'running'}]}));resolve({id,status:'running'});}
    else resolve({status:'finished',synthetic:true});
   };
   fixture.reject=(uncertain=false)=>{const e:any=new Error(uncertain?'合成网络结果未知，勿重发':'合成权限拒绝，未执行');e.uncertain=uncertain;reject(e);};
   fixture.complete=(failed=false)=>setState((s:any)=>({...s,jobs:[{id,status:failed?'failed':'finished',error:failed?'合成后台任务失败':'',result:{synthetic:true}}]}));
  });
 }
 const refresh=async()=>{},op=useOperation(record,state,request,refresh);
 const p:any={page:'memory',record,state,hub:{jobs:[],workspaces:[],monitors:[]},request,refresh,navigate:()=>{},openOriginal:()=>{},connect:()=>{},selectTask:()=>{},rows:[]};
 return <main className="workspace-pages"><h1>合成按钮等待反馈</h1><p>以下回执由测试手动放行，不连接真实模型或GitHub。</p>
  <section className="native-card" aria-label="共享操作按钮"><h2>同一队列中的两个原操作</h2>
   <div className="button-row">{['A','B'].map(id=><AsyncButton key={id} data-testid={'action-'+id} feedback={op.feedback} disabled={op.busy} onClick={()=>op.send('inspect',{id})}>核查样例 {id}</AsyncButton>)}</div>{op.status}
  </section>
  <section className="native-card" aria-label="独立异步读取"><AsyncButton data-testid="plain-read" onClick={async()=>{try{await request('/read');setMessage('读取完成');}catch(e){setMessage((e as Error).message);}}}>刷新只读资料</AsyncButton><p role="status">{message}</p></section>
  <section aria-label="实际记忆页面"><WorkbenchPages {...p}/></section>
  <section className="native-card" aria-label="实际Issue入口"><IssueManager record={record} state={state} items={[]} request={request} refresh={refresh}/></section>
  <IssueProgressPanel execution={{id:'synthetic-execution',run_id:'synthetic-run'}} comments={[]} output={null} disabled={op.busy} send={(action,payload)=>op.send(action,payload)} feedback={op.feedback}/>
  <CiInvestigation repositoryUrl="https://github.com/example/ci-contract-fixture" runs={[]} initialSelection={{url:'https://github.com/example/ci-contract-fixture',run_id:1001,attempt:1}}
   readEvidence={async(selection)=>{await request('/ci-read');const value=structuredClone(packet);value.selection.job_id=selection.job_id??null;value.log=selection.job_id?value.log:null;return value;}}/>
 </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
