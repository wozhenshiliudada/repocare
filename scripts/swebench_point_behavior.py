"""Pinned Docker-only supplementary behavior gate. Never calls a model or reads gold.

Exit 0: selected checks passed; 1: actual behavior failure; 2: precondition/infra failure.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import uuid

from point_behavior_probe import BASES, GROUPS, KIND

sha = lambda value: hashlib.sha256(value).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def snapshot(workspace):
    workspace = Path(workspace).resolve(strict=True)
    executable = shutil.which('git')
    require(executable and not Path(executable).resolve().is_relative_to(workspace), 'Trusted Git executable required')
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
    def git(*args):
        return subprocess.check_output([executable, '-c', 'core.hooksPath=' + os.devnull,
            '-c', 'core.fsmonitor=false', '-C', str(workspace), *args], env=env, stderr=subprocess.PIPE, timeout=30)
    require(Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve() == workspace, 'Workspace must be a repository root')
    head = git('rev-parse', 'HEAD').decode().strip()
    require(head in BASES.values(), 'Workspace does not match either frozen SymPy base')
    for path in (workspace/'sympy', workspace/'sympy/geometry', workspace/'sympy/geometry/point.py'):
        require(not path.is_symlink() and not path.is_junction(), 'Linked source paths are not supported')
    require(b'160000 ' not in git('ls-files', '--stage'), 'Submodules are not supported')
    require(not git('ls-files', '--others', '--exclude-standard').strip(), 'Untracked candidate files')
    paths = set(git('diff', '--name-only', '-z', 'HEAD').decode().strip('\0').split('\0')) - {''}
    require(paths <= {'sympy/geometry/point.py'}, 'Candidate changed files outside point.py')
    patch = git('diff', '--no-ext-diff', '--no-textconv', '--binary', 'HEAD')
    require(len(patch) <= 1024 * 1024, 'Candidate patch exceeds fixed limit')
    common = Path(git('rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip()).resolve()
    return {'workspace': str(workspace), 'base_commit': head, 'patch': patch, 'common_dir': str(common),
            'status': git('status', '--porcelain=v1', '--untracked-files=all'),
            'branch': git('rev-parse', '--abbrev-ref', 'HEAD')}


def pinned_image(images, head):
    require(isinstance(images, dict) and set(images) == set(BASES), 'Expected both fixed image locks')
    identity = next(key for key, value in BASES.items() if value == head)
    pin = images[identity]
    prefix = 'swebench/sweb.eval.x86_64.sympy_1776_sympy-' + identity.rsplit('-', 1)[1] + '@sha256:'
    require(isinstance(pin, dict) and isinstance(pin.get('digest'), str)
            and re.fullmatch(re.escape(prefix) + '[0-9a-f]{64}', pin['digest'])
            and isinstance(pin.get('image_id'), str) and re.fullmatch('sha256:[0-9a-f]{64}', pin['image_id']),
            'Explicit pinned local image digest/identity required')
    return pin


def verified_result(value, exit_code, base, patch_sha, group):
    require(isinstance(value, dict) and value.get('kind') == KIND and value.get('base_commit') == base
            and value.get('patch_sha256') == patch_sha and value.get('group') == group
            and type(value.get('model_requests')) is int and value['model_requests'] == 0
            and value.get('official_score') is False and value.get('infrastructure_error', False) is False,
            'Behavior receipt binding changed')
    rows = value.get('cases')
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
            and [row.get('case') for row in rows] == list(GROUPS[group])
            and all(type(row.get('passed')) is bool and isinstance(row.get('diagnostic'), str)
                    and len(row['diagnostic']) <= 4000 for row in rows), 'Missing or invalid behavior cases')
    passed = sum(row['passed'] for row in rows)
    require(type(value.get('passed')) is int and value['passed'] == passed
            and type(value.get('total')) is int and value['total'] == len(rows)
            and type(exit_code) is int and exit_code == (0 if passed == len(rows) else 1),
            'Behavior verdict and actual process exit disagree')
    return value


def read_logs(container, stdout):
    data = bytearray()
    stream = container.logs(stdout=stdout, stderr=not stdout, stream=True)
    try:
        for chunk in stream:
            data += chunk[:max(0, 65537 - len(data))]
            if len(data) > 65536:
                break
    finally:
        stream.close()
    require(len(data) <= 65536, 'Behavior output exceeded bounded receipt limit')
    return bytes(data)


def evaluate(workspace, image_file, output, group='preserve-imaginary'):
    require(group in GROUPS, 'Unknown behavior group')
    before = snapshot(workspace)
    image_file = Path(image_file).resolve(strict=True)
    require(image_file.stat().st_size <= 100000, 'Image lock exceeds fixed limit')
    pin = pinned_image(json.loads(image_file.read_bytes()), before['base_commit'])
    output = Path(output).resolve()
    require(not output.is_relative_to(Path(before['workspace']))
            and not output.is_relative_to(Path(before['common_dir'])), 'Behavior evidence must be outside source/Git metadata')
    directory = output / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    probe = Path(__file__).with_name('point_behavior_probe.py').read_bytes()
    intent = {'kind': KIND, 'base_commit': before['base_commit'], 'patch_sha256': sha(before['patch']),
              'probe_sha256': sha(probe), 'group': group, 'image': pin, 'network_disabled': True,
              'workspace': before['workspace'], 'model_requests': 0, 'official_score': False}
    (directory/'intent.json').write_text(json.dumps(intent, indent=2) + '\n', encoding='utf-8')
    import docker  # Existing judge dependency; no host import of repository code.
    endpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine' if os.name == 'nt' else 'unix:///var/run/docker.sock'
    client = docker.DockerClient(base_url=endpoint, timeout=45)
    owned = None
    try:
        require(client.images.get(pin['digest']).id == pin['image_id'], 'Pinned local image identity changed')
        owned = client.containers.create(pin['digest'], entrypoint=[], working_dir='/testbed',
            command=['/opt/miniconda3/envs/testbed/bin/python', '-I', '-B', '/probe/run.py',
                     '--base', before['base_commit'], '--group', group], network_disabled=True,
            cap_drop=['ALL'], security_opt=['no-new-privileges'], mem_limit='1g', pids_limit=128,
            log_config={'Type': 'json-file', 'Config': {'max-size': '1m', 'max-file': '1'}})
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode='w') as archive:
            folder = tarfile.TarInfo('probe'); folder.type = tarfile.DIRTYPE; folder.mode = 0o755
            archive.addfile(folder)
            for name, data in [('probe/run.py', probe), ('probe/patch.diff', before['patch'])]:
                member = tarfile.TarInfo(name); member.size = len(data); member.mode = 0o644
                archive.addfile(member, io.BytesIO(data))
        require(owned.put_archive('/', payload.getvalue()), 'Could not install fixed behavior probe')
        owned.start()
        terminal = owned.wait(timeout=90)
        stdout, stderr = read_logs(owned, True), read_logs(owned, False)
        (directory/'stdout.log').write_bytes(stdout); (directory/'stderr.log').write_bytes(stderr)
        value = verified_result(json.loads(stdout.splitlines()[-1]), terminal['StatusCode'],
                                before['base_commit'], sha(before['patch']), group)
        require(snapshot(workspace) == before, 'Source candidate changed during behavior check')
        rows = value['cases']
        value = {**value, 'image': pin, 'network_disabled': True, 'infrastructure_error': False,
            'tests_status': {'SUPPLEMENTARY_BEHAVIOR': {
                'success': [r['case'] for r in rows if r['passed']],
                'failure': [r['case'] for r in rows if not r['passed']]}},
            'failure_excerpt': '\n'.join(r['case'] + '\n' + r['diagnostic'] for r in rows if not r['passed'])[-14000:],
            'evidence_directory': str(directory), 'source_unchanged': True}
        (directory/'result.json').write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        return value
    finally:
        try:
            if owned is not None:
                owned.remove(force=True)  # Only the container created by this invocation.
        finally:
            client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--images', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--group', choices=GROUPS, default='preserve-imaginary')
    args = parser.parse_args()
    try:
        summary = evaluate(args.workspace, args.images, args.output, args.group)
        code = 0 if summary['passed'] == summary['total'] else 1
    except Exception as error:
        summary = {'kind': KIND, 'infrastructure_error': True, 'model_requests': 0, 'official_score': False,
                   'error': type(error).__name__ + ': ' + str(error)}
        code = 2
    print(json.dumps(summary), flush=True)
    raise SystemExit(code)
