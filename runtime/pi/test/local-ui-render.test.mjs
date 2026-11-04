import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
const code=await fs.readFile(new URL('../../../src/repo_maintainer/local_ui_assets/app.js',import.meta.url),'utf8');

// Only the DOM methods used by the initial render. No backend, browser state or model is contacted.
class Element {
 constructor(){this.children=[];this.dataset={};this.value='';this.classList={add(){},toggle(){}};this.textContent='';}
 setAttribute(name,value){this.attributes??=new Map();this.attributes.set(name,String(value));}
 getAttribute(name){return this.attributes?.get(name)??null;}
 append(...children){this.children.push(...children);}
 prepend(...children){this.children.unshift(...children);}
 replaceChildren(...children){this.children=children;}
 get firstChild(){return this.children[0]||null;}
 querySelectorAll(){return [];}
 querySelector(){return null;}
}
function text(node){return [node.textContent,...node.children.map(child=>typeof child==='string'?child:text(child))].join(' ');}
async function mount(experiences,overrides={}){
 const elements=new Map();const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const root={querySelector:selector=>get(selector.match(/"([^"]+)"/)[1])};
 const state={repository:'synthetic/repository',provider:'Synthetic UI test',policy:{allowed_paths:['calculator.py']},
  allow_auto:false,request_budget:50,requests_used:0,requests_remaining:50,requests_reserved_unknown:0,active:null,
  runtime:{build_sha256:'a'.repeat(64),started_at:'synthetic',max_output_tokens:128000,planning_timeout_seconds:1800,code_changed:false},
  jobs:[],plans:[],runs:[],recovery_cards:[],planning_attempts:[],source_memory:{enabled:false},structure_index:{enabled:false},
  experience_memory:experiences,knowledge_memory:{entries:[]},task_compaction:{},maintenance_memory:{status:'disabled'},
  memory_catalog:{adapters:[]},memory_retrieval:{enabled:false,available:false}};
 Object.assign(state,overrides);
 const sandbox={window:{},document:{getElementById:()=>({}),createElement:()=>new Element(),createTextNode:value=>({textContent:value,children:[]})},
  setInterval:()=>1,clearInterval(){},URL,fetch:async()=>({ok:true,status:200,json:async()=>structuredClone(state)})};
 vm.runInNewContext(code,sandbox,{filename:'production-app.js'});
 const mounted=sandbox.window.mountMaintenanceUI(root,{embedded:true,token:'synthetic-only'});await mounted.ready;mounted.dispose();
 return {elements,get};
}
const contextConfig={schema_version:1,enabled:true,context_window_tokens:1048576,trigger_tokens:700000,keep_recent_tokens:256,summary_max_output_tokens:128000};
function taskState(status='verified',enabled=true){
 const identity='a'.repeat(32),budget={request_budget:50,requests_used:11,requests_reserved_unknown:0,requests_remaining:39};
 return {recovery_cards:[{kind:'task',id:identity,status:status==='busy'?'busy':'available',
  task:{id:identity,status,completed_rounds:2},budget,sources:{memory:'verified',structure:'verified'},actions:[]}],
 task_compaction:{settings:contextConfig,settings_revision:'settings',tasks:[{task_id:identity,status,
  config:{...contextConfig,enabled},completed_planning_rounds:2,context_bytes_estimate:70000,can_compact:true,revision:'current'}],compactions:[]}};
}
for(const [status,enabled,expected] of [
 ['verified',true,/已允许.*原任务设置/],['verified',false,/未启用.*原任务设置/],
 ['busy',true,/尚未核验/],['blocked',true,/尚未核验/]]){
 test(`recovery card reports original task compaction ${status}/${enabled}`,async()=>{
  const page=await mount({enabled:false,entries:[]},taskState(status,enabled));
  const facts=descendants(page.get('task-recovery')).find(n=>n.children.some(c=>c.textContent==='上下文压缩'));
  const index=facts.children.findIndex(c=>c.textContent==='上下文压缩');
  assert.match(text(facts.children[index+1]),expected);
 });
}
function descendants(node){return [node,...node.children.flatMap(child=>typeof child==='string'?[]:descendants(child))];}

test('successful experience shows selected-check counts while old failure text remains literal',async()=>{
 const base={id:'a'.repeat(32),review:'pending',freshness:'verified',revision:'fixture',issue:'Addition',
  outcome:'candidate_test_passed',rounds:[{number:1,diagnosis:'Preserve signs',test_exits:[0],change_excerpt:'<literal patch>',
   validation_summary:{scope:'selected_commands',passed:1,total:1,semantic_correctness_proven:false}}]};
 const old={...base,id:'b'.repeat(32),outcome:'test_failed',rounds:[{number:1,diagnosis:'Old failure',test_exits:[1],
  change_excerpt:'<old literal>',failure_excerpt:'AssertionError: <script>literal only</script>'}]};
 const page=await mount({enabled:false,settings_revision:'fixture',entries:[base,old]});
 const content=text(page.get('experience-entries'));
 assert.match(content,/查看原改动与验证摘要/);assert.match(content,/原选定检查：1\/1 条命令通过/);
 assert.match(content,/不代表未覆盖行为正确或已经合并/);assert.match(content,/查看原改动与失败摘录/);
 assert.match(content,/<script>literal only<\/script>/);assert.doesNotMatch(content,/undefined/);
});

test('read-only same-topic peers remain visible beyond top-k without becoming model input',async()=>{
 const peer={adapter:'KnowledgeEntryAdapter',topic:'same topic <literal>',classification:'proposed',review_state:'accepted',
  freshness:'stale',in_context:false,summary:{title:'Compare this historical method'},source_refs:[{kind:'episode_payload',id:'old',sha256:'a'.repeat(64)}]};
 const result={returned_items:1,items:[],excluded:[],topic_peers:{available_groups:2,available_peers:11,omitted_groups:1,
  groups:[{topic:peer.topic,total_items:11,in_results:1,returned_peers:1,omitted_peers:9,peers:[peer]}]}};
 const page=await mount({enabled:false,entries:[]},{jobs:[{action:'memory_query',status:'finished',result:{memory_query:result}}]});
 const content=text(page.get('catalog-result'));
 assert.match(content,/同主题还有 11 项资料/);assert.match(content,/same topic <literal>/);
 assert.match(content,/已接受参考/);assert.match(content,/需按当前源码重新核验/);
 assert.match(content,/此处仅为查询结果/);assert.match(content,/仍有 9 项未展开/);
 assert.match(content,/另有 1 个同主题分组未展开/);assert.match(content,/不替你选择有效规则/);
});
for(const [name,status,canCompact,revision,disabled] of [
 ['matching preview','verified',true,'current',false],['changed checkpoint','verified',true,'old',true],
 ['already compacted','verified',false,'current',true],['busy writer','busy',true,'current',true]]){
 test(`compaction confirmation rejects stale UI preview: ${name}`,async()=>{
  const state=taskState(status);state.task_compaction.tasks[0].can_compact=canCompact;
  state.jobs=[{action:'compaction_preview',status:'finished',result:{compaction_preview:{task_id:'a'.repeat(32),
   revision,can_compact:true,budget:{requests_remaining:40}}}}];
  const page=await mount({enabled:false,entries:[]},state);
  const start=descendants(page.get('context-tasks')).find(n=>n.textContent==='确认消耗原额度整理上下文');
  assert.ok(start);assert.equal(Boolean(start.disabled),disabled);
 });
}
for(const [name,experience] of [['without journal memory',{enabled:false,entries:[]}],
 ['with original journal settings',{enabled:false,entries:[],settings_revision:'a'.repeat(64)}]]){
 test('initial UI renders knowledge, compaction, maintenance and retrieval '+name,async()=>{
  const page=await mount(experience);
  assert.equal(page.get('notice').textContent,'已连接。请生成方案，开始本地审批流程。');
  assert.match(text(page.get('knowledge-controls')),/提炼使用当前模型和原请求额度/);
  assert.match(text(page.get('catalog-result')),/当前使用词面检索/);
  assert.match(text(page.get('catalog-result')),/本次启动未登记本地语义模型/);
  assert.ok(page.get('context-controls').children.length>0);
  assert.ok(page.get('maintenance-items').children.length>0);
 });
}
