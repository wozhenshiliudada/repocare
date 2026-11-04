"""Explicit publication of a sealed, tested candidate to a new run-owned branch.

No checkout, merge, reset or push of the user's source branch is performed.
Local journals are audit data, not an authenticated multi-user permission service.
"""
from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

from .git import RepositoryGateway
from .models import Policy, SafetyError, digest
from .pi_provider import decode_plan
from .runner import run_test
from .store import RunStore, now, redact
from .recovery import operation_lock, unlinked, process_identity, require_test_stopped
from .workspace_inventory import inventory


def fingerprint(value: dict) -> str:
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def save_review(run_dir: Path, review: dict) -> None:
    for name in ("integration-review.json.tmp", "integration-review.json", "integration-review.md"):
        unlinked(run_dir / name)
    if "preparation" in review:
        review["preparation_sha256"] = fingerprint(review["preparation"])
    data = redact(json.dumps(review, indent=2, ensure_ascii=False)) + "\n"
    temp = run_dir / "integration-review.json.tmp"
    temp.write_text(data, encoding="utf-8")
    temp.replace(run_dir / "integration-review.json")
    core = review.get("review", {})
    lines = ["# Integration review", "", f"Status: **{review['status']}**", "",
             f"Fingerprint: `{review.get('review_sha256', 'not_ready')}`", "",
             f"New branch: `{core.get('target_branch', 'not_ready')}`", "",
             f"Sealed commit: `{core.get('commit', 'not_ready')}`", "",
             "Inspect `integration.diff` and JSON tests before approving this exact fingerprint.", "",
             "Source checkout/branch is not modified. This does not authorize merge or push.", ""]
    for test in core.get("tests", []):
        lines += [f"Test exit: `{test['returncode']}`; timeout: `{test['timed_out']}`", ""]
    if review.get("error"):
        lines += ["Error: " + review["error"], ""]
    (run_dir / "integration-review.md").write_text(redact("\n".join(lines)), encoding="utf-8")


def verification_registration(source: RepositoryGateway, directory: Path) -> list[str]:
    entries = source.command("worktree", "list", "--porcelain", "-z").decode().split("\0\0")
    for entry in entries:
        fields = entry.strip("\0").split("\0")
        if fields and fields[0].startswith("worktree ") and Path(fields[0][9:]).resolve() == directory:
            return fields
    return []


def ignored_files(repository: RepositoryGateway) -> bytes:
    return repository.command("ls-files", "--others", "--ignored", "--exclude-standard", "-z")


def discard_verification(run_dir: Path, review: dict, source: RepositoryGateway) -> None:
    intent = review["preparation"]
    directory = unlinked(run_dir / "integration-verification")
    if (intent["verification_dir"] != str(directory) or intent["common_dir"] != str(source.common_dir)
            or intent["source_repo"] != str(source.root)):
        raise SafetyError("Verification ownership intent changed; retain workspace")
    require_test_stopped(review)
    registration = verification_registration(source, directory)
    if not directory.exists() and not registration:
        review["cleanup"] = {"result": "verification_absent"}
        return
    if not directory.exists() or not registration or "detached" not in registration:
        raise SafetyError("Verification worktree registration is missing or changed; retain it")
    if "registration" in intent and registration != intent["registration"]:
        raise SafetyError("Verification registration changed; retain it")
    if not intent.get("commit"):
        raise SafetyError("No recorded sealed commit owns the existing verification directory")
    owned = RepositoryGateway(directory, source.audit)
    if (owned.common_dir != source.common_dir or owned.head != intent["commit"]
            or owned.command("symbolic-ref", "-q", "HEAD", check=False)):
        raise SafetyError("Verification workspace identity changed; retain it")
    snapshot = owned.snapshot()
    owned.require_clean(snapshot)
    actual_ignored = ignored_files(owned)
    if intent.get('environment_origin_sha256'):
        from .integration_environment import ignored_match
        ignored_valid = ignored_match(run_dir, intent, actual_ignored)
    else:
        ignored_valid = not actual_ignored
    if not ignored_valid or ("verification_snapshot" in intent and snapshot != intent["verification_snapshot"]):
        raise SafetyError("Verification files/index/metadata changed; retain them")
    manifest = inventory(directory)
    # Git status does not record empty directories. Before the first complete
    # checkpoint, only directories needed by tracked files may be removed.
    tracked = owned.command("ls-files", "-z").decode().rstrip("\0").split("\0")
    expected_dirs = {parent.as_posix() for name in tracked for parent in Path(name).parents
                     if parent.as_posix() != "."}
    if intent.get('environment_origin_sha256'):
        from .integration_environment import dependency_directories
        expected_dirs |= dependency_directories(run_dir, intent)
    if any(row.get("directory") and name not in expected_dirs for name, row in manifest.items()):
        raise SafetyError("Verification contains unrecorded directories; retain them")
    if "verification_inventory" in intent and manifest != intent["verification_inventory"]:
        raise SafetyError("Verification filesystem changed; retain it")
    review["operation_stage"] = "cleanup_intent"
    save_review(run_dir, review)
    source.command("worktree", "remove", "--force", str(directory))
    review.update(operation_stage="verification_removed", test_inflight=None,
                  cleanup={"result": "verification_removed"})
    save_review(run_dir, review)


