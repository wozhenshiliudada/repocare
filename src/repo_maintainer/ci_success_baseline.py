"""Compare complete, trusted exact-attempt CI observations without executing code.

The caller supplies observations from its bounded CI reader and an authenticated
read-only compare callback. Hash consistency is not authentication: never feed
Issue/LLM assertions here as trusted observations. No controller/schema changes.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from urllib.parse import quote

from .ci_evidence import _redact, _terminal_text
from .dashboard import unlinked
from .github_connection import GitHubConnection, github_url, branch_name
from .issue_intake import fingerprint
from .store import redact


class Incomparable(ValueError):
    pass


def need(value, reason):
    if not value:
        raise Incomparable(reason)


def positive(value):
    return type(value) is int and 0 < value <= 2**53-1


def sha(value, n=40):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{'+str(n)+'}', value) is not None


def stamp(value):
    need(isinstance(value, str) and len(value) <= 40, 'missing_timestamp')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise Incomparable('invalid_timestamp') from None
    need(result.tzinfo is not None, 'timestamp_without_timezone')
    return result.astimezone(timezone.utc)


def path(value):
    return (isinstance(value, str) and 0 < len(value) <= 512 and not value.startswith(('/', '\\'))
            and not any(c in value for c in ('\\', ':', '\x00', '*', '?', '#', '\n', '\r'))
            and all(part not in ('', '.', '..') for part in value.rstrip('/').split('/')))


def snapshot(value, *, success):
    need(isinstance(value, dict), 'missing_observation')
    need(len(json.dumps(value, ensure_ascii=False).encode()) <= 512*1024, 'observation_too_large')
    need(type(value.get('synthetic', False)) is bool, 'invalid_observation_origin')
    receipt_proof = value.get('original_receipt_proof')
    need(receipt_proof is None or isinstance(receipt_proof, dict)
         and set(receipt_proof) == {'sha256', 'observation_sha256'}
         and all(sha(d, 64) for d in receipt_proof.values()), 'invalid_original_receipt_proof')
    try:
        slug, _ = github_url('https://github.com/'+value['repository'])
        branch_name(value['branch'])
    except Exception:
        raise Incomparable('invalid_repository_or_branch') from None
    need(sha(value.get('sha')), 'missing_exact_sha')
    workflow, run, job = (value.get(key) for key in ('workflow', 'run', 'job'))
    need(all(isinstance(obj, dict) for obj in (workflow, run, job)), 'missing_workflow_run_job')
    need(positive(workflow.get('id')) and path(workflow.get('path'))
         and workflow['path'].startswith('.github/workflows/') and sha(workflow.get('content_sha256'), 64),
         'missing_workflow_content')
    need(positive(run.get('id')) and positive(run.get('attempt')) and positive(job.get('id')),
         'missing_run_attempt_job_identity')
    need(str(run.get('repository', '')).casefold() == slug.casefold()
         and run.get('branch') == value['branch'] and run.get('sha') == value['sha']
         and run.get('workflow_id') == workflow['id'] and job.get('run_id') == run['id']
         and job.get('attempt') == run['attempt'], 'run_job_membership_mismatch')
    need(run.get('event') in {'push', 'pull_request', 'workflow_dispatch', 'schedule'}, 'unknown_run_event')
    expected = {'success'} if success else {'failure', 'timed_out'}
    need(run.get('status') == 'completed' and run.get('conclusion') in expected,
         'historical_run_not_success' if success else 'current_run_not_failure')
    need(job.get('status') == 'completed' and job.get('conclusion') in expected,
         'historical_job_not_success' if success else 'current_job_not_failure')
    need(isinstance(job.get('name'), str) and 0 < len(job['name']) <= 256, 'missing_job_name')
    need(positive(job.get('runner_id')), 'job_has_no_runner')
    started, finished = stamp(run.get('started_at')), stamp(run.get('completed_at'))
    job_started, job_finished = stamp(job.get('started_at')), stamp(job.get('completed_at'))
    need(started <= job_started <= job_finished <= finished, 'invalid_run_job_time_order')
    environment, dependencies = value.get('environment'), value.get('dependencies')
    need(isinstance(environment, dict) and set(environment) ==
         {'os', 'arch', 'runtime', 'runtime_version', 'image', 'image_version'}
         and all(isinstance(v, str) and 0 < len(v) <= 200 for v in environment.values()),
         'missing_actual_environment')
    need(isinstance(dependencies, dict) and set(dependencies) ==
         {'manifest_sha256', 'lock_sha256', 'resolved_sha256'}
         and all(sha(v, 64) for v in dependencies.values()), 'missing_resolved_dependencies')
    checks = value.get('checks')
    need(isinstance(checks, list) and 0 < len(checks) <= 30, 'missing_checks')
    names = set()
    has_failure = False
    signatures, scopes = [], [workflow['path']]
    for check in checks:
        need(isinstance(check, dict), 'invalid_check')
        name, argv, scope = (check.get(k) for k in ('name', 'argv', 'scope'))
        need(isinstance(name, str) and 0 < len(name) <= 200 and name not in names, 'invalid_check_identity')
        names.add(name)
        need(isinstance(argv, list) and 0 < len(argv) <= 100 and
             all(isinstance(arg, str) and 0 < len(arg) <= 1000 and '\x00' not in arg for arg in argv),
             'missing_actual_command')
        need(isinstance(scope, list) and 0 < len(scope) <= 100 and all(path(p) for p in scope), 'missing_check_scope')
        need(check.get('status') == 'completed' and positive(check.get('executed_tests'))
             and check.get('conclusion') in ({'success'} if success else {'success', 'failure', 'timed_out'}),
             'missing_executed_checks')
        has_failure |= check['conclusion'] in {'failure', 'timed_out'}
        signatures.append({'name': name, 'argv': argv, 'scope': sorted(scope)})
        scopes += scope
    need(success or has_failure, 'current_failure_has_no_failed_check')
    evidence = value.get('evidence')
    need(isinstance(evidence, dict) and evidence.get('complete') is True, 'incomplete_evidence')
    for key in ('workflow', 'run', 'job', 'checks', 'environment', 'dependencies'):
        need(evidence.get(key+'_sha256') == fingerprint(value[key]), 'evidence_digest_mismatch_'+key)
    return dict(repository=slug.casefold(), branch=value['branch'], sha=value['sha'], workflow=workflow,
                run_id=run['id'], attempt=run['attempt'], job_id=job['id'], job_name=job['name'],
                event=run['event'], started=started, finished=finished, environment=environment,
                dependencies=dependencies, checks=sorted(signatures, key=lambda c: c['name']), scopes=scopes,
                evidence_sha256=fingerprint(evidence), synthetic=value.get('synthetic', False),
                original_receipt_proof=copy.deepcopy(receipt_proof))


def provenance(s):
    return {k: copy.deepcopy(s[k]) for k in ('repository', 'branch', 'sha', 'workflow', 'run_id',
                                           'attempt', 'job_id', 'job_name', 'evidence_sha256', 'synthetic',
                                           'original_receipt_proof')}


def diff_evidence(raw, base, current, known_secrets):
    need(isinstance(raw, dict) and raw.get('base_commit', {}).get('sha') == base['sha']
         and raw.get('merge_base_commit', {}).get('sha') == base['sha'], 'history_not_confirmed_ancestor')
    status = raw.get('status')
    need(status in {'ahead', 'identical'}, 'history_not_confirmed_ancestor')
    commits, files = raw.get('commits'), raw.get('files')
    total = raw.get('total_commits')
    need(isinstance(commits, list) and type(total) is int and 0 <= total <= 250 and len(commits) == total,
         'commit_comparison_incomplete')
    if base['sha'] == current['sha']:
        need(status == 'identical' and commits == [] and files == [], 'identical_compare_mismatch')
    else:
        need(status == 'ahead' and commits and commits[-1].get('sha') == current['sha']
             and all(isinstance(c, dict) and sha(c.get('sha')) for c in commits), 'compare_head_mismatch')
    # Exactly 300 files may be GitHub's truncated list; conservatively refuse it.
    need(isinstance(files, list) and len(files) < 300, 'file_comparison_incomplete')
    projected = []
    patch_bytes = 0
    for item in files:
        need(isinstance(item, dict) and path(item.get('filename')), 'invalid_diff_path')
        old = item.get('previous_filename')
        need(old is None or path(old), 'invalid_previous_diff_path')
        relevant = any(p == item['filename'] or p == old or
                       item['filename'].startswith(p.rstrip('/')+'/') or
                       old is not None and old.startswith(p.rstrip('/')+'/') for p in current['scopes'])
        if relevant:
            patch = item.get('patch')
            if isinstance(patch, str) and len(patch.encode()) <= 16000 and patch_bytes+len(patch.encode()) <= 64000:
                patch_bytes += len(patch.encode())
                patch = redact(_redact(_terminal_text(patch), known_secrets))
            else:
                patch = None
            projected.append({'filename': item['filename'], 'previous_filename': old,
                              'status': item.get('status'), 'patch': patch,
                              'patch_status': 'available' if patch is not None else 'unavailable_or_bounded',
                              'url': 'https://github.com/'+current['repository']+'/compare/'+base['sha']+'...'+current['sha']})
    return {'base_sha': base['sha'], 'head_sha': current['sha'], 'total_commits': total,
            'changed_files': len(files), 'relevant_files': projected,
            'url': 'https://github.com/'+current['repository']+'/compare/'+base['sha']+'...'+current['sha'],
            'interpretation': '相关变化仅为调查线索，不能仅凭差异断言某次提交是根因。'}


def compare_success(current, history, compare, *, known_secrets=()):
    """Return the latest proven comparable success or explicit reasons, never old green by default.

    compare(repository, base_sha, head_sha) must GET the exact GitHub compare data.
    History is bounded and the caller must disclose whether its search was complete.
    """
    result = {'status': 'unconfirmed', 'message': '无法确认可比较基线', 'baseline': None,
              'rejections': [], 'diff': None, 'search_scope': '仅限调用方提供的有界历史，非全仓历史保证'}
    try:
        need(isinstance(known_secrets, tuple) and all(isinstance(s, str) and s for s in known_secrets), 'invalid_known_secrets')
        c = snapshot(current, success=False)
    except (Incomparable, KeyError, TypeError, ValueError) as error:
        result['rejections'].append({'source': 'current', 'reason': str(error) if isinstance(error, Incomparable) else 'invalid_observation'})
        return result
    result['current'] = provenance(c)
    if not isinstance(history, list) or len(history) > 100:
        result['rejections'].append({'source': 'history', 'reason': 'history_bound_exceeded_or_invalid'})
        return result
    candidates = []
    for index, value in enumerate(history):
        try:
            h = snapshot(value, success=True)
            for key in ('repository', 'branch', 'workflow', 'job_name', 'event', 'environment', 'dependencies', 'checks', 'synthetic'):
                need(h[key] == c[key], 'different_'+key)
            need(h['finished'] < c['started'], 'success_not_before_current_attempt')
            candidates.append(h)
        except (Incomparable, KeyError, TypeError, ValueError) as error:
            result['rejections'].append({'source': index, 'reason': str(error) if isinstance(error, Incomparable) else 'invalid_observation'})
    candidates.sort(key=lambda v: (v['finished'], v['run_id'], v['attempt']), reverse=True)
    if not candidates and not history:
        result['rejections'].append({'source': 'history', 'reason': 'no_historical_observations'})
    for h in candidates:
        try:
            diff = diff_evidence(compare(c['repository'], h['sha'], c['sha']), h, c, known_secrets)
        except Exception as error:
            result['rejections'].append({'source': provenance(h), 'reason': str(error) if isinstance(error, Incomparable) else 'compare_unavailable'})
            continue
        result.update(status='comparable', message='已确认所提供历史范围内最近的可比较成功检查',
                      baseline=provenance(h), diff=diff,
                      comparison={'checks': copy.deepcopy(c['checks']), 'environment': copy.deepcopy(c['environment']),
                                  'dependencies': copy.deepcopy(c['dependencies'])})
        return result
    return result


class GitHubComparableHistory(GitHubConnection):
    """Narrow GET-only discovery. API green alone is never a comparable snapshot."""
    def successful_runs(self, repository, branch, workflow_id):
        slug, _ = github_url('https://github.com/'+repository)
        branch_name(branch)
        need(positive(workflow_id), 'invalid_workflow_identity')
        rows, seen, total = [], set(), None
        for page in range(1, 4):
            raw = self._request(f'/repos/{slug}/actions/workflows/{workflow_id}/runs'
                                f'?branch={quote(branch, safe="")}&status=success&per_page=30&page={page}')
            need(isinstance(raw, dict) and type(raw.get('total_count')) is int and raw['total_count'] >= 0
                 and isinstance(raw.get('workflow_runs'), list)
                 and len(raw['workflow_runs']) <= 30, 'invalid_workflow_runs_response')
            need(total is None or total == raw['total_count'], 'workflow_run_page_count_changed')
            total = raw['total_count']
            for run in raw['workflow_runs']:
                need(isinstance(run, dict) and positive(run.get('id')) and positive(run.get('run_attempt'))
                     and sha(run.get('head_sha')) and run.get('head_branch') == branch
                     and run.get('workflow_id') == workflow_id and run.get('status') == 'completed'
                     and run.get('conclusion') == 'success'
                     and isinstance(run.get('repository'), dict)
                     and str(run['repository'].get('full_name', '')).casefold() == slug.casefold(),
                     'workflow_run_scope_mismatch')
                need(run['id'] not in seen, 'duplicate_workflow_run_page')
                seen.add(run['id'])
                rows.append(run)
            need(len(rows) <= total, 'workflow_run_count_mismatch')
            if len(rows) == total:
                return {'runs': rows, 'complete': True, 'reason': None}
            if len(raw['workflow_runs']) < 30:
                need(False, 'workflow_run_pagination_incomplete')
        return {'runs': rows, 'complete': False, 'reason': 'history_window_bound'}

    def compare(self, repository, base_sha, head_sha):
        slug, _ = github_url('https://github.com/'+repository)
        need(sha(base_sha) and sha(head_sha), 'invalid_comparison_shas')
        return self._request(f'/repos/{slug}/compare/{base_sha}...{head_sha}')

    def run_attempt(self, repository, run_id, attempt):
        slug, _ = github_url('https://github.com/'+repository)
        need(positive(run_id) and positive(attempt) and attempt <= 10000, 'invalid_run_attempt')
        value = self._request(f'/repos/{slug}/actions/runs/{run_id}/attempts/{attempt}')
        need(isinstance(value, dict) and value.get('id') == run_id and value.get('run_attempt') == attempt
             and isinstance(value.get('repository'), dict)
             and str(value['repository'].get('full_name', '')).casefold() == slug.casefold(),
             'current_run_scope_changed')
        return value


def investigate_success(current, remote, hydrate, *, known_secrets=()):
    """Read candidate runs, hydrate original job/check/environment evidence, then compare.

    hydrate(run) is a trusted read-only adapter returning zero or more original
    job snapshots. Missing evidence stays missing. It must not run old code or CI.
    """
    result = compare_success(current, [], remote.compare, known_secrets=known_secrets)
    if 'current' not in result:
        return result
    c = result['current']
    try:
        found = remote.successful_runs(c['repository'], c['branch'], c['workflow']['id'])
        need(isinstance(found, dict) and isinstance(found.get('runs'), list) and len(found['runs']) <= 90,
             'invalid_history_discovery')
    except Exception as error:
        result['rejections'] = [{'source': 'history', 'reason': str(error)
                                 if isinstance(error, Incomparable) else 'history_discovery_unavailable'}]
        return result
    snapshots, rejected = [], []
    for run in found['runs']:
        try:
            jobs = hydrate(copy.deepcopy(run))
            need(isinstance(jobs, list) and 0 < len(jobs) <= 30, 'historical_job_details_missing')
            for job in jobs:
                s = snapshot(job, success=True)
                need(s['run_id'] == run['id'] and s['attempt'] == run['run_attempt']
                     and s['sha'] == run['head_sha'] and s['branch'] == run['head_branch']
                     and s['workflow']['id'] == run['workflow_id']
                     and s['repository'] == c['repository'], 'hydrated_run_scope_mismatch')
            snapshots.extend(jobs)
        except Exception as error:
            rejected.append({'source': run.get('id') if isinstance(run, dict) else None,
                             'reason': str(error) if isinstance(error, Incomparable) else 'historical_details_unavailable'})
    result = compare_success(current, snapshots, remote.compare, known_secrets=known_secrets)
    result['history_search'] = {'runs_observed': len(found['runs']), 'complete': found.get('complete') is True,
                               'reason': None if found.get('complete') is True else 'history_window_bound'}
    result['rejections'] += rejected
    return result


def original_ci_binding(packet):
    """Stable original-reader membership, not observation timestamps or log prose."""
    need(isinstance(packet, dict) and packet.get('schema_version') == 1
         and type(packet.get('synthetic')) is bool
         and packet.get('read_only') is True and packet.get('evidence_status') == 'ready'
         and packet.get('errors') == [], 'original_ci_observation_incomplete')
    coverage, jobs = packet.get('coverage'), packet.get('jobs')
    need(isinstance(coverage, dict) and coverage.get('has_next') is False
         and coverage.get('omitted_jobs') == 0 and coverage.get('steps_truncated') is False
         and isinstance(jobs, list) and 0 < len(jobs) <= 100
         and coverage.get('captured_jobs') == len(jobs) == coverage.get('total_jobs'),
         'original_ci_jobs_incomplete')
    need(isinstance(packet.get('repository'), dict) and positive(packet['repository'].get('id'))
         and isinstance(packet.get('run'), dict), 'original_ci_identity_missing')
    run = packet['run']
    need(positive(run.get('id')) and positive(run.get('attempt')) and positive(run.get('workflow_id'))
         and sha(run.get('head_sha')), 'original_ci_identity_missing')
    identities = set()
    projected = []
    for job in jobs:
        need(isinstance(job, dict) and positive(job.get('id')) and job['id'] not in identities
             and job.get('run_id') == run['id'] and job.get('run_attempt') == run['attempt']
             and job.get('head_sha') == run['head_sha'], 'original_ci_job_membership_changed')
        identities.add(job['id'])
        projected.append({key: copy.deepcopy(job.get(key)) for key in
                          ('id', 'run_id', 'run_attempt', 'head_sha', 'name', 'status', 'conclusion', 'steps')})
    return {'repository': copy.deepcopy(packet['repository']), 'run': {key: run.get(key) for key in
            ('id', 'attempt', 'workflow_id', 'head_sha', 'status', 'conclusion')},
            'jobs': sorted(projected, key=lambda job: job['id']), 'synthetic': packet.get('synthetic') is True}


class OriginalCiReceiptSource:
    """Opt-in adapter for already-reviewed, hash-pinned original CI receipts.

    Pins and root are trusted server configuration, never Issue/model fields.
    This class neither creates receipts nor imports/downloads unreviewed files.
    observe(url, run_id, attempt) must call the existing CiEvidenceReader only.
    A pinned receipt supplies actual commands/environment/dependencies missing
    from GitHub metadata; absent receipts remain unconfirmed.
    """
    def __init__(self, root, pins, observe, *, repository, branch, workflow_id, job_name):
        self.root = unlinked(Path(root))
        need(self.root.is_dir(), 'original_ci_receipt_root_unavailable')
        self.repository = github_url('https://github.com/'+repository)[0].casefold()
        branch_name(branch)
        need(positive(workflow_id) and isinstance(job_name, str) and 0 < len(job_name) <= 256,
             'invalid_original_ci_scope')
        need(isinstance(pins, dict) and len(pins) <= 100 and all(
             isinstance(k, str) and re.fullmatch(r'[1-9][0-9]*-[1-9][0-9]*-[1-9][0-9]*\.json', k)
             and sha(v, 64) for k, v in pins.items()), 'invalid_original_ci_receipt_pins')
        need(callable(observe), 'original_ci_reader_required')
        self.pins, self.observe = MappingProxyType(dict(pins)), observe
        self.branch, self.workflow_id, self.job_name = branch, workflow_id, job_name

    def _receipt(self, run_id, attempt, job_id):
        filename = f'{run_id}-{attempt}-{job_id}.json'
        need(filename in self.pins, 'original_ci_receipt_not_approved')
        try:
            path = unlinked(self.root/filename)
            need(path.is_file(), 'original_ci_receipt_missing')
            with path.open('rb') as stream:
                raw = stream.read(512*1024+1)
            need(len(raw) <= 512*1024, 'original_ci_receipt_too_large')
            need(hashlib.sha256(raw).hexdigest() == self.pins[filename], 'original_ci_receipt_changed')
            receipt = json.loads(raw.decode('utf-8'))
        except Incomparable:
            raise
        except Exception:
            raise Incomparable('original_ci_receipt_unavailable') from None
        need(isinstance(receipt, dict) and receipt.get('schema_version') == 1
             and receipt.get('kind') == 'repocare-original-ci-job', 'original_ci_receipt_invalid')
        return receipt

    def for_run(self, run, *, success):
        need(isinstance(run, dict) and positive(run.get('id')) and positive(run.get('run_attempt'))
             and sha(run.get('head_sha')) and run.get('head_branch') == self.branch
             and run.get('workflow_id') == self.workflow_id
             and isinstance(run.get('repository'), dict)
             and str(run['repository'].get('full_name', '')).casefold() == self.repository,
             'original_ci_discovery_scope_changed')
        try:
            packet = self.observe('https://github.com/'+self.repository, run['id'], run['run_attempt'])
        except Exception:
            raise Incomparable('original_ci_observation_unavailable') from None
        binding = original_ci_binding(packet)
        source = binding['run']
        need(binding['repository'].get('slug', '').casefold() == self.repository
             and binding['repository']['id'] == run['repository'].get('id')
             and source['id'] == run['id'] and source['attempt'] == run['run_attempt']
             and source['workflow_id'] == self.workflow_id and source['head_sha'] == run['head_sha']
             and source['status'] == run.get('status') and source['conclusion'] == run.get('conclusion'),
             'original_ci_observation_scope_changed')
        matches = [job for job in binding['jobs'] if job['name'] == self.job_name]
        need(len(matches) == 1, 'original_ci_selected_job_missing_or_ambiguous')
        job = matches[0]
        need(isinstance(job['steps'], list) and job['steps']
             and any(isinstance(step, dict) and step.get('status') == 'completed'
                     and step.get('conclusion') in {'success', 'failure', 'timed_out'} for step in job['steps']),
             'original_ci_job_has_no_executed_steps')
        receipt = self._receipt(run['id'], run['run_attempt'], job['id'])
        need(receipt.get('repository_id') == binding['repository']['id']
             and receipt.get('observation_sha256') == fingerprint(binding), 'original_ci_receipt_observation_changed')
        value = receipt.get('snapshot')
        s = snapshot(value, success=success)
        need(value.get('synthetic', binding['synthetic']) == binding['synthetic'], 'original_ci_receipt_origin_changed')
        need(s['repository'] == self.repository and s['branch'] == self.branch and s['sha'] == source['head_sha']
             and s['workflow']['id'] == self.workflow_id and s['run_id'] == source['id']
             and s['attempt'] == source['attempt'] and s['job_id'] == job['id'] and s['job_name'] == job['name']
             and s['event'] == run.get('event')
             and value['run']['status'] == source['status'] and value['run']['conclusion'] == source['conclusion']
             and value['job']['status'] == job['status'] and value['job']['conclusion'] == job['conclusion'],
             'original_ci_receipt_scope_changed')
        value = copy.deepcopy(value)
        value['synthetic'] = binding['synthetic']
        value['original_receipt_proof'] = {'sha256': self.pins[f"{run['id']}-{run['run_attempt']}-{job['id']}.json"],
                                           'observation_sha256': fingerprint(binding)}
        return value

    def hydrate(self, run):
        return [self.for_run(run, success=True)]


def baseline_for_packet(packet, *, sources=None, remote=None):
    """Read-only workbench/monitor bridge. Sources are trusted server objects only.

    With no reviewed runtime receipts, show the precise missing evidence instead
    of selecting an arbitrary green. Nothing in the HTTP request registers pins.
    """
    result = {'status': 'unconfirmed', 'message': '无法确认可比较基线', 'baseline': None, 'diff': None,
              'rejections': [], 'current': None}
    try:
        need(isinstance(packet, dict), 'current_ci_observation_missing')
        metadata = copy.deepcopy(packet)
        # A missing/blocked log does not erase an otherwise complete metadata
        # observation; the receipt reader independently rechecks its membership.
        if metadata.get('evidence_status') == 'partial' and metadata.get('errors') and all(
                e.get('stage') == 'log' for e in metadata['errors']):
            metadata['errors'], metadata['evidence_status'] = [], 'ready'
        b = original_ci_binding(metadata)
        run, repository = b['run'], b['repository']
        selected = packet.get('selection', {}).get('job_id')
        jobs = [j for j in b['jobs'] if j['id'] == selected] if selected else [
            j for j in b['jobs'] if j['status'] == 'completed' and j['conclusion'] in {'failure', 'timed_out'}]
        need(len(jobs) == 1, 'select_one_failed_job_for_comparison')
        job = jobs[0]
        result['current'] = {'repository':repository['slug'], 'branch':None, 'sha':run['head_sha'],
            'workflow':{'id':run['workflow_id']}, 'run_id':run['id'], 'attempt':run['attempt'],
            'job_id':job['id'], 'job_name':job['name'], 'synthetic':b['synthetic']}
        need(run['status'] == job['status'] == 'completed' and run['conclusion'] in {'failure', 'timed_out'}
             and job['conclusion'] in {'failure', 'timed_out'}, 'current_job_is_not_completed_failure')
        need(job.get('steps') and any(step.get('status') == 'completed' and step.get('conclusion') in
             {'success', 'failure', 'timed_out'} for step in job['steps']), 'current_job_has_no_executed_steps')
        source = (sources or {}).get((repository['slug'].casefold(), run['workflow_id'], job['name']))
        need(isinstance(source, OriginalCiReceiptSource), 'missing_reviewed_commands_environment_dependency_receipt')
        remote = remote or GitHubComparableHistory()
        raw = remote.run_attempt(repository['slug'], run['id'], run['attempt'])
        need(raw.get('head_sha') == run['head_sha'] and raw.get('workflow_id') == run['workflow_id']
             and raw.get('repository', {}).get('id') == repository['id']
             and raw.get('status') == run['status'] and raw.get('conclusion') == run['conclusion'],
             'current_run_scope_changed')
        current = source.for_run(raw, success=False)
        need(current['synthetic'] == b['synthetic'], 'current_ci_origin_changed')
        result = investigate_success(current, remote, source.hydrate)
        need(len(json.dumps(result, ensure_ascii=False).encode()) <= 64*1024, 'baseline_projection_limit')
        return result
    except Exception as error:
        reason = str(error) if isinstance(error, Incomparable) else 'baseline_evidence_unavailable'
        result.update(status='unconfirmed', message='无法确认可比较基线', baseline=None, diff=None)
        result['rejections'] = [{'source': 'current', 'reason': reason}]
        return result
