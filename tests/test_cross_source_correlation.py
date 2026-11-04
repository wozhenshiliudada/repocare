"""Original monitor and Issue controllers; no existing tests, same reviewed behavior."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import unittest

from repo_maintainer.demo import DEMO_TEST
from repo_maintainer.repository_workbench import inspect_repository
from repo_maintainer.repair_comparison import _environment_semantics, _grant_semantics
import test_workbench_correlation as fixtures


class CrossSourceCorrelationTests(unittest.TestCase):
    setUp=fixtures.WorkbenchCorrelationTests.setUp
    start_hub=fixtures.WorkbenchCorrelationTests.start_hub
    finish=fixtures.WorkbenchCorrelationTests.finish
    wait=fixtures.WorkbenchCorrelationTests.wait
    job=fixtures.WorkbenchCorrelationTests.job
    execute=fixtures.WorkbenchCorrelationTests.execute
    selector=fixtures.WorkbenchCorrelationTests.selector
    native=fixtures.WorkbenchCorrelationTests.native
    candidates=fixtures.WorkbenchCorrelationTests.candidates
    record=fixtures.WorkbenchCorrelationTests.record

    def test_reviewed_same_behavior_monitor_owner_prevents_issue_second_dispatch(self):
        self.repo.command('config','user.name','Synthetic Cross Source')
        self.repo.command('config','user.email','cross-source@example.invalid')
        self.repo.command('rm','tests/test_calculator.py')
        self.repo.command('commit','-m','no tests; independently reviewed behavior for two sources')
        self.github.head=self.repo.head
        self.request=self.github.issues[7]['title']+'\n'+self.github.issues[7]['body']
        source=inspect_repository(self.repo.root)
        self.execute('connect',{'path':str(self.repo.root),'revision':source['revision'],
            'allowed_paths':['calculator.py'],'readonly_paths':[], 'allowed_tests':[list(DEMO_TEST)],
            'timeout_seconds':120,'allow_auto':True,'confirmed':True})
        self.workspace=self.client.get('/api/state',headers=self.headers).json()['workspaces'][0]
        self.session=self.hub.workspaces[self.workspace['id']].session
        self.registry=self.session.registry
        config=self.execute('issue_config_preview',{'read_roots':['.'],'write_roots':['.'],
            'trusted_actors':['maintainer'],'mode':'auto','allow_install':False,'allow_network':False,
            'workflow_ids':[1001]},workspace=self.workspace)
        self.assertEqual(config['configuration']['timeout_seconds'],120)
        self.execute('issue_configure',{'config':config['configuration'],'revision':config['revision']},workspace=self.workspace)
        issue=self.execute('issue_investigate',{'number':7},workspace=self.workspace)
        self.assertEqual(self.execute('issue_environment',{'investigation_id':issue['id']},workspace=self.workspace)['status'],'validation_needed')
        proposal={'requirement':self.request,'rationale':'Same explicit input and expected sum reviewed for both sources',
            'limitations':'Only this Python sum','clarification':False,
            'cases':[{'id':'sum','module':'calculator.py','callable':'add','args':[2,3],
                'kwargs':{},'expect':{'kind':'json','value':5},'missing_behavior':False}]}
        self.model.schema_calls=[('propose_validation',proposal)]
        preview=self.execute('issue_validation_preview',{'investigation_id':issue['id']},workspace=self.workspace)
        proposed=self.execute('issue_validation_propose',{'investigation_id':issue['id'],'revision':preview['revision']},workspace=self.workspace)
        self.assertEqual(proposed['status'],'awaiting_validation_review',proposed)
        self.execute('issue_validation_review',{'investigation_id':issue['id'],'revision':proposed['revision'],
            'decision':'accept'},workspace=self.workspace)
        prepared=self.execute('issue_environment',{'investigation_id':issue['id']},workspace=self.workspace)

        payload={'path':str(self.repo.root),'revision':source['revision'],'branch':source['branch'],
            'github_url':'','interval_seconds':60,'timeout_seconds':120,'read_roots':['.'],
            'write_roots':['.'],'check_families':['unittest'],'mode':'auto','allow_local_code':True,
            'allow_install':False,'allow_network':False}
        preview=self.execute('monitor_rules_preview',payload)
        mid=self.execute('monitor_rules_configure',{**preview,'confirmed':True})['monitor_id']
        self.hub.executor.submit(self.hub.monitor.poll,mid).result(timeout=180)
        event=next(iter(self.hub.monitor.registries[mid].load('event').values()))
        self.assertEqual(event['root_preparation']['status'],'validation_needed',event)
        location={'monitor_id':mid,'event_id':event['id']}
        preview=self.execute('monitor_validation_preview',{**location,'request_text':self.request})
        proposed=self.execute('monitor_validation_propose',{**preview,'confirmed':True})
        accepted=self.execute('monitor_validation_review',{**location,'revision':proposed['revision'],'decision':'accept'})
        self.execute('monitor_validation_prepare',{**location,'revision':accepted['revision']})
        self.model.schema_calls=[]
        selectors={'left':self.selector(prepared),'right':{'kind':'monitor',**location}}
        preview=self.execute('maintenance_link_preview',selectors)
        a,b,_,_=self.hub.correlation._pair(selectors['left'],selectors['right'])
        out=os.environ.get('R3_EVIDENCE_DIR')
        if out:
            Path(out).mkdir(parents=True,exist_ok=True)
            (Path(out)/'cross-source-comparison.json').write_text(json.dumps({
                'preview':preview,'contexts':[{'environment':_environment_semantics(c['environment']),
                    'grant':_grant_semantics(c['grant']),'policy':asdict(c['policy']),
                    'model_identity':c['model_identity'],'max_attempts':c['max_attempts']} for c in (a,b)]},
                ensure_ascii=False,indent=2),encoding='utf-8')
        self.assertTrue(preview['preview']['comparison']['comparable'],preview['preview']['comparison'])
        self.assertEqual(self.execute('maintenance_link_confirm',{**preview,'confirmed':True})['status'],'linked')
        before_requests=len(self.model.requests)
        self.assertEqual(before_requests,2)  # One separately reviewed proposal per original source.
        repair=self.execute('monitor_repair_preview',{**location,'max_attempts':3})
        first=self.execute('monitor_repair_start',{**repair,'confirmed':True})
        self.assertEqual(first['status'],'candidate_ready',first)
        self.assertEqual(len(self.model.requests),before_requests+2)
        budget=self.registry.budget();native=self.native();reports=self.candidates()
        second=self.execute('issue_execute',{'investigation_id':prepared['id'],'revision':prepared['revision']},workspace=self.workspace)
        self.assertEqual(second['status'],'related_no_dispatch',second)
        self.assertEqual(len(self.model.requests),before_requests+2)
        self.assertEqual(self.registry.budget(),budget)
        self.assertEqual(self.native(),native);self.assertEqual(self.candidates(),reports)
        self.assertEqual(self.repo.command('status','--porcelain'),b'')
        link=self.hub.correlation.view()['links'][0]
        self.assertEqual(link['status'],'linked',link)
        self.assertEqual({s['label'] for s in link['sources']},{'Issue #7','提交 / CI · '+mid[:8]+' / '+event['id'][:8]})
        owner=link['owner_source']
        self.assertEqual(owner['kind'],'monitor')
        self.assertEqual(owner['monitor_id'],mid);self.assertEqual(owner['event_id'],event['id'])
        self.assertEqual(len(self.model.requests),before_requests+2)  # Projection is read-only.
        from repo_maintainer.workbench_correlation import AUTHORITY
        anchor=self.hub.monitor.registries[mid]
        authority=next(iter(anchor.load(AUTHORITY).values()))
        authority_id=next(iter(anchor.load(AUTHORITY)))
        anchor.put(AUTHORITY,authority_id,{**authority,'execution_id':'forged-owner'})
        blocked=self.hub.correlation.view()['links'][0]
        self.assertEqual(blocked['status'],'blocked');self.assertIsNone(blocked['owner_source'])
        anchor.put(AUTHORITY,authority_id,authority)
        self.assertEqual(self.hub.correlation.view()['links'][0]['status'],'linked')
        self.assertEqual(len(self.model.requests),before_requests+2)
        self.record('monitor-owner-issue-linked',monitor_result=first,issue_result=second)


if __name__=='__main__':unittest.main()
