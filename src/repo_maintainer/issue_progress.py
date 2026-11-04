"""Explicitly approved, create-once Issue progress comments in the original journal.

No controller hooks. observe is a trusted server-side source guard, never Issue
text. Unknown POST outcomes are read-only forever, including after a restart.
"""
from __future__ import annotations

import html
import json
import re
from contextlib import nullcontext
from urllib.parse import urlsplit

from .ci_evidence import _redact, _terminal_text
from .github_connection import branch_name, github_url
from .issue_intake import fingerprint
from .models import SafetyError
from .store import now, redact

KIND = 'issue_progress'
STAGES = {
    'accepted': '已接手', 'investigated': '调查结果', 'blocked': '需要补充信息或受阻',
    'candidate_passed': '候选通过，尚未交付', 'awaiting_review': '待审阅，尚未交付',
    'delivered': '已交付（已有准确交付证明）',
}


class CommentPermissionDenied(SafetyError):
    """A read-only preflight proved this account lacks the required permission."""


def require(condition, message):
    if not condition:
        raise SafetyError(message)


def _id(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', value),
            'Bounded task/run/event identifier required')
    return value


def _positive(value):
    require(type(value) is int and 0 < value <= 2**53-1, 'Exact positive GitHub identifier required')
    return value


def clean_text(value, secrets):
    require(isinstance(value, str) and len(value.encode()) <= 16000, 'Bounded progress text required')
    value = redact(_redact(_terminal_text(value), secrets))
    # No URLs, local paths, HTML or active Markdown in unstructured excerpts.
    value = re.sub(r'https?://[^\s<>]+', '[链接已省略]', value, flags=re.I)
    value = re.sub(r'(?:[A-Za-z]:[\\/]|\\\\)[^\s<>]+', '[本地路径已省略]', value)
    value = re.sub(r'(?<!\w)/(?:[^\s/]+/)+[^\s]*', '[本地路径已省略]', value)
    value = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----',
                   '[REDACTED PRIVATE KEY]', value, flags=re.S)
    value = html.escape(value, quote=True)
    value = value.replace('@', '（at）')
    return re.sub(r'([\\`*_{}\[\]()#+.!|>@~-])', r'\\\1', value)


def _link(value, repository):
    require(isinstance(value, str) and len(value) <= 1000, 'Bounded public evidence link required')
    p = urlsplit(value)
    require(p.scheme == 'https' and p.netloc == 'github.com' and not p.query and not p.fragment,
            'Only fixed GitHub evidence links without query/fragment are supported')
    require(re.fullmatch('/'+re.escape(repository)+r'/(?:pull/[1-9][0-9]*|actions/runs/[1-9][0-9]*'
                         r'|tree/[A-Za-z0-9_./%-]+|blob/[A-Za-z0-9_./%-]+)', p.path)
            and '%' not in p.path and '..' not in p.path and '//' not in p.path,
            'Evidence link must belong to the exact repository')
    return value


