"""Exit a real registered UI prepare job on a self-authored fixture only."""
import argparse
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer import integration
from repo_maintainer.demo import DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession, json_digest
from repo_maintainer.models import Policy
from repo_maintainer.provider import MockProvider
from repo_maintainer.pi_provider import PiProvider

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
parser.add_argument("stage")
parser.add_argument("--pi-mock-url")
parser.add_argument("--live-test", action="store_true")
args = parser.parse_args()
root = args.directory.resolve()
command = ("{python}", "-c", "from pathlib import Path; import calculator,time; "
           "time.sleep(30) if Path.cwd().name=='integration-verification' else None; "
           "raise SystemExit(calculator.add(2,3)!=5)") if args.live_test else DEMO_TEST
policy = Policy(("calculator.py",), (command,), readonly_paths=("tests/test_calculator.py",))
provider = PiProvider(policy.allowed_paths, args.pi_mock_url, policy.readonly_paths, 2) if args.pi_mock_url else MockProvider()
session = UISession(root / "repository", policy, provider, root / "runs")
run = session.state()["runs"][-1]
if run["status"] != "candidate_ready" or not run["repair_finished"]:
    raise RuntimeError("A registered, completed fixture candidate is required")
payload = {"run_id": run["run_id"], "revision": json_digest(session._run_view(run["run_id"]))}
session._operate("claim_run", payload)
original_command, original_save = RepositoryGateway.command, integration.save_review

def crash_command(repo, *values, **kw):
    adding = values[:3] == ("worktree", "add", "--detach")
    removing = values[:2] == ("worktree", "remove")
    if adding and args.stage == "before_add":
        os._exit(91)
    result = original_command(repo, *values, **kw)
    if (adding and args.stage == "after_add") or (removing and args.stage == "after_remove"):
        os._exit(91)
    return result

def crash_save(directory, review):
    original_save(directory, review)
    if review.get("operation_stage") == args.stage:
        os._exit(91)

RepositoryGateway.command = crash_command
integration.save_review = crash_save
session.submit(run["run_id"], "prepare", {**payload, "retry_aborted": False})
deadline = time.monotonic() + 40
while time.monotonic() < deadline:
    job = session.jobs[run["run_id"]]
    if job["status"] != "running":
        raise RuntimeError("Crash point not reached: " + str(job))
    time.sleep(.02)
raise RuntimeError("Crash point deadline exceeded")
