"""Protocol and durable original-journal contracts; no network or provider calls."""
from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from repo_maintainer import github_push as push
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.ui_registry import UIRegistry


SECRET = b'local-synthetic-signing-secret-0123456789'
TARGET = {'monitor_id': 'a' * 16, 'repository_id': 123456789,
          'repository': 'owner/private-project', 'branch': 'main'}
AFTER = 'b' * 40
BEFORE = 'a' * 40


def payload(**changes):
    value = {'repository': {'id': TARGET['repository_id'], 'full_name': TARGET['repository'], 'private': True},
             'ref': 'refs/heads/main', 'before': BEFORE, 'after': AFTER,
             'created': False, 'deleted': False, 'forced': False, 'head_commit': {'id': AFTER}}
    value.update(changes)
    return value


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def headers(body, delivery='delivery-1', event='push'):
    return [('Content-Type', 'application/json'), ('Content-Length', str(len(body))),
            ('X-GitHub-Event', event), ('X-GitHub-Delivery', delivery),
            ('X-Hub-Signature-256', 'sha256=' + hmac.new(SECRET, body, hashlib.sha256).hexdigest())]


def fact(delivery='delivery-1', *, event='push', **changes):
    body = encode(payload(**changes))
    return push.normalize_push(body, headers(body, delivery, event), TARGET, SECRET)


