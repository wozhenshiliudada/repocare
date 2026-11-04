import {ui} from './ui-language.mjs';
// Read-only presentation of original receipts; these labels never authorize an action.
export function checkName(test){
 const command=test.command||test.argv||[];
 return test.command_kind==='controller_behavior_contract'&&Array.isArray(command)&&command[0]==='repocare:behavior-v1'
  ?ui("固定行为案例验收（由控制器执行）"):Array.isArray(command)?command.join(' ')||test.name||ui("见原回执"):ui("见原回执");
}
export function checkPresentation(test){
 const text=[test.stdout,test.stderr].filter(Boolean).join('\n');
 const counts=[...text.matchAll(/Ran (\d+) tests?|(?:#|ℹ) tests (\d+)/g)].flatMap(m=>m.slice(1).filter(v=>v!==undefined).map(Number));
 const count=typeof test.tests_run==='number'?test.tests_run:counts.length?counts.reduce((a,b)=>a+b,0):null;
 const skipped=[...text.matchAll(/skipped=(\d+)|(?:#|ℹ) skipped (\d+)|(\d+) skipped/g)].flatMap(m=>m.slice(1).filter(v=>v!==undefined).map(Number));
 let category=test.category;
 if(test.timed_out)category='timeout';
 else if(test.output_truncated)category='inconclusive_truncated';
 else if(!category){
  if(/ModuleNotFoundError|ImportError:|ERR_MODULE_NOT_FOUND|MODULE_NOT_FOUND|Cannot find (?:module|package)/.test(text))category='environment_blocked';
  else if(count===0||/no tests ran|collected 0 items/.test(text))category='zero_tests';
  else if(count!==null&&skipped.length&&Math.max(...skipped)>=count)category='zero_effective_tests';
  else if(count===null)category='inconclusive_no_test_count';
  else category=test.returncode===0?'passed':'test_failed';
 }
 const labels={timeout:ui("超时"),inconclusive:ui("行为或环境结果不完整"),inconclusive_truncated:ui("输出截断，结论不完整"),environment_blocked:ui("环境阻塞"),zero_tests:ui("零测试"),zero_effective_tests:ui("全跳过或无有效测试"),inconclusive_no_test_count:(test.returncode===0?ui("命令通过"):typeof test.returncode==='number'?ui("命令失败"):ui("退出码未知"))+ui(" · 测试计数未知"),passed:test.command_kind==='controller_behavior_contract'?ui("行为案例通过"):ui("命令通过"),test_failed:ui("代码或测试失败")};
 return {category,count,label:labels[category]||ui("未知分类：")+category,effective:category==='passed'||category==='test_failed',passed:test.returncode===0&&category==='passed'};
}
export function compareCommandReceipts(baseline,candidate){
 if(!baseline?.length||baseline.length!==candidate?.length)return null;
 if(baseline.some((t,i)=>!Array.isArray(t.command||t.argv)||!(t.command||t.argv).length||JSON.stringify(t.command||t.argv)!==JSON.stringify(candidate[i].command||candidate[i].argv)))return null;
 return baseline.map((before,i)=>{
  const after=candidate[i],old=checkPresentation(before),next=checkPresentation(after);
  let transition='inconclusive';
  if(!before.timed_out&&!after.timed_out&&Number.isInteger(before.returncode)&&Number.isInteger(after.returncode))
   transition=before.returncode===0?(after.returncode===0?'maintained':'regressed'):(after.returncode===0?'repaired':'still_failing');
  return {command:after.command||after.argv,before:before.returncode,after:after.returncode,transition,effective:old.effective&&next.effective};
 });
}
export function activityEvidence(state,run,execution,plan){
 const records=[];
 const add=(label,evidence)=>{if(evidence&&Object.keys(evidence).length)records.push({label,evidence});};
 const origin=run?.planning_origin?.provider_evidence||plan?.planning_evidence;
 const ids=new Set([origin?.attempt_id,execution?.model_evidence?.attempt_id].filter(Boolean));
 const attempts=(state?.planning_attempts||[]).filter(a=>ids.has(a.id));
 if(origin&&!attempts.some(a=>a.id===origin.attempt_id))add(ui("原方案规划"),origin);
 for(const attempt of attempts)add(ui("原调用 ")+attempt.id,{...(attempt.evidence||attempt.progress),attempt_id:attempt.id,status:attempt.status,started_at:attempt.started_at,finished_at:attempt.finished_at});
 for(const round of run?.rounds||[])add(ui("执行阶段第 ")+round.number+ui(" 轮"),round.provider_evidence);
 if(!records.length&&execution?.model_evidence)add(ui("Issue原执行摘要"),execution.model_evidence);
 return {events:run?.events||[],records};
}
