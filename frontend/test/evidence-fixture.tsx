// Browser-only component acceptance fixture. No API client, controller or model invocation.
import React from 'react';
import {createRoot} from 'react-dom/client';
import {ActivityTimeline} from '../src/ActivityTimeline';
import {CheckComparison,TestTable} from '../src/EvidenceView';
import '../src/style.css';
import '../src/workspace.css';
const state={planning_attempts:[{id:'authored-receipt',status:'finished',evidence:{requests:1,requests_started:1,tool_calls:1,events_complete:true,events_dropped:0,events:[{type:'tool_execution_start',tool:'read_source',path:'fixture/example.py'},{type:'tool_execution_end',tool:'read_source',is_error:false}],usage:[{input:100,output:20,cacheRead:0,totalTokens:120}]}}]};
const run={planning_origin:{provider_evidence:{attempt_id:'authored-receipt'}},events:[{time:'作者样例时间1',stage:'基线检查'},{time:'作者样例时间2',stage:'候选检查'}],rounds:[{number:1,provider_evidence:{requests:0,usage:[]}}]};
const command=['python','-m','unittest'];
const baseline=[{command,returncode:1,stdout:'Ran 1 test\nFAILED (failures=1)'}],candidate=[{command,returncode:0,stdout:'Ran 1 test\nOK'}];
const edge=[{command:['zero'],returncode:0,stdout:'Ran 0 tests'},{command:['skip'],returncode:0,stdout:'Ran 2 tests\nOK (skipped=2)'},{command:['blocked'],returncode:1,stderr:'ModuleNotFoundError: authored_fixture'},{command:['truncated'],returncode:0,output_truncated:true},{command:['unknown'],returncode:0,stdout:'No structured test counter'}];
function Fixture(){return <main style={{height:'100vh',overflow:'auto',padding:24}}><div style={{maxWidth:960,margin:'0 auto'}}><h1>只读组件验收夹具</h1><p>以下是明确编写的回执样例，用于检查前端渲染与交互，不是一次真实任务的成果；此页没有网络请求、模型调用或执行按钮。</p><ActivityTimeline state={state} run={run}/><CheckComparison baseline={baseline} candidate={candidate}/><TestTable tests={edge} title="作者样例：有效性与阻塞状态"/></div></main>;}
createRoot(document.getElementById('root')!).render(<Fixture/>);
