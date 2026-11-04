// Separately approved test evidence. It confers no tool or edit authority.
import {createHash} from 'node:crypto';
const sha = value => createHash('sha256').update(value).digest('hex');
const hex = (value, length) => typeof value === 'string' && new RegExp(`^[a-f0-9]{${length}}$`).test(value);
export function verifiedBaseline(value, request) {
  if (value === undefined) return null;
  const keys = ['schema_version','kind','probe_id','base_sha','issue_sha256','snapshot_sha256',
    'tests_sha256','probe_run_sha256','probe_execution_sha256','test_focus'];
  const fail = () => {throw new Error('Invalid preflight baseline context');};
  if (!value || Object.keys(value).sort().join() !== keys.sort().join() || value.schema_version !== 1
      || value.kind !== 'preflight_baseline' || !hex(value.probe_id,32)
      || request.task_session || request.feedback || request.continuation_feedback
      || value.base_sha !== request.base_sha || value.issue_sha256 !== sha(request.issue)
      || !hex(value.probe_run_sha256,64) || !hex(value.probe_execution_sha256,64)
      || value.tests_sha256 !== sha(JSON.stringify(request.tests))
      || value.snapshot_sha256 !== sha(JSON.stringify(request.files.map(f => [f.path,f.before_sha256,f.editable!==false])))
      || !Array.isArray(request.tests) || request.tests.length < 1 || request.tests.length > 5
      || Buffer.byteLength(JSON.stringify(value)) > 32000) fail();
  const focus = value.test_focus;
  if (!focus || !Array.isArray(focus.tests) || focus.tests.length !== Math.min(4,request.tests.length)
      || focus.omitted_tests !== Math.max(0,request.tests.length-4)
      || !focus.tests.some(t => Number.isInteger(t.returncode) && t.returncode !== 0 && t.timed_out === false)) fail();
  const seen = new Set();
  for (const row of focus.tests) {
    const expected = request.tests[row.check_index-1];
    if (!Number.isInteger(row.check_index) || seen.has(row.check_index) || !Array.isArray(expected)
        || !Array.isArray(row.command) || row.command.length !== expected.length
        || row.command.some((arg,i) => typeof arg !== 'string' || !arg || (expected[i]!=='{python}' && arg!==expected[i]))
        || !Number.isInteger(row.returncode) || row.timed_out !== false
        || typeof row.assertion_excerpt !== 'string' || Array.from(row.assertion_excerpt).length > 1800
        || typeof row.fallback_tail !== 'string' || Array.from(row.fallback_tail).length > 1000) fail();
    seen.add(row.check_index);
  }
  return value;
}
