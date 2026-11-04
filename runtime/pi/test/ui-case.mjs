// Test-only loopback launcher; capability never appears in reports or console output.
import {spawn, execFile} from 'node:child_process';
import {promisify} from 'node:util';
import path from 'node:path';
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';

const exec = promisify(execFile);
export const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
export const python = process.env.PI_TEST_PYTHON || 'python';

export async function startUI(directory, {mockUrl = null, auto = false, restart = false,
  memoryRoot = null, memoryIndex = null, memoryRelations = null, memoryRelationsContext = false, structureIndex = null, structureContextQuery = null, maxAttempts = 1, requestBudget = 2} = {}) {
  directory = path.resolve(directory);
  let args = ['-u', path.join(project, 'maintain.py'), 'serve'];
  if (mockUrl) {
    if (!restart) await exec(python, ['-c',
      "import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from repo_maintainer.demo import create_fixture,DEMO_TEST;from repo_maintainer.models import Policy;from dataclasses import asdict;root=Path(sys.argv[2]);create_fixture(root/'repository');(root/'policy.json').write_text(json.dumps(asdict(Policy(('calculator.py',),(DEMO_TEST,),readonly_paths=('tests/test_calculator.py',)))),encoding='utf-8')",
      path.join(project, 'src'), directory], {cwd: project});
    args.push('--repo', path.join(directory, 'repository'), '--policy', path.join(directory, 'policy.json'),
      '--runs-dir', path.join(directory, 'runs'), '--pi-mock-url', mockUrl, '--request-budget', String(requestBudget));
    if(memoryRoot)args.push('--memory-root',memoryRoot);
    if(memoryIndex)args.push('--memory-index',memoryIndex);
    if(memoryRelations)args.push('--memory-relations',memoryRelations);
    if(memoryRelationsContext)args.push('--memory-relations-context');
    if(structureIndex)args.push('--structure-index',structureIndex);
    if(structureContextQuery!==null)args.push('--structure-context-query',structureContextQuery);
  } else args.push(restart ? '--resume-demo' : '--demo', directory);
  if (auto) args.push('--allow-auto');
  args.push('--max-attempts',String(maxAttempts));
  const child = spawn(python, args, {cwd: project, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
  let raw = '', stderr = '';
  child.stderr.on('data', chunk => {stderr += chunk;});
  const launch = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {child.kill();reject(new Error('UI launch timed out'));}, 30000);
    child.stdout.on('data', chunk => {
      raw += chunk;
      const match = raw.match(/http:\/\/127\.0\.0\.1:\d+\/#session=[0-9a-f]{64}/);
      if (match) {clearTimeout(timer);resolve(match[0]);}
    });
    child.once('error', error => {clearTimeout(timer);reject(error);});
    child.once('exit', code => {clearTimeout(timer);reject(new Error('UI startup exit '+code+': '+stderr));});
  });
  const url = new URL(launch);
  const token = new URLSearchParams(url.hash.slice(1)).get('session');
  const base = url.origin;
  const headers = {Authorization: 'Bearer '+token};
  async function api(endpoint, body) {
    const result = await fetch(base+'/api/'+endpoint, {signal:AbortSignal.timeout(15000), headers: {...headers,...(body?{'Content-Type':'application/json'}:{})},
      method: body?'POST':'GET', ...(body?{body: JSON.stringify(body)}:{})});
    const value = await result.json();
    if (!result.ok) throw new Error('UI HTTP '+result.status+': '+value.error);
    return value;
  }
  // Listener readiness only; these are local HTTP reads, never model/Antigravity requests.
  for (let i=0;i<30;i++) {
    try {await api('state');break;} catch(error) {
      if (i===29) {child.kill();throw error;}
      await new Promise(resolve=>setTimeout(resolve,100));
    }
  }
  return {child, launch, base, api, directory, async stop() {
    if (child.exitCode === null && child.signalCode === null) {
      const ended = new Promise(resolve => child.once('exit', resolve));
      child.kill();await ended;
    }
  }};
}

export async function operation(ui, action, payload) {
  const {randomUUID} = await import('node:crypto');
  const job = await ui.api('actions/'+action, {...payload, request_id:randomUUID().replaceAll('-','')});
  const deadline=Date.now()+90000;
  while (Date.now()<deadline) {
    const state=await ui.api('state');const row=state.jobs.find(item=>item.id===job.id);
    if(row.status==='failed')throw new Error(row.error);
    if(row.status==='finished')return {result:row.result,state};
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error('UI operation exceeded test deadline');
}

export async function git(directory, ...args) {
  const result=await exec('git',['-C',path.join(directory,'repository'),...args],{cwd:project});
  return result.stdout.trim();
}

export async function removeOwnFixture(directory, parent) {
  const resolved=path.resolve(directory), root=path.resolve(parent);
  if(path.dirname(resolved)!==root||!path.basename(resolved).startsWith('pi-ui-'))
    throw new Error('Refuse recursive cleanup outside generated UI test directory');
  await fs.rm(resolved,{recursive:true,force:true});
}
