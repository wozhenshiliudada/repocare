"""Conservative repair evidence and explicit abort; never replays models or tests."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from .dashboard import read_json
from .git import RepositoryGateway
from .integration import fingerprint, verification_registration
from .models import SafetyError, digest
from .recovery import operation_lock, process_identity, require_test_stopped, unlinked
from .store import RunStore, now, redact
from .workspace_inventory import inventory



def save_execution(directory: Path, data: dict):
    for name in ("repair-execution.json", "repair-execution.json.tmp"):
        unlinked(directory / name)
    data["owner_sha256"] = fingerprint(data["owner"])
    if data.get("checkpoint"):
        data["checkpoint_sha256"] = fingerprint(data["checkpoint"])
    original = json.dumps(data, ensure_ascii=False, indent=2)
    content = redact(original)
    # Do not authorize a manifest different from the original content after redaction.
    if json.loads(content) != json.loads(original):
        raise SafetyError("Execution evidence requires redaction; recovery authority is disabled")
    temporary = directory / "repair-execution.json.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(content + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(directory / "repair-execution.json")


class ExecutionJournal:
    def __init__(self, store: RunStore, source: RepositoryGateway):
        self.store, self.source = store, source
        r = store.record
        identity = process_identity(os.getpid())
        if not identity:
            raise SafetyError("Cannot record repair controller identity")
        self.data = {"schema_version": 1, "phase": "initialized", "controller": identity,
                     "inflight": None, "process_facts": [], "owner": {
                         "run_id": r["run_id"], "source_repo": str(source.root),
                         "common_dir": str(source.common_dir), "workspace": r["workspace"],
                         "branch": r["branch"], "base_sha": r["source_before"]["head"],
                         "source_before": r["source_before"], "policy": r["policy"]}}
        self.save()

    def save(self):
        save_execution(self.store.directory, self.data)

    def phase(self, value: str):
        self.data["phase"] = value
        self.save()

    def checkpoint(self, candidate: RepositoryGateway, expected: dict | None = None):
        manifest = inventory(candidate.root)
        if expected is not None and manifest != expected:
            raise SafetyError("Candidate files changed outside the approved edit; preserve them")
        snapshot = candidate.snapshot()
        previous = self.data.get("checkpoint", {}).get("snapshot")
        if previous and any(snapshot[key] != previous[key] for key in ("head", "refs", "branch", "index_sha256")):
            raise SafetyError("Candidate Git metadata changed outside the approved edit")
        self.data["checkpoint"] = {"snapshot": snapshot, "inventory": manifest,
                                  "registration": verification_registration(self.source, candidate.root)}
        self.phase("workspace_ready")

    def edited_inventory(self, edits) -> dict:
        expected = copy.deepcopy(self.data["checkpoint"]["inventory"])
        for edit in edits:
            expected[edit.path]["sha256"] = digest(edit.content.encode("utf-8"))
        return expected

    def start(self, kind: str):
        self.data["inflight"] = {"kind": kind, "status": "starting"}
        self.save()

    def pid(self, pid: int):
        identity = process_identity(pid)
        self.data["inflight"].update(status="running", identity=identity, exited_before_probe=identity is None)
        self.save()

    def done(self, result: dict | None = None):
        fact = {**self.data["inflight"], "status": "finished", "finished_at": now()}
        if result:
            fact.update(returncode=result["returncode"], timed_out=result["timed_out"])
        self.data["process_facts"].append(fact)
        self.data["inflight"] = None
        self.phase("test_finished")


def _inspect(directory: Path) -> tuple[dict, dict, dict, RepositoryGateway]:
    record, record_sha = read_json(directory / "run.json")
    execution, execution_sha = read_json(directory / "repair-execution.json")
    owner = execution.get("owner", {})
    workspace = unlinked(directory / "workspace")
    source = RepositoryGateway(unlinked(Path(record["source_repo"])))
    if (execution.get("schema_version") != 1 or execution.get("owner_sha256") != fingerprint(owner)
            or owner.get("run_id") != directory.name or record.get("run_id") != directory.name
            or owner.get("source_repo") != str(source.root) or owner.get("common_dir") != str(source.common_dir)
            or owner.get("workspace") != str(workspace) or record.get("workspace") != str(workspace)
            or owner.get("branch") != "maintenance/" + directory.name or record.get("branch") != owner["branch"]
            or owner.get("source_before") != record.get("source_before") or owner.get("policy") != record.get("policy")
            or owner.get("base_sha") != record["source_before"]["head"]
            or directory.is_relative_to(source.root) or directory.is_relative_to(source.common_dir)):
        raise SafetyError("Repair execution ownership changed; preserve evidence")
    reasons = []
    inflight = execution.get("inflight")
    for label, identity in (("controller", execution.get("controller")),
                            ("process", inflight.get("identity") if inflight else None)):
        if identity and process_identity(identity["pid"]) == identity:
            reasons.append(label + "_still_alive")
    if not execution.get("controller"):
        reasons.append("controller_identity_missing")
    if inflight and inflight.get("status") == "starting":
        reasons.append("process_pid_launch_gap")
    elif inflight:
        try:
            if inflight.get("status") != "running" or (not inflight.get("identity") and not inflight.get("exited_before_probe")):
                reasons.append("process_identity_missing")
            require_test_stopped({"test_inflight": inflight.get("identity")})
        except SafetyError:
            if "process_still_alive" not in reasons:
                reasons.append("process_state_unknown_or_alive")
    before = source.snapshot(exclude_branch=owner["branch"])
    source_matches = before == owner["source_before"]
    if not source_matches:
        reasons.append("source_snapshot_changed")
    publication = source.command("show-ref", "--verify", "--hash", "refs/heads/integration/" + directory.name,
                                 check=False).decode().strip()
    if publication or (directory / "integration-review.json").exists() or unlinked(directory / "integration-verification").exists():
        reasons.append("integration_evidence_exists")
    branch = source.command("show-ref", "--verify", "--hash", "refs/heads/" + owner["branch"], check=False).decode().strip()
    if branch and branch != owner["base_sha"]:
        reasons.append("candidate_ref_changed")
    registration = verification_registration(source, workspace)
    related = []
    for block in source.command("worktree", "list", "--porcelain", "-z").decode().split("\0\0"):
        fields = block.strip("\0").split("\0")
        if "branch refs/heads/" + owner["branch"] in fields:
            path = next((f[9:] for f in fields if f.startswith("worktree ")), "")
            related.append(path)
            if Path(path).absolute() != workspace:
                reasons.append("candidate_registered_at_other_path")
    checkpoint = execution.get("checkpoint")
    present = workspace.exists()
    checks = {"source_matches": source_matches, "workspace_exists": present, "branch": branch or None,
              "registration": registration, "related_worktrees":related,
              "controller": execution.get("controller"), "inflight": inflight}
    if checkpoint and execution.get("checkpoint_sha256") != fingerprint(checkpoint):
        reasons.append("checkpoint_hash_changed")
    if present:
        if not checkpoint or execution.get("checkpoint_sha256") != fingerprint(checkpoint):
            reasons.append("owned_checkpoint_missing")
        else:
            candidate = RepositoryGateway(workspace)
            checks["candidate_matches"] = (candidate.common_dir == source.common_dir
                and candidate.snapshot() == checkpoint["snapshot"] and inventory(workspace) == checkpoint["inventory"]
                and registration == checkpoint["registration"] and branch == owner["base_sha"])
            if not checks["candidate_matches"]:
                reasons.append("candidate_files_index_or_registration_changed")
    elif registration:
        reasons.append("dangling_worktree_registration")
    elif (checkpoint or branch) and execution.get("phase") not in {"abort_cleanup_intent", "aborted"}:
        reasons.append("candidate_disappeared_without_cleanup_intent")
    if execution.get("phase") in {"planning", "replanning", "workspace_add_intent", "editing", "automatic_cleanup"}:
        reasons.append("unfinished_external_or_edit_window")
    if record.get("finished_at") and execution.get("phase") not in {"abort_cleanup_intent", "aborted"}:
        reasons.append("repair_already_finished")
    if source.snapshot(exclude_branch=owner["branch"]) != before:
        reasons.append("source_changed_during_inspection")
    preview = {"schema_version": 1, "run_id": directory.name, "phase": execution.get("phase"),
               "execution_outcome": "unknown", "can_abort": not reasons and execution.get("phase") != "aborted",
               "status": "aborted" if execution.get("phase") == "aborted" else "abort_available" if not reasons else "manual_inspection_required",
               "checks": checks, "reasons": reasons, "record_sha256": record_sha, "execution_sha256": execution_sha}
    preview["revision"] = fingerprint(preview)
    return preview, record, execution, source


def inspect_repair(run_dir: Path) -> dict:
    with operation_lock(run_dir) as directory:
        return _inspect(directory)[0]


def recover_repair(run_dir: Path, approved: str, actor: str = "local-operator") -> dict:
    with operation_lock(run_dir) as directory:
        preview, record, execution, source = _inspect(directory)
        if approved != preview["revision"] or not preview["can_abort"]:
            raise SafetyError("Repair abort requires a fresh eligible inspection fingerprint")
        source.audit = record.setdefault("git_commands", [])
        authorization = {"actor":actor, "time":now(), "inspection_sha256":approved, "inspection":preview}
        execution.setdefault("abort_authorizations", []).append(authorization)
        if execution.get("inflight"):
            execution["process_facts"].append({**execution["inflight"], "status":"observed_stopped_on_recovery", "observed_at":now()})
        execution.update(phase="abort_cleanup_intent", abort_authorization=authorization, inflight=None)
        save_execution(directory, execution)
        # Recheck checkpoint and all ownership facts immediately before destructive Git operations.
        fresh, _, _, _ = _inspect(directory)
        if not fresh["can_abort"]:
            raise SafetyError("Repair changed before abort; preserve workspace")
        def command(*args):
            execution["inflight"] = {"kind": "git_abort", "status": "starting"}
            save_execution(directory, execution)
            def started(pid):
                identity = process_identity(pid)
                execution["inflight"].update(status="running", identity=identity, exited_before_probe=identity is None)
                save_execution(directory, execution)
            source.command(*args, on_start=started)
            execution["process_facts"].append({**execution["inflight"], "status":"finished", "finished_at":now()})
            execution["inflight"] = None
            save_execution(directory, execution)
        workspace = unlinked(directory / "workspace")
        if workspace.exists():
            record["diff"] = RepositoryGateway(workspace).diff()
            RunStore(directory, record).save()
            command("worktree", "remove", "--force", str(workspace))
        # Never prune registrations or delete an advanced/foreign branch.
        current, _, _, _ = _inspect(directory)
        if not current["can_abort"]:
            raise SafetyError("Repair changed after workspace removal; preserve remaining evidence")
        if current["checks"]["branch"]:
            command("update-ref", "-d", "refs/heads/" + execution["owner"]["branch"], execution["owner"]["base_sha"])
        after = source.snapshot()
        if after != execution["owner"]["source_before"]:
            raise SafetyError("Source changed while aborting; retain evidence")
        record.update(recovery={"result":"repair_aborted", "source_reset":False, "actor":actor,
                                "inspection_sha256":approved}, source_after=after, source_unchanged=True,
                      finished_at=record.get("finished_at") or now())
        store = RunStore(directory, record)
        if record.get("status") != "repair_aborted":
            store.event("repair_aborted")
        else:
            store.save()
        execution.update(phase="aborted", inflight=None, aborted_at=execution.get("aborted_at") or now())
        save_execution(directory, execution)
        return record
