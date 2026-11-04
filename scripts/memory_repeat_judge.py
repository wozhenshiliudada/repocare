"""Bounded Docker judge for authored repeat-error fixtures; never imports host candidates."""
import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import uuid

from memory_repeat_cases import CASES, KIND, PRESERVATION, feature_tests, fixture
from swebench_point_behavior import read_logs, require

sha = lambda value: hashlib.sha256(value).hexdigest()
IMAGE_SHA = 'cf141443a85d16108fd94f025f15996cc89a1ace20afdf41d77edc20603030a7'
IMAGE_DIGEST = 'swebench/sweb.eval.x86_64.sympy_1776_sympy-14531@sha256:' + IMAGE_SHA

def names(source):
    return sorted(node.name for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.FunctionDef) and node.name.startswith('test_'))

def verified(value, code, case, source_sha):
    require(isinstance(value, dict) and set(value) == {'kind', 'case', 'source_sha256', 'tests_status',
            'repeat_error', 'model_requests', 'official_score', 'passed'}, 'Unexpected judge fields')
    require(value['kind'] == KIND and value['case'] == case and value['source_sha256'] == source_sha,
            'Wrong source/case binding')
    require(type(value['model_requests']) is int and value['model_requests'] == 0
            and value['official_score'] is False, 'No paid or official score claims')
    require(set(value['tests_status']) == {'FEATURE', 'PRESERVATION'}, 'Missing judge groups')
    for group, source in (('FEATURE', feature_tests(case)), ('PRESERVATION', PRESERVATION)):
        row = value['tests_status'][group]
        require(set(row) == {'success', 'failure', 'errors', 'output'} and isinstance(row['output'], str)
                and len(row['output']) <= 12000, 'Invalid group output')
        require(all(isinstance(row[k], list) and all(isinstance(v, str) for v in row[k]) for k in ('success', 'failure')),
                'Invalid case lists')
        combined = row['success'] + row['failure']
        require(len(combined) == len(set(combined)) and sorted(combined) == names(source),
                'Duplicate, forged or missing cases')
        require(isinstance(row['errors'], list) and len(row['errors']) == len(set(row['errors']))
                and all(name in row['failure'] for name in row['errors']), 'Invalid error subset')
    passed = all(not row['failure'] for row in value['tests_status'].values())
    repeated = ('test_unique_declarations' in value['tests_status']['PRESERVATION']['failure']
                and 'test_unique_declarations' not in value['tests_status']['PRESERVATION']['errors'])
    require(type(value['passed']) is bool and value['passed'] == passed
            and type(value['repeat_error']) is bool and value['repeat_error'] == repeated
            and type(code) is int and code == (0 if passed else 1), 'Exit/result mismatch')
    return value