def archive_attempt(run_dir: Path, review: dict) -> None:
    identity = review["preparation"]["attempt_id"]
    if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
        raise SafetyError("Invalid preparation attempt identity")
    archive = unlinked(run_dir / "integration-attempts" / identity)
    archive.mkdir(parents=True, exist_ok=True)
    for name in ("integration-review.json", "integration-review.md", "integration.diff", "run.json", "report.md"):
        path = unlinked(run_dir / name)
        if path.exists():
            target = unlinked(archive / name)
            content = path.read_bytes()
            if target.exists():
                if target.read_bytes() != content:
                    raise SafetyError("Archived attempt differs; never overwrite its evidence")
            else:
                with target.open("xb") as stream:
                    stream.write(content)


def recover_integration(run_dir: Path) -> dict:
    """Abort interrupted preparation; never reuse partial tests as publication authority."""
    with operation_lock(run_dir) as directory:
        review_path = unlinked(directory / "integration-review.json")
        review = json.loads(review_path.read_text(encoding="utf-8"))
        intent = review.get("preparation")
        if (not intent or review.get("preparation_sha256") != fingerprint(intent)
                or intent.get("run_id") != directory.name):
            raise SafetyError("No matching preparation journal; legacy runs require manual ownership inspection")
        if review.get("status") not in {"preparing", "recovering", "cleanup_required", "failed", "aborted"}:
            raise SafetyError("Recovery only aborts unfinished or failed preparations; preserve ready/published reviews")
        record, source, candidate, _ = load_candidate(directory)
        if (intent["base_sha"] != record["base_sha"] or intent["candidate_snapshot"] != candidate.snapshot()
                or intent["source_repo"] != str(source.root) or intent["common_dir"] != str(source.common_dir)):
            raise SafetyError("Candidate/source identity changed since preparation; retain all evidence")
        require_source(record, source, "integration/" + record["run_id"])
        require_test_stopped(review)
        if review["status"] == "aborted":
            if (directory / "integration-verification").exists() or verification_registration(source, directory / "integration-verification"):
                raise SafetyError("Verification target reappeared after recovery; never adopt it")
        else:
            review["status"] = "recovering"
            save_review(directory, review)
            try:
                discard_verification(directory, review, source)
            except Exception as error:
                review.update(status="cleanup_required", cleanup_error=str(error))
                save_review(directory, review)
                raise
            review.update(status="aborted", aborted_at=now(), test_inflight=None)
            review["events"].append({"time": now(), "decision": "abort_preparation", "attempt_id": intent["attempt_id"]})
            save_review(directory, review)
        record["integration_preparation"] = {"status": "aborted", "attempt_id": intent["attempt_id"],
                                              "cleanup": review["cleanup"], "temporary_index_retained": intent.get("temporary_index_dir")}
        if not any(e.get("stage") == "integration_preparation_aborted" and e.get("attempt_id") == intent["attempt_id"]
                   for e in record["events"]):
            record["events"].append({"time": now(), "stage": "integration_preparation_aborted", "attempt_id": intent["attempt_id"]})
        RunStore(directory, record).save()
        return review


