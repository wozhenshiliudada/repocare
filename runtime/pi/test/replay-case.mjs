import path from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {startMock} from './mock-server.mjs';

export async function runCase(directory, scenario, decision, expected, options = {}) {
  const mock = await startMock({scenario,onRequest:options.onRequest});
  const root = fileURLToPath(new URL('../../..', import.meta.url));
  try {
    return await new Promise((resolve, reject) => {
      const child = spawn(process.env.PI_TEST_PYTHON || 'python',
        [path.join(root, 'tests', 'pi_replay.py'), '--url', mock.url,
          '--directory', directory, '--decision', decision, '--expected', expected,
          '--max-attempts', String(options.maxAttempts ?? 1), ...(options.rejectRevision ? ['--reject-revision'] : []),
          ...(options.largeSource ? ['--large-source'] : []),
          ...(options.integrate ? ['--integrate'] : [])],
        {windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
      let output = '', error = '';
      child.stdout.on('data', chunk => output += chunk);
      child.stderr.on('data', chunk => error += chunk);
      child.on('error', reject);
      child.on('close', code => code === 0 ? resolve(JSON.parse(output)) :
        reject(new Error(`Replay driver exited ${code}: ${error} ${output}`)));
    });
  } finally {await mock.close();}
}
