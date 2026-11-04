import {test} from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {build} from 'vite';
const out=path.resolve('.demo/ci-baseline-'+process.pid);
await build({configFile:false,logLevel:'error',build:{ssr:'src/CiComparableBaseline.tsx',outDir:out,emptyOutDir:false,
 rolldownOptions:{external:['react','react-dom','react/jsx-runtime'],output:{entryFileNames:'baseline.mjs'}}}});
const {CiComparableBaseline}=await import(pathToFileURL(path.join(out,'baseline.mjs')));
const current={repository:'example/project',branch:'main',sha:'b'.repeat(40),run_id:20,attempt:1,job_id:200,job_name:'unit',
 workflow:{id:1,path:'.github/workflows/tests.yml',content_sha256:'c'.repeat(64)},synthetic:false,evidence_sha256:'d'.repeat(64)};
const value={status:'comparable',current,baseline:{...current,sha:'a'.repeat(40),run_id:10,job_id:100},
 comparison:{checks:[{argv:['python','-m','unittest'],scope:['tests/']}],environment:{runtime:'python'},dependencies:{lock_sha256:'e'.repeat(64)}},
 diff:{url:'https://evil.invalid/?token=x',relevant_files:[{filename:'src/app.py',status:'modified',patch:'-old\n+<script>bad</script>'}]}};
const packet={repository:{slug:'example/project'},run:{id:20,attempt:1,head_sha:current.sha,workflow_id:1},jobs:[{id:200}],synthetic:false};
const render=(v=value,p=packet)=>renderToStaticMarkup(React.createElement(CiComparableBaseline,{value:v,packet:p}));
test('comparable baseline renders exact provenance, commands and escaped related diff',()=>{
 const html=render();
 for(const text of ['已确认本次证据范围内','main','Run','Attempt','Job','unittest','tests/','runtime','依赖','根因'])assert.ok(html.includes(text),text);
 assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>|evil\.invalid|token=x/);
 assert.match(html,new RegExp('github.com/example/project/compare/'+ 'a'.repeat(40)+'...'+ 'b'.repeat(40)));
});
test('no receipts explain why comparability cannot be confirmed',()=>{
 const html=render({status:'unconfirmed',rejections:[{reason:'missing_reviewed_commands_environment_dependency_receipt'}]});
 assert.match(html,/无法确认可比较基线/);assert.match(html,/实际环境和依赖回执/);assert.doesNotMatch(html,/已确认本次/);
});
test('a stale current selection or workflow receipt cannot render a green baseline',()=>{
 for(const v of [{...value,current:{...current,sha:'f'.repeat(40)}},
  {...value,current:{...current,branch:undefined}},
  {...value,baseline:{...value.baseline,workflow:{...current.workflow,content_sha256:'e'.repeat(64)}}}]){
  const html=render(v);assert.match(html,/无法确认可比较基线/);assert.doesNotMatch(html,/已确认本次/);
 }
});
test('synthetic evidence cannot be projected as a real current comparison',()=>{
 const html=render({...value,current:{...current,synthetic:true},baseline:{...value.baseline,synthetic:true}});
 assert.match(html,/无法确认可比较基线/);
});
