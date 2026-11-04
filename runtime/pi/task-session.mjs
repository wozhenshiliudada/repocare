import {MAINTENANCE_LIMITS as LIMITS} from './maintenance-limits.mjs';
// A Python-validated checkpoint receipt, never a path or authority from a model/UI text.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {SessionManager, VERSION} from '@earendil-works/pi-coding-agent';
import {INVESTIGATION_PURPOSE} from './investigation-tool.mjs';
import {REASONING_EFFORTS} from './provider-model.mjs';

const MAX_BYTES = LIMITS.native_session_max_bytes;
const sha = raw => createHash('sha256').update(raw).digest('hex');
const hex = (value, n) => typeof value === 'string' && new RegExp(`^[0-9a-f]{${n}}$`).test(value);
const check = (condition, message) => {if (!condition) throw new Error(message);};

async function noLinks(value) {
  check(typeof value === 'string' && path.isAbsolute(value) && path.normalize(value) === value,
    'Invalid controlled Pi session path');
  let cursor = value;
  while (true) {
    check(!(await fs.lstat(cursor)).isSymbolicLink(), 'Pi session path contains a link');
    const parent = path.dirname(cursor);
    if (parent === cursor) break;
    cursor = parent;
  }
  check((await fs.realpath(value)).toLowerCase() === value.toLowerCase(), 'Pi session path changed');
}

async function pinnedBytes(receipt) {
  await noLinks(receipt.session_file);
  const handle = await fs.open(receipt.session_file, 'r');
  try {
    check((await handle.stat()).size === receipt.file_bytes, 'Pi session size changed');
    const raw = Buffer.alloc(receipt.file_bytes + 1);
    const {bytesRead} = await handle.read(raw, 0, raw.length, 0);
    check(bytesRead === receipt.file_bytes, 'Pi session changed during read');
    check(sha(raw.subarray(0, bytesRead)) === receipt.file_sha256, 'Pi session changed after Python validation');
  } finally {await handle.close();}
}

export async function openTaskSession(receipt) {
  check(VERSION === '1.0.4', 'Expected fixed Pi 1.0.4');
  const fields = ['schema_version','task_id','revision','session_file','cwd','session_id','leaf_id',
    'file_sha256','file_bytes','tail_uncommitted'];
  check(receipt && Object.keys(receipt).sort().join() === fields.sort().join() && receipt.schema_version === 1
    && hex(receipt.task_id, 32) && Number.isInteger(receipt.revision) && receipt.revision >= 1
    && typeof receipt.session_id === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(receipt.session_id)
    && typeof receipt.leaf_id === 'string' && /^(?:[0-9a-f]{8}|[0-9a-f]{32}|[0-9a-f-]{36})$/.test(receipt.leaf_id)
    && hex(receipt.file_sha256, 64) && Number.isInteger(receipt.file_bytes)
    && receipt.file_bytes > 0 && receipt.file_bytes <= MAX_BYTES && typeof receipt.tail_uncommitted === 'boolean',
    'Invalid controlled Pi checkpoint receipt');
  await noLinks(receipt.cwd);
  const directory = path.dirname(receipt.cwd);
  check(path.basename(directory) === receipt.task_id && path.basename(receipt.cwd) === 'workspace'
    && path.dirname(receipt.session_file) === path.join(directory, 'sessions')
    && path.extname(receipt.session_file) === '.jsonl', 'Pi session escaped its task directory');
  await pinnedBytes(receipt);
  // Python strictly parsed all raw JSONL. SDK is invoked only after its exact bytes match.
  const manager = SessionManager.open(receipt.session_file, path.dirname(receipt.session_file), receipt.cwd);
  check(manager.getSessionId() === receipt.session_id, 'Pi restored a different session identity');
  manager.branch(receipt.leaf_id); // Re-select on every process open; SDK branch is not independently persisted.
  await pinnedBytes(receipt);
  return manager;
}

export async function planningTaskSession(task, provider, model, paths, purpose='repair', thinkingLevel='off') {
  const validationData=purpose==='validation-proposal-v1'&&task?.context?.schema_version===3;
  const contextFields=purpose==='repair'?'model,paths,provider':
    purpose==='validation-proposal-v1'?(validationData?'model,paths,provider,purpose,schema_version,validation_schema':'model,paths,provider,purpose,schema_version'):
    purpose===INVESTIGATION_PURPOSE?'investigation,model,paths,provider,purpose,schema_version':null;
  const validRoot = value => value === '.' || (typeof value === 'string' && value.length > 0
    && value.length <= 512 && !value.startsWith('/') && !value.includes('\\') && !value.includes(':')
    && !value.split('/').some(part => part === '' || part === '.' || part === '..'));
  check(task && Object.keys(task).sort().join() === 'binding,context,mode' &&
    contextFields && task.context && Object.keys(task.context).filter(k=>k!=='thinking_level').sort().join() === contextFields &&
    (purpose==='repair'||(task.context.purpose===purpose&&
      (validationData?task.context.validation_schema==='data-behavior-v2':task.context.schema_version===2))) &&
    [provider,model].every(v=>typeof v==='string'&&v.length>0&&Array.from(v).length<=128) &&
    Array.isArray(paths)&&paths.length>=1&&paths.length<=20&&paths.every(p=>typeof p==='string') &&
    new Set(paths.map(p=>p.toLowerCase())).size===paths.length &&
    task.context.provider === provider && task.context.model === model &&
    JSON.stringify(task.context.paths) === JSON.stringify(paths), 'Pi task runtime context changed');
  const hasThinkingLevel = Object.hasOwn(task.context,'thinking_level');
  check(REASONING_EFFORTS.openai.includes(thinkingLevel) &&
    (hasThinkingLevel ? thinkingLevel !== 'off' && task.context.thinking_level === thinkingLevel
                      : thinkingLevel === 'off'), 'Pi task thinking level changed');
  if (purpose===INVESTIGATION_PURPOSE) {
    const bound=task.context.investigation;
    check(bound && Object.keys(bound).sort().join()==='max_calls,max_output_bytes,read_roots,schema_version'
      && bound.schema_version===1 && Number.isInteger(bound.max_calls) && bound.max_calls>=1
      && bound.max_calls<=500 && Number.isInteger(bound.max_output_bytes)
      && bound.max_output_bytes>=1024 && bound.max_output_bytes<=1048576
      && Array.isArray(bound.read_roots) && bound.read_roots.length>=1 && bound.read_roots.length<=20
      && bound.read_roots.every(validRoot)
      && new Set(bound.read_roots.map(p=>p.toLowerCase())).size===bound.read_roots.length,
      'Pi investigation context changed');
  }
  if (task.mode === 'open') return openTaskSession(task.binding);
  const b = task.binding;
  check(task.mode === 'create' && b && Object.keys(b).sort().join() === 'cwd,revision,schema_version,sessions_dir,task_id'
    && b.schema_version === 1 && b.revision === 0 && hex(b.task_id,32), 'Invalid new controlled Pi task');
  await noLinks(b.cwd); await noLinks(b.sessions_dir);
  const directory=path.dirname(b.cwd);
  check(path.basename(directory) === b.task_id && path.basename(b.cwd) === 'workspace'
    && b.sessions_dir === path.join(directory,'sessions') && (await fs.readdir(b.sessions_dir)).length === 0,
    'New Pi session escaped its empty task directory');
  return SessionManager.create(b.cwd,b.sessions_dir);
}
