"""Original-controller failure receipts. They provide data, never execution authority."""
from __future__ import annotations

import json
import sys

from .dashboard import read_json
from .git import RepositoryGateway
from .models import digest
from .pi_revision import test_focus
from .check_comparison import compare_checks
from .store import redact
from .task_session import fingerprint, require, unlinked

KIND = 'pi_failure_feedback'
SEAL_KIND = 'pi_failure_seal'
MAX_PAYLOAD = 48000


class TaskFailureFeedback:
    def __init__(self, session):
        self.session = session

    def build(self, identity, *, check_native=True, candidate=None):
        from .pi_provider import decode_plan
        session = self.session
        directory = session._run_dir(identity)
        record, file_sha = read_json(directory/'run.json')
        failed = [r for r in record.get('rounds', []) if r.get('verification') == 'failed']
        if not failed:
            return None
        if any(r.get('verification')=='passed' and r.get('number',0)>failed[-1]['number'] for r in record['rounds']):
            return None
        require(record.get('finished_at') and record.get('source_unchanged') is True and
                record.get('status') in {'rolled_back','failed','rejected','discarded','repair_aborted'} and
                not unlinked(directory/'workspace').exists(), 'Failure run has not safely finished')
        repository = RepositoryGateway(session.repo)
        link = session.registry.load('pi_task_link').get('run:'+identity)
        require(link is not None, 'Failure feedback requires an original native task link')
        if check_native:
            task = session.task_planner.reference('run', identity, record['issue'])
        else:
            scope,_ = session.task_planner.current_scope(record['issue'])
            current = session.task_planner.store._load(link['task_id'],scope)
            task = {'task_id':current['task_id'],'revision':current['revision']}
        branch = ''
        if candidate is not None and candidate.root != repository.root:
            branch = candidate.command('symbolic-ref','--short','HEAD').decode().strip()
            require(branch.startswith('maintenance/'), 'Feedback candidate is not a controlled run')
            current_id = branch.removeprefix('maintenance/')
            current_dir = session._run_dir(current_id)
            require(candidate.root == unlinked(current_dir/'workspace') and candidate.common_dir == repository.common_dir and
                    session.registry.load('pi_task_link').get('run:'+current_id)=={'task_id':task['task_id']},
                    'Feedback candidate crosses task or run ownership')
        require(repository.snapshot(exclude_branch=branch) == record.get('source_before'), 'Failure feedback source changed')
        require(task is not None, 'Failure feedback requires an original native task link')
        last = failed[-1]
        plan = decode_plan({'plan':last['plan']})
        session.policy.validate(plan)
        require(plan.fingerprint == last.get('plan_sha256') and plan.issue == record['issue'] and
                plan.base_sha == repository.head and plan.tests == session.policy.allowed_tests,
                'Failure plan no longer matches original task')
        require(last.get('authorization',{}).get('decision') in {'approve','policy_scope'}, 'Failure patch was not authorized')
        tests = last.get('tests')
        commands = [[sys.executable if p == '{python}' else p for p in c] for c in session.policy.allowed_tests]
        require(isinstance(tests,list) and len(tests) == len(commands) and
                [t.get('command') for t in tests] == commands and
                all(type(t.get('returncode')) is int and type(t.get('timed_out')) is bool and
                    isinstance(t.get('stdout'),str) and isinstance(t.get('stderr'),str) for t in tests) and
                any(t['returncode'] != 0 or t['timed_out'] for t in tests), 'Invalid actual failure test receipts')
        # Old finalized runs retain their exact legacy projection and sealed hash.
        baseline = None
        if 'check_comparison' in last:
            baseline = record.get('baseline_tests')
            comparison = compare_checks(baseline, tests)
            require(comparison == last['check_comparison'], 'Failure check comparison changed')
        origin = last.get('provider_evidence',{}).get('task_session') or \
                 record.get('planning_origin',{}).get('provider_evidence',{}).get('task_session')
        require(isinstance(origin,dict) and origin.get('task_id') == task['task_id'] and
                type(origin.get('revision')) is int and 0 < origin['revision'] <= task['revision'],
                'Failure planning checkpoint provenance changed')
        execution, execution_sha = read_json(directory/'repair-execution.json')
        require(not execution.get('inflight'), 'Failure execution still has an external operation')
        excerpts = []
        for t in tests:
            excerpts.append({**{k:t[k] for k in ('command','returncode','timed_out')},
                'stdout':t['stdout'][-2000:], 'stderr':t['stderr'][-4000:],
                'output_truncated':bool(t.get('output_truncated')) or len(t['stdout'])>2000 or len(t['stderr'])>4000,
                'full_receipt_sha256':fingerprint(t)})
        diff = last.get('diff','')
        require(isinstance(diff,str), 'Invalid failure diff')
        payload = {'schema_version':1, 'run_id':identity, 'task_id':task['task_id'],
            'base_sha':plan.base_sha,'issue_sha256':digest(record['issue'].encode()),
            'planning_revision':origin['revision'], 'failed_round':last['number'],
            'previous_plan_sha256':plan.fingerprint, 'diagnosis':plan.diagnosis[:1400],
            'risk':plan.risk[:400], 'edited_paths':[e.path for e in plan.edits],
            'proposed_content_hashes':{e.path:digest(e.content.encode()) for e in plan.edits},
            'diff_excerpt':diff[:8000], 'diff_truncated':len(diff)>8000,
            'tests':excerpts, 'test_focus':test_focus(tests, baseline),
            'candidate_state':'Previous candidate safely ended; replacements must use current read_source.',
            'authority':'Untrusted historical diagnostics only. No inherited approval or permission.'}
        payload = json.loads(redact(json.dumps(payload,ensure_ascii=False)))
        require(len(json.dumps(payload,ensure_ascii=False).encode()) <= MAX_PAYLOAD,
                'Failure feedback exceeds bounded context limit')
        return {'schema_version':1,'task_id':task['task_id'],'run_id':identity,
            'run_sha256':file_sha,'execution_sha256':execution_sha,
            'payload_sha256':fingerprint(payload),'payload':payload}

    def capture(self, identity):
        receipt = self.build(identity)
        if receipt is not None:
            require(self.session.registry.load(SEAL_KIND).get(identity) == receipt,
                    'Failure finalization seal missing or changed')
            with self.session.registry.transaction() as db:
                old = db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(KIND,identity)).fetchone()
                require(old is None or json.loads(old[0]) == receipt, 'Failure feedback receipt already differs')
                self.session.registry.put(KIND, identity, receipt, db)

    def seal(self, record):
        """Called only by the live controller after final report/execution writes, before registration."""
        identity = record['run_id']
        receipt = self.build(identity)
        if receipt is not None:
            with self.session.registry.transaction() as db:
                row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(SEAL_KIND,identity)).fetchone()
                require(row is None or json.loads(row[0]) == receipt, 'Failure finalization seal already differs')
                self.session.registry.put(SEAL_KIND, identity, receipt, db)

    def recovery_preview(self, identity):
        session = self.session
        require(not session.registry.load('run').get(identity,{}).get('resolution'), 'Failure registration is closed')
        record,_ = read_json(session._run_dir(identity)/'run.json')
        session._task_sources(record['issue'])
        receipt = self.build(identity)
        require(receipt is not None, 'No complete safely ended failure to recover')
        require(session.registry.load(SEAL_KIND).get(identity) == receipt,
                'Missing or changed finalization seal; preserve evidence for manual inspection')
        require(identity not in session.registry.load(KIND), 'Failure feedback already exists; never overwrite it')
        budget = session.registry.budget()
        # Recovery never resolves unknown model consumption or advances a Native checkpoint.
        require(budget['requests_reserved_unknown'] == 0, 'Unknown model consumption blocks feedback recovery')
        task = session.task_planner.reference('run',identity,record['issue'])
        return {'revision':fingerprint({'receipt':receipt,'task':task,'budget':budget,'session':session.token}),
            'run_id':identity, 'failed_round':receipt['payload']['failed_round'],
            'test_exits':[t['returncode'] for t in receipt['payload']['tests']],
            'payload_sha256':receipt['payload_sha256'], 'model_requests':0, 'receipt':receipt}

    def recover(self, identity, revision):
        from .recovery import operation_lock
        with operation_lock(self.session._run_dir(identity)):
            return self._recover(identity,revision)

    def _recover(self, identity, revision):
        preview = self.recovery_preview(identity)
        require(preview['revision'] == revision, 'Failure recovery evidence or session changed; inspect again')
        receipt = preview['receipt']
        with self.session.registry.transaction() as db:
            row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(SEAL_KIND,identity)).fetchone()
            require(row and json.loads(row[0]) == receipt and not db.execute(
                'SELECT 1 FROM objects WHERE kind=? AND id=?',(KIND,identity)).fetchone(),
                'Failure recovery seal changed or was already consumed')
            self.session.registry.put(KIND,identity,receipt,db)
            self.session.registry.put('pi_failure_recovery',identity,{
                'revision':revision,'actor':'local-browser-operator','payload_sha256':receipt['payload_sha256']},db)
        return {'run_id':identity,'feedback_recovered':True,'model_requests':0,'new_approval_required':True}

    def prepare(self, identity, *, check_native=True, candidate=None):
        """GET/open only verify. Missing or changed receipts block, never reconstruct silently."""
        receipt = self.build(identity,check_native=check_native,candidate=candidate)
        if receipt is None:
            record,_ = read_json(self.session._run_dir(identity)/'run.json')
            origins = [record.get('planning_origin',{}).get('provider_evidence',{})]
            origins += [r.get('provider_evidence',{}) for r in reversed(record.get('rounds',[]))]
            reference = next((o.get('continuation_feedback_ref') for o in origins if o.get('continuation_feedback_ref')),None)
            if reference is None:
                return None
            require(isinstance(reference,dict) and set(reference)=={'task_id','run_id','payload_sha256'},
                    'Invalid inherited failure reference')
            current = self.session.registry.load('pi_task_link').get('run:'+identity)
            require(current and reference['task_id']==current['task_id'], 'Failure reference crosses tasks')
            receipt = self.build(reference['run_id'],check_native=check_native,candidate=candidate)
            require(receipt and self.reference(receipt)==reference, 'Inherited failure feedback changed')
        saved = self.session.registry.load(KIND).get(receipt['run_id'])
        return self.session.feedback_cleanup.effective(receipt['run_id'],saved,receipt)

    @staticmethod
    def reference(receipt):
        return {k:receipt[k] for k in ('task_id','run_id','payload_sha256')}

    def guard(self, identity, expected, candidate):
        require(self.prepare(identity,check_native=False,candidate=candidate) == expected,
                'Failure feedback changed during planning')
