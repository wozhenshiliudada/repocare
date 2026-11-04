import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {SessionManager} from '@earendil-works/pi-coding-agent';
import {original, candidate, feedback, worker, messageText, startProbeMock, runProbe as run}
  from './persistence-spike-fixture.mjs';

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'pi-persistence-spike-'));
  // Only this test's resolved mkdtemp path is removed, never a caller-supplied path.
  t.after(async () => fs.rm(root, {recursive: true, force: true}));
  const mock = await startProbeMock();
  t.after(async () => mock.close());
  return {root, mock};
}

function input(f, phase, extra = {}) {
  return {root: f.root, mock_url: f.mock.url, content: phase === 'create' ? original : candidate,
    phase, request_budget: 50, ...extra};
}

function safe(result) {
  assert.equal(result.pi_version, '1.0.4');
  assert.deepEqual(result.active_tools, ['propose_repair', 'read_source']);
  assert.equal(result.auto_compaction, false);
  assert.equal(result.auto_retry, false);
  assert.equal(result.cache_warming, 'off');
  assert.equal(result.complete_tool_pairs, true);
}

test('T0 real Pi create/dispose/open/continue in three processes preserves history and current tool snapshot', async t => {
  const f = await fixture(t);
  const first = await run(input(f, 'create'));
  safe(first);
  assert.equal(first.status, 'completed');
  assert.equal(first.requests_started, 3);
  assert.equal(first.plan.edits[0].content, candidate);
  const requestsBeforeOpen = f.mock.requests.length;
  const opened = await run(input(f, 'open', {session_file: first.session_file}));
  safe(opened);
  assert.equal(opened.status, 'opened');
  assert.equal(opened.requests_started, 0);
  assert.equal(opened.read_calls + opened.proposal_calls, 0);
  assert.deepEqual(opened.events, []);
  assert.equal(f.mock.requests.length, requestsBeforeOpen);
  assert.equal(opened.session_id, first.session_id);
  assert.equal(opened.initial_messages, first.final_messages);
  assert.equal(opened.file_before_sha256, opened.file_after_sha256);
  const resumed = await run(input(f, 'continue', {session_file: first.session_file, feedback,
    request_budget: 50 - first.requests_started}));
  safe(resumed);
  assert.equal(resumed.status, 'completed');
  assert.equal(new Set([first.pid, opened.pid, resumed.pid]).size, 3);
  assert.equal(resumed.session_id, first.session_id);
  assert.equal(resumed.initial_messages, first.final_messages);
  assert.equal(resumed.requests_started, 3);
  assert.equal(resumed.plan.edits[0].content, 'def add(a, b):\n    return a + b\n');
  assert.notEqual(resumed.plan.edits[0].before_sha256, first.plan.edits[0].before_sha256);
  const request = f.mock.requests[requestsBeforeOpen];
  const users = request.messages.filter(m => m.role === 'user').map(m => JSON.parse(messageText(m)));
  assert.equal(users.length, 2);
  assert.deepEqual(users[1].feedback, feedback);
  assert.equal(users[1].request_budget, 47);
  assert.ok(request.messages.some(m => m.role === 'tool' && m.content.includes('a - b')));
  assert.ok(request.messages.some(m => m.role === 'assistant' && m.tool_calls?.some(c => c.function.name === 'propose_repair')));
  for (const request of f.mock.requests) {
    assert.deepEqual(request.tools.map(t => t.function.name).sort(), ['propose_repair', 'read_source']);
    assert.equal(request.max_tokens, 128000);
  }
});

test('T0 reopened session uses supplied remaining request allowance and cannot reuse an old proposal', async t => {
  const f = await fixture(t);
  const first = await run(input(f, 'create'));
  const before = f.mock.requests.length;
  const stopped = await run(input(f, 'continue', {session_file: first.session_file, feedback, request_budget: 1}));
  assert.equal(stopped.status, 'incomplete');
  assert.equal(stopped.plan, null);
  assert.equal(stopped.requests_started, 1);
  assert.equal(stopped.proposal_calls, 0);
  assert.equal(f.mock.requests.length - before, 1);
  assert.equal(stopped.auto_retry, false);
});

