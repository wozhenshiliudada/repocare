"""Reproducible workflow evaluation; fixture patches are an oracle, not an LLM score."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .demo import DEMO_TEST, create_fixture
from .git import RepositoryGateway, git_env
from .models import Edit, Plan, Policy, SafetyError, digest
from .store import redact
from .workflow import cleanup_run, repair


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    kind: str
    issue: str
    policy: Policy
    expected_edits: dict[str, str]
    source: Path


def cases() -> list[EvaluationCase]:
    root = Path(__file__).resolve().parents[2] / "examples"
    result = [EvaluationCase("addition", "arithmetic_logic", "Addition must preserve positive and negative sums",
                            Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",)),
                            {"calculator.py": "def add(a, b):\n    return a + b\n"}, root / "buggy_repo")]
    for manifest in sorted((root / "evaluation-cases").glob("*/case.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        result.append(EvaluationCase(data["id"], data["kind"], data["issue"],
                                    Policy(tuple(data["allowed_paths"]), (DEMO_TEST,),
                                           readonly_paths=tuple(data["readonly_paths"])),
                                    data["expected_edits"], manifest.parent / "source"))
    return result


def create_case(case: EvaluationCase, destination: Path) -> Path:
    if destination.exists():
        raise SafetyError("Evaluation repository already exists")
    if case.id == "addition":
        return create_fixture(destination)
    shutil.copytree(case.source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (destination / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main", str(destination)], capture_output=True,
                   check=True, env=git_env())
    repository = RepositoryGateway(destination)
    repository.command("add", ".")
    repository.command("-c", "user.name=Evaluation Fixture", "-c", "user.email=fixture@example.invalid",
                       "commit", "-m", "fixture: " + case.id)
    return destination


class OracleProvider:
    """Known fixture fixes for verifying the workflow; never used as a model benchmark."""
    def __init__(self, case: EvaluationCase):
        self.case = case

    def plan(self, repository, issue, tests):
        edits = tuple(Edit(path, digest(repository.read(path)), content)
                      for path, content in self.case.expected_edits.items())
        return Plan(issue, repository.head, "Known fixture repair", "fixture only", edits,
                    tests, "fixture-oracle")


def run_evaluation(directory: Path, provider_factory=None, max_attempts: int = 1) -> dict:
    directory = directory.resolve()
    if directory.exists():
        raise SafetyError("Evaluation directory must be new")
    directory.mkdir(parents=True)
    summaries = []
    for case in cases():
        base = directory / case.id
        repo = create_case(case, base / "repository")
        provider = provider_factory(case) if provider_factory else OracleProvider(case)
        record = repair(repo, case.issue, provider, case.policy, "auto", base / "runs", max_attempts=max_attempts)
        summary = {"case": case.id, "kind": case.kind, "run_id": record["run_id"],
                   "status": record["status"], "rounds": len(record["rounds"]),
                   "baseline_exit": [t["returncode"] for t in record["baseline_tests"]],
                   "candidate_exit": [t["returncode"] for t in record["candidate_tests"]],
                   "source_unchanged": record["source_unchanged"],
                   "passed": record["status"] == "candidate_ready" and record["source_unchanged"]}
        if record["status"] == "candidate_ready":
            cleaned = cleanup_run(base / "runs" / record["run_id"])
            summary.update(cleanup_status=cleaned["status"], passed=summary["passed"] and cleaned["source_unchanged"])
        summaries.append(summary)
    result = {"kind": "workflow-fixture-evaluation" if provider_factory is None else "provider-evaluation",
              "model_accuracy_claim": False, "total": len(summaries),
              "passed": sum(item["passed"] for item in summaries), "cases": summaries}
    (directory / "summary.json").write_text(redact(json.dumps(result, indent=2)) + "\n", encoding="utf-8")
    lines = ["# Reproducible evaluation", "", "Fixture-oracle results measure the workflow, not LLM capability.", "",
             "| Case | Type | Baseline exits | Candidate exits | Source unchanged | Passed |", "|---|---|---|---|---|---|"]
    for item in summaries:
        lines.append(f"| {item['case']} | {item['kind']} | {item['baseline_exit']} | {item['candidate_exit']} | "
                     f"{item['source_unchanged']} | {item['passed']} |")
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result
