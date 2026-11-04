"""Ordinary authenticated Issue actions: natural-language goal to original repair."""
import unittest
import test_issue_workbench as fixtures
from repo_maintainer.models import digest


class IssueValidationTests(unittest.TestCase):
    def test_no_existing_tests_proposal_review_original_approval_and_candidate(self):
        self._cycle(no_tests=True)

    def test_green_existing_tests_missing_behavior_requires_independent_new_case(self):
        self._cycle(no_tests=False)

    def _cycle(self,no_tests):
        f=fixtures.IssueWorkbenchTests();f.setUp();self.addCleanup(f.doCleanups)
        f.repo.command('config','user.name','Synthetic Fixture')
        f.repo.command('config','user.email','synthetic@example.invalid')
        if no_tests:
            f.repo.command('rm','tests/test_calculator.py')
            f.repo.command('commit','-m','fixture without existing tests')
            function='add';missing=False
        else:
            p=f.repo.root/'calculator.py';p.write_bytes(p.read_bytes().replace(b'a - b',b'a + b'))
            f.repo.command('add','calculator.py');f.repo.command('commit','-m','green existing behavior')
            f.github.issue.update(title='Provide bounded_add',body='bounded_add(2,3) must return 5. Keep existing add behavior.')
            function='bounded_add';missing=True
        f.github.head=f.repo.head
        before=f.repo.snapshot()
        f.configure()
        job=f.execute('issue_investigate',{'number':7})
        self.assertEqual('investigated',job['status'])
        if no_tests:
            env=f.execute('issue_environment',{'investigation_id':job['id']})
            self.assertEqual('validation_needed',env['status']);self.assertEqual([],f.model.requests)
        proposal={'requirement':f.github.issue['body'],'rationale':'Explicit input and expected result in Issue',
            'limitations':'Only this stated Python behavior','clarification':False,
            'cases':[{'id':'requested_sum','module':'calculator.py','callable':function,'args':[2,3],
                'kwargs':{},'expect':{'kind':'json','value':5},'missing_behavior':missing}]}
        f.model.schema_calls=[('propose_validation',proposal)]
        preview=f.execute('issue_validation_preview',{'investigation_id':job['id']})
        original=f.session.registry.budget()
        result=f.execute('issue_validation_propose',{'investigation_id':job['id'],'revision':preview['revision']})
        self.assertEqual('awaiting_validation_review',result['status'])
        self.assertFalse(result['runtime_authorized']);self.assertEqual(1,len(f.model.requests))
        if no_tests:
            # Inject loss of only the derived result row, after original Native
            # commit and request settlement. Recovery must not call the model.
            import copy
            stored=copy.deepcopy(f.session.registry.load('validation_proposal')[result['id']])
            for key in ('proposal','proposal_sha256','contract'):stored.pop(key)
            stored['status']='interrupted_no_replay'
            f.session.registry.put('validation_proposal',stored['id'],stored)
            view=f.session.state()['issue_workbench']['validations'][0]
            budget=f.session.registry.budget()
            result=f.execute('issue_validation_reconcile',{'investigation_id':job['id'],'revision':view['revision']})
            self.assertEqual('awaiting_validation_review',result['status'])
            self.assertEqual(budget,f.session.registry.budget());self.assertEqual(1,len(f.model.requests))
            self.assertEqual(1,len(f.session.registry.load('validation_reconcile')))
        blocked=f.execute('issue_environment',{'investigation_id':job['id']})
        self.assertEqual('validation_review_required',blocked['status'])
        self.assertEqual({},f.session.registry.load('issue_execution'))
        accepted=f.execute('issue_validation_review',{'investigation_id':job['id'],
                         'revision':result['revision'],'decision':'accept'})
        self.assertEqual('accepted',accepted['status'])
        p=f.execute('issue_environment',{'investigation_id':job['id']})
        self.assertEqual(accepted['contract'],p['environment']['behavior_contract'])
        self.assertEqual(digest((f.github.issue['title']+'\n'+f.github.issue['body']).encode()),
                         p['environment']['behavior_contract']['request_sha256'])
        if no_tests:
            f.model.schema_calls=[]
        else:
            f.model.schema_calls=[('read_source',{'path':'calculator.py'}),('propose_repair',{
                'diagnosis':'Provide explicitly requested missing public function','risk':'fixture',
                'edits':[{'path':'calculator.py','replacements':[{'oldText':'return a + b',
                    'newText':'return a + b\n\ndef bounded_add(a, b):\n    return a + b'}]}]})]
        execution=f.post('issue_execute',{'investigation_id':p['id'],'revision':p['revision']})
        approval=f.pending()
        decision=f.post('issue_decide',{'approval_id':approval['id'],'revision':approval['revision'],'decision':'approve'})
        self.assertEqual('decision_recorded',decision['status'])
        outcome=f.wait(execution);self.assertEqual('finished',outcome['status'],outcome)
        row=outcome['result'];self.assertEqual('awaiting-review',row['status'],row)
        run=f.execute('issue_result',{'execution_id':row['id']})
        self.assertEqual('candidate_ready',run['status'])
        self.assertTrue(run['rounds'][0]['behavior_comparison']['candidate_verified'])
        import json
        record=json.loads((f.session.issue_workbench._execution(row['id'])[2].runs/row['run_id']/'run.json').read_text(encoding='utf-8'))
        from repo_maintainer.workflow_behavior import verify_pair
        from repo_maintainer.models import Policy
        policy=Policy(tuple(p['policy']['allowed_paths']),tuple(tuple(c) for c in p['policy']['allowed_tests']),
                      p['policy']['timeout_seconds'],tuple(p['policy']['readonly_paths']))
        self.assertTrue(verify_pair(record,f.repo,policy)['candidate_verified'])
        visible=[c for c in run['candidate_tests'] if c.get('command_kind')=='controller_behavior_contract']
        self.assertEqual(1,len(visible));self.assertEqual('recorded_summary_only',visible[0]['behavior_receipt']['scope'])
        self.assertTrue(any(t['returncode']!=0 for t in run['baseline_tests']))
        self.assertTrue(all(t['returncode']==0 for t in run['candidate_tests']))
        self.assertEqual(2,len(f.session.registry.load('pi_task')))
        self.assertEqual(original['request_budget'],f.session.registry.budget()['request_budget'])
        self.assertEqual(0,f.session.registry.budget()['requests_reserved_unknown'])
        self.assertEqual(before,f.repo.snapshot(exclude_branch='maintenance/'+row['run_id']))
