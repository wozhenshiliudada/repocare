"""Self-authored mixed-memory fixture and actual Pi/UI control chain."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession
from repo_maintainer.memory_collection import compose_memory
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.provider import MockProvider
from repo_maintainer.report_cases import export_report_case
from repo_maintainer.source_memory import CATALOG
from repo_maintainer.workflow import repair, cleanup_run


def fixture(directory):
    directory = directory.resolve(); directory.mkdir(parents=True, exist_ok=True)
    repo = create_fixture(directory / "repository")
    policy = Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",))
    (directory / "policy.json").write_text(json.dumps(asdict(policy)), encoding="utf-8")
    docs = directory / "documents"; docs.mkdir()
    (docs / "rule.md").write_text("状态：确认\naddition requires fresh current approval.", encoding="utf-8")
    (docs / "idea.md").write_text("状态：提议\nfuture addition graph is proposed only.", encoding="utf-8")
    (docs / "unhit.md").write_text("状态：待定\nUnrelated scope pending.", encoding="utf-8")
    entries = [dict(id=name, topic=name, path=name+".md", start_line=1, end_line=2,
                    kind="document", classification=classification)
               for name, classification in [("rule", "confirmed"), ("idea", "proposed"), ("unhit", "pending")]]
    (docs / CATALOG).write_text(json.dumps({"schema_version":1, "entries":entries}), encoding="utf-8")
    members = [{"id":"docs", "source_root":str(docs)}]
    selected = [{"id":"current-rule", "member_id":"docs", "entry_id":"rule"},
                {"id":"future-idea", "member_id":"docs", "entry_id":"idea"},
                {"id":"unhit-pending", "member_id":"docs", "entry_id":"unhit"}]
    for name in ("a", "b"):
        old = repair(repo, "addition history", MockProvider(), policy, "approval", directory / ("old-"+name),
                     approval=lambda plan:"approve", max_attempts=1)
        assert old["status"] == "candidate_ready"
        run = directory / ("old-"+name) / old["run_id"]
        assert cleanup_run(run)["status"] == "discarded"
        case = directory / "report-memory-cases" / name
        end = min(30, len((run / "report.md").read_text(encoding="utf-8").splitlines()))
        export_report_case(run, case, repo, directory / (name+".memory.sqlite3"), "old-run", "repair", 1, end)
        members.append({"id":name, "source_root":str(case)})
        selected.append({"id":"history-"+name, "member_id":name, "entry_id":"old-run"})
    spec = directory / "selection.json"
    spec.write_text(json.dumps({"schema_version":1, "members":members, "selections":selected}), encoding="utf-8")
    root, index = directory / "memory-collections" / "mixed", directory / "mixed.memory.sqlite3"
    compose_memory(spec, root, repo, index)
    return repo, root, index, policy


def trial(directory, url, mode):
    repo, root, index, policy = fixture(directory)
    gateway = RepositoryGateway(repo); before = gateway.snapshot()
    def open_session():
        value = PiProvider(policy.allowed_paths, url, policy.readonly_paths, max_requests=4 if mode=="auto" else 2)
        value.memory_root, value.memory_index = root, index
        return UISession(repo, policy, value, directory / "runs", allow_auto=True, max_attempts=2)
    session = open_session()
    summary = {"mode":mode, "paid_model_requests":0, "credential_reads":0, "antigravity_calls":0}
    try:
        preview = session._operate("memory_preview", {"issue":"Run addition"})["memory_preview"]
        assert len(preview["citations"]) == 4 and all("origin" in r for r in preview["citations"])
        assert session.state()["planning_attempts"] == []
        if mode == "failure":
            try: session._operate("plan", {"issue":"Run addition"})
            except SafetyError: pass
            else: raise AssertionError("failed model accepted")
            session.close(); session = open_session()
            session._operate("memory_preview", {"issue":"Run addition"})
            state = session.state()
            assert state["requests_reserved_unknown"] == 2 and state["requests_remaining"] == 0
            try: session._operate("plan", {"issue":"Run addition"})
            except SafetyError as error: assert "budget" in str(error)
            else: raise AssertionError("collection reset unknown allowance")
            summary.update(unknown_reserved=2, expected_requests=1)
        elif mode == "auto":
            result = session._operate("auto", {"issue":"Run addition"})
            run_dir = directory / "runs" / result["run_id"]
            record = json.loads((run_dir / "run.json").read_bytes())
            assert [r["verification"] for r in record["rounds"]] == ["failed", "passed"]
            assert session.state()["requests_used"] == 4
            for row in record["rounds"]:
                assert len(row["provider_evidence"]["memory_citations"]) == 4
            assert cleanup_run(run_dir)["status"] == "discarded"
            session.close(); session = open_session()
            try: session._operate("plan", {"issue":"Run addition"})
            except SafetyError as error: assert "budget" in str(error)
            else: raise AssertionError("collection reset used allowance")
            summary.update(expected_requests=4, rounds=["failed", "passed"], cleanup="discarded")
        else:
            session._operate("plan", {"issue":"Run addition"})
            old = session.state()["plans"][0]
            assert session.state()["runs"] == [] and old["status"] == "awaiting_approval"
            assert old["planning_evidence"]["memory_citations"] == preview["citations"]
            session.close(); session = open_session()
            current = session.state()["plans"][0]
            assert not current["claimed"] and current["revision"] != old["revision"]
            assert session.state()["requests_used"] == 2
            session._operate("claim_plan", {"plan_id":current["id"], "revision":current["revision"]})
            result = session._operate("decision", {"plan_id":current["id"], "fingerprint":current["fingerprint"], "decision":"approve"})
            assert result["workflow_status"] == "candidate_ready" and session.state()["requests_used"] == 2
            assert cleanup_run(directory / "runs" / result["run_id"])["status"] == "discarded"
            summary.update(expected_requests=2, pending_status=old["status"], new_approvals=1,
                           saved_execution_requests=0, cleanup="discarded")
        # An unhit pending source must invalidate preview and planning without a new attempt.
        attempts = len(session.state()["planning_attempts"])
        before_db = session.registry.path.read_bytes()
        path = directory / "documents" / "unhit.md"
        path.write_bytes(path.read_bytes()+b"\nchanged unhit source")
        assert session.state()["source_memory"]["status"] == "invalid"
        try: session._operate("memory_preview", {"issue":"Run addition"})
        except SafetyError as error: assert "changed" in str(error)
        else: raise AssertionError("unhit source bypassed validation")
        assert len(session.state()["planning_attempts"]) == attempts
        assert session.registry.path.read_bytes() == before_db
        assert gateway.snapshot() == before
        summary.update(status="mixed_memory_verified", source_unchanged=True, stale_extra_requests=0,
                       context_sha256=preview["context_sha256"])
        return summary
    finally: session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--url")
    parser.add_argument("--mode", choices=("fixture", "approval", "auto", "failure"), default="approval")
    args = parser.parse_args()
    if args.mode == "fixture":
        fixture(args.directory); print(json.dumps({"status":"self_authored_mixed_fixture_ready"}))
    else:
        if not args.url or not args.url.startswith("http://127.0.0.1:"): raise ValueError("Loopback models only")
        print(json.dumps(trial(args.directory.resolve(), args.url, args.mode)))