class IssueProgress:
    def __init__(self, registry, remote, observe, *, known_secrets=(), continuity=None):
        require(isinstance(known_secrets, tuple) and all(isinstance(s, str) and s for s in known_secrets),
                'Trusted known-secrets tuple required')
        self.registry, self.remote, self.observe, self.secrets = registry, remote, observe, known_secrets
        self.continuity = continuity or getattr(observe, 'comment_continuity', None)

    def _get(self, identity, db):
        row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (KIND, identity)).fetchone()
        require(row, 'Unknown progress proposal')
        value = json.loads(row[0])
        require(value['id'] == identity and fingerprint(value['core']) == value['approval_sha256'],
                'Progress proposal integrity changed')
        return value

    def view(self, identity):
        with self.registry.transaction() as db:
            return self._get(identity, db)

    def prepare(self, repository, number, run_id, event_id, *, actor_id=None):
        slug, _ = github_url('https://github.com/'+repository)
        slug = slug.casefold()
        _positive(number)
        _id(run_id)
        _id(event_id)
        require(not any(secret in run_id or secret in event_id for secret in self.secrets),
                'Progress identifiers contain a known secret')
        facts = self.observe(slug, number, run_id, event_id)
        require(isinstance(facts, dict) and facts.get('repository') == slug
                and facts.get('issue_number') == number and facts.get('run_id') == run_id
                and facts.get('event_id') == event_id and facts.get('stage') in STAGES,
                'Trusted progress source does not match the exact scope')
        stage = facts['stage']
        if stage in {'candidate_passed', 'awaiting_review'}:
            require(facts.get('candidate_checks_verified') is True and
                    re.fullmatch('[a-f0-9]{40}', str(facts.get('candidate_sha', ''))),
                    'Candidate stage needs verified checks and exact candidate SHA')
            require(isinstance(facts.get('branch'), str), 'Candidate branch must be shown')
            branch_name(facts['branch'])
            require(isinstance(facts.get('checks'), list) and 0 < len(facts['checks']) <= 30,
                    'Candidate stage needs actual check results')
            for check in facts['checks']:
                require(isinstance(check, dict) and type(check.get('passed')) is int and check['passed'] > 0
                        and type(check.get('failed')) is int and check['failed'] == 0
                        and type(check.get('skipped')) is int and check['skipped'] >= 0
                        and isinstance(check.get('name'), str), 'Invalid or nonpassing candidate check result')
        if stage == 'delivered':
            require(facts.get('delivery_verified') is True and
                    re.fullmatch('[a-f0-9]{40}', str(facts.get('delivered_sha', '')))
                    and isinstance(facts.get('delivery_proof_sha256'), str)
                    and re.fullmatch('[a-f0-9]{64}', facts['delivery_proof_sha256']),
                    'Delivered stage needs original verified delivery proof')
        issue_state = facts.get('issue_state', 'open')
        require(issue_state in {'open', 'closed'} and (issue_state == 'open' or stage == 'delivered'),
                'Only verified delivery comments may target an already closed Issue')
        identity = fingerprint([slug, number, run_id, event_id])
        marker = '<!-- repocare-progress:'+identity+' -->'
        lines = ['RepoCare：'+STAGES[stage], '', ('调查标识：' if facts.get('reference_kind')=='investigation' else '运行：')+run_id, '事件：'+event_id, '',
                 clean_text(facts.get('summary', ''), self.secrets)]
        for key, label in [('candidate_sha', '候选 SHA'), ('delivered_sha', '交付 SHA')]:
            if key in facts:
                require(re.fullmatch('[a-f0-9]{40}', str(facts[key])), 'Invalid evidence SHA')
                lines += [label+'：'+facts[key]]
        if facts.get('branch'):
            lines += ['分支：'+clean_text(facts['branch'], self.secrets)]
        if stage in {'candidate_passed', 'awaiting_review'}:
            for check in facts['checks']:
                lines += ['检查：'+clean_text(check['name'], self.secrets)+
                          f"；通过 {check['passed']}，失败 {check['failed']}，跳过 {check['skipped']}"]
        links = facts.get('links', [])
        require(isinstance(links, list) and len(links) <= 6, 'Too many progress links')
        lines += [_link(link, slug) for link in links]
        lines += ['', '候选测试与实际交付分别记录；此评论不授予执行权限。', marker]
        body = '\n'.join(lines)
        require(len(body.encode()) <= 24000 and redact(body) == body, 'Unstable or oversized sanitized comment')
        core = dict(repository=slug, issue_number=number, run_id=run_id, event_id=event_id,
                    stage=stage, issue_state=issue_state, source_sha256=fingerprint(facts), body=body, marker=marker)
        if actor_id is not None:
            core['github_actor_id'] = _positive(actor_id)
        if facts.get('reference_kind') == 'investigation':
            core['reference_kind'] = 'investigation'
        with self.registry.transaction() as db:
            row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (KIND, identity)).fetchone()
            if row:
                original = json.loads(row[0])['core']
                equivalent = dict(core)
                if 'issue_state' not in original and core['issue_state'] == 'open':
                    equivalent.pop('issue_state')  # Preserve v1 approval bytes; no journal migration.
                require(original == equivalent, 'Event changed; preserve original proposal and intent')
            else:
                require(db.execute('SELECT count(*) FROM objects WHERE kind=?', (KIND,)).fetchone()[0] < 100,
                        'Progress history capacity reached; preserve existing records')
                self.registry.put(KIND, identity, dict(id=identity, core=core, approval_sha256=fingerprint(core),
                                  status='prepared', created_at=now()), db)
        return self.view(identity)

    def send(self, identity, *, approve_sha256, expected_actor_id):
        with self.continuity.guard() if self.continuity else nullcontext():
            return self._send(identity, approve_sha256=approve_sha256, expected_actor_id=expected_actor_id)

    def _send(self, identity, *, approve_sha256, expected_actor_id):
        row = self.view(identity)
        require(row['status'] == 'prepared', 'Already sent or unknown; reconcile without another POST')
        require(approve_sha256 == row['approval_sha256'], 'Exact explicit comment approval required')
        _positive(expected_actor_id)
        c = row['core']
        require(c.get('github_actor_id', expected_actor_id) == expected_actor_id,
                'Comment account differs from the approved preview')
        require(fingerprint(self.observe(c['repository'], c['issue_number'], c['run_id'], c['event_id']))
                == c['source_sha256'], 'Progress evidence changed after preview')
        # Preflight is read-only. Permission/auth errors cannot create an intent or POST.
        try:
            self.remote.preflight(c['repository'], c['issue_number'], expected_actor_id,
                                  expected_issue_state=c.get('issue_state', 'open'))
        except CommentPermissionDenied:
            with self.registry.transaction() as db:
                current = self._get(identity, db)
                if current['status'] == 'prepared':
                    current.update(status='permission_denied', error='Read-only preflight denied comment permission; no POST sent')
                    self.registry.put(KIND, identity, current, db)
            raise
        existing = self.remote.comments(c['repository'], c['issue_number'])
        require(isinstance(existing, list) and len(existing) <= 300
                and all(isinstance(r, dict) for r in existing), 'Incomplete comment preflight; no POST sent')
        require(not any(c['marker'] in str(r.get('body', '')) for r in existing),
                'Progress marker already exists remotely; inspect original authorization, no POST sent')
        require(fingerprint(self.observe(c['repository'], c['issue_number'], c['run_id'], c['event_id']))
                == c['source_sha256'], 'Progress evidence changed during preflight')
        before = self.continuity.capture(c['issue_number']) if self.continuity else None
        require(fingerprint(self.observe(c['repository'], c['issue_number'], c['run_id'], c['event_id']))
                == c['source_sha256'], 'Progress evidence changed before the comment intent')
        with self.registry.transaction() as db:
            current = self._get(identity, db)
            require(current['status'] == 'prepared', 'Concurrent sender already claimed the event')
            current.update(status='post_intent', actor_id=expected_actor_id, authorized_at=now(),
                           authorization_sha256=approve_sha256)
            if before is not None:
                current['continuity_before'] = before
            self.registry.put(KIND, identity, current, db)
        try:
            raw = self.remote.create_comment(c['repository'], c['issue_number'], c['body'], expected_actor_id,
                                             expected_issue_state=c.get('issue_state', 'open'))
            return self._finish(identity, [raw])
        except BaseException:
            with self.registry.transaction() as db:
                current = self._get(identity, db)
                if current['status'] == 'post_intent':
                    current.update(status='post_unknown', error='Write outcome unknown; read-only reconciliation required')
                    self.registry.put(KIND, identity, current, db)
            raise

    def _finish(self, identity, comments):
        row = self.view(identity)
        c = row['core']
        require(isinstance(comments, list) and len(comments) <= 300, 'Incomplete comment observation')
        matches = [r for r in comments if isinstance(r, dict) and c['marker'] in str(r.get('body', ''))]
        require(len(matches) == 1, 'No unique matching comment; preserve unknown and never resend')
        raw = matches[0]
        require(raw.get('body') == c['body'] and raw.get('user', {}).get('id') == row['actor_id'],
                'Comment body or author differs; preserve unknown for review')
        comment_id = _positive(raw.get('id'))
        require(raw.get('html_url') == f"https://github.com/{c['repository']}/issues/{c['issue_number']}#issuecomment-{comment_id}",
                'Comment receipt does not match the approved Issue')
        witness, continuity_error = None, None
        if self.continuity and row.get('continuity_before'):
            try:
                witness = self.continuity.proof(row, raw)
            except Exception:
                continuity_error = 'Comment created; source continuity needs review, no additional write'
        with self.registry.transaction() as db:
            current = self._get(identity, db)
            require(current['status'] in {'post_intent', 'post_unknown', 'posted', 'posted-review-required'}, 'No durable write intent')
            receipt = dict(comment_id=comment_id, url=raw['html_url'], actor_id=row['actor_id'])
            require(current.get('receipt', receipt) == receipt, 'Conflicting comment receipts')
            current.update(status='posted-review-required' if continuity_error else 'posted', receipt=receipt,
                           observed_at=now(), continuity_error=continuity_error)
            self.registry.put(KIND, identity, current, db)
            if witness:
                from .issue_comment_continuity import KIND as CONTINUITY
                self.registry.put(CONTINUITY, identity, witness, db)
        return self.view(identity)

    def reconcile(self, identity):
        with self.continuity.guard() if self.continuity else nullcontext():
            return self._reconcile(identity)

    def _reconcile(self, identity):
        row = self.view(identity)
        require(row['status'] in {'post_intent', 'post_unknown', 'posted', 'posted-review-required'}, 'No original comment intent to reconcile')
        c = row['core']
        # A missing marker is not proof a timed-out POST failed. No path resets to prepared.
        return self._finish(identity, self.remote.comments(c['repository'], c['issue_number']))


