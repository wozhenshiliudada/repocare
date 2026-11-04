import {test} from 'node:test';
import assert from 'node:assert/strict';
import {failureEligible,failureExpiry,failurePreviewMatches,failureRemaining,failureLabel,failureCanResume} from '../src/failure-followup-model.mjs';

const expiry='2026-10-27T01:00:00Z',binding={monitor_id:'monitor',root_rules_sha256:'rules',model:'model'};
const event={id:'event',sha:'sha',root_execution:{finished_at:'done',run_id:'run',plan_id:'plan',binding:{ci_failure:{workflow_id:1}},model_authorization:{id:'model-grant'}}};
const budget={request_budget:8,requests_used:2,requests_reserved_unknown:0,requests_remaining:6};
const grant={id:'model-grant',core_sha256:'original',active:true,core:{binding,expires_at:expiry}};
const monitor={id:'monitor',root_rules:{sha256:'rules'},model_authorization:grant,...budget};
const proposal={revision:'revision',preview:{monitor_id:'monitor',event_id:'event',max_successors:1,expires_at:expiry,
 parent:{run_id:'run',head_sha:'sha'},model_authorization:{id:'model-grant',core_sha256:'original'},binding,budget,same_sha_only:true,may_charge:true}};

test('failure successor entry needs a finished original CI repair and original model grant',()=>{
 assert.equal(failureEligible(event),true);
 for(const key of ['finished_at','run_id','plan_id','model_authorization'])assert.equal(failureEligible({...event,root_execution:{...event.root_execution,[key]:null}}),false);
 assert.equal(failureEligible({...event,root_execution:{...event.root_execution,binding:{}}}),false);
 assert.equal(failureEligible({...event,failure_successor:{grant_id:'old'}}),false);
 assert.equal(failureEligible({...event,failure_parent_run_id:'manual-run',root_execution:{...event.root_execution,run_id:null}}),true);
});
test('failure expiry cannot exceed the original model grant or silently accept invalid input',()=>{
 const now=Date.parse('2026-10-27T00:00:00Z');
 assert.equal(failureExpiry(expiry,expiry,now),expiry);
 for(const value of ['', 'bad','2026-10-27T00:00:00Z','2026-10-27T00:00:59Z','2026-10-27T01:00:01Z'])assert.equal(failureExpiry(value,expiry,now),'');
 assert.equal(failureExpiry(expiry,undefined,now),'');
});
test('preview binds original parent, model, rules, inputs and current shared account',()=>{
 const matches=(p=proposal,m=monitor,e=event,max=1,until=expiry)=>failurePreviewMatches(p,m,e,max,until);
 assert.equal(matches(),true);
 for(const max of [0,4,1.5,NaN])assert.equal(matches(proposal,monitor,event,max),false);
 for(const patch of [{requests_used:3},{requests_remaining:5},{requests_reserved_unknown:1},{request_budget:9},
  {root_rules:{sha256:'changed'}},{model_authorization:{...grant,active:false}},
  {model_authorization:{...grant,id:'replacement'}},{model_authorization:{...grant,core_sha256:'changed'}}])assert.equal(matches(proposal,{...monitor,...patch}),false);
 assert.equal(matches(proposal,monitor,{...event,sha:'new-sha'}),false);
 assert.equal(matches(proposal,monitor,{...event,root_execution:{...event.root_execution,run_id:'new-run'}}),false);
 assert.equal(matches({...proposal,preview:{...proposal.preview,may_charge:false}}),false);
 assert.equal(matches(proposal,monitor,event,1,'2026-10-27T00:59:00Z'),false);
 const manual={...event,failure_parent_run_id:'run',root_execution:{...event.root_execution,run_id:null}};
 assert.equal(matches(proposal,monitor,manual),true);
});
test('claimed successors consume the bounded count and restart pause stays visible',()=>{
 const row={status:'paused',effective_status:'restart_paused',core:{max_successors:2},children:[{status:'failed_no_replay'}]};
 assert.equal(failureRemaining(row),1);assert.equal(failureLabel(row),'重启后已暂停');
 assert.equal(failureRemaining({...row,children:Array(4).fill({})}),0);
 const paused={...row,enabled:false,children:[],core:proposal.preview},now=Date.parse('2026-10-27T00:00:00Z');
 assert.equal(failureCanResume(paused,grant,now),true);
 for(const patch of [{active:false},{id:'new-model-grant'},{core_sha256:'changed'},
  {core:{...grant.core,binding:{...binding,model:'changed'}}}])assert.equal(failureCanResume(paused,{...grant,...patch},now),false);
 assert.equal(failureCanResume(paused,grant,Date.parse(expiry)),false);
 assert.equal(failureCanResume({...paused,status:'blocked'},grant,now),false);
});