def load_candidate(run_dir: Path) -> tuple[dict, RepositoryGateway, RepositoryGateway, Policy]:
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    has_environment = unlinked(run_dir / 'environment/environment.json').exists() or any(
        'environment_sha256' in test for test in record.get('candidate_tests', []))
    if record.get('environment') is None and has_environment:
        raise SafetyError('Original environment metadata missing; legacy runtime downgrade is disabled')
    identity = record["run_id"]
    if run_dir.name != identity or len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
        raise SafetyError("Invalid run identity")
    workspace = run_dir / "workspace"
    if record["branch"] != "maintenance/" + identity or Path(record["workspace"]).resolve() != workspace:
        raise SafetyError("Invalid candidate identity")
    if record["status"] not in {"candidate_ready", "integrated"} or not record.get("source_unchanged"):
        raise SafetyError("Only a verified successful candidate can be integrated")
    source = RepositoryGateway(Path(record["source_repo"]), record.setdefault("git_commands", []))
    if run_dir.is_relative_to(source.root) or run_dir.is_relative_to(source.common_dir):
        raise SafetyError("Integration evidence must be outside the source and Git metadata")
    candidate = RepositoryGateway(workspace, record["git_commands"])
    if (source.common_dir != candidate.common_dir or candidate.head != record["base_sha"]
            or candidate.command("symbolic-ref", "--short", "HEAD").decode().strip() != record["branch"]):
        raise SafetyError("Candidate ownership, branch or baseline changed")
    p = record["policy"]
    policy = Policy(tuple(p["allowed_paths"]), tuple(tuple(c) for c in p["allowed_tests"]),
                    p["timeout_seconds"], tuple(p.get("readonly_paths", [])))
    policy.validate_scope()
    expected = {}
    for item in record.get("rounds", []):
        plan = decode_plan(item)
        policy.validate(plan)
        if (item["plan_sha256"] != plan.fingerprint or plan.tests != policy.allowed_tests
                or plan.base_sha != record["base_sha"] or plan.issue != record["issue"]
                or item.get("authorization", {}).get("plan_sha256") != plan.fingerprint
                or item["authorization"]["decision"] not in {"approve", "policy_scope"}):
            raise SafetyError("Candidate has no matching edit authorization")
        for edit in plan.edits:
            previous = expected.get(edit.path)
            if previous is None:
                previous = source.command("show", record["base_sha"] + ":" + edit.path)
            if digest(previous) != edit.before_sha256:
                raise SafetyError("Recorded patch chain changed")
            expected[edit.path] = edit.content.encode("utf-8")
    if not expected or record["rounds"][-1].get("verification") != "passed":
        raise SafetyError("Candidate has no passed final verification")
    for relative, content in expected.items():
        if candidate.read(relative) != content:
            raise SafetyError("Candidate changed after verification")
    if candidate.diff() != record["diff"] or not record["diff"]:
        raise SafetyError("Candidate diff changed after verification")
    changed = candidate.command("diff", "--name-only", "-z", "HEAD").decode().rstrip("\0").split("\0")
    if (not set(changed) <= set(expected)
            or candidate.command("ls-files", "--others", "--exclude-standard")):
        raise SafetyError("Candidate contains unapproved changes")
    if record.get('environment') is not None:
        from .integration_environment import load_origin
        load_origin(run_dir, record, source, candidate, policy)  # No runtime or install.
    return record, source, candidate, policy


def require_source(record: dict, source: RepositoryGateway, target: str, published: bool = False) -> None:
    exclusions = (record["branch"], target) if published else (record["branch"],)
    if source.snapshot(exclude_branches=exclusions) != record["source_before"]:
        raise SafetyError("Source baseline or workspace changed; do not integrate")
    if not record["source_before"]["branch"].startswith("refs/heads/"):
        raise SafetyError("Integration requires a named source branch")


def prepare_integration(run_dir: Path, retry_aborted: bool = False) -> dict:
    with operation_lock(run_dir) as directory:
        return _prepare_integration(directory, retry_aborted)


