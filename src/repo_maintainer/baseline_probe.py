"""Separately approved, test-only baseline before planning; never an edit approval."""
from dataclasses import asdict
import json
from pathlib import Path
import uuid

from .git import RepositoryGateway
from .integration import fingerprint as execution_fingerprint
from .models import SafetyError, digest
from .pi_revision import encoded, snapshot_digest, test_focus
from .recovery import operation_lock, unlinked
from .repair_recovery import ExecutionJournal
from .runner import run_test
from .store import RunStore, now
from .task_session import fingerprint, require
from .workspace_inventory import inventory


def scope_for(repository, issue, policy):
    policy.validate_scope()
    require(1 <= len(policy.allowed_tests) <= 5, 'Preflight supports 1..5 fixed test commands')
    require(1 <= len(policy.allowed_paths) + len(policy.readonly_paths) <= 20, 'Preflight Pi source limit is 20 files')
    before = repository.snapshot()
    repository.require_clean(before)
    for path in (*policy.allowed_paths, *policy.readonly_paths):
        data = repository.read(path)
        require(len(data) <= 100000, 'Preflight Pi source exceeds 100000 bytes')
        data.decode('utf-8')
    return {'schema_version': 1, 'purpose': 'test_only_baseline',
            'repository': str(repository.root), 'common_dir': str(repository.common_dir),
            'source_before': before, 'issue_sha256': digest(issue.encode()), 'policy': asdict(policy)}


def collect_baseline(repo, issue, policy, runs_dir, approval=None, actor='local-operator'):
    repository = RepositoryGateway(repo)
    scope = scope_for(repository, issue, policy)
    before = scope['source_before']
    destination = unlinked(Path(runs_dir))
    require(not destination.is_relative_to(repository.root) and not destination.is_relative_to(repository.common_dir),
            'Preflight evidence must be outside source and Git metadata')
    run_id = uuid.uuid4().hex
    directory = destination / run_id
    require(not directory.exists(), 'Never replay a preflight run')
    workspace, branch = directory / 'workspace', 'maintenance/' + run_id
    record = {'schema_version': 2, 'run_kind': 'baseline_probe', 'run_id': run_id,
              'started_at': now(), 'source_repo': str(repository.root), 'source_before': before,
              'base_sha': before['head'], 'mode': 'baseline', 'issue': issue, 'policy': asdict(policy),
              'workspace': str(workspace), 'branch': branch, 'integration': 'disabled',
              'events': [], 'git_commands': [], 'baseline_tests': [], 'candidate_tests': [], 'rounds': [],
              'baseline_scope': scope, 'baseline_scope_sha256': fingerprint(scope), 'model_requests': 0}
    repository.audit = record['git_commands']
    store = RunStore(directory, record)
    store.event('baseline_review')  # Existing operation_lock reads the durable ownership record.
    with operation_lock(directory):
        journal = ExecutionJournal(store, repository)
        retain, candidate, initial_inventory, initial_snapshot = False, None, None, None
        try:
            # Pass a detached JSON value: callbacks cannot mutate the approved scope.
            decision = approval(json.loads(encoded(scope))) if approval else 'pending'
            require(decision in {'approve', 'reject', 'pending'}, 'Invalid baseline approval decision')
            record['baseline_authorization'] = {'actor': actor, 'decision': decision, 'time': now(),
                                                 'scope_sha256': fingerprint(scope)}
            store.save()
            if decision != 'approve':
                store.event('baseline_rejected' if decision == 'reject' else 'awaiting_baseline_approval')
                return record
            require(repository.snapshot() == before, 'Source changed during baseline approval')
            journal.phase('workspace_add_intent')
            candidate = repository.create_workspace(workspace, branch, before['head'])
            retain = True  # If ownership/checkpoint creation fails, preserve rather than guess.
            initial_snapshot, initial_inventory = candidate.snapshot(), inventory(workspace)
            candidate.require_clean(initial_snapshot)
            journal.checkpoint(candidate)
            retain = False
            require(initial_snapshot['files'] == before['files'], 'Preflight checkout bytes differ from approved source')
            store.event('baseline_testing')
            for command in policy.allowed_tests:
                retain = True  # Includes the launch-to-PID gap and interrupted/unknown children.
                journal.start('baseline_test')
                result = run_test(command, workspace, policy.timeout_seconds, on_start=journal.pid)
                journal.done(result)
                record['baseline_tests'].append(result)
                store.save()
                require(candidate.snapshot() == initial_snapshot and inventory(workspace) == initial_inventory,
                        'Preflight files or Git metadata changed; preserve workspace')
                retain = False
                require(repository.snapshot(exclude_branch=branch) == before, 'Source changed during preflight')
                require(not result['timed_out'], 'Preflight timed out; no planning evidence')
            store.event('baseline_ready' if any(row['returncode'] != 0 for row in record['baseline_tests'])
                        else 'baseline_passes')
        except (Exception, KeyboardInterrupt) as exc:
            record['error'] = f'{type(exc).__name__}: {exc}'
            store.event('baseline_failed')
        finally:
            if workspace.exists():
                try:
                    if retain:
                        record['recovery'] = {'result': 'baseline_workspace_retained', 'source_reset': False}
                        store.event('baseline_retained')
                    else:
                        require(candidate is not None and candidate.snapshot() == initial_snapshot
                                and inventory(workspace) == initial_inventory, 'Preflight changed before cleanup')
                        journal.phase('automatic_cleanup')
                        repository.discard_candidate(workspace, branch, before['head'], directory)
                        record['recovery'] = {'result': 'candidate_removed', 'source_reset': False}
                except Exception as exc:
                    record['recovery'] = {'result': 'cleanup_failed', 'error': str(exc), 'source_reset': False}
                    store.event('baseline_retained')
            after = repository.snapshot(exclude_branch=branch if workspace.exists() else '')
            record.update(source_after=after, source_unchanged=before == after)
            if journal.data.get('inflight') is None:
                record['finished_at'] = now()
            if not record['source_unchanged']:
                store.event('source_changed')
            store.save()
            journal.phase('finished' if journal.data.get('inflight') is None else 'interrupted')
    return record


