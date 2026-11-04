from __future__ import annotations

import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .git import RepositoryGateway
from .models import Plan, Policy, Provider, SafetyError, digest
from .runner import run_test
from .store import RunStore, now
from .recovery import operation_lock, unlinked
from .repair_recovery import ExecutionJournal
from .check_comparison import check_outcomes, compare_checks
from .maintenance_limits import LIMITS


def capture_plan(plan: Plan, provider: Provider, number: int) -> dict:
    import copy
    return {"number": number, "plan": asdict(plan), "plan_sha256": plan.fingerprint,
            "provider_evidence": copy.deepcopy(getattr(provider, "evidence", {})), "tests": []}


def plan_repair(repo: Path, issue: str, provider: Provider, policy: Policy) -> Plan:
    repository = RepositoryGateway(repo)
    repository.require_clean(repository.snapshot())
    policy.validate_scope()
    from .workspace_inventory import tracked_capacity
    if tracked_capacity(repository)['status'] != 'fits':
        raise SafetyError('Candidate capacity preflight failed before model planning')
    plan = provider.plan(repository, issue, policy.allowed_tests)
    policy.validate(plan)
    if plan.issue != issue or plan.tests != policy.allowed_tests:
        raise SafetyError("Plan changed issue or required regression tests")
    if plan.base_sha != repository.head:
        raise SafetyError("Plan baseline no longer matches repository")
    return plan


def repair(repo: Path, issue: str, provider: Provider, policy: Policy, mode: str,
           runs_dir: Path, approval: Callable[[Plan], str] | None = None,
           actor: str = "local-operator", max_attempts: int = 1, run_id: str | None = None,
           freshness_guard=None, operation_control=None, completion_hook=None, environment_factory=None,
           issue_binding=None, behavior_contract=None) -> dict:
    if issue_binding is not None:
        fields={'execution_id':32,'issue_token':64,'policy_binding_sha256':64,'task_id':32,'rules_sha256':64}
        if (not isinstance(issue_binding,dict) or set(issue_binding)!={*fields,'issue_number'}
                or type(issue_binding['issue_number']) is not int or not 1<=issue_binding['issue_number']<=9999999999
                or any(not isinstance(issue_binding[k],str) or len(issue_binding[k])!=length
                       or any(c not in '0123456789abcdef' for c in issue_binding[k]) for k,length in fields.items())):
            raise SafetyError('Invalid controller Issue/run binding')
    if mode not in {"approval", "auto"}:
        raise SafetyError("Mode must be approval or auto")
    if type(max_attempts) is not int or not 1 <= max_attempts <= LIMITS['candidate_max_rounds']:
        raise SafetyError("Repair attempts exceed configured limit")
    repository = RepositoryGateway(repo)
    from .workflow_behavior import is_behavior, validate_scope
    if behavior_contract is None and environment_factory is not None:
        behavior_contract = getattr(environment_factory, 'plan', {}).get('behavior_contract')
    if behavior_contract is not None:
        validate_scope(behavior_contract, repository, repository.head, issue=issue, policy=policy)
        if environment_factory is None:
            raise SafetyError('Behavior checks require an explicitly granted target environment')
    elif any(is_behavior(c) for c in policy.allowed_tests):
        raise SafetyError('Controller behavior check requires its pinned contract before planning')
    destination = unlinked(runs_dir)
    if destination.is_relative_to(repository.root) or destination.is_relative_to(repository.common_dir):
        raise SafetyError("Reports/workspaces must be outside source checkout and Git metadata")
    run_id = run_id or uuid.uuid4().hex
    if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise SafetyError("Invalid reserved run identity")
    run_dir = destination / run_id
    if run_dir.exists():
        raise SafetyError("Reserved run directory already exists; never replay a repair")
    workspace = run_dir / "workspace"
    branch = "maintenance/" + run_id
    record = {"schema_version": 2, "run_id": run_id, "started_at": now(),
              "source_repo": str(repository.root), "mode": mode, "issue": issue,
              "policy": asdict(policy), "events": [], "baseline_tests": [], "candidate_tests": [],
              "workspace": str(workspace), "branch": branch, "integration": "disabled",
              "git_commands": [], "rounds": [], "max_attempts": max_attempts}
    if issue_binding is not None:
        record['issue_execution']=dict(issue_binding)  # Durable before planning/Git/model side effects.
    if behavior_contract is not None:
        import copy
        record['behavior_validation'] = {'contract': copy.deepcopy(behavior_contract),
                                        'contract_sha256': behavior_contract['contract_sha256']}
    if getattr(provider, "planning_origin", None):
        record["planning_origin"] = provider.planning_origin
    repository.audit = record["git_commands"]
    store = RunStore(run_dir, record)
    before = repository.snapshot()
    record["source_before"] = before
    # The source baseline exists before a model returns any plan. Persist it
    # here so a settled no-plan turn can still be checked against its original
    # controller/Native scope; do not infer it later from a failed report.
    record["base_sha"] = before["head"]
    store.event("inspecting")
    from .pi_behavior import bound_expectations
    with operation_lock(run_dir), bound_expectations(provider, behavior_contract, repository):
        return _execute_repair(repository, provider, policy, mode, approval, actor, max_attempts, store, before, freshness_guard, operation_control, completion_hook, environment_factory)


