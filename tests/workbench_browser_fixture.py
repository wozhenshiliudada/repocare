"""Synthetic browser fixture; no live GitHub, credentials, or paid models."""
import sys
from pathlib import Path

root = Path(sys.argv[1])
root.mkdir()
from test_repository_workbench import FakeGitHub
from repo_maintainer.demo import create_fixture
from repo_maintainer.provider import MockProvider
from repo_maintainer.repository_workbench import RepositoryWorkbench, inspect_repository, serve_workbench

source = create_fixture(root / 'source')
config = root / '.env.provider.local'
config.write_text('synthetic; never read', encoding='utf-8')
hub = RepositoryWorkbench(root / 'hub', config, github=FakeGitHub(),
                          provider_factory=lambda policy: MockProvider(),
                          picker=lambda: inspect_repository(source))
serve_workbench(hub)
