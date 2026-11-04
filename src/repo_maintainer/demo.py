from __future__ import annotations

import json
import shutil
from pathlib import Path

from .git import RepositoryGateway
from .models import Policy, SafetyError
from .provider import MockProvider
from .workflow import cleanup_run, repair


DEMO_TEST = ("{python}", "-m", "unittest", "discover", "-s", "tests", "-v")


def create_fixture(destination: Path) -> Path:
    if destination.exists():
        raise SafetyError("Demo destination already exists; choose a new directory")
    source = Path(__file__).resolve().parents[2] / "examples" / "buggy_repo"
    if Path(__file__).resolve().parent.parent.name != "src":
        source = Path(__file__).resolve().parent / "demo_assets" / "buggy_repo"
    if not source.is_dir():
        raise SafetyError("Demo resources are missing; reinstall the complete package")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # RepositoryGateway requires an existing Git repository; initialization is fixture-only.
    import subprocess
    from .git import git_env

    subprocess.run(["git", "init", "-b", "main", str(destination)],
                   check=True, capture_output=True, env=git_env())
    repo = RepositoryGateway(destination)
    repo.command("add", ".")
    repo.command("-c", "user.name=Maintenance Demo", "-c", "user.email=demo@example.invalid",
                 "commit", "-m", "fixture: reproducible addition bug")
    return destination


def run_demo(directory: Path) -> dict:
    directory = directory.resolve()
    if directory.exists():
        raise SafetyError("Demo directory exists; choose a new directory to preserve earlier evidence")
    directory.mkdir(parents=True)
    repo = create_fixture(directory / "repository")
    policy = Policy(("calculator.py",), (DEMO_TEST,))
    cases = []
    scenarios = [("approval_reject", "fix", "approval", "reject", "rejected"),
                 ("approval_accept", "fix", "approval", "approve", "candidate_ready"),
                 ("auto_invalid_patch", "broken", "auto", None, "rolled_back"),
                 ("auto_regression", "regression", "auto", None, "rolled_back")]
    for name, scenario, mode, decision, expected in scenarios:
        record = repair(repo, "add(2, 3) must equal 5; preserve negative inputs",
                        MockProvider(scenario), policy, mode, directory / "runs",
                        approval=(lambda plan, choice=decision: choice) if decision else None)
        passed = record["status"] == expected and record["source_unchanged"]
        cases.append({"case": name, "run_id": record["run_id"], "status": record["status"],
                      "source_unchanged": record["source_unchanged"], "passed": passed})
        if record["status"] == "candidate_ready":
            cleaned = cleanup_run(directory / "runs" / record["run_id"])
            cases[-1]["cleanup_status"] = cleaned["status"]
            cases[-1]["passed"] = passed and cleaned["status"] == "discarded"
    summary = {"cases": cases, "passed": all(case["passed"] for case in cases),
               "provider": "deterministic mock (no LLM calls)", "repository": str(repo)}
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
