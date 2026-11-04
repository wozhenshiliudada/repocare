// Shared synthetic transport for the T0 Node/Python probes. No live provider path.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {startMock} from './mock-server.mjs';

export const original = 'def add(a, b):\n    return a - b\n';
export const candidate = 'def add(a, b):\n    return a * b\n';
export const feedback = {command: ['python', '-m', 'unittest'], returncode: 1,
  failure_id: 'test_add', assertion: 'Expected 5, got 6', previous_plan: 'first-proposal'};
export const worker = fileURLToPath(new URL('./persistence-spike-worker.mjs', import.meta.url));
export const messageText = message => typeof message.content === 'string' ? message.content
  : message.content.map(part => part.text ?? '').join('');

export function startProbeMock() {
  return startMock({script: body => {
    const lastUser = body.messages.findLastIndex(m => m.role === 'user');
    const context = JSON.parse(messageText(body.messages[lastUser]));
    const recent = body.messages.slice(lastUser + 1);
    const toolResults = recent.filter(m => m.role === 'tool');
    if (toolResults.length >= 2) return {content: 'Current proposal complete; approval belongs to the controller.'};
    if (toolResults.length === 1) {
      const source = JSON.parse(messageText(toolResults[0]));
      return {name: 'propose_repair', args: {diagnosis: context.round === 1 ? 'first attempt multiplication' : 'use verified failure to correct addition',
        risk: 'fixture', edits: [{path: 'calculator.py', replacements: [{
          oldText: source.content, newText: context.round === 1 ? candidate : 'def add(a, b):\n    return a + b\n'}]}]}};
    }
    return {name: 'read_source', args: {path: 'calculator.py'}};
  }});
}

export async function runProbe(input) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    ['PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'COMSPEC', 'PATHEXT'].includes(key.toUpperCase())));
  const child = spawn(process.execPath, [worker], {env, stdio: ['pipe', 'pipe', 'pipe']});
  let out = '', err = '';
  child.stdout.on('data', chunk => out += chunk);
  child.stderr.on('data', chunk => err += chunk);
  const timeout = setTimeout(() => child.kill(), 30000);
  try {
    const done = new Promise((resolve, reject) => {
      child.on('error', reject);
      child.on('close', (code, signal) => resolve({code, signal}));
    });
    child.stdin.end(JSON.stringify(input));
    const result = await done;
    assert.equal(result.code, 0, `worker failed (${result.signal ?? 'exit'}): ${err}`);
    return JSON.parse(out);
  } finally {clearTimeout(timeout);}
}

// One round per command: Python keeps the original UIRegistry as the only budget authority.
if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  let mock;
  try {
    let raw = '';
    for await (const chunk of process.stdin) {
      raw += chunk;
      if (raw.length > 16000) throw new Error('Probe input too large');
    }
    const input = JSON.parse(raw);
    mock = await startProbeMock();
    const result = await runProbe({...input, mock_url: mock.url});
    const lastRequest = mock.requests.at(-1);
    const users = lastRequest?.messages.filter(m => m.role === 'user').map(m => JSON.parse(messageText(m))) ?? [];
    process.stdout.write(JSON.stringify({...result, observed_http_requests: mock.requests.length,
      latest_request_budget: users.at(-1)?.request_budget ?? null,
      feedback_seen: users.at(-1)?.feedback ?? null}) + '\n');
  } catch (error) {
    process.stderr.write('T0 fixture failed: ' + error.message + '\n');
    process.exitCode = 1;
  } finally {await mock?.close();}
}
