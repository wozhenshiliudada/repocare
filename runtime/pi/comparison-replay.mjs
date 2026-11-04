// One bounded local replay. No live provider, credentials, retries or parallel model calls.
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {startMock} from './test/mock-server.mjs';

export async function comparisonReplay(directory, showcase=false, caseId=null) {
  const root=fileURLToPath(new URL('../..',import.meta.url));
  const driver=path.join(root,'tests/comparison_replay.py');
  async function python(args) {
    return new Promise((resolve,reject)=>{
      const child=spawn(process.env.PI_TEST_PYTHON||'python',[driver,...args],{windowsHide:true,stdio:['ignore','pipe','pipe']});
      let output='',error='';
      child.stdout.on('data',v=>output+=v);child.stderr.on('data',v=>error+=v);
      child.on('error',reject);
      child.on('close',code=>code===0?resolve(JSON.parse(output)):reject(new Error(`Comparison driver exit ${code}: ${error.slice(-4000)}`)));
    });
  }
  const selection=caseId?['--case',caseId]:[];
  const descriptors=await python(['--describe',...selection]);
  const mock=await startMock({comparisonCases:descriptors});
  try {
    const result=await python(['--directory',path.resolve(directory),'--url',mock.url,...selection,...(showcase?['--showcase']:[])]);
    const observed=mock.requests.length;
    const reported=Object.values(result.metrics).reduce((n,m)=>n+m.synthetic_model_requests,0);
    if(observed!==reported||observed>descriptors.length*4*4)throw new Error('Replay model-request accounting differs or exceeds ceiling');
    const trace=mock.requests.map(body=>{
      const user=body.messages.find(m=>m.role==='user');
      const context=JSON.parse(typeof user.content==='string'?user.content:user.content[0].text);
      return {case:context.issue.split(':')[1],feedback_present:Boolean(context.feedback),model:body.model};
    });
    await fs.writeFile(path.join(directory,'transport-validation.json'),JSON.stringify({passed:true,observed_requests:observed,reported_requests:reported,
      evidence_kind:'real Pi AgentSession + scripted loopback SSE',real_model_requests:0,trace},null,2)+'\n');
    return result;
  }finally{await mock.close();}
}

if(process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url){
  const args=process.argv.slice(2),index=args.indexOf('--directory');
  if(index<0||!args[index+1])throw new Error('Provide --directory NEW_ABSOLUTE_DIRECTORY');
  comparisonReplay(path.resolve(args[index+1]),args.includes('--showcase')).then(r=>console.log(JSON.stringify(r,null,2))).catch(e=>{console.error(e);process.exitCode=1;});
}
