import json
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from starlette.testclient import TestClient
from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import MAX_BODY, MAX_JOBS, UISession, create_app, json_digest
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.provider import MockProvider
from repo_maintainer.workflow import cleanup_run


class LocalUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = create_fixture(self.root / "repository")
        self.source = RepositoryGateway(self.repo)
        self.before = self.source.snapshot()
        self.policy = Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",))
        self.session = UISession(self.repo, self.policy, MockProvider(), self.root / "runs")
        self.addCleanup(self.finish)
        self.client = TestClient(create_app(self.session, 12345), base_url="http://127.0.0.1:12345")
        self.headers = {"Authorization": "Bearer " + self.session.token}

    def finish(self):
        self.session.close()
        for identity in self.session.runs:
            if (self.root / "runs" / identity / "run.json").exists():
                cleanup_run(self.root / "runs" / identity)
        self.client.close()

    def state(self):
        result = self.client.get("/api/state", headers=self.headers)
        self.assertEqual(200, result.status_code)
        return result.json()

    def submit(self, action, data, request_id=None):
        result = self.client.post("/api/actions/" + action,
                                  json={**data, "request_id": request_id or uuid.uuid4().hex}, headers=self.headers)
        self.assertEqual(202, result.status_code, result.text)
        return result.json()["id"]

    def wait(self, identity):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = next(j for j in self.state()["jobs"] if j["id"] == identity)
            if job["status"] != "running":
                return job
            time.sleep(.03)
        self.fail("UI operation did not finish")

    def operation(self, action, data):
        job = self.wait(self.submit(action, data))
        self.assertEqual("finished", job["status"], job)
        return job["result"]

    def plan(self):
        self.operation("plan", {"issue": "addition bug"})
        return self.state()["plans"][-1]

    def candidate(self):
        plan = self.plan()
        self.operation("decision", {"plan_id": plan["id"], "decision": "approve", "fingerprint": plan["fingerprint"]})
        run = self.state()["runs"][-1]
        self.assertEqual("candidate_ready", run["status"], run)
        return run

    def args(self, run):
        return {"run_id": run["run_id"], "revision": run["revision"]}

    def test_unauthenticated_foreign_host_origin_and_duplicate_headers_are_blocked(self):
        self.assertEqual(401, self.client.get("/api/state").status_code)
        for headers in ({**self.headers, "Host": "evil.test:12345"},
                        {**self.headers, "Origin": "https://evil.test"},
                        {**self.headers, "Sec-Fetch-Site": "cross-site"},
                        [("Authorization", "Bearer " + self.session.token), ("Host", "127.0.0.1:12345"),
                         ("Host", "evil.test")],
                        [("Authorization", "Bearer " + self.session.token), ("Authorization", "other")]):
            self.assertEqual(403, self.client.get("/api/state", headers=headers).status_code)
        self.assertEqual([], self.state()["jobs"])

    def test_static_assets_security_headers_no_secrets_or_unsafe_routes(self):
        for path in ("/", "/app.js", "/session-access.js", "/style.css", "/diff2html.min.js", "/diff2html.min.css"):
            response = self.client.get(path)
            self.assertEqual(200, response.status_code, path)
            self.assertNotIn(self.session.token, response.text)
            self.assertEqual("no-store", response.headers["cache-control"])
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
            self.assertNotIn("access-control-allow-origin", response.headers)
        for path in ("/.env", "/api/merge", "/api/push", "/workspaces", "/style.css/../secret"):
            self.assertEqual(404, self.client.get(path, headers=self.headers).status_code)
        self.assertEqual(405, self.client.get("/api/actions/plan", headers=self.headers).status_code)

    def test_post_requires_json_bounded_strict_fields_and_full_hashes(self):
        response = self.client.post("/api/actions/plan", content="{}", headers=self.headers)
        self.assertEqual(415, response.status_code)
        headers = {**self.headers, "Content-Type": "application/json"}
        for content in ('[]', '{"issue":"x","issue":"y","request_id":"'+uuid.uuid4().hex+'"}',
                        '{"issue":NaN}', '{"issue":"x","repo":"evil"}', '{}'):
            self.assertEqual(409, self.client.post("/api/actions/plan", content=content, headers=headers).status_code)
        self.assertEqual(413, self.client.post("/api/actions/plan", content=' '* (MAX_BODY+1), headers=headers).status_code)
        self.assertEqual(409, self.client.post("/api/actions/decision", json={"request_id":uuid.uuid4().hex,
                         "plan_id":uuid.uuid4().hex,"decision":"approve","fingerprint":"short"}, headers=self.headers).status_code)
        self.assertEqual([], self.state()["jobs"])

    def test_planning_leaves_source_untouched_and_reject_creates_no_workspace(self):
        plan = self.plan()
        self.assertIn("a + b", plan["diff"])
        self.assertEqual(self.before, self.source.snapshot())
        self.assertEqual([], self.state()["runs"])
        result = self.operation("decision", {"plan_id":plan["id"],"fingerprint":plan["fingerprint"],"decision":"reject"})
        self.assertEqual("rejected", result["workflow_status"])
        run = self.state()["runs"][0]
        self.assertEqual([], run["baseline_tests"])
        self.assertFalse((self.root/"runs"/run["run_id"]/"workspace").exists())
        self.assertEqual(self.before, self.source.snapshot())

    def test_changed_source_invalidates_approval_without_stashing_user_edits(self):
        plan = self.plan()
        path = self.repo/"calculator.py"
        path.write_text("# user edit\n", encoding="utf-8")
        job = self.wait(self.submit("decision", {"plan_id":plan["id"],"fingerprint":plan["fingerprint"],"decision":"approve"}))
        self.assertEqual("failed", job["status"])
        self.assertIn("Source changed", job["error"])
        self.assertEqual("# user edit\n", path.read_text())
        self.assertEqual([], self.state()["runs"])

    def test_tampered_saved_plan_file_cannot_be_approved(self):
        plan = self.plan()
        path = self.session.plans[plan["id"]]["path"]
        data = json.loads(path.read_text());data["plan"]["diagnosis"] = "tampered"
        path.write_text(json.dumps(data),encoding="utf-8")
        job = self.wait(self.submit("decision", {"plan_id":plan["id"],"fingerprint":plan["fingerprint"],"decision":"approve"}))
        self.assertEqual("failed",job["status"])
        self.assertEqual([],self.state()["runs"])

    def test_stale_or_already_consumed_plan_is_not_executed_again(self):
        plan=self.plan()
        job=self.wait(self.submit("decision",{"plan_id":plan["id"],"fingerprint":"0"*64,"decision":"approve"}))
        self.assertEqual("failed",job["status"])
        args={"plan_id":plan["id"],"fingerprint":plan["fingerprint"],"decision":"reject"}
        self.operation("decision",args)
        self.assertEqual("failed",self.wait(self.submit("decision",args))["status"])
        self.assertEqual(1,len(self.state()["runs"]))

    def test_plan_change_between_precheck_and_workflow_is_refused_before_tests(self):
        plan=self.plan()
        from repo_maintainer.pi_provider import SavedPlanProvider, decode_plan
        original=SavedPlanProvider.plan
        def raced(provider,*args):
            path=self.session.plans[plan["id"]]["path"]
            data=json.loads(path.read_text(encoding="utf-8"))
            data["plan"]["diagnosis"]="different valid plan after earlier read"
            data["plan_sha256"]=decode_plan(data).fingerprint
            path.write_text(json.dumps(data),encoding="utf-8")
            return original(provider,*args)
        with patch.object(SavedPlanProvider,"plan",raced):
            result=self.operation("decision",{"plan_id":plan["id"],"fingerprint":plan["fingerprint"],"decision":"approve"})
        self.assertEqual("failed",result["workflow_status"])
        run=self.state()["runs"][-1]
        self.assertEqual([],run["baseline_tests"])
        self.assertIn("approval refused",run["error"])
        self.assertEqual(self.before,self.source.snapshot())

    def test_duplicate_request_replays_job_and_changed_payload_is_refused(self):
        identity=self.submit("plan",{"issue":"addition"})
        self.wait(identity)
        self.assertEqual(identity,self.submit("plan",{"issue":"addition"},identity))
        result=self.client.post("/api/actions/plan",json={"issue":"changed","request_id":identity},headers=self.headers)
        self.assertEqual(409,result.status_code)
        self.assertEqual(1,len(self.state()["plans"]))

    def test_busy_session_and_bounded_job_history_prevent_parallel_or_unbounded_work(self):
        entered, release=threading.Event(),threading.Event()
        original=self.session._operate
        def slow(action,payload):
            entered.set();release.wait(5);return original(action,payload)
        with patch.object(self.session,"_operate",side_effect=slow):
            identity=self.submit("plan",{"issue":"addition"})
            self.assertTrue(entered.wait(5))
            result=self.client.post("/api/actions/plan",json={"issue":"other","request_id":uuid.uuid4().hex},headers=self.headers)
            self.assertEqual(409,result.status_code)
            release.set();self.wait(identity)
        with patch("repo_maintainer.local_ui.MAX_JOBS",1):
            with self.assertRaises(SafetyError):
                self.session.submit(uuid.uuid4().hex,"plan",{"issue":"other"})

    def test_auto_requires_startup_opt_in_and_failed_repair_rolls_back(self):
        job=self.wait(self.submit("auto",{"issue":"addition"}))
        self.assertEqual("failed",job["status"])
        self.session.allow_auto=True
        self.session.provider=MockProvider("broken")
        result=self.operation("auto",{"issue":"addition"})
        self.assertEqual("rolled_back",result["workflow_status"])
        self.assertEqual(self.before,self.source.snapshot())
        run=self.state()["runs"][-1]
        self.assertTrue(run["source_unchanged"])
        self.assertFalse((self.root/"runs"/run["run_id"]/"workspace").exists())
        self.assertNotEqual(0,run["candidate_tests"][0]["returncode"])

    def test_independent_publication_approval_and_cleanup_preserve_source_and_published_commit(self):
        run=self.candidate()
        self.operation("prepare",{**self.args(run),"retry_aborted":False})
        run=self.state()["runs"][-1];review=run["review"]
        self.assertEqual("prepared",review["status"])
        self.assertEqual([],self.source.command("for-each-ref","--format=%(refname)","refs/heads/integration").splitlines())
        args={**self.args(run),"fingerprint":run["plan_sha256"]}
        self.assertEqual("failed",self.wait(self.submit("publish",args))["status"])
        run=self.state()["runs"][-1]
        self.operation("reject_publish",{**self.args(run),"fingerprint":review["review_sha256"]})
        run=self.state()["runs"][-1]
        self.assertEqual("rejected",run["review"]["status"])
        result=self.operation("publish",{**self.args(run),"fingerprint":review["review_sha256"]})
        self.assertEqual("published",result["workflow_status"])
        run=self.state()["runs"][-1]
        target=run["integration_branch"];commit=run["integration_commit"]
        self.assertEqual(self.before["head"],self.source.head)
        self.operation("cleanup",self.args(run))
        self.assertEqual(commit,self.source.command("rev-parse",target).decode().strip())
        self.assertEqual(self.before,self.source.snapshot(exclude_branch=target))

    def test_evidence_change_invalidates_cleanup_revision(self):
        run=self.candidate()
        path=self.root/"runs"/run["run_id"]/"run.json"
        data=json.loads(path.read_text());data["issue"]="changed after display"
        path.write_text(json.dumps(data),encoding="utf-8")
        job=self.wait(self.submit("cleanup",self.args(run)))
        self.assertEqual("failed",job["status"])
        self.assertIn("changed after review",job["error"])
        self.assertTrue((path.parent/"workspace").exists())

    def test_foreign_repository_and_changed_policy_record_cannot_redirect_actions(self):
        run=self.candidate()
        path=self.root/"runs"/run["run_id"]/"run.json";original=path.read_text()
        for field,value in (("source_repo",str(self.root/"other")),("policy",{"allowed_paths":["other"]})):
            data=json.loads(original);data[field]=value;path.write_text(json.dumps(data),encoding="utf-8")
            job=self.wait(self.submit("cleanup",self.args(run)))
            self.assertEqual("failed",job["status"])
            self.assertTrue((path.parent/"workspace").exists())
        path.write_text(original,encoding="utf-8")

    def test_unregistered_path_identity_and_runs_inside_source_are_refused(self):
        with self.assertRaises(SafetyError):self.session._run_dir("../escape")
        with self.assertRaises(SafetyError):self.session._run_dir(uuid.uuid4().hex)
        with self.assertRaises(SafetyError):UISession(self.repo,self.policy,MockProvider(),self.repo/"runs")

    def test_recognized_secret_in_plan_cannot_be_displayed_or_approved(self):
        original=self.session.provider.plan
        def secret_plan(*args):
            from dataclasses import replace
            return replace(original(*args),diagnosis="sk-abcdefghijklmnopqrstuvw")
        with patch.object(self.session.provider,"plan",side_effect=secret_plan):
            job=self.wait(self.submit("plan",{"issue":"addition"}))
        self.assertEqual("failed",job["status"])
        self.assertNotIn("sk-abcdefghijklmnopqrstuvw",json.dumps(self.state()))
        self.assertEqual([],self.state()["plans"])

    def restart(self):
        old_token = self.session.token
        self.session.close()
        self.client.close()
        self.session = UISession(self.repo, self.policy, MockProvider(), self.root / "runs")
        self.client = TestClient(create_app(self.session, 12345), base_url="http://127.0.0.1:12345")
        self.headers = {"Authorization": "Bearer " + self.session.token}
        return old_token

    def test_restart_preserves_plan_and_jobs_but_requires_new_claim_and_approval(self):
        plan = self.plan()
        old_token = self.restart()
        self.assertNotEqual(old_token, self.session.token)
        self.assertEqual(401, self.client.get("/api/state", headers={"Authorization": "Bearer "+old_token}).status_code)
        restored = self.state()["plans"][0]
        self.assertFalse(restored["claimed"])
        self.assertEqual(plan["fingerprint"], restored["fingerprint"])
        self.assertEqual(0, restored["planning_evidence"]["requests"])
        self.assertEqual(1, len(self.state()["jobs"]))
        args = {"plan_id": plan["id"], "fingerprint": plan["fingerprint"], "decision": "approve"}
        self.assertEqual("failed", self.wait(self.submit("decision", args))["status"])
        self.assertEqual([], self.state()["runs"])
        self.assertEqual("failed", self.wait(self.submit("claim_plan", {"plan_id":plan["id"], "revision":plan["revision"]}))["status"])
        self.operation("claim_plan", {"plan_id":restored["id"], "revision":restored["revision"]})
        self.assertEqual([], self.state()["runs"], "Claim is read-only")
        result = self.operation("decision", args)
        self.assertEqual("candidate_ready", result["workflow_status"])
        record = json.loads((self.root/"runs"/result["run_id"]/"run.json").read_text())
        self.assertEqual(plan["id"], record["planning_origin"]["plan_id"])
        self.assertEqual(0, record["provider_evidence"]["requests"])
        self.assertEqual(self.before, self.source.snapshot(exclude_branch=record["branch"]))

    def test_consumed_plan_cannot_be_reexecuted_after_restart_and_request_id_stays_idempotent(self):
        plan = self.plan()
        args = {"plan_id":plan["id"], "fingerprint":plan["fingerprint"], "decision":"reject"}
        request_id = self.submit("decision", args)
        self.wait(request_id)
        self.restart()
        self.assertEqual(request_id, self.submit("decision", args, request_id))
        self.assertEqual("failed", self.wait(self.submit("decision", args))["status"])
        self.assertEqual("failed", self.wait(self.submit("claim_plan", {"plan_id":plan["id"],"revision":self.state()["plans"][0]["revision"]}))["status"])
        self.assertEqual(1, len(self.state()["runs"]))

    def test_historical_run_is_readonly_until_claim_then_fresh_revision_applies(self):
        run = self.candidate()
        self.restart()
        restored = self.state()["runs"][0]
        self.assertFalse(restored["claimed"])
        self.assertEqual("failed", self.wait(self.submit("prepare", {**self.args(restored), "retry_aborted":False}))["status"])
        self.assertFalse((self.root/"runs"/run["run_id"]/"integration-review.json").exists())
        self.operation("claim_run", self.args(restored))
        self.assertEqual("candidate_ready", self.state()["runs"][0]["status"])
        self.operation("prepare", {**self.args(self.state()["runs"][0]), "retry_aborted":False})
        self.assertEqual("prepared", self.state()["runs"][0]["review"]["status"])

    def test_tampered_historical_plan_and_changed_clean_source_cannot_be_claimed(self):
        plan = self.plan()
        self.restart()
        args = {"plan_id":plan["id"], "revision":self.state()["plans"][0]["revision"]}
        path = self.session.plans[plan["id"]]["path"]
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        self.assertEqual("failed", self.wait(self.submit("claim_plan", args))["status"])
        path.write_bytes(original)
        (self.repo/"calculator.py").write_text("# concurrent user commit\n", encoding="utf-8")
        self.source.command("add", "calculator.py")
        self.source.command("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "user change")
        self.assertEqual("failed", self.wait(self.submit("claim_plan", args))["status"])
        self.assertEqual([], self.state()["runs"])
        self.assertIn("concurrent", (self.repo/"calculator.py").read_text())

    def test_second_owner_and_changed_policy_fail_without_overwriting_journal(self):
        self.plan()
        with self.assertRaisesRegex(SafetyError, "owns"):
            UISession(self.repo, self.policy, MockProvider(), self.root/"runs")
        self.session.close()
        changed = Policy(("calculator.py",), (DEMO_TEST,), timeout_seconds=11)
        with self.assertRaisesRegex(SafetyError, "changed"):
            UISession(self.repo, changed, MockProvider(), self.root/"runs")
        self.restart()
        self.assertEqual(1, len(self.state()["plans"]))

    def test_interrupted_repair_without_final_evidence_cannot_be_claimed_for_cleanup(self):
        run = self.candidate()
        path = self.root/"runs"/run["run_id"]/"run.json"
        original = path.read_text()
        data = json.loads(original)
        data.pop("finished_at")
        data["status"] = "candidate_testing"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.restart()
        restored = self.state()["runs"][0]
        job = self.wait(self.submit("claim_run", self.args(restored)))
        self.assertEqual("failed", job["status"])
        self.assertIn("process evidence", job["error"])
        self.assertTrue((path.parent/"workspace").exists())
        path.write_text(original, encoding="utf-8")

    def test_process_exit_after_consumption_preserves_intent_without_git_replay(self):
        import subprocess
        plan = self.plan()
        self.session.close()
        script = """
import os,sys
from pathlib import Path
from unittest.mock import patch
from repo_maintainer.local_ui import UISession
from repo_maintainer.provider import MockProvider
from repo_maintainer.models import Policy
from repo_maintainer.demo import DEMO_TEST
s=UISession(Path(sys.argv[1]),Policy(('calculator.py',),(DEMO_TEST,),readonly_paths=('tests/test_calculator.py',)),MockProvider(),Path(sys.argv[2]))
p=s.state()['plans'][0]
s._operate('claim_plan',{'plan_id':p['id'],'revision':p['revision']})
with patch('repo_maintainer.local_ui.repair',side_effect=lambda *a,**kw:os._exit(23)):
 s.submit('a'*32,'decision',{'plan_id':p['id'],'fingerprint':p['fingerprint'],'decision':'approve'})
 s.close()
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.repo), str(self.root/"runs")],
                                cwd=Path(__file__).resolve().parents[1], timeout=30)
        self.assertEqual(23, result.returncode)
        self.restart()
        state = self.state()
        self.assertEqual("consumed", state["plans"][0]["status"])
        interrupted = next(j for j in state["jobs"] if j["id"] == "a"*32)
        self.assertEqual("interrupted", interrupted["status"])
        self.assertEqual("evidence_invalid", state["runs"][0]["status"])
        self.assertEqual(self.before, self.source.snapshot())
        self.assertEqual("failed", self.wait(self.submit("decision", {"plan_id":plan["id"],"fingerprint":plan["fingerprint"],"decision":"approve"}))["status"])


    def test_equivalent_integer_and_float_duration_reopens_and_claims_real_run(self):
        run = self.candidate()  # Original in-memory policy stores 30; JSON CLI stores 30.0.
        self.session.close()
        self.client.close()
        policy = Policy(self.policy.allowed_paths, self.policy.allowed_tests, 30.0, self.policy.readonly_paths)
        self.session = UISession(self.repo, policy, MockProvider(), self.root / "runs")
        self.client = TestClient(create_app(self.session,12345),base_url="http://127.0.0.1:12345")
        self.headers = {"Authorization":"Bearer " + self.session.token}
        restored = self.state()["runs"][0]
        self.assertEqual("candidate_ready", restored["status"])
        self.assertTrue(restored["repair_finished"])
        self.operation("claim_run", self.args(restored))
        path = self.root / "runs" / run["run_id"] / "run.json"
        original = path.read_bytes()
        try:
            data = json.loads(original)
            data["policy"]["timeout_seconds"] = True
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(SafetyError):
                self.session._run_dir(run["run_id"])
        finally:
            path.write_bytes(original)
        self.assertEqual(self.before,self.source.snapshot(exclude_branch=run["branch"]))


if __name__ == "__main__":unittest.main()
