"""Repository-local reviewed episodes. Historical data never carries execution rights."""
from dataclasses import asdict
import re
import uuid

from .dashboard import read_json
from .git import RepositoryGateway
from .models import SafetyError, digest
from .store import now, redact
from .task_session import fingerprint, repository_identity, require, TASK_ID
from .ui_backup import read_bytes

KIND = 'repository_experience'
PIN = 'experience_task'
LIMIT = 100
ERRORS = (SafetyError, OSError, ValueError, KeyError, TypeError)


def text(value, maximum):
    require(isinstance(value, str) and value.strip(), 'Experience text is missing')
    return re.sub(r'[\x00-\x1f]', ' ', redact(value))[:maximum]


class ExperienceMemory:
    def __init__(self, session):
        self.session = session
        repo = RepositoryGateway(session.repo)
        self.repository = repository_identity(repo.root, repo.common_dir)

    def enabled(self):
        saved = self.session.registry.load('experience_settings').get('default')
        require(saved is None or saved == {'schema_version': 1, 'enabled': True}
                or saved == {'schema_version': 1, 'enabled': False}, 'Invalid experience settings')
        return bool(saved and saved['enabled'])

    def policy(self):
        value = asdict(self.session.policy)
        value['timeout_seconds'] = float(value['timeout_seconds'])
        return fingerprint(value)

    def files(self):
        repo = RepositoryGateway(self.session.repo)
        paths = (*self.session.policy.allowed_paths, *self.session.provider.inner.readonly_paths)
        return {p: digest(repo.read(p)) for p in paths}

    def original(self, identity, *, version=None):
        require(version is None or type(version) is int and version in {1, 2, 3, 4},
                'Unsupported experience version')
        directory = self.session._run_dir(identity)  # Only the existing journal's registered runs.
        record, record_sha = read_json(directory/'run.json')
        execution, execution_sha = read_json(directory/'repair-execution.json')
        require(record.get('run_id') == identity and record.get('finished_at')
                and record.get('source_unchanged') is True and not execution.get('inflight'),
                'Experience requires a safely finished original run')
        require(record.get('source_repo') == str(self.session.repo),
                'Experience repository differs')
        pins = {name: digest(read_bytes(directory/name, 2*1024*1024))
                for name in ('run.json', 'repair-execution.json', 'report.md')}
        require(pins['run.json'] == record_sha and pins['repair-execution.json'] == execution_sha,
                'Experience evidence changed during capture')
        original_rounds = record.get('rounds', [])
        successful = bool(original_rounds and original_rounds[-1].get('verification') == 'passed')
        if version is None:
            version = 4 if successful else 3
        require(version != 4 or successful, 'Version4 requires a test-passed final candidate')
        paths = (*self.session.policy.allowed_paths, *self.session.provider.inner.readonly_paths)
        source_files = {p: record['source_before']['files'].get(p) for p in paths}
        require(all(isinstance(h, str) and re.fullmatch('[a-f0-9]{64}', h) for h in source_files.values()),
                'Original authorized source fingerprints are missing')
        rounds = []
        from .pi_provider import decode_plan
        for row in original_rounds:
            plan = decode_plan({'plan': row['plan']})
            require(plan.fingerprint == row.get('plan_sha256') and plan.issue == record['issue'],
                    'Experience plan fingerprint differs')
            self.session.policy.validate(plan)
            checks = row.get('tests', [])
            require(isinstance(checks, list) and all(type(t.get('returncode')) is int
                    and type(t.get('timed_out')) is bool for t in checks), 'Invalid experience tests')
            verification = row.get('verification', 'not_run')
            if verification in {'passed', 'failed'}:
                import sys
                expected = [[sys.executable if v == '{python}' else v for v in cmd] for cmd in plan.tests]
                if hasattr(self.session, 'experience_commands'):
                    expected = self.session.experience_commands(record, plan.tests)
                require([t.get('command') for t in checks] == expected and
                        (verification == 'passed') == all(t['returncode'] == 0 and not t['timed_out'] for t in checks),
                        'Experience outcome differs from actual tests')
            from .pi_revision import failure_summary
            rounds.append({'number': row['number'], 'plan_sha256': plan.fingerprint,
                           'diagnosis': text(plan.diagnosis, 500), 'verification': verification,
                           'change_excerpt': text(row.get('diff') or 'No patch applied', 1800),
                           'failure_excerpt': text(failure_summary(checks) if version >= 3 else
                               ' '.join(str(t.get('stderr') or t.get('stdout') or '')
                               for t in checks) or 'No test output', 1600),
                           'paths': [e.path for e in plan.edits],
                           'test_exits': [t['returncode'] for t in checks],
                           'test_timeouts': [t['timed_out'] for t in checks]})
            if version == 4 and verification == 'passed':
                rounds[-1].pop('failure_excerpt')
                rounds[-1]['validation_summary'] = {'scope': 'selected_commands',
                    'passed': len(checks), 'total': len(checks), 'semantic_correctness_proven': False}
        require(len(rounds) <= 3, 'Experience round limit exceeded')
        outcome = ('candidate_test_passed' if rounds and rounds[-1]['verification'] == 'passed'
                   else 'test_failed' if rounds and rounds[-1]['verification'] == 'failed' else 'not_verified')
        if version == 1:
            for row in rounds:
                row.pop('change_excerpt');row.pop('failure_excerpt')
        return {'schema_version': version, 'repository_sha256': self.repository, 'run_id': identity,
                'task_id': self.session.registry.load('pi_task_link').get('run:'+identity, {}).get('task_id'),
                'classification': 'historical', 'runtime_authorized': False,
                'issue': text(record['issue'], 1000), 'base_sha': record['base_sha'],
                'recorded_status': record['status'], 'outcome': outcome, 'rounds': rounds,
                'policy_sha256': self.policy(), 'source_files': source_files, 'evidence': pins}

    def capture(self, identity):
        require(self.session.task_planner is not None, 'Experience memory requires the original Pi task journal')
        require(not self.session.budget_registry.budget()['requests_reserved_unknown'],
                'Unknown consumption must be inspected before capturing experience')
        previous = self.session.registry.load(KIND).get(identity)
        if previous and previous.get('payload') == self.original(identity,
                version=previous['payload'].get('schema_version')):
            return {'experience_id': identity, 'unchanged': True}
        value = self.original(identity)
        require(isinstance(value['task_id'], str) and TASK_ID.fullmatch(value['task_id']),
                'Experience requires an original task binding')
        if previous and previous.get('payload') == value:
            return {'experience_id': identity, 'unchanged': True}
        require(previous is not None or len(self.session.registry.load(KIND)) < LIMIT, 'Experience entry limit reached')
        entry = {'payload': value, 'payload_sha256': fingerprint(value), 'review': 'pending',
                 'review_version': (previous or {}).get('review_version', 0)+1, 'captured_at': now()}
        self.session.registry.put(KIND, identity, entry)
        return {'experience_id': identity, 'review': 'pending'}

    def validate(self, identity, entry, *, applicable=False):
        payload = entry['payload']
        require(entry['review'] in {'pending', 'accepted', 'rejected'} and type(entry['review_version']) is int
                and entry['review_version'] > 0 and entry['payload_sha256'] == fingerprint(payload)
                and payload['repository_sha256'] == self.repository and payload['run_id'] == identity
                and payload['classification'] == 'historical' and payload['runtime_authorized'] is False,
                'Experience binding or payload changed')
        directory = self.session._run_dir(identity)
        require(payload['evidence'] == {name: digest(read_bytes(directory/name, 2*1024*1024))
                for name in ('run.json', 'repair-execution.json', 'report.md')}, 'Experience original evidence changed')
        require(self.session.registry.load('pi_task_link').get('run:'+identity) == {'task_id': payload['task_id']},
                'Experience original task binding changed')
        expected = self.original(identity, version=payload.get('schema_version'))
        require(expected == payload, 'Experience projection no longer matches original facts')
        if applicable:
            require(payload['policy_sha256'] == self.policy() and set(payload['source_files']) == set(self.files()),
                    'Experience test policy or authorized paths changed')
        return payload

    def challenge(self, identity, entry):
        return fingerprint({'id': identity, 'entry': entry, 'session': self.session.token})

    def review(self, identity, revision, choice):
        require(choice in {'accepted', 'rejected'}, 'Invalid experience review decision')
        entry = self.session.registry.load(KIND).get(identity)
        require(entry and revision == self.challenge(identity, entry), 'Experience review challenge changed')
        if choice == 'accepted':
            self.validate(identity, entry)  # Acceptance does not declare applicability or correctness.
        updated = {**entry, 'review': choice, 'review_version': entry['review_version']+1}
        with self.session.registry.transaction() as db:
            import json
            row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (KIND, identity)).fetchone()
            saved = json.loads(row[0]) if row else None
            require(saved == entry, 'Experience changed during review')
            self.session.registry.put(KIND, identity, updated, db)
            self.session.registry.put('experience_review', uuid.uuid4().hex,
                {'id': identity, 'decision': choice, 'payload_sha256': entry['payload_sha256'],
                 'review_version': updated['review_version'], 'time': now()}, db)
        return {'experience_id': identity, 'review': choice}

    def view(self):
        rows = []
        for identity, entry in self.session.registry.load(KIND).items():
            row = {'id': identity, 'review': entry.get('review', 'invalid'), 'freshness': 'invalid',
                   'runtime_authorized': False, 'revision': self.challenge(identity, entry)}
            try:
                payload = self.validate(identity, entry)
                row.update(issue=payload['issue'], outcome=payload['outcome'], rounds=payload['rounds'],
                           payload_sha256=entry['payload_sha256'], task_id=payload['task_id'], freshness='verified')
                try:
                    self.validate(identity, entry, applicable=True)
                    row['applicability'] = ('matching_files_and_policy' if payload['source_files'] == self.files()
                                            else 'revalidate_changed_files')
                    if row['applicability'] == 'revalidate_changed_files':
                        row['reason'] = 'Historical source differs; independently verify current authorized files'
                except ERRORS as error:
                    row['applicability'] = 'changed';row['reason'] = redact(str(error))
            except ERRORS as error:
                row['reason'] = redact(str(error))
            rows.append(row)
        return {'enabled': self.enabled(), 'entries': rows, 'classification': 'historical',
                'capture_errors': [(i,e) for i,e in self.session.registry.load('experience_capture').items()
                                   if i not in self.session.registry.load(KIND)],
                'runtime_authorized': False, 'max_entries': LIMIT, 'scope': 'original_repository_journal'}

    def search(self, issue):
        require(isinstance(issue, str) and 0 < len(issue) <= 2000, 'Invalid experience query')
        from .semantic_retrieval import session_rank
        candidates, documents, excluded = {}, {}, []
        for identity, entry in self.session.registry.load(KIND).items():
            if entry.get('review') != 'accepted':continue
            try:
                payload = self.validate(identity, entry, applicable=True)
                documents[identity] = payload['issue']+'\n'+'\n'.join(r['diagnosis']+'\n'+'\n'.join(r['paths'])
                                      +'\n'+r.get('failure_excerpt','') for r in payload['rounds'])
                candidates[identity] = {'id': identity, 'payload_sha256': entry['payload_sha256'],
                        'review_version': entry['review_version'], 'episode': payload,
                        'applicability': 'matching_original_source' if payload['source_files'] == self.files()
                                         else 'requires_revalidation'}
            except ERRORS as error:
                excluded.append({'id': identity, 'reason': redact(str(error))})
        # Select before model reservation. Large records remain stored, not silently truncated.
        from .source_memory import canonical
        hits, size = [], 0
        ranked,_,strategy=session_rank(self.session,issue,documents)
        for _, identity in ranked:
            row = candidates[identity]
            length = len(canonical(row))
            if size + length > 40000:
                excluded.append({'id': identity, 'reason': 'Episode exceeds retrieval byte budget; full evidence retained'})
                continue
            hits.append(row);size += length
            if len(hits) == 3:break
        return {'hits': hits, 'excluded': excluded, 'runtime_authorized': False,'retrieval':strategy}

    def begin(self, identity, issue, continuation):
        if continuation or not self.enabled():return  # Old tasks never acquire new memory implicitly.
        selection = self.search(issue)
        self.session.registry.put(PIN, identity, {'schema_version': 2, 'task_id': identity,
            'source_files': self.files(), 'issue_sha256': digest(issue.encode()),
            'hits': selection['hits'], 'excluded': selection['excluded']})
        from .semantic_retrieval import record_selection
        record_selection(self.session,identity,issue,'experience',selection)

    def verify_task(self, identity, issue):
        pin = self.session.registry.load(PIN).get(identity)
        if pin is None:
            # A lost selection must not silently remove the dependencies of an existing plan.
            for plan in self.session.plans.values():
                evidence = plan['view'].get('planning_evidence', {})
                require(not (evidence.get('task_session', {}).get('task_id') == identity and
                             evidence.get('experience_context_sha256')), 'Original experience selection is missing')
            return None
        require(pin['task_id'] == identity and pin['issue_sha256'] == digest(issue.encode()),
                'Experience task selection changed')
        require(isinstance(pin['hits'], list) and len(pin['hits']) <= 3
                and len({h['id'] for h in pin['hits']}) == len(pin['hits']), 'Invalid experience task hits')
        if pin.get('schema_version') == 2:
            require(pin['source_files'] == self.files(), 'Experience task original source changed')
        for hit in pin['hits']:
            entry = self.session.registry.load(KIND).get(hit['id'])
            require(entry and entry['review'] == 'accepted' and entry['review_version'] == hit['review_version']
                    and entry['payload_sha256'] == hit['payload_sha256'], 'Pinned experience review changed')
            require(self.validate(hit['id'], entry, applicable=True) == hit['episode'], 'Pinned experience evidence changed')
        return pin

    def context(self, issue, repository, identity=None):
        planner = self.session.task_planner
        identity = identity or (planner.identity if planner else None)
        pin = self.verify_task(identity, issue) if identity else None
        if pin is None:return None
        paths = (*self.session.policy.allowed_paths, *self.session.provider.inner.readonly_paths)
        files = [{'path': p, 'content': repository.read(p).decode('utf-8'),
                  'before_sha256': digest(repository.read(p)), 'editable': p in self.session.policy.allowed_paths} for p in paths]
        # Same source snapshot encoding used by PiProvider and the Node adapter.
        import json
        version = pin.get('schema_version', 1)
        result = {'schema_version': version, 'task_id': identity, 'repository_sha256': self.repository,
                  'issue_sha256': digest(issue.encode()), 'planning_base_sha': repository.head,
                  'planning_files_sha256': digest(json.dumps(files,ensure_ascii=False,separators=(',', ':')).encode()),
                  'classification': 'historical', 'runtime_authorized': False, 'hits': pin['hits']}
        if version == 2:
            result['selection_files_sha256'] = fingerprint(pin['source_files'])
        final = {**result, 'context_sha256': fingerprint(result)}
        require(len(json.dumps(final, ensure_ascii=False).encode()) <= 48000, 'Experience context exceeds limit')
        return final

    def checked_plan(self, data, issue):
        evidence = data.get('provider_evidence', {})
        identity = evidence.get('task_session', {}).get('task_id')
        pin = self.verify_task(identity, issue) if identity else None
        if pin is None:
            require('experience_context_sha256' not in evidence, 'Unexpected experience receipt')
            return
        current = self.context(issue, RepositoryGateway(self.session.repo), identity)
        require(current['context_sha256'] == evidence.get('experience_context_sha256'),
                'Saved experience receipt no longer matches task')
