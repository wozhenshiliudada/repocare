"""Controlled workflow ablation on newly created fixtures; never takes a user repo.

First patches and grading tests are matched. Scripted answers and Pi loopback
answers are synthetic, not live-model accuracy. Grading is outside each arm.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import re
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

from .comparison_fixtures import cases, ComparisonCase
from .dashboard import export_dashboard
from .git import RepositoryGateway, git_env
from .integration import fingerprint, prepare_integration, integrate
from .models import Edit, Plan, SafetyError, digest
from .pi_provider import PiProvider
from .recovery import unlinked
from .runner import run_test
from .store import now, redact
from .workflow import cleanup_run, repair

ARMS = ("patch_only", "test_gate", "guarded_one", "guarded_two")


class ScriptProvider:
    def __init__(self, case):
        self.case, self.steps = case, 0
        self.evidence = {"transport": "fixture-script", "requests": 0, "model_cost_verified": False}

    def plan(self, repository, issue, tests):
        return self.propose(repository, issue, tests, False)

    def revise(self, repository, issue, tests, feedback):
        if not feedback.get("tests"):
            raise SafetyError("Fixture revision requires real failed-test feedback")
        return self.propose(repository, issue, tests, True)

    def propose(self, repository, issue, tests, revision):
        self.steps += 1
        content = self.case.later if revision and self.case.later is not None else self.case.first
        return Plan(issue, repository.head, "Public scripted fixture response", "synthetic experiment",
                    (Edit("logic.py", digest(repository.read("logic.py")), content),), tests, "fixture-script")


def write_files(case, destination):
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in case.files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def file_snapshot(directory):
    return {file.relative_to(directory).as_posix(): digest(file.read_bytes())
            for file in directory.rglob("*") if file.is_file() and ".git" not in file.relative_to(directory).parts}


def create_repository(case, destination):
    write_files(case, destination)
    subprocess.run(["git", "init", "-b", "main", str(destination)], capture_output=True, check=True, env=git_env())
    repo = RepositoryGateway(destination)
    repo.command("add", ".")
    repo.command("-c", "user.name=Comparison Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-m", "fixture: " + case.id)
    return repo


def grade(case, directory, edits):
    write_files(case, directory)
    for edit in edits:
        if edit.path != "logic.py" or digest((directory / edit.path).read_bytes()) != edit.before_sha256:
            raise SafetyError("Grader patch chain differs from actually applied edits")
        (directory / edit.path).write_bytes(edit.content.encode())
    before = file_snapshot(directory)
    results = [run_test(cmd, directory, case.policy.timeout_seconds) for cmd in case.policy.allowed_tests]
    if before != file_snapshot(directory):
        raise SafetyError("Independent grading modified fixture files")
    return results


def passed(results):
    return bool(results) and all(t["returncode"] == 0 and not t["timed_out"] for t in results)


def _raw_arm(case, arm, source, provider, owner):
    """Private experiment on an internally created disposable source copy only."""
    if source.root != (owner / "repository").resolve() or source.common_dir != source.root / ".git":
        raise SafetyError("Ablation controls only operate on their newly created fixture copies")
    plan = provider.plan(source, case.task, case.policy.allowed_tests)
    case.policy.validate(plan)
    if plan.issue != case.task or plan.base_sha != source.head or plan.tests != case.policy.allowed_tests:
        raise SafetyError("Comparison proposal changed its task/baseline/tests")
    edits = []
    decision = "reject" if case.rejected else "approve"
    tests = []
    if decision == "approve":
        for edit in plan.edits:
            if digest(source.read(edit.path)) != edit.before_sha256:
                raise SafetyError("Comparison patch precondition changed")
        for edit in plan.edits:
            source.safe_file(edit.path).write_bytes(edit.content.encode())
            edits.append(edit)
        if arm == "test_gate":
            tests = [run_test(cmd, source.root, case.policy.timeout_seconds) for cmd in case.policy.allowed_tests]
    accepted = decision == "approve" and (arm == "patch_only" or passed(tests))
    return {"status": "patch_retained" if accepted else "rejected", "accepted": accepted,
            "applied": edits, "plans": [plan], "workflow_tests": tests,
            "authorization": {"decision": decision, "actor": "scripted-comparison-operator",
                              "plan_sha256": plan.fingerprint}, "rounds": 1}


def showcase_publish(run_dir):
    review = prepare_integration(run_dir)
    if review["status"] != "prepared":
        raise SafetyError("Showcase preparation is not ready")
    pending = integrate(run_dir)
    source = RepositoryGateway(Path(json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["source_repo"]))
    ref = "refs/heads/" + review["review"]["target_branch"]
    if source.command("show-ref", "--verify", "--hash", ref, check=False):
        raise SafetyError("Showcase published a branch before independent approval")
    reject = integrate(run_dir, reject=True, actor="scripted-showcase-operator")
    if source.command("show-ref", "--verify", "--hash", ref, check=False):
        raise SafetyError("Showcase rejection published a branch")
    published = integrate(run_dir, review["review_sha256"], actor="scripted-showcase-operator")
    if pending["status"] != "awaiting_approval" or reject["status"] != "rejected" or published["status"] != "published":
        raise SafetyError("Showcase independent approval sequence failed")
    return {"approval_kind": "scripted fixture operator, not human/account authentication",
            "steps": ["prepared", "awaiting_approval", "rejected", "published"],
            "review_sha256": review["review_sha256"], "branch": review["review"]["target_branch"],
            "commit": review["review"]["commit"], "pending_target_exists": False, "rejected_target_exists": False}


def execute_cell(case, arm, base, mock_url, showcase):
    base.mkdir(parents=True)
    source = create_repository(case, base / "repository")
    before = source.snapshot()
    fixture_sha = fingerprint(file_snapshot(source.root))
    baseline = grade(case, base / "grader-before", [])
    if passed(baseline) or any(t["timed_out"] for t in baseline):
        raise SafetyError("Comparison needs a reproducible red baseline")
    provider = PiProvider(case.policy.allowed_paths, mock_url, case.policy.readonly_paths, max_requests=4) if mock_url else ScriptProvider(case)
    started = time.monotonic()
    record = None
    if arm.startswith("guarded_"):
        record = repair(source.root, case.task, provider, case.policy, "approval" if case.rejected else "auto",
                        base / "runs", approval=(lambda plan: "reject") if case.rejected else None,
                        actor="scripted-comparison-operator", max_attempts=2 if arm == "guarded_two" else 1)
        if record["status"] not in {"candidate_ready", "rolled_back", "rejected"}:
            raise SafetyError("Guarded arm infrastructure failure: " + record.get("error", record["status"]))
        plans = [Plan(r["plan"]["issue"], r["plan"]["base_sha"], r["plan"]["diagnosis"], r["plan"]["risk"],
                      tuple(Edit(**e) for e in r["plan"]["edits"]), tuple(tuple(c) for c in r["plan"]["tests"]),
                      r["plan"]["provider"]) for r in record["rounds"]]
        applied = [Edit(**e) for r in record["rounds"] if r.get("diff") for e in r["plan"]["edits"]]
        result = {"status": record["status"], "accepted": record["status"] == "candidate_ready", "applied": applied,
                  "plans": plans, "workflow_tests": record["candidate_tests"], "rounds": len(record["rounds"]),
                  "authorization": record.get("authorization", {}), "run_dir": str(Path(record["workspace"]).parent)}
    else:
        result = _raw_arm(case, arm, source, provider, base)
    duration = round(time.monotonic() - started, 6)
    grading_started = time.monotonic()
    graded = grade(case, base / "grader-after", result["applied"])
    grade_duration = round(time.monotonic() - grading_started, 6)
    first_patch_sha = fingerprint({"edits": [{"path": e.path, "content": e.content} for e in result["plans"][0].edits]})
    source_unchanged = before == source.snapshot(exclude_branch=record["branch"] if record else "")
    failed_residue = bool(result["applied"]) and not passed(graded) and (
        Path(record["workspace"]).exists() if record else not source_unchanged)
    contract_ok = ((source_unchanged if record or case.rejected else True)
                   and (not result["applied"] if case.rejected else True)
                   and (not result["accepted"] or passed(graded) if arm != "patch_only" else True))
    row = {"case": case.id, "arm": arm, "status": result["status"], "accepted": result["accepted"],
           "authorized_repair_task": not case.rejected, "grader_passed": passed(graded), "baseline_tests": baseline,
           "grader_tests": graded, "workflow_tests": result["workflow_tests"], "rounds": result["rounds"],
           "fixture_sha256": fixture_sha, "first_patch_sha256": first_patch_sha, "policy": asdict(case.policy),
           "source_copy_unchanged": source_unchanged, "failed_candidate_retained": failed_residue,
           "workflow_duration_seconds": duration, "grading_duration_seconds": grade_duration,
           "requests": getattr(provider, "requests_used", 0), "provider_evidence": getattr(provider, "evidence", {}),
           "authorization": result["authorization"], "plans": [asdict(p) for p in result["plans"]],
           "contract_passed": contract_ok, "run_dir": result.get("run_dir")}
    if record and result["accepted"]:
        if showcase and case.id == "shipping" and arm == "guarded_two":
            row["showcase_integration"] = showcase_publish(Path(result["run_dir"]))
        cleanup = cleanup_run(Path(result["run_dir"]))
        row.update(cleanup_status=cleanup["status"], cleanup_source_unchanged=cleanup["source_unchanged"])
        row["contract_passed"] = row["contract_passed"] and cleanup["source_unchanged"]
    (base / "arm-result.json").write_text(redact(json.dumps(row, indent=2, ensure_ascii=False)) + "\n", encoding="utf-8")
    return row


def aggregate(rows):
    metrics = {}
    for arm in ARMS:
        cells = [r for r in rows if r["arm"] == arm]
        eligible = [r for r in cells if r["authorized_repair_task"]]
        metrics[arm] = {"tasks": len(cells), "repair_tasks": len(eligible),
            "graded_repairs": sum(r["accepted"] and r["grader_passed"] for r in eligible),
            "false_acceptances": sum(r["accepted"] and not r["grader_passed"] for r in cells),
            "failed_candidates_retained": sum(r["failed_candidate_retained"] for r in cells),
            "approval_violations": sum(not r["source_copy_unchanged"] for r in cells if not r["authorized_repair_task"]),
            "synthetic_model_requests": sum(r["requests"] for r in cells),
            "workflow_duration_seconds": round(sum(r["workflow_duration_seconds"] for r in cells), 6)}
    return metrics


def write_reports(directory, summary):
    from junitparser import JUnitXml, TestCase, TestSuite, Failure
    suite = TestSuite("controlled-workflow-contracts")
    suite.add_property("evidence_kind", summary["evidence_kind"])
    suite.add_property("model_accuracy_claim", "false")
    suite.add_property("grading", "independent full tests; rejection excluded from repair denominator")
    for row in summary["cells"]:
        case = TestCase(row["case"], row["arm"], row["workflow_duration_seconds"])
        case.system_out = json.dumps({k: row[k] for k in ("accepted", "grader_passed", "failed_candidate_retained")})
        if not row["contract_passed"]: case.result = [Failure("Workflow contract failed", "contract")]
        suite.add_testcase(case)
    xml = JUnitXml(); xml.add_testsuite(suite)
    xml.write(str(directory / "contracts.xml"))
    (directory / "summary.json").write_text(redact(json.dumps(summary, indent=2, ensure_ascii=False)) + "\n", encoding="utf-8")
    lines = ["# Controlled workflow comparison", "", "Synthetic responses and public fixture answers: not live-model accuracy.", "",
             "Four new defect families + one scripted approval rejection. Every arm has the same source/tests/first patch.",
             "The grader reruns complete tests independently. Repair counts exclude rejection. Raw controls only alter disposable copies.",
             "Single-round and two-round request ceilings are both 4, but the latter may consume more requests. Timing is descriptive, no repeats.", "",
             "| Arm | Graded repairs / authorized tasks | False acceptances | Failed candidates retained | Approval violations | Synthetic requests |", "|---|---|---|---|---|---|"]
    for arm, m in summary["metrics"].items():
        lines.append(f"| {arm} | {m['graded_repairs']}/{m['repair_tasks']} | {m['false_acceptances']} | {m['failed_candidates_retained']} | {m['approval_violations']} | {m['synthetic_model_requests']} |")
    lines += ["", "JUnit reports workflow contracts, not repair success. Baseline negative outcomes remain in JSON/this report.",
              "Full evidence: per-cell arm-result.json, grader dirs, guarded run.json/report/diffs; no merge/push of any user repository.", ""]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run_comparison(directory, mock_url=None, showcase=False, selected_cases=None):
    if mock_url:
        parsed = urlsplit(mock_url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != "/v1"):
            raise SafetyError("Comparison only allows explicit synthetic HTTP 127.0.0.1:PORT/v1")
    selected = selected_cases if selected_cases is not None else cases()
    if (not 1 <= len(selected) <= 5 or len({c.id for c in selected}) != len(selected)
            or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", c.id) for c in selected)):
        raise SafetyError("Comparison requires 1-5 uniquely named trusted fixture cases")
    destination = unlinked(directory)
    if destination.exists(): raise SafetyError("Comparison directory must be new; never overwrite evidence")
    destination.mkdir(parents=True)
    dataset_sha = fingerprint({"cases": [asdict(c) for c in selected]})
    rows = []
    summary = {"schema_version": 1, "started_at": now(), "evidence_kind": "pi-loopback-scripted" if mock_url else "fixture-scripted",
               "model_accuracy_claim": False, "real_model_requests": 0, "dataset_sha256": dataset_sha,
               "cells": rows, "passed": False, "metrics": {}}
    try:
        for case in selected:
            for arm in ARMS:
                rows.append(execute_cell(case, arm, destination / case.id / arm, mock_url, showcase))
                summary["metrics"] = aggregate(rows)
                write_reports(destination, summary)
        matched = all(len({r["fixture_sha256"] for r in rows if r["case"] == c.id}) == 1
                      and len({r["first_patch_sha256"] for r in rows if r["case"] == c.id}) == 1 for c in selected)
        summary.update(passed=bool(rows) and len(rows) == len(selected) * len(ARMS) and matched and all(r["contract_passed"] for r in rows),
                       inputs_and_first_patches_matched=matched, finished_at=now())
        if showcase:
            summary["dashboard"] = export_dashboard([Path(r["run_dir"]) for r in rows if r["run_dir"]], destination / "dashboard.html")
        write_reports(destination, summary)
    except Exception as error:
        summary.update(error=f"{type(error).__name__}: {error}", finished_at=now())
        write_reports(destination, summary)
        raise
    return summary
