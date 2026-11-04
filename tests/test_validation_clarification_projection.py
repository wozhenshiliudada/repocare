import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import threading
import unittest
from repo_maintainer.validation_proposal import PURPOSE,clarification_leaf
from repo_maintainer.task_session import fingerprint
from repo_maintainer.models import digest, SafetyError
from repo_maintainer.monitor_validation import MonitorValidation
from repo_maintainer.maintenance_monitor import MaintenanceMonitor
from repo_maintainer.pi_provider import PiProvider

class MemoryRegistry:
    def __init__(self):self.objects={}
    def load(self,kind):return copy.deepcopy(self.objects.get(kind,{}))
    def put(self,kind,identity,value):self.objects.setdefault(kind,{})[identity]=copy.deepcopy(value)

def chain(depth=12):
    provider=PiProvider((),'http://127.0.0.1:1/v1',('calculator.py',),max_requests=6)
    binding=provider.task_identity()[1];registry=MemoryRegistry();issue='Correct add'
    source={'schema_version':1,'purpose':PURPOSE,'repository_id':'a'*64,'base_sha':'b'*40,
        'request_sha256':digest(issue.encode()),'issue':issue,'read_roots':['.'],
        'files':[{'path':'calculator.py','content':'def add(a,b): return a-b\n','before_sha256':digest(b'def add(a,b): return a-b\n'),'editable':False}],
        'authority':{'provider':binding},'authority_sha256':fingerprint({'provider':binding}),'runtime_authorized':False}
    source['source_sha256']=fingerprint(source)
    def ident(s):return fingerprint({'source':s,'provider_identity':binding})
    root=cursor=ident(source);parents={}
    for i in range(depth):
        row={'id':cursor,'source':source,'status':'clarification_needed','runtime_authorized':False}
        registry.put('validation_proposal',cursor,row);parents[cursor]=copy.deepcopy(row)
        answer='Expected addition '+str(i);child=copy.deepcopy(source)
        child['issue']=source['issue']+'\n\n用户补充业务期望：\n'+answer
        child['request_sha256']=digest(child['issue'].encode());child.pop('source_sha256')
        child['source_sha256']=fingerprint(child);child_id=ident(child)
        registry.put('validation_clarification',cursor,{'parent_id':cursor,'parent_sha256':fingerprint(row),
            'child_id':child_id,'answer':answer,'source':child})
        cursor=child_id;source=child
    registry.put('validation_proposal',cursor,{'id':cursor,'source':source,'status':'started','runtime_authorized':False})
    registry.put('monitor_validation_request','e'*16,{'proposal_id':root,'core':{'request_text':issue}})
    registry.put('event','e'*16,{'id':'e'*16,'status':'blocked'})
    return registry,provider,root,cursor,parents

class DeepProjection(unittest.TestCase):
    def test_monitor_projection_targets_true_leaf(self):
        registry,provider,root,leaf,parents=chain()
        controller=MonitorValidation(SimpleNamespace(registries={'mid':registry},hub=SimpleNamespace(token='session')))
        value=controller.projection('mid',{'id':'e'*16})
        self.assertEqual(leaf,value['id']);self.assertEqual('started',value['status'])
    def test_restart_marks_only_true_started_leaf(self):
        registry,provider,root,leaf,parents=chain();monitor=object.__new__(MaintenanceMonitor)
        monitor.root=Path('.demo/r1-clarification/mock-monitor');monitor.records={};monitor.registries={}
        monitor.lock=threading.RLock();monitor.next_due={};monitor._provider=lambda _:provider
        for name in ('model_authorization','acceptance_watch','failure_followup'):
            setattr(monitor,name,SimpleNamespace(restore=lambda _:None))
        spec={'schema_version':2,'id':'0'*16,'allowed_paths':[],'readonly_paths':[],'allowed_tests':[],
            'requires_root_rules':True,'timeout_seconds':30,'interval_seconds':60}
        with patch('repo_maintainer.maintenance_monitor.UIRegistry',return_value=registry):monitor._open('0'*16,spec)
        self.assertEqual('interrupted_no_replay',registry.load('validation_proposal')[leaf]['status'])
        for identity,row in parents.items():self.assertEqual(row,registry.load('validation_proposal')[identity])

    def test_cycle_parent_tamper_and_missing_child_stay_readonly(self):
        registry,provider,root,leaf,parents=chain()
        saved=copy.deepcopy(registry.objects)
        link=registry.load('validation_clarification')[root];link['child_id']=root
        registry.put('validation_clarification',root,link)
        with self.assertRaises(SafetyError):clarification_leaf(registry,root)
        registry.objects=copy.deepcopy(saved)
        parent=registry.load('validation_proposal')[root];parent['unexpected']='changed'
        registry.put('validation_proposal',root,parent)
        with self.assertRaises(SafetyError):clarification_leaf(registry,root)
        registry.objects=copy.deepcopy(saved);registry.objects['validation_proposal'].pop(leaf)
        self.assertEqual((leaf,12),(clarification_leaf(registry,root)[0],len(clarification_leaf(registry,root)[1])))
        controller=MonitorValidation(SimpleNamespace(registries={'mid':registry},hub=SimpleNamespace(token='session')))
        self.assertEqual('interrupted_no_replay',controller.projection('mid',{'id':'e'*16})['status'])

if __name__=='__main__':unittest.main(verbosity=2)
