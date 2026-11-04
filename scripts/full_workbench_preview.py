"""Disposable UI acceptance: real controllers/Pi/Git, local synthetic model and GitHub."""
import argparse
import json
from pathlib import Path
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tests'))
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.issue_workbench import IssueWorkbench
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.models import SafetyError
from repo_maintainer.repository_workbench import RepositoryWorkbench, inspect_repository, serve_workbench
from test_issue_intake import FakeGitHub
from test_task_planning import SyntheticModel


class BrowserFixture:
    def __init__(self, remote): self.remote=remote
    def view(self,kind,url=None,page=1):
        if kind=='account':return {'login':'synthetic-maintainer'}
        if kind=='repositories':return {'repositories':[{'name':'example/project','url':'https://github.com/example/project','private':False,'archived':False}], 'has_next':False,'page':page}
        if kind=='branches':return {'branches':['main'],'has_next':False,'page':page}
        issue=self.remote.issue
        items=[{'id':issue['id'],'number':issue['number'],'title':issue['title'],'state':issue['state'],
                'author':'synthetic-author','url':issue['html_url'],'updated_at':issue['updated_at']}] if kind=='issues' else []
        return {'items':items,'has_next':False,'page':page}


class NoExternalOperations:
    """Fail closed for every unimplemented remote/auth/transport operation."""
    def __getattr__(self,name):
        def blocked(*args,**kwargs):
            raise SafetyError('Synthetic preview: external GitHub/auth/publication is disabled; use isolated delivery tests')
        return blocked


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--port',type=int,default=28534)
    args=parser.parse_args()
    root=args.directory.resolve()
    if root.exists():parser.error('Use a new directory; never reset an existing fixture')
    root.mkdir(parents=True)
    source=create_fixture(root/'calculator-fixture')
    repo=RepositoryGateway(source)
    repo.command('remote','add','origin','https://github.com/example/project.git')
    model=SyntheticModel()
    github=FakeGitHub(repo.head)
    github.issue.update(title='修复加法结果错误，保留负数与混合输入',body='add(2, 3) must return 5. Preserve negative and mixed operands. This is a synthetic UI fixture.')
    config=root/'synthetic-provider.txt'
    config.write_text('SYNTHETIC LOCAL FIXTURE - NO REAL CREDENTIALS',encoding='utf-8')
    hub=RepositoryWorkbench(root/'state',config,request_budget=25,
        provider_factory=lambda policy:PiProvider(policy.allowed_paths,model.url,policy.readonly_paths,25),ui='react')
    hub.github_browser=BrowserFixture(github)
    hub.github=NoExternalOperations()
    hub.port=args.port
    before=repo.snapshot()
    initial_head=repo.head
    initial_file=repo.read('calculator.py')
    try:
        preview=inspect_repository(source)
        job=hub.submit(uuid.uuid4().hex,'connect',{'path':str(source),'revision':preview['revision'],
            'allowed_paths':['calculator.py'],'readonly_paths':['tests/test_calculator.py'],
            'allowed_tests':[list(DEMO_TEST)],'timeout_seconds':30,'allow_auto':False,'confirmed':True})
        deadline=time.monotonic()+30
        while hub.jobs[job['id']]['status']=='running' and time.monotonic()<deadline:time.sleep(.02)
        if hub.jobs[job['id']]['status']!='finished':raise RuntimeError('Synthetic connect did not finish')
        for workspace in hub.workspaces.values():
            session=workspace.session
            session.provider.inner.config_file=None
            session.label='真实 Pi SDK · 本机合成模型 · 真实模型费用 0'
            session.issue_workbench=IssueWorkbench(session,github,remote=NoExternalOperations(),transport=NoExternalOperations())
        print('SYNTHETIC GitHub and local SSE only. No credentials or live remote writes.',flush=True)
        serve_workbench(hub,args.port)
    finally:
        model.close()
        # Evidence is local and deliberately retained, including any candidate worktree.
        (root/'source-check.json').write_text(json.dumps({'head_unchanged':repo.head==initial_head,
            'implementation_bytes_unchanged':repo.read('calculator.py')==initial_file,
            'synthetic_requests':len(model.requests),'paid_requests':0,'live_github_writes':0},indent=2),encoding='utf-8')


if __name__=='__main__':main()
