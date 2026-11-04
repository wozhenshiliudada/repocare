"""Same display names and common Git dirs do not authorize sharing task memory."""
import json
from pathlib import Path
import tempfile
import unittest

from test_task_planning import SyntheticModel
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_provider import PiProvider

ISSUE='Fix signed addition without importing another checkout history.'
MARKER='PRIMARY_CHECKOUT_REFERENCE_ONLY'


class RepositoryIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.model=SyntheticModel();self.addCleanup(self.model.close)
        self.sessions=[];self.addCleanup(lambda:[s.close() for s in reversed(self.sessions)])
        self.policy=Policy(('calculator.py',),(DEMO_TEST,),readonly_paths=('tests/test_calculator.py',))

    def open(self,source,state):
        session=UISession(source,self.policy,PiProvider(('calculator.py',),self.model.url,
                          readonly_paths=('tests/test_calculator.py',),max_requests=50),state)
        self.sessions.append(session);return session

    def note(self,session):
        session._operate('maintenance_note',{'note_id':None,'revision':session.maintenance.revision(),
            'title':ISSUE,'detail':MARKER,'status':'open','priority':2})
        session._operate('maintenance_settings',{'enabled':True,'revision':session.maintenance.revision()})

    def test_same_named_repositories_keep_actual_model_history_memory_and_original_journals_separate(self):
        left=create_fixture(self.root/'left/repository');right=create_fixture(self.root/'right/repository')
        first=self.open(left,self.root/'left-state');self.note(first)
        plan=first._operate('plan',{'issue':ISSUE});primary=first.task_planner.identity
        self.assertIn(MARKER,json.dumps(self.model.requests[-1]))
        first_budget=first.registry.budget();native=first.task_planner.store._load(primary,first.task_planner.scope)
        second=self.open(right,self.root/'right-state');second._operate('plan',{'issue':ISSUE})
        self.assertNotIn(MARKER,json.dumps(self.model.requests[-1]));self.assertNotEqual(primary,second.task_planner.identity)
        self.assertNotEqual(first.catalog.repository,second.catalog.repository)
        self.assertFalse(second.registry.load('maintenance_note'));self.assertEqual(first_budget,first.registry.budget())
        self.assertEqual(2,second.registry.budget()['requests_used']);self.assertEqual(4,len(self.model.requests))
        with self.assertRaises(SafetyError):second.task_planner.store._load(primary,native['scope'])
        with self.assertRaises(SafetyError):second._operate('decision',{**plan,'decision':'approve'})
        first.close();before=(self.root/'left-state/ui-state/registry.sqlite3').read_bytes()
        with self.assertRaises(SafetyError):self.open(right,self.root/'left-state')
        self.assertEqual(before,(self.root/'left-state/ui-state/registry.sqlite3').read_bytes())
        self.assertEqual(4,len(self.model.requests))

    def test_linked_worktree_shares_git_objects_but_never_adopts_old_task_review_or_consumption(self):
        source=create_fixture(self.root/'source');repo=RepositoryGateway(source);linked=self.root/'linked'
        repo.command('worktree','add','--detach',str(linked),repo.head)
        self.assertEqual(repo.common_dir,RepositoryGateway(linked).common_dir)
        first=self.open(source,self.root/'primary-state');self.note(first)
        plan=first._operate('plan',{'issue':ISSUE});task=first.task_planner.identity;budget=first.registry.budget()
        second=self.open(linked,self.root/'linked-state')
        self.assertNotEqual(first.catalog.repository,second.catalog.repository)
        self.assertFalse(second.registry.load('pi_task'));self.assertFalse(second.registry.load('maintenance_note'))
        self.assertFalse(second.catalog.query(MARKER)['items'])
        with self.assertRaises(SafetyError):second.task_planner.store._load(task,first.task_planner.scope)
        with self.assertRaises(SafetyError):second._operate('decision',{**plan,'decision':'approve'})
        self.assertEqual(0,second.registry.budget()['requests_used']);self.assertEqual(budget,first.registry.budget())
        self.assertEqual(2,len(self.model.requests))
        first.close();before=(self.root/'primary-state/ui-state/registry.sqlite3').read_bytes()
        with self.assertRaises(SafetyError):self.open(linked,self.root/'primary-state')
        self.assertEqual(before,(self.root/'primary-state/ui-state/registry.sqlite3').read_bytes())