class ProtocolTests(unittest.TestCase):
    def assert_input_error(self, callback, code='invalid_push', status=400):
        with self.assertRaises(push.PushInputError) as caught:
            callback()
        self.assertEqual((caught.exception.code, caught.exception.http_status), (code, status))
        self.assertNotIn(SECRET.decode(), str(caught.exception))
        return caught.exception

    def test_target_strict_types_fields_and_canonical_repository(self):
        target = dict(TARGET, repository='Owner/PRIVATE-project')
        self.assertEqual(push.validate_target(target), TARGET)
        invalid = [None, dict(TARGET, extra='no'), {k: v for k, v in TARGET.items() if k != 'branch'},
                   dict(TARGET, repository_id=True), dict(TARGET, repository_id=0),
                   dict(TARGET, repository_id=1.0), dict(TARGET, repository_id=-1),
                   dict(TARGET, monitor_id='A' * 16), dict(TARGET, branch='../main'),
                   dict(TARGET, repository='owner/project?token=secret'),
                   dict(TARGET, repository='/owner/private-project/'), dict(TARGET, repository='owner/private-project.git')]
        for value in invalid:
            with self.subTest(value=value):
                self.assert_input_error(lambda: push.validate_target(value), 'invalid_target')

    def test_fact_exact_fields_raw_body_hash_and_ignored_untrusted_content(self):
        body = encode(payload(message='中文', sender={'login': 'maintainer'},
                              compare='https://attacker.invalid/token', command='delete all', path='../../escape')) + b' \n'
        result = push.normalize_push(body, headers(body), TARGET, SECRET)
        self.assertEqual(set(result), {'event', 'delivery_id', 'payload_sha256', 'repository_id', 'repository',
                                      'ref', 'before', 'after', 'created', 'deleted', 'forced'})
        self.assertEqual(result['payload_sha256'], digest(body))
        self.assertEqual(result['after'], AFTER)
        self.assertNotIn('attacker', json.dumps(result))
        self.assertNotIn(SECRET.decode(), json.dumps(result))

    def test_raw_signature_precedes_json_and_is_not_reserialized(self):
        body = encode(payload())
        signed = headers(body)
        self.assert_input_error(lambda: push.normalize_push(body + b' ', signed[:-4] + signed[-4:], TARGET, SECRET))
        # Without a length header, altered raw bytes still fail the HMAC.
        no_length = [p for p in signed if p[0] != 'Content-Length']
        self.assert_input_error(lambda: push.normalize_push(body + b' ', no_length, TARGET, SECRET), 'invalid_signature', 403)
        malformed = b'{not JSON'
        self.assert_input_error(lambda: push.normalize_push(malformed, headers(body)[:1] +
            [('X-Hub-Signature-256', 'sha256=' + '0' * 64)], TARGET, SECRET), 'invalid_signature', 403)
        self.assert_input_error(lambda: push.normalize_push(malformed, headers(malformed), TARGET, SECRET))

    def test_bad_missing_and_uppercase_signatures(self):
        body = encode(payload())
        for signature in ('', 'sha1=' + 'a' * 40, 'sha256=' + '0' * 64, 'sha256=' + 'F' * 64):
            signed = [(k, signature if k == 'X-Hub-Signature-256' else v) for k, v in headers(body)]
            with self.subTest(signature=signature):
                self.assert_input_error(lambda: push.normalize_push(body, signed, TARGET, SECRET), 'invalid_signature', 403)

    def test_duplicate_critical_headers_are_rejected_case_insensitively(self):
        body = encode(payload())
        for name, value in headers(body) + [('Content-Encoding', 'identity'), ('Transfer-Encoding', 'chunked')]:
            signed = headers(body)
            if name not in dict(signed):
                signed.append((name, value))
            signed.append((name.swapcase(), value))
            with self.subTest(name=name):
                self.assert_input_error(lambda: push.normalize_push(body, signed, TARGET, SECRET))

    def test_strict_json_duplicate_keys_utf8_nonfinite_and_root(self):
        for body in (b'{"repository":{},"repository":{}}',
                     encode(payload()).replace(b'"id":123456789', b'"id":123456789,"id":123456789'),
                     b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}',
                     b'\xff', b'[]', b'null', b'true', b'{} trailing'):
            with self.subTest(body=body[:60]):
                self.assert_input_error(lambda: push.normalize_push(body, headers(body), TARGET, SECRET))

    def test_body_limit_inclusive_and_original_bytes_type(self):
        body = encode(payload())
        padded = body + b' ' * (push.MAX_BODY_BYTES - len(body))
        self.assertEqual(push.normalize_push(padded, headers(padded), TARGET, SECRET)['payload_sha256'], digest(padded))
        self.assert_input_error(lambda: push.normalize_push(padded + b' ', [], TARGET, SECRET), 'payload_too_large', 413)
        self.assert_input_error(lambda: push.normalize_push(bytearray(body), headers(body), TARGET, SECRET))

    def test_media_encoding_and_length_guards(self):
        body = encode(payload())
        for replacement in ('text/plain', 'application/json; charset=latin1', 'application/json; anything=yes'):
            signed = [(k, replacement if k == 'Content-Type' else v) for k, v in headers(body)]
            self.assert_input_error(lambda: push.normalize_push(body, signed, TARGET, SECRET), 'unsupported_media_type', 415)
        for extra in ([('Content-Encoding', 'gzip')], [('Transfer-Encoding', 'chunked')]):
            self.assert_input_error(lambda: push.normalize_push(body, headers(body) + extra, TARGET, SECRET), 'unsupported_media_type', 415)
        for length, code, status in [('1', 'invalid_push', 400), ('-1', 'invalid_push', 400),
                                     (str(push.MAX_BODY_BYTES + 1), 'payload_too_large', 413)]:
            signed = [(k, length if k == 'Content-Length' else v) for k, v in headers(body)]
            self.assert_input_error(lambda: push.normalize_push(body, signed, TARGET, SECRET), code, status)
        signed = [(k, 'application/json; charset="UTF-8"' if k == 'Content-Type' else v) for k, v in headers(body)]
        self.assertEqual(push.normalize_push(body, signed + [('Content-Encoding', 'identity')], TARGET, SECRET)['after'], AFTER)

    def test_delivery_opaque_ascii_preserved_never_trusted_as_path(self):
        body = encode(payload())
        delivery = '../opaque:id?x=1'
        self.assertEqual(push.normalize_push(body, headers(body, delivery), TARGET, SECRET)['delivery_id'], delivery)
        for delivery in ('', ' ', ' delivery', 'delivery ', 'line\nnext', '中', 'x' * 201):
            with self.subTest(delivery=delivery):
                self.assert_input_error(lambda: push.normalize_push(body, headers(body, delivery), TARGET, SECRET))

    def test_only_push_and_ping_supported_after_valid_signature(self):
        body = encode(payload())
        self.assert_input_error(lambda: push.normalize_push(body, headers(body, event='workflow_run'), TARGET, SECRET), 'unsupported_event', 422)
        result = push.normalize_push(body, headers(body, event='ping'), TARGET, SECRET)
        self.assertEqual([result[k] for k in ('ref', 'before', 'after')], [None] * 3)
        self.assertEqual([result[k] for k in ('created', 'deleted', 'forced')], [False] * 3)

    def test_fixed_private_numeric_repository_and_ref(self):
        for repository in ({'id': True, 'full_name': TARGET['repository'], 'private': True},
                           {'id': 1, 'full_name': TARGET['repository'], 'private': True},
                           {'id': TARGET['repository_id'], 'full_name': 'other/project', 'private': True},
                           {'id': TARGET['repository_id'], 'full_name': TARGET['repository'], 'private': False},
                           {'id': TARGET['repository_id'], 'full_name': TARGET['repository'], 'private': 1}):
            body = encode(payload(repository=repository))
            self.assert_input_error(lambda: push.normalize_push(body, headers(body), TARGET, SECRET))
        self.assert_input_error(lambda: fact(ref='refs/heads/repair'))
        self.assert_input_error(lambda: fact(ref='refs/tags/main'))

    def test_required_sha_boolean_and_head_commit_types(self):
        for change in ({'before': 'A' * 40}, {'after': []}, {'created': 1}, {'deleted': 'false'},
                       {'forced': None}, {'head_commit': []}, {'head_commit': {'id': BEFORE}},
                       {'after': '0' * 40, 'head_commit': None}, {'deleted': True}):
            with self.subTest(change=change):
                self.assert_input_error(lambda: fact(**change))
        body = encode({k: v for k, v in payload().items() if k != 'forced'})
        self.assert_input_error(lambda: push.normalize_push(body, headers(body), TARGET, SECRET))
        self.assertTrue(fact(deleted=True, after='0' * 40, head_commit=None)['deleted'])
        self.assertTrue(fact(created=True, before='0' * 40)['created'])


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='repocare-push-')
        self.root = Path(self.temp.name)
        self.binding = {'monitor': {'id': TARGET['monitor_id'], 'branch': 'main',
                                   'github_url': 'https://github.com/' + TARGET['repository']},
                        'provider': {'request_budget': 12}}
        self.registry = UIRegistry(self.root, self.binding)
        self.registry.put('event', 'existing-event', {'id': 'existing-event', 'status': 'awaiting_approval'})
        self.attempt, _ = self.registry.reserve(2)
        self.baseline = self.original_state()
        self.inbox = push.PushInbox(self.registry, TARGET)

    def tearDown(self):
        self.registry.close()
        self.temp.cleanup()

    def original_state(self):
        with self.registry.transaction() as db:
            return {'budget': db.execute('SELECT * FROM budget').fetchall(),
                    'metadata': db.execute('SELECT body FROM metadata').fetchall(),
                    'objects': db.execute("SELECT kind,id,body FROM objects WHERE kind NOT LIKE 'github_push_%' ORDER BY kind,id").fetchall()}

    def assert_original_unchanged(self):
        self.assertEqual(self.original_state(), self.baseline)

    def test_public_limits_original_registry_and_no_nested_load(self):
        self.assertEqual((push.MAX_DELIVERIES, push.MAX_WORK, push.MAX_JOURNAL_BYTES), (10000, 1000, 32 * 1024 * 1024))
        self.assertIs(self.inbox.registry, self.registry)
        with patch.object(self.registry, 'load', side_effect=AssertionError('nested transaction')):
            receipt = self.inbox.accept(fact())
            self.inbox.pending()
            self.inbox.begin(receipt['work_id'])
            self.inbox.finish(receipt['work_id'], 'event_linked', event_id=receipt['event_id'])
            self.inbox.recover()
            self.inbox.view()
        self.assert_original_unchanged()
        self.assertEqual(list(self.root.glob('**/registry.sqlite3')), [self.registry.path])

    def test_same_delivery_returns_original_receipt_after_finish(self):
        value = fact()
        receipt = self.inbox.accept(value)
        self.assertEqual(set(receipt), {'id', 'status', 'work_id', 'event_id'})
        self.assertEqual(receipt['event_id'], digest(AFTER.encode())[:16])
        self.inbox.begin(receipt['work_id'])
        self.inbox.finish(receipt['work_id'], 'event_linked', event_id=receipt['event_id'])
        self.assertEqual(self.inbox.accept(value), receipt)
        view = self.inbox.view()
        self.assertEqual(view['deliveries'][0]['receipt']['status'], 'received')
        self.assertEqual(view['work'][0]['status'], 'event_linked')
        self.assert_original_unchanged()

    def test_same_delivery_different_raw_payload_conflicts_atomically(self):
        receipt = self.inbox.accept(fact())
        before = self.registry.path.read_bytes()
        changed = fact(created=True)
        with self.assertRaises(push.PushInputError) as caught:
            self.inbox.accept(changed)
        self.assertEqual((caught.exception.code, caught.exception.http_status), ('delivery_conflict', 409))
        self.assertEqual(before, self.registry.path.read_bytes())
        self.assertEqual(self.inbox.view()['deliveries'][0]['receipt'], receipt)

    def test_changed_header_delivery_same_raw_body_one_work(self):
        body = encode(payload())
        one = push.normalize_push(body, headers(body, 'first'), TARGET, SECRET)
        two = push.normalize_push(body, headers(body, 'second'), TARGET, SECRET)
        first, second = self.inbox.accept(one), self.inbox.accept(two)
        self.assertEqual(first['work_id'], second['work_id'])
        self.assertEqual(second['status'], 'duplicate_commit')
        self.assertEqual(len(self.inbox.pending()), 1)
        self.assertEqual(len(self.inbox.view()['deliveries']), 2)
        self.assert_original_unchanged()

    def test_same_commit_different_payloads_and_before_never_new_work(self):
        first = self.inbox.accept(fact('first'))
        second = self.inbox.accept(fact('second', before='c' * 40, created=True))
        self.assertEqual(first['work_id'], second['work_id'])
        self.assertEqual(len(self.inbox.view()['work']), 1)

    def test_ping_forced_deleted_only_notifications(self):
        for value, status in [(fact('ping', event='ping'), 'ping'),
                              (fact('force', forced=True), 'blocked_forced'),
                              (fact('delete', deleted=True, after='0' * 40, head_commit=None), 'blocked_deleted')]:
            self.assertEqual(self.inbox.accept(value), {'id': value['delivery_id'], 'status': status, 'work_id': None, 'event_id': None})
        self.assertEqual(self.inbox.pending(), [])
        self.assertEqual(self.inbox.view()['work'], [])
        self.assert_original_unchanged()

    def test_atomic_concurrent_delivery_and_commit_dedup_and_single_claim(self):
        values = [fact('same' if i % 2 == 0 else f'delivery-{i}') for i in range(24)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(self.inbox.accept, values))
        view = self.inbox.view()
        self.assertEqual((len(view['deliveries']), len(view['work'])), (13, 1))
        self.assertEqual(len({r['work_id'] for r in receipts}), 1)
        work_id = receipts[0]['work_id']
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(self.inbox.begin, [work_id] * 24))
        self.assertEqual(sum(value is not None for value in claims), 1)
        self.assertEqual(self.inbox.pending(), [])
        self.assert_original_unchanged()

    def test_concurrent_conflicting_delivery_exactly_one_winner(self):
        values = [fact(after=c * 40, head_commit={'id': c * 40}) for c in ('c', 'd')]
        def accept(value):
            try:
                return self.inbox.accept(value)['status']
            except push.PushInputError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(accept, values))
        self.assertCountEqual(results, ['received', 'delivery_conflict'])
        self.assertEqual(len(self.inbox.view()['work']), 1)

    def test_constructor_does_not_recover_running_dispatch(self):
        receipt = self.inbox.accept(fact())
        claimed = self.inbox.begin(receipt['work_id'])
        second = push.PushInbox(self.registry, TARGET)
        self.assertEqual(second.view()['work'], [claimed])
        self.assertIsNone(second.begin(receipt['work_id']))
        self.assert_original_unchanged()

    def test_restart_recovery_never_replays_intent_but_preserves_received(self):
        claimed = self.inbox.accept(fact('claimed'))
        pending = self.inbox.accept(fact('pending', after='c' * 40, head_commit={'id': 'c' * 40}))
        self.inbox.begin(claimed['work_id'])
        self.registry.close()
        self.registry = UIRegistry(self.root, self.binding)
        inbox = push.PushInbox(self.registry, TARGET)
        self.assertEqual(inbox.view()['work'][0]['status'], 'dispatch_intent')
        recovered = inbox.recover()
        self.assertEqual([(r['id'], r['status']) for r in recovered], [(claimed['work_id'], 'interrupted_no_replay')])
        self.assertEqual([r['id'] for r in inbox.pending()], [pending['work_id']])
        self.assertIsNone(inbox.begin(claimed['work_id']))
        self.assertEqual(inbox.accept(fact('later'))['status'], 'duplicate_commit')
        self.assertEqual(inbox.recover(), [])
        with self.assertRaises(SafetyError):
            inbox.finish(claimed['work_id'], 'failed')
        self.assertEqual(self.registry.budget()['requests_reserved_unknown'], 2)
        self.assertEqual(self.registry.load('attempt')[self.attempt]['status'], 'unknown')

    def test_finish_requires_claim_exact_event_and_terminal_idempotency(self):
        for status in sorted(push.FINISH_STATUSES):
            with self.subTest(status=status):
                value = fact(status, after=digest(status.encode())[:40], head_commit=None)
                receipt = self.inbox.accept(value)
                with self.assertRaises(SafetyError):
                    self.inbox.finish(receipt['work_id'], status, event_id=receipt['event_id'])
                self.inbox.begin(receipt['work_id'])
                with self.assertRaises(SafetyError):
                    self.inbox.finish(receipt['work_id'], status, event_id='0' * 16)
                if status == 'event_linked':
                    with self.assertRaises(SafetyError):
                        self.inbox.finish(receipt['work_id'], status)
                row = self.inbox.finish(receipt['work_id'], status, event_id=receipt['event_id'], message='Done')
                self.assertEqual(self.inbox.finish(receipt['work_id'], status, event_id=receipt['event_id'], message='Done'), row)
                with self.assertRaises(SafetyError):
                    self.inbox.finish(receipt['work_id'], status, message='different')
                with self.assertRaises(SafetyError):
                    self.inbox.finish(receipt['work_id'], 'failed' if status != 'failed' else 'blocked')
        self.assert_original_unchanged()

    def test_finish_fixed_status_message_sanitized(self):
        receipt = self.inbox.accept(fact())
        self.inbox.begin(receipt['work_id'])
        for status, message in [('completed', ''), ('interrupted_no_replay', ''), ([], ''), ('failed', '\n'), ('failed', 'x' * 513)]:
            with self.assertRaises(SafetyError):
                self.inbox.finish(receipt['work_id'], status, message=message)
        result = self.inbox.finish(receipt['work_id'], 'failed', message='Blocked https://example.invalid/credential')
        self.assertEqual(result['message'], 'Blocked [URL]')

    def test_read_only_views_are_copies_and_leave_database_bytes_unchanged(self):
        self.inbox.accept(fact())
        before = self.registry.path.read_bytes()
        view = self.inbox.view()
        pending = self.inbox.pending()
        view['work'][0]['status'] = 'received-again'
        view['deliveries'][0]['fact']['after'] = 'd' * 40
        pending[0]['after'] = 'e' * 40
        self.assertEqual(self.registry.path.read_bytes(), before)
        self.assertEqual(self.inbox.view()['work'][0]['after'], AFTER)
        self.assert_original_unchanged()

    def test_target_mapping_cannot_change_numeric_repo_or_monitor_scope(self):
        for target in (dict(TARGET, repository_id=999), dict(TARGET, branch='repair'),
                       dict(TARGET, monitor_id='c' * 16), dict(TARGET, repository='other/repo')):
            with self.subTest(target=target), self.assertRaises(SafetyError):
                push.PushInbox(self.registry, target)
        self.assert_original_unchanged()
        with self.assertRaises(SafetyError):
            push.PushInbox(object(), TARGET)

    def test_delivery_and_work_caps_refuse_new_but_existing_delivery_first(self):
        receipt = self.inbox.accept(fact())
        with patch.object(push, 'MAX_DELIVERIES', 1):
            self.assertEqual(self.inbox.accept(fact()), receipt)
            with self.assertRaises(push.PushInputError) as caught:
                self.inbox.accept(fact('new'))
            self.assertEqual((caught.exception.code, caught.exception.http_status), ('inbox_full', 503))
        with patch.object(push, 'MAX_WORK', 1):
            self.assertEqual(self.inbox.accept(fact('duplicate'))['status'], 'duplicate_commit')
            with self.assertRaises(push.PushInputError):
                self.inbox.accept(fact('new-sha', after='c' * 40, head_commit=None))
        self.assertEqual(len(self.inbox.view()['work']), 1)
        self.assert_original_unchanged()

    def test_journal_size_cap_rolls_back_new_and_preserves_existing(self):
        receipt = self.inbox.accept(fact())
        before = self.registry.path.read_bytes()
        with patch.object(push, 'MAX_JOURNAL_BYTES', 1):
            self.assertEqual(self.inbox.accept(fact()), receipt)
            with self.assertRaises(push.PushInputError) as caught:
                self.inbox.accept(fact('new', after='c' * 40, head_commit=None))
            self.assertEqual((caught.exception.code, caught.exception.http_status), ('inbox_full', 503))
        self.assertEqual(before, self.registry.path.read_bytes())
        self.assertEqual(len(self.inbox.view()['deliveries']), 1)

    def test_transaction_failure_leaves_no_orphan_work_or_new_request(self):
        original = self.registry.put
        def fail_delivery(kind, identity, value, db=None):
            if kind == push.DELIVERIES:
                raise SafetyError('synthetic persistence interruption')
            return original(kind, identity, value, db)
        before = self.registry.path.read_bytes()
        with patch.object(self.registry, 'put', side_effect=fail_delivery), self.assertRaises(SafetyError):
            self.inbox.accept(fact())
        self.assertEqual(before, self.registry.path.read_bytes())
        self.assertEqual(self.inbox.view(), {'deliveries': [], 'work': []})
        self.assert_original_unchanged()

    def test_bad_persisted_facts_fail_closed_instead_of_recreating_work(self):
        receipt = self.inbox.accept(fact())
        for body in ('null', '[]', '{"schema_version":1}', '{"id":1,"id":2}'):
            with self.subTest(body=body):
                with self.registry.transaction() as db:
                    saved = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (push.WORK, receipt['work_id'])).fetchone()[0]
                    db.execute('UPDATE objects SET body=? WHERE kind=? AND id=?', (body, push.WORK, receipt['work_id']))
                with self.assertRaises(SafetyError):
                    self.inbox.accept(fact('changed-header'))
                with self.registry.transaction() as db:
                    db.execute('UPDATE objects SET body=? WHERE kind=? AND id=?', (saved, push.WORK, receipt['work_id']))
        self.assertEqual(len(self.inbox.view()['deliveries']), 1)

    def test_invalid_public_fact_cannot_enter_journal(self):
        for value in (dict(fact(), event=[]), dict(fact(), repository_id=True), dict(fact(), extra='untrusted'),
                      dict(fact(), ref='refs/heads/other'), dict(fact(), payload_sha256='bad')):
            with self.assertRaises(push.PushInputError):
                self.inbox.accept(value)
        self.assertEqual(self.inbox.view(), {'deliveries': [], 'work': []})
        self.assert_original_unchanged()

    def test_changed_target_record_and_receipt_fail_closed(self):
        receipt = self.inbox.accept(fact())
        for kind, identity, body in ((push.TARGET, TARGET['monitor_id'], 'null'),
                                     (push.TARGET, TARGET['monitor_id'], json.dumps({'schema_version': True, 'target': TARGET})),
                                     (push.DELIVERIES, receipt['id'], '[]'),
                                     (push.DELIVERIES, receipt['id'], json.dumps({'schema_version': 1, 'id': receipt['id'],
                                         'fact': fact(), 'receipt': dict(receipt, work_id='other'), 'received_at': 'synthetic'}))):
            with self.subTest(kind=kind, body=body):
                with self.registry.transaction() as db:
                    saved = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (kind, identity)).fetchone()[0]
                    db.execute('UPDATE objects SET body=? WHERE kind=? AND id=?', (body, kind, identity))
                with self.assertRaises(SafetyError):
                    self.inbox.accept(fact())
                with self.registry.transaction() as db:
                    db.execute('UPDATE objects SET body=? WHERE kind=? AND id=?', (saved, kind, identity))
        self.assert_original_unchanged()


if __name__ == '__main__':
    unittest.main()
