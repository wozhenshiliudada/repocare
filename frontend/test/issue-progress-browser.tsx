import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {IssueProgressPanel} from '../src/IssueProgressPanel';
import '../src/style.css';
function App(){
 const [output,setOutput]=useState<any>(null),[comments,setComments]=useState<any[]>([]),[writes,setWrites]=useState(0);
 const execution={id:'e'.repeat(32),run_id:'a'.repeat(32)};
 async function send(action:string,payload:any){
  if(action==='issue_progress_preview')setOutput({action,execution_id:execution.id,result:{id:'b'.repeat(64),
   status:'prepared',approval_sha256:'c'.repeat(64),github_actor:{id:42,login:'fixture-maintainer'},core:{repository:'example/project',issue_number:7,
   run_id:execution.run_id,stage:payload.stage,body:'RepoCare：候选通过，尚未交付\n运行：'+execution.run_id+'\n检查：unit；通过 4，失败 0，跳过 0\n本页上游为合成，无真实GitHub写入。'}}});
  else if(action==='issue_progress_send'){
   const row={...output.result,status:'post_unknown'};setComments([row]);setWrites(w=>w+1);setOutput({action,result:row});
  }else if(action==='issue_progress_reconcile'){
   setComments(rows=>rows.map(row=>({...row,status:'posted',receipt:{url:'https://github.com/example/project/issues/7#issuecomment-123'}})));
   setOutput({action,result:{status:'posted'}});
  }
 }
 return <main style={{maxWidth:960,margin:'24px auto',padding:24}}><h1>Issue 评论合成验收</h1><p>本机组件夹具 · 真实评论写入 0 · 模拟写入计数 <span data-testid="writes">{writes}</span></p>
  <IssueProgressPanel execution={execution} comments={comments} output={output} disabled={false} send={send}/></main>;
}
createRoot(document.getElementById('root')!).render(<App/>);
