// R4: versioned read-only investigation tools. Python TaskInvestigation owns
// every grant, counter and receipt; this module only carries bounded requests.
import {MAINTENANCE_LIMITS as LIMITS} from './maintenance-limits.mjs';
export const INVESTIGATION_PURPOSE = 'repair-investigation-v1';
export const INVESTIGATION_TOOL_NAMES = Object.freeze(['read_approved_source', 'search_approved_sources']);
const MAX_RESULT_BYTES = 1200000;
const MAX_REQUEST_BYTES = 65536;
const check = (condition, message) => {if (!condition) throw new Error(message);};
const hex = (value, n) => typeof value === 'string' && new RegExp(`^[0-9a-f]{${n}}$`).test(value);

export class InvestigationDenied extends Error {
  constructor(message) {
    super(message);
    this.name = 'InvestigationDenied';
  }
}

function root(value) {
  if (value === '.') return true;
  return typeof value === 'string' && value.length > 0 && value.length <= 512
    && !value.startsWith('/') && !value.includes('\\') && !value.includes(':')
    && !value.split('/').some(part => part === '' || part === '.' || part === '..');
}

export function verifiedInvestigation(value, request) {
  const context = request?.task_session?.context;
  if (!value) {
    check(!context || context.purpose !== INVESTIGATION_PURPOSE,
      'Investigation Native scope lacks its bridge');
    return null;
  }
  check(context && context.schema_version === 2 && context.purpose === INVESTIGATION_PURPOSE,
    'Investigation bridge requires its versioned Native scope');
  check(value && Object.keys(value).sort().join() ===
    'base_sha,bridge,max_calls,max_output_bytes,read_roots,schema_version,scope_sha256'
    && value.schema_version === 1 && hex(value.scope_sha256, 64) && hex(value.base_sha, 40)
    && value.base_sha === request.base_sha, 'Invalid investigation bridge binding');
  check(Array.isArray(value.read_roots) && value.read_roots.length >= 1
    && value.read_roots.length <= 20 && value.read_roots.every(root)
    && new Set(value.read_roots.map(p => p.toLowerCase())).size === value.read_roots.length
    && Number.isInteger(value.max_calls) && value.max_calls >= 1 && value.max_calls <= 500
    && Number.isInteger(value.max_output_bytes)
    && value.max_output_bytes >= 1024 && value.max_output_bytes <= 1048576,
    'Invalid investigation bridge bounds');
  check(context.paths.length >= 1 && context.paths.length <= LIMITS.source_context_max_files
    && JSON.stringify(context.investigation?.read_roots) === JSON.stringify(value.read_roots)
    && context.investigation?.max_calls === value.max_calls
    && context.investigation?.max_output_bytes === value.max_output_bytes,
    'Investigation bridge differs from the Native scope');
  check(value.bridge && Object.keys(value.bridge).sort().join() === 'token,url'
    && hex(value.bridge.token, 64), 'Invalid investigation bridge credential');
  let url;
  try {url = new URL(value.bridge.url);} catch {throw new Error('Invalid investigation bridge URL');}
  check(url.protocol === 'http:' && url.hostname === '127.0.0.1'
    && !url.username && !url.password && !url.search && !url.hash
    && (url.pathname === '/' || url.pathname === ''), 'Investigation bridge must be loopback HTTP');
  return value;
}

export async function investigationToolCall(investigation, tool, args) {
  const operation = tool === 'search_approved_sources' ? 'search_sources' : 'read_source';
  const body = JSON.stringify({tool, args});
  check(Buffer.byteLength(body) <= MAX_REQUEST_BYTES, 'Investigation request exceeds bridge bound');
  let response, text;
  try {
    response = await fetch(new URL('/v1/tool', investigation.bridge.url), {
      method: 'POST', redirect: 'error',
      headers: {'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + investigation.bridge.token},
      body, signal: AbortSignal.timeout(30000)});
    text = await response.text();
  } catch {
    throw new InvestigationDenied('Investigation bridge unavailable');
  }
  if (Buffer.byteLength(text) > MAX_RESULT_BYTES) {
    throw new InvestigationDenied('Investigation bridge unavailable');
  }
  let value;
  try {value = JSON.parse(text);} catch {throw new InvestigationDenied('Investigation bridge unavailable');}
  if (!response.ok) {
    if (response.status === 409 || value?.error === 'budget_exhausted') {
      throw new InvestigationDenied('Investigation budget exhausted');
    }
    if (response.status === 403 && value?.error === 'denied') {
      throw new InvestigationDenied('Investigation denied by the original read scope');
    }
    throw new InvestigationDenied('Investigation bridge unavailable');
  }
  check(value && typeof value === 'object'
    && value.operation === operation
    && value.scope_sha256 === investigation.scope_sha256
    && value.base_sha === investigation.base_sha
    && value.runtime_authorized === false && value.write_authorized === false
    && Number.isInteger(value.call_index) && value.call_index >= 0, 'Invalid investigation receipt');
  return value;
}
