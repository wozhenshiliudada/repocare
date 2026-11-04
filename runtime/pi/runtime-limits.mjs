import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

// One source of truth in both layouts. A source checkout keeps the JSON under
// src/repo_maintainer; a wheel ships it next to the packaged runtime directory.
const here = path.dirname(fileURLToPath(import.meta.url));
const location = path.basename(here) === 'pi_runtime'
  ? path.join(here, '..', 'pi_runtime_limits.json')
  : path.join(here, '..', '..', 'src', 'repo_maintainer', 'pi_runtime_limits.json');
if (!fs.existsSync(location))
  throw new Error('Missing Pi runtime limits configuration: ' + location);
const config = JSON.parse(fs.readFileSync(location, 'utf8'));
if (Object.keys(config).sort().join(',') !== 'max_output_tokens,max_requests,parent_timeout_grace_seconds,planning_timeout_seconds'
    || Object.values(config).some(value => !Number.isSafeInteger(value) || value <= 0))
  throw new Error('Invalid Pi runtime limits configuration');
// Keep the historical JSON field/default; it does not cap declared model limits.
export const DEFAULT_MAX_OUTPUT_TOKENS = config.max_output_tokens;
export const MAX_OUTPUT_TOKENS = DEFAULT_MAX_OUTPUT_TOKENS;
export const MAX_SAFE_TOKEN_COUNT = Number.MAX_SAFE_INTEGER;
export const MAX_REQUESTS = config.max_requests;
export const PLANNING_TIMEOUT_SECONDS = config.planning_timeout_seconds;
export const PLANNING_TIMEOUT_MS = PLANNING_TIMEOUT_SECONDS * 1000;
export const MAX_USAGE_RECORDS = MAX_REQUESTS + 1;
