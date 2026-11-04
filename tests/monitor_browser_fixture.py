"""Real Pi replay + real local Git; GitHub metadata is a bounded substitute."""
import sys
from pathlib import Path

from repo_maintainer.demo import create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.github_connection import fetch_snapshot
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.repository_workbench import RepositoryWorkbench, inspect_repository, serve_workbench
from test_repository_workbench import FakeGitHub

root, url = Path(sys.argv[1]), sys.argv[2]
root.mkdir()
source = create_fixture(root / 'source')
repository = RepositoryGateway(source)
repository.command('config', 'user.name', 'Browser Monitor')
repository.command('config', 'user.email', 'browser@example.invalid')
config = root / '.env.provider.local'
config.write_text('synthetic; not read')
class GitHubReplay(FakeGitHub):
    def branch_head(self, url, branch):
        return repository.head
    def workflow_runs(self, url, branch, sha):
        return [{'id': 123, 'attempt': 1, 'name': 'Synthetic CI', 'conclusion': 'failure',
                 'url': 'https://github.com/example/private/actions/runs/123'}]
    def snapshot(self, url, branch, sha, destination):
        fetch_snapshot(source, branch, sha, destination)

hub = RepositoryWorkbench(root / 'hub', config, github=GitHubReplay(),
    provider_factory=lambda policy: PiProvider(policy.allowed_paths, url, policy.readonly_paths, 6),
    picker=lambda: inspect_repository(source))
serve_workbench(hub)
