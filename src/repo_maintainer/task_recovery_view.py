"""Read-only task recovery projection. Existing controller previews own all action authority."""
from pathlib import Path
import re

from .dashboard import unlinked
from .git import RepositoryGateway
from .models import SafetyError, digest
from .store import redact
from .task_session import TASK_ID


READ_ERRORS = (SafetyError, ValueError, OSError, KeyError, TypeError)


def _validation_tasks(session):
    """Classify original readonly proposal bindings, never their repair authority.

    They have their own Issue/monitor projection. A purpose string alone must
    not hide an orphan, changed source, broken chain or damaged checkpoint.
    Historical context2 and explicit context3 retain their original protocol.
    """
    from .task_session import _context, fingerprint, VALIDATION_PURPOSE
    from .validation_proposal import clarification_leaf, proposal_contract
    planner = session.task_planner
    if not planner:
        return set()
    rows = session.registry.load('validation_proposal')
    links = session.registry.load('validation_clarification')
    result = set()
    for identity, record in session.registry.load('pi_task').items():
        try:
            context = _context(record['context'])
            if context.get('purpose') != VALIDATION_PURPOSE:
                continue  # In particular, investigation is still a repair.
            matches = [(key, row) for key, row in rows.items()
                       if isinstance(row, dict) and row.get('task_id') == identity]
            if len(matches) != 1:
                continue
            key, row = matches[0]
            source = row['source']
            if (not isinstance(source, dict) or row['id'] != key or row.get('runtime_authorized') is not False
                    or row.get('status') not in {'started', 'interrupted_no_replay', 'clarification_needed',
                        'awaiting_validation_review', 'accepted', 'rejected'}
                    or set(source) != {'schema_version', 'purpose', 'repository_id', 'base_sha',
                        'request_sha256', 'issue', 'read_roots', 'files', 'authority',
                        'authority_sha256', 'runtime_authorized', 'source_sha256'}
                    or type(source['schema_version']) is not int or source['schema_version'] != 1
                    or source['purpose'] != VALIDATION_PURPOSE or source['runtime_authorized'] is not False
                    or source['source_sha256'] != fingerprint({k: v for k, v in source.items() if k != 'source_sha256'})
                    or source['authority_sha256'] != fingerprint(source['authority'])
                    or source['request_sha256'] != digest(source['issue'].encode())
                    or [f['path'] for f in source['files']] != context['paths']
                    or any(f.get('editable') is not False or digest(f['content'].encode()) != f['before_sha256']
                           for f in source['files'])):
                continue
            binding = source['authority']['provider']
            if (any(binding[k] != context[k] for k in ('provider', 'model'))
                    or key != fingerprint({'source': source, 'provider_identity': binding})):
                continue
            scope = dict(repository_sha256=source['repository_id'], base_sha=source['base_sha'],
                issue_sha256=fingerprint(source['issue']),
                policy_sha256=fingerprint({'purpose': VALIDATION_PURPOSE, 'readonly': context['paths']}),
                provider_sha256=fingerprint(binding),
                source_sha256=fingerprint({'validation_source': source['source_sha256']}),
                tests_sha256=fingerprint({'purpose': VALIDATION_PURPOSE, 'execution_checks': False}),
                context_sha256=fingerprint(context))
            saved = planner.store._load(identity, scope)  # Original budget/scope/checkpoint metadata; no lease or SDK.
            checkpoint = saved['checkpoint']
            if row['status'] not in {'started', 'interrupted_no_replay'} and checkpoint is None:
                continue
            if row['status'] not in {'started', 'interrupted_no_replay'}:
                if proposal_contract(source, row['proposal']) != row['contract']:
                    continue
                if (row['status'] == 'clarification_needed') != row['proposal']['clarification']:
                    continue
            if checkpoint:
                name = checkpoint['file']
                if not isinstance(name, str) or len(name) > 200 or Path(name).name != name or not re.fullmatch(r'[A-Za-z0-9._-]+\.jsonl', name):
                    continue
                path = unlinked(planner.store.directory(identity) / 'sessions' / name)
                with path.open('rb') as stream:
                    prefix = stream.read(checkpoint['prefix_bytes'])
                if len(prefix) != checkpoint['prefix_bytes'] or digest(prefix) != checkpoint['prefix_sha256']:
                    continue
            root, seen = key, set()
            for _ in range(len(links) + 1):
                if root in seen:
                    raise SafetyError('Clarification chain cycle')
                seen.add(root)
                parents = [p for p, link in links.items() if link.get('child_id') == root]
                if not parents:
                    break
                if len(parents) != 1:
                    raise SafetyError('Ambiguous readonly proposal parent')
                root = parents[0]
            leaf, history = clarification_leaf(session.registry, root)
            if leaf in rows and key in [*history, leaf]:
                result.add(identity)
        except (*READ_ERRORS, AttributeError):
            continue  # Keep the original blocked/unknown recovery projection for invalid records.
    return result