def run_probe(source, case, pin, output):
    require(case in CASES and isinstance(source, bytes) and len(source) <= 100000, 'Unsupported fixture source')
    require(isinstance(pin, dict) and pin.get('digest') == IMAGE_DIGEST
            and pin.get('image_id') == 'sha256:' + IMAGE_SHA, 'Only the existing pinned local image is supported')
    directory = Path(output).resolve() / uuid.uuid4().hex
    directory.mkdir(parents=True)
    files = {name: Path(__file__).with_name(name).read_bytes()
             for name in ('memory_repeat_probe.py', 'memory_repeat_cases.py')}
    intent = {'kind': KIND, 'case': case, 'source_sha256': sha(source),
              'container_name': 'repocare-repeat-' + directory.name, 'image': pin,
              'sources': {name: sha(raw) for name, raw in files.items()}, 'model_requests': 0}
    (directory / 'intent.json').write_text(json.dumps(intent), encoding='utf-8')
    import docker
    client = docker.DockerClient(base_url='npipe:////./pipe/dockerDesktopLinuxEngine'
                                 if os.name == 'nt' else 'unix:///var/run/docker.sock', timeout=30)
    container = None
    try:
        require(client.images.get(pin['digest']).id == pin['image_id'], 'Pinned image changed')
        container = client.containers.create(pin['digest'], name=intent['container_name'], entrypoint=[],
            working_dir='/candidate', command=['/opt/miniconda3/envs/testbed/bin/python', '-I', '-B', '/probe/run.py', case],
            network_disabled=True, cap_drop=['ALL'], security_opt=['no-new-privileges'],
            mem_limit='256m', pids_limit=64,
            log_config={'Type': 'json-file', 'Config': {'max-size': '1m', 'max-file': '1'}})
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode='w') as archive:
            for name in ('candidate', 'probe'):
                member = tarfile.TarInfo(name); member.type = tarfile.DIRTYPE; member.mode = 0o755
                archive.addfile(member)
            for name, raw in [('candidate/printer.py', source), ('probe/run.py', files['memory_repeat_probe.py']),
                              ('probe/memory_repeat_cases.py', files['memory_repeat_cases.py'])]:
                member = tarfile.TarInfo(name); member.size = len(raw); member.mode = 0o644
                archive.addfile(member, io.BytesIO(raw))
        require(container.put_archive('/', payload.getvalue()), 'Probe upload failed')
        container.start(); terminal = container.wait(timeout=75)
        stdout, stderr = read_logs(container, True), read_logs(container, False)
        (directory / 'stdout.log').write_bytes(stdout); (directory / 'stderr.log').write_bytes(stderr)
        value = verified(json.loads(stdout.splitlines()[-1]), terminal['StatusCode'], case, sha(source))
        receipt = {'result': value, 'exit_code': terminal['StatusCode'], 'network_disabled': True,
                   'host_mounts': False, 'infrastructure_error': False, 'image': pin, 'evidence_directory': str(directory)}
        (directory / 'result.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        return receipt
    finally:
        try:
            if container is not None:
                container.remove(force=True)
        finally:
            client.close()

def snapshot(root, case, workspace):
    root = Path(root).resolve(strict=True); workspace = Path(workspace).resolve(strict=True)
    manifest = json.loads((root / 'manifest.json').read_bytes())
    require(manifest['kind'] == KIND and manifest['root'] == str(root) and case in CASES, 'Wrong frozen suite')
    executable = shutil.which('git')
    require(executable and not Path(executable).resolve().is_relative_to(workspace), 'Trusted Git required')
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0')
    def git(*args):
        return subprocess.check_output([executable, '-c', 'core.hooksPath=' + os.devnull,
            '-c', 'core.fsmonitor=false', '-C', str(workspace), *args], env=env, stderr=subprocess.PIPE, timeout=20)
    require(Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve() == workspace, 'Expected worktree root')
    common = Path(git('rev-parse', '--git-common-dir').decode().strip())
    common = (workspace / common).resolve() if not common.is_absolute() else common.resolve()
    matches = [cell for cell in manifest['cells'] if cell['case'] == case and
               common == Path(cell['source']) / '.git']
    require(len(matches) == 1 and git('rev-parse', 'HEAD').decode().strip() == matches[0]['base_sha'], 'Wrong fixed base')
    require(set(git('diff', '--name-only', 'HEAD').decode().splitlines()) <= {'printer.py'}
            and not git('ls-files', '--others', '--exclude-standard').strip(), 'Candidate outside scope')
    for name, expected in (('printer.py', None), ('test_feature.py', feature_tests(case).encode())):
        path = workspace / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 100000, 'Invalid source file')
        if expected is not None:
            require(path.read_bytes() == expected, 'Visible checks modified')
    require(git('show', 'HEAD:printer.py') == fixture(case).encode(), 'Base source changed')
    return {'workspace': str(workspace), 'common': str(common), 'base': matches[0]['base_sha'],
            'source': (workspace / 'printer.py').read_bytes(), 'diff': git('diff', '--binary', 'HEAD')}

def evaluate(root, case, workspace):
    before = snapshot(root, case, workspace)
    pin = json.loads((Path(root) / 'image-lock.json').read_bytes())
    receipt = run_probe(before['source'], case, pin, Path(root) / 'judge-results')
    require(snapshot(root, case, workspace) == before, 'Candidate changed during checks')
    result = receipt['result']
    # Actual text from both groups reaches the original controller after the first plan is saved.
    return {**result, 'source_unchanged': True, 'infrastructure_error': False,
            'failure_excerpt': '\n'.join(row['output'] for row in result['tests_status'].values() if row['failure'])[-16000:],
            'evidence_directory': receipt['evidence_directory']}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True); parser.add_argument('--case', choices=CASES, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    try:
        value = evaluate(args.root, args.case, args.workspace); code = 0 if value['passed'] else 1
    except Exception as error:
        value = {'infrastructure_error': True, 'error': type(error).__name__ + ': ' + str(error), 'model_requests': 0}; code = 2
    print(json.dumps(value), flush=True)
    raise SystemExit(code)
