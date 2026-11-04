"""Actual Pi plus self-authored relationships; synthetic loopback providers only."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gui_relations_fixture import fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import Policy, SafetyError
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.workflow import cleanup_run


def trial(directory, url, mode):
    sample = directory / "sample"; fixture(sample)
    repo = sample / "repository"; policy = Policy(**json.loads((sample / "policy.json").read_bytes()))
    # JSON lists normalize to the original immutable scope tuples.
    policy = Policy(tuple(policy.allowed_paths), tuple(tuple(r) for r in policy.allowed_tests),
                    readonly_paths=tuple(policy.readonly_paths))
    gateway = RepositoryGateway(repo); before = gateway.snapshot()
    manifest = sample / "decisions.memory-relations.json"; original = manifest.read_bytes()
    def open_session():
        value = PiProvider(policy.allowed_paths, url, policy.readonly_paths, max_requests=4)
        value.memory_root = sample / "memory-collections" / "decisions"
        value.memory_index = sample / "decisions.memory.sqlite3"
        value.memory_relations = manifest; value.memory_relations_context = True
        return UISession(repo, policy, value, sample / "runs", allow_auto=True, max_attempts=2)
    session = open_session()
    summary = {"mode": mode, "paid_model_requests": 0, "credential_reads": 0, "antigravity_calls": 0}
    try:
        preview = session._operate("memory_preview", {"issue": "addition"})["memory_preview"]
        assert session.state()["planning_attempts"] == []
        receipt = preview["context_sha256"]
        if mode in {"failure", "changed"}:
            try: session._operate("plan", {"issue": "addition"})
            except SafetyError: pass
            else: raise AssertionError("failed/stale inference returned a plan")
            state = session.state()
            assert state["plans"] == [] and state["runs"] == []
            assert state["requests_reserved_unknown"] == 4 and state["requests_remaining"] == 0
            if mode == "changed":
                assert manifest.read_bytes() != original and state["source_memory"]["status"] == "invalid"
                manifest.write_bytes(original)
            session.close(); session = open_session()
            session._operate("memory_preview", {"issue": "addition"})
            assert session.state()["requests_reserved_unknown"] == 4
            try: session._operate("plan", {"issue": "addition"})
            except SafetyError as error: assert "budget" in str(error)
            else: raise AssertionError("failed inference allowance reset")
            summary.update(unknown_reserved=4, expected_requests=1 if mode == "failure" else 2)
        elif mode in {"auto", "auto_changed"}:
            result = session._operate("auto", {"issue": "addition"})
            record = json.loads((sample / "runs" / result["run_id"] / "run.json").read_bytes())
            if mode == "auto_changed":
                assert result["workflow_status"] == "rolled_back"
                assert len(record["rounds"]) == 1 and record["rounds"][0]["verification"] == "failed"
                assert "relations file changed" in record["error"]
                assert record["provider_evidence"]["consumption"] == "unknown"
                assert record["recovery"]["result"] == "candidate_removed"
                assert not (sample / "runs" / result["run_id"] / "workspace").exists()
                assert session.state()["requests_used"] == 2 and session.state()["requests_reserved_unknown"] == 2
                assert manifest.read_bytes() != original
                manifest.write_bytes(original)
                session.close(); session = open_session()
                assert session.state()["requests_used"] == 2 and session.state()["requests_reserved_unknown"] == 2
                summary.update(expected_requests=4, unknown_reserved=2, used_requests=2, workflow_status="rolled_back")
            else:
                assert result["workflow_status"] == "candidate_ready"
                assert [r["verification"] for r in record["rounds"]] == ["failed", "passed"]
                assert all(r["provider_evidence"]["memory_context_sha256"] == receipt for r in record["rounds"])
                assert session.state()["requests_used"] == 4
                assert cleanup_run(sample / "runs" / result["run_id"])["status"] == "discarded"
                session.close(); session = open_session()
                summary.update(expected_requests=4, rounds=["failed", "passed"], cleanup="discarded")
            try: session._operate("plan", {"issue": "addition"})
            except SafetyError as error: assert "budget" in str(error)
            else: raise AssertionError("used budget reset")
        else:
            session._operate("plan", {"issue": "addition"}); old = session.state()["plans"][0]
            assert old["planning_evidence"]["memory_context_sha256"] == receipt
            assert old["status"] == "awaiting_approval" and session.state()["runs"] == []
            session.close(); session = open_session(); current = session.state()["plans"][0]
            assert not current["claimed"] and session.state()["requests_used"] == 2
            try: session._operate("claim_plan", {"plan_id": current["id"], "revision": old["revision"]})
            except SafetyError: pass
            else: raise AssertionError("old claim challenge accepted")
            session._operate("claim_plan", {"plan_id": current["id"], "revision": current["revision"]})
            result = session._operate("decision", {"plan_id": current["id"], "fingerprint": current["fingerprint"], "decision": "approve"})
            assert result["workflow_status"] == "candidate_ready" and session.state()["requests_used"] == 2
            assert cleanup_run(sample / "runs" / result["run_id"])["status"] == "discarded"
            summary.update(expected_requests=2, new_approvals=1, saved_execution_requests=0, cleanup="discarded")
        # A change to a source outside retrieval must still invalidate all claims.
        extra = sample / "current" / "unhit.md"; data = extra.read_bytes()
        extra.write_bytes(data+b"\nChanged outside retrieval")
        assert session.state()["source_memory"]["status"] == "invalid"
        try: session._operate("memory_preview", {"issue": "addition"})
        except SafetyError: pass
        else: raise AssertionError("unhit source bypassed validation")
        extra.write_bytes(data)
        assert gateway.snapshot() == before
        summary.update(status="pi_decision_context_verified", context_sha256=receipt, source_unchanged=True)
        return summary
    finally: session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--mode", choices=("approval", "auto", "failure", "changed", "auto_changed"), required=True)
    args = parser.parse_args()
    if not args.url.startswith("http://127.0.0.1:"): raise ValueError("Loopback only")
    print(json.dumps(trial(args.directory, args.url, args.mode)))