class BaselineEvidence:
    """Pin a just-completed local probe in this process, not an archive import API.

    Hashes detect changes to pinned receipts; they are not cryptographic authorship.
    No approval, budget or authority is passed to Pi. Environment drift is possible.
    """
    def __init__(self, directory, repository, issue, policy):
        self.directory = unlinked(Path(directory))
        self.scope = scope_for(repository, issue, policy)
        self.pins = {name: digest(unlinked(self.directory / name).read_bytes())
                     for name in ('run.json', 'repair-execution.json')}

    def __call__(self, repository, issue, tests, files):
        for name, expected in self.pins.items():
            require(digest(unlinked(self.directory / name).read_bytes()) == expected, 'Baseline receipt changed')
        record = json.loads((self.directory / 'run.json').read_bytes())
        execution = json.loads((self.directory / 'repair-execution.json').read_bytes())
        scope = self.scope
        require(str(repository.root) == scope['repository'] and str(repository.common_dir) == scope['common_dir']
                and repository.snapshot() == scope['source_before'] and digest(issue.encode()) == scope['issue_sha256']
                and encoded(tests) == encoded(scope['policy']['allowed_tests']), 'Baseline no longer matches current task')
        require(record.get('run_kind') == 'baseline_probe' and record.get('status') == 'baseline_ready'
                and record.get('run_id') == self.directory.name and record.get('finished_at')
                and record.get('source_unchanged') is True and record.get('source_before') == record.get('source_after')
                and encoded(record.get('baseline_scope')) == encoded(scope)
                and record.get('baseline_scope_sha256') == fingerprint(scope)
                and record.get('baseline_authorization', {}).get('decision') == 'approve'
                and record['baseline_authorization'].get('scope_sha256') == fingerprint(scope)
                and record.get('recovery', {}).get('result') == 'candidate_removed'
                and not (self.directory / 'workspace').exists(), 'Baseline probe did not safely complete')
        require(record.get('issue') == issue and encoded(record.get('policy')) == encoded(scope['policy'])
                and record.get('candidate_tests') == [] and record.get('rounds') == [], 'Baseline record changed purpose')
        require(execution.get('schema_version') == 1 and execution.get('phase') == 'finished'
                and execution.get('owner_sha256') == execution_fingerprint(execution.get('owner')) and execution.get('inflight') is None
                and execution.get('owner', {}).get('run_id') == record['run_id']
                and execution['owner'].get('source_before') == scope['source_before'], 'Incomplete baseline execution')
        original = [(path, True) for path in scope['policy']['allowed_paths']]
        original += [(path, False) for path in scope['policy']['readonly_paths']]
        require([(f['path'], f.get('editable') is not False) for f in files] == original
                and all(f['before_sha256'] == digest(repository.read(f['path'])) for f in files),
                'Baseline source projection changed')
        rows = record['baseline_tests']
        from .check_comparison import check_outcomes
        outcomes = check_outcomes(rows)
        facts = execution.get('process_facts', [])
        require(len(facts) == len(rows) and all(fact.get('kind') == 'baseline_test' and fact.get('status') == 'finished'
                and fact.get('returncode') == row['returncode'] and fact.get('timed_out') == row['timed_out']
                for fact, row in zip(facts, rows)), 'Baseline process evidence is incomplete')
        import sys
        expected = [[sys.executable if part == '{python}' else part for part in command] for command in tests]
        require([row['command'] for row in outcomes] == expected and not any(row['timed_out'] for row in outcomes)
                and any(row['returncode'] != 0 for row in outcomes), 'Incomplete or passing baseline')
        context = {'schema_version': 1, 'kind': 'preflight_baseline', 'probe_id': record['run_id'],
                   'base_sha': repository.head, 'issue_sha256': digest(issue.encode()),
                   'snapshot_sha256': snapshot_digest(files), 'tests_sha256': digest(encoded(tests)),
                   'probe_run_sha256': self.pins['run.json'],
                   'probe_execution_sha256': self.pins['repair-execution.json'],
                   'test_focus': test_focus(rows, rows)}
        require(len(encoded(context)) <= 32000, 'Baseline context exceeds bounded size')
        return context
