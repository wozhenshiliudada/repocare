"""Pure Native parsing: configured request limit does not grant source access."""
import json
import tempfile
import unittest
from pathlib import Path

import test_task_session as fixture
from repo_maintainer.models import SafetyError
from repo_maintainer.runtime_limits import MAX_REQUESTS
from repo_maintainer.task_session import validate_transcript


class NativeRequestLimitsTests(unittest.TestCase):
    with_denied_read = fixture.TaskSessionTests.with_denied_read

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='native-request-limits-')
        self.addCleanup(temporary.cleanup)
        self.cwd = Path(temporary.name)/'workspace'
        self.cwd.mkdir()
        self.entries = fixture.transcript(self.cwd)

    def denied(self, remaining):
        entries = self.with_denied_read()
        result = entries[5]['message']
        result['details']['requests_remaining'] = remaining
        result['content'][0]['text'] = json.dumps(result['details'])
        return entries

    def validate(self, entries):
        return validate_transcript(fixture.raw(entries), cwd=self.cwd, context=fixture.CONTEXT)

    def test_denied_read_accepts_old_and_configured_remaining_bounds(self):
        for remaining in (0, 100, 101, 499, MAX_REQUESTS):
            with self.subTest(remaining=remaining):
                self.assertEqual(1, self.validate(self.denied(remaining))['rounds'])

    def test_denied_read_rejects_out_of_bounds_and_non_integer_remaining(self):
        for remaining in (MAX_REQUESTS+1, -1, True, 1.5, str(MAX_REQUESTS-1)):
            with self.subTest(remaining=remaining), self.assertRaisesRegex(SafetyError, 'denied-read receipt'):
                self.validate(self.denied(remaining))

    def test_larger_remaining_cannot_weaken_denial_or_source_scope_guards(self):
        for mutation in ('success', 'extra_field', 'content', 'details', 'wrong_error',
                         'missing_details', 'foreign_proposal'):
            entries = self.denied(MAX_REQUESTS)
            result = entries[5]['message']
            if mutation == 'success':
                result['isError'] = False
            elif mutation == 'extra_field':
                result['details']['content'] = 'unauthorized source body'
                result['content'][0]['text'] = json.dumps(result['details'])
            elif mutation == 'content':
                result['content'][0]['text'] = json.dumps({'content': 'unauthorized source body'})
            elif mutation == 'details':
                result['details']['requests_remaining'] = MAX_REQUESTS-1
            elif mutation == 'wrong_error':
                result['details']['error'] = 'Unknown failure'
                result['content'][0]['text'] = json.dumps(result['details'])
            elif mutation == 'missing_details':
                del result['details']
            else:
                entries[4]['message']['content'][0].update(name='propose_repair',
                    arguments={'edits': [{'path': 'outside.py'}]})
                result['toolName'] = 'propose_repair'
            with self.subTest(mutation=mutation), self.assertRaises(SafetyError):
                self.validate(entries)


if __name__ == '__main__':
    unittest.main()
