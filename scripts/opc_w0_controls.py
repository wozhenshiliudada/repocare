"""Create new authored control repositories; execute only this file's own fixtures.

No downloaded code, model endpoint, production service or existing run ledger is
opened. The new output directory is never reused, deleted or reset.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from opc_w0_support import check_outcome

GOOD = '''from decimal import Decimal, ROUND_HALF_UP

def discounted(amount, percent):
    if not Decimal("0") <= percent <= Decimal("100"):
        raise ValueError("discount outside 0..100")
    return (amount * (Decimal("1") - percent / Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
'''
BROKEN = GOOD.replace('Decimal("1") - percent', 'Decimal("1") + percent')
WRONG = GOOD.replace('(Decimal("1") - percent / Decimal("100"))', 'Decimal("0.8")')
FILES = {
    'checkout/__init__.py': '',
    'checkout/lines.py': 'def subtotal(items):\n    return sum(price * quantity for price, quantity in items)\n',
    'checkout/api.py': 'from .lines import subtotal\nfrom .discounts import discounted\n\ndef total(items, percent):\n    return discounted(subtotal(items), percent)\n',
    'tests/__init__.py': '',
    'tests/test_checkout.py': '''import unittest
from decimal import Decimal as D
from checkout.api import total

class CheckoutTests(unittest.TestCase):
    def test_discount(self): self.assertEqual(total([(D("100"), 1)], D("20")), D("80.00"))
    def test_quantity(self): self.assertEqual(total([(D("19.99"), 3)], D("20")), D("47.98"))
    def test_no_discount(self): self.assertEqual(total([(D("100"), 1)], D("0")), D("100.00"))
    def test_full_discount(self): self.assertEqual(total([(D("100"), 1)], D("100")), D("0.00"))
''',
}
JUDGE = '''import unittest
from decimal import Decimal as D
from checkout.api import total

class PreservationTests(unittest.TestCase):
    def test_other_discount(self): self.assertEqual(total([(D("100"), 1)], D("15")), D("85.00"))
    def test_invalid_discount(self):
        with self.assertRaises(ValueError): total([(D("100"), 1)], D("101"))
'''


def run(argv, cwd, expected=None):
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT'}}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    result = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=30, shell=False)
    row = {'argv': argv, 'returncode': result.returncode,
           'stdout': result.stdout.decode('utf-8', errors='replace'),
           'stderr': result.stderr.decode('utf-8', errors='replace')}
    if expected is not None and result.returncode != expected:
        raise RuntimeError(json.dumps(row))
    return row


def populate(directory, discount, judge=False):
    directory.mkdir()
    for name, value in {**FILES, 'checkout/discounts.py': discount}.items():
        target = directory / name
        target.parent.mkdir(exist_ok=True)
        target.write_text(value, encoding='utf-8', newline='\n')
    if judge:
        (directory / 'tests/test_preservation.py').write_text(JUDGE, encoding='utf-8', newline='\n')


def prepare(output, git):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    history = output / 'history'
    populate(history, GOOD)
    run([git, 'init', '-b', 'main'], history, 0)
    def commit(message):
        run([git, 'add', '.'], history, 0)
        run([git, '-c', 'user.name=RepoCare W0 Fixture', '-c',
             'user.email=w0-fixture@example.invalid', 'commit', '-m', message], history, 0)
        return run([git, 'rev-parse', 'HEAD'], history, 0)['stdout'].strip()
    success = commit('fixture: correct checkout')
    (history / 'checkout/discounts.py').write_text(BROKEN, encoding='utf-8', newline='\n')
    failing = commit('fixture: introduce checkout regression')
    diff = run([git, 'diff', '--no-ext-diff', '--no-textconv', success, failing, '--'], history, 0)
    records = []
    for name, code, expected in [('original_good', GOOD, 0), ('regression', BROKEN, 1),
                                 ('reference', GOOD, 0), ('wrong_candidate', WRONG, 1)]:
        target = output / name
        populate(target, code, judge=True)
        row = run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'], target, expected)
        matches = re.findall(r'Ran (\d+) tests?', row['stderr'])
        row.update(name=name, tests_run=int(matches[-1]) if matches else None)
        row['outcome'] = check_outcome(row['returncode'], False, row['tests_run'])
        if row['tests_run'] != 6:
            raise RuntimeError('Control test discovery changed')
        records.append(row)
    empty = output / 'empty'
    empty.mkdir()
    row = run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', '.'], empty)
    # Python versions differ: older unittest returns 0, Python 3.14 returns 5.
    if row['returncode'] not in {0, 5}:
        raise RuntimeError('Unexpected zero-test process failure')
    if not re.search(r'Ran 0 tests', row['stderr']):
        raise RuntimeError('Zero-test control did not actually discover zero tests')
    row.update(name='zero_tests', tests_run=0, outcome=check_outcome(row['returncode'], False, 0))
    records.append(row)
    row = run([sys.executable, '-B', '-c', 'import repocare_w0_missing_dependency'], empty, 1)
    if 'ModuleNotFoundError' not in row['stderr']:
        raise RuntimeError('Missing-dependency control did not fail as expected')
    row.update(name='missing_dependency', tests_run=None, outcome=check_outcome(1, False, None))
    records.append(row)
    if run([git, 'status', '--porcelain'], history, 0)['stdout']:
        raise RuntimeError('Authored fixture repository is dirty')
    result = {'schema_version': 1, 'fixture_only': True, 'model_calls': 0,
              'production_workflow_exercised': False, 'runtime_authorized': False,
              'good_sha': success, 'failing_sha': failing, 'git_diff': diff['stdout'],
              'subprocess_checks': len(records), 'distinct_fixture_tests': 6, 'checks': records}
    (output / 'controls-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--git', required=True)
    args = parser.parse_args()
    result = prepare(args.output, args.git)
    print(json.dumps({k: v for k, v in result.items() if k not in {'checks', 'git_diff'}}))
