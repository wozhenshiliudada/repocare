import {test} from 'node:test';
import assert from 'node:assert/strict';
import {taskRows,operationTask,graphLanes,loadPreferences,fileTree} from '../src/model.mjs';

test('a new manual operation selects its exact plan among existing Issue executions',()=>{
 const state={plans:[{id:'new-plan',plan:{issue:'same title'}}],recovery_cards:[{kind:'plan',id:'new-plan',task:{id:'manual-task'}}],
  issue_workbench:{executions:[{id:'existing-issue',task_id:'issue-task',issue_number:7}]}};
 assert.equal(taskRows(state)[0].id,'issue-task');
 assert.equal(operationTask(state,{plan_id:'new-plan'}).id,'manual-task');
 assert.equal(operationTask(state,{plan_id:'missing'}),null);
 assert.equal(operationTask(state,{}),null);
});

test('Issue executions project original Native identity and never enter manual run controls',()=>{
 const state={recovery_cards:[{kind:'task',id:'native',task:{id:'native'}}],issue_workbench:{
  executions:[{id:'execution',task_id:'native',run_id:'original-run',investigation_id:'token',issue_number:7,status:'awaiting-review'}],
  investigations:[{id:'token',snapshot:{issue:{title:'Actual issue title'}}}]}};
 const [row]=taskRows(state);assert.equal(taskRows(state).length,1);assert.equal(row.id,'native');
 assert.equal(row.title,'#7 Actual issue title');assert.equal(row.status,'awaiting-review');
 assert.deepEqual(row.selection,{kind:'issue',id:'execution',number:7});assert.deepEqual(row.runs,[]);
 assert.equal(row.approved,undefined);assert.equal(row.requests_remaining,undefined);
});

test('original task identifiers group plan and run without inventing approvals or budgets',()=>{
 const state={plans:[{id:'p',plan:{issue:'repair'},status:'consumed',run_id:'r'}],runs:[{run_id:'r',status:'candidate_ready'}],recovery_cards:[{kind:'plan',id:'p',task:{id:'native'}}]};
 const rows=taskRows(state);assert.equal(rows.length,1);assert.equal(rows[0].id,'native');assert.equal(rows[0].runs[0],state.runs[0]);assert.deepEqual(rows[0].selection,{kind:'run',id:'r'});assert.equal(rows[0].approved,undefined);
});
test('legacy/unbound records remain distinguishable and unlinked checkpoints remain visible',()=>{
 const rows=taskRows({plans:[{id:'same',plan:{issue:'old'}}],runs:[{run_id:'same'}],recovery_cards:[{kind:'task',id:'checkpoint',task:{id:'native'}}]});
 assert.equal(new Set(rows.map(r=>r.id)).size,3);assert.equal(rows[0].selection.kind,'task');
});
test('topology preserves input order despite skewed dates; ten parent lanes remain represented',()=>{
 const parents=Array.from({length:10},(_,i)=>'p'+i);
 const input=[{sha:'merge',parents,timestamp:0},...parents.map((sha,i)=>({sha,parents:[],timestamp:100-i}))];
 const rows=graphLanes(input);assert.deepEqual(rows.map(r=>r.sha),input.map(c=>c.sha));assert.equal(rows[0].edges.length,10);assert.equal(rows[0].after.length,10);assert.equal(rows[10].lane,9);
});
test('diamond graph converges shared parent and keeps missing parent edge',()=>{
 const rows=graphLanes([{sha:'m',parents:['a','b']},{sha:'a',parents:['p']},{sha:'b',parents:['p']},{sha:'p',parents:['missing']}]);
 assert.equal(rows[2].edges[0].to,rows[3].lane);assert.equal(rows[3].edges[0].parent,'missing');
});
test('preference load rejects invalid schemas and tolerates blocked tab storage',()=>{
 const blocked={getItem(){throw new Error('denied');}};assert.equal(loadPreferences(blocked).theme,'light');
 const bad={getItem(){return JSON.stringify({version:1,theme:'unsafe',fontSize:999});}};assert.equal(loadPreferences(bad).fontSize,15);
 const valid={getItem(){return JSON.stringify({version:1,theme:'system',fontSize:17,panel:'issues',repository:'stable',task:'original'});}};assert.equal(loadPreferences(valid).repository,'stable');
});
test('file tree retains exact nested paths and does not interpret special property names',()=>{
 const root=fileTree(['src/core/a.py','src/b.py','.gitignore','__proto__/safe.txt']);
 assert.equal(root.size,3);assert.equal(root.get('src').file,undefined);
 assert.equal(root.get('src').children.get('core').children.get('a.py').path,'src/core/a.py');
 assert.equal(root.get('__proto__').children.get('safe.txt').file,true);
});
