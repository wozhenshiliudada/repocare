// Local synthetic repair evidence; no live provider or external repository input.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {runCase} from './test/replay-case.mjs';

const directory=path.resolve(process.argv[2]);
await fs.mkdir(directory,{recursive:true});
assert.equal((await fs.readdir(directory)).length,0,'Preserve previous smoke evidence; never replay it');
const contexts=[];
const result=await runCase(directory,'retry_fix','approve','candidate_ready',{maxAttempts:3,
  onRequest:body=>{const raw=body.messages.find(message=>message.role==='user').content;
    contexts.push(JSON.parse(typeof raw==='string'?raw:raw[0].text));}});
const revision=contexts.find(context=>context.revision_context)?.revision_context;
assert.ok(revision?.test_focus.tests[0].assertion_excerpt.includes('AssertionError'));
assert.equal(result.source_unchanged,true);
assert.equal(result.cleanup_status,'discarded');
await fs.writeFile(path.join(directory,'observed-revision-context.json'),JSON.stringify(revision,null,2)+'\n');
await fs.writeFile(path.join(directory,'verified-result.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify({status:result.status,cleanup:result.cleanup_status,
  rounds:result.rounds.map(round=>round.verification),model_requests:result.provider_evidence.total_requests_used,
  revision_receipt:result.provider_evidence.revision_context_sha256,source_unchanged:result.source_unchanged,
  live_calls:0}));