def _prepare_integration(run_dir: Path, retry_aborted: bool) -> dict:
    run_dir = run_dir.resolve()
    unlinked(run_dir / "integration-review.json")
    if (run_dir / "integration-review.json").exists():
        if not retry_aborted:
            raise SafetyError("Integration review already exists; preserve it and inspect its recorded state")
        old = json.loads((run_dir / "integration-review.json").read_text(encoding="utf-8"))
        if old.get("status") != "aborted" or old.get("preparation", {}).get("run_id") != run_dir.name:
            raise SafetyError("Only an explicitly recovered aborted preparation can be retried")
        if old.get("preparation_sha256") != fingerprint(old["preparation"]):
            raise SafetyError("Aborted preparation journal changed; retain its evidence")
        if (run_dir / "integration-verification").exists():
            raise SafetyError("Verification workspace remains; recover it before retrying")
        require_test_stopped(old)
    elif retry_aborted:
        raise SafetyError("There is no aborted preparation to retry")
    record, source, candidate, policy = load_candidate(run_dir)
    target = "integration/" + record["run_id"]
    require_source(record, source, target)
    verification_dir = run_dir / "integration-verification"
    unlinked(verification_dir)
    if verification_dir.exists() or verification_registration(source, verification_dir):
        raise SafetyError("Verification target already exists or is registered; never adopt it")
    if retry_aborted:
        archive_attempt(run_dir, old)
    snapshot = candidate.snapshot()
    intent = {"attempt_id": uuid.uuid4().hex, "run_id": record["run_id"], "source_repo": str(source.root),
              "common_dir": str(source.common_dir), "verification_dir": str(verification_dir),
              "candidate_snapshot": snapshot, "base_sha": record["base_sha"]}
    review = {"schema_version": 2, "status": "preparing", "operation_stage": "intent", "started_at": now(),
              "events": [], "preparation": intent}
    save_review(run_dir, review)
    environment = None
    try:
        if record.get('environment') is not None:
            from .integration_environment import SealedEnvironment
            environment = SealedEnvironment(run_dir, record, source, candidate, policy, review,
                                            lambda: save_review(run_dir, review))
            intent['environment_origin_sha256'] = environment.origin['receipt_sha256']
            save_review(run_dir, review)
        with tempfile.TemporaryDirectory(prefix="seal-", dir=run_dir) as temporary:
            intent["temporary_index_dir"] = temporary
            save_review(run_dir, review)
            index = Path(temporary) / "index"
            source.command("read-tree", record["base_sha"], index_file=index)
            paths = candidate.command("diff", "--name-only", "-z", "HEAD").split(b"\0")
            for raw in filter(None, paths):
                relative = raw.decode()
                entry = source.command("ls-tree", "-z", record["base_sha"], "--", relative)
                mode = entry.split(b" ", 1)[0].decode()
                if mode not in {"100644", "100755"}:
                    raise SafetyError("Integration supports existing regular tracked files only")
                blob = source.command("hash-object", "-w", "--stdin", input_data=candidate.read(relative)).decode().strip()
                source.command("update-index", "--cacheinfo", mode, blob, relative, index_file=index)
            tree = source.command("write-tree", index_file=index).decode().strip()
        commit = source.command("-c", "user.name=Maintenance Agent", "-c", "user.email=agent@example.invalid",
                                "commit-tree", tree, "-p", record["base_sha"], "-m",
                                "maintenance: verified candidate " + record["run_id"]).decode().strip()
        sealed_diff = source.command("diff", "--no-ext-diff", "--no-textconv", record["base_sha"], commit, "--").decode()
        if sealed_diff != record["diff"]:
            raise SafetyError("Sealed commit differs from the verified candidate")
        unlinked(run_dir / "integration.diff").write_text(redact(sealed_diff), encoding="utf-8")
        intent.update(commit=commit, tree=tree, diff_sha256=digest(sealed_diff.encode()))
        review["operation_stage"] = "worktree_add_intent"
        save_review(run_dir, review)  # Own the exact path/commit before Git creates it.
        source.command("worktree", "add", "--detach", source.worktree_path_argument(verification_dir), commit)
        sealed = RepositoryGateway(verification_dir, record["git_commands"])
        before_tests = sealed.snapshot()
        intent["verification_snapshot"] = before_tests
        intent["verification_inventory"] = inventory(verification_dir)
        intent["registration"] = verification_registration(source, verification_dir)
        review["operation_stage"] = "verification_ready"
        save_review(run_dir, review)
        if environment is not None:
            environment.attach(sealed)
            intent['verification_ignored'] = ignored_files(sealed).hex()
            save_review(run_dir, review)
        tests = []
        for command in policy.allowed_tests:
            review["operation_stage"] = "test_starting"
            save_review(run_dir, review)
            def started(pid):
                review.update(operation_stage="test_running", test_inflight=process_identity(pid))
                save_review(run_dir, review)
            if environment is not None:
                tests.append(environment.run_check(command, timeout=policy.timeout_seconds))
            else:
                tests.append(run_test(command, verification_dir, policy.timeout_seconds, on_start=started))
            review.update(operation_stage="test_finished", test_inflight=None, verification_tests=tests)
            save_review(run_dir, review)
        review["verification_tests"] = tests
        if any(test["returncode"] != 0 or test["timed_out"] or
               (environment is not None and test.get('category') != 'passed') for test in tests):
            review["failed_tests"] = tests
            raise SafetyError("Sealed commit verification failed")
        expected_ignored = bytes.fromhex(intent.get('verification_ignored', '')) if environment is not None else b''
        if (sealed.snapshot() != before_tests or ignored_files(sealed) != expected_ignored
                or inventory(verification_dir) != intent["verification_inventory"]):
            raise SafetyError("Sealed verification modified files or Git metadata")
        if candidate.snapshot() != snapshot:
            raise SafetyError("Candidate changed while sealing and verifying")
        require_source(record, source, target)
        core = {"run_id": record["run_id"], "preparation_attempt_id": intent["attempt_id"],
                "source_repo": str(source.root), "base_sha": record["base_sha"],
                "source_before": record["source_before"], "target_branch": target, "tree": tree, "commit": commit,
                "diff_sha256": digest(sealed_diff.encode()), "candidate_snapshot": snapshot,
                "policy": record["policy"], "edit_plan_sha256": record["plan_sha256"], "tests": tests}
        if environment is not None:
            core['environment_verification'] = environment.summary(tests)
        review.update(review=core, review_sha256=fingerprint(core), finished_at=now())
    except BaseException as error:
        review.update(status="failed", error=f"{type(error).__name__}: {error}", finished_at=now())
        save_review(run_dir, review)
        raise
    finally:
        try:
            discard_verification(run_dir, review, source)
            if review["status"] == "preparing":
                review["status"] = "prepared"
        except Exception as error:
            review.update(status="cleanup_required", cleanup_error=str(error))
        save_review(run_dir, review)
        record["integration_preparation"] = {"status": review["status"], "attempt_id": intent["attempt_id"],
                                              "cleanup": review.get("cleanup"), "error": review.get("cleanup_error", review.get("error"))}
        RunStore(run_dir, record).save()
    return review