def verified_check_counts(result):
    """Use original check classification and explicit summaries; never invent counts."""
    from .target_environment import classify
    checked = classify(result)
    require(checked['category'] == 'passed' and checked['returncode'] == 0,
            'Original check is not a verified nonempty passing check')
    text = result['stdout']+'\n'+result['stderr']
    unittest_counts = re.findall(r'Ran (\d+) tests?', text)
    if len(unittest_counts) == 1:
        total = int(unittest_counts[0])
        skipped_rows = re.findall(r'skipped=(\d+)', text)
        require(len(skipped_rows) <= 1, 'Ambiguous original skipped-test summary')
        skipped = int(skipped_rows[0]) if skipped_rows else 0
        passed = total-skipped
    else:
        pytest_pass = re.findall(r'\b(\d+) passed\b', text)
        node_pass = re.findall(r'(?:#|ℹ) pass (\d+)\b', text)
        require(len(pytest_pass)+len(node_pass) == 1, 'Original test counts cannot be confirmed')
        passed = int((pytest_pass or node_pass)[0])
        skipped_rows = re.findall(r'\b(\d+) skipped\b|(?:#|ℹ) skipped (\d+)\b', text)
        require(len(skipped_rows) <= 1, 'Ambiguous original skipped-test summary')
        skipped = int(next(v for v in skipped_rows[0] if v)) if skipped_rows else 0
    require(passed > 0 and skipped >= 0, 'No effective original passing tests')
    command = result.get('command')
    require(isinstance(command, (list, tuple)) and command and all(isinstance(c, str) for c in command),
            'Original check command missing')
    return {'name': ' '.join(command), 'passed': passed, 'failed': 0, 'skipped': skipped}


