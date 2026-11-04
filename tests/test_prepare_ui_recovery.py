"""Real process exit, persisted UI jobs, fresh claims and explicit recovery."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import psutil
from starlette.testclient import TestClient
from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.integration import save_review
from repo_maintainer.local_ui import UISession, create_app
from repo_maintainer.models import Policy
from repo_maintainer.provider import MockProvider
from repo_maintainer.recovery import process_identity


class PrepareUIRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / "repository")
        self.source = RepositoryGateway(self.repo)
        self.before = self.source.snapshot()
        self.policy = Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",))
        self.session = self.client = None
        self.open()
        self.addCleanup(self.close)

    def open(self):
        self.session = UISession(self.repo, self.policy, MockProvider(), self.root / "runs")
        self.client = TestClient(create_app(self.session, 12345), base_url="http://127.0.0.1:12345")
        self.headers = {"Authorization": "Bearer " + self.session.token}

    def close(self):
        if self.session:
            self.session.close()
            self.client.close()
            self.session = self.client = None

    def state(self):
        response = self.client.get("/api/state", headers=self.headers)
        self.assertEqual(200, response.status_code)
        return response.json()

    def current_run(self):
        return self.state()["runs"][-1]

    def action(self, action, fields=None, failed=None):
        if fields is None:
            run = self.current_run()
            fields = {"run_id": run["run_id"], "revision": run["revision"]}
        response = self.client.post("/api/actions/" + action,
            json={**fields, "request_id": uuid.uuid4().hex}, headers=self.headers)
        self.assertEqual(202, response.status_code, response.text)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = next(j for j in self.state()["jobs"] if j["id"] == response.json()["id"])
            if job["status"] != "running":
                self.assertEqual("failed" if failed else "finished", job["status"], job)
                if failed:
                    self.assertIn(failed, job["error"])
                return job
            time.sleep(.03)
        self.fail("UI job deadline")

    def candidate(self):
        self.action("plan", {"issue": "addition bug"})
        plan = self.state()["plans"][-1]
        self.action("decision", {"plan_id": plan["id"], "fingerprint": plan["fingerprint"], "decision": "approve"})
        run = self.current_run()
        self.assertEqual("candidate_ready", run["status"])
        self.directory = self.root / "runs" / run["run_id"]
        self.candidate_before = RepositoryGateway(self.directory / "workspace").snapshot()

    def crash(self, stage, live=False):
        prior = self.current_run()
        old_payload = {"run_id":prior["run_id"], "revision":prior["revision"], "retry_aborted":False}
        self.old_headers = dict(self.headers)
        self.close()
        argv = [sys.executable, str(Path(__file__).with_name("prepare_ui_crash.py")), str(self.root), stage]
        if live:
            argv.append("--live-test")
        result = subprocess.run(argv, capture_output=True, timeout=50)
        self.assertEqual(91, result.returncode, result.stderr.decode(errors="replace"))
        self.open()
        self.assertEqual(401, self.client.get("/api/state", headers=self.old_headers).status_code)
        self.assertFalse(self.current_run()["claimed"])
        job = next(j for j in self.state()["jobs"] if j["id"] == prior["run_id"])
        self.assertEqual("interrupted", job["status"])
        self.assertEqual("interrupted", self.session.submit(prior["run_id"], "prepare", old_payload)["status"])
        self.assertEqual(0, self.state()["requests_used"])
        self.action("recover", failed="explicit claim")
        self.action("claim_run")

    def review(self):
        return json.loads((self.directory / "integration-review.json").read_text(encoding="utf-8"))

    def unchanged(self):
        self.assertEqual(self.before, self.source.snapshot(exclude_branch="maintenance/" + self.current_run()["run_id"]))
        self.assertEqual(self.candidate_before, RepositoryGateway(self.directory / "workspace").snapshot())
        self.assertEqual(b"", self.source.command("for-each-ref", "--format=%(refname)", "refs/heads/integration"))

    def test_creation_testing_and_cleanup_exit_windows_reopen_without_replay(self):
        # Each subprocess registers its own prepare intent and exits without closing.
        for stage in ("before_add", "after_add", "verification_ready", "test_finished", "cleanup_intent", "after_remove"):
            with self.subTest(stage=stage):
                self.candidate()
                self.crash(stage)
                self.action("cleanup", failed="preparation")
                self.action("recover")
                self.assertEqual("aborted", self.current_run()["review"]["status"])
                self.assertFalse((self.directory / "integration-verification").exists())
                self.unchanged()
                self.action("cleanup")

    def test_new_attempt_full_tests_archive_and_new_publication_approval(self):
        self.candidate()
        self.crash("after_remove")
        old = self.review()
        self.action("recover")
        evidence = (self.directory / "integration-review.json").read_bytes()
        run = self.current_run()
        self.action("prepare", {"run_id":run["run_id"], "revision":run["revision"], "retry_aborted":True})
        fresh = self.review()
        self.assertNotEqual(old["preparation"]["attempt_id"], fresh["preparation"]["attempt_id"])
        self.assertNotEqual(old["review_sha256"], fresh["review_sha256"])
        self.assertEqual(evidence, (self.directory / "integration-attempts" / old["preparation"]["attempt_id"] / "integration-review.json").read_bytes())
        self.assertEqual(len(self.policy.allowed_tests), len(fresh["review"]["tests"]))
        self.assertTrue(all(t["returncode"] == 0 and not t["timed_out"] for t in fresh["review"]["tests"]))
        self.close(); self.open()
        run = self.current_run()
        self.action("publish", {"run_id":run["run_id"], "revision":run["revision"], "fingerprint":fresh["review_sha256"]}, failed="explicit claim")
        self.action("claim_run")
        run = self.current_run()
        self.action("publish", {"run_id":run["run_id"], "revision":run["revision"], "fingerprint":old["review_sha256"]}, failed="current independent")
        self.unchanged()
        self.action("publish", {"run_id":run["run_id"], "revision":run["revision"], "fingerprint":fresh["review_sha256"]})
        self.assertEqual("published", self.current_run()["review"]["status"])
        self.assertEqual(self.before["head"], self.source.head)
        self.assertEqual("consumed", self.state()["plans"][0]["status"])
        self.assertEqual(0, self.state()["requests_used"])
        self.action("cleanup")

    def test_alive_test_preserves_worktree_until_owned_process_has_stopped(self):
        self.close()
        self.root = self.root / "live"
        self.repo = create_fixture(self.root / "repository")
        self.source = RepositoryGateway(self.repo)
        self.before = self.source.snapshot()
        command = ("{python}", "-c", "from pathlib import Path; import calculator,time; "
                   "time.sleep(30) if Path.cwd().name=='integration-verification' else None; "
                   "raise SystemExit(calculator.add(2,3)!=5)")
        self.policy = Policy(("calculator.py",), (command,), readonly_paths=("tests/test_calculator.py",))
        self.open(); self.candidate(); self.crash("test_running", live=True)
        identity = self.review()["test_inflight"]
        child = psutil.Process(identity["pid"])
        try:
            self.assertEqual(identity, process_identity(child.pid))
            self.assertEqual(identity, self.current_run()["review"]["test_inflight"])
            self.action("recover", failed="still alive")
            self.action("cleanup", failed="preparation")
            self.assertTrue((self.directory / "integration-verification").exists())
            self.unchanged()
        finally:
            if process_identity(child.pid) == identity:
                child.terminate(); child.wait(timeout=10)
        self.action("recover"); self.action("cleanup")

    def test_unknown_test_start_is_retained_after_restart(self):
        self.candidate(); self.crash("test_starting")
        self.action("recover", failed="manual process verification")
        self.action("cleanup", failed="preparation")
        self.assertTrue((self.directory / "integration-verification").exists())
        self.unchanged()
        # This injector exited before Popen; fixture-only restoration is not a UI permission.
        displayed = self.current_run()
        stale = {'run_id': displayed['run_id'], 'revision': displayed['revision']}
        review = self.review(); review["operation_stage"] = "verification_ready"
        save_review(self.directory, review)
        self.action("recover", stale, failed="evidence changed after review")
        self.action("recover"); self.action("cleanup")

    def test_empty_directory_is_preserved_and_cleanup_required_can_be_recovered(self):
        self.candidate(); self.crash("after_add")
        extra = self.directory / "integration-verification" / "human-empty"
        extra.mkdir()
        self.action("recover", failed="unrecorded directories")
        self.assertTrue(extra.is_dir())
        self.assertEqual("cleanup_required", self.current_run()["review"]["status"])
        self.close(); self.open(); self.action("claim_run")
        self.action("recover", failed="unrecorded directories")
        extra.rmdir()  # Remove only the empty directory authored by this test.
        self.action("recover"); self.unchanged(); self.action("cleanup")


if __name__ == "__main__":
    unittest.main()
