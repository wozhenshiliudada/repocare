"""Author subprocess: simulated remote close and actual process exit boundaries."""
import copy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from test_issue_closure import AcceptanceRemote
from test_issue_delivery import FakeGitHub, FakePulls
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.issue_execution import IssueExecution
from repo_maintainer.issue_delivery import IssueDelivery
from repo_maintainer.issue_closure import IssueClosure
from repo_maintainer.ui_registry import UIRegistry

c = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
source = RepositoryGateway(Path(c['source']))
registry = UIRegistry(Path(c['state']), {'repository': str(source.root), 'provider': {'request_budget': 50}})
github = FakeGitHub(c['default_head'])
github.issue, github.events = c['issue'], c['events']
draft = FakePulls(github, c['head'], c['commit'])
draft.rows = [c['pr']]
case = SimpleNamespace(repo=source, root=Path(c['state']).parent, github=github, remote=draft, commit=c['commit'])
remote = AcceptanceRemote(case)
remote.pr, remote.runs = copy.deepcopy(c['pr']), c['runs']
remote.jobs = {int(k): v for k, v in c['jobs'].items()}
execution = IssueExecution.__new__(IssueExecution)
execution.registry, execution.source, execution.runs = registry, source, registry.root.parent
execution.spec = c['spec']
service = IssueClosure(IssueDelivery(execution, draft), remote, c['policy'])

def exit_now(code):
    Path(sys.argv[2]).write_text(json.dumps({'issue': remote.issue, 'events': github.events, 'writes': remote.writes}), encoding='utf-8')
    os._exit(code)

if c['phase'] == 'remote':
    original = remote.close_completed
    def close(*args):
        value = original(*args)
        exit_now(50)
        return value
    remote.close_completed = close
else:
    original = service._finish
    def finish(*args):
        value = original(*args)
        if value['status'] != 'closed-verified':
            raise AssertionError(value)
        exit_now(51)
        return value
    service._finish = finish
p = c['proposal']
service.close(c['identity'], p['challenge'], approve_sha256=p['approval_sha256'], actor='fixture')
raise AssertionError('Expected real owner exit')
