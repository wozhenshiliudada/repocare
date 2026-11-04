"""Signed GitHub facts and a durable inbox on the original monitor journal.

No receiver, Git, provider, executor or second request ledger is created here.
An inbox dispatch intent only permits the original controller to inspect work.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re

from .github_connection import branch_name, github_url
from .models import SafetyError, digest
from .store import now, redact
from .ui_registry import UIRegistry

MAX_BODY_BYTES = 25 * 1024 * 1024
MAX_DELIVERIES = 10000
MAX_WORK = 1000
MAX_JOURNAL_BYTES = 32 * 1024 * 1024
TARGET = 'github_push_target'
DELIVERIES = 'github_push_delivery'
WORK = 'github_push_work'
FINISH_STATUSES = frozenset({'event_linked', 'blocked', 'superseded', 'failed'})
_TARGET_KEYS = {'monitor_id', 'repository_id', 'repository', 'branch'}
_FACT_KEYS = {'event', 'delivery_id', 'payload_sha256', 'repository_id', 'repository',
              'ref', 'before', 'after', 'created', 'deleted', 'forced'}
_CRITICAL = {'x-github-event', 'x-github-delivery', 'x-hub-signature-256',
             'content-type', 'content-length', 'content-encoding', 'transfer-encoding'}
_SHA = re.compile(r'[a-f0-9]{40}')
_HASH = re.compile(r'[a-f0-9]{64}')
_ZERO = '0' * 40


class PushInputError(SafetyError):
    def __init__(self, message, *, http_status=400, code='invalid_push'):
        super().__init__(message)
        self.http_status, self.code = http_status, code


def _need(value, message='Invalid GitHub push payload', *, http_status=400, code='invalid_push'):
    if not value:
        raise PushInputError(message, http_status=http_status, code=code)


def _positive(value):
    return type(value) is int and value > 0


def _identifier(value):
    return isinstance(value, str) and 1 <= len(value) <= 200 and all(33 <= ord(c) <= 126 for c in value)


def validate_target(target):
    _need(isinstance(target, dict) and set(target) == _TARGET_KEYS,
          'Invalid push target configuration', code='invalid_target')
    _need(isinstance(target['monitor_id'], str) and re.fullmatch(r'[a-f0-9]{16}', target['monitor_id'])
          and _positive(target['repository_id']) and isinstance(target['repository'], str),
          'Invalid push target configuration', code='invalid_target')
    try:
        slug, _ = github_url('https://github.com/' + target['repository'])
        branch = branch_name(target['branch'])
    except (SafetyError, ValueError, TypeError):
        raise PushInputError('Invalid push target configuration', code='invalid_target') from None
    _need(slug.casefold() == target['repository'].casefold(),
          'Invalid push target configuration', code='invalid_target')
    return {'monitor_id': target['monitor_id'], 'repository_id': target['repository_id'],
            'repository': slug.casefold(), 'branch': branch}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate')
        result[key] = value
    return result


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('nonfinite')
    return number


def _strict_json(raw):
    return json.loads(raw, object_pairs_hook=_object, parse_float=_float,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))


def _headers(headers, size):
    _need(isinstance(headers, list) and len(headers) <= 100)
    result = {}
    for pair in headers:
        _need(isinstance(pair, tuple) and len(pair) == 2 and all(isinstance(x, str) for x in pair))
        name, value = pair
        _need(len(name) <= 200 and re.fullmatch(r'[!#$%&\x27*+.^_`|~0-9A-Za-z-]+', name) and len(value) <= 8192
              and not any(ord(c) < 32 or ord(c) == 127 for c in value))
        name = name.casefold()
        if name in _CRITICAL:
            _need(name not in result, 'Duplicate critical GitHub push header')
            result[name] = value if name == 'x-github-delivery' else value.strip()
    _need('transfer-encoding' not in result, 'Unsupported push body encoding',
          http_status=415, code='unsupported_media_type')
    _need(result.get('content-encoding', 'identity').casefold() == 'identity',
          'Unsupported push body encoding', http_status=415, code='unsupported_media_type')
    content_type = [p.strip().casefold() for p in result.get('content-type', '').split(';')]
    _need(content_type[0] == 'application/json' and all(p in {'charset=utf-8', 'charset="utf-8"'}
          for p in content_type[1:]) and len(content_type) <= 2,
          'GitHub push requires JSON', http_status=415, code='unsupported_media_type')
    length = result.get('content-length')
    if length is not None:
        _need(re.fullmatch(r'[0-9]{1,10}', length))
        _need(int(length) <= MAX_BODY_BYTES, 'GitHub push body is too large',
              http_status=413, code='payload_too_large')
        _need(int(length) == size)
    return result


def _check_fact(fact, target):
    _need(isinstance(fact, dict) and set(fact) == _FACT_KEYS)
    _need(isinstance(fact['event'], str) and fact['event'] in {'push', 'ping'} and _identifier(fact['delivery_id'])
          and isinstance(fact['payload_sha256'], str) and _HASH.fullmatch(fact['payload_sha256']))
    _need(_positive(fact['repository_id']) and fact['repository_id'] == target['repository_id']
          and fact['repository'] == target['repository'])
    _need(all(type(fact[k]) is bool for k in ('created', 'deleted', 'forced')))
    if fact['event'] == 'ping':
        _need(all(fact[k] is None for k in ('ref', 'before', 'after'))
              and not any(fact[k] for k in ('created', 'deleted', 'forced')))
    else:
        _need(fact['ref'] == 'refs/heads/' + target['branch']
              and all(isinstance(fact[k], str) and _SHA.fullmatch(fact[k]) for k in ('before', 'after')))
        _need((fact['deleted'] and fact['after'] == _ZERO)
              or (not fact['deleted'] and fact['after'] != _ZERO))
    return copy.deepcopy(fact)


def normalize_push(body: bytes, headers: list[tuple[str, str]], target: dict, secret: bytes):
    target = validate_target(target)
    _need(type(body) is bytes)
    _need(len(body) <= MAX_BODY_BYTES, 'GitHub push body is too large',
          http_status=413, code='payload_too_large')
    _need(type(secret) is bytes and 32 <= len(secret) <= 4096,
          'Invalid push signature configuration', code='invalid_target')
    values = _headers(headers, len(body))
    signature = values.get('x-hub-signature-256', '')
    expected = 'sha256=' + hmac.new(secret, body, hashlib.sha256).hexdigest()
    _need(re.fullmatch(r'sha256=[a-f0-9]{64}', signature) and hmac.compare_digest(signature, expected),
          'GitHub push signature verification failed', http_status=403, code='invalid_signature')
    event = values.get('x-github-event')
    _need(event in {'push', 'ping'}, 'Unsupported GitHub webhook event',
          http_status=422, code='unsupported_event')
    delivery = values.get('x-github-delivery')
    _need(_identifier(delivery))
    try:
        payload = _strict_json(body.decode('utf-8'))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise PushInputError('Invalid GitHub push JSON') from None
    _need(isinstance(payload, dict))
    repository = payload.get('repository')
    _need(isinstance(repository, dict) and _positive(repository.get('id'))
          and repository['id'] == target['repository_id'] and repository.get('private') is True
          and isinstance(repository.get('full_name'), str)
          and repository['full_name'].casefold() == target['repository'])
    fact = {'event': event, 'delivery_id': delivery, 'payload_sha256': digest(body),
            'repository_id': target['repository_id'], 'repository': target['repository'],
            'ref': None, 'before': None, 'after': None, 'created': False, 'deleted': False, 'forced': False}
    if event == 'push':
        _need(all(key in payload for key in ('ref', 'before', 'after', 'created', 'deleted', 'forced')))
        fact.update({key: payload[key] for key in ('ref', 'before', 'after', 'created', 'deleted', 'forced')})
        head = payload.get('head_commit')
        if head is not None:
            _need(isinstance(head, dict))
            if 'id' in head:
                _need(isinstance(head['id'], str) and _SHA.fullmatch(head['id']) and head['id'] == fact['after'])
    return _check_fact(fact, target)


def _work_id(target, fact):
    return digest(json.dumps([target['monitor_id'], fact['repository_id'], fact['repository'],
                              fact['ref'], fact['after']], separators=(',', ':')).encode())


class PushInbox:
    """Only the passed, already-owned monitor UIRegistry receives inbox facts.

    Constructor binds the mapping only. Startup recovery must be explicitly
    invoked by the controller, never by constructing a second live adapter.
    """
    def __init__(self, registry, target):
        if not isinstance(registry, UIRegistry):
            raise SafetyError('Push inbox requires the original open monitor journal')
        self.registry, self.target = registry, validate_target(target)
        with registry.transaction() as db:
            row = db.execute('SELECT body FROM metadata').fetchone()
            try:
                metadata = _strict_json(row[0]) if row else {}
                if not isinstance(metadata, dict) or not isinstance(metadata.get('monitor'), dict):
                    raise ValueError('mapping')
            except (ValueError, TypeError, RecursionError):
                raise SafetyError('Push inbox requires the original GitHub monitor mapping') from None
            monitor = metadata.get('monitor', {})
            try:
                slug, _ = github_url(monitor.get('github_url'))
            except (SafetyError, ValueError, TypeError):
                raise SafetyError('Push inbox requires the original GitHub monitor mapping') from None
            if (monitor.get('id') != self.target['monitor_id'] or monitor.get('branch') != self.target['branch']
                    or slug.casefold() != self.target['repository']):
                raise SafetyError('Push inbox target differs from the original monitor mapping')
            saved = self._read(db, TARGET, self.target['monitor_id'])
            expected = {'schema_version': 1, 'target': self.target}
            if saved is None:
                self._write(db, TARGET, self.target['monitor_id'], expected)
                self._capacity(db)
            elif saved != expected or type(saved.get('schema_version')) is not int:
                raise SafetyError('Push inbox target mapping changed; preserve the original journal')

    @staticmethod
    def _read(db, kind, identity):
        row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (kind, identity)).fetchone()
        if row is None:
            return None
        try:
            value = _strict_json(row[0])
            if not isinstance(value, dict):
                raise ValueError('object')
            return value
        except (TypeError, ValueError, RecursionError):
            raise SafetyError('Push inbox journal data is invalid') from None

    def _write(self, db, kind, identity, value):
        self.registry.put(kind, identity, value, db)
        if self._read(db, kind, identity) != value:
            raise SafetyError('Push inbox cannot preserve its exact journal facts')

    def _bound(self, db):
        saved = self._read(db, TARGET, self.target['monitor_id'])
        if (saved != {'schema_version': 1, 'target': self.target}
                or type(saved.get('schema_version')) is not int):
            raise SafetyError('Push inbox target mapping changed; preserve the original journal')

    @staticmethod
    def _capacity(db):
        if db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0] > MAX_JOURNAL_BYTES:
            raise PushInputError('Push inbox is full; preserve earlier notifications', http_status=503, code='inbox_full')

    def _work(self, db, identity):
        row = self._read(db, WORK, identity)
        if row is None:
            raise SafetyError('Original push work is missing')
        keys = {'schema_version', 'id', 'monitor_id', 'repository_id', 'repository', 'ref', 'after',
                'event_id', 'status', 'received_at', 'dispatch_at', 'finished_at', 'message'}
        fact = {k: row.get(k) for k in ('repository_id', 'repository', 'ref', 'after')}
        if (set(row) != keys or type(row['schema_version']) is not int or row['schema_version'] != 1 or row['id'] != identity
                or row['monitor_id'] != self.target['monitor_id']
                or not _positive(row['repository_id']) or row['repository_id'] != self.target['repository_id'] or row['repository'] != self.target['repository']
                or row['ref'] != 'refs/heads/' + self.target['branch']
                or not isinstance(row['after'], str) or not _SHA.fullmatch(row['after']) or row['after'] == _ZERO
                or _work_id(self.target, fact) != identity or row['event_id'] != digest(row['after'].encode())[:16]
                or not isinstance(row['status'], str) or row['status'] not in {'received', 'dispatch_intent', 'interrupted_no_replay', *FINISH_STATUSES}
                or not isinstance(row['received_at'], str) or not isinstance(row['message'], str)):
            raise SafetyError('Original push work binding changed')
        if row['status'] == 'received':
            valid = row['dispatch_at'] is None and row['finished_at'] is None and row['message'] == ''
        elif row['status'] == 'dispatch_intent':
            valid = isinstance(row['dispatch_at'], str) and row['finished_at'] is None and row['message'] == ''
        else:
            valid = isinstance(row['dispatch_at'], str) and isinstance(row['finished_at'], str)
        if not valid:
            raise SafetyError('Original push work lifecycle changed')
        return row

    def _delivery(self, db, identity):
        row = self._read(db, DELIVERIES, identity)
        if row is None:
            return None
        if (set(row) != {'schema_version', 'id', 'fact', 'receipt', 'received_at'}
                or type(row['schema_version']) is not int or row['schema_version'] != 1
                or row['id'] != identity or not isinstance(row['received_at'], str)):
            raise SafetyError('Original push delivery binding changed')
        try:
            fact = _check_fact(row['fact'], self.target)
        except PushInputError:
            raise SafetyError('Original push delivery facts changed') from None
        if fact['delivery_id'] != identity:
            raise SafetyError('Original push delivery identity changed')
        receipt = row['receipt']
        if not isinstance(receipt, dict) or set(receipt) != {'id', 'status', 'work_id', 'event_id'} or receipt['id'] != identity:
            raise SafetyError('Original push receipt binding changed')
        if fact['event'] == 'ping' or fact['deleted'] or fact['forced']:
            status = 'ping' if fact['event'] == 'ping' else 'blocked_deleted' if fact['deleted'] else 'blocked_forced'
            valid = receipt == {'id': identity, 'status': status, 'work_id': None, 'event_id': None}
        else:
            work_id = _work_id(self.target, fact)
            valid = (isinstance(receipt['status'], str) and receipt['status'] in {'received', 'duplicate_commit'}
                     and receipt['work_id'] == work_id and receipt['event_id'] == digest(fact['after'].encode())[:16])
            self._work(db, work_id)
        if not valid:
            raise SafetyError('Original push receipt facts changed')
        return row

    def accept(self, fact):
        fact = _check_fact(fact, self.target)
        with self.registry.transaction() as db:
            self._bound(db)
            old = self._delivery(db, fact['delivery_id'])
            if old is not None:
                if old.get('fact') != fact:
                    raise PushInputError('GitHub delivery identity has conflicting content',
                                         http_status=409, code='delivery_conflict')
                return copy.deepcopy(old['receipt'])
            if db.execute('SELECT count(*) FROM objects WHERE kind=?', (DELIVERIES,)).fetchone()[0] >= MAX_DELIVERIES:
                raise PushInputError('Push inbox is full; preserve earlier notifications', http_status=503, code='inbox_full')
            work_id, event_id = None, None
            if fact['event'] == 'ping':
                status = 'ping'
            elif fact['deleted']:
                status = 'blocked_deleted'
            elif fact['forced']:
                status = 'blocked_forced'
            else:
                work_id = _work_id(self.target, fact)
                event_id = digest(fact['after'].encode())[:16]
                if self._read(db, WORK, work_id) is None:
                    if db.execute('SELECT count(*) FROM objects WHERE kind=?', (WORK,)).fetchone()[0] >= MAX_WORK:
                        raise PushInputError('Push inbox is full; preserve earlier notifications', http_status=503, code='inbox_full')
                    self._write(db, WORK, work_id, {'schema_version': 1, 'id': work_id,
                        'monitor_id': self.target['monitor_id'], 'repository_id': fact['repository_id'],
                        'repository': fact['repository'], 'ref': fact['ref'], 'after': fact['after'], 'event_id': event_id,
                        'status': 'received', 'received_at': now(), 'dispatch_at': None, 'finished_at': None, 'message': ''})
                    status = 'received'
                else:
                    self._work(db, work_id)
                    status = 'duplicate_commit'
            receipt = {'id': fact['delivery_id'], 'status': status, 'work_id': work_id, 'event_id': event_id}
            self._write(db, DELIVERIES, fact['delivery_id'], {'schema_version': 1,
                'id': fact['delivery_id'], 'fact': fact, 'receipt': receipt, 'received_at': now()})
            self._capacity(db)
            return copy.deepcopy(receipt)

    def _rows(self, db, kind, maximum):
        ids = db.execute('SELECT id FROM objects WHERE kind=? ORDER BY rowid LIMIT ?', (kind, maximum + 1)).fetchall()
        if len(ids) > maximum:
            raise SafetyError('Push inbox history exceeds its preserved bound')
        return [self._work(db, identity) if kind == WORK else self._delivery(db, identity) for (identity,) in ids]

    def pending(self):
        with self.registry.transaction() as db:
            self._bound(db)
            return [r for r in self._rows(db, WORK, MAX_WORK) if r['status'] == 'received']

    def begin(self, work_id):
        with self.registry.transaction() as db:
            self._bound(db)
            row = self._work(db, work_id)
            if row['status'] != 'received':
                return None
            row.update(status='dispatch_intent', dispatch_at=now())
            self._write(db, WORK, work_id, row)
            return copy.deepcopy(row)

    def finish(self, work_id, status, *, event_id=None, message=''):
        if not isinstance(status, str) or status not in FINISH_STATUSES or not isinstance(message, str) or len(message) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in message):
            raise SafetyError('Invalid push work outcome')
        message = re.sub(r'https?://\S+', '[URL]', redact(message))
        with self.registry.transaction() as db:
            self._bound(db)
            row = self._work(db, work_id)
            if event_id is not None and event_id != row['event_id']:
                raise SafetyError('Push outcome belongs to a different commit event')
            if status == 'event_linked' and event_id != row['event_id']:
                raise SafetyError('Linked push outcome requires the exact original event')
            if row['status'] == status:
                if row['message'] != message:
                    raise SafetyError('Push outcome has already been recorded differently')
                return copy.deepcopy(row)
            if row['status'] != 'dispatch_intent':
                raise SafetyError('Push work was not claimed or has already ended; no replay')
            row.update(status=status, finished_at=now(), message=message)
            self._write(db, WORK, work_id, row)
            return copy.deepcopy(row)

    def recover(self):
        changed = []
        with self.registry.transaction() as db:
            self._bound(db)
            for row in self._rows(db, WORK, MAX_WORK):
                if row['status'] == 'dispatch_intent':
                    row.update(status='interrupted_no_replay', finished_at=now(),
                               message='Previous dispatch outcome is unknown; inspect the original event')
                    self._write(db, WORK, row['id'], row)
                    changed.append(copy.deepcopy(row))
        return changed

    def view(self):
        with self.registry.transaction() as db:
            self._bound(db)
            return {'deliveries': self._rows(db, DELIVERIES, MAX_DELIVERIES),
                    'work': self._rows(db, WORK, MAX_WORK)}
