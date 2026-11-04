import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const ceilings = {
  workspace_max_entries:250000, workspace_max_bytes:8*1024**3,
  catalog_max_files:250000, catalog_max_path_bytes:32*1024**2,
  source_context_max_files:128, source_file_max_bytes:2*1024**2,
  planning_packet_max_bytes:256*1024**2, discovery_max_files:2000,
  discovery_max_bytes:128*1024**2, discovery_catalog_max_entries:250000,
  discovery_seconds:600, candidate_max_rounds:30, test_timeout_max_seconds:7200,
  native_session_max_bytes:1024**3,
};
export function validateMaintenanceLimits(values) {
  if(!values || Array.isArray(values)
    || Object.keys(values).sort().join(',')!==Object.keys(ceilings).sort().join(',')
    || Object.entries(values).some(([k,v])=>!Number.isSafeInteger(v)||v<1||v>ceilings[k])
    || values.source_file_max_bytes>values.discovery_max_bytes
    || values.source_file_max_bytes*values.source_context_max_files>values.planning_packet_max_bytes
    || values.catalog_max_files>values.discovery_catalog_max_entries)
    throw new Error('Invalid maintenance resource limits configuration');
  return Object.freeze({...values});
}
// A source checkout keeps the JSON under src/repo_maintainer; a wheel ships it
// next to the packaged runtime directory. No second hand-maintained copy.
const here = path.dirname(fileURLToPath(import.meta.url));
const location = path.basename(here) === 'pi_runtime'
  ? path.join(here, '..', 'maintenance_limits.json')
  : path.join(here, '..', '..', 'src', 'repo_maintainer', 'maintenance_limits.json');
if (!fs.existsSync(location))
  throw new Error('Missing maintenance resource limits configuration: ' + location);
export const MAINTENANCE_LIMITS=validateMaintenanceLimits(JSON.parse(fs.readFileSync(location,'utf8')));
