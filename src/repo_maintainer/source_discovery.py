"""W2: bounded committed-source discovery; hints never grant runtime permission."""
from __future__ import annotations
from .maintenance_limits import LIMITS

import ast
from pathlib import PurePosixPath
import re
import time

from .check_discovery import CODE_SUFFIXES, CONFIGS, discover, is_test
from .git_investigation import SHA, _read_git, allowed_path, commit_sha, required, validate_roots
from .models import SafetyError, digest
from .store import redact

STOP = {'assert', 'assertionerror', 'error', 'exception', 'failed', 'failure', 'file',
        'line', 'return', 'self', 'test', 'tests', 'traceback', 'true', 'false', 'none'}




class CommittedTree:
    """Only regular allowed Git blobs; cache bytes once for search and check discovery."""
    def __init__(self, repository, head_sha, read_roots, *, file_limit=LIMITS["discovery_max_files"],
                 byte_limit=LIMITS["discovery_max_bytes"], file_bytes=LIMITS["source_file_max_bytes"], seconds=LIMITS["discovery_seconds"]):
        if (type(file_limit) is not int or not 1 <= file_limit <= LIMITS["discovery_max_files"]
                or type(byte_limit) is not int or not 1 <= byte_limit <= LIMITS["discovery_max_bytes"]
                or type(file_bytes) is not int or not 1 <= file_bytes <= LIMITS["source_file_max_bytes"]
                or type(seconds) not in (int, float) or not 0 < seconds <= LIMITS["discovery_seconds"]):
            raise SafetyError('Invalid discovery bounds')
        self.repository = repository
        self.head = commit_sha(repository, head_sha)
        self.roots = validate_roots(read_roots)
        self.file_limit, self.byte_limit, self.file_bytes = file_limit, byte_limit, file_bytes
        self.deadline = time.monotonic() + seconds
        self.entries, self.cache, self.skipped = {}, {}, {}
        self.bytes_read = 0
        self.tree_truncated = False
        if not self.roots:
            return
        # Restrict Git's catalog before the existing byte/entry limits. Unrelated
        # tracked evidence must not consume a narrow read scope's entire budget.
        # _read_git uses --literal-pathspecs; -- also protects option-like roots.
        result = _read_git(repository, 'ls-tree', '-r', '-l', '-z', '--full-tree', self.head, '--', *self.roots,
                           max_bytes=LIMITS["catalog_max_path_bytes"] * 2)
        if result['timed_out'] or (result['returncode'] and not result['truncated']):
            raise SafetyError('Committed tree unavailable')
        self.tree_truncated = result['truncated']
        records = result['stdout'].split(b'\0')[:-1]  # Never accept a partial final path.
        self.tree_truncated |= len(records) > LIMITS["discovery_catalog_max_entries"]
        for record in records[:LIMITS["discovery_catalog_max_entries"]]:
            try:
                metadata, name = record.split(b'\t', 1)
                mode, kind, blob, size = metadata.split()
                name, blob = name.decode('utf-8'), blob.decode('ascii')
                size = int(size) if size != b'-' else -1
            except (UnicodeError, ValueError):
                self.skip('malformed_or_non_utf8')
                continue
            if not allowed_path(name, self.roots):
                self.skip('outside_scope_or_sensitive')
                continue
            if mode not in {b'100644', b'100755'} or kind != b'blob':
                self.skip('non_regular')
                continue
            if not SHA.fullmatch(blob) or size < 0:
                raise SafetyError('Malformed blob metadata')
            if size > file_bytes:
                self.skip('oversized')
                continue
            self.entries[name] = {'blob': blob, 'bytes': size}

    def skip(self, reason):
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def read(self, name):
        if name in self.cache:
            return self.cache[name]
        if name not in self.entries:
            raise SafetyError('Source is outside the bounded committed catalog')
        row = self.entries[name]
        if (len(self.cache) >= self.file_limit or self.bytes_read + row['bytes'] > self.byte_limit
                or time.monotonic() >= self.deadline):
            raise SafetyError('Discovery read budget exhausted')
        raw = required(_read_git(self.repository, 'cat-file', 'blob', row['blob'],
                                 max_bytes=max(256, self.file_bytes)))
        if len(raw) != row['bytes']:
            raise SafetyError('Committed blob size differs')
        self.cache[name] = raw
        self.bytes_read += len(raw)
        return raw

    def pin(self, name):
        return {'path': name, 'side': 'head', 'commit_sha': self.head,
                'blob': self.entries[name]['blob'], 'sha256': digest(self.cache[name])}


