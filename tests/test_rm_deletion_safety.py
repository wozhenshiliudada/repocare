"""Negative safety tests for the rm-deletes-everything incident class.

Covers three layers from docs/digital-media-contest/rm-deletion-safety-impl-brief.md:
path primitives (canonical_path / safe_context_path), policy validation
(Policy.validate_scope / Policy.validate) and the workflow (workspace creation,
candidate cleanup, full repair against a rogue destructive plan, shell=False
test execution).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer import runner
from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import (Edit, Plan, Policy, SafetyError, canonical_path,
                                    safe_context_path)
from repo_maintainer.provider import MockProvider
from repo_maintainer.runner import run_test
from repo_maintainer.workflow import repair


class PathLayerTests(unittest.TestCase):
    def test_canonical_path_rejects_root_and_absolute_paths(self):
        for value in ("/", "C:\\", "/etc/passwd", "C:/Windows/system32"):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                canonical_path(value)

    def test_canonical_path_rejects_parent_traversal(self):
        for value in ("../secret", "foo/../../etc"):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                canonical_path(value)

    def test_canonical_path_rejects_dotgit_and_reserved_names(self):
        for value in (".git/config", "CON", "AUX", "NUL", "COM1", "LPT1"):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                canonical_path(value)

    def test_safe_context_path_rejects_sensitive_files(self):
        for value in (".env", ".env.local", "credentials.json", "id_rsa.pem", "secrets.yaml"):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                safe_context_path(value)


class PolicyLayerTests(unittest.TestCase):
    def test_dangerous_command_in_allowed_tests_is_still_list_literal(self):
        # The whitelist mechanism itself only checks non-empty string lists, not
        # command semantics. The real protection is that allowed_tests entries are
        # exact string lists. shell=False prevents implicit shell interpretation,
        # but does not make an explicitly authorized executable safe.
        policy = Policy(("calculator.py",), (("rm", "-rf", "/"),))
        policy.validate_scope()  # scope check passes: entries are plain string lists
        for command in policy.allowed_tests:
            self.assertIsInstance(command, tuple)
            self.assertTrue(all(isinstance(arg, str) and arg for arg in command))
        # Validate data only: never execute this explicitly authorized command.
        plan = Plan("issue", "0" * 64, "diagnosis", "low",
                    (Edit("calculator.py", "0" * 64, "content"),),
                    policy.allowed_tests, "mock")
        policy.validate(plan)

    def test_rogue_plan_with_dangerous_test_is_rejected(self):
        policy = Policy(("calculator.py",), (DEMO_TEST,))
        plan = Plan("issue", "0" * 64, "rogue diagnosis", "low",
                    (Edit("calculator.py", "0" * 64, "content"),),
                    (("rm", "-rf", "/"),), "rogue")
        with self.assertRaisesRegex(SafetyError, "not explicitly authorized"):
            policy.validate(plan)

    def test_policy_rejects_edits_outside_allowed_paths(self):
        policy = Policy(("calculator.py",), (DEMO_TEST,))
        for path in ("/etc/passwd", "../foo", ".git/config", "C:/Windows/system32/config"):
            plan = Plan("issue", "0" * 64, "rogue diagnosis", "low",
                        (Edit(path, "0" * 64, "content"),), (DEMO_TEST,), "rogue")
            with self.subTest(path=path), self.assertRaises(SafetyError):
                policy.validate(plan)


class RunnerShellTests(unittest.TestCase):
    def test_test_runner_uses_shell_false(self):
        metachars = "a && b | c; $(d) `e`"
        real_popen = runner.subprocess.Popen
        captured = {}

        def spy(*args, **kwargs):
            captured.update(kwargs)
            return real_popen(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(runner.subprocess, "Popen", spy):
                result = run_test(("{python}", "-c", "import sys; print(sys.argv[1])", metachars),
                                  Path(temp), 10)
        self.assertEqual(0, result["returncode"], result["stderr"])
        self.assertIsInstance(result["command"], list)
        self.assertTrue(all(isinstance(part, str) for part in result["command"]))
        self.assertEqual([metachars], result["command"][3:])
        self.assertIn(metachars, result["stdout"])  # passed verbatim, not interpreted
        self.assertFalse(captured.get("shell", False))


class WorkflowLayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / "repo")
        self.gateway = RepositoryGateway(self.repo)
        self.before = self.gateway.snapshot()
        self.policy = Policy(("calculator.py",), (DEMO_TEST,))
        self.runs = self.root / "runs"

    def test_create_workspace_refuses_path_inside_source_repo(self):
        with self.assertRaises(SafetyError):
            self.gateway.create_workspace(self.repo / "subworkspace",
                                          "maintenance/inside", self.before["head"])
        self.assertFalse((self.repo / "subworkspace").exists())

    def test_discard_candidate_refuses_foreign_directory(self):
        foreign = self.root / "other_run" / "workspace"
        foreign.mkdir(parents=True)
        (foreign / "keep.txt").write_text("evidence")
        with self.assertRaises(SafetyError):
            self.gateway.discard_candidate(foreign, "maintenance/foreign", self.before["head"],
                                           self.root / "current_run")
        self.assertTrue(foreign.exists())
        self.assertTrue((foreign / "keep.txt").exists())

    def test_full_workflow_rogue_destructive_plan_is_blocked_before_write(self):
        class RogueProvider(MockProvider):
            def __init__(self, tests=None, edits=None):
                super().__init__("fix")
                self.rogue_tests, self.rogue_edits = tests, edits

            def plan(self, repository, issue, allowed):
                plan = super().plan(repository, issue, allowed)
                return replace(plan,
                               tests=self.rogue_tests if self.rogue_tests is not None else plan.tests,
                               edits=self.rogue_edits if self.rogue_edits is not None else plan.edits)

        cases = [
            ((("rm", "-rf", "/"),), None, "Test command is not explicitly authorized"),
            ((("powershell", "-Command", "Remove-Item", "C:\\"),), None,
             "Test command is not explicitly authorized"),
            ((("bash", "-c", "rm -rf /"),), None, "Test command is not explicitly authorized"),
            (None, (Edit("/etc/passwd", "0" * 64, "root:x:0:0\n"),), "Unsafe repository path"),
            (None, (Edit("../outside.txt", "0" * 64, "content"),), "Unsafe repository path"),
            (None, (Edit(".git/config", "0" * 64, "content"),), "Unsafe repository path"),
        ]
        for tests, edits, error in cases:
            with self.subTest(tests=tests, edits=edits):
                if tests is not None:
                    self.assertTrue(all(isinstance(command, tuple) for command in tests))
                with patch("repo_maintainer.workflow.run_test") as test_runner:
                    result = repair(self.repo, "Addition bug", RogueProvider(tests, edits),
                                    self.policy, "auto", self.runs)
                test_runner.assert_not_called()
                self.assertEqual("failed", result["status"], result)
                self.assertIn(error, result["error"], result)
                self.assertFalse(Path(result["workspace"]).exists())
                self.assertTrue(result["source_unchanged"], result)
                self.assertEqual(self.before, self.gateway.snapshot())


if __name__ == "__main__":
    unittest.main()
