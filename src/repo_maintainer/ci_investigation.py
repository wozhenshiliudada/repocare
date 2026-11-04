"""Bridge trusted monitor selection to the existing read-only W1/W2 report.

No Issue is invented, no test result is fabricated from a CI conclusion, and no
provider, shell check, approval or second task/budget journal is created.
"""
from __future__ import annotations

import json

from .github_connection import github_url
from .issue_intake import fingerprint
from .models import SafetyError
from .repository_diagnosis import build_report
from .store import redact


def investigate_ci(repository, spec, event, reader, guard, *, baseline_reader=None):
    """Called only by the serialized monitor with its registered scope/event."""
    slug, _ = github_url(spec['github_url'])
    head = event['sha']
    failure = event['ci_failures'][0]  # One bounded observation, never fan out.
    guard()
    if repository.head != head:
        raise SafetyError('CI investigation snapshot differs from monitor event')
    packet = reader.observe(spec['github_url'], failure['id'], failure['attempt'],
                            expected_head_sha=head)

    def bound(value):
        run, repo = value.get('run'), value.get('repository')
        if (not run or not repo or repo['slug'].casefold() != slug.casefold()
                or run['id'] != failure['id'] or run['attempt'] != failure['attempt']
                or run['head_sha'] != head or value['evidence_status'] in {'blocked', 'stale'}):
            raise SafetyError('CI evidence is unavailable or differs from the registered event')
        if run['status'] != 'completed' or run['conclusion'] not in {'failure', 'timed_out', 'action_required'}:
            raise SafetyError('Selected CI attempt is no longer a completed failure')

    bound(packet)
    failed = next((j for j in packet['jobs'] if j['status'] == 'completed'
                   and j['conclusion'] in {'failure', 'timed_out', 'action_required'}), None)
    # Empty host policy stays disabled; metadata can still guide investigation.
    log_requested = bool(failed and reader.transport.policy.hosts)
    if log_requested:
        packet = reader.observe(spec['github_url'], failure['id'], failure['attempt'], failed['id'],
            expected_repository_id=packet['repository']['id'], expected_head_sha=head)
        bound(packet)
        failed = next((j for j in packet['jobs'] if j['id'] == failed['id']), None)
        if failed is None:
            raise SafetyError('Previously observed failed Job membership changed')

    steps = [s for s in (failed or {}).get('steps', []) if s.get('conclusion') in
             {'failure', 'timed_out', 'action_required'}]
    log = packet.get('log') or {}
    excerpt = redact(log.get('excerpt', ''))
    hints = '\n'.join([failure.get('name', ''), (failed or {}).get('name', ''),
                       *(s['name'] for s in steps), excerpt])
    query = hints.encode('utf-8')[:4096].decode('utf-8', errors='ignore')
    from .monitor_rules import scope
    roots, writes = scope(spec)
    report = build_report(repository, head_sha=head, read_roots=roots, search=True, query=query)
    # Never turn a remote conclusion into a made-up local command/exit code.
    evidence = {'packet_sha256': fingerprint(packet), 'repository': packet['repository'],
        'run': packet['run'], 'evidence_status': packet['evidence_status'],
        'observed_at': packet['observed_at'], 'completed_at': packet['completed_at'],
        'coverage': packet['coverage'], 'errors': packet['errors'],
        'selected_job_id': (failed or {}).get('id'),
        'job_source': (failed or {}).get('source_url'),
        'job_provenance': (failed or {}).get('provenance'),
        'failed_steps': steps[:20], 'failed_steps_omitted': max(0, len(steps)-20),
        'log_requested': log_requested, 'log_status': log.get('status', 'not_requested'),
        'log_excerpt': excerpt[:8192], 'log_excerpt_projection_truncated': len(excerpt)>8192,
        'captured_sha256': log.get('captured_sha256'),
        'capture_truncated': log.get('capture_truncated'),
        'excerpt_truncated': log.get('excerpt_truncated'),
        'complete_packet_preserved': False,
        'synthetic': packet['synthetic']}
    result = {'schema_version': 1, 'status': 'investigated', 'base_sha': head,
        'selection': {'repository': slug, 'run_id': failure['id'], 'attempt': failure['attempt']},
        'selection_sha256': fingerprint([slug.casefold(), head, failure['id'], failure['attempt']]),
        'scope_sha256': fingerprint(spec), 'ci_evidence': evidence, 'report': report,
        'authority': {'basis': 'registered_monitor_policy', 'mode': 'approval',
            'read_paths': roots, 'write_paths': list(writes),
            'test_commands': spec['allowed_tests'], 'runtime_authorized': False,
            'repair_authorized': False, 'requires_reproduction': True,
            'next_step': 'reproduce_failure' if steps else 'review_environment_or_missing_ci_evidence'},
        'model_requests': 0, 'tests_executed': False, 'root_cause_proven': False}
    from .ci_success_baseline import baseline_for_packet
    baseline_packet = {**packet, 'selection':{**packet['selection'], 'job_id':(failed or {}).get('id')}}
    try:
        result['comparable_baseline'] = (baseline_reader or baseline_for_packet)(baseline_packet)
    except Exception:
        result['comparable_baseline'] = {'status':'unconfirmed', 'message':'无法确认可比较基线',
            'baseline':None, 'diff':None, 'rejections':[{'source':'history', 'reason':'baseline_evidence_unavailable'}]}
    if len(json.dumps(result, ensure_ascii=False).encode()) > 262144:
        raise SafetyError('CI investigation exceeds original event report capacity')
    guard()
    if repository.head != head:
        raise SafetyError('Source changed during CI investigation')
    return result