class IssueProgressSource:
    """Read original IssueExecution/Delivery/Closure facts; never grants or starts work.

    This adapter is opt-in and has no controller/route hooks. A bound observer
    rechecks original evidence at preview and send, instead of trusting flags
    supplied in Issue text or by a model. All remote operations are GETs.
    """
    def __init__(self, execution, *, delivery=None, closure=None):
        self.execution, self.registry = execution, execution.registry
        self.delivery, self.closure = delivery, closure
        require(delivery is None or delivery.execution is execution and delivery.registry is self.registry,
                'Progress delivery must use the original execution and registry')
        require(closure is None or closure.delivery is delivery and closure.registry is self.registry,
                'Progress closure must use the original delivery and registry')

    def _original(self, execution_id):
        service = self.execution
        with self.registry.transaction() as db:
            row = service._load('issue_execution', execution_id, db)
            require(row and row.get('id') == execution_id, 'Original Issue execution missing')
            job = service._load('issue_diagnosis', row['investigation_id'], db)
            head = service._load('issue_execution_head', row['issue_key'], db)
            metadata = json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
        require(metadata.get('repository') == str(service.source.root), 'Original journal source changed')
        require(head and head.get('execution_id') == execution_id and job,
                'Original execution claim advanced or investigation missing')
        snap = job['snapshot']
        require(snap['repository_id'] == row['repository_id'] and snap['issue']['number'] == row['issue_number']
                and snap['token'] == row['issue_token'] and row['rules_sha256'] == service.rules['rules_sha256']
                and row['spec_sha256'] == fingerprint(service.spec), 'Original Issue scope changed')
        return row, job

    def facts(self, execution_id, stage):
        from .dashboard import read_json
        row, job = self._original(execution_id)
        service = self.execution
        require(stage in STAGES, 'Unsupported progress stage')
        facts = dict(repository=service.spec['repository'], issue_number=row['issue_number'],
                     run_id=row['run_id'], execution_id=execution_id, stage=stage, links=[], issue_state='open')
        if stage != 'delivered':
            from .issue_intake import IssueReader
            current = IssueReader(service.connection, service.spec, registry=self.registry).snapshot(row['issue_number'])
            require(current.get('token') == row['issue_token'], 'Original Issue or target changed before progress preview')
        binding = {key: row[key] for key in ('id', 'run_id', 'issue_number', 'issue_token',
                                            'rules_sha256', 'spec_sha256')}
        if stage == 'accepted':
            require(row['status'] in {'claimed', 'running', 'awaiting-review', 'paused', 'interrupted', 'failed_no_replay'},
                    'Original execution has no current accepted claim')
            facts['summary'] = '原Issue执行已接手；执行标识：'+execution_id+'。本条仅回写接手事实，不代表候选通过或交付。'
            facts['source_proof_sha256'] = fingerprint(binding)
        elif stage == 'investigated':
            require(job.get('status') == 'investigated' and isinstance(job.get('report'), dict),
                    'Original completed investigation report missing')
            report = job['report']
            files, tests = report.get('file_candidates'), report.get('test_candidates')
            require(isinstance(files, list) and isinstance(tests, list), 'Original investigation candidate lists missing')
            hints = [item['path'] for item in files[:3] if isinstance(item, dict) and isinstance(item.get('path'), str)]
            facts['summary'] = ('原只读调查已完成；调查标识：'+row['investigation_id']+
                f'；代码候选 {len(files)} 项，检查候选 {len(tests)} 项；'+
                ('定位线索：'+', '.join(hints)+'；' if hints else '')+
                '报告指纹：'+fingerprint(report)+'。调查结论不代表复现、修复或交付。')
            facts['source_proof_sha256'] = fingerprint([binding, report])
        elif stage == 'blocked':
            require(row['status'] in {'paused', 'interrupted', 'failed_no_replay', 'stale'},
                    'Original execution has no recorded blocker')
            reason = row.get('error') or row.get('workflow_status') or row['status']
            require(isinstance(reason, str), 'Original blocker reason invalid')
            facts['summary'] = '原执行状态：'+row['status']+'；原因：'+reason+'。保留原候选、Native和额度；本评论不会重试执行。'
            facts['source_proof_sha256'] = fingerprint([binding, row['status'], reason])
        elif stage in {'candidate_passed', 'awaiting_review'}:
            from .integration import verified_publication
            require(self.delivery is not None, 'Original delivery verifier required for candidate comments')
            original, b = self.delivery._local(execution_id)
            require(original['run_id'] == row['run_id'], 'Candidate run advanced')
            self.delivery._fresh(b)
            proof = verified_publication(service.runs/row['run_id'])
            require(proof['review']['commit'] == b['commit']
                    and fingerprint(proof['record']['candidate_tests']) == b['candidate_tests_sha256']
                    and fingerprint(proof['review']['tests']) == b['sealed_tests_sha256'],
                    'Original sealed check receipts changed')
            checks = proof['review']['tests']
            require(isinstance(checks, list) and 0 < len(checks) <= 30, 'Original sealed checks missing')
            candidate_checks = proof['record'].get('candidate_tests')
            require(isinstance(candidate_checks, list) and 0 < len(candidate_checks) <= 30,
                    'Original candidate checks missing')
            for check in candidate_checks:
                verified_check_counts(check)
            facts.update(candidate_checks_verified=True, candidate_sha=b['commit'], branch=b['head'],
                         checks=[verified_check_counts(check) for check in checks],
                         source_proof_sha256=fingerprint(b),
                         summary='原候选与封装检查通过；报告指纹：'+read_json(service.runs/row['run_id']/'run.json')[1]+'。尚未交付。')
            if stage == 'awaiting_review':
                draft = self.delivery.view(execution_id)
                require(draft.get('status') == 'awaiting-review' and draft['core']['binding'] == b
                        and draft.get('authorization', {}).get('approval_sha256') == draft['approval_sha256']
                        == fingerprint(draft['core']), 'Original verified PR creation receipt required')
                receipt = draft['receipt']
                require(receipt['head_sha'] == b['commit'] and receipt['state'] == 'open'
                        and not receipt.get('merged_at'), 'PR receipt no longer represents an unmerged candidate')
                facts['links'] = [_link(receipt['url'], b['repository'])]
                facts['source_proof_sha256'] = fingerprint([b, receipt])
        else:
            from .issue_acceptance import observe
            require(row['status'] == 'delivered' and self.closure is not None
                    and row.get('issue_closure_id') == execution_id, 'Original verified delivery receipt required')
            closing = self.closure.view(execution_id)
            require(closing['status'] == 'closed-verified'
                    and closing.get('authorization', {}).get('approval_sha256') == closing['approval_sha256']
                    == fingerprint(closing['core']), 'Original delivery acceptance authorization changed')
            proof = observe(self.delivery, self.closure.remote, execution_id, self.closure.policy)
            require(fingerprint(proof) == closing['verified_proof_sha256'], 'Original delivered version evidence changed')
            b = proof['binding']
            require(b['run_id'] == row['run_id'] and b['issue_number'] == row['issue_number'], 'Delivery run association changed')
            facts.update(delivery_verified=True, delivered_sha=proof['default_head_sha'],
                         delivery_proof_sha256=fingerprint(proof), issue_state=proof['issue']['issue']['state'],
                         links=[_link(proof['pr']['url'], b['repository'])],
                         summary='原交付验收已通过；实际默认分支与CI证明已重新只读核对。此评论不合并、不关闭或重开Issue。')
        # Evidence may be re-read, but one original execution/stage is one event.
        # Changed content cannot manufacture a fresh ID to bypass create-once.
        facts['event_id'] = stage+'-'+fingerprint([execution_id, stage])
        require(self._original(execution_id)[0] == row, 'Original execution advanced during progress read')
        return facts

    def observer(self, execution_id, stage):
        def observe(repository, number, run_id, event_id):
            facts = self.facts(execution_id, stage)
            require((repository, number, run_id, event_id) ==
                    (facts['repository'], facts['issue_number'], facts['run_id'], facts['event_id']),
                    'Original progress event advanced or another scope was requested')
            return facts
        from .issue_comment_continuity import CommentContinuity
        observe.comment_continuity = CommentContinuity(self.registry, self.execution.connection, self.execution.spec)
        return observe


