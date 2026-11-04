"""Bounded, read-only CI observations with exact-attempt membership provenance."""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from datetime import datetime, timezone

from .ci_transport import (CiJsonPage, CiLimits, CiLogCapture, CiReadBudget,
                           CiReadError, CiTransport)
from .github_connection import github_url


_MAX_ID = 2**53 - 1
_SHA = re.compile(r'[a-f0-9]{40}')
_BEARER = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9_./+=:\-]+')
_TOKEN = re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b')
_ASSIGNMENT = re.compile(
    r'''(?i)\b(token|password|secret|api[_-]?key|authorization)\b\s*[:=]\s*(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)''')
_URL_QUERY = re.compile(r'''https?://[^\s<>"']*\?[^\s<>"']*''', re.IGNORECASE)


def _positive(value, maximum=_MAX_ID):
    if type(value) is not int or not 1 <= value <= maximum:
        raise CiReadError('invalid_response')
    return value


def _object(value):
    if not isinstance(value, dict):
        raise CiReadError('invalid_response')
    return value


def _terminal_text(text: str) -> str:
    """Linear scan: drop escape/control strings, including unterminated ones."""
    output, mode = [], 'text'
    for char in text:
        if mode == 'text':
            if char == '\x1b':
                mode = 'escape'
            elif char == '\x9b':
                mode = 'csi'
            elif char in '\x9d\x90\x98\x9e\x9f':
                mode = 'osc' if char == '\x9d' else 'string'
            elif char in '\n\t' or not unicodedata.category(char).startswith('C'):
                output.append(char)
        elif mode == 'escape':
            mode = {'[': 'csi', ']': 'osc', 'P': 'string', 'X': 'string',
                    '^': 'string', '_': 'string'}.get(char, 'text')
        elif mode == 'csi':
            if '@' <= char <= '~':
                mode = 'text'
        elif mode in {'osc', 'string'}:
            if char == '\x9c' or mode == 'osc' and char == '\x07':
                mode = 'text'
            elif char == '\x1b':
                mode += '_escape'
        elif mode.endswith('_escape'):
            mode = 'text' if char == '\\' else mode.removesuffix('_escape')
    return ''.join(output)


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    text = _BEARER.sub('Bearer [REDACTED]', text)
    text = _TOKEN.sub('[REDACTED]', text)
    text = _ASSIGNMENT.sub(lambda match: match[1] + '=[REDACTED]', text)
    return _URL_QUERY.sub('[REDACTED_URL]', text)


def project_log(capture: CiLogCapture, job: dict, limits: CiLimits,
                known_secrets: tuple[str, ...] = ()) -> dict:
    if (not isinstance(capture, CiLogCapture) or not isinstance(capture.raw, bytes)
            or type(capture.truncated) is not bool or len(capture.raw) > limits.log_bytes):
        raise CiReadError('invalid_response')
    total = capture.remote_total_bytes
    if (total is not None and (type(total) is not int or total < len(capture.raw)
            or (not capture.truncated and total != len(capture.raw))
            or (capture.truncated and total <= len(capture.raw)))):
        raise CiReadError('invalid_response')
    try:
        text = capture.raw.decode('utf-8')
        replacement = False
    except UnicodeDecodeError:
        text, replacement = capture.raw.decode('utf-8', errors='replace'), True
    clean = _terminal_text(text)
    controls_removed = clean != text
    # Do not display a secret prefix cut off by the byte capture boundary.
    tail_line_omitted = capture.truncated and not clean.endswith('\n')
    if tail_line_omitted:
        clean = clean.rpartition('\n')[0]
    redacted = _redact(clean, (*known_secrets, *capture.sensitive_values))
    lines = redacted.split('\n')
    line_truncated = any(len(line) > limits.line_characters for line in lines)
    excerpt = '\n'.join(line[:limits.line_characters] for line in lines)
    excerpt_truncated = tail_line_omitted or len(excerpt) > limits.excerpt_characters
    return {
        'job_id': job['id'], 'status': 'truncated' if capture.truncated else 'captured',
        'capture_range': 'prefix', 'captured_bytes': len(capture.raw),
        'captured_sha256': hashlib.sha256(capture.raw).hexdigest(),
        'hash_scope': 'captured_raw_bytes_before_decode_redaction',
        'remote_total_bytes': total, 'capture_truncated': capture.truncated,
        'excerpt_truncated': excerpt_truncated, 'line_truncated': line_truncated,
        'decoding_replacement': replacement, 'controls_removed': controls_removed,
        'tail_line_omitted': tail_line_omitted,
        'redacted': redacted != clean, 'excerpt': excerpt[:limits.excerpt_characters],
        'source_url': job['source_url'],
        'step_attribution': 'job_log_only_not_independently_verified',
    }


