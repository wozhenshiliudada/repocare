import fs from 'node:fs/promises';
import path from 'node:path';
import {runCase} from './test/replay-case.mjs';

const index = process.argv.indexOf('--directory');
if (index < 0 || !process.argv[index + 1]) throw new Error('Provide a new --directory');
const directory = path.resolve(process.argv[index + 1]);
await fs.mkdir(directory, {recursive: false});
const result = await runCase(path.join(directory, 'approved-integration'), 'fix', 'approve',
  'candidate_ready', {integrate: true});
const summary = {model_transport: 'real Pi with loopback synthetic SSE; no live model', ...result};
await fs.writeFile(path.join(directory, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
console.log(JSON.stringify(summary, null, 2));
process.exitCode = summary.passed ? 0 : 1;