def recovery_cards(session, state):
    links = session.registry.load('pi_task_link')
    tasks, failures = {}, {}
    cards = []

    def task(kind, identity, issue):
        link = {'task_id': identity} if kind == 'task' else links.get(kind + ':' + identity)
        if link is None:
            return {'id': None, 'status': 'not_bound', 'completed_rounds': None}
        if (not isinstance(link, dict) or set(link) != {'task_id'} or
                not isinstance(link['task_id'], str) or not TASK_ID.fullmatch(link['task_id'])):
            return {'id': None, 'status': 'blocked', 'completed_rounds': None,
                    'reason': 'Invalid original Pi task link'}
        if kind != 'task' and (not isinstance(issue, str) or not 1 <= len(issue) <= 2000):
            return {'id': link['task_id'], 'status': 'blocked', 'completed_rounds': None,
                    'reason': 'Invalid original task issue'}
        key = (link['task_id'], issue)
        if key not in tasks:
            value = {'id': link['task_id'], 'status': 'blocked', 'completed_rounds': None}
            try:
                scope, _ = session.task_planner.current_scope(issue or '')
                if kind != 'task' and not state['active'] and getattr(session, 'experience', None):
                    session.experience.verify_task(link['task_id'], issue)
                if kind != 'task' and not state['active'] and getattr(session, 'knowledge', None):
                    session.knowledge.verify_task(link['task_id'], issue)
                if kind == 'task':
                    # An unlinked checkpoint cannot authorize continuation. Compare every known
                    # scope dimension and use only its original issue hash for read-only validation.
                    scope['issue_sha256'] = session.registry.load('pi_task')[identity]['scope']['issue_sha256']
                record = session.task_planner.store._load(link['task_id'], scope)
                value.update(completed_rounds=record['revision'], status='missing')
                if record['revision']:
                    if state['active']:
                        value['status'] = 'busy'  # Never read a writer's uncommitted tail as a finished turn.
                    else:
                        with session.task_planner.store.lease(link['task_id'], scope, record['revision']) as lease:
                            descriptor = lease.descriptor()
                        value.update(status='verified', tail_uncommitted=descriptor['tail_uncommitted'])
            except READ_ERRORS as error:
                value.update(status='blocked', reason=redact(str(error)))
            tasks[key] = value
        return tasks[key]

    def latest_failure(task_id):
        if state['active'] or not task_id or not session.failure_feedback:
            # Failure verification takes the original task lease. A display poll must
            # not compete with the active claim/planning/approval writer for that lease.
            return None
        if task_id in failures:
            return failures[task_id]
        saved = session.registry.load('pi_failure_feedback')
        owned = [r for r in state['runs'] if links.get('run:' + r['run_id']) == {'task_id': task_id}]
        candidates = [r for r in owned if r['status'] == 'candidate_ready']
        candidate = None
        if len(candidates) == 1:
            try:
                candidate = RepositoryGateway(session._run_dir(candidates[0]['run_id']) / 'workspace')
            except READ_ERRORS:
                pass  # Existing feedback verifier will reject an invalid source/candidate snapshot.
        result = None
        for run in sorted(owned, key=lambda r: str(r.get('recorded_at') or ''), reverse=True):
            identity = run['run_id']
            if identity not in saved:
                continue
            result = {'run_id': identity, 'status': 'blocked'}
            try:
                receipt = session.failure_feedback.prepare(identity, candidate=candidate)
                if receipt is None or receipt['task_id'] != task_id:
                    raise SafetyError('Failure feedback does not belong to this task')
                payload = receipt['payload']
                result.update(status='verified', failed_round=payload['failed_round'],
                              test_exits=[t['returncode'] for t in payload['tests']],
                              diagnosis=payload['diagnosis'][:400],
                              excerpt=redact('\n'.join((t['stderr'] or t['stdout'])[-500:]
                                                      for t in payload['tests']))[:1000])
            except READ_ERRORS as error:
                result['reason'] = redact(str(error))
            break
        failures[task_id] = result
        return result

    linked_ids = {link['task_id'] for link in links.values()
                  if isinstance(link, dict) and set(link) == {'task_id'}
                  and isinstance(link['task_id'], str) and TASK_ID.fullmatch(link['task_id'])}
    readonly = _validation_tasks(session)
    unlinked_tasks = [{'id': identity} for identity in session.registry.load('pi_task')
                      if identity not in linked_ids and identity not in readonly]
    for kind, entries in (('plan', state['plans']), ('run', state['runs']), ('task', unlinked_tasks)):
        for item in entries:
            identity = item.get('id') if kind in {'plan', 'task'} else item['run_id']
            issue = item.get('plan', {}).get('issue') if kind == 'plan' else item.get('issue')
            checkpoint = task(kind, identity, issue) if session.task_planner else {
                'id': None, 'status': 'not_bound', 'completed_rounds': None}
            card = {'kind': kind, 'id': identity, 'task': checkpoint,
                    'latest_failure': latest_failure(checkpoint['id']),
                    'budget': {k: state[k] for k in ('request_budget', 'requests_used',
                                                   'requests_reserved_unknown', 'requests_remaining')},
                    'sources': {'memory': state['source_memory'].get('status', 'disabled'),
                                'structure': state['structure_index'].get('status', 'disabled')},
                    'compression_enabled': False, 'actions': [], 'status': 'blocked',
                    'reason': '', 'new_approval_required': True}
            if kind == 'task':
                card.update(status='registration_pending', reason='No original plan or run is bound to this checkpoint; inspect the original attempt')
            elif kind == 'plan':
                if item.get('status') == 'awaiting_approval':
                    card.update(status='review_plan', reason='Review the current plan below; claim historical plans before a new approval')
                else:
                    card.update(status='plan_completed', reason='This plan has been handled; inspect its original run below')
            elif item.get('feedback_cleanup'):
                card.update(status='cleanup_pending', reason='Failure cleanup transition needs explicit reconciliation')
                card['actions'] = [{'action': 'reconcile_feedback_cleanup', 'revision': item['feedback_cleanup']['revision']}]
            elif item.get('failure_recovery') and not item['failure_recovery'].get('blocked'):
                card.update(status='feedback_pending', reason='Finished failure feedback needs explicit recovery')
                if card['latest_failure'] is None:
                    card['latest_failure'] = {'run_id': identity, 'status': 'sealed',
                        'failed_round': item['failure_recovery']['failed_round'],
                        'test_exits': item['failure_recovery']['test_exits']}
                card['actions'] = [{'action': 'recover_feedback', 'revision': item['failure_recovery']['revision']}]
            elif item.get('continuation', {}).get('available'):
                card.update(status='available', reason='Generate a new plan for this same task; new approval required')
                card['actions'] = [{'action': 'resume_run', 'revision': item['continuation']['revision'], 'mode': 'approval'}]
                if item['continuation']['auto_allowed']:
                    card['actions'].append({'action': 'resume_run', 'revision': item['continuation']['revision'], 'mode': 'auto'})
            elif item.get('repair_finished') is False:
                card.update(status='repair_pending', reason='Inspect the unfinished repair using the original recovery controls below')
            elif item.get('status') == 'candidate_ready':
                card.update(status='candidate_pending', reason='The candidate passed tests; review it before a separate publication approval')
            else:
                card['reason'] = item.get('continuation', {}).get('reason', 'Inspect the original evidence below')
            if checkpoint['status'] in {'not_bound', 'missing', 'blocked'}:
                card.update(status=checkpoint['status'], reason=checkpoint.get('reason') or
                            ('Historical record has no native task binding' if checkpoint['status'] == 'not_bound' else
                             'No completed planning checkpoint exists'), actions=[])
            if card['sources']['memory'] == 'invalid' or card['sources']['structure'] == 'invalid':
                card.update(status='source_invalid', reason='Required sources changed; verify the original sources first', actions=[])
            if state['requests_reserved_unknown']:
                card.update(status='unknown', reason='Unknown consumption is retained; inspect the original attempt evidence', actions=[])
            elif state['request_budget'] and state['requests_remaining'] <= 0:
                card['actions'] = [a for a in card['actions'] if a['action'] != 'resume_run']
                if card['status'] == 'available':
                    card.update(status='budget_exhausted', reason='No request capacity remains; review the original budget before a new plan')
            if state['runtime']['code_changed']:
                card.update(status='runtime_changed', reason='Restart the service with its original journal before a new operation', actions=[])
            if state['active']:
                card.update(status='busy', reason='An operation is active; completed checkpoints and consumption are retained', actions=[])
            cards.append(card)
    return cards
