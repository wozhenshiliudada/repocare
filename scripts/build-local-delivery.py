"""Build a new Windows x64/CPython3.13 local kit from already verified local inputs. No downloads."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from repo_maintainer.recovery import unlinked
from repo_maintainer.semantic_retrieval import ModelPackage


def copy_tree(source, destination):
    source = unlinked(source)
    def ignore(directory, names):
        for name in names:
            unlinked(Path(directory) / name)
        return [name for name in names if name == '__pycache__' or name.endswith('.pyc')]
    shutil.copytree(source, destination, ignore=ignore)


def build(destination, wheelhouse, project_wheel, model_manifest):
    destination, wheelhouse, project_wheel = map(unlinked, (destination, wheelhouse, project_wheel))
    for source in [ROOT/'src', ROOT/'runtime', ROOT/'scripts', ROOT/'examples', ROOT/'requirements', wheelhouse]:
        if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
            raise ValueError('New output must be separate from all copied input trees')
    ModelPackage(model_manifest)
    if project_wheel.parent != wheelhouse or not project_wheel.name.startswith('repo_maintainer-'):
        raise ValueError('Select the project wheel inside the fixed wheelhouse')
    destination.mkdir(parents=True, exist_ok=False)
    app = destination/'app'
    app.mkdir()
    for name in ['maintain.py', 'pyproject.toml', 'SETUP.md', 'THIRD_PARTY.md']:
        shutil.copyfile(ROOT/name, app/name)
    (app/'src').mkdir()
    copy_tree(ROOT/'src/repo_maintainer', app/'src/repo_maintainer')
    copy_tree(ROOT/'examples', app/'examples')
    copy_tree(ROOT/'requirements', app/'requirements')
    copy_tree(ROOT/'third_party', app/'third_party')
    (app/'scripts').mkdir()
    for name in ['doctor.py', 'python_binding_check.py', 'prepare-semantic-model.py']:
        shutil.copyfile(ROOT/'scripts'/name, app/'scripts'/name)
    (app/'runtime').mkdir()
    for runtime in ['pi', 'pyright', 'codegraph']:
        # Exact runtime subtree, including its preserved upstream licenses and platform packages.
        copy_tree(ROOT/'runtime'/runtime, app/'runtime'/runtime)
    (app/'tests').mkdir()
    shutil.copyfile(ROOT/'tests/pi_replay.py', app/'tests/pi_replay.py')
    copy_tree(ROOT/'runtime/semantic/.python', destination/'python')
    copy_tree(model_manifest.parent, destination/'model')
    copy_tree(wheelhouse, destination/'wheels')
    for name in ['Install.ps1', 'Start.ps1', 'verify.py']:
        shutil.copyfile(ROOT/'scripts/delivery'/name, destination/name)
    (destination/'README.txt').write_text(
        'RepoCare local Windows x64 / CPython3.13 delivery kit\n'
        'Requires Windows x64, installed Git and Node >=22.19.0 <25. No Docker required.\n'
        'Run Install.ps1 once; it verifies files and installs only the included hash-pinned wheels offline.\n'
        'Run Start.ps1 explicitly. It registers the included fixed local model; retrieval still defaults off.\n'
        'The source app/runtime layout is required by Pi. The Python wheel alone is not the full kit.\n'
        'Never copy your .env, active workspace state, credentials, or budgets into this directory.\n'
        'Installation does not start a service, spend model requests, or migrate existing state.\n'
        'Third-party licenses are retained inside app/third_party, python and original runtime packages.\n'
        'This is a private local artifact, not a signed public distribution or cross-platform installer.\n', encoding='utf-8')
    files = []
    for path in sorted(destination.rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        data = path.read_bytes()
        files.append({'path': path.relative_to(destination).as_posix(), 'bytes': len(data),
                      'sha256': hashlib.sha256(data).hexdigest()})
    manifest = {'schema_version': 1, 'platform': 'Windows x64 / CPython3.13',
                'model_manifest_sha256': hashlib.sha256(model_manifest.read_bytes()).hexdigest(),
                'files': files, 'model_requests': 0}
    (destination/'delivery-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return {'directory': str(destination), 'files': len(files), 'bytes': sum(r['bytes'] for r in files),
            'manifest_sha256': hashlib.sha256((destination/'delivery-manifest.json').read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--wheelhouse', type=Path, required=True)
    parser.add_argument('--project-wheel', type=Path, required=True)
    parser.add_argument('--model-manifest', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.directory, args.wheelhouse, args.project_wheel, unlinked(args.model_manifest)), indent=2))
