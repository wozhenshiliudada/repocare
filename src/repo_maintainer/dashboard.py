"""Export bounded, allowlisted run evidence to an offline read-only dashboard."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from .models import SafetyError
from .path_safety import is_junction
from .store import now, redact

ASSETS = Path(__file__).with_name("dashboard_assets")
MAX_FILE = 8 * 1024 * 1024
MAX_EXPORT = 24 * 1024 * 1024
MAX_RUNS = 100
RUN_ID = re.compile(r"[0-9a-f]{32}")


def unlinked(path: Path) -> Path:
    # Check every component, including the user-supplied root and output parent.
    absolute = path.absolute()
    for item in (absolute, *absolute.parents):
        if item.is_symlink() or is_junction(item):
            raise SafetyError("Dashboard paths cannot traverse symlinks or junctions")
    return absolute.resolve()


def read_json(path: Path) -> tuple[dict, str]:
    unlinked(path)
    with path.open("rb") as stream:
        content = stream.read(MAX_FILE + 1)
    if len(content) > MAX_FILE:
        raise SafetyError("Run evidence exceeds the 8 MiB file limit")
    data = json.loads(content.decode("utf-8"))
    if not isinstance(data, dict):
        raise SafetyError("Run evidence must be a JSON object")
    return data, hashlib.sha256(content).hexdigest()


def pick(data: dict, keys: str) -> dict:
    if not isinstance(data, dict):
        raise SafetyError("Malformed evidence object")
    return {key: data[key] for key in keys.split() if key in data}


def objects(values: list) -> list:
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise SafetyError("Malformed evidence array")
    return values


def authorization(data: dict) -> dict:
    return pick(data, "decision actor time plan_sha256 review_sha256")


def tests(values: list) -> list:
    rows=[]
    for item in objects(values):
        row=pick(item,"command returncode timed_out duration_seconds output_truncated stdout stderr category tests_run command_kind")
        if item.get('command_kind')=='controller_behavior_contract':
            receipt=item.get('behavior_receipt',{})
            require_receipt=isinstance(receipt,dict) and isinstance(receipt.get('counts'),dict) \
                and isinstance(receipt.get('cases'),list) and 1<=len(receipt['cases'])<=24
            if not require_receipt:
                row.update(category='inconclusive',behavior_receipt={'status':'record_invalid'})
            else:
                # A historical display projection. Never open artifact paths
                # from a record or present this as freshly executed validation.
                summary=pick(receipt,'phase category contract_sha256 environment_sha256 receipt_sha256')
                summary['counts']=pick(receipt['counts'],'total passed failed inconclusive')
                summary['cases']=[pick(case,'id category') for case in objects(receipt['cases'])]
                summary['scope']='recorded_summary_only'
                row['behavior_receipt']=summary
        rows.append(row)
    return rows


def memory_evidence(evidence: dict) -> dict:
    result = pick(evidence, "memory_context_sha256 structure_context_sha256 experience_context_sha256 knowledge_context_sha256 context_policy_sha256 maintenance_context_sha256 maintenance_selection_sha256 memory_load_sha256 memory_task_type")
    if isinstance(evidence.get('memory_load'),dict):
        from .memory_items import validate_load_view
        try:result['memory_load']=validate_load_view(evidence['memory_load'],evidence.get('memory_load_sha256'))
        except (SafetyError,ValueError,KeyError,TypeError):result['memory_load_error']='Recorded load is invalid; source evidence must be inspected'
    if isinstance(evidence.get('knowledge_citations'), list):
        result['knowledge_citations'] = [pick(row, 'id payload_sha256 review_version')
                                        for row in evidence['knowledge_citations'][:3]]
    if isinstance(evidence.get('experience_citations'), list):
        result['experience_citations'] = [pick(row, 'id payload_sha256 review_version')
                                         for row in evidence['experience_citations'][:3]]
    if isinstance(evidence.get('structure_provenance'), dict):
        structure = evidence['structure_provenance']
        result['structure_provenance'] = pick(structure, 'manifest_sha256 graph_sha256 query_sha256 planning_base_sha planning_files_sha256 source_head structural_claims_only runtime_authorized')
        for key, maximum in [('hits', 5), ('related_nodes', 50)]:
            result['structure_provenance'][key] = [pick(row, 'id kind name qualified_name path language start_line end_line source_sha256')
                                                   for row in objects(structure.get(key, []))[:maximum]]
        result['structure_provenance']['edges'] = [pick(row, 'source target kind') for row in objects(structure.get('edges', []))[:50]]
    if "memory_citations" in evidence:
        result["memory_citations"] = [pick(row, "id topic path start_line end_line kind classification "
                     "classification_origin source_sha256 excerpt_sha256")
                     for row in objects(evidence["memory_citations"])[:5]]
        for citation, original in zip(result["memory_citations"], evidence["memory_citations"]):
            if isinstance(original.get("origin"), dict):
                origin = pick(original["origin"], "member_id entry_id path catalog_sha256 sources_sha256 kind run_id")
                if (set(origin) != {"member_id", "entry_id", "path", "catalog_sha256", "sources_sha256", "kind", "run_id"}
                        or any(not isinstance(value, str) or len(value) > 512
                               for key, value in origin.items() if key != "run_id")
                        or origin["run_id"] is not None and (not isinstance(origin["run_id"], str) or len(origin["run_id"]) > 32)):
                    raise SafetyError("Malformed memory origin in report evidence")
                citation["origin"] = origin
    return result


def sha(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def project_run(directory: Path) -> dict:
    record, file_sha = read_json(directory / "run.json")
    if record.get("run_id") != directory.name or not RUN_ID.fullmatch(directory.name):
        raise SafetyError("Run directory and identity do not match")
    result = pick(record, "run_id schema_version status mode issue base_sha source_repo branch "
                  "source_unchanged integration integration_branch integration_commit "
                  "integration_review_sha256 error approval_outcome diff plan_sha256")
    result["authorization"] = authorization(record.get("authorization", {}))
    origin = record.get("planning_origin", {})
    result["planning_origin"] = pick(origin, "plan_id plan_sha256 file_sha256")
    result["planning_origin"]["provider_evidence"] = pick(origin.get("provider_evidence", {}),
                                                       "pi_version transport requests attempt_id credential_source model_cost_verified")
    result["planning_origin"]["provider_evidence"].update(memory_evidence(origin.get("provider_evidence", {})))
    result["recovery"] = pick(record.get("recovery", {}), "result reason error")
    result["integration_preparation"] = pick(record.get("integration_preparation", {}), "status attempt_id cleanup error")
    result["record_sha256"] = file_sha
    result["repair_finished"] = bool(record.get("finished_at"))
    execution_path = directory / "repair-execution.json"
    if execution_path.exists():
        execution, execution_sha = read_json(execution_path)
        result["repair_execution"] = pick(execution, "schema_version phase controller inflight process_facts owner_sha256 checkpoint_sha256 aborted_at")
        result["repair_execution"]["record_sha256"] = execution_sha
    result["recorded_at"] = record.get("events", [{}])[-1].get("time") if record.get("events") else None
    result["policy"] = pick(record.get("policy", {}), "allowed_paths readonly_paths allowed_tests timeout_seconds")
    result["baseline_tests"] = tests(record.get("baseline_tests", []))
    result["candidate_tests"] = tests(record.get("candidate_tests", []))
    result["events"] = [pick(item, "time stage reason error") for item in objects(record.get("events", []))]
    result["rounds"] = []
    for item in objects(record.get("rounds", [])):
        row = pick(item, "number plan_sha256 verification diff")
        row["authorization"] = authorization(item.get("authorization", {}))
        plan = item.get("plan", {})
        row["plan"] = pick(plan, "diagnosis risk provider")
        row["paths"] = [edit.get("path") for edit in objects(plan.get("edits", []))]
        row["plan_hash_matches"] = bool(plan) and sha(plan) == item.get("plan_sha256")
        row["tests"] = tests(item.get("tests", []))
        if item.get('behavior_comparison') is not None:
            row['behavior_comparison']=pick(item['behavior_comparison'],
                'schema_version contract_sha256 counts comparable baseline_reproduced candidate_verified scope')
            row['behavior_comparison']['read_scope']='recorded_summary_only'
        evidence = item.get("provider_evidence", {})
        row["provider_evidence"] = pick(evidence, "pi_version transport requests credential_source model_cost_verified")
        row["provider_evidence"].update(memory_evidence(evidence))
        row["provider_evidence"]["usage"] = [pick(u, "input output cacheRead cacheWrite totalTokens")
                                                for u in objects(evidence.get("usage", []))]
        result["rounds"].append(row)
    result["review"] = None
    if (directory / "integration-review.json").exists():
        review, review_file_sha = read_json(directory / "integration-review.json")
        core = review.get("review", {})
        view = pick(review, "status started_at review_sha256 error cleanup_error operation_stage aborted_at")
        view["attempt_id"] = review.get("preparation", {}).get("attempt_id")
        view["test_inflight"] = pick(review.get("test_inflight") or {}, "pid created_at")
        view["authorization"] = authorization(review.get("authorization", {}))
        view["cleanup"] = pick(review.get("cleanup", {}), "result reason error") if isinstance(review.get("cleanup", {}), dict) else review.get("cleanup")
        view["record_sha256"] = review_file_sha
        view["hash_matches"] = sha(core) == review["review_sha256"] if core and review.get("review_sha256") else None
        view["core"] = pick(core, "commit tree base_sha target_branch diff_sha256 edit_plan_sha256")
        view["tests"] = tests(core.get("tests", review.get("verification_tests", [])))
        # Read only one fixed evidence name; never paths from JSON or arbitrary files.
        diff_path = directory / "integration.diff"
        if diff_path.exists():
            unlinked(diff_path)
            with diff_path.open("rb") as stream:
                content = stream.read(MAX_FILE + 1)
            if len(content) > MAX_FILE:
                raise SafetyError("Integration diff exceeds the file limit")
            # Match the workflow's read_text semantics (Windows writes CRLF).
            view["diff"] = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            view["diff_hash_matches"] = (hashlib.sha256(view["diff"].encode()).hexdigest() == core["diff_sha256"]
                                         if core.get("diff_sha256") else None)
        else:
            view["diff_hash_matches"] = None
        result["review"] = view
    return result


def export_dashboard(inputs: list[Path], output: Path) -> dict:
    roots = [unlinked(path) for path in inputs]
    target = unlinked(output)
    if target.suffix.lower() != ".html" or target.exists():
        raise SafetyError("Choose a new .html output; existing files are never overwritten")
    directories = set()
    for root in roots:
        if not root.is_dir():
            raise SafetyError("Dashboard input must be a run directory or runs collection")
        if (root / "run.json").exists():
            directories.add(root)
        else:
            # No recursive scan: never enter workspaces, .git, config or credentials.
            for child in root.iterdir():
                if RUN_ID.fullmatch(child.name) and child.is_dir():
                    directories.add(unlinked(child))
        if len(directories) > MAX_RUNS:
            raise SafetyError("Dashboard supports at most 100 explicitly selected runs")
    if not directories:
        raise SafetyError("No run records found in the selected inputs")
    if any(target.is_relative_to(root) for root in roots):
        raise SafetyError("Dashboard output must be outside input evidence")
    runs = [project_run(path) for path in sorted(directories)]
    for run in runs:
        source = run.get("source_repo")
        if source and target.is_relative_to(Path(source).resolve()):
            raise SafetyError("Dashboard output must be outside the recorded source repository")
    payload = {"schema_version": 1, "generated_at": now(), "read_only": True, "runs": runs}
    # Select fields first, redact values second, then escape HTML parser delimiters.
    data = redact(json.dumps(payload, ensure_ascii=False))
    if len(data.encode("utf-8")) > MAX_EXPORT:
        raise SafetyError("Dashboard evidence exceeds the 24 MiB export limit")
    for literal, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                             ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        data = data.replace(literal, escaped)
    scripts = [(ASSETS / "vendor" / "diff2html.min.js").read_text(encoding="utf-8"),
               (ASSETS / "dashboard.js").read_text(encoding="utf-8")]
    if any("</script" in script.lower() for script in scripts):
        raise SafetyError("Invalid embedded dashboard asset")
    hashes = " ".join("'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
                      for script in scripts)
    styles = (ASSETS / "vendor" / "diff2html.min.css").read_text(encoding="utf-8")
    styles += "\n" + (ASSETS / "dashboard.css").read_text(encoding="utf-8")
    license_text = "\n\n".join(path.read_text(encoding="utf-8") for path in
                               sorted((ASSETS / "vendor").glob("*LICENSE*")))
    license_text += "\n\n" + (ASSETS / "vendor" / "NOTICE.txt").read_text(encoding="utf-8")
    from html import escape
    template = (ASSETS / "dashboard.html").read_text(encoding="utf-8")
    html = template.replace("@@CSP@@", "default-src 'none'; script-src " + hashes +
                            "; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'")
    # Data is inserted last so marker-like strings in evidence remain literal.
    html = html.replace("@@STYLE@@", styles).replace("@@VENDOR@@", scripts[0])
    html = html.replace("@@APP@@", scripts[1]).replace("@@LICENSE@@", escape(license_text)).replace("@@DATA@@", data)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(html)
    return {"dashboard": str(target), "runs": len(runs), "read_only": True,
            "generated_at": payload["generated_at"], "bytes": target.stat().st_size}
