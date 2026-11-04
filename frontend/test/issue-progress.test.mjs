import {test} from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {build} from 'vite';
const out=path.resolve('.demo/issue-progress-'+process.pid);
await build({configFile:false,logLevel:'error',build:{ssr:'src/IssueProgressPanel.tsx',outDir:out,emptyOutDir:false,
 rolldownOptions:{external:['react','react-dom','react/jsx-runtime'],output:{entryFileNames:'progress.mjs'}}}});
const {IssueProgressPanel}=await import(pathToFileURL(path.join(out,'progress.mjs')));
const execution={id:'e',run_id:'r'};
const proposal={id:'p',core:{repository:'example/project',issue_number:7,run_id:'r',stage:'accepted',body:'safe <script> & token-filtered'},
 status:'prepared',approval_sha256:'a'.repeat(64),github_actor:{id:42,login:'maintainer'}};
function render(extra={}){let calls=0;const html=renderToStaticMarkup(React.createElement(IssueProgressPanel,
 {execution,comments:[],output:null,disabled:false,send:()=>{calls++;},...extra}));assert.equal(calls,0);return html;}
test('preview displays exact escaped body and approval starts unchecked/disabled',()=>{
 const html=render({output:{action:'issue_progress_preview',execution_id:'e',result:proposal}});
 assert.match(html,/safe &lt;script&gt;/);assert.doesNotMatch(html,/<script>|checked=""/);
 assert.match(html,/disabled=""[^>]*>批准发送这条评论/);
 assert.match(html,/maintainer/);
});
test('unknown results expose read-only reconciliation without another send approval',()=>{
 const row={...proposal,status:'post_unknown'};
 const html=render({comments:[row]});
 assert.match(html,/网络结果未知/);assert.match(html,/只读核对这条评论，不重发/);
 assert.doesNotMatch(html,/批准发送这条评论/);
});
test('another execution cannot reuse this preview or expose its approval',()=>{
 const html=render({output:{action:'issue_progress_preview',execution_id:'another',result:proposal}});
 assert.doesNotMatch(html,/批准发送这条评论|safe &lt;script&gt;/);
});
test('investigation-only comments do not expose candidate or delivered stages',()=>{
 const html=render({sourceKind:'investigation',execution:{id:'diagnosis',run_id:'diagnosis',investigation_id:'diagnosis'}});
 assert.match(html,/调查结果|信息不足或阻塞/);
 assert.doesNotMatch(html,/value="candidate_passed"|value="delivered"/);
});

test('an actual execution retains its comment stages and preview when it also exposes its original investigation',()=>{
 const html=render({execution:{...execution,investigation_id:'original-diagnosis'},
  output:{action:'issue_progress_preview',execution_id:'e',result:proposal}});
 assert.match(html,/value="candidate_passed"/);assert.match(html,/value="delivered"/);
 assert.match(html,/safe &lt;script&gt;/);assert.match(html,/批准发送这条评论/);
});

test('actual comment preview handlers keep explicit execution and investigation authorities separate',async()=>{
 const calls=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
 function tree(extra){
  const previous=internal.H;
  internal.H={useState:value=>[value,()=>{}],useEffect:()=>{}};
  try{return IssueProgressPanel({execution:{...execution,investigation_id:'original-diagnosis'},
   comments:[],output:null,disabled:false,send:async(...args)=>calls.push(args),...extra});}
  finally{internal.H=previous;}
 }
 function find(node,label){
  if(!node||typeof node!=='object')return;
  if(Array.isArray(node))return node.map(n=>find(n,label)).find(Boolean);
  return node.props?.children===label?node:find(node.props?.children,label);
 }
 await find(tree({}), '预览此阶段的评论').props.onClick();
 await find(tree({sourceKind:'investigation',execution:{id:'diagnosis',run_id:'diagnosis'}}), '预览此阶段的评论').props.onClick();
 assert.deepEqual(calls,[['issue_progress_preview',{execution_id:'e',stage:'accepted'}],
  ['issue_progress_investigation_preview',{investigation_id:'diagnosis',stage:'accepted'}]]);
});
