import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {verifiedExperience,experienceHash} from '../experience-context.mjs';
const sha=v=>createHash('sha256').update(v).digest('hex');
const request={issue:'Fix addition 负数',base_sha:'a'.repeat(40),files:[{path:'calculator.py',content:'return a-b'}],task_session:{binding:{task_id:'b'.repeat(32)}}};
const seal=v=>{const {context_sha256,...body}=v;return {...body,context_sha256:experienceHash(body)};};
function sample(){
 const episode={schema_version:1,repository_sha256:'c'.repeat(64),run_id:'d'.repeat(32),task_id:'e'.repeat(32),
  classification:'historical',runtime_authorized:false,issue:'Earlier addition 负数',base_sha:'a'.repeat(40),
  recorded_status:'rolled_back',outcome:'test_failed',rounds:[{number:1,plan_sha256:'f'.repeat(64),diagnosis:'failed negative case',verification:'failed',paths:['calculator.py'],test_exits:[1],test_timeouts:[false]}],
  policy_sha256:'a'.repeat(64),source_files:{'calculator.py':'b'.repeat(64)},evidence:{'run.json':'a'.repeat(64),'repair-execution.json':'b'.repeat(64),'report.md':'c'.repeat(64)}};
 return seal({schema_version:1,task_id:request.task_session.binding.task_id,repository_sha256:episode.repository_sha256,
  issue_sha256:sha(request.issue),planning_base_sha:request.base_sha,planning_files_sha256:sha(JSON.stringify(request.files)),classification:'historical',runtime_authorized:false,
  hits:[{id:episode.run_id,payload_sha256:experienceHash(episode),review_version:2,episode}]});
}
test('strict historical context and absent legacy field',()=>{
 assert.equal(verifiedExperience(undefined,request),null);assert.deepEqual(verifiedExperience(sample(),request),sample());
});

test('version3 failure projection retains strict bounded data and never grants authority',()=>{
 const value=sample();const hit=value.hits[0];hit.episode.schema_version=3;
 hit.episode.rounds[0].change_excerpt='minimal original patch';
 hit.episode.rounds[0].failure_excerpt='E AttributeError: actual original failure';
 hit.payload_sha256=experienceHash(hit.episode);
 assert.deepEqual(verifiedExperience(seal(value),request),seal(value));
 for(const change of [e=>e.schema_version=4,e=>e.runtime_authorized=true,
    e=>e.rounds[0].failure_excerpt='x'.repeat(1601),e=>e.command=['shell']]){
   const invalid=structuredClone(value);change(invalid.hits[0].episode);
   invalid.hits[0].payload_sha256=experienceHash(invalid.hits[0].episode);
   assert.throws(()=>verifiedExperience(seal(invalid),request));
 }
});
test('history cannot introduce commands paths or execution permission even with new hashes',()=>{
 for(const change of [e=>e.command=['powershell'],e=>e.runtime_authorized=true,e=>e.classification='confirmed',e=>e.source_files['.env.secret']='a'.repeat(64)]){
  const value=sample();change(value.hits[0].episode);value.hits[0].payload_sha256=experienceHash(value.hits[0].episode);
  assert.throws(()=>verifiedExperience(seal(value),request));
 }
});
test('wrong task current snapshot baseline issue payload or duplicate entry rejected',()=>{
 for(const change of [v=>v.task_id='a'.repeat(32),v=>v.issue_sha256='a'.repeat(64),v=>v.planning_base_sha='b'.repeat(40),v=>v.planning_files_sha256='a'.repeat(64),v=>v.hits[0].payload_sha256='a'.repeat(64),v=>v.hits.push(v.hits[0])]){
  const value=sample();change(value);assert.throws(()=>verifiedExperience(seal(value),request));
 }
});
test('oversized prose and invalid test receipt rejected',()=>{
 for(const change of [e=>e.issue='x'.repeat(1001),e=>e.rounds[0].test_exits=['0'],e=>e.rounds[0].test_timeouts=[]]){
  const value=sample();change(value.hits[0].episode);value.hits[0].payload_sha256=experienceHash(value.hits[0].episode);
  assert.throws(()=>verifiedExperience(seal(value),request));
 }
});
test('version2 changed historical files require explicit revalidation and bounded excerpts',()=>{
 const value=sample();value.schema_version=2;value.selection_files_sha256='c'.repeat(64);
 const hit=value.hits[0];hit.applicability='requires_revalidation';hit.episode.schema_version=2;
 hit.episode.rounds[0].change_excerpt='return abs(a)+abs(b)';hit.episode.rounds[0].failure_excerpt='negative assertion failed';
 hit.payload_sha256=experienceHash(hit.episode);
 assert.deepEqual(verifiedExperience(seal(value),request),seal(value));
 hit.applicability='matching_original_source';assert.throws(()=>verifiedExperience(seal(value),request));
 hit.applicability='requires_revalidation';hit.episode.outcome='candidate_test_passed';
 hit.payload_sha256=experienceHash(hit.episode);assert.throws(()=>verifiedExperience(seal(value),request));
});

function success(version=4){
 const value=sample(),hit=value.hits[0],e=hit.episode,r=e.rounds[0];
 e.schema_version=version;e.outcome='candidate_test_passed';e.recorded_status='candidate_ready';
 r.verification='passed';r.test_exits=[0];r.change_excerpt='Preserve signed addition';
 if(version===4)r.validation_summary={scope:'selected_commands',passed:1,total:1,semantic_correctness_proven:false};
 else r.failure_excerpt='Legacy successful report text';
 hit.payload_sha256=experienceHash(e);return seal(value);
}
test('v4 selected-command counts and legacy successful v2/v3 remain historical',()=>{
 for(const version of [2,3,4]){const value=success(version);assert.deepEqual(verifiedExperience(value,request),value);}
});
test('v4 retains earlier actual failure without attaching a success summary to it',()=>{
 const value=success(),e=value.hits[0].episode,failed=structuredClone(sample().hits[0].episode.rounds[0]);
 failed.change_excerpt='Original wrong change';failed.failure_excerpt='AssertionError negative operands';
 e.rounds[0].number=2;e.rounds.unshift(failed);value.hits[0].payload_sha256=experienceHash(e);
 assert.deepEqual(verifiedExperience(seal(value),request),seal(value));
});
test('rehashed v4 fabricated counts, authority, raw excerpts and incomplete tests are rejected',()=>{
 for(const change of [
  e=>e.rounds[0].validation_summary.passed=2,e=>e.rounds[0].validation_summary.total=0,
  e=>e.rounds[0].validation_summary.passed=true,e=>e.rounds[0].validation_summary.scope='all_behaviors',
  e=>e.rounds[0].validation_summary.semantic_correctness_proven=true,e=>e.rounds[0].validation_summary.command=['shell'],
  e=>e.rounds[0].failure_excerpt='Raw report',e=>e.rounds[0].test_exits=[1],
  e=>e.rounds[0].test_timeouts=[true],e=>{e.rounds[0].test_exits=[];e.rounds[0].test_timeouts=[];e.rounds[0].validation_summary.total=0;e.rounds[0].validation_summary.passed=0;},
  e=>{e.outcome='test_failed';e.rounds[0].verification='failed';e.rounds[0].test_exits=[1];delete e.rounds[0].validation_summary;e.rounds[0].failure_excerpt='Real failure';}
 ]){
  const value=success();change(value.hits[0].episode);value.hits[0].payload_sha256=experienceHash(value.hits[0].episode);
  assert.throws(()=>verifiedExperience(seal(value),request));
 }
});