def _execute_repair(repository, provider, policy, mode, approval, actor, max_attempts, store, before, freshness_guard=None, operation_control=None, completion_hook=None, environment_factory=None):
    record, run_dir = store.record, store.directory
    workspace, branch, issue = Path(record["workspace"]), record["branch"], record["issue"]
    candidate = None
    retain = False
    journal = ExecutionJournal(store, repository)
    environment = None
    def phase(value):
        if operation_control:
            operation_control(value)
    try:
        repository.require_clean(before)
        policy.validate_scope()
        from .workspace_inventory import tracked_capacity
        phase('capacity_preflight')
        record['candidate_preflight'] = tracked_capacity(repository)
        store.save()
        if record['candidate_preflight']['status'] != 'fits':
            raise SafetyError('Candidate capacity preflight blocked before model planning; preserve evidence')
        phase('planning')
        journal.phase("planning")
        plan = provider.plan(repository, issue, policy.allowed_tests)
        phase('reviewing_plan')
        policy.validate(plan)
        if plan.issue != issue or plan.tests != policy.allowed_tests:
            raise SafetyError("Plan changed issue or required regression tests")
        if plan.base_sha != before["head"]:
            raise SafetyError("Provider returned a stale baseline")
        record.update(plan=asdict(plan), plan_sha256=plan.fingerprint, base_sha=plan.base_sha)
        record["provider_evidence"] = getattr(provider, "evidence", {})
        first_round = capture_plan(plan, provider, 1)
        record["rounds"].append(first_round)
        store.event("planned")
        journal.phase("planned")
        if mode=='auto' and record['provider_evidence'].get('repeated_failed_patch'):
            raise SafetyError('Repeated verified failed patch; automatic execution is blocked')

        def authorize(current_plan: Plan, current_round: dict) -> bool:
            decision = (approval(current_plan) if approval else "pending") if mode == "approval" else "policy_scope"
            if decision not in ({"approve", "reject", "pending"} if mode == "approval" else {"policy_scope"}):
                raise SafetyError("Invalid approval decision")
            event = {"actor": actor, "time": now(), "decision": decision,
                     "plan_sha256": current_plan.fingerprint}
            if mode == "auto":
                event["scope"] = asdict(policy)
            current_round["authorization"] = event
            record["authorization"] = event
            store.save()
            if decision in {"reject", "pending"}:
                record["approval_outcome"] = decision
                store.event("rejected" if decision == "reject" else "awaiting_approval",
                            round=current_round["number"])
                return False
            return True

        if not authorize(plan, first_round):
            return record
        # Re-check after an arbitrarily long human approval wait.
        if repository.snapshot() != before:
            raise SafetyError("Source changed during planning/approval; re-plan before editing")
        store.event("creating_workspace")
        phase('creating_workspace')
        journal.phase("workspace_add_intent")
        candidate = repository.create_workspace(workspace, branch, plan.base_sha)
        baseline_snapshot = candidate.snapshot()
        candidate.require_clean(baseline_snapshot)
        if environment_factory is not None:
            from .workflow_environment import JournaledEnvironment
            phase('environment_preparing')
            if freshness_guard:
                freshness_guard()
            record['environment']={'status':'preparing','receipt':'environment/environment.json'}
            store.save()
            journal.phase('environment_preparing')
            environment=environment_factory(repository,candidate,run_dir,journal)
            if (not isinstance(environment,JournaledEnvironment) or environment.journal is not journal
                    or environment.receipt['status']!='prepared'):
                raise SafetyError('Expected a prepared, granted target environment')
            if (environment.workspace.root!=workspace or environment.plan['head_sha']!=plan.base_sha
                    or any(list(c) not in [p['argv'] for p in environment.plan['checks']] for c in policy.allowed_tests)):
                raise SafetyError('Prepared environment differs from original repair scope')
            if record.get('behavior_validation') is not None and environment.plan.get('behavior_contract') != record['behavior_validation']['contract']:
                raise SafetyError('Prepared environment changed the approved behavior contract')
            if candidate.snapshot()!=baseline_snapshot:
                raise SafetyError('Environment preparation changed repository files or Git metadata')
            record['environment'].update(status='prepared',binding=environment.binding,
                environment_sha256=environment.receipt['environment_sha256'])
            store.save()
        journal.checkpoint(candidate)
        store.event("baseline_testing")
        for command in plan.tests:
            phase('baseline_testing')
            if environment is None:
                journal.start("baseline_test")
                result = run_test(command, workspace, policy.timeout_seconds, on_start=journal.pid)
                journal.done(result)
            else:
                result = environment.run_check(command,workspace,policy.timeout_seconds,behavior_phase='baseline')
            record["baseline_tests"].append(result)
            store.save()
            if environment is not None and result['category'] not in {'passed','test_failed'}:
                raise SafetyError('Baseline environment/check is inconclusive: '+result['category'])
            if result["timed_out"]:
                raise SafetyError("Baseline test timed out")
        if candidate.snapshot()["status"]:
            raise SafetyError("Baseline tests modified repository files")
        if candidate.snapshot() != baseline_snapshot:
            raise SafetyError("Baseline tests modified Git metadata")
        if all(result["returncode"] == 0 for result in record["baseline_tests"]):
            raise SafetyError("Baseline already passes; no reproduced failing test")
        expected_files = {}
        seen_diffs = set()
        for attempt in range(1, max_attempts + 1):
            current_round = record["rounds"][-1]
            if repository.snapshot(exclude_branch=branch) != before:
                raise SafetyError("Source checkout changed; candidate cannot be accepted")
            store.event("editing", round=attempt)
            phase('editing')
            if freshness_guard:
                freshness_guard()
            expected_inventory = journal.edited_inventory(plan.edits)
            journal.phase("editing")
            # Check every precondition before applying any file from the approved plan.
            for edit in plan.edits:
                if digest(candidate.read(edit.path)) != edit.before_sha256:
                    raise SafetyError("File content does not match approved patch precondition")
                if candidate.read(edit.path) == edit.content.encode("utf-8"):
                    raise SafetyError("No progress: proposed edit is unchanged")
            for edit in plan.edits:
                candidate.safe_file(edit.path).write_bytes(edit.content.encode("utf-8"))
                expected_files[edit.path] = edit.content.encode("utf-8")
            record["diff"] = candidate.diff()
            current_round["diff"] = record["diff"]
            signature = digest(record["diff"].encode())
            if not record["diff"] or signature in seen_diffs:
                raise SafetyError("Candidate has no tracked diff or repeats an earlier candidate")
            seen_diffs.add(signature)
            before_tests = candidate.snapshot()
            journal.checkpoint(candidate, expected_inventory)
            record["candidate_tests"] = current_round["tests"]
            store.event("candidate_testing", round=attempt)
            for command in policy.allowed_tests:
                phase('candidate_testing')
                if environment is None:
                    journal.start("candidate_test")
                    result = run_test(command, workspace, policy.timeout_seconds, on_start=journal.pid)
                    journal.done(result)
                else:
                    result = environment.run_check(command,workspace,policy.timeout_seconds)
                current_round["tests"].append(result)
                store.save()
                if environment is not None and result['category'] not in {'passed','test_failed'}:
                    raise SafetyError('Candidate environment/check is inconclusive: '+result['category'])
            paths = set(candidate.command("diff", "--name-only", "-z", "HEAD").decode().rstrip("\0").split("\0"))
            untracked = candidate.command("ls-files", "--others", "--exclude-standard")
            if not paths <= set(expected_files) or untracked:
                raise SafetyError("Tests changed files outside the approved patch")
            for path, content in expected_files.items():
                if candidate.read(path) != content:
                    raise SafetyError("Tests changed approved patch contents")
            if candidate.diff() != record["diff"] or candidate.snapshot() != before_tests:
                raise SafetyError("Tests changed approved patch metadata")
            if repository.snapshot(exclude_branch=branch) != before:
                raise SafetyError("Source checkout changed; candidate cannot be accepted")
            comparison = compare_checks(record["baseline_tests"], current_round["tests"])
            if environment is not None:
                from .target_environment import compare_environment_checks
                current_round['environment_comparison']=compare_environment_checks(record['baseline_tests'],current_round['tests'])
                if not current_round['environment_comparison']['comparable']:
                    raise SafetyError('Baseline/candidate target environments are not comparable')
            current_round["check_comparison"] = comparison
            failed = not comparison["all_candidate_checks_passed"]
            if not failed and record.get('behavior_validation') is not None:
                from .workflow_behavior import verify_pair
                current_round['behavior_comparison'] = verify_pair(record, repository, policy, candidate)
            current_round["verification"] = "failed" if failed else "passed"
            store.save()
            if not failed:
                phase('verifying_completion')
                if freshness_guard:
                    freshness_guard()
                retain = True
                store.event("candidate_ready", round=attempt, message="Tests passed; retained for separate review")
                journal.phase("candidate_ready_pending_finish")
                break
            if any(t["timed_out"] for t in current_round["tests"]):
                raise SafetyError("Candidate verification failed: timeout")
            if attempt >= max_attempts or not hasattr(provider, "revise"):
                raise SafetyError("Candidate verification failed: repair budget exhausted")
            store.event("replanning", round=attempt + 1)
            phase('replanning')
            journal.phase("replanning")
            feedback_tests,feedback_baseline=current_round["tests"],record["baseline_tests"]
            if environment is not None:
                feedback_tests=environment.revision_checks(feedback_tests,policy.allowed_tests)
                feedback_baseline=environment.revision_checks(feedback_baseline,policy.allowed_tests)
            feedback = {"round": attempt, "previous_plan_sha256": plan.fingerprint,
                        "diff": record["diff"], "tests": feedback_tests,
                        "baseline_checks": check_outcomes(feedback_baseline)}
            review_snapshot = candidate.snapshot()
            try:
                plan = provider.revise(candidate, issue, policy.allowed_tests, feedback)
                policy.validate(plan)
                if plan.base_sha != before["head"] or plan.issue != issue or plan.tests != policy.allowed_tests:
                    raise SafetyError("Revised plan changed baseline, issue or test scope")
                next_round = capture_plan(plan, provider, attempt + 1)
                next_round["feedback_from_round"] = attempt
                record["rounds"].append(next_round)
                record.update(plan=asdict(plan), plan_sha256=plan.fingerprint,
                              provider_evidence=next_round["provider_evidence"])
                store.event("planned", round=attempt + 1)
                journal.phase("planned")
                authorized = authorize(plan, next_round)
            finally:
                # Preserve concurrent user edits even when approval rejects or planning fails.
                if candidate.snapshot() != review_snapshot:
                    retain = True
                    record["diff"] = candidate.diff()
                    record["recovery"] = {"result": "candidate_retained_due_to_concurrent_change", "source_reset": False}
                    raise SafetyError("Candidate changed during planning/approval; re-plan before editing")
            if not authorized:
                return record
    except KeyboardInterrupt:
        record["error"] = "Operator interrupted execution"
        store.event("interrupted")
    except Exception as error:
        from .job_control import OperationStopped
        if record.get('environment',{}).get('status')=='preparing':
            record['environment']['status']='environment_blocked'
        if isinstance(error, OperationStopped):
            record['operator_stop'] = True
        record["error"] = f"{type(error).__name__}: {error}"
        record["provider_evidence"] = getattr(provider, "evidence", {})
        store.event("interrupted" if record.get('operator_stop') else "failed")
    finally:
        # Worktree creation can succeed even if construction/inspection was interrupted.
        if workspace.exists() and not retain:
            try:
                journal.phase("automatic_cleanup")
                record["diff"] = RepositoryGateway(workspace, record["git_commands"]).diff()
                store.save()  # Evidence is durable before cleanup.
                repository.discard_candidate(workspace, branch, before["head"], run_dir)
                record["recovery"] = {"result": "candidate_removed", "source_reset": False}
                store.event("rolled_back")
            except Exception as error:
                record["recovery"] = {"result": "cleanup_failed", "error": str(error)}
                store.event("cleanup_required")
        after = repository.snapshot(exclude_branch=branch if retain else "")
        record.update(source_after=after, source_unchanged=before == after, finished_at=now())
        if not record["source_unchanged"]:
            record["status"] = "source_changed"
        store.save()
        journal.phase("finished")
        if completion_hook:
            completion_hook(record)  # Completed report/execution bytes stable; still under the original operation lock.
    return record


