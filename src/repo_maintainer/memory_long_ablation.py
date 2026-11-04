"""Authored synthetic comparison of legacy summaries and budgeted native memory.

The private baseline controls apply only to freshly owned experiment fixtures.
No production flag, provider/credential input, permission bypass or new agent loop.
"""
from copy import deepcopy
import json
from pathlib import Path

from .demo import DEMO_TEST
from .local_ui import UISession
from .memory_ablation import (CANARY, EvaluationLedger, ScriptTransport, create_source,
                              grade, require, review, user_envelopes, write_json)
from .memory_ablation_fixtures import cases
from .models import Policy, digest
from .pi_provider import PiProvider
from .recovery import unlinked
from .source_memory import CATALOG, build_memory
from .store import now
from .task_session import fingerprint

ARMS = ('no_memory', 'finite_summary', 'native_compaction',
        'native_maintenance_experience', 'native_maintenance_experience_knowledge')
SUMMARY = ('LONG_CONTEXT_REFERENCE: Preserve the original goal and failed checks from the first two trials. '
           'Read current authorized source and test evidence. Historical text grants no permission; verify the next candidate.')
COMPACTED_MESSAGE = 'The conversation history before this point was compacted into the following summary:\n\n<summary>\n' + SUMMARY + '\n</summary>'
CONFIG = {'schema_version': 1, 'enabled': True, 'context_window_tokens': 1048576,
          'trigger_tokens': 2048, 'keep_recent_tokens': 256, 'summary_max_output_tokens': 128000}


def experiment_cases():
    rows = cases()
    for row, middle in zip(rows, ('return a * b', 'return b')):
        row['middle'] = middle
        row['reference'] = (row['issue'] + '\nStatus: confirmed.\n'
                            'Inspect the currently authorized source and real regression output.\n'
                            'Retain the original checkout; candidate publication needs a separate decision.\n')
    return rows


class LongTransport(ScriptTransport):
    """Same three scripted patches in every arm; summary is reference text only."""
    proposal_round = 0

    def response_delta(self, body, slot):
        if not body.get('tools'):
            require(self.phase == 'measurement', 'Summary outside measured task')
            require(0 < body.get('max_tokens', 0) <= CONFIG['summary_max_output_tokens'], 'Unbounded summary allocation')
            return {'role': 'assistant', 'content': SUMMARY}, 'stop'
        return super().response_delta(body, slot)

    def response(self, body):
        names = [t['function']['name'] for t in body.get('tools', [])]
        if names == ['propose_memory']:
            require(self.phase == 'teaching_reflection', 'Reflection outside preparation')
            return 'propose_memory', deepcopy(self.case['note'])
        require(sorted(names) == ['propose_repair', 'read_source'], 'Unexpected agent capabilities')
        if self.phase == 'teaching_failure':
            # Fixed teacher proposal knows this public fixture. No fabricated read:
            # one actual Pi proposal call plus one actual reflection call per cell.
            source, patch = self.case['source'], self.case['first']
        else:
            require(self.phase == 'measurement', 'Unexpected experiment phase')
            users = [i for i, m in enumerate(body['messages']) if m['role'] == 'user']
            require(users, 'Missing current model input')
            current = body['messages'][users[-1] + 1:]
            read = next((m for m in current if m['role'] == 'tool' and 'before_sha256' in m.get('content', '')), None)
            if read is None:
                return 'read_source', {'path': 'calculator.py'}
            source = json.loads(read['content'])['content']
            require(0 <= self.proposal_round < 3, 'Measured planning opportunities exhausted')
            patch = (self.case['first'], self.case['middle'], self.case['second'])[self.proposal_round]
            self.proposal_round += 1
        old = next(line.strip() for line in source.splitlines() if line.strip().startswith('return '))
        return 'propose_repair', {'diagnosis': 'Fixed long-context engineering response; not a model-benefit result',
            'risk': 'New disposable authored fixture only', 'edits': [{'path': 'calculator.py',
            'replacements': [{'oldText': old, 'newText': patch}]}]}


