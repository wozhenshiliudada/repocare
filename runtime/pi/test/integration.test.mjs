import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {runCase} from './replay-case.mjs';

for (const [scenario, decision, expected] of [
  ['fix', 'approve', 'candidate_ready'], ['broken', 'approve', 'rolled_back'],
  ['fix', 'reject', 'rejected'],
]) {
  test(`Pi + Git workflow: ${scenario}/${decision}`, {timeout: 30000}, async () => {
    const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'pi-workflow-'));
    try {
      const result = await runCase(temp, scenario, decision, expected);
      assert.equal(result.status, expected);
      assert.equal(result.source_unchanged, true);
      assert.equal(result.provider_evidence.pi_version, '1.0.4');
      assert.equal(result.provider_evidence.transport, 'loopback-mock');
      if (expected === 'rejected') assert.deepEqual(result.baseline_exit, []);
      if (expected === 'candidate_ready') {
        assert.notEqual(result.baseline_exit[0], 0);
        assert.equal(result.candidate_exit[0], 0);
      }
    } finally {await fs.rm(temp, {recursive: true, force: true});}
  });
}

test('Pi revises a failed patch from real test feedback', {timeout: 45000}, async () => {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'pi-revision-'));
  try {
    const observed=[];
    const result = await runCase(temp, 'retry_fix', 'approve', 'candidate_ready', {maxAttempts: 3,
      onRequest:body=>observed.push(body)});
    assert.equal(result.rounds.length, 2);
    assert.deepEqual(result.rounds.map(r => r.verification), ['failed', 'passed']);
    assert.equal(result.provider_evidence.total_requests_used, 4);
    assert.equal(result.source_unchanged, true);
    const revision=observed.map(body=>{const raw=body.messages.find(message=>message.role==='user').content;
      return JSON.parse(typeof raw==='string'?raw:raw[0].text);})
      .find(context=>context.revision_context)?.revision_context;
    assert.equal(revision.history.length,1);
    assert.equal(revision.history[0].plan_sha256,result.rounds[0].authorization.plan_sha256);
    assert.ok(revision.history[0].reads.length>0);
    assert.notEqual(revision.test_focus.tests[0].returncode,0);
    assert.match(revision.test_focus.tests[0].assertion_excerpt,/AssertionError/);
    assert.equal(revision.test_focus.tests[0].transition,'still_failing');
    assert.equal(revision.test_focus.tests[0].baseline_returncode,result.baseline_exit[0]);
    assert.equal(revision.test_focus.comparison.semantic_correctness_proven,false);
    assert.match(result.provider_evidence.revision_context_sha256,/^[a-f0-9]{64}$/);
  } finally {await fs.rm(temp, {recursive: true, force: true});}
});

test('Pi revision rejected after the first approved patch rolls back', {timeout: 45000}, async () => {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'pi-reject-revision-'));
  try {
    const result = await runCase(temp, 'retry_fix', 'approve', 'rolled_back', {maxAttempts: 3, rejectRevision: true});
    assert.equal(result.rounds.length, 2);
    assert.equal(result.rounds[1].authorization.decision, 'reject');
    assert.deepEqual(result.rounds[1].exits, []);
    assert.equal(result.source_unchanged, true);
  } finally {await fs.rm(temp, {recursive: true, force: true});}
});

test('Pi candidate publication needs separate approval and preserves source checkout', {timeout: 60000}, async () => {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'pi-integration-'));
  try {
    const result = await runCase(temp, 'fix', 'approve', 'candidate_ready', {integrate: true});
    assert.equal(result.integration.unapproved_status, 'awaiting_approval');
    assert.equal(result.integration.status, 'published');
    assert.equal(result.integration.source_unchanged, true);
    assert.equal(result.cleanup_status, 'discarded');
    assert.match(result.integration.branch, /^integration\/[a-f0-9]{32}$/);
  } finally {await fs.rm(temp, {recursive: true, force: true});}
});