def integrate(run_dir: Path, approve_sha256: str | None = None, reject: bool = False,
              actor: str = "local-integration-operator") -> dict:
    with operation_lock(run_dir) as directory:
        return _integrate(directory, approve_sha256, reject, actor)


def _integrate(run_dir: Path, approve_sha256: str | None, reject: bool, actor: str) -> dict:
    run_dir = run_dir.resolve()
    review, core, expected = _read_review(run_dir)
    if approve_sha256 and approve_sha256 != expected:
        raise SafetyError("Integration approval fingerprint is stale or invalid")
    if review["status"] not in {"prepared", "awaiting_approval", "rejected", "publishing", "published"}:
        raise SafetyError("Integration review is not ready")
    if approve_sha256 and reject:
        raise SafetyError("Integration decisions conflict")
    if not approve_sha256 or reject:
        if review["status"] in {"publishing", "published"}:
            raise SafetyError("Publication already started; inspect/recover its existing authorization")
        decision = "reject" if reject else "pending"
        review["events"].append({"actor": actor, "time": now(), "decision": decision, "review_sha256": expected})
        review["status"] = "rejected" if reject else "awaiting_approval"
        save_review(run_dir, review)
        return review
    record, source, candidate, policy = _verify_review_candidate(run_dir, review)
    target = core["target_branch"]
    ref = "refs/heads/" + target
    published = source.command("show-ref", "--verify", "--hash", ref, check=False).decode().strip()
    recovering = review["status"] in {"publishing", "published"}
    if recovering and (review.get("authorization", {}).get("review_sha256") != expected
                       or review["authorization"].get("decision") != "approve"):
        raise SafetyError("Publication recovery requires its durable matching authorization")
    if published and (not recovering or published != core["commit"]):
        raise SafetyError("Integration target already exists or changed; never overwrite it")
    if review["status"] == "published" and not published:
        raise SafetyError("Published target was removed; do not recreate it automatically")
    require_source(record, source, target, published=bool(published))
    if not published:
        review.update(status="publishing", authorization={"actor": actor, "time": now(),
                      "decision": "approve", "review_sha256": expected})
        review["events"].append(review["authorization"])
        save_review(run_dir, review)  # Write-ahead journal before the only ref mutation.
        transaction = ("start\nverify " + core["source_before"]["branch"] + " " + core["base_sha"]
                       + "\ncreate " + ref + " " + core["commit"] + "\nprepare\ncommit\n")
        source.command("update-ref", "--no-deref", "--stdin", input_data=transaction.encode())
    # Any source/candidate race after publication is reported; never reset the user's work.
    require_source(record, source, target, published=True)
    if candidate.snapshot(exclude_branch=target) != core["candidate_snapshot"]:
        raise SafetyError("Candidate changed during publication; retain branch for inspection")
    review.update(status="published", published_at=now(), source_unchanged=True)
    save_review(run_dir, review)
    record.update(integration="published_branch", integration_branch=target, integration_commit=core["commit"],
                  integration_review_sha256=expected, source_unchanged=True,
                  source_after=source.snapshot(exclude_branches=(record["branch"], target)), finished_at=now())
    RunStore(run_dir, record).event("integrated", target_branch=target, commit=core["commit"])
    return review


