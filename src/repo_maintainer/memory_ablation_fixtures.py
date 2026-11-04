"""Authored, public engineering fixtures. Never describe these as blind tasks."""
from __future__ import annotations

from .task_session import fingerprint

ISSUE_ADD = 'Fix addition and preserve negative and mixed operands.'


def _case(identity, issue, source, first, second, visible, hidden, topic):
    def tests(rows, name):
        lines = ['import unittest', 'from calculator import logic', '', 'class Checks(unittest.TestCase):']
        for n, (args, expected) in enumerate(rows):
            lines += [f'    def test_{name}_{n}(self):', f'        self.assertEqual(logic{args!r}, {expected!r})']
        return '\n'.join(lines) + '\n'
    return {'id': identity, 'issue': issue, 'source': 'def logic(a, b):\n    ' + source + '\n',
            'first': first, 'second': second,
            'visible_tests': tests(visible, 'visible'), 'grader_tests': tests(hidden, 'independent'),
            'note': {'kind': 'procedure', 'title': topic,
                     'summary': 'The earlier trial failed the original regression tests and was rolled back.',
                     'triggers': [issue], 'preconditions': ['Compare current source and policy with the original evidence.'],
                     'steps': ['Inspect the real failed assertions; do not repeat the failed trial blindly.'],
                     'verification': ['Run the currently authorized tests and retain their exit codes.'],
                     'stop_conditions': ['Stop on missing evidence, changed policy or exhausted budget.']}}


def cases():
    return [
        _case('signed_add', ISSUE_ADD, 'return a - b', 'return abs(a) + abs(b)', 'return a + b',
              [((2, 3), 5), ((-2, -3), -5), ((-2, 3), 1), ((0, 0), 0)],
              [((-9, 4), -5), ((11, -2), 9), ((0, -7), -7), ((8, 6), 14)],
              'Addition and signed mixed operands'),
        _case('lower_bound', 'Fix lower bound clamp and preserve values above the lower bound.',
              'return a', 'return min(a, b)', 'return max(a, b)',
              [((2, 3), 3), ((4, 3), 4), ((-5, -2), -2), ((0, 0), 0)],
              [((9, 4), 9), ((-9, -4), -4), ((1, 8), 8), ((7, 7), 7)],
              'Lower bound clamp and unchanged upper values')]


def suite_sha256():
    return fingerprint({'schema_version': 1, 'cases': cases(), 'kind': 'public-authored-engineering'})