class IssueInvestigationProgressSource:
    """Original investigation facts before an execution/model grant exists."""
    def __init__(self, workbench):
        self.workbench, self.registry = workbench, workbench.registry

    def facts(self, identity, stage):
        from .issue_intake import IssueReader
        w = self.workbench
        config = w._config()
        require(stage in {'accepted','investigated','blocked'}, 'Investigation has no candidate or delivery authority')
        with self.registry.transaction() as db:
            found = db.execute("SELECT body FROM objects WHERE kind='issue_diagnosis' AND id=?", (identity,)).fetchone()
            require(found, 'Original Issue investigation missing')
            job = json.loads(found[0])
            head = db.execute("SELECT body FROM objects WHERE kind='issue_head' AND id=?", (job['issue_key'],)).fetchone()
        original = job['snapshot']
        require(head and json.loads(head[0])['token'] == original['token'] == identity
                and original['spec_sha256'] == fingerprint(config['spec']), 'Original investigation scope changed')
        current = IssueReader(w.connection, config['spec'], registry=self.registry).snapshot(original['issue']['number'])
        require(current.get('token') == identity, 'Issue or target changed since the investigation')
        facts = dict(repository=config['spec']['repository'], issue_number=original['issue']['number'], run_id=identity,
                     reference_kind='investigation', stage=stage, links=[], issue_state='open')
        if stage == 'accepted':
            require(job['status'] in {'started','investigated','failed_no_replay'}, 'Investigation no longer belongs to this task')
            summary = '原只读调查已接手。本条仅确认调查任务，不表示已经修改代码或交付。'
            proof = original['token']
        elif stage == 'investigated':
            report = job.get('report')
            require(job['status']=='investigated' and isinstance(report, dict), 'Completed original investigation required')
            files, tests = report.get('file_candidates'), report.get('test_candidates')
            require(isinstance(files,list) and isinstance(tests,list), 'Original investigation results missing')
            summary = f'原只读调查已完成；代码候选 {len(files)} 项、检查候选 {len(tests)} 项。报告指纹：'+fingerprint(report)+'。尚未授权修改或证明根因。'
            proof = fingerprint(report)
        else:
            require(job['status'] in {'investigated','failed_no_replay'}, 'Original investigation has no report or blocker')
            environment = self.registry.load('issue_workbench_environment').get(identity)
            reason = job.get('error_type') if job['status']=='failed_no_replay' else (
                '尚未准备目标环境和检查建议，需要补充或确认维护条件。' if not environment else
                '环境建议已准备，等待维护者确认执行与审批；这不是代码修复失败。')
            summary = '原调查待补充或确认：'+str(reason)+' 本评论不会触发模型、安装依赖或修改代码。'
            proof = fingerprint([job['status'],reason,environment])
        facts.update(summary=summary, source_proof_sha256=proof,
                     event_id=stage+'-'+fingerprint(['investigation',identity,stage]))
        return facts

    def observer(self, identity, stage):
        def observe(repository, number, run_id, event_id):
            facts = self.facts(identity, stage)
            require((repository,number,run_id,event_id)==(facts['repository'],facts['issue_number'],facts['run_id'],facts['event_id']),
                    'Original investigation comment scope changed')
            return facts
        from .issue_comment_continuity import CommentContinuity
        observe.comment_continuity = CommentContinuity(self.registry,self.workbench.connection,self.workbench._config()['spec'])
        return observe
