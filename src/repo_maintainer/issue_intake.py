"""Explicit, bounded read-only Issue investigation. No model, remote writes or repair."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import time
import uuid

from .dashboard import unlinked
from .git import RepositoryGateway
from .github_connection import GitHubConnection, branch_name, github_url
from .git_investigation import SHA, validate_roots
from .models import SafetyError, digest
from .repository_diagnosis import build_report
from .store import redact
from .ui_registry import UIRegistry

LOGIN = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}\Z')
STAMP = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z')
STATES = {'ready-for-agent', 'in-progress', 'blocked', 'awaiting-review', 'needs-triage'}


def fingerprint(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())


def specification(url, branch, roots, trusted_actors, triage_bugs=False):
    slug, _ = github_url(url)
    if (not isinstance(trusted_actors, (list, tuple)) or len(trusted_actors) > 20
            or any(not isinstance(a, str) or not LOGIN.fullmatch(a) for a in trusted_actors)
            or len(set(a.casefold() for a in trusted_actors)) != len(trusted_actors)
            or type(triage_bugs) is not bool):
        raise SafetyError('Invalid explicit Issue trust configuration')
    return {'schema_version': 1, 'repository': slug.casefold(), 'branch': branch_name(branch),
            'read_roots': list(validate_roots(roots)),
            'trusted_label_actors': sorted(a.casefold() for a in trusted_actors), 'triage_bugs': triage_bugs}


def project_issue(value, slug, number):
    if not isinstance(value, dict) or value.get('number') != number or type(number) is not int or not 1 <= number <= 9999999999:
        raise SafetyError('Unexpected Issue identity')
    if (value.get('html_url', '').casefold() != f'https://github.com/{slug}/issues/{number}'
            or value.get('repository_url', '').casefold() != 'https://api.github.com/repos/'+slug):
        # GitHub PR issue responses have a /pull/ URL; exclude before requiring /issues/.
        if 'pull_request' not in value:
            raise SafetyError('Issue repository identity differs')
    if 'pull_request' in value:
        return {'number': number, 'is_pull_request': True}
    title, body = value.get('title'), value.get('body') or ''
    labels = value.get('labels')
    if (not isinstance(title, str) or not isinstance(body, str) or not isinstance(labels, list)
            or len(title.encode()) > 1024 or len(body.encode()) > 32768 or len(labels) > 50
            or value.get('state') not in {'open', 'closed'} or not STAMP.fullmatch(value.get('updated_at', ''))
            or type(value.get('id')) is not int or value['id'] < 1):
        raise SafetyError('Issue exceeds its schema or text bounds')
    names = []
    for label in labels:
        name = label.get('name') if isinstance(label, dict) else None
        if not isinstance(name, str) or not 1 <= len(name) <= 100:
            raise SafetyError('Malformed Issue label')
        names.append(name.casefold())
    return {'number': number, 'id': value['id'], 'is_pull_request': False, 'state': value['state'],
            'updated_at': value['updated_at'], 'labels': sorted(set(names)),
            'title': redact(title), 'body': redact(body),
            'raw_text_sha256': fingerprint({'title': title, 'body': body})}


class IssueReader:
    def __init__(self, connection, spec, *, registry=None):
        self.connection, self.spec, self.registry = connection, spec, registry
        self.calls, self.deadline = 0, time.monotonic() + 60

    def api(self, path):
        if self.calls >= 16 or time.monotonic() >= self.deadline:
            raise SafetyError('Issue API request/time bound reached')
        self.calls += 1
        result = self.connection.api(path)
        if time.monotonic() >= self.deadline:
            raise SafetyError('Issue API time bound reached')
        return result

    def pages(self, suffix):
        rows = []
        for page in range(1, 4):
            separator = '' if suffix.endswith('?') else '&'
            value = self.api('/repos/'+self.spec['repository']+suffix+separator+f'per_page=30&page={page}')
            if not isinstance(value, list) or len(value) > 30 or any(not isinstance(r, dict) for r in value):
                raise SafetyError('Malformed bounded Issue page')
            rows += value
            if len(value) < 30:
                return rows, True
        return rows, False

    def open_issues(self):
        rows, complete = self.pages('/issues?state=open')
        refs = []
        for row in rows:
            if 'pull_request' in row:
                continue
            issue = project_issue(row, self.spec['repository'], row.get('number'))
            if issue['state'] == 'open':
                refs.append({'number': issue['number'], 'title': issue['title'], 'labels': issue['labels']})
        return {'issues': refs, 'complete': complete, 'runtime_authorized': False}

    def snapshot(self, number):
        from .issue_comment_continuity import guard, normalize
        with guard(self.registry):
            return normalize(self.raw_snapshot(number), self.registry)

    def raw_snapshot(self, number):
        slug = self.spec['repository']
        base = '/repos/'+slug
        meta = self.api(base)
        if (not isinstance(meta, dict) or str(meta.get('full_name', '')).casefold() != slug
                or type(meta.get('id')) is not int or meta['id'] < 1):
            raise SafetyError('Repository identity unavailable')
        path = base+'/issues/'+str(number)
        issue = project_issue(self.api(path), slug, number)
        if issue['is_pull_request']:
            return {'status': 'ignored_pull_request', 'runtime_authorized': False}
        events, complete = self.pages('/issues/'+str(number)+'/events?')
        relevant, identities = [], set()
        for event in events:
            identity = event.get('id')
            if type(identity) is not int or identity < 1 or identity in identities:
                raise SafetyError('Duplicate or malformed Issue event')
            identities.add(identity)
            if (event.get('event') not in {'labeled', 'unlabeled'}
                    or not isinstance(event.get('label'), dict)
                    or str(event['label'].get('name', '')).casefold() != 'ready-for-agent'):
                continue
            actor = (event.get('actor') or {}).get('login')
            if (not isinstance(actor, str) or not LOGIN.fullmatch(actor)
                    or not STAMP.fullmatch(event.get('created_at', ''))
                    or event.get('url', '').casefold() != f'https://api.github.com/repos/{slug}/issues/events/{identity}'):
                raise SafetyError('Malformed readiness event provenance')
            relevant.append({'id': identity, 'actor': actor.casefold(), 'event': event['event'],
                             'created_at': event['created_at']})
        relevant.sort(key=lambda e: (e['created_at'], e['id']))
        from urllib.parse import quote
        branch = self.api(base+'/branches/'+quote(self.spec['branch'], safe=''))
        head = branch.get('commit', {}).get('sha') if isinstance(branch, dict) else None
        if not isinstance(head, str) or not SHA.fullmatch(head):
            raise SafetyError('Target branch SHA unavailable')
        if project_issue(self.api(path), slug, number) != issue:
            raise SafetyError('Issue changed while collecting evidence')
        latest = relevant[-1] if relevant else None
        labels = set(issue['labels'])
        if issue['state'] != 'open':
            status = 'ignored_closed'
        elif 'ready-for-agent' in labels:
            if not complete:
                status = 'incomplete_events'
            elif not latest or latest['event'] != 'labeled' or latest['actor'] not in self.spec['trusted_label_actors']:
                status = 'untrusted_label'
            elif labels & (STATES - {'ready-for-agent'}):
                status = 'conflicting_status_labels'
            elif not issue['title'].strip() or not issue['body'].strip():
                status = 'needs_triage'
            else:
                status = 'ready'
        elif labels & {'in-progress', 'blocked', 'awaiting-review'}:
            status = 'not_available'
        elif 'bug' in labels and self.spec['triage_bugs']:
            status = 'triage_only'
        else:
            status = 'needs_triage'
        binding = {'repository': slug, 'repository_id': meta['id'], 'issue': issue,
                   'head_sha': head, 'readiness_events': relevant, 'events_complete': complete,
                   'spec_sha256': fingerprint(self.spec)}
        return {**binding, 'token': fingerprint(binding), 'status': status, 'runtime_authorized': False,
                'latest_readiness_event': latest}


class IssueIntake:
    """Use the existing registry's objects/transactions. Never reserve or alter its budget."""
    def __init__(self, connection, registry, spec):
        self.connection, self.registry, self.spec = connection, registry, spec

    def _observe(self, snapshot, db):
        key = fingerprint([snapshot['repository_id'], snapshot['issue']['number']])
        row = db.execute("SELECT body FROM objects WHERE kind='issue_head' AND id=?", (key,)).fetchone()
        old = json.loads(row[0]) if row else None
        if old and old['token'] != snapshot['token']:
            job = db.execute("SELECT body FROM objects WHERE kind='issue_diagnosis' AND id=?", (old['token'],)).fetchone()
            if job:
                value = json.loads(job[0])
                value.update(status='stale', reason='Issue, trust, labels or target version changed')
                self.registry.put('issue_diagnosis', old['token'], value, db)
        self.registry.put('issue_head', key, {'token': snapshot['token'], 'status': snapshot['status']}, db)
        return key

    def investigate(self, number, repository):
        if type(number) is not int or not 1 <= number <= 9999999999:
            raise SafetyError('Invalid Issue number')
        origin = repository.command('config', '--get', 'remote.origin.url').decode().strip()
        if github_url(origin)[0].casefold() != self.spec['repository']:
            raise SafetyError('Local source origin differs from Issue repository')
        reader = IssueReader(self.connection, self.spec, registry=self.registry)
        snapshot = reader.snapshot(number)
        if snapshot['status'] == 'ignored_pull_request':
            return snapshot
        token = snapshot['token']
        if self.registry.path.stat().st_size > 30 * 1024 * 1024:
            raise SafetyError('Original journal near capacity; preserve it without new claims')
        with self.registry.transaction() as db:
            key = self._observe(snapshot, db)
            if snapshot['status'] not in {'ready', 'triage_only'}:
                return {'status': snapshot['status'], 'snapshot': snapshot, 'runtime_authorized': False}
            row = db.execute("SELECT body FROM objects WHERE kind='issue_diagnosis' AND id=?", (token,)).fetchone()
            if row:
                return {'status': 'duplicate_no_replay', 'previous_status': json.loads(row[0])['status'],
                        'investigation_id': token, 'runtime_authorized': False}
            if db.execute("SELECT count(*) FROM objects WHERE kind='issue_diagnosis'").fetchone()[0] >= 100:
                raise SafetyError('Issue investigation journal capacity reached')
            job = {'id': token, 'issue_key': key, 'owner': uuid.uuid4().hex, 'status': 'started',
                   'snapshot': snapshot, 'runtime_authorized': False}
            self.registry.put('issue_diagnosis', token, job, db)
        try:
            text = snapshot['issue']['title']+'\n'+snapshot['issue']['body']
            query = text.encode()[:4096].decode('utf-8', errors='ignore')
            report = build_report(repository, head_sha=snapshot['head_sha'],
                                  read_roots=self.spec['read_roots'], search=True, query=query)
            if len(json.dumps(report, ensure_ascii=False).encode()) > 262144:
                raise SafetyError('Investigation report exceeds journal bound')
            fresh = reader.snapshot(number)
            if fresh.get('token') != token or fresh['status'] not in {'ready', 'triage_only'}:
                job.update(status='stale', reason='Remote source changed during investigation')
            else:
                job.update(status='investigated', report=report)
        except Exception as error:
            job.update(status='failed_no_replay', error_type=type(error).__name__)
        with self.registry.transaction() as db:
            current = db.execute("SELECT body FROM objects WHERE kind='issue_head' AND id=?", (key,)).fetchone()
            if not current or json.loads(current[0])['token'] != token:
                # A later observer has already advanced this Issue. Never publish an
                # older result or roll its head back, even if our last HTTP read was earlier.
                job.update(status='stale', reason='Newer observation already registered')
                job.pop('report', None)
            elif 'fresh' in locals() and fresh.get('token'):
                self._observe(fresh, db)
            self.registry.put('issue_diagnosis', token, job, db)
        return job


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--github-url', required=True)
    parser.add_argument('--branch', required=True)
    parser.add_argument('--issue', type=int, required=True)
    parser.add_argument('--read-root', action='append', default=[])
    parser.add_argument('--trusted-label-actor', action='append', default=[])
    parser.add_argument('--triage-bugs', action='store_true')
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    repo = RepositoryGateway(args.repo)
    state, output = unlinked(args.state_root), unlinked(args.output)
    if (state.is_relative_to(repo.root) or state.is_relative_to(repo.common_dir)
            or output.is_relative_to(repo.root) or output.is_relative_to(repo.common_dir)
            or output.is_relative_to(state) or output.exists()):
        raise SafetyError('Use external state and a new external report, outside the journal')
    spec = specification(args.github_url, args.branch, args.read_root, args.trusted_label_actor, args.triage_bugs)
    # Standalone read-only journal has no paid budget and cannot become a repair journal.
    registry = UIRegistry(state, {'issue_intake': spec, 'provider': {'request_budget': 0}})
    try:
        result = IssueIntake(GitHubConnection(), registry, spec).investigate(args.issue, repo)
        with output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps({'status': result['status'], 'report': str(output), 'model_calls': 0}))
    finally:
        registry.close()


if __name__ == '__main__':
    main()
