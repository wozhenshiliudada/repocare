"""The output's claimed identity must match its actual pinned SDK Native receipt."""
from pathlib import Path
from types import SimpleNamespace
import unittest

import test_validation_native as native_fixture
from repo_maintainer.models import SafetyError
from repo_maintainer.task_session import fingerprint
from repo_maintainer.validation_provider import _native_result


class ValidationOutputBindingTests(unittest.TestCase):
    def test_synchronized_stdout_hash_tampering_cannot_replace_original_native_identity(self):
        fixture=native_fixture.ValidationNativeTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        original=fixture.create()
        files=[{'path':'calculator.py','content':'synthetic source','editable':False,'before_sha256':'a'*64}]
        native=next(e['message']['details'] for e in fixture.entries
                    if e.get('message',{}).get('role')=='toolResult')
        data={'proposal':native_fixture.proposal(),'proposal_sha256':native['proposal_sha256'],
              'evidence':{'validation_proposal_sha256':native['proposal_sha256']},
              'task_checkpoint':{'task_id':fixture.identity,'revision':0,
                  'file':Path(original['session_file']).name,'leaf_id':original['leaf_id'],
                  'session_id':original['session_id'],'snapshot_sha256':fingerprint(files)}}
        provider=SimpleNamespace(_task_session_request=fixture.task)
        self.assertEqual(data['task_checkpoint'],_native_result(provider,data,files))
        data['proposal_sha256']=data['evidence']['validation_proposal_sha256']='f'*64
        with self.assertRaisesRegex(SafetyError,'accepted original Native tool call'):
            _native_result(provider,data,files)


if __name__=='__main__':unittest.main()
