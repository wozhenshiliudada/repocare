"""Read-only inspection of a consumed-plan/run-record gap; no Git mutations or replay."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .dashboard import read_json, unlinked
from .git import RepositoryGateway
from .models import Policy, SafetyError, digest
from .pi_provider import decode_plan
from .store import redact


def inspect_gap(repo: Path, policy: Policy, runs_dir: Path, identity: str,
                plans: dict, entry: dict) -> dict:
    if entry.get("run_id") != identity:
        raise SafetyError("Interrupted run ownership changed")
    origins = [(key, item) for key, item in plans.items()
               if item["view"].get("run_id") == identity and item["view"].get("status") == "consumed"]
    preview = {"schema_version": 1, "run_id": identity, "plan_id": None,
               "execution_outcome": "unknown", "can_close_registration": False,
               "checks": {}, "reasons": [], "resolution": entry.get("resolution")}
    checks, reasons = preview["checks"], preview["reasons"]
    origin = origins[0][1] if len(origins) == 1 else None
    if origin is None:
        reasons.append("consumed_plan_origin_missing_or_ambiguous")
    else:
        preview["plan_id"] = origins[0][0]
        try:
            data, file_sha = read_json(origin["path"])
            plan = decode_plan(data)
            policy.validate(plan)
            checks["plan_matches"] = (file_sha == origin["file_sha"]
                and data.get("plan_sha256") == plan.fingerprint == origin["view"]["fingerprint"]
                and data["plan"] == origin["view"]["plan"]
                and plan.tests == policy.allowed_tests and plan.base_sha == origin["snapshot"]["head"])
            # Includes provider evidence and source snapshot, not just the patch.
            checks["origin_sha256"] = digest(json.dumps(
                {k: v for k, v in origin.items() if k != "path"}, sort_keys=True, ensure_ascii=False).encode())
        except (OSError, ValueError, KeyError, TypeError, SafetyError):
            checks["plan_matches"] = False
        if not checks["plan_matches"]:
            reasons.append("plan_evidence_changed_or_unreadable")
    repository = RepositoryGateway(repo)
    before = repository.snapshot()
    checks["source_matches"] = before == origin["snapshot"] if origin else None
    checks["source_snapshot_sha256"] = digest(json.dumps(before, sort_keys=True).encode())
    if checks["source_matches"] is not True:
        reasons.append("source_snapshot_changed_or_unknown")
    directory = unlinked(runs_dir / identity)
    workspace = unlinked(directory / "workspace")
    verification = unlinked(directory / "integration-verification")
    record_path = unlinked(directory / "run.json")
    checks["directory_exists"] = directory.exists()
    checks["run_evidence"] = "absent"
    if record_path.exists():
        checks["run_evidence"] = "present"
        try:
            record, record_sha = read_json(record_path)
            checks["record_sha256"] = record_sha
            checks["record_owner_matches"] = (record.get("run_id") == identity
                and record.get("source_repo") == str(repo) and record.get("workspace") == str(workspace)
                and record.get("branch") == "maintenance/" + identity
                and json.loads(json.dumps(record.get("policy"))) == json.loads(json.dumps(asdict(policy))))
            checks["repair_final_evidence"] = bool(record.get("finished_at"))
        except (OSError, ValueError, KeyError, TypeError, SafetyError):
            checks["run_evidence"] = "invalid"
        reasons.append("run_evidence_present_or_invalid")
    if checks["directory_exists"]:
        reasons.append("run_directory_exists_preserve_contents")
    branches = ("maintenance/" + identity, "integration/" + identity)
    for name, branch in zip(("candidate_ref", "publication_ref"), branches):
        checks[name] = repository.command("show-ref", "--verify", "--hash", "refs/heads/" + branch,
                                          check=False).decode().strip() or None
        if checks[name]:
            reasons.append(name + "_exists_preserve")
    # Parse Git's NUL framing; paths may contain spaces or newlines. Never traverse registrations.
    registrations = []
    for block in repository.command("worktree", "list", "--porcelain", "-z").decode().split("\0\0"):
        fields = block.split("\0")
        paths = [field[9:] for field in fields if field.startswith("worktree ")]
        refs = [field[7:] for field in fields if field.startswith("branch ")]
        if not paths:
            continue
        path = Path(paths[0]).absolute()
        if path.is_relative_to(directory) or any(ref == "refs/heads/" + branch for ref in refs for branch in branches):
            registrations.append({"path": str(path), "branches": refs,
                                  "detached": "detached" in fields, "prunable": any(f.startswith("prunable") for f in fields)})
    checks["worktree_registrations"] = registrations
    if registrations:
        reasons.append("worktree_registration_exists_preserve")
    if repository.snapshot() != before:
        reasons.append("source_changed_during_inspection")
    # Detect an external writer after the first path/ref read, without deleting or repairing anything.
    if directory.exists() != checks["directory_exists"] or record_path.exists() != (checks["run_evidence"] != "absent"):
        reasons.append("run_evidence_changed_during_inspection")
    if origin and checks["plan_matches"]:
        try:
            if read_json(origin["path"])[1] != origin["file_sha"]:
                reasons.append("plan_changed_during_inspection")
        except (OSError, ValueError, SafetyError):
            reasons.append("plan_changed_during_inspection")
    if preview["resolution"]:
        preview["status"] = "registration_closed" if not reasons else "resolution_conflict"
    else:
        preview["status"] = "closure_available" if not reasons else "manual_inspection_required"
        preview["can_close_registration"] = not reasons
    return json.loads(redact(json.dumps(preview, ensure_ascii=False)))
