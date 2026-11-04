import test from 'node:test';
import assert from 'node:assert/strict';
import {generatePlan} from '../plan.mjs';
import {startMock} from './mock-server.mjs';

const request = {issue: 'add(2, 3) should be 5', base_sha: 'a'.repeat(40),
  files: [{path: 'calculator.py', before_sha256: 'b'.repeat(64),
    content: 'def add(a, b):\n    return a - b\n'}],
  tests: [['{python}', '-m', 'unittest', 'discover', '-s', 'tests', '-v']]};

test('real Pi session reads immutable snapshot and proposes structured patch', async () => {
  const mock = await startMock();
  try {
    const result = await generatePlan(request, {mockUrl: mock.url});
    assert.equal(result.plan.edits[0].content, 'def add(a, b):\n    return a + b\n');
    assert.equal(result.plan.edits[0].before_sha256, request.files[0].before_sha256);
    assert.equal(result.evidence.pi_version, '1.0.4');
    assert.deepEqual(result.evidence.active_tools, ['propose_repair', 'read_source']);
    assert.ok(result.evidence.events.some(e => e.type === 'tool_execution_end'));
    assert.ok(mock.requests.length >= 2);
    for (const message of mock.requests)
      assert.deepEqual(message.tools.map(t => t.function.name).sort(), ['propose_repair', 'read_source']);
  } finally {await mock.close();}
});

test('Pi cannot submit out-of-scope paths', async () => {
  const mock = await startMock({unauthorized: true});
  try {await assert.rejects(generatePlan(request, {mockUrl: mock.url}));}
  finally {await mock.close();}
});

test('provider failures do not produce an accepted plan', async () => {
  const mock = await startMock({failure: true});
  try {await assert.rejects(generatePlan(request, {mockUrl: mock.url}));}
  finally {await mock.close();}
});

test('mock endpoint is restricted to loopback', async () => {
  await assert.rejects(generatePlan(request, {mockUrl: 'https://example.com'}), /loopback/);
});

test('read-only context cannot be proposed as an edit', async () => {
  const mock = await startMock();
  try {
    await assert.rejects(generatePlan({...request, files: request.files.map(f => ({...f, editable: false}))},
      {mockUrl: mock.url}));
  } finally {await mock.close();}
});

test('model request budget stops at the specified count without transport retries', async () => {
  const mock = await startMock();
  try {
    await assert.rejects(generatePlan({...request, request_budget: 1}, {mockUrl: mock.url}));
    assert.equal(mock.requests.length, 1);
  } finally {await mock.close();}
});
