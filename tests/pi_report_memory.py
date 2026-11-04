"""Real local workflow report -> explicit case -> real Pi; no live credentials."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import Policy, SafetyError, digest
from repo_maintainer.provider import MockProvider
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.report_cases import export_report_case
from repo_maintainer.source_memory import search_memory
from repo_maintainer.workflow import repair, cleanup_run

parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True)
parser.add_argument("--directory", type=Path, required=True)
args = parser.parse_args()
repo = create_fixture(args.directory / "repository")
gateway = RepositoryGateway(repo); before = gateway.snapshot()
policy = Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",))
old = repair(repo, "addition history", MockProvider(), policy, "approval", args.directory / "old-runs",
             approval=lambda plan: "approve", max_attempts=1)
assert old["status"] == "candidate_ready"
old_directory = args.directory / "old-runs" / old["run_id"]
assert cleanup_run(old_directory)["status"] == "discarded"
root, index = args.directory / "report-memory-cases" / "addition", args.directory / "addition.memory.sqlite3"
original_report = (old_directory / "report.md").read_bytes()
exported = export_report_case(old_directory, root, repo, index, "prior-run", "repair", 1, 30)
assert exported["record"]["recorded_status"] == "discarded" and not exported["runtime_authorized"]
issue = "Run addition bug"
context = search_memory(root, repo, index, issue)["context"]
assert len(context["hits"]) == 1
assert context["hits"][0]["source_sha256"] == digest(original_report)
assert "| approve |" in context["hits"][0]["excerpt"]

def provider():
    value = PiProvider(policy.allowed_paths, args.url, policy.readonly_paths, max_requests=2)
    value.memory_root, value.memory_index = root, index
    return value

first = provider()
pending = repair(repo, issue, first, policy, "approval", args.directory / "pending-runs", max_attempts=1)
assert pending["status"] == "awaiting_approval" and not Path(pending["workspace"]).exists()
assert gateway.snapshot() == before and first.requests_used == 2
assert pending["provider_evidence"]["memory_context_sha256"] == context["context_sha256"]
assert pending["provider_evidence"]["memory_citations"][0]["classification"] == "historical"
try: first.plan(gateway, issue, policy.allowed_tests)
except SafetyError as error: assert "budget exhausted" in str(error)
else: raise AssertionError("Historical report refunded exhausted budget")

decisions = []
def approve(plan):
    decisions.append(plan.fingerprint)
    return "approve"
second = provider()  # A separate, explicitly bounded synthetic trial, not a refund.
result = repair(repo, issue, second, policy, "approval", args.directory / "current-runs",
                approval=approve, max_attempts=1)
assert result["status"] == "candidate_ready" and len(decisions) == 1
assert second.requests_used == 2 and result["source_unchanged"]
assert cleanup_run(args.directory / "current-runs" / result["run_id"])["status"] == "discarded"
assert gateway.snapshot() == before

# Changes to the original JSON, even with an unchanged copied report, invalidate new calls.
record = json.loads((old_directory / "run.json").read_bytes())
record["historical_budget_claim"] = 999
(old_directory / "run.json").write_text(json.dumps(record), encoding="utf-8")
stale = provider()
stale.config_file = args.directory / "must-not-read.env"
try: stale.plan(gateway, issue, policy.allowed_tests)
except SafetyError as error: assert "report source or copied evidence changed" in str(error)
else: raise AssertionError("Changed original report reached provider")
assert stale.requests_used == 0 and gateway.snapshot() == before
assert (root / "report.md").read_bytes() == original_report
print(json.dumps({"status": "report_case_pi_verified", "historical_status": "discarded",
                  "pending_status": pending["status"], "new_approvals": len(decisions),
                  "synthetic_requests": 4, "stale_extra_requests": 0, "exhausted_extra_requests": 0,
                  "source_unchanged": True, "cleanup_status": "discarded", "context_sha256": context["context_sha256"]}))