def _read_review(run_dir: Path):
    unlinked(run_dir / "integration-review.json")
    review = json.loads((run_dir / "integration-review.json").read_text(encoding="utf-8"))
    if "review" not in review:
        raise SafetyError("Integration review is not ready")
    core = review["review"]
    if "preparation" in review and (review.get("preparation_sha256") != fingerprint(review["preparation"])
            or core.get("preparation_attempt_id") != review["preparation"].get("attempt_id")):
        raise SafetyError("Integration preparation identity changed")
    expected = fingerprint(core)
    if review.get("review_sha256") != expected:
        raise SafetyError("Integration approval fingerprint is stale or invalid")
    return review, core, expected


def _verify_review_candidate(run_dir: Path, review: dict):
    core = review['review']
    record, source, candidate, policy = load_candidate(run_dir)
    target = "integration/" + record["run_id"]
    if (core["run_id"] != record["run_id"] or core["target_branch"] != target
            or core["source_repo"] != str(source.root) or core["source_before"] != record["source_before"]
            or core["base_sha"] != record["base_sha"] or core["policy"] != record["policy"]
            or core["edit_plan_sha256"] != record["plan_sha256"]
            or core["diff_sha256"] != digest(record["diff"].encode())):
        raise SafetyError("Integration review no longer matches the candidate run")
    if len(core["tests"]) != len(policy.allowed_tests) or any(
            t["returncode"] != 0 or t["timed_out"] for t in core["tests"]):
        raise SafetyError("Integration has no complete passed verification")
    if record.get('environment') is not None:
        from .integration_environment import verify_publication
        verify_publication(run_dir, record, source, candidate, policy, review)
    if candidate.snapshot(exclude_branch=target) != core["candidate_snapshot"]:
        raise SafetyError("Candidate state changed after integration review")
    if (source.command("rev-parse", core["commit"] + "^{tree}").decode().strip() != core["tree"]
            or source.command("rev-list", "--parents", "-n", "1", core["commit"]).decode().strip()
            != core["commit"] + " " + core["base_sha"]
            or digest(source.command("diff", "--no-ext-diff", "--no-textconv", core["base_sha"],
                                     core["commit"], "--")) != core["diff_sha256"]):
        raise SafetyError("Sealed commit identity changed")
    return record, source, candidate, policy


def verified_publication(run_dir: Path) -> dict:
    """Read-only proof of an already approved local publication; never republishes."""
    with operation_lock(run_dir) as directory:
        review, core, expected = _read_review(directory)
        if (review.get('status') != 'published'
                or review.get('authorization', {}).get('decision') != 'approve'
                or review['authorization'].get('review_sha256') != expected):
            raise SafetyError('An already approved local publication is required')
        record, source, _, _ = _verify_review_candidate(directory, review)
        if (record.get('integration') != 'published_branch'
                or record.get('integration_branch') != core['target_branch']
                or record.get('integration_commit') != core['commit']
                or record.get('integration_review_sha256') != expected
                or source.command('show-ref', '--verify', '--hash', 'refs/heads/'+core['target_branch'],
                                  check=False).decode().strip() != core['commit']):
            raise SafetyError('Local publication record/ref changed; never recreate it')
        require_source(record, source, core['target_branch'], published=True)
        return {'record': record, 'review': core, 'review_sha256': expected}
