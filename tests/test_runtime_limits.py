import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repo_maintainer import pi_evidence
from repo_maintainer.cli import main
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.models import SafetyError
from repo_maintainer.runtime_limits import (MAX_PROGRESS_BYTES, MAX_REQUESTS, MAX_USAGE_RECORDS,
                                          MAX_SAFE_TOKEN_COUNT, DEFAULT_MAX_OUTPUT_TOKENS,
                                          PLANNING_TIMEOUT_SECONDS, PROCESS_TIMEOUT_SECONDS)
from repo_maintainer.ui_registry import UIRegistry, provider_binding
from test_pi_evidence import evidence


class RuntimeLimitsTests(unittest.TestCase):
    def test_cli_workbench_defaults_to_configured_max_and_accepts_lower_legacy_budget(self):
        for args,expected in [([],MAX_REQUESTS),(['--request-budget','6'],6),(['--request-budget','50'],50),(['--request-budget','100'],100)]:
            with patch('repo_maintainer.repository_workbench.RepositoryWorkbench') as constructor, \
                    patch('repo_maintainer.repository_workbench.serve_workbench'):
                self.assertEqual(0,main(['workbench','--workspace-root','unused','--provider-config','unused.local',*args]))
                self.assertEqual(expected,constructor.call_args.kwargs['request_budget'])
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(['workbench','--workspace-root','unused','--provider-config','unused.local','--request-budget',str(MAX_REQUESTS+1)])

    def test_larger_default_cannot_rebind_or_refund_a_legacy_unknown_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            old=PiProvider(('calculator.py',),max_requests=6)
            binding={'repository':'fixture','provider':provider_binding(old)}
            registry=UIRegistry(root,binding);registry.reserve();registry.close()
            path=root/'ui-state/registry.sqlite3';before=path.read_bytes()
            with self.assertRaises(SafetyError):
                UIRegistry(root,{'repository':'fixture','provider':provider_binding(PiProvider(('calculator.py',)))})
            self.assertEqual(before,path.read_bytes())
            registry=UIRegistry(root,binding)
            try:
                self.assertEqual(6,registry.budget()['requests_reserved_unknown'])
                self.assertEqual(0,registry.budget()['requests_remaining'])
            finally:registry.close()

    def test_parent_deadline_and_evidence_preserve_configured_usage_bound(self):
        self.assertEqual(PROCESS_TIMEOUT_SECONDS,pi_evidence.PROCESS_TIMEOUT_SECONDS)
        self.assertGreater(PROCESS_TIMEOUT_SECONDS,PLANNING_TIMEOUT_SECONDS)
        data=evidence();data.update(requests_started=MAX_REQUESTS,usage=[{'input':1,'output':2}]*MAX_USAGE_RECORDS,
            tool_budget=MAX_REQUESTS*4,tool_calls=MAX_REQUESTS*4,max_output_tokens=128000,planning_timeout_seconds=PLANNING_TIMEOUT_SECONDS)
        self.assertEqual(MAX_USAGE_RECORDS,len(pi_evidence.validate_evidence(data,MAX_REQUESTS)['usage']))
        selected=copy.deepcopy(data);selected['max_output_tokens']=4096
        self.assertEqual(4096,pi_evidence.validate_evidence(selected,MAX_REQUESTS)['max_output_tokens'])
        self.assertFalse(pi_evidence.matches_model_limits(selected,{'limits_explicit':False,'max_output_tokens':128000}))
        for field,value in [('usage',[{}]*(MAX_USAGE_RECORDS+1)),('planning_timeout_seconds',60),('max_output_tokens',MAX_SAFE_TOKEN_COUNT+1),('tool_budget',MAX_REQUESTS*4+1)]:
            invalid=copy.deepcopy(data);invalid[field]=value
            with self.assertRaises(SafetyError):pi_evidence.validate_evidence(invalid,MAX_REQUESTS)

    def test_large_declared_outputs_remain_exact_receipts_with_legacy_default_unchanged(self):
        self.assertEqual(128000,DEFAULT_MAX_OUTPUT_TOKENS)
        for output in (128001,1000000,1500000,MAX_SAFE_TOKEN_COUNT):
            data={**evidence(), 'max_output_tokens':output}
            self.assertEqual(output,pi_evidence.validate_evidence(data,1)['max_output_tokens'])
            self.assertTrue(pi_evidence.matches_model_limits(data,{'limits_explicit':True,'max_output_tokens':output}))
            self.assertFalse(pi_evidence.matches_model_limits(data,{'limits_explicit':True,'max_output_tokens':output-1}))
            compact={**data,'purpose':'task_compaction','compaction_context_sha256':'a'*64,'compaction_max_output_tokens':output}
            self.assertEqual(output,pi_evidence.validate_evidence(compact,1)['compaction_max_output_tokens'])
        for output in (True,0,-1,1.5,MAX_SAFE_TOKEN_COUNT+1):
            with self.assertRaises(SafetyError):pi_evidence.validate_evidence({**evidence(),'max_output_tokens':output},1)

    def test_larger_progress_bound_retains_valid_frames_without_adopting_raw_text(self):
        frame=evidence('running')
        raw=b'x'*(2*1024*1024)+b'\n'+pi_evidence.PREFIX+json.dumps(frame).encode()+b'\n'
        self.assertEqual(frame,pi_evidence.progress_evidence(raw,50))
        self.assertIsNone(pi_evidence.progress_evidence(b'x'*(MAX_PROGRESS_BYTES+1),50))


if __name__=='__main__':unittest.main()
