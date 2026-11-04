"""Legacy planning must not bypass a later durable source association."""
import copy
import unittest
from unittest.mock import patch

from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.models import SafetyError
from repo_maintainer.workbench_correlation import ROUTES
import test_maintenance_monitor as fixtures


class LegacyCorrelationFence(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.MaintenanceTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def link(self,mid,event):
        registry=self.f.hub.monitor.registries[mid]
        origin={'kind':'monitor','monitor_id':mid,'event_id':event['id'],'registry_path':str(registry.path)}
        # A partial registration is enough to deny the unsupported legacy path;
        # it is never sufficient to grant a new owner or executable authority.
        registry.put(ROUTES,fingerprint(origin),{'pair_id':'pending-original-association','origin':origin})

    def test_registration_before_old_session_prevents_planning_and_spending(self):
        original=self.f.hub.monitor._session
        def register(mid,event):
            self.link(mid,event)
            return original(mid,event)
        with patch.object(self.f.hub.monitor,'_session',side_effect=register):
            mid=self.f.start()
        registry=self.f.hub.monitor.registries[mid]
        self.assertEqual(0,fixtures.CountingProvider.calls)
        self.assertEqual(0,registry.budget()['requests_used'])
        self.assertEqual(0,registry.budget()['requests_reserved_unknown'])
        self.assertEqual({},registry.load('pi_task'))
        self.assertFalse(self.f.hub.monitor.servers)

    def test_saved_legacy_session_rechecks_association_before_another_model(self):
        mid=self.f.start();server=self.f.server(mid);event=self.f.view()['events'][-1]
        registry=self.f.hub.monitor.registries[mid]
        before=registry.budget();plans=copy.deepcopy(server.session.plans)
        self.link(mid,event)
        with self.assertRaisesRegex(SafetyError,'关联'):
            server.session._plan('Keep the same original calculator maintenance request')
        self.assertEqual(1,fixtures.CountingProvider.calls)
        self.assertEqual(before,registry.budget())
        self.assertEqual(plans,server.session.plans)

    def test_null_registration_is_not_treated_as_no_registration(self):
        original=self.f.hub.monitor._session
        def register(mid,event):
            registry=self.f.hub.monitor.registries[mid]
            origin={'kind':'monitor','monitor_id':mid,'event_id':event['id'],'registry_path':str(registry.path)}
            registry.put(ROUTES,fingerprint(origin),None)
            return original(mid,event)
        with patch.object(self.f.hub.monitor,'_session',side_effect=register):
            mid=self.f.start()
        self.assertEqual(0,fixtures.CountingProvider.calls)
        self.assertEqual(0,self.f.hub.monitor.registries[mid].budget()['requests_used'])

    def test_unrelated_commit_association_does_not_disable_current_legacy_scope(self):
        mid=self.f.start();server=self.f.server(mid)
        registry=self.f.hub.monitor.registries[mid]
        old={'id':'other-committed-event','sha':'b'*40}
        registry.put('event',old['id'],old);self.link(mid,old)
        before=registry.budget()
        self.f.hub.monitor._legacy_guard(mid,self.f.view()['events'][0]['sha'])
        self.assertEqual(before,registry.budget())
        self.assertEqual(1,fixtures.CountingProvider.calls)


if __name__=='__main__':unittest.main(verbosity=2)
