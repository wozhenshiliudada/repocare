// Provider compatibility is chosen by the configured endpoint, not arbitrary prompt text.
export function preparePayload(payload, baseUrl, reasoning=null) {
  const host = new URL(baseUrl).hostname;
  if (!reasoning?.explicit && host === 'api.deepseek.com') payload.thinking = {type: 'disabled'};
  if (host === 'api.xiaomimimo.com' || host === 'token-plan-cn.xiaomimimo.com') {
    if (!reasoning?.explicit) payload.thinking = {type: 'disabled'};
    if (payload.max_tokens !== undefined) {
      payload.max_completion_tokens = payload.max_tokens;
      delete payload.max_tokens;
    }
  }
  if (reasoning?.explicit) {
    delete payload.reasoning_effort;
    delete payload.thinking;
    if (reasoning.protocol === 'deepseek') {
      payload.thinking = {type: reasoning.effort === 'off' ? 'disabled' : 'enabled'};
      if (reasoning.effort !== 'off' && reasoning.apiValue !== null) payload.reasoning_effort = reasoning.apiValue;
    } else if (reasoning.protocol === 'openai' && reasoning.apiValue !== null) {
      payload.reasoning_effort = reasoning.apiValue;
    }
  }
  return payload;
}