test('T0 checkpoint leaf excludes abandoned tail without deleting it and requires reselecting leaf on each open', async t => {
  const f = await fixture(t);
  const first = await run(input(f, 'create'));
  const manager = SessionManager.open(first.session_file);
  const tail = manager.appendMessage({role: 'user', content: 'ABANDONED_UNVERIFIED_TAIL', timestamp: Date.now()});
  manager.branch(first.leaf_id);
  // branch() changes memory only; open() chooses the last on-disk entry again.
  assert.equal(SessionManager.open(first.session_file).getLeafId(), tail);
  const reopened = await run(input(f, 'open', {session_file: first.session_file, checkpoint_leaf: first.leaf_id}));
  assert.equal(reopened.final_messages, first.final_messages);
  assert.equal(reopened.leaf_id, first.leaf_id);
  assert.equal(reopened.file_before_sha256, reopened.file_after_sha256);
  assert.ok((await fs.readFile(first.session_file, 'utf8')).includes('ABANDONED_UNVERIFIED_TAIL'));
  const before = f.mock.requests.length;
  const resumed = await run(input(f, 'continue', {session_file: first.session_file, checkpoint_leaf: first.leaf_id, feedback}));
  assert.equal(resumed.status, 'completed');
  assert.ok(!JSON.stringify(f.mock.requests[before].messages).includes('ABANDONED_UNVERIFIED_TAIL'));
});

test('T0 current resource loader policy and tool allowlist are reinstalled on resume', async t => {
  const f = await fixture(t);
  const first = await run(input(f, 'create', {policy: 'OLD_POLICY_MARKER'}));
  const before = f.mock.requests.length;
  await run(input(f, 'continue', {session_file: first.session_file, feedback, policy: 'NEW_POLICY_MARKER'}));
  const systems = f.mock.requests[before].messages.filter(m => m.role === 'system').map(m => m.content).join('\n');
  assert.ok(systems.includes('NEW_POLICY_MARKER'));
  assert.ok(!systems.includes('OLD_POLICY_MARKER'));
  // This is an API behavior probe, not permission for production policy migration.
});

test('T0 SDK open of a missing path creates a new identity instead of rejecting recovery', async t => {
  const f = await fixture(t);
  const missing = path.join(f.root, 'missing.jsonl');
  const manager = SessionManager.open(missing, f.root, f.root);
  assert.ok(manager.getSessionId());
  assert.equal(manager.getEntryCount(), 0);
  await assert.rejects(fs.stat(missing), {code: 'ENOENT'});
  // T1 must check existence and fixed session identity before calling SDK open().
});

test('T0 SDK silently skips malformed transcript suffix: recovery must validate raw JSONL independently', async t => {
  const f = await fixture(t);
  const first = await run(input(f, 'create'));
  await fs.appendFile(first.session_file, '{"type":"message","unfinished":');
  const manager = SessionManager.open(first.session_file);
  assert.equal(manager.getLeafId(), first.leaf_id);
  assert.equal(manager.buildSessionContext().messages.length, first.final_messages);
  // Successful SDK open is insufficient proof of a valid complete checkpoint.
});

test('T0 SDK initializes an empty session file: recovery must reject empty persisted history', async t => {
  const f = await fixture(t);
  const empty = path.join(f.root, 'empty.jsonl');
  await fs.writeFile(empty, '');
  const manager = SessionManager.open(empty, f.root, f.root);
  assert.equal(manager.getEntryCount(), 0);
  assert.ok((await fs.readFile(empty, 'utf8')).includes('"type":"session"'));
});

test('T0 probe refuses any non-loopback endpoint without reading credentials or transmitting', async t => {
  const f = await fixture(t);
  const child = spawn(process.execPath, [worker], {env: {}, stdio: ['pipe', 'pipe', 'pipe']});
  let err = '';
  child.stderr.on('data', chunk => err += chunk);
  child.stdout.resume();
  const done = new Promise(resolve => child.on('close', resolve));
  child.stdin.end(JSON.stringify(input(f, 'create', {mock_url: 'https://example.com'})));
  assert.equal(await done, 1);
  assert.match(err, /only synthetic loopback/);
  assert.equal(f.mock.requests.length, 0);
});
