import {test} from 'node:test';
import assert from 'node:assert/strict';
import {repositoryContext,issueEvidence,serviceBlocked,stopOutcome,monitorPayload,workspaceFromLaunch,monitorsForWorkspace} from '../src/operation-model.mjs';

test('an unconnected remote remains browsable without gaining workspace authority',()=>{
 const value=repositoryContext(null,'https://github.com/example/other');
 assert.equal(value.remoteUrl,'https://github.com/example/other');assert.equal(value.canMaintain,false);assert.equal(value.workspace,null);
 assert.equal(repositoryContext({github_url:'https://github.com/example/local'},value.remoteUrl).remoteUrl,'https://github.com/example/local');
});
test('monitor launch accepts only a known workspace on the current service origin',()=>{
 const id='a'.repeat(32),hub={workspaces:[{id}]},origin='http://127.0.0.1:28555';
 assert.equal(workspaceFromLaunch(hub,origin+'/#workspace='+id,origin).id,id);
 for(const url of ['https://example.invalid/#workspace='+id,origin+'/#session=private',origin+'/#workspace='+'b'.repeat(32),'not-a-url'])assert.equal(workspaceFromLaunch(hub,url,origin),null);
});
test('entering a frozen event workspace retains only its authenticated parent monitor',()=>{
 const origin='http://127.0.0.1:28555',id='a'.repeat(32),other='b'.repeat(32);
 const record={id,repository:'/monitor/events/source'};
 const hub={workspaces:[record,{id:other,repository:'/other/source'}],monitors:[
  {id:'parent',spec:{path:'/original/repository'},events:[{launch_url:origin+'/#workspace='+id}]},
  {id:'foreign',spec:{path:'/other/repository'},events:[{launch_url:'https://outside.invalid/#workspace='+id}]},
  {id:'other',spec:{path:'/other/repository'},events:[{launch_url:origin+'/#workspace='+other}]}]};
 assert.deepEqual(monitorsForWorkspace(hub,record,origin).map(m=>m.id),['parent']);
 assert.equal(monitorsForWorkspace(hub,null,origin).length,3);
 assert.deepEqual(monitorsForWorkspace(hub,{id:'c'.repeat(32),repository:'/other/repository'},origin).map(m=>m.id),['foreign','other']);
});
function fixture(){return {issue_workbench:{investigations:[{id:'token-7',status:'investigated',snapshot:{issue:{number:7},token:'token-7'}}],environments:[{id:'token-7',config_sha256:'config',binding:{sha:'bound'}}]},jobs:[
 {action:'issue_investigate',status:'finished',result:{id:'token-7',snapshot:{issue:{number:7}},report:{partial:true,hits:['selected']}}},
 {action:'issue_environment',status:'finished',result:{id:'token-7',config_sha256:'config',binding:{sha:'bound'},environment:{runtime:'python'}}},
 {action:'issue_investigate',status:'finished',result:{id:'token-other',snapshot:{issue:{number:8}},report:{hits:['wrong']}}}
 ]};}
test('report and environment survive remount by deriving only matching original job receipts',()=>{
 const value=issueEvidence(fixture(),7);assert.deepEqual(value.report.report.hits,['selected']);assert.equal(value.report.report.partial,true);assert.equal(value.environment.environment.runtime,'python');assert.equal(value.historical,false);
 assert.equal(issueEvidence(fixture(),8).report,undefined);
});
test('a changed Issue token marks old evidence historical rather than becoming a new approval',()=>{
 const state=fixture();state.jobs.push({action:'issue_read',status:'finished',result:{token:'new-token',issue:{number:7}}});
 assert.equal(issueEvidence(state,7).historical,true);
});
test('a foreign environment binding and unsuccessful jobs cannot replace original evidence',()=>{
 const state=fixture();state.jobs.push({action:'issue_environment',status:'finished',result:{id:'token-7',config_sha256:'config',binding:{sha:'foreign'}}});
 state.jobs.push({action:'issue_investigate',status:'failed',result:{id:'token-7',report:{hits:['failed']}}});
 assert.equal(issueEvidence(state,7).environment.binding.sha,'bound');assert.deepEqual(issueEvidence(state,7).report.report.hits,['selected']);
});
test('JSON field ordering in persisted environment receipts cannot hide an equal original binding',()=>{
 const state=fixture();state.issue_workbench.environments[0].binding={z:{b:2,a:1},a:['fixed',3]};
 state.jobs.find(j=>j.action==='issue_environment').result.binding={a:['fixed',3],z:{a:1,b:2}};
 assert.ok(issueEvidence(state,7).environment);
 state.jobs.find(j=>j.action==='issue_environment').result.binding.a.reverse();
 assert.equal(issueEvidence(state,7).environment,undefined);
});
test('every original activity blocks stopping and missing service data cannot grant it',()=>{
 for(const key of ['active','workspace_active','history_active','monitor_enabled'])assert.equal(serviceBlocked({[key]:true}),true);
 assert.equal(serviceBlocked(null),true);assert.equal(serviceBlocked({}),false);
});
test('stop receipt rejection, acknowledgement and disconnect remain different outcomes',()=>{
 assert.equal(stopOutcome([{id:'a',status:'failed',error:'monitor queued'}],'a').message,'monitor queued');
 assert.equal(stopOutcome([{id:'a',status:'finished',result:{stopping:true}}],'a').status,'acknowledged');
 assert.equal(stopOutcome([],'a',true).status,'unknown');assert.equal(stopOutcome([],'a',false).status,'pending');
 assert.notEqual(stopOutcome([{id:'b',status:'finished',result:{stopping:true}}],'a').status,'acknowledged');
 assert.equal(stopOutcome([{id:'a',status:'failed',result:{error:'original nested refusal'}}],'a').message,'original nested refusal');
 assert.equal(stopOutcome([{id:'a',status:'interrupted'}],'a').status,'unknown');
});
test('native monitor configuration requires consent, clean preview, exact scope and original interval limits',()=>{
 const source={path:'fixture',revision:'fixed',clean:true,github_url:'https://github.com/example/fixture'};
 const policy={allowed_paths:['calculator.py'],readonly_paths:['tests/test_calculator.py'],allowed_tests:[['python','-m','unittest']],timeout_seconds:30};
 const options={branch:'main',interval:300,github:false,confirmed:true};
 const result=monitorPayload(source,policy,options);assert.deepEqual(result.allowed_tests,policy.allowed_tests);assert.equal(result.revision,'fixed');assert.equal(result.github_url,'');
 for(const override of [{confirmed:false},{interval:59},{interval:3601},{interval:60.5},{branch:' '}])assert.equal(monitorPayload(source,policy,{...options,...override}),null);
 assert.equal(monitorPayload({...source,clean:false},policy,options),null);
 assert.equal(monitorPayload({...source,github_url:''},policy,{...options,github:true}),null);
});
