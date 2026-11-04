export const MAX_SAFE_TOKEN_COUNT: number;
export function parseModelTokenValue(text: string): number | null;
export type ModelTokenValues = {context_window_tokens?: number; max_output_tokens?: number};
export function parseModelTokenValues(contextText: string, outputText: string): ModelTokenValues | null;
