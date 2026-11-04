import io
import hashlib
import http.client
import http.server
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from repo_maintainer.model_profiles import ModelProfiles
from repo_maintainer.models import SafetyError
from repo_maintainer.provider_config import load_provider_config, resolve_model_reasoning, validate_provider_config
from repo_maintainer.provider_models import discover_provider_models, MAX_MODEL_RESPONSE_BYTES


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status, self.headers = status, headers or {}

    def getheader(self, key, default=None):
        return self.headers.get(key, default)


class ProviderModelDiscoveryTests(unittest.TestCase):
    key = 'synthetic-private-directory-credential'
    request = {'base_url': 'https://provider.invalid/relay/v1/', 'api_key': key}

    def invoke(self, data=None, *, raw=None, status=200, headers=None):
        response = Response(raw if raw is not None else json.dumps(data).encode(), status, headers)
        with patch('repo_maintainer.provider_models.http.client.HTTPSConnection') as connection:
            client = connection.return_value
            client.sock = None
            client.getresponse.return_value = response
            value = discover_provider_models(self.request)
            return value, connection, client

    def test_one_same_origin_request_and_optional_official_metadata(self):
        result, constructor, connection = self.invoke({'data': [
            {'id': 'deepseek-flash', 'name': 'DeepSeek V4.1 Flash', 'context_window': 1048576,
             'max_output_tokens': 393216, 'effort': {'supported_levels': ['low', 'high', 'max']}},
            {'id': 'relay/basic'}]})
        self.assertEqual('https://provider.invalid/relay/v1/models', result['models_url'])
        constructor.assert_called_once()
        self.assertEqual(('provider.invalid',), constructor.call_args.args)
        connection.request.assert_called_once_with('GET', '/relay/v1/models', headers={
            'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json', 'Accept-Encoding': 'identity'})
        self.assertEqual(393216, result['models'][0]['max_output_tokens'])
        self.assertEqual(['low', 'high', 'max'], result['models'][0]['reasoning_efforts'])
        self.assertEqual({'id': 'relay/basic', 'name': 'relay/basic', 'context_window_tokens': None,
                          'max_output_tokens': None, 'reasoning_efforts': []}, result['models'][1])
        self.assertNotIn(self.key, json.dumps(result))
        connection.close.assert_called()

    def test_redirect_is_not_followed_even_same_origin_and_no_payload_error_echo(self):
        for status in (301, 302, 307, 308, 401, 404, 429, 500):
            with self.subTest(status=status), patch('repo_maintainer.provider_models.http.client.HTTPSConnection') as connection:
                connection.return_value.sock = None
                connection.return_value.getresponse.return_value = Response(self.key.encode(), status,
                    {'Location': 'https://other.invalid/steal'})
                with self.assertRaises(SafetyError) as caught:
                    discover_provider_models(self.request)
                self.assertNotIn(self.key, str(caught.exception))
                connection.assert_called_once()
                connection.return_value.request.assert_called_once()
                connection.return_value.close.assert_called()

    def test_bad_requests_stop_before_transport(self):
        for value in ({**self.request, 'profile_id': 'x'}, {'base_url': self.request['base_url']},
                      {**self.request, 'base_url': 'http://provider.invalid'},
                      {**self.request, 'base_url': 'https://user:password@provider.invalid'},
                      {**self.request, 'base_url': 'https://provider.invalid?key=secret'}):
            with self.subTest(value_kind=list(value)), patch('repo_maintainer.provider_models.http.client.HTTPSConnection') as connection:
                with self.assertRaises(SafetyError):
                    discover_provider_models(value)
                connection.assert_not_called()

    def test_response_size_encoding_duplicates_and_ids_are_bounded(self):
        bad = [dict(raw=b'x' * (MAX_MODEL_RESPONSE_BYTES + 1)),
               dict(raw=b'{}', headers={'Content-Length': str(MAX_MODEL_RESPONSE_BYTES + 1)}),
               dict(raw=b'{}', headers={'Content-Encoding': 'gzip'}),
               dict(raw=b'{"data":[],"data":[]}'), dict(data={'data': [{'id': 'has space'}]}),
               dict(data={'data': [{'id': 'model'}, {'id': 'model'}]}),
               dict(data={'data': [{'id': str(n)} for n in range(1001)]}),
               dict(data={'data': [{'id': self.key}]}),
               dict(raw=('{"data":[{"id":"safe","name":"' + self.key.replace('s', '\\u0073') + '"}]}').encode())]
        for item in bad:
            with self.subTest(kind=list(item)), self.assertRaises(SafetyError) as caught:
                self.invoke(**item)
            self.assertNotIn(self.key, str(caught.exception))

    def test_invalid_optional_metadata_stays_unknown_and_never_guesses_from_id(self):
        result, _, _ = self.invoke({'data': [{'id': 'deepseek-fake', 'context_window': True,
            'max_output_tokens': 9007199254740992, 'effort': {'supported_levels': ['high', 'high']}}]})
        row = result['models'][0]
        self.assertIsNone(row['context_window_tokens'])
        self.assertIsNone(row['max_output_tokens'])
        self.assertEqual([], row['reasoning_efforts'])

    def test_transport_failure_is_redacted_and_never_retried(self):
        with patch('repo_maintainer.provider_models.http.client.HTTPSConnection') as connection:
            connection.return_value.sock = None
            connection.return_value.request.side_effect = OSError(self.key)
            with self.assertRaises(SafetyError) as caught:
                discover_provider_models(self.request)
            self.assertNotIn(self.key, str(caught.exception))
            connection.return_value.request.assert_called_once()

    def test_total_deadline_interrupts_a_trickling_or_blocked_body(self):
        expired = threading.Event()
        class BlockedResponse(Response):
            def read1(self, _size):
                expired.wait(1)
                raise OSError('synthetic socket stopped')
        with patch('repo_maintainer.provider_models.http.client.HTTPSConnection') as connection, \
                patch('repo_maintainer.provider_models.MODEL_DISCOVERY_TIMEOUT_SECONDS', 0.03):
            connection.return_value.close.side_effect = expired.set
            connection.return_value.getresponse.return_value = BlockedResponse(b'')
            before = time.monotonic()
            with self.assertRaises(SafetyError):
                discover_provider_models(self.request)
            self.assertLess(time.monotonic() - before, 0.7)
            connection.return_value.sock.shutdown.assert_called_once()

    def test_connection_close_socket_keeps_one_overall_deadline_after_trickled_body(self):
        # HTTPSConnection inherits the exact HTTPConnection response/socket handoff.
        # Patch only TLS construction; all request/response reads use real loopback sockets.
        for protocol in ('HTTP/1.0', 'HTTP/1.1'):
            with self.subTest(protocol=protocol):
                stop = threading.Event()
                requests = []
                sockets = []
                class Handler(http.server.BaseHTTPRequestHandler):
                    protocol_version = protocol
                    def do_GET(self):
                        requests.append((self.path, self.headers.get('Authorization')))
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', '200')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        if not stop.wait(0.45):
                            try:
                                self.wfile.write(b'{')
                                self.wfile.flush()
                            except OSError:
                                pass
                        stop.wait(2)
                    def log_message(self, *_args):
                        pass
                server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                server.daemon_threads = True
                worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
                worker.start()
                def connection(host, port=None, *, timeout, context):
                    client = http.client.HTTPConnection(host, port=port, timeout=timeout)
                    getresponse = client.getresponse
                    def response():
                        original_socket = client.sock
                        result = getresponse()
                        self.assertIsNone(client.sock, 'Connection:close must exercise socket handoff')
                        sockets.append(original_socket)
                        return result
                    client.getresponse = response
                    return client
                try:
                    with patch('repo_maintainer.provider_models.http.client.HTTPSConnection', side_effect=connection), \
                            patch('repo_maintainer.provider_models.MODEL_DISCOVERY_TIMEOUT_SECONDS', 0.6):
                        before = time.monotonic()
                        with self.assertRaises(SafetyError):
                            discover_provider_models({'base_url': f'https://127.0.0.1:{server.server_port}/v1',
                                                      'api_key': self.key})
                        elapsed = time.monotonic() - before
                    self.assertLess(elapsed, 0.9, f'Overall 0.6s deadline was renewed: {elapsed:.3f}s')
                    self.assertEqual([('/v1/models', 'Bearer ' + self.key)], requests)
                    self.assertEqual(1, len(sockets))
                finally:
                    stop.set()
                    server.shutdown()
                    server.server_close()
                    worker.join(timeout=2)

    def test_json_escaped_credential_echo_is_rejected_in_all_decoded_fields_and_keys(self):
        key = 'synthetic\\directory"key'
        request = {**self.request, 'api_key': key}
        cases = [{'data': [{'id': 'safe', 'name': key}]},
                 {'data': [{'id': 'safe', 'ignored_metadata': {'nested': [key]}}]},
                 {'data': [{'id': 'safe', 'ignored_metadata': {key: 'innocent'}}]},
                 {'data': [{'id': 'safe'}], 'ignored_top_level': key}]
        for value in cases:
            raw = json.dumps(value).encode()
            self.assertNotIn(key.encode(), raw, 'Fixture must exercise JSON escaping')
            with self.subTest(case=value), patch('repo_maintainer.provider_models.http.client.HTTPSConnection') as connection:
                connection.return_value.sock = None
                connection.return_value.getresponse.return_value = Response(raw)
                with self.assertRaises(SafetyError) as caught:
                    discover_provider_models(request)
                self.assertNotIn(key, str(caught.exception))
                connection.return_value.request.assert_called_once()


class ProviderReasoningProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hub = SimpleNamespace(root=self.root, provider_config=self.root / 'original.env',
            lock=threading.RLock(), closed=False)
        self.store = ModelProfiles(self.hub)
        self.base = {'PI_MAINTENANCE_API_KEY': 'synthetic-profile-credential',
            'PI_MAINTENANCE_BASE_URL': 'https://provider.invalid/v1', 'PI_MAINTENANCE_MODEL': 'exact-api-id'}

    def create(self, **values):
        return self.store.create({'action': 'create', 'revision': self.store.view()['revision'],
            'name': 'Human Display Name', 'base_url': self.base['PI_MAINTENANCE_BASE_URL'],
            'model': self.base['PI_MAINTENANCE_MODEL'], 'api_key': self.base['PI_MAINTENANCE_API_KEY'], **values})

    def test_legacy_bytes_and_fingerprint_never_gain_new_fields(self):
        raw = ''.join(f'{key}="{value}"\n' for key, value in self.base.items()).encode()
        self.hub.provider_config.write_bytes(raw)
        original = self.store.resolve('legacy')
        self.assertIsNone(original['reasoning_protocol'])
        self.assertFalse(original['reasoning_explicit'])
        self.create(reasoning_protocol='deepseek', reasoning_effort='max')
        self.assertEqual(raw, self.hub.provider_config.read_bytes())
        self.assertEqual(original, ModelProfiles(self.hub).resolve('legacy'))
        self.assertEqual(set(self.base), set(load_provider_config(self.hub.provider_config)))

    def test_explicit_reasoning_reopens_and_does_not_leak_key(self):
        created = self.create(reasoning_protocol='deepseek', reasoning_effort='max')
        row = ModelProfiles(self.hub).resolve(created['created_profile_id'])
        self.assertEqual(('deepseek', 'max', True), tuple(row[k] for k in
            ('reasoning_protocol', 'reasoning_effort', 'reasoning_explicit')))
        config = load_provider_config(row['path'])
        self.assertEqual('max', config['PI_MAINTENANCE_REASONING_EFFORT'])
        self.assertNotIn(self.base['PI_MAINTENANCE_API_KEY'], json.dumps(created))

    def test_only_explicit_paired_supported_protocols_and_efforts_are_accepted(self):
        for extra in ({'PI_MAINTENANCE_REASONING_EFFORT': 'high'},
                      {'PI_MAINTENANCE_REASONING_PROTOCOL': 'deepseek'},
                      {'PI_MAINTENANCE_REASONING_PROTOCOL': 'guess', 'PI_MAINTENANCE_REASONING_EFFORT': 'high'},
                      {'PI_MAINTENANCE_REASONING_PROTOCOL': 'none', 'PI_MAINTENANCE_REASONING_EFFORT': 'high'},
                      {'PI_MAINTENANCE_REASONING_PROTOCOL': 'deepseek', 'PI_MAINTENANCE_REASONING_EFFORT': 'medium'}):
            with self.subTest(extra=extra), self.assertRaises(SafetyError):
                validate_provider_config({**self.base, **extra})
        self.assertEqual({'reasoning_protocol': None, 'reasoning_effort': None, 'reasoning_explicit': False, 'reasoning_levels': None},
                         resolve_model_reasoning(self.base))

    def test_source_profile_key_reuse_is_explicit_exact_origin_and_append_only(self):
        first = self.create()
        original = self.store.resolve(first['created_profile_id'])
        raw = original['path'].read_bytes()
        second = self.create(source_profile_id=original['id'], api_key='', model='second-exact-id')
        self.assertNotEqual(original['id'], second['created_profile_id'])
        self.assertEqual(raw, original['path'].read_bytes())
        config = load_provider_config(self.store.resolve(second['created_profile_id'])['path'])
        self.assertEqual(self.base['PI_MAINTENANCE_API_KEY'], config['PI_MAINTENANCE_API_KEY'])
        for base in ('https://other.invalid/v1', 'https://provider.invalid/v1/'):
            with self.subTest(base=base), self.assertRaises(SafetyError):
                self.create(source_profile_id=original['id'], api_key='', base_url=base, model='third-id')
        with self.assertRaises(SafetyError):
            self.create(api_key='', model='no-source')

    def test_model_id_forbids_whitespace_while_human_name_can_have_spaces(self):
        self.create(name='Allowed Human Display Name')
        with self.assertRaises(SafetyError):
            self.create(model='Not an API ID')

    def test_reasoning_variant_reuses_key_and_limits_without_touching_source_or_default(self):
        initial = self.create(reasoning_protocol='deepseek', reasoning_effort='high',
                              context_window_tokens=4000000, max_output_tokens=1500000)
        source = self.store.resolve(initial['created_profile_id'])
        raw = source['path'].read_bytes()
        variant = self.store.reasoning_variant({'action': 'reasoning_variant', 'revision': initial['revision'],
            'profile_id': source['id'], 'reasoning_effort': 'max'})
        row = self.store.resolve(variant['created_profile_id'])
        self.assertNotEqual(source['id'], row['id'])
        self.assertEqual('max', row['reasoning_effort'])
        self.assertEqual((4000000, 1500000), (row['context_window_tokens'], row['max_output_tokens']))
        self.assertEqual(source['id'], variant['default_profile_id'])
        self.assertEqual(raw, source['path'].read_bytes())
        self.assertNotIn(self.base['PI_MAINTENANCE_API_KEY'], json.dumps(variant))
        count = len(list(self.store.root.glob('*.env')))
        repeated = self.store.reasoning_variant({'action': 'reasoning_variant', 'revision': variant['revision'],
            'profile_id': source['id'], 'reasoning_effort': 'max'})
        self.assertEqual(row['id'], repeated['created_profile_id'])
        self.assertEqual(variant['revision'], repeated['revision'])
        self.assertEqual(count, len(list(self.store.root.glob('*.env'))))
        restored = self.store.reasoning_variant({'action': 'reasoning_variant', 'revision': repeated['revision'],
            'profile_id': row['id'], 'reasoning_effort': 'high'})
        self.assertEqual(source['id'], restored['created_profile_id'])

    def test_reasoning_variant_refuses_unknown_protocol_invalid_effort_and_stale_revision(self):
        original = self.create()
        with self.assertRaises(SafetyError):
            self.store.reasoning_variant({'action': 'reasoning_variant', 'revision': original['revision'],
                'profile_id': original['created_profile_id'], 'reasoning_effort': 'max'})
        explicit = self.create(reasoning_protocol='deepseek', reasoning_effort='high')
        for effort, revision in (('medium', explicit['revision']), ('max', original['revision'])):
            with self.subTest(effort=effort), self.assertRaises(SafetyError):
                self.store.reasoning_variant({'action': 'reasoning_variant', 'revision': revision,
                    'profile_id': explicit['created_profile_id'], 'reasoning_effort': effort})

    def test_explicit_level_table_preserves_mapping_and_deleted_levels_across_variants(self):
        levels = [{'level': 'off', 'api_value': None}, {'level': 'minimal', 'api_value': 'low'},
                  {'level': 'xhigh', 'api_value': 'high'}]
        first = self.create(reasoning_protocol='deepseek', reasoning_effort='minimal', reasoning_levels=levels)
        source = self.store.resolve(first['created_profile_id'])
        self.assertEqual(levels, source['reasoning_levels'])
        original = source['path'].read_bytes()
        result = self.store.reasoning_variant({'action': 'reasoning_variant', 'revision': first['revision'],
            'profile_id': source['id'], 'reasoning_effort': 'xhigh'})
        variant = self.store.resolve(result['created_profile_id'])
        self.assertEqual(levels, variant['reasoning_levels'])
        self.assertEqual(original, source['path'].read_bytes())
        self.assertEqual(source['id'], result['default_profile_id'])
        for missing in ('medium', 'max'):
            with self.subTest(missing=missing), self.assertRaises(SafetyError):
                self.store.reasoning_variant({'action': 'reasoning_variant', 'revision': result['revision'],
                    'profile_id': variant['id'], 'reasoning_effort': missing})

    def test_level_table_requires_unique_canonical_members_and_explicit_off_semantics(self):
        for protocol, effort, levels in (
                ('deepseek', 'high', []), ('deepseek', 'high', [{'level': 'high', 'api_value': 'high'}] * 2),
                ('deepseek', 'high', [{'level': 'custom-level', 'api_value': 'high'}]),
                ('deepseek', 'high', [{'level': 'high', 'api_value': 'with space'}]),
                ('deepseek', 'off', [{'level': 'off', 'api_value': 'none'}]),
                ('openai', 'off', [{'level': 'off', 'api_value': 'low'}]),
                ('none', 'high', [{'level': 'high', 'api_value': None}]),
                ('deepseek', 'medium', [{'level': 'high', 'api_value': 'high'}])):
            with self.subTest(protocol=protocol, effort=effort), self.assertRaises(SafetyError):
                self.create(reasoning_protocol=protocol, reasoning_effort=effort, reasoning_levels=levels)
        for api_value in (None, 'none'):
            row = self.create(reasoning_protocol='openai', reasoning_effort='off',
                reasoning_levels=[{'level': 'off', 'api_value': api_value}])
            self.assertEqual(api_value, self.store.resolve(row['created_profile_id'])['reasoning_levels'][0]['api_value'])

    def test_mapping_object_key_order_reuses_identity_without_changing_old_bytes_or_sha(self):
        first_levels = [{'level': 'off', 'api_value': None}, {'level': 'high', 'api_value': 'high'}]
        first = self.create(reasoning_protocol='deepseek', reasoning_effort='high', reasoning_levels=first_levels)
        source = self.store.resolve(first['created_profile_id'])
        original_bytes = source['path'].read_bytes()
        original_sha = hashlib.sha256(original_bytes).hexdigest()
        catalog_bytes = self.store.catalog_path.read_bytes()
        permuted = [{'api_value': None, 'level': 'off'}, {'api_value': 'high', 'level': 'high'}]
        repeated = self.create(reasoning_protocol='deepseek', reasoning_effort='high', reasoning_levels=permuted,
                               name='Different human caption must not mint another allowance')
        self.assertEqual(source['id'], repeated['created_profile_id'])
        self.assertEqual(first['revision'], repeated['revision'])
        self.assertEqual(1, len(list(self.store.root.glob('*.env'))))
        self.assertEqual(original_bytes, source['path'].read_bytes())
        self.assertEqual(original_sha, self.store.resolve(source['id'])['config_sha256'])
        self.assertEqual(catalog_bytes, self.store.catalog_path.read_bytes())
        reordered = self.create(reasoning_protocol='deepseek', reasoning_effort='high', reasoning_levels=list(reversed(permuted)))
        self.assertNotEqual(source['id'], reordered['created_profile_id'], 'Array order is meaningful picker order')
        self.assertEqual(original_bytes, source['path'].read_bytes())


if __name__ == '__main__':
    unittest.main()
