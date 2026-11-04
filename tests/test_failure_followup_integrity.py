"""Cheap derived-link tampering checks and deterministic Native-reader contention."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from filelock import FileLock, Timeout

from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.maintenance_monitor import MaintenanceMonitor
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.monitor_failure_followup import (
    CLAIMS, KIND, MonitorFailureFollowup, child_event_id, selection_key, successor_lineage)

MID, PARENT, RUN, GRANT, HEAD = 'a'*16, 'b'*16, 'c'*32, 'd'*32, 'e'*40


class Registry:
    def __init__(self, rows=None, path=None):
        self.rows, self.path = rows or {}, path or Path('original-registry.sqlite3')
    def load(self, kind):return deepcopy(self.rows.get(kind,{}))
    def budget(self, *, include_usage=False):
        result = {'request_budget': 8, 'requests_used': 2, 'requests_reserved_unknown': 0, 'requests_remaining': 6}
        if include_usage:
            result.update(requests_reserved_active=0, requests_ended_unknown=0)
        return result


class FailureFollowupIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.old = {'repository':'example/repo','repository_id':901,'head_sha':HEAD,
                    'workflow_id':301,'run_id':101,'attempt':1}
        self.pin = {**self.old,'attempt':2}
        self.key = selection_key(self.pin); self.eid = child_event_id(MID,self.key)
        self.core = {'monitor_id':MID,'event_id':PARENT,'max_successors':2,
            'parent':{'run_id':RUN,'head_sha':HEAD,'workflow_id':301,'ci_failure':self.old},
            'model_authorization':{'id':'1'*32,'core_sha256':'2'*64}}
        self.row = {'id':GRANT,'core':self.core,'core_sha256':fingerprint(self.core),
            'enabled':True,'children':[{'event_id':self.eid,'selection_key':self.key}]}
        link = successor_lineage(self.row,self.key)
        self.claim = {'id':self.key,'grant_id':GRANT,'event_id':self.eid,'lineage':deepcopy(link),
                      'ci_failure':self.pin,'status':'started'}
        self.event = {'id':self.eid,'sha':HEAD,'failure_successor':deepcopy(link),
                      'model_observation':deepcopy(self.core['model_authorization'])}
        self.registry = Registry({KIND:{GRANT:self.row},CLAIMS:{self.key:self.claim}})
        self.monitor = SimpleNamespace(hub=SimpleNamespace(),registries={MID:self.registry})
        self.manager = MonitorFailureFollowup(self.monitor)

    def checked(self):return self.manager._bound_claim(MID,self.row,self.event)
    def sync_claim(self):self.registry.rows[CLAIMS][self.key] = deepcopy(self.claim)

    def test_exact_derived_claim_is_accepted_without_model_or_budget_operations(self):
        before = deepcopy(self.registry.rows)
        self.assertEqual(self.checked(),self.claim)
        self.assertEqual(before,self.registry.rows)

    def test_both_lineage_copies_cannot_replace_authorized_parent_fields(self):
        for field,value in (('parent_event_id','3'*16),('parent_run_id','4'*32),
                            ('parent_sha256','5'*64),('grant_id','6'*32)):
            with self.subTest(field=field):
                event,claim = deepcopy(self.event),deepcopy(self.claim)
                event['failure_successor'][field] = value;claim['lineage'][field] = value
                self.registry.rows[CLAIMS][self.key] = claim
                with self.assertRaises(SafetyError):self.manager._bound_claim(MID,self.row,event)

    def test_claim_pin_cannot_change_while_persisted_selection_key_stays_old(self):
        self.claim['ci_failure']['attempt'] = 3; self.sync_claim()
        with self.assertRaises(SafetyError):self.checked()

    def test_two_consistent_child_id_copies_cannot_replace_derived_event_id(self):
        self.event['id'] = self.claim['event_id'] = '7'*16
        self.row['children'][0]['event_id'] = '7'*16;self.sync_claim()
        with self.assertRaises(SafetyError):self.checked()

    def test_missing_duplicate_or_conflicting_child_references_are_rejected(self):
        for references in ([],self.row['children']*2,
            [*self.row['children'],{'event_id':'8'*16,'selection_key':self.key}],
            [*self.row['children'],{'event_id':self.eid,'selection_key':'9'*64}]):
            with self.subTest(references=references):
                row = {**self.row,'children':references}
                with self.assertRaises(SafetyError):self.manager._bound_claim(MID,row,self.event)

    def test_preparation_and_execution_pins_must_match_the_exact_claim(self):
        for field,body in (('root_preparation',{'ci_failure_binding':{**self.pin,'attempt':3}}),
                           ('root_execution',{'binding':{'ci_failure':{**self.pin,'attempt':3}}})):
            with self.subTest(field=field):
                event = {**self.event,field:body}
                with self.assertRaises(SafetyError):self.manager._bound_claim(MID,self.row,event)

    def test_completed_validation_also_recomputes_authorization_and_derived_links(self):
        self.event['root_execution'] = {'status':'candidate_ready'}
        self.event['failure_successor']['parent_run_id'] = '3'*32
        self.claim['lineage'] = deepcopy(self.event['failure_successor']);self.sync_claim()
        with patch.object(self.manager,'_parent',return_value=self.core['parent']):
            with self.assertRaises(SafetyError):self.manager.validate_event(MID,self.event)
        self.event['failure_successor'] = successor_lineage(self.row,self.key)
        self.claim['lineage'] = deepcopy(self.event['failure_successor']);self.sync_claim()
        self.row['core_sha256'] = '0'*64
        self.registry.rows[KIND][GRANT] = self.row
        with patch.object(self.manager,'_parent',return_value=self.core['parent']):
            with self.assertRaises(SafetyError):self.manager.validate_event(MID,self.event)

    def test_selection_key_rejects_boolean_identifiers(self):
        with self.assertRaises(SafetyError):selection_key({**self.pin,'attempt':True})


class FailureFollowupNativeReaderTests(unittest.TestCase):
    def test_parent_native_verification_does_not_compete_with_monitor_view_reader(self):
        self._native_reader('monitor')

    def test_direct_parent_state_reader_uses_the_exact_parent_session_lock(self):
        self._native_reader('direct')

    def test_unrelated_native_writer_still_causes_original_filelock_rejection(self):
        self._native_reader('external_writer')

    def _native_reader(self, reader_kind):
        """Real monitor.view and real nonblocking FileLock; no Pi/model/Git process."""
        with tempfile.TemporaryDirectory(prefix='followup-lock-') as directory:
            root = Path(directory); source_root = root/MID/'events'/PARENT/'source'
            lock_file = root/'parent-native-session.lock'
            parent_session_lock = threading.RLock()
            view_holds, release_view, reached_native, native_entered, native_done = (threading.Event() for _ in range(5))
            policy = Policy(('calculator.py',),(('python','-m','unittest'),),30,())
            policy_json = json.loads(json.dumps(asdict(policy)))
            pin = {'repository':'example/repo','repository_id':901,'head_sha':HEAD,
                   'workflow_id':301,'run_id':101,'attempt':1}
            prep = {'status':'reproduced','root_rules_sha256':'f'*64,'ci_failure_binding':pin,
                    'ci_diagnosis':{'ci_evidence':{'run':{'workflow_id':301},'failed_steps':[], 'log_excerpt':''}},
                    'baseline':{'run_id':'0'*32}}
            prep['result_sha256'] = fingerprint(prep)
            binding = {'monitor_id':MID,'event_id':PARENT,'base_sha':HEAD,'root_rules_sha256':'f'*64,
                       'ci_failure':pin,'preparation_sha256':prep['result_sha256'],
                       'baseline_sha256':'b'*64,'policy_sha256':fingerprint(policy_json)}
            execution = {'finished_at':'done','status':'candidate_ready','binding':binding,'run_id':RUN}
            event = {'id':PARENT,'sha':HEAD,'root_execution':execution,'root_preparation':prep,'plan_id':None}
            registry = Registry({'event':{PARENT:event}},root/'original.sqlite3')
            monitor = object.__new__(MaintenanceMonitor)
            monitor.root,monitor.hub,monitor.lock = root,SimpleNamespace(),threading.RLock()
            monitor.records,monitor.registries = {MID:{'id':MID,'spec':{}}},{MID:registry}
            config = {'sha256':'f'*64,'settings':{}}
            monitor.root_rules = SimpleNamespace(current=lambda *args,**kw:deepcopy(config))
            monitor.model_authorization = SimpleNamespace(view=lambda *_:None)
            monitor.acceptance_watch = SimpleNamespace(records={})
            monitor.validation = SimpleNamespace(projection=lambda *_:None)
            manager = monitor.failure_followup = MonitorFailureFollowup(monitor)

            def native_lock():
                return FileLock(str(lock_file),timeout=0,blocking=False,fallback_to_soft=False,preserve_lock_file=True)
            def state():
                with parent_session_lock,native_lock():
                    view_holds.set()
                    if not release_view.wait(5):raise AssertionError('reader was not released')
                return {'runs':[],'plans':[]}
            monitor.servers = {(MID,'root-'+PARENT):SimpleNamespace(url='local',
                session=SimpleNamespace(active=None,state=state,lock=parent_session_lock))}
            source = SimpleNamespace(root=source_root,head=HEAD,snapshot=lambda:{},require_clean=lambda _:None)
            metadata = {'repository':str(source_root),'shared_budget':str(registry.path),
                        'target_environment':{'controller':binding},'policy':policy_json}
            record = {'run_id':RUN,'source_repo':str(source_root),'base_sha':HEAD,'finished_at':'done',
                      'source_unchanged':True,'policy':policy_json}
            baseline = {'run_id':'0'*32,'status':'baseline_ready','source_unchanged':True,'cleanup':'candidate_removed'}
            class DB:
                def execute(self,*_):return SimpleNamespace(fetchone=lambda:(json.dumps(metadata),))
            @contextmanager
            def journal(_):yield DB()
            def object_at(_db,kind,key):
                return {'run_id':RUN} if kind == 'run' else {'task_id':'1'*32}
            def read_json(path):
                if path.name == 'repair-execution.json':return {'phase':'finished','inflight':None},'r'*64
                if path.parent.name == '0'*32:return baseline,'b'*64
                return record,'d'*64
            class Delivery:
                def __init__(self,*_):reached_native.set()
                def _native(self,*_):
                    native_entered.set()
                    try:
                        with native_lock():return {'task_id':'1'*32,'revision':1,'snapshot_sha256':'2'*64}
                    except Timeout as error:raise SafetyError('Another writer owns this Pi task session') from error
                    finally:native_done.set()
            errors,view_errors = [],[]
            def parent_worker():
                try:manager._parent(MID,PARENT,own_child='9'*16)
                except BaseException as error:errors.append(error)
            def view_worker():
                try:
                    if reader_kind == 'monitor':monitor.view()
                    elif reader_kind == 'direct':state()
                    else:
                        with native_lock():
                            view_holds.set()
                            if not release_view.wait(5):raise AssertionError('external writer was not released')
                except BaseException as error:view_errors.append(error)
            with patch('repo_maintainer.monitor_failure_followup.RepositoryGateway',return_value=source),\
                 patch('repo_maintainer.monitor_failure_followup.failure_binding',return_value=pin),\
                 patch('repo_maintainer.monitor_failure_followup.journal',journal),\
                 patch('repo_maintainer.monitor_failure_followup.object_at',object_at),\
                 patch('repo_maintainer.monitor_failure_followup.read_json',read_json),\
                 patch('repo_maintainer.monitor_delivery.MonitorDelivery',Delivery),\
                 patch('repo_maintainer.maintenance_memory.monitor_projection',return_value={}):
                reader = threading.Thread(target=view_worker,daemon=True);reader.start()
                try:
                    self.assertTrue(view_holds.wait(3),'monitor.view did not hold the native reader')
                    worker = threading.Thread(target=parent_worker,daemon=True);worker.start()
                    self.assertTrue(reached_native.wait(3),'parent never reached the original native check: '+repr(errors))
                    if native_entered.wait(.25):
                        self.assertTrue(native_done.wait(3),'nonblocking native lease did not settle')
                finally:release_view.set()
                reader.join(3);worker.join(3)
                self.assertFalse(reader.is_alive() or worker.is_alive(),'native reader lock order did not settle')
                self.assertEqual(view_errors,[])
                expected = ['Another writer owns this Pi task session'] if reader_kind == 'external_writer' else []
                self.assertEqual([str(e) for e in errors],expected, 'parent native locking did not preserve the expected reader/writer boundary')

    def test_monitor_view_never_holds_monitor_lock_while_entering_child_state(self):
        """A child state owns child.session.lock before its parent source guard."""
        monitor = object.__new__(MaintenanceMonitor)
        monitor.lock = threading.RLock()
        eid = '9'*16
        event = {'id':eid,'sha':HEAD,'plan_id':None,'failure_successor':{'parent_event_id':PARENT},
                 'root_execution':{'status':'candidate_ready','binding':{'root_rules_sha256':'f'*64}}}
        registry = Registry({'event':{eid:event}})
        monitor.records,monitor.registries = {MID:{'id':MID}},{MID:registry}
        monitor.root_rules = SimpleNamespace(current=lambda *_:{'sha256':'f'*64})
        monitor.acceptance_watch = SimpleNamespace(records={})
        monitor.failure_followup = SimpleNamespace(state=lambda *_:[])
        monitor.model_authorization = SimpleNamespace(view=lambda *_:None)
        monitor.validation = SimpleNamespace(projection=lambda *_:None)
        child_lock = threading.RLock();monitor_available = []
        def state():
            with child_lock:
                def probe():
                    acquired = monitor.lock.acquire(blocking=False)
                    monitor_available.append(acquired)
                    if acquired:monitor.lock.release()
                checker = threading.Thread(target=probe,daemon=True)
                checker.start();checker.join(3)
                self.assertFalse(checker.is_alive(),'monitor lock-order probe did not settle')
                self.assertEqual(monitor_available,[True],'monitor.view held monitor.lock before child.session.lock')
                # The real child guard takes monitor.lock for its exact parent
                # after owning child.session.lock. This must remain acyclic.
                with monitor.lock:pass
            return {'runs':[],'plans':[]}
        monitor.servers = {(MID,'root-'+eid):SimpleNamespace(url='local',session=SimpleNamespace(active=None,state=state))}
        with patch('repo_maintainer.maintenance_memory.monitor_projection',return_value={}):
            self.assertEqual(monitor.view()[0]['events'][0]['id'],eid)


if __name__ == '__main__':unittest.main()
