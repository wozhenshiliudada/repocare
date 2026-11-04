"""Author fixtures only: terminate the real monitor/repair controller at a fixed boundary."""
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from repo_maintainer.repository_workbench import RepositoryWorkbench
from repo_maintainer.pi_provider import PiProvider
from test_repository_workbench import FakeGitHub,FakeServer

root=Path(sys.argv[1]);url=sys.argv[2];phase=sys.argv[3]
hub=RepositoryWorkbench(root/'hub',root/'.env.provider.local',request_budget=8,allow_auto=True,
    github=FakeGitHub(),server_factory=FakeServer,
    provider_factory=lambda p:PiProvider(p.allowed_paths,url,readonly_paths=p.readonly_paths,max_requests=8))
hub.monitor.stop()
mid=next(iter(hub.monitor.records));event=hub.monitor.registries[mid].load('event').popitem()[1]
if phase=='continuation_planning':
    with patch.object(PiProvider,'plan',side_effect=lambda *_:os._exit(94)):
        value=hub.monitor.continuation.inspect({'monitor_id':mid,'event_id':event['id'],'run_id':None,'mode':'approval'})
        hub.monitor.continuation.start({**value,'confirmed':True})
elif phase=='planning':
    with patch.object(PiProvider,'plan',side_effect=lambda *_:os._exit(94)):
        value=hub.monitor.execution.preview({'monitor_id':mid,'event_id':event['id'],'max_attempts':1})
        hub.monitor.execution.start({**value,'confirmed':True})
else:
    session=hub.monitor.execution.session(mid,event).session
    plan=next(iter(session.plans.values()))
    session._operate('claim_plan',{'plan_id':plan['view']['id'],'revision':session._plan_revision(plan)})
    original=session._phase
    def boundary(value):
        original(value)
        if value==phase:os._exit(94)
    session._phase=boundary
    session._operate('decision',{'plan_id':plan['view']['id'],'fingerprint':plan['view']['fingerprint'],'decision':'approve'})
raise RuntimeError('Author crash boundary was not reached')
