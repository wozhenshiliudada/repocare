// Immutable profile metadata. These declared limits are not provider capability discovery.
import {DEFAULT_MAX_OUTPUT_TOKENS} from './runtime-limits.mjs';

export const MODEL_LIMIT_FIELDS = ['PI_MAINTENANCE_CONTEXT_WINDOW_TOKENS', 'PI_MAINTENANCE_MAX_OUTPUT_TOKENS'];
export function effectiveModelLimits({env=process.env, modelId, baseUrl, mockUrl, contextPolicy=null,
  legacyContextWindowTokens, legacyMaxOutputTokens=DEFAULT_MAX_OUTPUT_TOKENS}={}) {
  const explicit = MODEL_LIMIT_FIELDS.some(field => Object.hasOwn(env, field));
  const profileContext=mockUrl||(modelId==='mimo-v2.6-flash'&&
    ['api.xiaomimimo.com','token-plan-cn.xiaomimimo.com'].includes(new URL(baseUrl).hostname))?1048576:32768;
  if (legacyContextWindowTokens === undefined) legacyContextWindowTokens=profileContext;
  const values = explicit?[profileContext,DEFAULT_MAX_OUTPUT_TOKENS]:
    [contextPolicy?.context_window_tokens || legacyContextWindowTokens, legacyMaxOutputTokens];
  for (const [index, field] of MODEL_LIMIT_FIELDS.entries()) {
    if (!Object.hasOwn(env, field)) continue;
    const value = env[field];
    if (typeof value !== 'string' || !/^[1-9][0-9]*$/.test(value))
      throw new Error('Model token limits must be positive decimal integers');
    values[index] = Number(value);
  }
  const [contextWindowTokens, maxOutputTokens] = values;
  if (explicit && (!Number.isSafeInteger(contextWindowTokens) || !Number.isSafeInteger(maxOutputTokens)
      || contextWindowTokens < 4096 || maxOutputTokens < 1 || maxOutputTokens >= contextWindowTokens))
    throw new Error('Unsupported model limits: safe integer context at least 4096, positive output below context');
  // A separate historical context policy can impose a tighter safety guard;
  // it does not rewrite the selected model or any original task policy bytes.
  const budgetContextWindowTokens = explicit
    ? Math.min(contextWindowTokens, contextPolicy?.context_window_tokens || contextWindowTokens)
    : contextPolicy?.context_window_tokens || null;
  return {contextWindowTokens, maxOutputTokens, explicit,
    enforceContextBudget: budgetContextWindowTokens !== null, budgetContextWindowTokens};
}
