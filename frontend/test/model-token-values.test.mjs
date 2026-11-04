import test from 'node:test';
import assert from 'node:assert/strict';
import {parseModelTokenValue, parseModelTokenValues} from '../src/model-token-values.mjs';

test('token declarations use decimal k/K/m/M and exact whole-token arithmetic', () => {
  for (const [text, expected] of [['1k',1000],['1K',1000],['1m',1000000],['1M',1000000],
    ['1.5M',1500000],['0.001K',1],['1.0000000K',1000],[' 2.25m ',2250000],
    ['128001',128001],['9007199254740991',Number.MAX_SAFE_INTEGER],['9007199254.740991M',Number.MAX_SAFE_INTEGER]])
    assert.equal(parseModelTokenValue(text), expected, text);
  for (const text of ['', '0', '0M', '1.5', '0.0001K', '1.0000001M', '1e6', '+1M', '-1',
    '01K', '.5M', '1,000', '1Mi', 'NaN', 'Infinity', '9007199254740992', '9007199254.740992M'])
    assert.equal(parseModelTokenValue(text), null, text);
});

test('optional declarations preserve legacy omission and enforce context/output relationship', () => {
  assert.deepEqual(parseModelTokenValues(' ', ''), {});
  assert.deepEqual(parseModelTokenValues('2.5M', '1.5M'), {context_window_tokens:2500000,max_output_tokens:1500000});
  assert.deepEqual(parseModelTokenValues('', '1M'), {max_output_tokens:1000000});
  assert.deepEqual(parseModelTokenValues('4096', ''), {context_window_tokens:4096});
  for (const values of [['4K','1K'],['1M','1M'],['1M','1.5M'],['garbage',''],['','0']])
    assert.equal(parseModelTokenValues(...values), null);
});
