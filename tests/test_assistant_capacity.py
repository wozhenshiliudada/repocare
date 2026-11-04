"""Pipe byte budgets remain bounded and separate from declared model tokens."""
import subprocess
import sys
import threading
import unittest

from repo_maintainer.pi_live import communicate_live
from repo_maintainer.assistant_limits import MAX_ASSISTANT_NATIVE_BYTES, MAX_ASSISTANT_TEXT_BYTES, MAX_ASSISTANT_REPLY_BYTES
from repo_maintainer.maintenance_limits import LIMITS


class AssistantCapacityTests(unittest.TestCase):
    def capture(self, **options):
        code="import sys;sys.stdin.buffer.read();sys.stdout.buffer.write(b'x'*(3*1024**2))"
        process=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            return communicate_live(process,b'{}',1,lambda _value:None,threading.Event(),10,**options)[0]
        finally:
            process.wait(timeout=10)
            for stream in (process.stdin,process.stdout,process.stderr):
                stream.close()

    def test_assistant_capture_supports_large_replies_while_old_default_stays_bounded(self):
        self.assertEqual(2100001,len(self.capture()))
        self.assertEqual(3*1024**2,len(self.capture(output_limit_bytes=MAX_ASSISTANT_REPLY_BYTES)))

    def test_native_budget_uses_existing_resource_config_and_capture_rejects_unbounded_values(self):
        self.assertEqual(LIMITS['native_session_max_bytes'],MAX_ASSISTANT_NATIVE_BYTES)
        self.assertGreaterEqual(MAX_ASSISTANT_TEXT_BYTES,16*1024**2)
        for limit in (0,True,MAX_ASSISTANT_REPLY_BYTES+1):
            with self.assertRaises(ValueError):
                communicate_live(None,b'',1,lambda _value:None,threading.Event(),10,output_limit_bytes=limit)
