import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {verifiedContinuation} from '../continuation-feedback.mjs';
const sha=s=>createHash('sha256').update(s).digest('hex');
const request={issue:'fixture',base_sha:'a'.repeat(40),task_session:{binding:{task_id:'b'.repeat(32)}},
  files:[{path:'calculator.py',editable:true},{path:'tests/test_calculator.py',editable:false}]};
const value={schema_version:1,task_id:'b'.repeat(32),run_id:'c'.repeat(32),base_sha:request.base_sha,
  issue_sha256:sha(request.issue),planning_revision:1,previous_plan_sha256:'d'.repeat(64),
  proposed_content_hashes:{'calculator.py':'e'.repeat(64)},tests:[{command:['python','-m','unittest'],
    returncode:1,timed_out:false,stdout:'',stderr:'FAIL: fixture'}]};
test('same-task failure is diagnostic data',()=>assert.equal(verifiedContinuation(value,request),value));
test('no feedback preserves old request protocol',()=>assert.equal(verifiedContinuation(undefined,request),null));
for(const [name,change] of [
  ['task',v=>v.task_id='0'.repeat(32)],['issue',v=>v.issue_sha256='0'.repeat(64)],
  ['base',v=>v.base_sha='0'.repeat(40)],['successful tests',v=>v.tests[0].returncode=0],
  ['readonly patch',v=>v.proposed_content_hashes={'tests/test_calculator.py':'e'.repeat(64)}],
  ['scope expansion',v=>v.proposed_content_hashes={'outside.py':'e'.repeat(64)}],
  ['oversize',v=>v.tests[0].stderr='x'.repeat(48000)],['malformed tests',v=>v.tests[0].returncode=true]
])test('reject '+name,()=>{const changed=structuredClone(value);change(changed);
  assert.throws(()=>verifiedContinuation(changed,request));});
