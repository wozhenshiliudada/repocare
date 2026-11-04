"""Real original SQLite journals; no model/network/candidate replay fixtures."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.maintenance_correlation import (
    APPROVAL_KIND, CLAIMS, DISPATCH_KIND, LINKS, OBSERVATIONS, SOURCE_KIND,
    MaintenanceCorrelation, source_reference,
)
from repo_maintainer.models import SafetyError
from repo_maintainer.ui_registry import UIRegistry


class MaintenanceCorrelationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bindings = [
            {'monitor': {'id': 'a' * 16}, 'provider': {'request_budget': 11}},
            {'repository': str(self.root / 'repo'), 'policy': {'allowed_paths': ['a.py']},
             'provider': {'request_budget': 7}},
        ]
        self.monitor = UIRegistry(self.root / 'monitor', self.bindings[0])
        self.issue = UIRegistry(self.root / 'issue', self.bindings[1])
        self.addCleanup(lambda: self.monitor.close())
        self.addCleanup(lambda: self.issue.close())
        self.coordinator = MaintenanceCorrelation(self.monitor, [self.issue], owner_validator=self.verify_owner)
        self.budgets = [self.monitor.budget(), self.issue.budget()]
        self.monitor.put('pi_task', 'monitor-native', {'task_id': 'original-monitor', 'bytes_sha256': '1' * 64})
        self.issue.put('pi_task', 'issue-native', {'task_id': 'original-issue', 'bytes_sha256': '2' * 64})
        self.native = [self.monitor.load('pi_task'), self.issue.load('pi_task')]

    def source(self, source_type, **changes):
        return {'source_type': source_type, 'source_id': source_type + ':fixture',
                'source_version_sha256': '0' * 64, 'repository_id': 42, 'repository': 'owner/repo',
                'branch': 'main', 'head_sha': 'a' * 40, 'checks_sha256': 'b' * 64,
                'environment_sha256': 'c' * 64, 'fault_sha256': 'd' * 64,
                'authority_sha256': 'e' * 64, **changes}

    def observe(self, source_type, **changes):
        registry = self.issue if source_type == 'issue' else self.monitor
        return self.coordinator.observe_source(registry, self.source(source_type, **changes))['observation_id']

    @staticmethod
    def verify_owner(source, intent, authority):
        # A synthetic original controller grant for this fixture only. Production
        # adapters must call their existing scope/freshness/fee guards instead.
        return (set(authority) == {'schema_version', 'scope_sha256', 'source_version_sha256', 'local_grant'}
                and type(authority['schema_version']) is int and authority['schema_version'] == 1
                and authority['local_grant'] is True
                and authority['scope_sha256'] == source['authority_sha256']
                and authority['source_version_sha256'] == source['source_version_sha256'])

    def owner(self, registry, key, observation=None):
        ledger = self.coordinator.paths[str(registry.path.resolve())]
        candidates = [row for row in self.monitor.load(OBSERVATIONS).values()
                      if row['source']['reference']['ledger_id'] == ledger]
        selected = self.monitor.load(OBSERVATIONS)[observation] if observation else candidates[-1]
        source = selected['source']
        registry.put('fixture_original_authority', key,
                     {'schema_version': 1, 'scope_sha256': source['authority_sha256'],
                      'source_version_sha256': source['source_version_sha256'], 'local_grant': True})
        intent = {'schema_version': 1, 'coordinator_id': self.coordinator.coordinator_id,
                  'observation_id': selected['id'], 'source_reference': source['reference'],
                  'authority_reference': source_reference(registry, 'fixture_original_authority', key),
                  'authority_sha256': source['authority_sha256']}
        registry.put(DISPATCH_KIND, key, intent)
        return source_reference(registry, DISPATCH_KIND, key)

    def approve(self, left, right, **changes):
        value = {'schema_version': 1, 'approved': True, 'actor': 'local-operator',
                 'observations': sorted([left, right]),
                 'comparison_sha256': self.coordinator.comparison_fingerprint(left), **changes}
        key = fingerprint(value)
        self.monitor.put(APPROVAL_KIND, key, value)
        return source_reference(self.monitor, APPROVAL_KIND, key)

    def linked_pair(self):
        left, right = self.observe('ci'), self.observe('issue')
        result = self.coordinator.link(left, right, self.approve(left, right))
        self.assertEqual('related', result['status'], result)
        return left, right

    def unchanged(self):
        self.assertEqual(self.budgets, [self.monitor.budget(), self.issue.budget()])
        self.assertEqual(self.native, [self.monitor.load('pi_task'), self.issue.load('pi_task')])

    def test_linked_distinct_original_ledgers_hold_one_dispatch_without_moving_native_or_budget(self):
        left, right = self.linked_pair()
        first = self.coordinator.claim(left, self.owner(self.monitor, 'first'))
        second = self.coordinator.claim(right, self.owner(self.issue, 'second'))
        self.assertTrue(first['dispatch_allowed'], first)
        self.assertEqual('held_by', second['status'], second)
        self.assertEqual(first['claim_id'], second['claim_id'])
        self.assertEqual(1, len(self.monitor.load(CLAIMS)))
        self.assertEqual({}, self.issue.load(CLAIMS))
        self.unchanged()

    def test_comparable_textless_observations_are_related_but_cannot_suppress_without_approval(self):
        left = self.observe('ci')
        result = self.coordinator.observe_source(self.issue, self.source('issue'))
        right = result['observation_id']
        self.assertEqual([left], result['related'])
        self.assertFalse(result['related_is_authority'])
        self.assertTrue(self.coordinator.claim(left, self.owner(self.monitor, 'a'))['dispatch_allowed'])
        self.assertTrue(self.coordinator.claim(right, self.owner(self.issue, 'b'))['dispatch_allowed'])
        self.assertEqual('conflict', self.coordinator.link(left, right, self.approve(left, right))['status'])
        self.assertEqual(2, len(self.monitor.load(CLAIMS)))
        self.unchanged()

    def test_same_sha_different_faults_never_suppress_each_other(self):
        left, right = self.observe('ci'), self.observe('issue', fault_sha256='f' * 64)
        result = self.coordinator.link(left, right, self.approve(left, right))
        self.assertEqual('conflict', result['status'])
        self.assertTrue(self.coordinator.claim(left, self.owner(self.monitor, 'a'))['dispatch_allowed'])
        self.assertTrue(self.coordinator.claim(right, self.owner(self.issue, 'b'))['dispatch_allowed'])

    def test_two_distinct_issues_with_same_fault_need_explicit_link_to_share_dispatch(self):
        left = self.observe('issue', source_id='issue:101')
        right = self.observe('issue', source_id='issue:102')
        self.assertNotEqual(left, right)
        self.assertTrue(self.coordinator.claim(left, self.owner(self.issue, 'a', left))['dispatch_allowed'])
        self.assertTrue(self.coordinator.claim(right, self.owner(self.issue, 'b', right))['dispatch_allowed'])

    def test_new_source_version_is_a_new_fact_and_cannot_reuse_old_alias_approval(self):
        left, right = self.linked_pair()
        proof = self.approve(left, right)
        newer = self.observe('issue', source_version_sha256='f' * 64)
        self.assertNotEqual(right, newer)
        self.assertEqual('conflict', self.coordinator.link(left, newer, proof)['status'])
        self.assertTrue(self.coordinator.claim(left, self.owner(self.monitor, 'a'))['dispatch_allowed'])
        self.assertTrue(self.coordinator.claim(newer, self.owner(self.issue, 'new'))['dispatch_allowed'])

    def test_source_schema_rejects_malformed_types_and_boolean_ids(self):
        for changes in ({'source_type': []}, {'repository_id': True}, {'head_sha': 5},
                        {'source_version_sha256': 'no'}, {'source_id': ''}):
            with self.subTest(changes=changes), self.assertRaises(SafetyError):
                self.coordinator.observe_source(self.issue, {**self.source('issue'), **changes})

    def test_mismatch_checks_environment_authority_repository_branch_and_sha_stays_separate(self):
        left = self.observe('ci')
        for field, value in [('checks_sha256', 'f' * 64), ('environment_sha256', 'f' * 64),
                             ('authority_sha256', 'f' * 64), ('repository_id', 43),
                             ('repository', 'other/repo'), ('branch', 'dev'), ('head_sha', 'f' * 40)]:
            with self.subTest(field=field):
                right = self.observe('issue', **{field: value})
                self.assertEqual('conflict', self.coordinator.link(left, right, self.approve(left, right))['status'])
                self.assertTrue(self.coordinator.claim(right, self.owner(self.issue, field))['dispatch_allowed'])
        self.unchanged()

    def test_racing_claims_on_linked_sources_allow_exactly_one_owner(self):
        left, right = self.linked_pair()
        owners = [self.owner(self.monitor, 'one'), self.owner(self.issue, 'two')]
        barrier = threading.Barrier(2)

        def race(index):
            barrier.wait(timeout=5)
            return self.coordinator.claim([left, right][index], owners[index])

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(race, range(2)))
        self.assertEqual(1, sum(r['dispatch_allowed'] for r in results), results)
        self.assertEqual({'dispatch_allowed', 'held_by'}, {r['status'] for r in results})
        self.assertEqual(1, len(self.monitor.load(CLAIMS)))
        self.unchanged()

    def test_repeated_identical_source_and_owner_never_replay(self):
        observation = self.observe('commit')
        owner = self.owner(self.monitor, 'same')
        self.assertEqual(observation, self.observe('commit'))
        first = self.coordinator.claim(observation, owner)
        second = self.coordinator.claim(observation, owner)
        self.assertTrue(first['dispatch_allowed'])
        self.assertFalse(second['dispatch_allowed'])
        self.assertEqual(first['claim_id'], second['claim_id'])
        self.assertEqual(1, len(self.monitor.load(OBSERVATIONS)))

    def test_unknown_and_restart_keep_claim_and_original_unknown_consumption(self):
        left, right = self.linked_pair()
        owner = self.owner(self.monitor, 'unknown')
        result = self.coordinator.claim(left, owner)
        reservation, _ = self.monitor.reserve(maximum=2)
        self.monitor.settle(reservation, {'requests': None}, success=False)
        budget = self.monitor.budget()
        self.coordinator.mark_outcome(result['claim_id'], owner, 'unknown', revision=0)
        self.monitor.close()
        self.issue.close()
        self.monitor = UIRegistry(self.root / 'monitor', self.bindings[0])
        self.issue = UIRegistry(self.root / 'issue', self.bindings[1])
        self.coordinator = MaintenanceCorrelation(self.monitor, [self.issue], owner_validator=self.verify_owner)
        held = self.coordinator.claim(right, self.owner(self.issue, 'after-restart'))
        self.assertEqual('held_by', held['status'], held)
        self.assertEqual('unknown', held['outcome'])
        self.assertEqual(budget, self.monitor.budget())
        self.assertEqual(2, budget['requests_reserved_unknown'])
        self.assertEqual(self.native, [self.monitor.load('pi_task'), self.issue.load('pi_task')])

    def test_finished_and_interrupted_claims_are_never_released(self):
        for outcome in ('finished', 'interrupted'):
            with self.subTest(outcome=outcome):
                observation = self.observe('ci', fault_sha256=fingerprint(outcome))
                owner = self.owner(self.monitor, outcome)
                result = self.coordinator.claim(observation, owner)
                self.coordinator.mark_outcome(result['claim_id'], owner, outcome, revision=0)
                self.assertEqual('held_by', self.coordinator.claim(observation, owner)['status'])
                with self.assertRaises(SafetyError):
                    self.coordinator.mark_outcome(result['claim_id'], owner, 'started', revision=1)

    def test_tampered_original_source_reference_blocks_dispatch_without_creating_claim(self):
        observation = self.observe('issue')
        source = self.monitor.load(OBSERVATIONS)[observation]['source']
        reference = source['reference']
        self.issue.put(SOURCE_KIND, reference['object_id'], {'schema_version': 1, 'source': self.source('issue', branch='dev')})
        result = self.coordinator.claim(observation, self.owner(self.issue, 'blocked'))
        self.assertEqual('conflict', result['status'], result)
        self.assertEqual({}, self.monitor.load(CLAIMS))

    def test_rehashed_source_cannot_borrow_reference_for_another_workflow_or_root_scope(self):
        observation = self.observe('ci')
        source = self.monitor.load(OBSERVATIONS)[observation]['source']
        for field in ('checks_sha256', 'authority_sha256'):
            with self.subTest(field=field), self.assertRaises(SafetyError):
                self.coordinator.observe({**source, field: 'f' * 64})

    def test_tampered_observation_link_owner_or_approval_is_fail_closed(self):
        left, right = self.linked_pair()
        owner = self.owner(self.monitor, 'owner')
        result = self.coordinator.claim(left, owner)
        other_owner = self.owner(self.issue, 'right-owner')
        claim = self.monitor.load(CLAIMS)[result['claim_id']]
        changed = deepcopy(claim)
        changed['core']['observation_id'] = right
        self.monitor.put(CLAIMS, claim['id'], changed)
        self.assertEqual('conflict', self.coordinator.claim(right, other_owner)['status'])
        self.monitor.put(CLAIMS, claim['id'], claim)
        link_id, link = next(iter(self.monitor.load(LINKS).items()))
        changed = deepcopy(link)
        changed['core']['comparison_sha256'] = 'f' * 64
        self.monitor.put(LINKS, link_id, changed)
        self.assertEqual('conflict', self.coordinator.claim(right, other_owner)['status'])
        self.monitor.put(LINKS, link_id, link)
        proof = link['core']['proof_reference']
        value = self.monitor.load(APPROVAL_KIND)[proof['object_id']]
        self.monitor.put(APPROVAL_KIND, proof['object_id'], {**value, 'approved': False})
        self.assertEqual('conflict', self.coordinator.claim(right, other_owner)['status'])

    def test_untrusted_approval_and_unknown_ledger_do_not_merge(self):
        left, right = self.observe('ci'), self.observe('issue')
        for changes in ({'actor': 'issue-author'}, {'approved': False}, {'schema_version': True}):
            result = self.coordinator.link(left, right, self.approve(left, right, **changes))
            self.assertEqual('conflict', result['status'], result)
        unknown = self.owner(self.issue, 'unknown-ledger')
        unknown['ledger_id'] = 'f' * 64
        self.assertEqual('conflict', self.coordinator.claim(left, unknown)['status'])
        self.assertEqual({}, self.monitor.load(CLAIMS))

    def test_linking_unclaimed_third_alias_to_existing_owner_preserves_the_owner(self):
        left, right = self.linked_pair()
        result = self.coordinator.claim(left, self.owner(self.monitor, 'owner'))
        third = self.observe('review')
        self.assertEqual('related', self.coordinator.link(right, third, self.approve(right, third))['status'])
        held = self.coordinator.claim(third, self.owner(self.monitor, 'third', third))
        self.assertEqual('held_by', held['status'], held)
        self.assertEqual(result['claim_id'], held['claim_id'])

    def test_metadata_drift_and_extra_schema_fields_cannot_create_alias_authority(self):
        observation = self.observe('issue')
        owner = self.owner(self.issue, 'owner')
        with self.issue.transaction() as db:
            value = json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
            value['policy']['allowed_paths'] = ['other.py']
            db.execute('UPDATE metadata SET body=?', (json.dumps(value),))
        self.assertEqual('conflict', self.coordinator.claim(observation, owner)['status'])
        source = self.source('ci')
        with self.assertRaises(SafetyError):
            self.coordinator.observe_source(self.monitor, {**source, 'fuzzy_text': 'same exception'})

    def test_stale_alias_original_source_cannot_suppress_a_current_source(self):
        left, right = self.linked_pair()
        first = self.coordinator.claim(left, self.owner(self.monitor, 'original-owner', left))
        original = self.monitor.load(OBSERVATIONS)[left]['source']['reference']
        fact = self.monitor.load(SOURCE_KIND)[original['object_id']]
        fact['source']['branch'] = 'changed-original-fact'
        self.monitor.put(SOURCE_KIND, original['object_id'], fact)
        held = self.coordinator.claim(right, self.owner(self.issue, 'current-owner', right))
        self.assertEqual('conflict', held['status'], held)
        self.assertFalse(held['dispatch_allowed'])
        self.assertEqual([first['claim_id']], list(self.monitor.load(CLAIMS)))
        self.unchanged()

    def test_typed_canonical_source_fact_rejects_float_and_boolean_repository_ids(self):
        for malformed, valid in ((42.0, 42), (True, 1)):
            source = self.source('issue', repository_id=valid)
            key = 'forged-source-' + str(valid)
            self.issue.put(SOURCE_KIND, key, {'schema_version': 1, 'source': {**source, 'repository_id': malformed}})
            with self.subTest(repository_id=malformed), self.assertRaises(SafetyError):
                self.coordinator.observe({**source, 'reference': source_reference(self.issue, SOURCE_KIND, key)})
        self.assertEqual({}, self.monitor.load(OBSERVATIONS))
        self.assertEqual({}, self.monitor.load(CLAIMS))

    def test_arbitrary_original_native_fact_is_not_a_dispatch_owner(self):
        observation = self.observe('ci')
        result = self.coordinator.claim(observation, source_reference(self.issue, 'pi_task', 'issue-native'))
        self.assertEqual('conflict', result['status'], result)
        self.assertEqual({}, self.monitor.load(CLAIMS))
        self.unchanged()

    def test_an_immutable_owner_cannot_dispatch_a_new_source_version(self):
        previous = self.observe('commit', source_id='same-source', source_version_sha256='1' * 64)
        owner = self.owner(self.monitor, 'immutable-original-intent', previous)
        first = self.coordinator.claim(previous, owner)
        newer = self.observe('commit', source_id='same-source', source_version_sha256='2' * 64)
        duplicate = self.coordinator.claim(newer, owner)
        self.assertEqual('dispatch_allowed', first['status'])
        self.assertEqual('conflict', duplicate['status'], duplicate)
        self.assertEqual(1, len(self.monitor.load(CLAIMS)))
        self.unchanged()

    def test_missing_or_non_boolean_controller_authority_verification_denies_dispatch(self):
        observation = self.observe('ci')
        owner = self.owner(self.monitor, 'owner', observation)
        default = MaintenanceCorrelation(self.monitor, [self.issue])
        self.assertEqual('conflict', default.claim(observation, owner)['status'])
        for value in (False, None, 1, 'approved'):
            self.coordinator.owner_validator = lambda source, intent, authority: value
            with self.subTest(value=value):
                self.assertEqual('conflict', self.coordinator.claim(observation, owner)['status'])
        self.assertEqual({}, self.monitor.load(CLAIMS))

    def test_intent_fields_cannot_borrow_another_scope_coordinator_or_authority(self):
        observation = self.observe('ci')
        owner = self.owner(self.monitor, 'owner', observation)
        original = self.monitor.load(DISPATCH_KIND)[owner['object_id']]
        other_authority = self.owner(self.issue, 'foreign', self.observe('issue'))
        foreign_intent = self.issue.load(DISPATCH_KIND)[other_authority['object_id']]
        other_source = self.observe('review')
        other_reference = self.monitor.load(OBSERVATIONS)[other_source]['source']['reference']
        for changes in ({'schema_version': True}, {'coordinator_id': 'f' * 64},
                        {'observation_id': 'f' * 64}, {'authority_sha256': 'f' * 64},
                        {'authority_reference': foreign_intent['authority_reference']},
                        {'source_reference': other_reference},
                        {'untrusted_extra': 'permission'}):
            self.monitor.put(DISPATCH_KIND, 'forged-intent', {**original, **changes})
            result = self.coordinator.claim(observation, source_reference(self.monitor, DISPATCH_KIND, 'forged-intent'))
            with self.subTest(changes=changes):
                self.assertEqual('conflict', result['status'], result)
        self.assertEqual({}, self.monitor.load(CLAIMS))

    def test_controller_verifier_reads_original_journals_without_a_coordinator_write_lock(self):
        observation = self.observe('issue')
        owner = self.owner(self.issue, 'owner', observation)
        reads = []
        def verify(source, intent, authority):
            with self.monitor.transaction() as db:
                reads.append(db.execute('SELECT count(*) FROM objects').fetchone()[0])
            with self.issue.transaction() as db:
                reads.append(db.execute('SELECT count(*) FROM objects').fetchone()[0])
            return self.verify_owner(source, intent, authority)
        self.coordinator.owner_validator = verify
        result = self.coordinator.claim(observation, owner)
        self.assertTrue(result['dispatch_allowed'], result)
        self.assertEqual(2, len(reads))
        self.unchanged()

    def test_authority_changed_between_verification_and_claim_cannot_dispatch(self):
        observation = self.observe('issue')
        owner = self.owner(self.issue, 'owner', observation)
        intent = self.issue.load(DISPATCH_KIND)[owner['object_id']]
        authority_ref = intent['authority_reference']
        entered, changed = threading.Event(), threading.Event()
        errors = []
        def revoke():
            try:
                self.assertTrue(entered.wait(3))
                value = self.issue.load(authority_ref['kind'])[authority_ref['object_id']]
                self.issue.put(authority_ref['kind'], authority_ref['object_id'], {**value, 'local_grant': False})
            except BaseException as error:
                errors.append(error)
            finally:
                changed.set()
        worker = threading.Thread(target=revoke)
        def verify(source, intent, authority):
            entered.set()
            self.assertTrue(changed.wait(3))
            return self.verify_owner(source, intent, authority)
        self.coordinator.owner_validator = verify
        worker.start()
        try:
            result = self.coordinator.claim(observation, owner)
        finally:
            worker.join(3)
        self.assertEqual([], errors)
        self.assertFalse(worker.is_alive())
        self.assertEqual('conflict', result['status'], result)
        self.assertEqual({}, self.monitor.load(CLAIMS))
        self.unchanged()

    def test_verifier_error_is_sanitized_and_cannot_create_claim(self):
        observation = self.observe('ci')
        owner = self.owner(self.monitor, 'owner', observation)
        def fail(*args):
            raise ValueError('synthetic-private-provider-key')
        self.coordinator.owner_validator = fail
        result = self.coordinator.claim(observation, owner)
        self.assertEqual('conflict', result['status'])
        self.assertNotIn('synthetic-private-provider-key', json.dumps(result))
        self.assertEqual({}, self.monitor.load(CLAIMS))

    def test_duplicate_outcome_is_idempotent_and_cannot_restart_a_candidate(self):
        observation = self.observe('ci')
        owner = self.owner(self.monitor, 'owner', observation)
        claim = self.coordinator.claim(observation, owner)
        first = self.coordinator.mark_outcome(claim['claim_id'], owner, 'started', revision=0)
        before = self.monitor.load(CLAIMS)
        self.assertEqual(first, self.coordinator.mark_outcome(claim['claim_id'], owner, 'started', revision=0))
        self.assertEqual(first, self.coordinator.mark_outcome(claim['claim_id'], owner, 'started', revision=1))
        self.assertEqual(before, self.monitor.load(CLAIMS))
        candidate = self.coordinator.mark_outcome(claim['claim_id'], owner, 'candidate_ready', revision=1)
        with self.assertRaises(SafetyError):
            self.coordinator.mark_outcome(claim['claim_id'], owner, 'started', revision=candidate['revision'])
        finished = self.coordinator.mark_outcome(claim['claim_id'], owner, 'finished', revision=2)
        self.assertEqual(finished, self.coordinator.mark_outcome(claim['claim_id'], owner, 'finished', revision=2))
        self.assertEqual('held_by', self.coordinator.claim(observation, owner)['status'])

    def test_forged_duplicate_history_is_rejected_instead_of_progressed(self):
        observation = self.observe('ci')
        owner = self.owner(self.monitor, 'owner', observation)
        claim = self.coordinator.claim(observation, owner)
        self.coordinator.mark_outcome(claim['claim_id'], owner, 'started', revision=0)
        row = self.monitor.load(CLAIMS)[claim['claim_id']]
        row['history'].append(deepcopy(row['history'][0]))
        row['revision'] += 1
        self.monitor.put(CLAIMS, claim['claim_id'], row)
        result = self.coordinator.claim(observation, owner)
        self.assertEqual('conflict', result['status'], result)


if __name__ == '__main__':
    unittest.main()
