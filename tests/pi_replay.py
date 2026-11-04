"""Integration driver: real Pi session against a synthetic loopback model."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.demo import DEMO_TEST, create_fixture
from repo_maintainer.models import Policy
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.workflow import cleanup_run, repair

parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True)
parser.add_argument("--directory", type=Path, required=True)
parser.add_argument("--decision", choices=("approve", "reject"), default="approve")
parser.add_argument("--expected", choices=("candidate_ready", "rejected", "rolled_back", "failed"), required=True)
parser.add_argument("--max-attempts", type=int, default=1)
parser.add_argument("--reject-revision", action="store_true")
parser.add_argument("--integrate", action="store_true")
parser.add_argument("--large-source", action="store_true")
args = parser.parse_args()
repo = create_fixture(args.directory / "repository")
if args.large_source:
    from repo_maintainer.git import RepositoryGateway
    source = repo / "calculator.py"
    source.write_bytes(("# preserve — ‘original’ 😀 trailing spaces   \r\n" * 800).encode() + source.read_bytes())
    git = RepositoryGateway(repo)
    git.command("add", "calculator.py")
    git.command("-c", "user.name=Maintenance Demo", "-c", "user.email=demo@example.invalid",
                "commit", "-m", "Large immutable snapshot fixture")
original = (repo / "calculator.py").read_bytes()
decisions = []
def approve(plan):
    decisions.append(plan.fingerprint)
    return "reject" if args.reject_revision and len(decisions) > 1 else args.decision
record = repair(repo, "add(2, 3) must equal 5; preserve negative inputs",
                PiProvider(("calculator.py",), args.url, ("tests/test_calculator.py",), max_requests=6),
                Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",)),
                "approval", args.directory / "runs", approval=approve, max_attempts=args.max_attempts)
passed = record["status"] == args.expected and record["source_unchanged"]
evidence = record.get("provider_evidence", {})
summary = {"passed": passed, "run_id": record["run_id"], "status": record["status"],
           "source_unchanged": record["source_unchanged"], "provider_evidence": evidence,
           "baseline_exit": [t["returncode"] for t in record["baseline_tests"]],
           "candidate_exit": [t["returncode"] for t in record["candidate_tests"]]}
summary["original_bytes"] = len(original)
summary["original_bytes_preserved"] = (repo / "calculator.py").read_bytes() == original
if record["status"] == "candidate_ready":
    candidate = (Path(record["workspace"]) / "calculator.py").read_bytes()
    summary["candidate_unchanged_bytes_preserved"] = candidate == original.replace(b"a - b", b"a + b")
summary["rounds"] = [{"number": r["number"], "verification": r.get("verification", "not_run"),
                      "authorization": r.get("authorization"), "exits": [t["returncode"] for t in r["tests"]]}
                     for r in record["rounds"]]
if record["status"] == "candidate_ready":
    if args.integrate:
        from repo_maintainer.integration import prepare_integration, integrate
        review = prepare_integration(args.directory / "runs" / record["run_id"])
        pending = integrate(args.directory / "runs" / record["run_id"])
        published = integrate(args.directory / "runs" / record["run_id"], review["review_sha256"])
        summary["integration"] = {"unapproved_status": pending["status"], "status": published["status"],
                                  "review_sha256": review["review_sha256"], "commit": review["review"]["commit"],
                                  "branch": review["review"]["target_branch"], "source_unchanged": published["source_unchanged"]}
        passed = passed and published["status"] == "published" and published["source_unchanged"]
    recovered = cleanup_run(args.directory / "runs" / record["run_id"])
    summary["cleanup_status"] = recovered["status"]
    summary["passed"] = passed and recovered["source_unchanged"] and recovered["status"] == "discarded"
print(json.dumps(summary, ensure_ascii=False))
raise SystemExit(0 if summary["passed"] else 1)
