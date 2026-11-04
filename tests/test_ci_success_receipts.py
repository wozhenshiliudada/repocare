"""Original CI reader + real pinned files; all CI HTTP data is synthetic."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from repo_maintainer.ci_evidence import CiEvidenceReader
from repo_maintainer.ci_transport import CiJsonPage
from repo_maintainer.ci_success_baseline import (Incomparable, OriginalCiReceiptSource,
    compare_success, investigate_success, original_ci_binding)
from repo_maintainer.issue_intake import fingerprint
from test_ci_success_baseline import observation, seal


class MetadataTransport:
    def __init__(self, values):
        self.pages, self.calls = {}, []
        for value in values:
            repo = {'id': 77, 'full_name': value['repository']}
            run, job = value['run'], value['job']
            prefix = '/repos/'+value['repository']
            path = prefix+f"/actions/runs/{run['id']}/attempts/{run['attempt']}"
            self.pages[prefix] = repo
            self.pages[path] = {'id': run['id'], 'run_attempt': run['attempt'], 'repository': repo,
                'workflow_id': value['workflow']['id'], 'head_sha': value['sha'],
                'status': run['status'], 'conclusion': run['conclusion']}
            self.pages[path+'/jobs?per_page=100&page=1'] = {'total_count': 1, 'jobs': [
                {'id': job['id'], 'run_id': run['id'], 'run_attempt': run['attempt'],
                 'head_sha': value['sha'], 'name': job['name'], 'status': job['status'],
                 'conclusion': job['conclusion'], 'steps': [dict(number=1, name='original checks',
                    status='completed', conclusion=job['conclusion'])]}]}

    def get_json(self, path, budget):
        self.calls.append(path)
        return CiJsonPage(copy.deepcopy(self.pages[path]))

    def get_log(self, *args):
        raise AssertionError('Receipt hydration must not download logs or execute tests')


def discovered(value):
    return dict(id=value['run']['id'], run_attempt=value['run']['attempt'], head_sha=value['sha'],
                head_branch=value['branch'], workflow_id=value['workflow']['id'], event=value['run']['event'],
                status=value['run']['status'], conclusion=value['run']['conclusion'],
                repository={'id': 77, 'full_name': value['repository']})


class ReceiptSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current, self.history = observation(False), observation(True)
        self.transport = MetadataTransport([self.current, self.history])
        self.reader = CiEvidenceReader(self.transport, synthetic=True)
        self.pins = {}
        self.write_receipt(self.current)
        self.write_receipt(self.history)
        self.source = self.source_with()
        self.transport.calls.clear()

    def source_with(self, **changes):
        scope = dict(repository='example/project', branch='main', workflow_id=1,
                     job_name=self.current['job']['name'])
        scope.update(changes)
        return OriginalCiReceiptSource(self.root, self.pins, self.reader.observe, **scope)

    def filename(self, value):
        return f"{value['run']['id']}-{value['run']['attempt']}-{value['job']['id']}.json"

    def write_receipt(self, value, change=None):
        packet = self.reader.observe('https://github.com/'+value['repository'],
                                     value['run']['id'], value['run']['attempt'])
        receipt = {'schema_version': 1, 'kind': 'repocare-original-ci-job', 'repository_id': 77,
                   'observation_sha256': fingerprint(original_ci_binding(packet)), 'snapshot': copy.deepcopy(value)}
        if change:
            change(receipt)
        raw = (json.dumps(receipt, sort_keys=True)+'\n').encode()
        name = self.filename(value)
        (self.root/name).write_bytes(raw)
        self.pins[name] = hashlib.sha256(raw).hexdigest()

    def test_original_reader_and_pinned_files_hydrate_matching_history(self):
        current = self.source.for_run(discovered(self.current), success=False)
        history = self.source.hydrate(discovered(self.history))
        def compare(repo, base, head):
            return {'base_commit': {'sha': base}, 'merge_base_commit': {'sha': base},
                    'status': 'ahead', 'total_commits': 1, 'commits': [{'sha': head}],
                    'files': [{'filename': 'src/app.py', 'status': 'modified', 'patch': '-old\n+new'}]}
        result = compare_success(current, history, compare)
        self.assertEqual(result['status'], 'comparable', result)
        self.assertTrue(result['baseline']['synthetic'])
        self.assertEqual(result['baseline']['original_receipt_proof']['sha256'], self.pins[self.filename(self.history)])
        self.assertEqual(current['original_receipt_proof']['sha256'], self.pins[self.filename(self.current)])
        self.assertEqual(len(self.transport.calls), 6)  # Only repo/run/attempt-jobs GETs for each run.
        self.assertEqual(history[0]['environment'], self.history['environment'])

    def test_investigation_can_use_concrete_hydrator_and_preserves_missing_receipt_reason(self):
        current = self.source.for_run(discovered(self.current), success=False)
        source = self.source_with()
        class Remote:
            def successful_runs(inner, *args):
                return {'runs': [discovered(self.history)], 'complete': True}
            def compare(inner, *args):
                raise AssertionError('No compare without the approved receipt')
        (self.root/self.filename(self.history)).unlink()
        result = investigate_success(current, Remote(), source.hydrate)
        self.assertEqual(result['status'], 'unconfirmed')
        self.assertIn('original_ci_receipt_missing', [r['reason'] for r in result['rejections']])

    def test_present_but_unapproved_receipt_is_not_automatically_imported(self):
        source = OriginalCiReceiptSource(self.root, {}, self.reader.observe,
            repository='example/project', branch='main', workflow_id=1, job_name=self.current['job']['name'])
        before = sorted(p.name for p in self.root.iterdir())
        with self.assertRaisesRegex(Incomparable, 'original_ci_receipt_not_approved'):
            source.hydrate(discovered(self.history))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)

    def test_changed_receipt_bytes_rejected_before_deserialization(self):
        path = self.root/self.filename(self.history)
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(Incomparable, 'original_ci_receipt_changed'):
            self.source.hydrate(discovered(self.history))

    def test_hash_pin_does_not_replace_original_job_membership(self):
        def change(receipt):
            receipt['snapshot']['job']['id'] = 999
            seal(receipt['snapshot'])
        self.write_receipt(self.history, change)
        source = self.source_with()
        with self.assertRaisesRegex(Incomparable, 'original_ci_receipt_scope_changed'):
            source.hydrate(discovered(self.history))

    def test_missing_actual_environment_is_not_filled_from_host(self):
        def change(receipt):
            receipt['snapshot']['environment'] = {}
            seal(receipt['snapshot'])
        self.write_receipt(self.history, change)
        with self.assertRaisesRegex(Incomparable, 'missing_actual_environment'):
            self.source_with().hydrate(discovered(self.history))

    def test_original_observation_digest_and_repository_id_must_match(self):
        for key, value in [('observation_sha256', 'f'*64), ('repository_id', 88)]:
            with self.subTest(key=key):
                self.write_receipt(self.history, lambda r: r.update({key: value}))
                with self.assertRaisesRegex(Incomparable, 'original_ci_receipt_observation_changed'):
                    self.source_with().hydrate(discovered(self.history))

    def test_wrong_branch_attempt_or_repository_stops_before_reader(self):
        for field, value in [('head_branch', 'another'), ('run_attempt', 0),
                             ('repository', {'id': 77, 'full_name': 'another/repo'})]:
            with self.subTest(field=field):
                run = discovered(self.history)
                run[field] = value
                with self.assertRaisesRegex(Incomparable, 'original_ci_discovery_scope_changed'):
                    self.source.hydrate(run)
        self.assertEqual(self.transport.calls, [])

    def test_no_executed_original_steps_cannot_be_replaced_by_claimed_checks(self):
        for path, response in self.transport.pages.items():
            if '/runs/10/' in path and '/jobs?' in path:
                response['jobs'][0]['steps'] = []
        with self.assertRaisesRegex(Incomparable, 'original_ci_job_has_no_executed_steps'):
            self.source.hydrate(discovered(self.history))

    def test_original_reader_partial_packet_is_not_a_usable_receipt_source(self):
        self.transport.pages['/repos/example/project/actions/runs/10/attempts/1/jobs?per_page=100&page=1']['total_count'] = 2
        with self.assertRaisesRegex(Incomparable, 'original_ci_observation_incomplete'):
            self.source.hydrate(discovered(self.history))

    def test_reader_exception_is_a_fixed_reason_without_raw_secret(self):
        def unavailable(*args):
            raise TimeoutError('signed_url?token=private')
        source = OriginalCiReceiptSource(self.root, self.pins, unavailable,
            repository='example/project', branch='main', workflow_id=1, job_name=self.current['job']['name'])
        with self.assertRaisesRegex(Incomparable, '^original_ci_observation_unavailable$'):
            source.for_run(discovered(self.current), success=False)

    def test_duplicate_matrix_job_name_is_ambiguous(self):
        response = self.transport.pages['/repos/example/project/actions/runs/10/attempts/1/jobs?per_page=100&page=1']
        other = copy.deepcopy(response['jobs'][0])
        other['id'] = 101
        response['jobs'].append(other)
        response['total_count'] = 2
        with self.assertRaisesRegex(Incomparable, 'original_ci_selected_job_missing_or_ambiguous'):
            self.source.hydrate(discovered(self.history))

    def test_pins_are_copied_and_immutable_and_paths_are_fixed_ids(self):
        self.pins.clear()
        self.assertEqual(len(self.source.hydrate(discovered(self.history))), 1)
        with self.assertRaises(TypeError):
            self.source.pins['other'] = 'a'*64
        with self.assertRaisesRegex(Incomparable, 'invalid_original_ci_receipt_pins'):
            OriginalCiReceiptSource(self.root, {'../escape.json': 'a'*64}, self.reader.observe,
                repository='example/project', branch='main', workflow_id=1, job_name='unit')

    def test_synthetic_receipt_cannot_be_compared_with_real_current(self):
        current = self.source.for_run(discovered(self.current), success=False)
        history = self.source.hydrate(discovered(self.history))
        current['synthetic'] = False  # Another caller's actual current observation.
        result = compare_success(current, history, lambda *args: self.fail('must not compare'))
        self.assertEqual(result['status'], 'unconfirmed')
        self.assertIn('different_synthetic', [r['reason'] for r in result['rejections']])

    def test_receipt_cannot_relabel_its_original_synthetic_source(self):
        self.write_receipt(self.history, lambda r: r['snapshot'].update(synthetic=False))
        with self.assertRaisesRegex(Incomparable, 'original_ci_receipt_origin_changed'):
            self.source_with().hydrate(discovered(self.history))


if __name__ == '__main__':
    unittest.main()