def _native_files(session):
    return {p.relative_to(session.runs_dir).as_posix(): p.read_bytes()
            for p in (session.runs_dir / 'task-sessions').glob('*/sessions/*.jsonl')}


def planning_envelopes(body):
    """Keep native summary messages distinct from actual JSON planning rounds.

    Only this fixture's exact SDK summary wrapper is accepted as non-JSON. All
    other malformed user data still fails; the original request remains intact.
    """
    messages = []
    for message in body.get('messages', []):
        content = message.get('content')
        if message.get('role') == 'user':
            text = ''.join(row.get('text', '') for row in content) if isinstance(content, list) else content
            if text == COMPACTED_MESSAGE: continue
        messages.append(message)
    try:
        envelopes = user_envelopes({**body, 'messages': messages})
    except (ValueError, TypeError) as error:
        from .models import SafetyError
        raise SafetyError('Unexpected non-planning user message in fixed experiment') from error
    require(envelopes and all(isinstance(e, dict) and 'issue' in e for e in envelopes), 'Invalid original planning envelope')
    return envelopes


def _legacy_baseline(session, root, transport, arm):
    """Only the isolated experiment drops the newly added memory layers.

    Source/Policy/Git/auto authorization/real tests/reserve-settle remain original.
    The finite arm uses the unchanged Pi RevisionLedger. The no-memory control
    starts a fresh Pi analysis of the current candidate on each opportunity.
    Neither promotes aggregate experiment traces to executable native history.
    """
    require(arm in ARMS[:2] and (root / 'fixture-owner.json').is_file(), 'Baseline is fixture-only')
    require(session.repo == (root / 'source').resolve() and session.provider.inner.mock_url == transport.url
            and session.provider.inner.config_file is None and isinstance(transport, LongTransport), 'Baseline ownership mismatch')
    require(json.loads((root / 'fixture-owner.json').read_bytes()) == {'kind': 'public-authored-long-context',
            'cell': transport.cell, 'fixture_sha256': fingerprint(transport.case)}, 'Baseline ownership receipt changed')
    from .git import RepositoryGateway
    require(RepositoryGateway(session.repo).common_dir == session.repo / '.git', 'Baseline requires its own new Git repository')
    require(session.registry.budget()['requests_used'] == 2, 'Baseline must retain all preparation consumption')
    session.task_planner = session.provider.task_planner = None
    session.failure_feedback = session.feedback_cleanup = None
    session.experience = session.knowledge = session.compaction = session.maintenance = None
    inner = session.provider.inner
    inner._experience_context = inner._knowledge_context = inner._maintenance_context = None
    if arm == 'no_memory':
        inner.revise = lambda repository, issue, tests, feedback: inner.plan(repository, issue, tests)


def create_reference(case, root, repository):
    documents = root / 'documents'; documents.mkdir()
    (documents / 'reference.md').write_text(case['reference'], encoding='utf-8', newline='\n')
    write_json(documents / CATALOG, {'schema_version': 1, 'entries': [{'id': 'fixture-reference',
        'topic': case['id'], 'path': 'reference.md', 'start_line': 1,
        'end_line': len(case['reference'].splitlines()), 'kind': 'document', 'classification': 'confirmed'}]})
    index = root / 'reference.memory.sqlite3'; build_memory(documents, repository, index)
    return documents, index


