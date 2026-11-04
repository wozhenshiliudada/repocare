// A local synthetic transport for the Python selected-profile acceptance boundary.
import assert from 'node:assert/strict';
import {ModelRuntime} from '@earendil-works/pi-coding-agent';
import {generatePlan} from '../plan.mjs';
import {effectiveModelLimits} from '../model-limits.mjs';
import {startMock} from './mock-server.mjs';
let input='';for await(const chunk of process.stdin)input+=chunk;
const request=JSON.parse(input),limits=effectiveModelLimits({modelId:process.env.PI_MAINTENANCE_MODEL,
 baseUrl:process.env.PI_MAINTENANCE_BASE_URL});
let registrations=0;
const original=ModelRuntime.prototype.registerProvider;
ModelRuntime.prototype.registerProvider=function(name,config){
 assert.equal(config.models[0].contextWindow,limits.contextWindowTokens);
 assert.equal(config.models[0].maxTokens,limits.maxOutputTokens);registrations++;
 return original.call(this,name,config);
};
const mock=await startMock({script:(_body,turn)=>turn===1?{name:'propose_repair',args:{diagnosis:'addition uses subtraction',risk:'low',
 edits:[{path:'calculator.py',replacements:[{oldText:'a - b',newText:'a + b'}]}]}}:{content:'submitted'}});
try{
 const result=await generatePlan(request,{mockUrl:mock.url});
 assert.equal(registrations,1);assert.equal(mock.requests.length,1);
 for(const body of mock.requests){assert.equal(body.max_tokens,limits.maxOutputTokens);
  const message=body.messages.find(m=>m.role==='user');
  const packet=JSON.parse(typeof message.content==='string'?message.content:message.content[0].text);
  assert.equal(packet.max_output_tokens,limits.maxOutputTokens);}
 process.stdout.write(JSON.stringify(result));
}finally{ModelRuntime.prototype.registerProvider=original;await mock.close();}
