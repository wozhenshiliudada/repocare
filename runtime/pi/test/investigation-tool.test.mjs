import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {generatePlan} from '../plan.mjs';
import {INVESTIGATION_PURPOSE} from '../investigation-tool.mjs';
import {planningTaskSession} from '../task-session.mjs';
import {startMock} from './mock-server.mjs';

const BASE = 'b'.repeat(40);
const SCOPE = 'a'.repeat(64);
const TOKEN = 'c'.repeat(64);
const SOURCE = 'from logic.hidden import OFFSET_ab12cd\n\n\ndef add(a, b):\n    return a - b\n';
const HIDDEN = 'OFFSET_ab12cd = 17\n';
const sha = raw => createHash('sha256').update(raw).digest('hex');

function source(path, content, editable = true) {
  return {path, content, editable, before_sha256: sha(content)};
}

async function startBridge() {
  const calls = [];
  const server = http.createServer(async (req, res) => {
    if (req.method !== 'POST' || req.url !== '/v1/tool') {
      res.writeHead(404, {'content-type': 'application/json'});res.end('{}');return;
    }
    if (req.headers.authorization !== 'Bearer ' + TOKEN) {
      res.writeHead(401, {'content-type': 'application/json'});res.end('{"error":"unauthorized"}');return;
    }
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    calls.push(body);
    const index = calls.length - 1;
    const reply = (status, value) => {
      res.writeHead(status, {'content-type': 'application/json'});
      res.end(JSON.stringify(value));
    };
    if (body.tool === 'search_approved_sources') {
      reply(200, {operation: 'search_sources', query_sha256: 'd'.repeat(64),
        hits: [{source: {path: 'logic/hidden.py', side: 'head', commit_sha: BASE,
          blob: 'e'.repeat(40), sha256: sha(HIDDEN)}, line: 1, text: HIDDEN.trim(),
          excerpt_truncated: false}], scanned_files: 1, skipped_nontext: 0,
        catalog_files: 1, complete_catalog_scan: true, catalog_truncated: false,
        interpretation: 'Literal text matches in fixed permitted sources.',
        scope_sha256: SCOPE, base_sha: BASE, runtime_authorized: false,
        write_authorized: false, call_index: index});
      return;
    }
    if (body.tool === 'read_approved_source' && body.args.path === 'logic/.env') {
      reply(403, {error: 'denied'});return;
    }
    if (body.tool === 'read_approved_source') {
      reply(200, {operation: 'read_source', source: {path: 'logic/hidden.py', side: 'head',
        commit_sha: BASE, blob: 'e'.repeat(40), sha256: sha(HIDDEN)}, lines: [{line: 1, text: HIDDEN.trim()}],
        total_lines: 1, truncated: false, side: 'original_fixed_commit', scope_sha256: SCOPE,
        base_sha: BASE, runtime_authorized: false, write_authorized: false, call_index: index});
      return;
    }
    reply(400, {error: 'invalid'});
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {url: `http://127.0.0.1:${server.address().port}`, calls,
    close: () => new Promise(resolve => {server.closeAllConnections();server.close(resolve);})};
}

function request(bridge, {task = true, hidden = true} = {}) {
  const files = [source('calculator.py', SOURCE)];
  const context = {schema_version: 2, purpose: INVESTIGATION_PURPOSE,
    provider: 'pi-loopback', model: 'mock-maintenance', paths: ['calculator.py'],
    investigation: {schema_version: 1, read_roots: ['logic'], max_calls: 4, max_output_bytes: 32768}};
  const taskId = 'f'.repeat(32);
  return {issue: 'Restore calibrated addition.', base_sha: BASE, files, tests: [['python', 'test.py']],
    request_budget: 6,
    ...(task ? {task_session: {mode: 'create', binding: {schema_version: 1, task_id: taskId,
      revision: 0, cwd: '', sessions_dir: ''}, context}} : {}),
    ...(hidden ? {investigation: {schema_version: 1, scope_sha256: SCOPE, base_sha: BASE,
      read_roots: ['logic'], max_calls: 4, max_output_bytes: 32768,
      bridge: {url: bridge.url, token: TOKEN}}} : {})};
}

async function taskRoot() {
  const base = process.env.R4_TEST_ROOT || os.tmpdir();
  await fs.mkdir(base, {recursive: true});
  const root = await fs.mkdtemp(path.join(base, 'pi-r4-node-'));
  const taskId = 'f'.repeat(32);
  const cwd = path.join(root, taskId, 'workspace');
  const sessions = path.join(root, taskId, 'sessions');
  await fs.mkdir(cwd, {recursive: true});
  await fs.mkdir(sessions);
  return {root, taskId, cwd, sessions};
}

test('real Pi SDK uses approved search/read and reports the new active tools', async t => {
  const root = await taskRoot();
  const bridge = await startBridge();
  t.after(async () => {
    await bridge.close();
    await fs.rm(root.root, {recursive: true, force: true});
  });
  const value = request(bridge);
  value.task_session.binding.cwd = root.cwd;
  value.task_session.binding.sessions_dir = root.sessions;
  const mock = await startMock({script: (body, n) => {
    assert.deepEqual(body.tools.map(t => t.function.name).sort(),
      ['propose_repair', 'read_approved_source', 'read_source', 'search_approved_sources']);
    const current = body.messages.slice(body.messages.findLastIndex(m => m.role === 'user') + 1);
    const tools = current.filter(m => m.role === 'tool');
    if (!tools.length) return {name: 'search_approved_sources', args: {query: 'OFFSET_'}};
    const last = tools[tools.length - 1];
    if (last.content.includes('"operation":"search_sources"')) {
      const found = JSON.parse(last.content);
      return {name: 'read_approved_source', args: {path: found.hits[0].source.path}};
    }
    if (last.content.includes('"operation":"read_source"')) {
      const found = JSON.parse(last.content);
      const line = found.lines[0].text;
      const name = line.split('=')[0].trim();
      return {name: 'propose_repair', args: {diagnosis: 'Read the approved calibration dependency',
        risk: 'low', edits: [{path: 'calculator.py',
          replacements: [{oldText: 'return a - b', newText: `return a + b + ${name}`}]}]}};
    }
    return {content: 'submitted'};
  }});
  t.after(() => mock.close());
  const result = await generatePlan(value, {mockUrl: mock.url});
  assert.equal(result.status, 'completed');
  assert.equal(result.plan.edits[0].content,
    SOURCE.replace('return a - b', 'return a + b + OFFSET_ab12cd'));
  assert.deepEqual(result.evidence.active_tools,
    ['propose_repair', 'read_approved_source', 'read_source', 'search_approved_sources']);
  assert.equal(result.evidence.purpose, INVESTIGATION_PURPOSE);
  assert.equal(result.evidence.investigation_scope_sha256, SCOPE);
  assert.ok(result.evidence.events.some(event => event.tool === 'search_approved_sources'));
  assert.ok(result.evidence.events.some(event => event.tool === 'read_approved_source'));
  assert.equal(bridge.calls.length, 2);
  assert.equal(value.files[0].content, SOURCE);
  assert.ok(!JSON.stringify(mock.requests[0]).includes('OFFSET_ab12cd = 17'));
});

test('legacy two-tool evidence is still valid and exposes no investigation purpose', async t => {
  const mock = await startMock({scenario: 'fix'});
  t.after(() => mock.close());
  const value = {issue: 'Fix addition', base_sha: BASE, request_budget: 4,
    files: [source('calculator.py', 'def add(a, b):\n    return a - b\n')]};
  const result = await generatePlan(value, {mockUrl: mock.url});
  assert.equal(result.status, 'completed');
  assert.deepEqual(result.evidence.active_tools, ['propose_repair', 'read_source']);
  assert.equal('purpose' in result.evidence, false);
  assert.equal('investigation_scope_sha256' in result.evidence, false);
});

test('a proposal that edits the approved read-only dependency is rejected before any write', async t => {
  const root = await taskRoot();
  const bridge = await startBridge();
  const mock = await startMock({script: (body, n) => {
    if (n === 1) return {name: 'propose_repair', args: {diagnosis: 'edit hidden', risk: 'low',
      edits: [{path: 'logic/hidden.py', replacements: [{oldText: '17', newText: '19'}]}]}};
    return {content: 'The dependency is read-only; no repair proposed.'};
  }});
  t.after(async () => {
    await mock.close();
    await bridge.close();
    await fs.rm(root.root, {recursive: true, force: true});
  });
  const value = request(bridge);
  value.task_session.binding.cwd = root.cwd;
  value.task_session.binding.sessions_dir = root.sessions;
  await assert.rejects(generatePlan(value, {mockUrl: mock.url}), error => {
    assert.equal(error.evidence.failure_category, 'no_plan');
    assert.equal(error.evidence.requests, 2);
    assert.equal(error.evidence.tool_calls, 1);
    return true;
  });
  assert.equal(bridge.calls.length, 0);
});

test('a denied sensitive path is surfaced as a fixed error and never grants a read', async t => {
  const root = await taskRoot();
  const bridge = await startBridge();
  const mock = await startMock({script: (body, n) => {
    const current = body.messages.slice(body.messages.findLastIndex(m => m.role === 'user') + 1);
    const tools = current.filter(m => m.role === 'tool');
    if (!tools.length) return {name: 'read_approved_source', args: {path: 'logic/.env'}};
    if (n === 2) return {name: 'read_approved_source', args: {path: 'logic/hidden.py'}};
    return {name: 'propose_repair', args: {diagnosis: 'Use approved constant', risk: 'low',
      edits: [{path: 'calculator.py', replacements: [
        {oldText: 'return a - b', newText: 'return a + b + OFFSET_ab12cd'}]}]}};
  }});
  t.after(async () => {
    await mock.close();
    await bridge.close();
    await fs.rm(root.root, {recursive: true, force: true});
  });
  const value = request(bridge);
  value.task_session.binding.cwd = root.cwd;
  value.task_session.binding.sessions_dir = root.sessions;
  const result = await generatePlan(value, {mockUrl: mock.url});
  assert.equal(result.status, 'completed');
  assert.equal(bridge.calls.length, 2);
  const denied = mock.requests[1].messages.find(m => m.role === 'tool'
    && m.content.includes('Investigation denied'));
  assert.ok(denied, 'sensitive read must return a fixed denial receipt');
  assert.ok(!JSON.stringify(mock.requests).includes('PRIVATE_KEY'));
});

test('legacy Native context is still accepted but cannot carry the investigation purpose', async t => {
  const root = await taskRoot();
  t.after(() => fs.rm(root.root, {recursive: true, force: true}));
  const legacy = {provider: 'pi-loopback', model: 'mock-maintenance', paths: ['calculator.py']};
  await planningTaskSession({mode: 'create', binding: {schema_version: 1, task_id: root.taskId,
    revision: 0, cwd: root.cwd, sessions_dir: root.sessions}, context: legacy},
    'pi-loopback', 'mock-maintenance', ['calculator.py'], 'repair');
  const context = {schema_version: 2, purpose: INVESTIGATION_PURPOSE,
    provider: 'pi-loopback', model: 'mock-maintenance', paths: ['calculator.py'],
    investigation: {schema_version: 1, read_roots: ['logic'], max_calls: 4, max_output_bytes: 32768}};
  await assert.rejects(planningTaskSession({mode: 'create', binding: {schema_version: 1,
    task_id: root.taskId, revision: 0, cwd: root.cwd, sessions_dir: root.sessions},
    context}, 'pi-loopback', 'mock-maintenance', ['calculator.py'], 'repair'), /context changed/);
});