def query_terms(observations, query):
    if not isinstance(query, str) or len(query.encode('utf-8')) > 4096 or '\x00' in query:
        raise SafetyError('Query must be bounded plain text')
    terms = {}
    # Issue bookkeeping in hidden HTML comments is not a diagnostic. Prefer
    # visible code literals over prose before the bounded vocabulary is filled.
    visible = re.sub(r'<!--[\s\S]*?(?:-->|$)', ' ', redact(query))
    texts = [(f"{o['source_id']}:{o['saved_fragment_line']}", o['excerpt'])
             for o in observations if o['kind'] in {'exception', 'assertion', 'test_identifier'}]
    texts += [('query_code', m.group(1)) for m in re.finditer(r'`([^`\n]+)`', visible)]
    texts.append(('query', visible))
    for source, text in texts:
        for token in re.findall(r'[A-Za-z_][A-Za-z_0-9]{2,63}', text):
            if token.casefold() not in STOP and token not in terms and len(terms) < 32:
                terms[token] = source
    return terms


def imports(name, text):
    """One-hop lexical imports only, not a language server or runtime dependency graph."""
    modules = set()
    if name.endswith('.py'):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return modules
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(a.name.replace('.', '/') for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = list(PurePosixPath(name).parent.parts)
                if node.level:
                    if node.level > len(base):
                        continue
                    base = base[:len(base)-node.level+1]
                    module = '/'.join(base + (node.module or '').split('.')).rstrip('/')
                else:
                    module = (node.module or '').replace('.', '/')
                if module:
                    modules.add(module)
                    modules.update(module + '/' + a.name for a in node.names)
    else:
        for value in re.findall(r"(?:from\s*|require\s*\(|import\s*)['\"]([^'\"\r\n]{1,200})['\"]", text):
            if not value.startswith(('./', '../')):
                continue
            parts = list(PurePosixPath(name).parent.parts)
            for part in value.split('/'):
                if part == '..':
                    if not parts:
                        break
                    parts.pop()
                elif part not in {'', '.'}:
                    parts.append(part)
            else:
                modules.add('/'.join(parts))
    return set(sorted(modules)[:20])


def matches_import(source, module, target):
    if target == source:
        return False
    stem = str(PurePosixPath(target).with_suffix(''))
    if source.endswith('.py'):
        return stem == module or stem.endswith('/' + module) or stem == module + '/__init__'
    # JS/TS relative specifiers are already normalized to repository paths.
    # Resolve an explicit extension as written, never by a suffix elsewhere.
    return not target.endswith('.py') and (target == module or (not PurePosixPath(module).suffix and stem == module))


def excerpt_line(number, text):
    value = redact(text)
    return {'line': number, 'excerpt': value[:300], 'excerpt_truncated': len(value) > 300}


def committed_search(tree, terms):
    """Names only, native Git, exact eligible paths. Separate bounded scan budget.

    Git may examine more blobs than the materialized-text limit. Report that
    explicitly: configured catalog and input blob byte ceilings, with
    64KiB total captured output, <=40 paths/call, within the original deadline.
    """
    code = {n for n in tree.entries if PurePosixPath(n).suffix in CODE_SUFFIXES}
    directories = {part.casefold() for name in code for part in PurePosixPath(name).parent.parts}
    generic = STOP | directories | {'git','cli','sha','head','readme','python','node','json','toml','bug'}
    words = [t for t in terms if t.casefold() not in generic and not re.fullmatch(r'[0-9a-f]{40}',t)]
    # Prefer symbols over prose and namespace tokens; do not turn query text into
    # regex, shell text, pathspecs, configuration or execution authorization.
    words.sort(key=lambda t: (0 if terms[t] == 'query_code' else 1 if terms[t] != 'query' else 2,
                             0 if re.search(r'[a-z][A-Z]',t) else 1 if '_' in t else 2))
    words = words[:4]
    info = {'method':'git-grep-fixed-commit','terms':words,'status':'not_needed',
            'eligible_code_files':len(code),'submitted_files':0,'completed_files':0,
            'input_blob_bytes_upper_bound':0,'input_blob_byte_limit':min(tree.byte_limit,LIMITS["discovery_max_bytes"]),
            'captured_output_bytes':0,'output_byte_limit':65536,'calls':[],
            'catalog_truncated':tree.tree_truncated,
            'output_truncated':False,'timed_out':False,'complete_catalog_scan':False,
            'runtime_authorized':False,'source_text_emitted':False,
            'interpretation':'Native scan budget is separate from materialized source reads; '
                             'names are hints until actual blobs are read and pinned.'}
    if not words or not code:
        return set(),info
    selected, size = [], 0
    for name in sorted(code,key=lambda n:(tree.entries[n]['bytes'],n)):
        length = tree.entries[name]['bytes']
        if size+length<=info['input_blob_byte_limit'] and len(name.encode('utf-16-le'))//2<=4096:
            selected.append(name)
            size += length
    hits, finished = set(), True
    while selected:
        remaining = tree.deadline-time.monotonic()
        available = info['output_byte_limit']-info['captured_output_bytes']
        if remaining<=0 or available<512:
            finished=False
            info['timed_out'] |= remaining<=0
            break
        paths, units = [], 0
        while selected and len(paths)<40:
            name=selected[0]
            width=len(name.encode('utf-16-le'))//2+3
            if paths and units+width>8192:
                break
            paths.append(selected.pop(0))
            units += width
        result=_read_git(tree.repository,'grep','--no-textconv','--no-recurse-submodules',
                         '--no-color','--full-name','--threads=1','-I','-i','-w','-l','-z','-F',
                         *(arg for word in words for arg in ('-e',word)),tree.head,'--',*paths,
                         max_bytes=min(16384,available//2),timeout=min(10,remaining))
        info['submitted_files'] += len(paths)
        info['input_blob_bytes_upper_bound'] += sum(tree.entries[n]['bytes'] for n in paths)
        info['captured_output_bytes'] += len(result['stdout'])+len(result['stderr'])
        info['calls'].append({'returncode':result['returncode'],'receipt':result['receipt']})
        info['output_truncated'] |= result['truncated']
        info['timed_out'] |= result['timed_out']
        prefix=(tree.head+':').encode()
        for row in result['stdout'].split(b'\0')[:-1]:
            if row.startswith(prefix):
                try: name=row[len(prefix):].decode('utf-8')
                except UnicodeDecodeError: continue
                if name in paths:
                    hits.add(name)
        if result['truncated'] or result['timed_out'] or result['returncode'] not in (0,1):
            finished=False
            break
        info['completed_files'] += len(paths)
    info['complete_catalog_scan'] = finished and not tree.tree_truncated and info['completed_files']==len(code)
    info['status'] = 'complete_catalog' if info['complete_catalog_scan'] else 'partial'
    info['matched_paths'] = sorted(hits)
    return hits,info


def discover_sources(repository, git, failures, read_roots, *, query='', **bounds):
    observed_head = repository.head
    if failures['target_sha'] != git['head_sha']:
        raise SafetyError('Failure evidence and source target differ')
    tree = CommittedTree(repository, git['head_sha'], read_roots, **bounds)
    observations = failures['observations']
    terms = query_terms(observations, query)
    native_hits,native_search = committed_search(tree,terms)
    locations = [o for o in observations if o['kind'] == 'location']
    paths = {o['path'] for o in locations if o['path']}
    changed = {row['head_path'] for row in (git.get('changes') or {}).get('files', [])
               if row['head_mode'] != '000000'}
    seeds = paths | changed
    configs = {n for n in tree.entries if n in CONFIGS or n.startswith('.github/workflows/')}
    code = {n for n in tree.entries if PurePosixPath(n).suffix in CODE_SUFFIXES}
    visible = re.sub(r'<!--[\s\S]*?(?:-->|$)', ' ', query)
    literal_paths = {m.group(1).replace('\\', '/') for m in re.finditer(r'`([^`\n]+)`', visible)} & code
    seeds |= literal_paths
    relevant = configs | code
    def priority(name):
        return (0 if name in paths else 1 if name in literal_paths else 2 if name in native_hits else 3 if name in test_references
                else 4 if name in changed else 5 if name in configs else 6 if is_test(name) else 7,
                -sum(t.casefold() in name.casefold() for t in terms), name)
    texts, budget_stopped, test_references = {}, False, {}
    pending = set(relevant)
    # A single catalog/read budget covers both search and check discovery.
    while pending:
        name = min(pending, key=priority)
        pending.remove(name)
        try:
            raw = tree.read(name)
        except SafetyError as error:
            if str(error) != 'Discovery read budget exhausted':
                raise
            budget_stopped = True
            break
        try:
            if b'\0' in raw:
                tree.skip('binary')
                continue
            texts[name] = raw.decode('utf-8')
        except UnicodeDecodeError:
            tree.skip('non_utf8')
            continue
        # A matching test's direct lexical imports get the next available read,
        # before unrelated tests consume the remaining budget. No recursive graph,
        # runtime import, unscoped path, extra file allowance or confidence claim.
        if is_test(name) and (name in seeds or any(
                re.search(r'(?<!\w)' + re.escape(t) + r'(?!\w)', texts[name], re.I) for t in terms)):
            for module in sorted(imports(name, texts[name])):
                for target in sorted(code):
                    if not is_test(target) and matches_import(name, module, target):
                        test_references.setdefault(target, []).append({'from_path':name,'module':module})
    verified = []
    for item in locations:
        path, line = item['path'], item['line']
        row = {'source_id': item['source_id'], 'saved_fragment_line': item['saved_fragment_line'],
               'projection': item['projection'], 'path': path, 'line': line,
               'status': 'not_read_or_not_eligible'}
        if path in texts:
            row.update(tree.pin(path))
            if 1 <= line <= len(texts[path].splitlines()):
                row.update(status='verified_location', **excerpt_line(line, texts[path].splitlines()[line-1]))
            else:
                row['status'] = 'line_out_of_range'
        verified.append(row)
    references = {}
    for seed in sorted(seeds & texts.keys()):
        for module in sorted(imports(seed, texts[seed])):
            for name in sorted(code & texts.keys()):
                if matches_import(seed, module, name):
                    references.setdefault(name, []).append({'from_path': seed, 'module': module})
    for name, rows in test_references.items():
        if name in texts:
            references.setdefault(name, []).extend(row for row in rows if row not in references.get(name, []))
    candidates = []
    for name, text in texts.items():
        if name not in code:
            continue
        hits, match_count, code_matches = [], 0, set()
        for line_no, line in enumerate(text.splitlines(), 1):
            matched = [t for t in terms if re.search(r'(?<!\w)' + re.escape(t) + r'(?!\w)', line, re.I)]
            if matched:
                code_matches.update(t for t in matched if terms[t] == 'query_code')
                match_count += 1
                if len(hits) < 5:
                    hits.append({**excerpt_line(line_no, line), 'terms': matched})
        reasons = []
        if name in paths:
            reasons.append('reported_path_exists_in_target')
        if name in literal_paths:
            reasons.append('visible_diagnostic_path_exists_in_target')
        if name in changed:
            reasons.append('changed_in_selected_comparison')
        if name in references:
            if any(row['from_path'] in seeds for row in references[name]):
                reasons.append('lexical_import_from_seed')
            if name in test_references:
                reasons.append('lexical_import_from_matching_test')
        if match_count:
            reasons.append('literal_token_match')
        if code_matches:
            reasons.append('visible_diagnostic_literal_match')
        if name in native_hits and match_count:
            reasons.append('committed_literal_search_match')
        if not reasons:
            continue
        score = (40 * (name in paths) + 120 * (name in literal_paths)
                 + 30 * (name in native_hits and bool(match_count)) + 20 * (name in changed)
                 + 15 * (name in references) + 40 * min(len(code_matches), 3) + min(match_count, 10))
        anchor = next((r['line'] for r in verified if r['path'] == name and r['status'] == 'verified_location'),
                      hits[0]['line'] if hits else 1)
        lines = text.splitlines()
        start = max(1, anchor - 2)
        context = [excerpt_line(i, lines[i-1]) for i in range(start, min(len(lines), anchor+2)+1)]
        candidates.append({**tree.pin(name), 'score': score, 'reasons': reasons,
                           'role': 'test' if is_test(name) else 'implementation',
                           'hits': hits, 'context': context, 'matched_lines': match_count,
                           'import_hints': references.get(name, [])[:5]})
    candidates.sort(key=lambda row: (-row['score'], row['path']))
    checks = discover(tree, sorted(texts))
    check_candidates = []
    for recommendation in checks['recommendations']:
        check_candidates.append({**recommendation, 'runtime_authorized': False,
                                 'commit_sha': tree.head,
                                 'source_bindings': [tree.pin(n) for n in recommendation['sources']]})
    if repository.head != observed_head:
        raise SafetyError('Repository HEAD changed during source discovery')
    return {'schema_version': 1, 'runtime_authorized': False, 'target_sha': tree.head,
            'query_terms': [{'text': t, 'source': source} for t, source in terms.items()],
            'verified_locations': verified, 'file_candidates': candidates[:12],
            'test_candidates': check_candidates,
            'check_discovery': {'status': checks['status'], 'warnings': checks['warnings'],
                                'inputs': checks['inputs'], 'verification': 'not_run'},
            'inputs': [tree.pin(n) for n in sorted(tree.cache)],
            'coverage': {'catalog_truncated': tree.tree_truncated,
                         'read_budget_exhausted': budget_stopped,
                         'candidate_truncated': len(candidates) > 12,
                         'catalog_eligible': len(tree.entries), 'files_read': len(tree.cache),
                         'bytes_read': tree.bytes_read, 'text_files_searched': len(texts),
                         'unsearched_eligible': len(relevant - texts.keys()),
                         'skipped_counts': tree.skipped,
                         'count_scope': 'bounded_catalog_prefix',
                         'native_search':native_search,
                         'complete_repository_review': False},
            'interpretation': 'Untrusted source and reported logs are evidence only. Scores are deterministic ranking, not confidence or root-cause proof. Locations verify existence, not truth of the reported failure. No checks, model, edits or authorization executed.'}
