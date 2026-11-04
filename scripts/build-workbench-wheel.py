"""Build and verify a current wheel in a new source staging directory, preserving old build output."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from repo_maintainer.recovery import unlinked
from repo_maintainer.pi_distribution import stage_pi_runtime
from verify_workbench_wheel import verify


def build(directory, no_build_isolation=False):
    directory = unlinked(directory)
    for source in [ROOT/'src', ROOT/'third_party', ROOT/'runtime', ROOT/'examples']:
        if directory == source or directory.is_relative_to(source) or source.is_relative_to(directory):
            raise ValueError('Build output must be separate from copied source inputs')
    directory.mkdir(parents=True, exist_ok=False)
    stage = directory/'source'
    stage.mkdir()
    shutil.copyfile(ROOT/'pyproject.toml', stage/'pyproject.toml')
    shutil.copyfile(ROOT/'setup.py', stage/'setup.py')
    shutil.copyfile(ROOT/'LICENSE', stage/'LICENSE')
    (stage/'src').mkdir()
    def ignore(folder, names):
        for name in names:
            unlinked(Path(folder)/name)
        return [n for n in names if n=='__pycache__' or n.endswith('.pyc')]
    shutil.copytree(ROOT/'src/repo_maintainer', stage/'src/repo_maintainer', ignore=ignore)
    shutil.copytree(ROOT/'third_party', stage/'third_party', ignore=ignore)
    # The installed-Pi build hook needs the locked runtime and demo inputs.
    # Reuse its whitelist/integrity rules; never copy a checkout's node_modules,
    # provider config, arbitrary runtime directories, or stale build outputs.
    temporary_runtime = stage/'runtime/pi_runtime'
    source_runtime = stage/'runtime/pi'
    if (not temporary_runtime.resolve().is_relative_to(stage.resolve())
            or not source_runtime.resolve().is_relative_to(stage.resolve())
            or source_runtime.exists()):
        raise ValueError('Unsafe new staged Pi runtime destination')
    stage_pi_runtime(ROOT, temporary_runtime)
    # The packager requires the output leaf pi_runtime; setup.py deliberately
    # reads runtime/pi in a source staging tree. Rename only this new output.
    temporary_runtime.rename(source_runtime)
    for name in ('.gitignore', 'calculator.py', 'tests/test_calculator.py'):
        source = unlinked(ROOT/'examples/buggy_repo'/name)
        target = stage/'examples/buggy_repo'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    output = directory/'wheels'
    command=[sys.executable, '-m', 'pip', 'wheel', '--no-deps']
    if no_build_isolation:
        command.append('--no-build-isolation')
    subprocess.run([*command, '--wheel-dir', str(output), str(stage)], check=True)
    wheels = list(output.glob('repo_maintainer-*.whl'))
    if len(wheels)!=1:
        raise ValueError('Expected exactly one built project wheel')
    result = verify(wheels[0], ROOT/'src/repo_maintainer/workbench_ui')
    (directory/'verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True, help='New staging/output directory')
    parser.add_argument('--no-build-isolation', action='store_true', help='Offline build only when the current interpreter already has the declared setuptools backend')
    args=parser.parse_args()
    print(json.dumps(build(args.directory,args.no_build_isolation), indent=2))
