// User-entered declarations, not provider capability discovery. k/M are decimal.
export const MAX_SAFE_TOKEN_COUNT = Number.MAX_SAFE_INTEGER;

export function parseModelTokenValue(text) {
  if (typeof text !== 'string') return null;
  const match = /^(0|[1-9][0-9]*)(?:\.([0-9]+))?([kKmM]?)$/.exec(text.trim());
  if (!match || match[1].length > 16) return null;
  const exponent = match[3] ? (match[3].toLowerCase() === 'k' ? 3 : 6) : 0;
  // Remove zero-only precision before checking that the result is whole tokens.
  const fraction = (match[2] || '').replace(/0+$/, '');
  if (fraction.length > exponent) return null;
  const value = BigInt(match[1] + fraction.padEnd(exponent, '0'));
  return value > 0n && value <= BigInt(MAX_SAFE_TOKEN_COUNT) ? Number(value) : null;
}

export function parseModelTokenValues(contextText, outputText) {
  if (typeof contextText !== 'string' || typeof outputText !== 'string') return null;
  const fields = {};
  if (contextText.trim()) {
    const value = parseModelTokenValue(contextText);
    if (value === null || value < 4096) return null;
    fields.context_window_tokens = value;
  }
  if (outputText.trim()) {
    const value = parseModelTokenValue(outputText);
    if (value === null) return null;
    fields.max_output_tokens = value;
  }
  if (fields.context_window_tokens !== undefined && fields.max_output_tokens !== undefined
      && fields.max_output_tokens >= fields.context_window_tokens) return null;
  return fields;
}
