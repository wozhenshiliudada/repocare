"""Test-only crash injector for fixed, self-authored Git/UI fixtures. No live model."""
import argparse
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer import repair_recovery as rr
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import Policy
from repo_maintainer.provider import MockProvider
from repo_maintainer.workflow import repair

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
parser.add_argument("--phase", default="candidate_checkpoint")
parser.add_argument("--ui", action="store_true")
parser.add_argument("--live-test", action="store_true")
parser.add_argument("--pi-mock-url")
args = parser.parse_args()
root = args.directory.resolve()
command = ("{python}", "-c", "import time,unittest;time.sleep(4);r=unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('tests'));raise SystemExit(not r.wasSuccessful())") if args.live_test else DEMO_TEST
policy = Policy(("calculator.py",), (command,), readonly_paths=("tests/test_calculator.py",))
if args.phase in {"after_remove", "after_branch_delete", "before_branch_delete"}:
    directory = next(p.parent for p in (root / "runs").glob("*/run.json"))
    original = RepositoryGateway.command
    def crash_command(repo, *values, **kw):
        if values[:2] == ("update-ref", "-d") and args.phase == "before_branch_delete":
            os._exit(95)
        result = original(repo, *values, **kw)
        if (values[:2] == ("worktree", "remove") and args.phase == "after_remove") or (values[:2] == ("update-ref", "-d") and args.phase == "after_branch_delete"):
            os._exit(95)
        return result
    preview = rr.inspect_repair(directory)
    with patch.object(RepositoryGateway, "command", crash_command):
        rr.recover_repair(directory, preview["revision"], "crash-fixture")
else:
    original = rr.save_execution
    checkpoints = 0
    def crash_save(directory, data):
        global checkpoints
        original(directory, data)
        phase, inflight = data["phase"], data.get("inflight") or {}
        if phase == "workspace_ready" and not inflight:
            checkpoints += 1
        matched = ((args.phase == "workspace_ready" and phase == "workspace_ready" and checkpoints == 1)
            or (args.phase == "candidate_checkpoint" and phase == "workspace_ready" and checkpoints == 2)
            or (args.phase == "test_starting" and inflight.get("status") == "starting")
            or (args.phase == "baseline_pid" and inflight.get("kind") == "baseline_test" and inflight.get("status") == "running")
            or (args.phase == "baseline_result" and phase == "test_finished" and data["process_facts"][-1]["kind"] == "baseline_test")
            or (args.phase == "candidate_result" and phase == "test_finished" and data["process_facts"][-1]["kind"] == "candidate_test")
            or (args.phase in {"editing", "workspace_add_intent", "planning"} and phase == args.phase))
        if matched:
            os._exit(94)
    with patch.object(rr, "save_execution", crash_save):
        if args.ui:
            from repo_maintainer.local_ui import UISession
            from repo_maintainer.pi_provider import PiProvider
            provider = PiProvider(policy.allowed_paths, args.pi_mock_url, policy.readonly_paths, 2) if args.pi_mock_url else MockProvider()
            session = UISession(root / "repository", policy, provider, root / "runs")
            plan = session.state()["plans"][-1]
            session._operate("claim_plan", {"plan_id":plan["id"], "revision":plan["revision"]})
            session.submit("d"*32, "decision", {"plan_id":plan["id"], "fingerprint":plan["fingerprint"], "decision":"approve"})
            session.close()
        else:
            repo = root / "repository"
            if not repo.exists():
                create_fixture(repo)
            repair(repo, "addition bug", MockProvider(), policy, "auto", root / "runs")
