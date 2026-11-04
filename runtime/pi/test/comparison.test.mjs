import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {comparisonReplay} from '../comparison-replay.mjs';

test('Pi controlled ablation: independent grades, retained failures and real feedback revision',{timeout:120000},async()=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'pi-comparison-'));
  try{
    const directory=path.join(root,'run');
    const result=await comparisonReplay(directory,false,'switch');
    assert.equal(result.passed,true);
    assert.equal(result.inputs_and_first_patches_matched,true);
    assert.equal(result.metrics.patch_only.false_acceptances,1);
    assert.equal(result.metrics.test_gate.failed_candidates_retained,1);
    assert.equal(result.metrics.guarded_one.failed_candidates_retained,0);
    assert.equal(result.metrics.guarded_two.graded_repairs,1);
    const transport=JSON.parse(await fs.readFile(path.join(directory,'transport-validation.json'),'utf8'));
    assert.equal(transport.observed_requests,10);
    assert.equal(transport.real_model_requests,0);
    assert(transport.trace.some(r=>r.feedback_present));
  }finally{
    assert(path.resolve(root).startsWith(path.resolve(os.tmpdir())+path.sep));
    await fs.rm(root,{recursive:true,force:true});
  }
});