def cleanup_run(run_dir: Path, before_cleanup=None, after_cleanup=None) -> dict:
    """Explicit cleanup/recovery after success or an abrupt process termination."""
    from .recovery import operation_lock, guard_preparation
    with operation_lock(run_dir) as directory:
        guard_preparation(directory)
        from .dashboard import read_json
        if not read_json(directory / "run.json")[0].get("finished_at"):
            raise SafetyError("Interrupted repair requires inspect-repair and explicit recover-repair before cleanup")
        if before_cleanup is not None and before_cleanup() is False:
            return read_json(directory/'run.json')[0]
        result=_cleanup_run(directory)
        if after_cleanup:
            after_cleanup()
        return result


def _cleanup_run(run_dir: Path) -> dict:
    import json

    run_dir = run_dir.resolve()
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    run_id = record["run_id"]
    if run_dir.name != run_id or len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise SafetyError("Invalid run directory identity")
    branch = "maintenance/" + run_id
    workspace = run_dir / "workspace"
    if record["branch"] != branch or Path(record["workspace"]).resolve() != workspace:
        raise SafetyError("Invalid candidate identity")
    repository = RepositoryGateway(Path(record["source_repo"]))
    repository.audit = record.setdefault("git_commands", [])
    if run_dir.is_relative_to(repository.root) or run_dir.is_relative_to(repository.common_dir):
        raise SafetyError("Refuse cleanup within source checkout or Git metadata")
    store = RunStore(run_dir, record)
    if workspace.exists():
        record["diff"] = RepositoryGateway(workspace, record["git_commands"]).diff()
        store.save()
        repository.discard_candidate(workspace, branch, record["base_sha"], run_dir)
    else:
        refs = repository.command("show-ref", "--verify", "--hash", "refs/heads/" + branch, check=False)
        if refs:
            if refs.decode().strip() != record["base_sha"]:
                raise SafetyError("Candidate branch advanced; do not discard")
            worktrees = repository.command("worktree", "list", "--porcelain").decode()
            if f"branch refs/heads/{branch}\n" in worktrees:
                raise SafetyError("Candidate has another worktree; do not discard")
            repository.command("branch", "-d", branch)
    record["recovery"] = {"result": "candidate_removed", "source_reset": False}
    published = record.get("integration_branch", "")
    if published:
        if published != "integration/" + run_id:
            raise SafetyError("Invalid integration branch identity")
        actual = repository.command("show-ref", "--verify", "--hash", "refs/heads/" + published,
                                    check=False).decode().strip()
        if actual != record.get("integration_commit"):
            published = ""  # External changes remain visible in the source comparison.
    after = repository.snapshot(exclude_branch=published)
    record.update(source_after=after, source_unchanged=after == record["source_before"], finished_at=now())
    store.event("discarded" if record["source_unchanged"] else "source_changed")
    return record
