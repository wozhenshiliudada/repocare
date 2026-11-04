"""Fixed-commit task-time source tools; callers own grants and Native binding.

The journal is the original task registry. A tool retry or new process never
creates new capacity. This module performs no model call and grants no writes.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import re

from .maintenance_limits import LIMITS
from .models import SafetyError, digest, safe_context_path
from .source_discovery import CommittedTree
from .store import redact
from .task_session import repository_identity


def _hash(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())


def _require(value, message):
    if not value:
        raise SafetyError(message)


def _deadline(value):
    try:
        parsed = datetime.fromisoformat(value)
        _require(parsed.tzinfo is not None, 'Investigation deadline requires timezone')
        return parsed
    except (TypeError, ValueError):
        raise SafetyError('Invalid investigation deadline') from None


class TaskInvestigation:
    """Controller-owned broker. Issue/model text cannot register or widen scope."""

    def __init__(self, registry, repository, task_id, *, guard=None):
        _require(isinstance(task_id, str) and re.fullmatch(r'[a-f0-9]{32}', task_id),
                 'Invalid investigation task identity')
        self.registry, self.repository, self.task_id = registry, repository, task_id
        self.guard = guard or (lambda: None)

    def register(self, *, repository_id, base_sha, read_roots, policy_sha256,
                 expires_at, max_calls=50, max_output_bytes=262144):
        """New-task controller call only, after its original rules/grant check."""
        _require(all(isinstance(v, str) and re.fullmatch(r'[a-f0-9]{64}', v)
                     for v in (repository_id, policy_sha256)), 'Invalid investigation authority binding')
        _require(repository_id == repository_identity(self.repository.root, self.repository.common_dir),
                 'Investigation repository identity differs')
        _require(type(max_calls) is int and 1 <= max_calls <= 500
                 and type(max_output_bytes) is int and 1024 <= max_output_bytes <= 1048576,
                 'Invalid investigation tool bounds')
        _require(_deadline(expires_at) > datetime.now(timezone.utc), 'Investigation scope expired')
        self.guard()
        tree = CommittedTree(self.repository, base_sha, read_roots)
        scope = {'kind': 'task-investigation-v1', 'task_id': self.task_id,
                 'repository_id': repository_id, 'base_sha': tree.head,
                 'read_roots': list(tree.roots), 'policy_sha256': policy_sha256,
                 'expires_at': expires_at, 'max_calls': max_calls,
                 'max_output_bytes': max_output_bytes,
                 'scan_byte_limit': LIMITS['discovery_max_bytes'],
                 'unique_read_limit': LIMITS['discovery_max_files']}
        record = {'scope': scope, 'scope_sha256': _hash(scope), 'calls': 0,
                  'scan_bytes': 0, 'output_bytes': 0, 'read_paths': [], 'receipts': []}
        with self.registry.transaction() as db:
            saved = db.execute("SELECT body FROM objects WHERE kind=? AND id=?",
                               ('task_investigation', self.task_id)).fetchone()
            old = json.loads(saved[0]) if saved else None
            if old is not None:
                self._record(db)
                _require(old.get('scope') == scope and old.get('scope_sha256') == _hash(scope),
                         'Existing investigation scope cannot be replaced')
                return old['scope_sha256']
            self.registry.put('task_investigation', self.task_id, record, db)
        return record['scope_sha256']

    def existing_scope(self):
        """Read-only restart aid: the persisted scope, never a fresh grant."""
        with self.registry.transaction() as db:
            saved = db.execute("SELECT body FROM objects WHERE kind=? AND id=?",
                               ('task_investigation', self.task_id)).fetchone()
            if not saved:
                return None
            return deepcopy(self._record(db)['scope'])

    def journal(self):
        """Validated original row for Native transcript/receipt reconciliation."""
        with self.registry.transaction() as db:
            return deepcopy(self._record(db))

    def _record(self, db):
        saved = db.execute("SELECT body FROM objects WHERE kind=? AND id=?",
                           ('task_investigation', self.task_id)).fetchone()
        row = json.loads(saved[0]) if saved else None
        _require(isinstance(row, dict) and set(row) == {'scope', 'scope_sha256', 'calls',
                 'scan_bytes', 'output_bytes', 'read_paths', 'receipts'} and isinstance(row.get('scope'), dict),
                 'No original investigation scope')
        scope = row['scope']
        _require(row.get('scope_sha256') == _hash(scope) and scope.get('task_id') == self.task_id,
                 'Investigation scope binding changed')
        _require(set(scope) == {'kind','task_id','repository_id','base_sha','read_roots','policy_sha256',
                               'expires_at','max_calls','max_output_bytes','scan_byte_limit','unique_read_limit'}
                 and scope['kind'] == 'task-investigation-v1'
                 and type(scope['max_calls']) is int and 1 <= scope['max_calls'] <= 500
                 and type(scope['max_output_bytes']) is int and 1024 <= scope['max_output_bytes'] <= 1048576
                 and scope['scan_byte_limit'] == LIMITS['discovery_max_bytes']
                 and scope['unique_read_limit'] == LIMITS['discovery_max_files'],
                 'Investigation bounds changed')
        _require(all(type(row[name]) is int and 0 <= row[name] <= maximum for name, maximum in
                     [('calls', scope['max_calls']), ('scan_bytes', scope['scan_byte_limit']),
                      ('output_bytes', scope['max_output_bytes'])]), 'Invalid investigation counters')
        names, receipts = row['read_paths'], row['receipts']
        _require(isinstance(names, list) and all(isinstance(name, str) for name in names)
                 and names == sorted(set(names)) and len(names) <= scope['unique_read_limit']
                 and isinstance(receipts, list) and len(receipts) <= row['calls'],
                 'Invalid investigation usage journal')
        # Newly written receipts pin their call index so Native can reconcile a
        # selected branch even when an older uncommitted tail exists. Historical
        # receipts remain readable by their journal position.
        _require(all(isinstance(receipt, dict) and
                     set(receipt) in ({'result_sha256','bytes','operation'},
                                      {'call','result_sha256','bytes','operation'})
                     and isinstance(receipt['result_sha256'], str)
                     and re.fullmatch(r'[a-f0-9]{64}', receipt['result_sha256'])
                     and type(receipt['bytes']) is int and receipt['bytes'] > 0
                     and receipt['operation'] in {'read_source','search_sources'}
                     and ('call' not in receipt or
                          (type(receipt['call']) is int and receipt['call'] == index))
                     for index, receipt in enumerate(receipts))
                 and sum(receipt['bytes'] for receipt in receipts) == row['output_bytes'],
                 'Investigation receipt accounting differs')
        _require(scope.get('repository_id') == repository_identity(self.repository.root, self.repository.common_dir),
                 'Investigation repository identity differs')
        _require(_deadline(scope['expires_at']) > datetime.now(timezone.utc),
                 'Investigation scope expired')
        return row

    def _tree(self, row):
        scope = row['scope']
        return CommittedTree(self.repository, scope['base_sha'], scope['read_roots'])

    def _reserve(self, *, paths=(), scan_bytes=0, begin=False):
        self.guard()
        with self.registry.transaction() as db:
            row = self._record(db)
            scope = row['scope']
            names = sorted(set(row['read_paths']) | set(paths))
            _require(row['calls'] + int(begin) <= scope['max_calls']
                     and row['scan_bytes'] + scan_bytes <= scope['scan_byte_limit']
                     and len(names) <= scope['unique_read_limit'], 'Investigation tool budget exhausted')
            row.update(calls=row['calls'] + int(begin), scan_bytes=row['scan_bytes'] + scan_bytes,
                       read_paths=names)
            self.registry.put('task_investigation', self.task_id, row, db)
        return row

    def _finish(self, result):
        self.guard()
        with self.registry.transaction() as db:
            row = self._record(db)
            call = len(row['receipts'])
            result.update(scope_sha256=row['scope_sha256'], base_sha=row['scope']['base_sha'],
                          runtime_authorized=False, write_authorized=False, call_index=call)
            size = len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode())
            remaining = row['scope']['max_output_bytes'] - row['output_bytes']
            # Keep partial evidence usable when valid UTF-8 line windows are
            # larger than remaining capacity. Never hide that coverage shrank.
            items = result.get('lines', result.get('hits'))
            while size > remaining and items:
                items.pop()
                result['output_truncated'] = True
                result['omitted_rows'] = result.get('omitted_rows', 0) + 1
                if 'complete_catalog_scan' in result:
                    result['complete_catalog_scan'] = False
                if 'truncated' in result:
                    result['truncated'] = True
                size = len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode())
            _require(row['output_bytes'] + size <= row['scope']['max_output_bytes'],
                     'Investigation output budget exhausted')
            row['output_bytes'] += size
            row['receipts'].append({'call': call, 'result_sha256': _hash(result), 'bytes': size,
                                    'operation': result['operation']})
            self.registry.put('task_investigation', self.task_id, row, db)
        return result

    def read_source(self, path, *, start_line=1, max_lines=80):
        safe_context_path(path)
        _require(type(start_line) is int and start_line >= 1 and type(max_lines) is int
                 and 1 <= max_lines <= 200, 'Invalid investigation line window')
        row = self._reserve(begin=True)
        tree = self._tree(row)
        _require(path in tree.entries, 'Source outside original read roots')
        # Reserve before Git I/O. Exceptions/interruptions keep the reservation.
        self._reserve(paths=(path,), scan_bytes=tree.entries[path]['bytes'])
        raw = tree.read(path)
        try:
            _require(b'\0' not in raw, 'Binary source cannot enter investigation context')
            lines = raw.decode('utf-8-sig').splitlines()
        except UnicodeError:
            raise SafetyError('Source is not supported UTF-8 text') from None
        selected = lines[start_line - 1:start_line - 1 + max_lines]
        # Bound lines even when a minified source has a very long single line.
        clipped = any(len(line) > 500 for line in selected)
        rows = [{'line': start_line + i, 'text': redact(line)[:500]} for i, line in enumerate(selected)]
        return self._finish({'operation': 'read_source', 'source': tree.pin(path), 'lines': rows,
                             'total_lines': len(lines), 'truncated': clipped or
                             start_line - 1 + len(selected) < len(lines),
                             'side': 'original_fixed_commit'})

    def search_sources(self, text, *, max_hits=20):
        _require(isinstance(text, str) and 1 <= len(text.encode('utf-8')) <= 512
                 and '\0' not in text and type(max_hits) is int and 1 <= max_hits <= 100,
                 'Search requires bounded literal text')
        row = self._reserve(begin=True)
        tree = self._tree(row)
        hits, skipped, scanned, complete = [], 0, 0, True
        for path in sorted(tree.entries):
            self._reserve(paths=(path,), scan_bytes=tree.entries[path]['bytes'])
            raw = tree.read(path)
            scanned += 1
            try:
                if b'\0' in raw:
                    skipped += 1
                    continue
                lines = raw.decode('utf-8-sig').splitlines()
            except UnicodeError:
                skipped += 1
                continue
            for line_number, line in enumerate(lines, 1):
                if text.casefold() in line.casefold():
                    hits.append({'source': tree.pin(path), 'line': line_number,
                                 'text': redact(line)[:500], 'excerpt_truncated': len(line) > 500})
                    if len(hits) == max_hits:
                        complete = False
                        break
            if not complete:
                break
        return self._finish({'operation': 'search_sources', 'query_sha256': digest(text.encode()),
                             'hits': hits, 'scanned_files': scanned, 'skipped_nontext': skipped,
                             'catalog_files': len(tree.entries),
                             'complete_catalog_scan': complete and not tree.tree_truncated,
                             'catalog_truncated': tree.tree_truncated,
                             'interpretation': 'Literal text matches in fixed permitted sources; not a root-cause proof.'})
