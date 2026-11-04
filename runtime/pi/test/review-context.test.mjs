import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {verifiedReview} from '../review-context.mjs';
import {experienceHash} from '../experience-context.mjs';
const request={issue:'Fix signed addition',base_sha:'a'.repeat(40),task_session:{binding:{task_id:'b'.repeat(32)}}};
const seal=body=>({...body,context_sha256:experienceHash(body)});
const context={schema_version:1,task_id:'b'.repeat(32),execution_id:'c'.repeat(32),report_sha256:'d'.repeat(64),
  issue_sha256:createHash('sha256').update(request.issue).digest('hex'),base_sha:request.base_sha,
  previous_plan_sha256:null,reason:'Check actual Windows behavior.\nDo not assume prior proposal was applied.',claims_only:true,runtime_authorized:false};
test('review binds exact task issue baseline and historical report while remaining data only',()=>{
  assert.equal(verifiedReview(undefined,request),null);
  const value=seal(context);assert.equal(verifiedReview(value,request),value);
  for(const patch of [{schema_version:true},{task_id:'a'.repeat(32)},{issue_sha256:'e'.repeat(64)},
    {base_sha:'f'.repeat(40)},{runtime_authorized:true},{claims_only:false},{reason:''},
    {reason:'x'.repeat(2001)},{reason:'bad\u0000text'},{commands:['git push']},{previous_plan_sha256:'wrong'}])
    assert.throws(()=>verifiedReview(seal({...context,...patch}),request));
  assert.throws(()=>verifiedReview({...value,reason:'changed'},request));
  assert.throws(()=>verifiedReview(value,{...request,task_session:undefined}));
});