class CiEvidenceReader:
    """No implicit network: callers must supply a reviewed transport adapter.

    synthetic must be set explicitly by the caller, never inferred from success.
    The workbench registers it only through its authenticated read pool.
    """

    def __init__(self, transport: CiTransport, *, synthetic: bool,
                 limits: CiLimits | None = None, clock=time.monotonic,
                 utc_now=None, known_secrets=()):
        if type(synthetic) is not bool:
            raise ValueError('Explicit observation provenance required')
        if not isinstance(known_secrets, (tuple, list)) or any(
                not isinstance(secret, str) or not secret for secret in known_secrets):
            raise ValueError('Invalid internal redaction secrets')
        self.transport, self.synthetic = transport, synthetic
        self.limits, self.clock = limits or CiLimits(), clock
        self.utc_now = utc_now or (lambda: datetime.now(timezone.utc).isoformat())
        self.known_secrets = tuple(known_secrets)

    def _text(self, value, limit=200):
        if not isinstance(value, str) or len(value) > 10000:
            raise CiReadError('invalid_response')
        return _redact(_terminal_text(value), self.known_secrets)[:limit]

    def _state(self, value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-z_]{1,100}', value):
            raise CiReadError('invalid_response')
        return self._text(value, 100)

    def _json(self, path, budget):
        budget.begin_request()
        result = self.transport.get_json(path, budget)
        budget.remaining()
        if not isinstance(result, CiJsonPage) or type(result.has_next) is not bool:
            raise CiReadError('invalid_response')
        try:
            raw = json.dumps(result.data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise CiReadError('invalid_response') from None
        if len(raw) > self.limits.json_response_bytes:
            raise CiReadError('size_limit')
        budget.remaining()
        # An adapter can pass ephemeral credential values for redaction without
        # mutating reader state shared by concurrent observations.
        def scrub(value):
            if isinstance(value, str):
                for secret in result.sensitive_values:
                    value = value.replace(secret, '[REDACTED]')
                return value
            if isinstance(value, list):
                return [scrub(item) for item in value]
            if isinstance(value, dict):
                return {key: scrub(item) for key, item in value.items()}
            return value
        return CiJsonPage(scrub(result.data), result.has_next)

    def _job(self, item, run, slug, remaining_steps, *, membership_source):
        item = _object(item)
        identity = _positive(item.get('id'))
        for key, expected in [('run_id', run['id']), ('run_attempt', run['attempt']),
                              ('head_sha', run['head_sha'])]:
            if key not in item:
                if key == 'run_attempt':
                    continue
                raise CiReadError('invalid_response')
            if type(item[key]) is not type(expected) or item[key] != expected:
                raise CiReadError('identity_mismatch')
        steps = item.get('steps')
        if not isinstance(steps, list):
            raise CiReadError('invalid_response')
        projected, numbers = [], set()
        # Even omitted steps must have distinct, valid numbers/status fields.
        for step in steps:
            step = _object(step)
            number = _positive(step.get('number'))
            if number in numbers:
                raise CiReadError('invalid_response')
            numbers.add(number)
            value = {'number': number, 'name': self._text(step.get('name'), 1000),
                     'status': self._state(step.get('status')),
                     'conclusion': None if step.get('conclusion') is None
                     else self._state(step['conclusion'])}
            if len(projected) < remaining_steps:
                projected.append(value)
        return {'id': identity, 'run_id': run['id'], 'run_attempt': run['attempt'],
                'provenance': {'membership_source': membership_source,
                               'attempt_source': 'job_field' if 'run_attempt' in item
                               else 'verified_attempt_jobs_membership'},
                'head_sha': run['head_sha'], 'name': self._text(item.get('name')),
                'status': self._state(item.get('status')),
                'conclusion': None if item.get('conclusion') is None
                else self._state(item['conclusion']), 'steps': projected,
                'source_url': f'https://github.com/{slug}/actions/runs/{run["id"]}/job/{identity}'
                }, len(steps) > len(projected)

    def observe(self, url, run_id, attempt, job_id=None, *,
                expected_repository_id=None, expected_head_sha=None):
        # Invalid caller input is rejected before transport/credential work.
        slug, _ = github_url(url)
        _positive(run_id)
        _positive(attempt, 10000)
        if job_id is not None:
            _positive(job_id)
        if expected_repository_id is not None:
            _positive(expected_repository_id)
        if expected_head_sha is not None and (not isinstance(expected_head_sha, str)
                or not _SHA.fullmatch(expected_head_sha)):
            raise CiReadError('invalid_response')
        budget, stage = CiReadBudget(self.limits, self.clock), 'repository'
        result = {
            'schema_version': 1, 'synthetic': self.synthetic,
            'selection': {'repository_slug': slug, 'run_id': run_id, 'attempt': attempt,
                          'job_id': job_id}, 'repository': None, 'run': None,
            'evidence_status': 'ready', 'observed_at': self.utc_now(),
            'completed_at': None, 'jobs': [], 'coverage': {
                'total_jobs': None, 'captured_jobs': 0, 'pages_read': 0,
                'has_next': None, 'omitted_jobs': None, 'steps_truncated': False},
            'log': None if job_id is None else {'job_id': job_id, 'status': 'blocked', 'excerpt': ''},
            'errors': [], 'limits': self.limits.public(),
            'read_only': True, 'root_cause_proven': False, 'repair_verified': False,
        }
        prefix = '/repos/' + slug
        try:
            repository = _object(self._json(prefix, budget).data)
            repo_id = _positive(repository.get('id'))
            full_name = repository.get('full_name')
            if not isinstance(full_name, str) or full_name.casefold() != slug.casefold():
                raise CiReadError('identity_mismatch')
            result['repository'] = {'id': repo_id, 'slug': slug}
            if expected_repository_id is not None and expected_repository_id != repo_id:
                raise CiReadError('identity_mismatch')
            stage = 'run'
            raw_run = _object(self._json(
                f'{prefix}/actions/runs/{run_id}/attempts/{attempt}', budget).data)
            run_repo = _object(raw_run.get('repository'))
            if any(type(raw_run.get(key)) is not int or raw_run[key] != value
                   for key, value in [('id', run_id), ('run_attempt', attempt)]):
                raise CiReadError('identity_mismatch')
            if (type(run_repo.get('id')) is not int or run_repo['id'] != repo_id
                    or not isinstance(run_repo.get('full_name'), str)
                    or run_repo['full_name'].casefold() != slug.casefold()):
                raise CiReadError('identity_mismatch')
            head = raw_run.get('head_sha')
            if not isinstance(head, str) or not _SHA.fullmatch(head):
                raise CiReadError('invalid_response')
            if expected_head_sha is not None and head != expected_head_sha:
                raise CiReadError('identity_mismatch')
            result['run'] = run = {
                'id': run_id, 'attempt': attempt,
                'workflow_id': _positive(raw_run.get('workflow_id')), 'head_sha': head,
                'status': self._state(raw_run.get('status')),
                'conclusion': None if raw_run.get('conclusion') is None
                else self._state(raw_run['conclusion']),
                'source_url': f'https://github.com/{slug}/actions/runs/{run_id}/attempts/{attempt}',
            }
            stage = 'jobs'
            coverage, seen, step_count = result['coverage'], set(), 0
            for page in range(1, self.limits.max_pages + 1):
                response = self._json(
                    f'{prefix}/actions/runs/{run_id}/attempts/{attempt}/jobs'
                    f'?per_page={self.limits.jobs_per_page}&page={page}', budget)
                data = _object(response.data)
                total, items = data.get('total_count'), data.get('jobs')
                if (type(total) is not int or not 0 <= total <= _MAX_ID or not isinstance(items, list)
                        or len(items) > self.limits.jobs_per_page):
                    raise CiReadError('invalid_response')
                if coverage['total_jobs'] is not None and total != coverage['total_jobs']:
                    coverage['total_jobs'], coverage['omitted_jobs'] = None, None
                    raise CiReadError('invalid_response')
                coverage.update(total_jobs=total, pages_read=page, has_next=response.has_next)
                validated, page_ids, page_steps, truncated = [], set(), 0, False
                for item in items:
                    job, shortened = self._job(item, run, slug,
                                              max(0, self.limits.max_steps-step_count-page_steps),
                                              membership_source=f'https://api.github.com{prefix}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page={self.limits.jobs_per_page}&page={page}')
                    if job['id'] in seen or job['id'] in page_ids:
                        raise CiReadError('invalid_response')
                    page_ids.add(job['id'])
                    validated.append(job)
                    page_steps += len(job['steps'])
                    truncated |= shortened
                if len(seen) + len(validated) > total or response.has_next and not items:
                    raise CiReadError('invalid_response')
                available = self.limits.max_jobs - len(result['jobs'])
                result['jobs'].extend(validated[:available])
                seen.update(page_ids)
                step_count += page_steps
                coverage['steps_truncated'] |= truncated
                if len(validated) > available:
                    raise CiReadError('size_limit')
                if not response.has_next:
                    if len(seen) != total:
                        raise CiReadError('invalid_response')
                    break
                if page == self.limits.max_pages or len(result['jobs']) == self.limits.max_jobs:
                    raise CiReadError('size_limit')
            if coverage['steps_truncated']:
                result['errors'].append(CiReadError('size_limit').record('steps'))
        except CiReadError as error:
            result['errors'].append(error.record(stage))
        except TimeoutError:
            result['errors'].append(CiReadError('timeout').record(stage))
        except OSError:
            result['errors'].append(CiReadError('network_error').record(stage))
        except (ValueError, TypeError, KeyError, RecursionError):
            result['errors'].append(CiReadError('invalid_response').record(stage))

        coverage = result['coverage']
        coverage['captured_jobs'] = len(result['jobs'])
        if coverage['total_jobs'] is not None:
            coverage['omitted_jobs'] = max(0, coverage['total_jobs'] - len(result['jobs']))
        identity_conflict = any(e['code'] == 'identity_mismatch' for e in result['errors'])
        if job_id is not None and result['run'] is not None and not identity_conflict:
            selected = next((job for job in result['jobs'] if job['id'] == job_id), None)
            result['log'] = {'job_id': job_id, 'status': 'blocked', 'excerpt': ''}
            try:
                if selected is None:
                    complete = coverage['has_next'] is False and not result['errors']
                    raise CiReadError('not_found' if complete else 'membership_unverified')
                stage = 'job'
                check, _ = self._job(self._json(
                    f'{prefix}/actions/jobs/{job_id}', budget).data, result['run'], slug, 0,
                    membership_source=selected['provenance']['membership_source'])
                if check['id'] != job_id:
                    raise CiReadError('identity_mismatch')
                selected['provenance']['detail_source'] = f'https://api.github.com{prefix}/actions/jobs/{job_id}'
                selected['provenance']['detail_attempt_source'] = check['provenance']['attempt_source']
                stage = 'log'
                budget.begin_request()
                captured = self.transport.get_log(f'{prefix}/actions/jobs/{job_id}/logs', budget)
                budget.remaining()
                result['log'] = project_log(captured, selected, self.limits, self.known_secrets)
                budget.remaining()
                if any(result['log'][key] for key in ('capture_truncated', 'excerpt_truncated',
                                                      'line_truncated', 'decoding_replacement')):
                    result['errors'].append(CiReadError('size_limit' if not result['log']['decoding_replacement']
                                                      else 'invalid_response').record('log'))
            except CiReadError as error:
                result['errors'].append(error.record(stage if selected is not None else 'job'))
                if stage == 'log' and error.code in {'not_found', 'logs_unavailable'}:
                    result['log']['status'] = 'unavailable'
            except TimeoutError:
                result['errors'].append(CiReadError('timeout').record(stage))
            except OSError:
                result['errors'].append(CiReadError('network_error').record(stage))
            except (ValueError, TypeError, KeyError, RecursionError):
                result['errors'].append(CiReadError('invalid_response').record(stage))
        self._status(result)
        result['completed_at'] = self.utc_now()
        self._bound(result)
        return result

    @staticmethod
    def _status(result):
        if any(e['code'] == 'identity_mismatch' for e in result['errors']):
            result['evidence_status'] = 'stale'
        elif result['errors']:
            result['evidence_status'] = 'partial' if result['run'] else 'blocked'

    def _bound(self, result):
        def size():
            return len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8'))
        if size() <= self.limits.response_bytes:
            return
        result['errors'].append(CiReadError('size_limit').record('result'))
        for job in result['jobs']:
            job['steps'] = []
        result['coverage']['steps_truncated'] = True
        if result['log'] and 'excerpt_truncated' in result['log']:
            result['log']['excerpt'] = result['log']['excerpt'][:self.limits.response_bytes // 8]
            result['log']['excerpt_truncated'] = True
        while result['jobs'] and size() > self.limits.response_bytes:
            result['jobs'].pop()
        result['coverage']['captured_jobs'] = len(result['jobs'])
        total = result['coverage']['total_jobs']
        result['coverage']['omitted_jobs'] = None if total is None else total - len(result['jobs'])
        self._status(result)
        if size() > self.limits.response_bytes:
            raise CiReadError('size_limit')
