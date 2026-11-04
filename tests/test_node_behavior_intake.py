"""Ordinary Issue and monitor original action dispatchers, local SSE only."""
import json
import hashlib
import os
from pathlib import Path
import unittest

import test_issue_workbench as issue_fixture
import test_monitor_validation as monitor_fixture
from repo_maintainer.workflow_behavior import verify_pair
from repo_maintainer.git import RepositoryGateway


ORIGINAL='export function total(a,b){return a-b;}\n'
FIXED='export function total(a,b){return a+b;}\n'
REQUEST='total(2,3) must return 5; preserve signed operands.'


def convert(repo):
    repo.command('config','user.name','Synthetic Node Intake')
    repo.command('config','user.email','node-intake@example.invalid')
    repo.command('rm','calculator.py','tests/test_calculator.py')
    (repo.root/'calculator.mjs').write_text(ORIGINAL,encoding='utf-8')
    repo.command('add','calculator.mjs');repo.command('commit','-m','synthetic Node project without tests')


def proposal():
    return {'requirement':REQUEST,'rationale':'Explicit Issue business result',
        'limitations':'Node positional JSON call only','clarification':False,
        'cases':[{'id':'sum','module':'calculator.mjs','callable':'total','args':[2,3],
            'kwargs':{},'expect':{'kind':'json','value':5},'missing_behavior':False}]}


def repair_call():
    return ('propose_repair',{'diagnosis':'Required addition','risk':'Synthetic fixture',
        'edits':[{'path':'calculator.mjs','replacements':[{'oldText':'return a-b;','newText':'return a+b;'}]}]})


def preserve_intake(test, fixture, name):
    """Copy original synthetic evidence before the fixture's normal safe cleanup."""
    if not os.environ.get('R1_JS_EVIDENCE_DIR'):return
    def save():
        destination=Path(os.environ['R1_JS_EVIDENCE_DIR'])/name
        destination.mkdir(parents=True,exist_ok=False)
        registries=({'issue':fixture.session.registry} if name=='issue' else fixture.hub.monitor.registries)
        for identity,registry in registries.items():
            value={'budget':registry.budget(),'objects':{kind:registry.load(kind) for kind in
                ('validation_proposal','pi_task','attempt','issue_execution','monitor_validation_request','event')}}
            (destination/(identity+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        (destination/'sse-payloads.json').write_text(json.dumps(fixture.model.requests,ensure_ascii=False,indent=2),encoding='utf-8')
        index=[]
        for path in fixture.root.rglob('*'):
            if path.is_file() and (path.suffix=='.jsonl' or path.name in {'run.json','environment.json','contract.json'}
                                  or path.name.endswith(('.request.json','.actual.json'))):
                raw=path.read_bytes();target=f'original-{len(index):03d}'+path.suffix
                (destination/target).write_bytes(raw)
                index.append({'original':str(path),'copy':target,'sha256':hashlib.sha256(raw).hexdigest()})
        (destination/'originals.json').write_text(json.dumps(index,indent=2),encoding='utf-8')
    test.addCleanup(save)


class NodeBehaviorIntakeTests(unittest.TestCase):
    def test_ordinary_issue_review_original_new_approval_and_node_candidate(self):
        f=issue_fixture.IssueWorkbenchTests();f.setUp();self.addCleanup(f.doCleanups)
        preserve_intake(self,f,'issue')
        convert(f.repo);f.github.head=f.repo.head
        f.github.issue.update(title='Fix Node total',body=REQUEST)
        before=f.repo.snapshot();f.configure()
        job=f.execute('issue_investigate',{'number':7})
        initial=f.execute('issue_environment',{'investigation_id':job['id']})
        self.assertEqual('validation_needed',initial['status'])
        f.model.schema_calls=[('propose_validation',proposal())]
        preview=f.execute('issue_validation_preview',{'investigation_id':job['id']})
        row=f.execute('issue_validation_propose',{'investigation_id':job['id'],'revision':preview['revision']})
        self.assertEqual('awaiting_validation_review',row['status'])
        self.assertEqual('node-behavior-v1',row['contract']['kind'])
        f.execute('issue_validation_review',{'investigation_id':job['id'],'revision':row['revision'],'decision':'accept'})
        env=f.execute('issue_environment',{'investigation_id':job['id']})
        self.assertEqual('node',env['environment']['runtime'])
        f.model.schema_calls=[repair_call()]
        job=f.post('issue_execute',{'investigation_id':env['id'],'revision':env['revision']})
        approval=f.pending()
        f.post('issue_decide',{'approval_id':approval['id'],'revision':approval['revision'],'decision':'approve'})
        result=f.wait(job);self.assertEqual('finished',result['status'],result)
        run=f.execute('issue_result',{'execution_id':result['result']['id']})
        self.assertEqual('candidate_ready',run['status'])
        self.assertEqual(['test_failed'],[r['category'] for r in run['baseline_tests']])
        self.assertEqual(['passed'],[r['category'] for r in run['candidate_tests']])
        self.assertEqual(2,len(f.model.requests))
        self.assertEqual(0,f.session.registry.budget()['requests_reserved_unknown'])
        self.assertEqual(before,f.repo.snapshot(exclude_branch='maintenance/'+result['result']['run_id']))

    def test_ordinary_monitor_review_original_environment_ledger_and_node_candidate(self):
        f=monitor_fixture.MonitorValidationTests();f.setUp();self.addCleanup(f.doCleanups)
        preserve_intake(self,f,'monitor')
        convert(f.git);f.request=REQUEST
        before=f.git.snapshot()
        mid=f.configure(allow_local_code=True,mode='auto',check_families=['node-native'])
        f.worker(f.hub.monitor.poll,mid)
        event=f.view()['events'][0]
        self.assertEqual('validation_needed',event['root_preparation']['status'],event)
        f.proposal=lambda function='total',missing=False:proposal()
        row=f.propose(mid,event)
        self.assertEqual('node-behavior-v1',row['contract']['kind'])
        accepted=f.accept(mid,event,row)
        current=f.prepare_validation(mid,event,accepted)
        self.assertEqual('node',current['root_preparation']['environment']['runtime'])
        f.model.schema_calls=[repair_call()]
        result=f.execute(f.repair_preview(mid,current))
        self.assertEqual('candidate_ready',result['status'],result)
        session=f.session(mid,current);f.preserve_cleanup(session)
        run_id=next(reversed(session.runs))
        record=json.loads((session.runs_dir/run_id/'run.json').read_text(encoding='utf-8'))
        self.assertTrue(verify_pair(record,RepositoryGateway(session.repo),session.policy)['candidate_verified'])
        self.assertEqual('node-behavior-result-v1',record['candidate_tests'][0]['behavior_receipt']['kind'])
        budget=f.hub.monitor.registries[mid].budget()
        self.assertEqual((2,0),tuple(budget[k] for k in ('requests_used','requests_reserved_unknown')))
        self.assertEqual(0,session.registry.budget()['requests_used'])
        self.assertEqual(before,f.git.snapshot())
