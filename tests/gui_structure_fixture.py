"""Self-authored repair fixture with a real optional CodeGraph index."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.models import Policy
from repo_maintainer.structure_index import build_structure
from repo_maintainer.ui_backup import encoded


def fixture(directory):
    directory = directory.resolve()
    directory.mkdir()
    repo = create_fixture(directory / 'repository')
    # The selected original bytes must also match Git on Windows.
    subprocess.run(['git', '-C', str(repo), 'config', 'core.autocrlf', 'false'], check=True)
    (repo / 'calculator.py').write_bytes(b'def add(a, b):\n    return a - b\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'calculator.py'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Structure Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '--allow-empty', '-m', 'stable structure fixture'],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    policy = Policy(('calculator.py',), (DEMO_TEST,), readonly_paths=('tests/test_calculator.py',))
    (directory / 'policy.json').write_bytes(encoded(asdict(policy)))
    spec = directory / 'selection.private.json'
    spec.write_bytes(encoded({'schema_version': 1, 'paths': ['calculator.py']}))
    return build_structure(spec, repo, directory / 'structure-indexes' / 'sample')


if __name__ == '__main__':
    fixture(Path(sys.argv[1]))
    print(json.dumps({'status': 'self_authored_structure_fixture_ready'}))