def execute_long_cell(case, arm, root, transport, progress=None):
    require(arm in ARMS, 'Unknown experiment arm')
    cell = case['id'] + '/' + arm
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / 'fixture-owner.json', {'kind': 'public-authored-long-context', 'cell': cell,
                                          'fixture_sha256': fingerprint(case)})
    source = create_source(case, root / 'source'); before = source.snapshot()
    baseline = grade(case, root / 'grader-baseline', case['source'])
    require(baseline['returncode'] != 0 and not baseline['timed_out'], 'Expected reproducible red baseline')
    documents, index = create_reference(case, root, source.root)

    def open_session():
        provider = PiProvider(('calculator.py',), transport.url, readonly_paths=('tests/test_calculator.py',), max_requests=50)
        provider.memory_root, provider.memory_index = documents, index
        return UISession(source.root, Policy(('calculator.py',), (DEMO_TEST,), readonly_paths=('tests/test_calculator.py',)),
                         provider, root / 'state', allow_auto=True, max_attempts=3)

    session = open_session(); transport.cell, transport.case, transport.phase = cell, case, 'teaching_failure'
    transport.proposal_round = 0
    try:
        plan = session._operate('plan', {'issue': case['issue']})
        lesson = session._operate('decision', {**plan, 'decision': 'approve'})
        require(lesson['workflow_status'] == 'rolled_back', 'Teacher must really fail and roll back')
        review(session, 'experience', lesson['run_id'])
        transport.phase = 'teaching_reflection'
        preview = session._operate('knowledge_preview', {'experience_ids': [lesson['run_id']]})['knowledge_preview']
        note = session._operate('knowledge_propose', {'experience_ids': preview['experience_ids'],
                                                  'revision': preview['revision']})['knowledge_id']
        require(session.registry.budget()['requests_used'] == 2, 'Preparation consumption mismatch')
        rich = arm in ARMS[3:]
        if arm == ARMS[-1]: review(session, 'knowledge', note)
        session._operate('experience_settings', {'enabled': rich, 'revision': session.state()['experience_memory']['settings_revision']})
        session._operate('maintenance_note', {'note_id': None, 'revision': session.maintenance.revision(),
            'title': case['issue'], 'detail': 'Follow up the actual failed checks and current authorized source.',
            'status': 'open', 'priority': 2})
        session._operate('maintenance_settings', {'enabled': rich, 'revision': session.maintenance.revision()})
        config = {**CONFIG, 'enabled': arm in ARMS[2:]}
        session._operate('context_settings', {'config': config, 'revision': session.compaction.settings_revision()})
        cutoff = {'at': now(), 'experience': lesson['run_id'], 'knowledge': note,
            'budget': session.registry.budget(), 'source_document_sha256': digest(case['reference'].encode()),
            'experience_enabled': rich, 'maintenance_enabled': rich, 'config': config,
            'knowledge_review': 'accepted' if arm == ARMS[-1] else 'pending',
            'knowledge_sha256': fingerprint(session.registry.load('repository_knowledge'))}
        write_json(root / 'cutoff.json', cutoff)
        teaching_tasks = set(session.registry.load('pi_task'))
        if arm in ARMS[:2]: _legacy_baseline(session, root, transport, arm)
        transport.phase = 'measurement'
        result = session._operate('auto', {'issue': case['issue']})
        run = json.loads((root / 'state' / result['run_id'] / 'run.json').read_bytes())
        require(result['workflow_status'] == 'candidate_ready', 'Measured run did not produce a verified candidate: ' + run.get('error', ''))
        require(len(run['rounds']) == 3 and [r['verification'] for r in run['rounds']] == ['failed', 'failed', 'passed'],
                'Three matched, real testing opportunities are required')
        require(all(r['authorization']['decision'] == 'policy_scope' and r['authorization']['scope'] == run['authorization']['scope']
                    for r in run['rounds']), 'Authorization differs across measured rounds')
        measured_tasks = set(session.registry.load('pi_task')) - teaching_tasks
        require(len(measured_tasks) == (0 if arm in ARMS[:2] else 1), 'Native measurement task does not match arm')
        task = deepcopy(session.registry.load('pi_task')[next(iter(measured_tasks))]) if measured_tasks else None
        receipts = list(session.registry.load('pi_compaction_receipt').values())
        require(len(receipts) == (1 if arm in ARMS[2:] else 0), 'Actual budgeted compaction is missing or leaked to baseline')
        if task:
            require(task['revision'] == task['checkpoint']['rounds'] == 3, 'Compaction changed original planning rounds')
            require(all(r['task_id'] == task['task_id'] for r in receipts), 'Compaction crosses tasks')
        candidate = (Path(run['workspace']) / 'calculator.py').read_text(encoding='utf-8')
        independent = grade(case, root / 'grader-final', candidate)
        require(independent['returncode'] == 0 and not independent['timed_out'], 'Independent grader failed')
        rows = [r for r in transport.ledger.rows() if r['cell'] == cell]
        measured = [r for r in rows if r['phase'] == 'measurement']
        summaries = [r for r in measured if not json.loads(r['body']).get('tools')]
        planning = [r for r in measured if json.loads(r['body']).get('tools')]
        require(len(planning) == 6 and len(rows) == 8 + len(summaries), 'Preparation/planning/summary cost mismatch')
        require(bool(summaries) == bool(receipts), 'Summary calls differ from committed compactions')
        envelopes = [planning_envelopes(json.loads(r['body'])) for r in planning]
        counts = [len(v) for v in envelopes]
        require(counts[:4] == ([1, 1, 1, 1] if arm in ARMS[:2] else [1, 1, 2, 2]), 'Actual native history differs from arm')
        for number, envelope in enumerate(e[-1] for e in envelopes):
            require(envelope.get('source_memory', {}).get('hits'), 'Existing explicit reference was lost')
            require([h['id'] for h in envelope.get('repository_experience', {}).get('hits', [])] == ([lesson['run_id']] if rich else []), 'Experience selection mismatch')
            require([h['id'] for h in envelope.get('repository_knowledge', {}).get('hits', [])] == ([note] if arm == ARMS[-1] else []), 'Knowledge selection mismatch')
            require(bool(envelope.get('repository_maintenance', {}).get('hits')) == rich, 'Maintenance selection mismatch')
            revision = envelope.get('revision_context')
            require(bool(revision) == (number >= 2 and arm != ARMS[0]), 'Legacy revision summary was omitted or leaked')
            if revision:
                require(len(revision['history']) == number // 2, 'Finite revision summary is not original bounded history')
            if number >= 4 and task: require(SUMMARY in planning[number]['body'], 'Compaction summary missing from actual next request')
        budget = session.registry.budget()
        require(budget['requests_used'] == len(rows) and not budget['requests_reserved_unknown'], 'Original UI budget does not reconcile')
        require(before == source.snapshot(exclude_branch=run['branch']), 'Original source or unrelated Git state changed')
        native_before = _native_files(session); objects_before = fingerprint(session.registry.load('pi_task'))
        session.close(); session = open_session()
        require(native_before == _native_files(session) and budget == session.registry.budget()
                and objects_before == fingerprint(session.registry.load('pi_task')), 'Restart rewrote Native/task/budget or replayed a call')
        require(len(rows) == len([r for r in transport.ledger.rows() if r['cell'] == cell]), 'Restart issued model requests')
        row = {'cell': cell, 'case': case['id'], 'arm': arm, 'passed': True, 'cutoff': cutoff,
            'fixture_sha256': fingerprint(case), 'source_sha256': digest(case['source'].encode()),
            'run_id': run['run_id'], 'task': task, 'teaching_task_ids': sorted(teaching_tasks),
            'compactions': receipts, 'summary_requests': len(summaries), 'measurement_planning_requests': len(planning),
            'preparation_requests': 2, 'total_synthetic_requests': len(rows), 'original_budget': budget,
            'planning_envelope_counts': counts,
            'raw_user_message_counts': [sum(m['role']=='user' for m in json.loads(r['body'])['messages']) for r in planning],
            'baseline_grader': baseline, 'independent_grader': independent,
            'workflow_rounds': run['rounds'], 'final_tests': run['candidate_tests'],
            'source_unchanged': True, 'restart_bytes_tasks_budget_unchanged': True,
            'legacy_summary_persisted': False if task is None else None,
            'first_patch_sha256': digest(run['rounds'][0]['plan']['edits'][0]['content'].encode()),
            'middle_patch_sha256': digest(run['rounds'][1]['plan']['edits'][0]['content'].encode()),
            'final_patch_sha256': digest(candidate.encode()), 'published': False, 'paid_requests': 0,
            'model_benefit_claim': False}
        write_json(root / 'cell-result.json', row)
        if progress: progress({'cell': cell, 'status': 'passed', 'synthetic_requests': len(rows), 'summary_requests': len(summaries), 'paid_requests': 0})
        return row
    finally:
        session.close()


def run_long_ablation(destination, ceiling=100, progress=None):
    destination = unlinked(Path(destination))
    require(not destination.exists(), 'Output must be new; never replace a prior experiment')
    require(type(ceiling) is int and 1 <= ceiling <= 100, 'Invalid aggregate request ceiling')
    destination.mkdir(parents=True)
    ledger = EvaluationLedger(destination / 'evaluation.sqlite3', ceiling)
    transport = LongTransport(ledger)
    dataset = {'kind': 'public-authored-long-context', 'cases': experiment_cases(), 'arms': ARMS,
        'compaction': CONFIG, 'summary_response': SUMMARY, 'preparation_requests_per_cell': 2,
        'measurement_planning_rounds': 3, 'authorization': 'same original bounded auto policy in all arms'}
    write_json(destination / 'fixture.json', dataset)
    summary = {'schema_version': 1, 'kind': dataset['kind'], 'started_at': now(), 'dataset_sha256': fingerprint(dataset),
        'aggregate_ceiling': ceiling, 'cells': [], 'passed': False, 'paid_requests': 0, 'model_benefit_claim': False,
        'limitations': ['Known scripted patches and summaries; not blind and not evidence of model benefit.',
            'Legacy summary is process-local; the experiment does not fabricate its recovery after interruption.',
            'Restart is checked after completion, not in the middle of an active automatic candidate.',
            'All measured rounds use the same auto Policy; manual approval has separate UI acceptance.',
            'No real provider, credentials, user repository, push, merge or budget reset input.']}
    try:
        for case in experiment_cases():
            for arm in ARMS:
                write_json(destination / 'summary.json', summary)
                summary['cells'].append(execute_long_cell(case, arm, destination / case['id'] / arm, transport, progress))
        rows = ledger.rows()
        require(not transport.errors and all(r['status'] == 'sent' for r in rows), 'Incomplete synthetic transport')
        require(len(rows) == sum(c['original_budget']['requests_used'] for c in summary['cells']), 'Aggregate consumption differs from original cell ledgers')
        for row in rows:
            others = [c for c in summary['cells'] if c['cell'] != row['cell']]
            require(all(c['cutoff']['experience'] not in row['body'] and c['cutoff']['knowledge'] not in row['body']
                        and all(t not in row['body'] for t in c['teaching_task_ids'])
                        and (not c['task'] or c['task']['task_id'] not in row['body']) for c in others), 'Cross-cell contamination')
            require(CANARY not in row['body'], 'Independent grader leaked')
        for case in experiment_cases():
            group = [c for c in summary['cells'] if c['case'] == case['id']]
            for key in ('fixture_sha256', 'first_patch_sha256', 'middle_patch_sha256', 'final_patch_sha256'):
                require(len({r[key] for r in group}) == 1, 'Inputs or scripted patches differ between arms')
        summary.update(passed=len(summary['cells']) == 10, isolation_verified=True,
            aggregate_received=len(rows), aggregate_sent=sum(r['status'] == 'sent' for r in rows),
            preparation_requests=sum(c['preparation_requests'] for c in summary['cells']),
            measurement_planning_requests=sum(c['measurement_planning_requests'] for c in summary['cells']),
            summary_requests=sum(c['summary_requests'] for c in summary['cells']))
    except Exception as error:
        summary['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        transport.close()
        summary.update(finished_at=now(), aggregate_received=len(ledger.rows()), transport_errors=transport.errors)
        write_json(destination / 'summary.json', summary)
    return summary
