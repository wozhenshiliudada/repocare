import fs from 'node:fs/promises';
import path from 'node:path';
import {runCase} from './test/replay-case.mjs';

const index = process.argv.indexOf('--directory');
if (index < 0 || !process.argv[index + 1]) throw new Error('Provide --directory for replay evidence');
const directory = path.resolve(process.argv[index + 1]);
await fs.mkdir(directory, {recursive: false});
const cases = [];
for (const [name, scenario, decision, expected, options] of [
  ['approval_accept', 'fix', 'approve', 'candidate_ready'],
  ['approval_reject', 'fix', 'reject', 'rejected'],
  ['invalid_patch', 'broken', 'approve', 'rolled_back'],
  ['regression', 'regression', 'approve', 'rolled_back'],
  ['retry_success', 'retry_fix', 'approve', 'candidate_ready', {maxAttempts: 3}],
  ['retry_rejected', 'retry_fix', 'approve', 'rolled_back', {maxAttempts: 3, rejectRevision: true}],
]) {
  cases.push({name, ...await runCase(path.join(directory, name), scenario, decision, expected, options)});
}
const result = {pi_version: '1.0.4', model_transport: 'loopback synthetic SSE; no live LLM',
  passed: cases.every(c => c.passed), cases};
await fs.writeFile(path.join(directory, 'summary.json'), JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify(result, null, 2));
process.exitCode = result.passed ? 0 : 1;
