"""Owned subprocess fixture: exit after real Pi planning, before approval can edit."""
import json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_issue_intake import FakeGitHub
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.issue_execution import IssueExecution
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.ui_registry import UIRegistry

value=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
repo=RepositoryGateway(Path(value['source']))
github=FakeGitHub(repo.head)
github.issue=value['issue']
registry=UIRegistry(Path(value['state']),value['metadata'])
def provider(policy):
    return PiProvider(policy.allowed_paths,value['model_url'],readonly_paths=policy.readonly_paths,max_requests=50)
service=IssueExecution(repo.root,registry,github,value['spec'],value['rules'],provider)
service.start(value['investigation_id'],value['environment'],value['grant'],approval=lambda _:os._exit(91))
raise AssertionError('Expected the owned fixture to exit before editing')
