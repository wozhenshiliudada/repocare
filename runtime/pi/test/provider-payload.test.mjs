import test from 'node:test';
import assert from 'node:assert/strict';
import {preparePayload} from '../provider-payload.mjs';

test('MiMo standard and Token Plan payloads disable thinking and preserve token budget', () => {
  for (const host of ['api.xiaomimimo.com', 'token-plan-cn.xiaomimimo.com']) {
    const payload = preparePayload({max_tokens: 128000, messages: [], tools: []}, `https://${host}/v1`);
    assert.deepEqual(payload.thinking, {type: 'disabled'});
    assert.equal(payload.max_completion_tokens, 128000);
    assert.equal(payload.max_tokens, undefined);
  }
});

test('other endpoints retain their payload and DeepSeek retains its existing compatibility', () => {
  assert.deepEqual(preparePayload({max_tokens: 99}, 'https://example.invalid/v1'), {max_tokens: 99});
  assert.deepEqual(preparePayload({max_tokens: 99}, 'https://api.deepseek.com'),
    {max_tokens: 99, thinking: {type: 'disabled'}});
});
