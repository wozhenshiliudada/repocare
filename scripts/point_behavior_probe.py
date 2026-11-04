"""Owned development checks, deliberately separate from official/held-out scores.

Imported by the host for constants only; repository code is imported only by the
probe in a fresh network-disabled container. No reference repair is needed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

KIND = 'sympy-point-development-behavior-v1'
BASES = {'sympy__sympy-17655': 'f5e965947af2410ded92cfad987aaf45262ea434',
         'sympy__sympy-22714': '3ff4717b6aef6086e78f01cdfa06f64ae23aed7e'}
PRESERVATION = ('reject_imaginary_keyword_evaluate_false',
                'reject_imaginary_global_evaluate_false', 'reject_imaginary_default')
GROUPS = {'preserve-imaginary': PRESERVATION,
          'real-coordinates': ('real_coordinates_global_evaluate_false',),
          'all': ('real_coordinates_global_evaluate_false',) + PRESERVATION}


def check_case(name):
    import sympy
    assert str(Path(sympy.__file__).resolve()).startswith('/testbed/sympy/'), 'Wrong SymPy source origin'
    from sympy import I, Integer, Point, evaluate
    if name == 'real_coordinates_global_evaluate_false':
        with evaluate(False):
            point = Point(Integer(1), Integer(2))
            assert tuple(point) == (Integer(1), Integer(2)), 'Real coordinates changed'
        return
    def rejected():
        try:
            if name == 'reject_imaginary_keyword_evaluate_false':
                Point(I, 0, evaluate=False)
            else:
                Point(I, 0)
        except ValueError as error:
            assert 'Imaginary coordinates are not permitted' in str(error), 'Unexpected ValueError: ' + str(error)
        else:
            raise AssertionError('Forbidden imaginary coordinates returned without the expected ValueError')
    if name == 'reject_imaginary_global_evaluate_false':
        with evaluate(False):
            rejected()
    else:
        rejected()


def run_cases(group):
    rows = []
    for name in GROUPS[group]:
        try:
            check_case(name)
        except Exception:
            rows.append({'case': name, 'passed': False, 'diagnostic': traceback.format_exc()[-4000:]})
        else:
            rows.append({'case': name, 'passed': True, 'diagnostic': ''})
    return rows


def probe(base, group):
    if base not in BASES.values():
        raise ValueError('Unexpected frozen source base')
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
    def git(*args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                                        '-C', '/testbed', *args], env=env, stderr=subprocess.PIPE, timeout=30)
    git('checkout', '--detach', base)
    if git('rev-parse', 'HEAD').decode().strip() != base:
        raise ValueError('Container checkout did not reach frozen base')
    patch = Path('/probe/patch.diff').read_bytes()
    if patch:
        git('apply', '--check', '/probe/patch.diff')
        git('apply', '/probe/patch.diff')
    changed = set(git('diff', '--name-only', '-z', 'HEAD').decode().strip('\0').split('\0')) - {''}
    if not changed <= {'sympy/geometry/point.py'}:
        raise ValueError('Patch changed files outside the fixed point.py scope')
    sys.path.insert(0, '/testbed')
    # No host repository import; this file is executed only inside the container.
    rows = run_cases(group)
    return {'kind': KIND, 'base_commit': base, 'patch_sha256': hashlib.sha256(patch).hexdigest(),
            'group': group, 'cases': rows, 'passed': sum(row['passed'] for row in rows),
            'total': len(rows), 'model_requests': 0, 'official_score': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--group', choices=GROUPS, required=True)
    args = parser.parse_args()
    try:
        result = probe(args.base, args.group)
        code = 0 if result['passed'] == result['total'] else 1
    except Exception as error:
        result = {'kind': KIND, 'infrastructure_error': True, 'error': type(error).__name__ + ': ' + str(error)}
        code = 2
    print(json.dumps(result), flush=True)
    raise SystemExit(code)
