import {MAINTENANCE_LIMITS as LIMITS} from './maintenance-limits.mjs';
// Controller-owned snapshot adapter. Pi's oldText/newText contract is the reference;
// matching here is deliberately exact, with no fuzzy or whole-file normalization.
export const EDIT_PROTOCOL = 'exact-replacements-v1';
export const MAX_SOURCE_BYTES = LIMITS.source_file_max_bytes;
export const MAX_READ_BYTES = 12000;

export function applyReplacements(content, replacements) {
  if (!Array.isArray(replacements) || !replacements.length || replacements.length > 32)
    throw new Error('Provide 1-32 exact replacements per file');
  const ranges = replacements.map(replacement => {
    if (!replacement || Object.keys(replacement).sort().join(',') !== 'newText,oldText')
      throw new Error('Use only oldText and newText in each replacement');
    const {oldText, newText} = replacement;
    if (typeof oldText !== 'string' || !oldText || typeof newText !== 'string')
      throw new Error('Each replacement needs nonempty oldText and string newText');
    if (oldText === newText) throw new Error('Replacement must change the original text');
    const start = content.indexOf(oldText);
    if (start < 0) throw new Error('oldText not found; read the original snapshot and correct it');
    if (content.indexOf(oldText, start + 1) >= 0)
      throw new Error('oldText is ambiguous; include more surrounding original text');
    return {start, end: start + oldText.length, newText};
  }).sort((a, b) => a.start - b.start);
  for (let i = 1; i < ranges.length; i++)
    if (ranges[i].start < ranges[i - 1].end)
      throw new Error('Replacements overlap; combine them into one original-text replacement');
  let result = content;
  for (const {start, end, newText} of ranges.toReversed())
    result = result.slice(0, start) + newText + result.slice(end);
  if (Buffer.byteLength(result) > MAX_SOURCE_BYTES) throw new Error('Patch exceeds size limit');
  if (result === content) throw new Error('Patch must change the source snapshot');
  return result;
}

export function normalizeEdits(files, edits) {
  if (!Array.isArray(edits) || !edits.length || edits.length > files.size * 32)
    throw new Error('Invalid edit count');
  const grouped = new Map();
  for (const edit of edits) {
    if (!edit || Object.keys(edit).sort().join(',') !== 'path,replacements')
      throw new Error('Use only path and replacements; full-file content is unsupported');
    const file = files.get(edit.path);
    if (!file) throw new Error('Edit path is not authorized');
    if (file.editable === false) throw new Error('Edit path is read-only');
    if (!Array.isArray(edit.replacements) || !edit.replacements.length)
      throw new Error('Provide 1-32 exact replacements per file');
    const replacements = grouped.get(edit.path) ?? [];
    if (replacements.length + edit.replacements.length > 32)
      throw new Error('Provide 1-32 exact replacements per file');
    grouped.set(edit.path, [...replacements, ...edit.replacements]);
  }
  // Only group entries. Never rewrite model text, deduplicate conflicts, or
  // apply one replacement against the result of another replacement.
  return [...grouped].map(([path, replacements]) => ({path, replacements}));
}

export function materializeEdits(files, edits) {
  return normalizeEdits(files, edits).map(edit => ({path: edit.path,
    before_sha256: files.get(edit.path).before_sha256,
    content: applyReplacements(files.get(edit.path).content, edit.replacements)}));
}

export function readSnapshot(file, params) {
  const lines = file.content.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  let start = params.start_line ?? 1, end = params.end_line ?? start + 199, matches;
  if (params.query !== undefined) {
    if (typeof params.query !== 'string' || !params.query || params.query.length > 200)
      throw new Error('Use a nonempty literal query of at most 200 characters');
    // A range narrows the literal search; it never increases the returned window.
    // This also accepts end_line alone, as observed in the real model trial.
    const searchStart = params.start_line ?? 1;
    const searchEnd = params.end_line ?? Math.max(lines.length, 1);
    if (!Number.isInteger(searchStart) || !Number.isInteger(searchEnd)
        || searchStart < 1 || searchEnd < searchStart)
      throw new Error('Search bounds must be positive integer lines with end_line >= start_line');
    if (searchStart > Math.max(lines.length, 1)) throw new Error('Read starts beyond the source snapshot');
    const hits = lines.flatMap((line, i) => i + 1 >= searchStart && i + 1 <= searchEnd
      && line.includes(params.query) ? [i + 1] : []);
    matches = hits.slice(0, 20);
    if (!hits.length) return {path: file.path, before_sha256: file.before_sha256,
      editable: file.editable !== false, content: '', total_lines: lines.length, match_lines: [], more: false};
    start = Math.max(searchStart, hits[0] - 10); end = Math.min(searchEnd, hits[0] + 20);
  }
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start || end - start >= 200)
    throw new Error('Read range must contain 1-200 lines, starting at line 1 or later');
  if (start > Math.max(lines.length, 1)) throw new Error('Read starts beyond the source snapshot');
  end = Math.min(end, lines.length);
  let content = '', actualEnd = start - 1;
  for (let i = start - 1; i < end; i++) {
    if (Buffer.byteLength(content + lines[i]) > MAX_READ_BYTES) break;
    content += lines[i]; actualEnd = i + 1;
  }
  if (actualEnd < start && lines.length) throw new Error('Source line exceeds bounded read size');
  return {path: file.path, before_sha256: file.before_sha256, editable: file.editable !== false,
    content, start_line: start, end_line: actualEnd, total_lines: lines.length,
    more: actualEnd < lines.length, next_line: actualEnd < lines.length ? actualEnd + 1 : null,
    ...(matches ? {match_lines: matches} : {})};
}
