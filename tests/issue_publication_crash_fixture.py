"""Author fixture only: native local bare push, then real owner exit/PID gap."""
import json
import os
from pathlib import Path
import sys

from test_issue_delivery import FakeGitHub, FakePulls
from test_issue_publication import LocalBranchTransport
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.issue_execution import IssueExecution
from repo_maintainer.issue_delivery import IssueDelivery
from repo_maintainer.issue_publication import IssuePublication
from repo_maintainer.ui_registry import UIRegistry

config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
source = RepositoryGateway(Path(config['source']))
registry = UIRegistry(Path(config['state']), {'repository': str(source.root), 'provider': {'request_budget': 50}})
execution = IssueExecution.__new__(IssueExecution)
execution.source, execution.registry, execution.runs = source, registry, registry.root.parent
execution.spec, execution.rules = config['spec'], config['rules']
execution.connection = FakeGitHub(source.head)
remote = FakePulls(execution.connection, config['head'], config['commit'])
transport = LocalBranchTransport(config['bare'])
service = IssuePublication(IssueDelivery(execution, remote), transport)
original = service._event

def event(identity, stage, detail):
    original(identity, stage, detail)
    if config['phase'] == stage:
        Path(sys.argv[2]).write_text(json.dumps({'phase': stage, 'transport_attempts': transport.attempts}), encoding='utf-8')
        os._exit(48 if stage == 'push_done' else 49)

service._event = event
proposal = config['proposal']
service.push(config['identity'], proposal['challenge'], approve_sha256=proposal['approval_sha256'], actor='fixture')
raise AssertionError('Fixture must exit at its requested durable phase')
