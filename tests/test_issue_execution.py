"""Simulated GitHub + real Pi AgentSession/Native/Git/venv, loopback synthetic SSE."""
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import test_issue_intake as issue_fixtures
import test_task_planning as pi_fixtures
from repo_maintainer.demo import create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.issue_execution import IssueExecution,ExecutionReader
from repo_maintainer.issue_intake import IssueIntake,specification,fingerprint
from repo_maintainer.maintenance_rules import maintenance_rules
from repo_maintainer.models import SafetyError,Policy
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.repair_recovery import inspect_repair,recover_repair
from repo_maintainer.target_environment import discover_environment,make_grant
from repo_maintainer.ui_registry import UIRegistry,provider_binding
from repo_maintainer.workflow import cleanup_run,repair


class IssueExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.repo=RepositoryGateway(create_fixture(self.root/'source'))
        self.repo.command('remote','add','origin','https://github.com/example/project.git')
        self.github=issue_fixtures.FakeGitHub(self.repo.head)
        self.github.issue.update(title='Fix calculator add subtraction bug',
                                 body='add(2,3) must be 5; preserve negative and mixed operands.')
        self.model=pi_fixtures.SyntheticModel()
        self.addCleanup(self.model.close)
        self.spec=specification('https://github.com/example/project','main',['.'],['maintainer'])
        self.environment=discover_environment(self.repo,self.repo.head,['.'])
        self.grant=make_grant(self.environment,allow_local_code=True)
        self.rules=maintenance_rules(self.repo,read_roots=['.'],write_roots=['.'],
                                     check_ids=[self.environment['checks'][0]['id']])
        self.prototype=PiProvider(('calculator.py',),self.model.url,max_requests=50)
        self.metadata={'repository':str(self.repo.root),'provider':provider_binding(self.prototype),
                       'maintenance_rules_sha256':self.rules['rules_sha256'],'issue_spec_sha256':fingerprint(self.spec)}
        self.registry=UIRegistry(self.root/'state',self.metadata)
        self.addCleanup(lambda:self.registry.close())
        self.job=IssueIntake(self.github,self.registry,self.spec).investigate(7,self.repo)
        self.assertEqual(self.job['status'],'investigated')
        self.executor=self.service()

    def provider(self,policy):
        return PiProvider(policy.allowed_paths,self.model.url,readonly_paths=policy.readonly_paths,max_requests=50)

    def service(self):
        return IssueExecution(self.repo.root,self.registry,self.github,self.spec,self.rules,self.provider)

    def run_issue(self,approval=lambda _: 'approve'):
        value=self.executor.start(self.job['id'],self.environment,self.grant,approval=approval)
        if value.get('run_id'):
            owner=self.executor.runs/value['run_id']
            if (owner/'workspace').exists():
                self.addCleanup(cleanup_run,owner)
        return value

    def test_real_pi_issue_to_candidate_preserves_original_ledger_native_and_bidirectional_links(self):
        before=self.repo.snapshot()
        value=self.run_issue()
        self.assertEqual(value['status'],'awaiting-review',value)
        self.assertEqual(self.executor.runs,self.registry.root.parent,
                         'Issue execution must use the original run-id directory, not a parallel run namespace')
        record=json.loads((self.executor.runs/value['run_id']/'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['status'],'candidate_ready',record.get('error'))
        self.assertEqual(record['issue_execution']['execution_id'],value['id'])
        self.assertEqual(record['issue_execution']['task_id'],value['task_id'])
        self.assertTrue(record['rounds'][0]['environment_comparison']['candidate_verified'])
        self.assertEqual(self.registry.budget()['requests_used'],2)
        self.assertEqual(self.registry.budget()['requests_reserved_unknown'],0)
        task=self.registry.load('pi_task')[value['task_id']]
        self.assertEqual(task['revision'],1)
        self.assertEqual(self.registry.load('pi_task_link')['run:'+value['run_id']]['task_id'],value['task_id'])
        self.assertEqual(before,self.repo.snapshot(exclude_branch='maintenance/'+value['run_id']))
        requests=len(self.model.requests)
        duplicate=self.run_issue()
        self.assertEqual(duplicate['status'],'duplicate_no_replay')
        self.assertEqual(len(self.model.requests),requests)
        self.assertEqual(self.github.issue['state'],'open')

    def test_two_workers_cannot_execute_same_issue_version_twice(self):
        entered,release=threading.Event(),threading.Event()
        result=[]
        def approval(_):
            entered.set()
            self.assertTrue(release.wait(20))
            return 'approve'
        thread=threading.Thread(target=lambda:result.append(self.run_issue(approval)))
        thread.start()
        try:
            self.assertTrue(entered.wait(25))
            other=self.service().start(self.job['id'],self.environment,self.grant,approval=lambda _:'approve')
            self.assertEqual(other['status'],'duplicate_no_replay')
        finally:
            release.set()
            thread.join(35)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]['status'],'awaiting-review',result)
        self.assertEqual(len(self.model.requests),2)

    def test_focused_task_checks_reach_original_pi_native_approval_and_same_environment(self):
        self.environment=discover_environment(self.repo,self.repo.head,['.'],investigation=self.job['report'])
        self.grant=make_grant(self.environment,allow_local_code=True)
        self.assertEqual(self.environment['selection']['selected_tests'],['tests/test_calculator.py'])
        approvals=[]
        def approve(plan):
            approvals.append(plan)
            self.assertIn('--pattern=test_calculator.py',plan.tests[0])
            return 'approve'
        before=self.repo.snapshot()
        value=self.run_issue(approve)
        self.assertEqual(value['status'],'awaiting-review',value)
        self.assertEqual(len(approvals),1)
        record=json.loads((self.executor.runs/value['run_id']/'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['policy']['allowed_tests'][0],self.environment['checks'][0]['argv'])
        self.assertTrue(record['rounds'][0]['environment_comparison']['candidate_verified'])
        self.assertEqual(self.registry.budget()['requests_used'],2)
        self.assertEqual(self.registry.budget()['requests_reserved_unknown'],0)
        self.assertEqual(before,self.repo.snapshot(exclude_branch='maintenance/'+value['run_id']))

    def test_issue_candidate_seals_publishes_and_closes_only_after_explicit_default_acceptance(self):
        from repo_maintainer.integration import prepare_integration,integrate
        before=self.repo.snapshot()
        value=self.run_issue()
        self.assertEqual(value['status'],'awaiting-review',value)
        budget=self.registry.budget()
        native={str(p):p.read_bytes() for p in (self.registry.root.parent/'task-sessions').rglob('*.jsonl')}
        self.assertTrue(native)
        owner=self.executor.runs/value['run_id']
        review=prepare_integration(owner)
        self.assertEqual(review['status'],'prepared',review)
        self.assertTrue(review['review']['environment_verification']['comparison']['candidate_verified'])
        self.assertEqual(integrate(owner)['status'],'awaiting_approval')
        self.assertEqual(integrate(owner,review['review_sha256'])['status'],'published')
        self.assertEqual(self.registry.budget(),budget)
        self.assertEqual({str(p):p.read_bytes() for p in (self.registry.root.parent/'task-sessions').rglob('*.jsonl')},native)
        self.assertEqual(len(self.model.requests),2)
        self.assertEqual(self.github.issue['state'],'open')

        self.assertEqual(self.executor.view(value['id'])['status'],'awaiting-review')
        self.assertEqual(before,self.repo.snapshot(exclude_branches=('maintenance/'+value['run_id'],'integration/'+value['run_id'])))
        # Same authentic original claim/task/Native/budget, simulated remote head.
        from repo_maintainer.issue_delivery import IssueDelivery
        from test_issue_delivery import FakePulls
        remote=FakePulls(self.github,review['review']['target_branch'],review['review']['commit'])
        delivery=IssueDelivery(self.executor,remote)
        from repo_maintainer.issue_publication import IssuePublication
        from test_issue_publication import LocalBranchTransport,prepare_bare
        remote_bare=self.root/'native-remote.git'
        prepare_bare(self.repo,remote_bare)
        publisher=IssuePublication(delivery,LocalBranchTransport(remote_bare))
        publication=publisher.prepare(value['id'])
        published=publisher.push(value['id'],publication['challenge'],
                                 approve_sha256=publication['approval_sha256'],actor='fixture')
        self.assertEqual(published['status'],'remote-ref-verified',published)
        self.assertEqual(remote.writes,0,'Branch approval must not authorize a PR')
        prepared=delivery.prepare(value['id'])
        delivered=delivery.create(value['id'],prepared['challenge'],
                                  approve_sha256=prepared['approval_sha256'],actor='fixture')
        self.assertEqual(delivered['status'],'awaiting-review',delivered)
        self.assertEqual(remote.writes,1)
        self.assertEqual(self.registry.budget(),budget)
        self.assertEqual({str(p):p.read_bytes() for p in (self.registry.root.parent/'task-sessions').rglob('*.jsonl')},native)
        self.assertEqual(len(self.model.requests),2)
        self.assertEqual(self.github.issue['state'],'open')

        from types import SimpleNamespace
        from test_issue_closure import AcceptanceRemote
        from repo_maintainer.issue_closure import IssueClosure
        from repo_maintainer.issue_acceptance import acceptance_policy
        accepted_case=SimpleNamespace(repo=self.repo,root=self.root,commit=review['review']['commit'],
                                      github=self.github,remote=remote,addCleanup=self.addCleanup)
        accepted=AcceptanceRemote(accepted_case)
        accepted.merge()
        closer=IssueClosure(delivery,accepted,acceptance_policy([1001]))
        proposal=closer.prepare(value['id'])
        self.assertEqual(self.github.issue['state'],'open','Evidence alone cannot authorize an Issue close')
        final=closer.close(value['id'],proposal['challenge'],approve_sha256=proposal['approval_sha256'],actor='fixture')
        self.assertEqual(final['status'],'closed-verified',final)
        self.assertEqual(self.executor.view(value['id'])['status'],'delivered')
        self.assertEqual(self.github.issue['state'],'closed')
        self.assertEqual(self.registry.budget(),budget)
        self.assertEqual({str(p):p.read_bytes() for p in (self.registry.root.parent/'task-sessions').rglob('*.jsonl')},native)
        self.assertEqual(len(self.model.requests),2)
        import os,hashlib
        capture=os.environ.get('REPOCARE_CLOSURE_CHAIN_EVIDENCE')
        if capture:
            directory=Path(capture)
            directory.mkdir(parents=True,exist_ok=True)
            captured=[]
            for index,(name,raw) in enumerate(sorted(native.items())):
                current=Path(name).read_bytes()
                target=directory/f'native-{index}.jsonl'
                target.write_bytes(current)
                captured.append({'file':target.name,'before_sha256':hashlib.sha256(raw).hexdigest(),
                                 'after_sha256':hashlib.sha256(current).hexdigest()})
            report={'budget_before':budget,'budget_after':self.registry.budget(),
                    'model_requests':len(self.model.requests),'native':captured,'patch_calls':accepted.writes,
                    'closure':final,'execution':self.executor.view(value['id']),
                    'source_before':before,'source_after':self.repo.snapshot(exclude_branches=(
                        'maintenance/'+value['run_id'],'integration/'+value['run_id'])),
                    'actual_default_checks':accepted.actual_checks}
            (directory/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')

    def test_pause_restart_resume_requires_new_approval_keeps_same_native_and_used_requests(self):
        decisions=[]
        def pause(_):
            identity=next(iter(self.executor.controls))
            view=self.executor.view(identity)
            self.executor.request_pause(identity,view['challenge'])
            decisions.append('old approve is interrupted')
            return 'approve'
        first=self.run_issue(pause)
        self.assertEqual(first['status'],'paused',first)
        self.assertEqual(self.registry.budget()['requests_used'],2)
        task=self.registry.load('pi_task')[first['task_id']]
        native=self.root/'state/task-sessions'/first['task_id']/'sessions'/task['checkpoint']['file']
        prefix=native.read_bytes()
        with self.assertRaisesRegex(SafetyError,'new approval callback'):
            self.executor.resume(first['id'],first['challenge'],self.environment,self.grant)
        self.registry.close()
        self.registry=UIRegistry(self.root/'state',self.metadata)
        self.executor=self.service()
        self.assertEqual(self.registry.budget()['requests_used'],2)
        fresh=self.executor.view(first['id'])
        second=self.executor.resume(first['id'],fresh['challenge'],self.environment,self.grant,
                                    approval=lambda _:(decisions.append('new approve') or 'approve'))
        if (self.executor.runs/second['run_id']/'workspace').exists():
            self.addCleanup(cleanup_run,self.executor.runs/second['run_id'])
        self.assertEqual(second['status'],'awaiting-review',second)
        self.assertEqual(second['task_id'],first['task_id'])
        self.assertEqual(self.registry.budget()['requests_used'],4)
        self.assertEqual(self.registry.load('pi_task')[first['task_id']]['revision'],2)
        self.assertTrue(native.read_bytes().startswith(prefix))
        self.assertEqual(decisions,['old approve is interrupted','new approve'])
        with self.assertRaisesRegex(SafetyError,'version advanced'):
            self.executor.resume(first['id'],fresh['challenge'],self.environment,self.grant,approval=lambda _:'approve')

    def environment_failure(self):
        with patch('repo_maintainer.workflow_environment.GrantedEnvironment.__call__',
                   side_effect=SafetyError('Synthetic dependency preparation failure')):
            first=self.run_issue()
        self.assertEqual(first['status'],'failed_no_replay',first)
        self.assertEqual(first['workflow_status'],'rolled_back')
        return first

    def test_explicit_environment_retry_after_restart_preserves_native_budget_and_old_failure(self):
        first=self.environment_failure()
        path=self.executor.runs/first['run_id']/'run.json'
        old=path.read_bytes()
        task=self.registry.load('pi_task')[first['task_id']]
        native=self.root/'state/task-sessions'/first['task_id']/'sessions'/task['checkpoint']['file']
        prefix=native.read_bytes()
        before=self.registry.budget()
        preview=self.executor.environment_retry_preview(first['id'])
        self.assertEqual(before,self.registry.budget())
        self.assertEqual(preview['new_model_requests'],0)
        self.assertEqual(len(self.model.requests),2)
        with self.assertRaises(SafetyError):
            self.executor.resume(first['id'],first['challenge'],self.environment,self.grant,approval=lambda _:'approve')
        self.assertEqual(self.run_issue()['status'],'duplicate_no_replay')
        self.registry.close()
        self.registry=UIRegistry(self.root/'state',self.metadata)
        self.executor=self.service()
        with self.assertRaisesRegex(SafetyError,'new approval'):
            self.executor.retry_environment(first['id'],preview['revision'],self.environment,self.grant)
        decisions=[]
        second=self.executor.retry_environment(first['id'],preview['revision'],self.environment,self.grant,
                    approval=lambda _:(decisions.append('fresh approval') or 'approve'))
        if (self.executor.runs/second['run_id']/'workspace').exists():
            self.addCleanup(cleanup_run,self.executor.runs/second['run_id'])
        self.assertEqual(second['status'],'awaiting-review',second)
        self.assertEqual(second['task_id'],first['task_id'])
        self.assertEqual(second['previous_execution'],first['id'])
        self.assertEqual(second['continuation_kind'],'environment_retry')
        self.assertEqual(path.read_bytes(),old)
        self.assertEqual(self.executor.view(first['id']),first)
        self.assertTrue(native.read_bytes().startswith(prefix))
        self.assertEqual(self.registry.budget()['requests_used'],4)
        self.assertEqual(self.registry.budget()['requests_reserved_unknown'],0)
        self.assertEqual(decisions,['fresh approval'])
        with self.assertRaises(SafetyError):
            self.executor.retry_environment(first['id'],preview['revision'],self.environment,self.grant,
                                            approval=lambda _:'approve')
        self.assertEqual(len(self.model.requests),4)

    def test_environment_retry_preview_rejects_non_environment_or_changed_evidence_without_calls(self):
        first=self.environment_failure()
        path=self.executor.runs/first['run_id']/'run.json'
        old=path.read_bytes()
        record=json.loads(old)
        preview=self.executor.environment_retry_preview(first['id'])
        for changes in ({'source_unchanged':False},{'candidate_tests':[{'returncode':1}]},
                        {'baseline_tests':[{'returncode':1}]},{'status':'candidate_ready'},
                        {'environment':{'status':'prepared'}},{'finished_at':None}):
            with self.subTest(changes=changes):
                path.write_text(json.dumps({**record,**changes}),encoding='utf-8')
                with self.assertRaises(SafetyError):
                    self.executor.environment_retry_preview(first['id'])
                path.write_bytes(old)
        path.write_text(json.dumps(record,indent=3),encoding='utf-8')
        with self.assertRaisesRegex(SafetyError,'preview changed'):
            self.executor.retry_environment(first['id'],preview['revision'],self.environment,self.grant,
                                            approval=lambda _:'approve')
        path.write_bytes(old)
        self.assertEqual(len(self.model.requests),2)
        self.assertEqual(len(self.registry.load('issue_execution')),1)

    def test_environment_retry_blocks_unknown_budget_changed_issue_and_missing_native(self):
        first=self.environment_failure()
        preview=self.executor.environment_retry_preview(first['id'])
        task=self.registry.load('pi_task')[first['task_id']]
        native=self.root/'state/task-sessions'/first['task_id']/'sessions'/task['checkpoint']['file']
        prefix=native.read_bytes()
        native.unlink()
        with self.assertRaises((SafetyError,OSError)):
            self.executor.environment_retry_preview(first['id'])
        native.write_bytes(prefix)
        native.write_bytes(prefix.replace(b'calculator',b'xxxxxxxxxx'))
        with self.assertRaises(SafetyError):
            self.executor.environment_retry_preview(first['id'])
        native.write_bytes(prefix)
        journal=self.executor.runs/first['run_id']/'repair-execution.json'
        original_journal=journal.read_bytes()
        value=json.loads(original_journal)
        value['inflight']={'kind':'environment_command','status':'starting'}
        journal.write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaisesRegex(SafetyError,'process outcome'):
            self.executor.environment_retry_preview(first['id'])
        journal.write_bytes(original_journal)
        with self.registry.transaction() as db:
            db.execute('UPDATE budget SET held=1 WHERE id=1')
        with self.assertRaisesRegex(SafetyError,'unknown'):
            self.executor.environment_retry_preview(first['id'])
        with self.registry.transaction() as db:
            db.execute('UPDATE budget SET held=0 WHERE id=1')
        self.github.issue['updated_at']='2026-10-23T12:00:00Z'
        with self.assertRaisesRegex(SafetyError,'changed after investigation'):
            self.executor.retry_environment(first['id'],preview['revision'],self.environment,self.grant,
                                            approval=lambda _:'approve')
        self.assertEqual(len(self.model.requests),2)
        self.assertEqual(len(self.registry.load('issue_execution')),1)

    def test_manual_close_during_inference_never_reopens_or_refunds_unknown_consumption(self):
        def close(_body,count):
            if count==2:
                self.github.issue.update(state='closed',updated_at='2026-10-20T02:00:00Z')
        self.model.on_request=close
        value=self.run_issue()
        self.assertEqual(value['status'],'stale',value)
        self.assertEqual(self.github.issue['state'],'closed')
        self.assertEqual(self.registry.budget()['requests_reserved_unknown'],50)
        self.registry.close()
        self.registry=UIRegistry(self.root/'state',self.metadata)
        self.executor=self.service()
        self.assertEqual(self.registry.budget()['requests_reserved_unknown'],50)
        requests=len(self.model.requests)
        with self.assertRaisesRegex(SafetyError,'changed after investigation'):
            self.run_issue()
        self.assertEqual(len(self.model.requests),requests)
        self.assertEqual(self.registry.budget()['requests_reserved_unknown'],50)

    def test_live_owner_takeover_and_old_pause_challenge_are_rejected(self):
        checked=[]
        def reject(_):
            identity=next(iter(self.executor.controls))
            view=self.executor.view(identity)
            with self.assertRaisesRegex(SafetyError,'still alive'):
                self.executor.takeover(identity,view['challenge'])
            with self.assertRaisesRegex(SafetyError,'challenge changed'):
                self.executor.request_pause(identity,'0'*64)
            checked.append(True)
            return 'reject'
        value=self.run_issue(reject)
        self.assertEqual(value['status'],'failed_no_replay')
        self.assertEqual(value['workflow_status'],'rejected')
        self.assertTrue(checked)
        self.assertEqual(self.registry.budget()['requests_used'],2)

    def test_readonly_zero_budget_registry_cannot_be_upgraded_into_paid_execution(self):
        self.registry.close()
        prototype=PiProvider(('calculator.py',),self.model.url,max_requests=0)
        binding={**self.metadata,'provider':provider_binding(prototype)}
        self.registry=UIRegistry(self.root/'readonly-state',binding)
        job=IssueIntake(self.github,self.registry,self.spec).investigate(7,self.repo)
        def provider(policy):
            return PiProvider(policy.allowed_paths,self.model.url,readonly_paths=policy.readonly_paths,max_requests=0)
        service=IssueExecution(self.repo.root,self.registry,self.github,self.spec,self.rules,provider)
        with self.assertRaisesRegex(SafetyError,'exhausted/unknown'):
            service.start(job['id'],self.environment,self.grant,approval=lambda _:'approve')
        self.assertFalse(self.model.requests)
        self.assertFalse(self.registry.load('issue_execution'))
        self.assertEqual(self.registry.budget()['request_budget'],0)

    def test_rule_or_provider_binding_change_cannot_replace_original_budget(self):
        def wrong(policy):
            return PiProvider(policy.allowed_paths,self.model.url,readonly_paths=policy.readonly_paths,max_requests=100)
        service=IssueExecution(self.repo.root,self.registry,self.github,self.spec,self.rules,wrong)
        with self.assertRaisesRegex(SafetyError,'provider/budget differs'):
            service.start(self.job['id'],self.environment,self.grant,approval=lambda _:'approve')
        self.assertFalse(self.registry.load('issue_execution'))
        self.assertEqual(self.registry.budget()['request_budget'],50)
        self.assertFalse(self.model.requests)

    def test_original_source_memory_binding_cannot_be_silently_dropped(self):
        self.registry.close()
        binding={**self.metadata,'source_memory':{'fixture_pin':'explicitly_pinned_source'}}
        self.registry=UIRegistry(self.root/'memory-state',binding)
        job=IssueIntake(self.github,self.registry,self.spec).investigate(7,self.repo)
        service=self.service()
        with self.assertRaisesRegex(SafetyError,'unchanged scope and guard'):
            service.start(job['id'],self.environment,self.grant,approval=lambda _:'approve')
        self.assertFalse(self.registry.load('issue_execution'))
        self.assertFalse(self.model.requests)

    def test_pause_before_first_native_turn_can_resume_without_replaying_any_request(self):
        original=self.executor._fresh
        first_call=True
        def pause(row,window):
            nonlocal first_call
            if first_call:
                first_call=False
                view=self.executor.view(row['id'])
                self.executor.request_pause(row['id'],view['challenge'])
            return original(row,window)
        with patch.object(self.executor,'_fresh',side_effect=pause):
            value=self.run_issue()
        self.assertEqual(value['status'],'paused',value)
        self.assertNotIn('task_id',value)
        self.assertEqual(self.registry.budget()['requests_used'],0)
        self.assertFalse(self.model.requests)
        resumed=self.executor.resume(value['id'],value['challenge'],self.environment,self.grant,approval=lambda _:'approve')
        if (self.executor.runs/resumed['run_id']/'workspace').exists():
            self.addCleanup(cleanup_run,self.executor.runs/resumed['run_id'])
        self.assertEqual(resumed['status'],'awaiting-review',resumed)
        self.assertEqual(self.registry.budget()['requests_used'],2)

    def test_freshness_request_budget_does_not_loop_or_execute(self):
        window={'calls':120,'limit':120}
        calls=len(self.github.calls)
        with self.assertRaisesRegex(SafetyError,'bound reached'):
            ExecutionReader(self.executor,window).snapshot(7)
        self.assertEqual(len(self.github.calls),calls)
        self.assertFalse(self.registry.load('issue_execution'))

    def test_controller_reverse_binding_rejects_malformed_metadata_before_calls(self):
        valid={'execution_id':'a'*32,'issue_number':7,'issue_token':'b'*64,
               'policy_binding_sha256':'c'*64,'task_id':'d'*32,'rules_sha256':'e'*64}
        policy=Policy(('calculator.py',),tuple(tuple(c['argv']) for c in self.environment['checks']))
        before=self.repo.snapshot()
        for value in ({'source_repo':'untrusted-override'},dict(valid,issue_number=True),dict(valid,task_id='x'*32)):
            with self.subTest(value=value),self.assertRaisesRegex(SafetyError,'controller Issue/run binding'):
                repair(self.repo.root,'fixture',self.prototype,policy,'approval',self.root/'invalid-runs',issue_binding=value)
        self.assertEqual(before,self.repo.snapshot())
        self.assertFalse((self.root/'invalid-runs').exists())
        self.assertFalse(self.model.requests)

    def test_edit_after_approval_blocks_application_with_known_settled_consumption(self):
        before=self.repo.snapshot()
        def approve(_):
            self.github.issue.update(body='A different task now',updated_at='2026-10-20T02:00:00Z')
            return 'approve'
        value=self.run_issue(approve)
        self.assertEqual(value['status'],'stale',value)
        self.assertEqual(self.registry.budget()['requests_used'],2)
        self.assertEqual(self.registry.budget()['requests_reserved_unknown'],0)
        self.assertEqual(before,self.repo.snapshot())
        self.assertFalse((self.executor.runs/value['run_id']/'environment').exists())

    def test_autonomous_candidate_uses_explicit_rules_and_same_original_budget(self):
        self.registry.close()
        self.rules=maintenance_rules(self.repo,read_roots=['.'],write_roots=['.'],
            check_ids=[self.environment['checks'][0]['id']],mode='auto',allow_auto=True)
        self.metadata={**self.metadata,'maintenance_rules_sha256':self.rules['rules_sha256']}
        self.registry=UIRegistry(self.root/'auto-state',self.metadata)
        self.job=IssueIntake(self.github,self.registry,self.spec).investigate(7,self.repo)
        self.executor=self.service()
        value=self.run_issue(None)
        self.assertEqual(value['status'],'awaiting-review',value)
        self.assertEqual(self.registry.budget()['requests_used'],2)
        record=json.loads((self.executor.runs/value['run_id']/'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['authorization']['decision'],'policy_scope')
        self.assertEqual(record['mode'],'auto')

    def test_actual_owner_exit_requires_original_recovery_then_new_takeover_and_approval(self):
        before=self.repo.snapshot()
        payload={'source':str(self.repo.root),'state':str(self.root/'state'),'metadata':self.metadata,
            'issue':self.github.issue,'spec':self.spec,'rules':self.rules,'model_url':self.model.url,
            'investigation_id':self.job['id'],'environment':self.environment,'grant':self.grant}
        request=self.root/'crash-request.json'
        request.write_text(json.dumps(payload),encoding='utf-8')
        self.registry.close()
        result=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('issue_execution_crash_fixture.py')),str(request)],
                              capture_output=True,timeout=90)
        self.registry=UIRegistry(self.root/'state',self.metadata)
        self.executor=self.service()
        self.assertEqual(result.returncode,91,result.stderr.decode(errors='replace'))
        identity=next(iter(self.registry.load('issue_execution')))
        view=self.executor.view(identity)
        self.assertEqual(view['status'],'running')
        self.assertEqual(self.registry.budget()['requests_used'],2)
        interrupted=json.loads((self.executor.runs/view['run_id']/'run.json').read_text(encoding='utf-8'))
        self.assertEqual(interrupted.get('issue_execution',{}).get('execution_id'),identity,
                         'Reverse Issue/run binding must be durable before planning or editing')
        with self.assertRaisesRegex(SafetyError,'recover the original repair'):
            self.executor.takeover(identity,view['challenge'])
        owner=self.executor.runs/view['run_id']
        preview=inspect_repair(owner)
        self.assertTrue(preview['can_abort'],preview)
        recovered=recover_repair(owner,preview['revision'])
        self.assertTrue(recovered['source_unchanged'])
        reconciled=self.executor.takeover(identity,self.executor.view(identity)['challenge'])
        self.assertEqual(reconciled['status'],'interrupted')
        resumed=self.executor.resume(identity,reconciled['challenge'],self.environment,self.grant,
                                     approval=lambda _:'approve')
        if (self.executor.runs/resumed['run_id']/'workspace').exists():
            self.addCleanup(cleanup_run,self.executor.runs/resumed['run_id'])
        self.assertEqual(resumed['status'],'awaiting-review',resumed)
        self.assertEqual(resumed['task_id'],view['task_id'])
        self.assertEqual(self.registry.budget()['requests_used'],4)
        self.assertEqual(self.registry.budget()['requests_reserved_unknown'],0)
        self.assertEqual(before,self.repo.snapshot(exclude_branch='maintenance/'+resumed['run_id']))
