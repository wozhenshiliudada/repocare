"""Real Pi UI driver using the independently counted loopback model only."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.source_memory import build_memory, CATALOG
from repo_maintainer.workflow import cleanup_run


def fixture(directory):
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    repo = create_fixture(directory / "repository")
    root = directory / "documents"
    root.mkdir()
    # Treat all text as data, including script-shaped historical instructions.
    (root / "rule.md").write_text("状态：确认\naddition requires a fresh approval", encoding="utf-8")
    (root / "case.md").write_text('Historical addition case: old approval does not grant permission. <img src=x onerror="window.memoryInjected=true">', encoding="utf-8")
    entries = [dict(id=identifier, topic=identifier, path=identifier+".md", start_line=1, end_line=end,
                    kind=kind, classification=classification)
               for identifier, end, kind, classification in [("rule", 2, "document", "confirmed"),
                                                           ("case", 1, "case", "historical")]]
    (root / CATALOG).write_text(json.dumps({"schema_version": 1, "entries": entries}), encoding="utf-8")
    index = directory / "private.memory.sqlite3"
    build_memory(root, repo, index)
    policy = Policy(("calculator.py",), (DEMO_TEST,), readonly_paths=("tests/test_calculator.py",))
    (directory / "policy.json").write_text(json.dumps(asdict(policy)), encoding="utf-8")
    return repo, root, index, policy


def trial(directory, url, mode):
    repo, root, index, policy = fixture(directory)
    gateway = RepositoryGateway(repo)
    before = gateway.snapshot()
    runs = directory / "runs"
    def open_session():
        provider = PiProvider(policy.allowed_paths, url, policy.readonly_paths, max_requests=4)
        provider.memory_root, provider.memory_index = root, index
        return UISession(repo, policy, provider, runs, allow_auto=True, max_attempts=2)
    session = open_session()
    summary = {"mode": mode, "paid_model_requests": 0, "credential_reads": 0}
    try:
        preview = session._operate("memory_preview", {"issue": "addition"})["memory_preview"]
        assert len(preview["citations"]) == 2 and not preview["runtime_authorized"]
        assert session.state()["planning_attempts"] == []
        if mode == "failure":
            try: session._operate("plan", {"issue": "addition"})
            except SafetyError: pass
            else: raise AssertionError("failed model accepted")
            assert session.state()["requests_reserved_unknown"] == 4
            session.close();session = open_session()
            session._operate("memory_preview", {"issue": "addition"})
            try: session._operate("plan", {"issue": "addition"})
            except SafetyError: pass
            else: raise AssertionError("unknown budget reset")
            state = session.state()
            assert state["requests_reserved_unknown"] == 4 and state["requests_remaining"] == 0
            assert len(state["planning_attempts"]) == 1 and state["plans"] == [] and state["runs"] == []
            summary.update(unknown_reserved=4, attempt_count=1)
        elif mode == "auto":
            result = session._operate("auto", {"issue": "addition"})
            record = json.loads((runs / result["run_id"] / "run.json").read_text(encoding="utf-8"))
            assert result["workflow_status"] == "candidate_ready"
            assert [r["verification"] for r in record["rounds"]] == ["failed", "passed"]
            assert session.state()["requests_used"] == 4
            for row in record["rounds"]:
                assert {c["classification"] for c in row["provider_evidence"]["memory_citations"]} == {"confirmed", "historical"}
            cleanup_run(runs / result["run_id"])
            session.close();session = open_session()
            assert session.state()["requests_remaining"] == 0
            try: session._operate("plan", {"issue": "addition"})
            except SafetyError: pass
            else: raise AssertionError("auto consumed requests were reset")
            summary.update(requests_used=4, rounds=["failed", "passed"], cleanup="discarded")
        else:
            session._operate("plan", {"issue": "addition"})
            old = session.state()["plans"][0]
            assert old["planning_evidence"]["memory_context_sha256"] == preview["context_sha256"]
            assert session.state()["runs"] == [] and gateway.snapshot() == before
            session.close();session = open_session()
            plan = session.state()["plans"][0]
            assert not plan["claimed"] and old["revision"] != plan["revision"]
            try: session._operate("claim_plan", {"plan_id": plan["id"], "revision": old["revision"]})
            except SafetyError: pass
            else: raise AssertionError("old claim accepted")
            session._operate("claim_plan", {"plan_id": plan["id"], "revision": plan["revision"]})
            result = session._operate("decision", {"plan_id": plan["id"], "fingerprint": plan["fingerprint"], "decision": "approve"})
            assert result["workflow_status"] == "candidate_ready"
            state = session.state()
            assert state["requests_used"] == 2
            assert state["plans"][0]["status"] == "consumed"
            assert state["runs"][0]["planning_origin"]["provider_evidence"]["memory_context_sha256"] == preview["context_sha256"]
            cleanup_run(runs / result["run_id"])
            session.close()
            (root / "rule.md").write_text("状态：确认\nchanged addition decision", encoding="utf-8")
            session = open_session()
            assert session.state()["source_memory"]["status"] == "invalid"
            assert session.state()["plans"][0]["status"] == "consumed"
            try: session._operate("plan", {"issue": "addition"})
            except SafetyError: pass
            else: raise AssertionError("stale sources reached model")
            assert len(session.state()["planning_attempts"]) == 1
            summary.update(requests_used=2, saved_execution_requests=0, stale_extra_requests=0, cleanup="discarded")
        assert gateway.snapshot() == before
        summary.update(status="ui_memory_verified", source_unchanged=True)
        return summary
    finally: session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--url")
    parser.add_argument("--mode", choices=("approval", "auto", "failure", "fixture"), default="approval")
    args = parser.parse_args()
    if args.mode == "fixture":
        fixture(args.directory)
        print(json.dumps({"status": "self_authored_fixture_ready"}))
    else:
        if not args.url or not args.url.startswith("http://127.0.0.1:"):
            raise ValueError("Only explicit loopback synthetic models are allowed")
        print(json.dumps(trial(args.directory.resolve(), args.url, args.mode)))
