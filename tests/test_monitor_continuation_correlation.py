"""Original correlated monitor/Issue/Native and local synthetic Pi only."""
import copy
import json
from pathlib import Path
import time
import unittest

from repo_maintainer.demo import DEMO_TEST
from repo_maintainer.models import SafetyError
from repo_maintainer.repository_workbench import inspect_repository
from repo_maintainer.workbench_correlation import RECEIPTS, MONITOR_CONTINUATION_PURPOSE
from repo_maintainer.maintenance_correlation import CLAIMS
import test_workbench_correlation as fixtures


class MonitorContinuationCorrelationTests(unittest.TestCase):
    setUp=fixtures.WorkbenchCorrelationTests.setUp
    start_hub=fixtures.WorkbenchCorrelationTests.start_hub
    wait=fixtures.WorkbenchCorrelationTests.wait
    job=fixtures.WorkbenchCorrelationTests.job
    execute=fixtures.WorkbenchCorrelationTests.execute
    selector=fixtures.WorkbenchCorrelationTests.selector
    native=fixtures.WorkbenchCorrelationTests.native
    candidates=fixtures.WorkbenchCorrelationTests.candidates

    def worker(self,function,*args,composite=False):
        # Explicitly approved for this three-round composite fixture only.
        return self.hub.executor.submit(function,*args).result(timeout=600 if composite else 180)

    def diagnostic(self,phase):
        # Only this disposable fixture; never credentials or an existing account.
        data={'phase':phase,'time':time.time(),'requests':len(self.model.requests),
            'jobs':[{k:row.get(k) for k in ('action','status','error','result')}
                    for row in self.hub.jobs.values()],
            'monitor_budgets':[r.budget() for r in self.hub.monitor.registries.values()],
            'root_elapsed_seconds':getattr(self,'root_elapsed',None),
            'continuation_elapsed_seconds':getattr(self,'continuation_elapsed',None),
            'monitor_executions':[{'monitor_id':mid,'event_id':eid,'execution':event.get('root_execution')}
                for mid,r in self.hub.monitor.registries.items() for eid,event in r.load('event').items()],
            'session_jobs':[{k:job.get(k) for k in ('id','action','status','error','result')}
                for server in self.hub.monitor.servers.values() for job in server.session.jobs.values()]}
        if self.model.requests:
            data['last_messages']=self.model.requests[-1]['messages'][-3:]
        data['runs']=[]
        for server in self.hub.monitor.servers.values():
            for rid in server.session.runs:
                path=server.session.runs_dir/rid/'run.json'
                if path.is_file():
                    try:
                        record=json.loads(path.read_text(encoding='utf-8'))
                        data['runs'].append({k:record.get(k) for k in ('run_id','status','rounds','finished_at')})
                    except ValueError:data['runs'].append({'run_id':rid,'reading':'write-in-progress'})
        path=Path(__file__).resolve().parents[1]/'.demo'/('r3-purpose-stages-'+self.root.name+'.jsonl')
        with path.open('a',encoding='utf-8') as stream:stream.write(json.dumps(data,ensure_ascii=False)+'\n')

    def finish(self):
        self.client.close();self.hub.close()
        self.diagnostic('closed-original-queue-terminal')
        from repo_maintainer.workflow import cleanup_run
        for run in self.root.rglob('run.json'):
            if (run.parent/'workspace').exists():cleanup_run(run.parent)
        self.model.close();self.temp.cleanup()

    def correlated_parent(self):
        # Same reviewed negative-operand oracle, not weaker checks/timeout tricks.
        self.repo.command('config','user.name','Synthetic Correlated Continuation')
        self.repo.command('config','user.email','continuation@example.invalid')
        self.repo.command('rm','tests/test_calculator.py')
        self.repo.command('commit','-m','same reviewed behavior for correlated sources')
        self.github.head=self.repo.head
        request=self.github.issues[7]['title']+'\n'+self.github.issues[7]['body']
        source=inspect_repository(self.repo.root)
        self.execute('connect',{'path':str(self.repo.root),'revision':source['revision'],
            'allowed_paths':['calculator.py'],'readonly_paths':[],'allowed_tests':[list(DEMO_TEST)],
            'timeout_seconds':120,'allow_auto':True,'confirmed':True})
        self.workspace=self.client.get('/api/state',headers=self.headers).json()['workspaces'][0]
        self.session=self.hub.workspaces[self.workspace['id']].session
        self.registry=self.session.registry
        value=self.execute('issue_config_preview',{'read_roots':['.'],'write_roots':['.'],
            'trusted_actors':['maintainer'],'mode':'auto','allow_install':False,'allow_network':False,
            'workflow_ids':[1001]},workspace=self.workspace)
        self.execute('issue_configure',{'config':value['configuration'],'revision':value['revision']},workspace=self.workspace)
        issue=self.execute('issue_investigate',{'number':7},workspace=self.workspace)
        self.assertEqual(self.execute('issue_environment',{'investigation_id':issue['id']},workspace=self.workspace)['status'],'validation_needed')
        proposal={'requirement':request,'rationale':'Same separately approved negative operand check',
            'limitations':'Only this Python addition','clarification':False,
            'cases':[{'id':'negative','module':'calculator.py','callable':'add','args':[-2,3],
                'kwargs':{},'expect':{'kind':'json','value':1},'missing_behavior':False}]}
        self.model.schema_calls=[('propose_validation',proposal)]
        value=self.execute('issue_validation_preview',{'investigation_id':issue['id']},workspace=self.workspace)
        proposed=self.execute('issue_validation_propose',{'investigation_id':issue['id'],'revision':value['revision']},workspace=self.workspace)
        self.execute('issue_validation_review',{'investigation_id':issue['id'],'revision':proposed['revision'],'decision':'accept'},workspace=self.workspace)
        prepared=self.execute('issue_environment',{'investigation_id':issue['id']},workspace=self.workspace)
        value=self.execute('monitor_rules_preview',{'path':str(self.repo.root),'revision':source['revision'],
            'branch':source['branch'],'github_url':'','interval_seconds':60,'timeout_seconds':120,
            'read_roots':['.'],'write_roots':['.'],'check_families':['unittest'],'mode':'auto',
            'allow_local_code':True,'allow_install':False,'allow_network':False})
        mid=self.execute('monitor_rules_configure',{**value,'confirmed':True})['monitor_id']
        self.worker(self.hub.monitor.poll,mid)
        registry=self.hub.monitor.registries[mid]
        event=next(iter(registry.load('event').values()))
        location={'monitor_id':mid,'event_id':event['id']}
        value=self.execute('monitor_validation_preview',{**location,'request_text':request})
        proposed=self.execute('monitor_validation_propose',{**value,'confirmed':True})
        accepted=self.execute('monitor_validation_review',{**location,'revision':proposed['revision'],'decision':'accept'})
        self.execute('monitor_validation_prepare',{**location,'revision':accepted['revision']})
        self.model.schema_calls=[]
        self.assertFalse(registry.load(CLAIMS))  # Readonly Native grants no repair owner.
        value=self.execute('maintenance_link_preview',{'left':self.selector(prepared),'right':{'kind':'monitor',**location}})
        self.assertTrue(value['preview']['comparison']['comparable'],value)
        self.execute('maintenance_link_confirm',{**value,'confirmed':True})
        def wrong_candidate(body,count):
            users=[i for i,m in enumerate(body['messages']) if m['role']=='user']
            current=body['messages'][users[-1]+1:]
            read=next((m for m in current if m['role']=='tool' and 'before_sha256' in m.get('content','')),None)
            self.model.schema_calls=[]
            if read:
                current_source=json.loads(read['content'])['content']
                old=next(line.strip() for line in current_source.splitlines() if line.strip().startswith('return '))
                # Every round submits a fresh valid proposal for its actual
                # snapshot. Repeating the same replacement would test format
                # retries/consumption rather than a safely finished failure.
                self.model.schema_calls=[('read_source',{'path':'calculator.py'}),('propose_repair',{
                    'diagnosis':'Distinct synthetic failing candidate','risk':'local fixture only',
                    'edits':[{'path':'calculator.py','replacements':[{
                        'oldText':old,'newText':'return a - b # wrong round '+str(count)}]}]})]
        self.model.on_request=wrong_candidate
        value=self.execute('monitor_repair_preview',{**location,'max_attempts':3})
        self.diagnostic('before-root-repair')
        began=time.monotonic()
        try:
            result=self.worker(self.hub.monitor.execution.start,{**value,'confirmed':True},composite=True)
        finally:
            self.root_elapsed=time.monotonic()-began
            self.diagnostic('after-root-repair-wait')
        self.assertEqual(result['status'],'failed_no_replay',result)
        self.model.on_request=None;self.model.schema_calls=[]
        event=registry.load('event')[event['id']]
        self.assertEqual(registry.budget()['requests_used'],7)  # proposal 1 + three repair rounds of 2.
        return mid,event,prepared

    def unchanged(self,registries):
        return (len(self.model.requests),self.native(),[r.budget() for r in registries],
                [copy.deepcopy(r.load('pi_task')) for r in registries])

    def test_exact_owner_continuation_keeps_original_claim_and_native_and_blocks_other_purposes(self):
        mid,event,prepared=self.correlated_parent()
        registry=self.hub.monitor.registries[mid]
        location={'monitor_id':mid,'event_id':event['id'],'run_id':None,'mode':'auto'}
        value=self.worker(self.hub.monitor.continuation.inspect,location)
        self.assertTrue(value['preview']['can_continue'],value)
        payload={**value,'confirmed':True}
        root=copy.deepcopy(event['root_execution']['correlation'])
        claim=copy.deepcopy(registry.load(CLAIMS))
        old_report=(self.hub.monitor.root/mid/'r'/event['id']/event['root_execution']['run_id']/'run.json').read_bytes()
        original_task=value['preview']['task']['task_id']
        original_revision=registry.load('pi_task')[original_task]['revision']
        before=self.unchanged([registry,self.registry])
        # A source holding a readonly proposal is still a repair non-owner.
        result=self.execute('issue_execute',{'investigation_id':prepared['id'],'revision':prepared['revision']},workspace=self.workspace)
        self.assertEqual(result['status'],'related_no_dispatch')
        self.assertEqual(self.unchanged([registry,self.registry]),before)
        # Live pause, unknown consumption, changed source and readonly purpose
        # never allocate a replacement Native or consume a model request.
        self.worker(self.hub.monitor.operate,'monitor_pause',{'monitor_id':mid})
        with self.assertRaises(SafetyError):self.worker(self.hub.monitor.continuation.start,payload)
        self.worker(self.hub.monitor.operate,'monitor_resume',{'monitor_id':mid})
        self.assertEqual(self.unchanged([registry,self.registry]),before)
        attempt,_=self.worker(lambda:registry.reserve(1))
        unknown=self.unchanged([registry,self.registry])
        with self.assertRaises(SafetyError):self.worker(self.hub.monitor.continuation.start,payload)
        self.assertEqual(self.unchanged([registry,self.registry]),unknown)
        self.worker(lambda:registry.settle(attempt,{'requests':0},success=True))  # Synthetic fixture reservation only.
        source=self.hub.monitor.root/mid/'events'/event['id']/'source'/'calculator.py'
        raw=source.read_bytes();source.write_bytes(raw+b'\n# changed\n')
        with self.assertRaises(SafetyError):self.worker(self.hub.monitor.continuation.start,payload)
        source.write_bytes(raw)
        altered=copy.deepcopy(event);altered['root_execution']['correlation']['purpose']='validation-proposal-v1'
        self.worker(registry.put,'event',event['id'],altered)
        with self.assertRaisesRegex(SafetyError,'repair|负责人'):self.worker(self.hub.correlation.verify_monitor,mid,altered)
        self.worker(registry.put,'event',event['id'],event)
        self.assertEqual(self.unchanged([registry,self.registry]),before)
        # Two concurrent queue submissions use the same exact operator receipt.
        # Only the first may extend the original task; the second cannot replay.
        began=time.monotonic()
        futures=[self.hub.executor.submit(self.hub.monitor.continuation.start,payload) for _ in range(2)]
        result=futures[0].result(timeout=600)
        self.continuation_elapsed=time.monotonic()-began
        self.assertEqual(result['status'],'candidate_ready',result)
        with self.assertRaises(SafetyError):futures[1].result(timeout=180)
        rows=registry.load('monitor_continuation');self.assertEqual(len(rows),1)
        row=next(iter(rows.values()));receipt=row['correlation']
        self.assertEqual(receipt['purpose'],MONITOR_CONTINUATION_PURPOSE)
        self.assertEqual(registry.load(RECEIPTS)[row['id']],receipt)
        self.assertEqual(registry.load(CLAIMS),claim)
        self.assertEqual(event,registry.load('event')[event['id']])
        self.assertEqual((self.hub.monitor.root/mid/'r'/event['id']/event['root_execution']['run_id']/'run.json').read_bytes(),old_report)
        self.assertEqual(registry.load('pi_task')[original_task]['revision'],original_revision+1)
        self.assertEqual(registry.budget()['requests_used'],before[2][0]['requests_used']+2)
        self.assertEqual(len(self.model.requests),before[0]+2)
        session=self.hub.monitor.servers[mid,'root-'+event['id']].session
        from repo_maintainer.workflow import cleanup_run
        for rid in session.runs:
            if (session.runs_dir/rid/'workspace').exists():self.addCleanup(cleanup_run,session.runs_dir/rid)
        after=self.unchanged([registry,self.registry])
        for mutate in (lambda r:r.pop('correlation'),
                       lambda r:r['correlation'].update(purpose='validation-proposal-v1'),
                       lambda r:r.update(correlation=copy.deepcopy(root)),
                       lambda r:r.update(kind='readonly')):
            bad=copy.deepcopy(row);mutate(bad)
            self.worker(registry.put,'monitor_continuation',row['id'],bad)
            with self.assertRaisesRegex(SafetyError,'purpose|回执'):self.worker(self.hub.monitor.execution.guard,mid,event['id'])
            self.worker(registry.put,'monitor_continuation',row['id'],row)
        self.assertEqual(self.unchanged([registry,self.registry]),after)
        self.diagnostic('completed-continuation-proof')


if __name__=='__main__':unittest.main()
