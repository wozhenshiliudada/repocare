"""Allowlisted read-only inspection of controlled workflow comparison summaries."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .dashboard import read_json, unlinked
from .models import SafetyError

REQUIRED_ARMS = ("patch_only", "test_gate", "guarded_one", "guarded_two")

METRIC_INT_FIELDS = (
    "tasks",
    "repair_tasks",
    "graded_repairs",
    "false_acceptances",
    "failed_candidates_retained",
    "approval_violations",
    "synthetic_model_requests",
)


def load_comparison_summary(path: Path | str) -> dict:
    target = unlinked(Path(path))
    try:
        if not target.is_file():
            raise SafetyError("Comparison summary path must be an accessible regular file")
        data, _sha = read_json(target)
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        raise SafetyError("Comparison summary path must be an accessible regular file")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SafetyError("Comparison summary file is not valid UTF-8 JSON")

    if not isinstance(data, dict):
        raise SafetyError("Comparison summary must be a JSON object")

    schema_version = data.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise SafetyError("schema_version must be exact integer 1")

    evidence_kind = data.get("evidence_kind")
    if evidence_kind not in ("fixture-scripted", "pi-loopback-scripted"):
        raise SafetyError("evidence_kind must be fixture-scripted or pi-loopback-scripted")

    claim = data.get("model_accuracy_claim")
    if claim is not False:
        raise SafetyError("model_accuracy_claim must be False")

    real_requests = data.get("real_model_requests")
    if type(real_requests) is not int or real_requests != 0:
        raise SafetyError("real_model_requests must be exact integer 0")

    passed = data.get("passed")
    if type(passed) is not bool:
        raise SafetyError("passed must be a boolean")

    metrics = data.get("metrics")
    if not isinstance(metrics, dict) or set(metrics.keys()) != set(REQUIRED_ARMS) or len(metrics) != 4:
        raise SafetyError("metrics must contain exactly the four comparison arms")

    clean_metrics = {}
    for arm in REQUIRED_ARMS:
        arm_data = metrics.get(arm)
        if not isinstance(arm_data, dict):
            raise SafetyError("Arm metrics must be a JSON object")

        for field in METRIC_INT_FIELDS:
            if field not in arm_data:
                raise SafetyError(f"Missing required metric field: {field}")
            val = arm_data[field]
            if type(val) is not int or val < 0:
                raise SafetyError(f"Metric {field} must be an exact non-negative integer")

        tasks = arm_data["tasks"]
        repair_tasks = arm_data["repair_tasks"]
        graded_repairs = arm_data["graded_repairs"]
        false_acceptances = arm_data["false_acceptances"]
        failed_candidates_retained = arm_data["failed_candidates_retained"]
        approval_violations = arm_data["approval_violations"]
        synthetic_requests = arm_data["synthetic_model_requests"]

        if repair_tasks > tasks:
            raise SafetyError("repair_tasks cannot exceed tasks")
        if graded_repairs > repair_tasks:
            raise SafetyError("graded_repairs cannot exceed repair_tasks")
        if false_acceptances > tasks:
            raise SafetyError("false_acceptances cannot exceed tasks")
        if failed_candidates_retained > tasks:
            raise SafetyError("failed_candidates_retained cannot exceed tasks")
        if approval_violations > tasks:
            raise SafetyError("approval_violations cannot exceed tasks")

        if "workflow_duration_seconds" not in arm_data:
            raise SafetyError("Missing workflow_duration_seconds")
        duration = arm_data["workflow_duration_seconds"]
        try:
            finite = type(duration) in (int, float) and math.isfinite(duration)
        except OverflowError:
            finite = False
        if not finite or duration < 0:
            raise SafetyError("workflow_duration_seconds must be a finite non-negative number")

        if evidence_kind == "fixture-scripted" and synthetic_requests != 0:
            raise SafetyError("synthetic_model_requests must be 0 for fixture-scripted evidence")

        clean_metrics[arm] = {
            "tasks": tasks,
            "repair_tasks": repair_tasks,
            "graded_repairs": graded_repairs,
            "false_acceptances": false_acceptances,
            "failed_candidates_retained": failed_candidates_retained,
            "approval_violations": approval_violations,
            "synthetic_model_requests": synthetic_requests,
            "workflow_duration_seconds": duration,
        }

    clean_summary = {
        "schema_version": 1,
        "evidence_kind": evidence_kind,
        "model_accuracy_claim": False,
        "real_model_requests": 0,
        "passed": passed,
        "status": "completed" if passed else "partial",
        "metrics": clean_metrics,
    }

    if "inputs_and_first_patches_matched" in data:
        matched = data["inputs_and_first_patches_matched"]
        if type(matched) is not bool:
            raise SafetyError("inputs_and_first_patches_matched must be a boolean")
        clean_summary["inputs_and_first_patches_matched"] = matched

    if "dataset_sha256" in data:
        sha_val = data["dataset_sha256"]
        if not isinstance(sha_val, str) or not re.fullmatch(r"[0-9a-f]{64}", sha_val):
            raise SafetyError("dataset_sha256 must be a 64-character lowercase hex string")
        clean_summary["dataset_sha256"] = sha_val

    return clean_summary


def format_comparison_summary(summary: dict) -> str:
    passed = summary.get("passed", False)
    status_label = "completed" if passed else "partial (incomplete / contract unfulfilled)"
    evidence_kind = summary.get("evidence_kind", "unknown")
    metrics = summary.get("metrics", {})

    lines = [
        "Controlled Workflow Comparison Summary",
        "======================================",
        f"Status: {status_label}",
        f"Evidence Kind: {evidence_kind}",
        f"Real Model Requests: {summary.get('real_model_requests', 0)}",
        f"Model Accuracy Claim: {summary.get('model_accuracy_claim', False)}",
    ]
    if "dataset_sha256" in summary:
        lines.append(f"Dataset SHA-256: {summary['dataset_sha256']}")
    if "inputs_and_first_patches_matched" in summary:
        lines.append(f"Inputs and First Patches Matched: {summary['inputs_and_first_patches_matched']}")

    lines.append("")
    lines.append("| Arm | Graded repairs / authorized tasks | False acceptances | Failed candidates retained | Approval violations | Synthetic requests |")
    lines.append("|---|---|---|---|---|---|")

    for arm in REQUIRED_ARMS:
        m = metrics.get(arm, {})
        repairs = f"{m.get('graded_repairs', 0)}/{m.get('repair_tasks', 0)}"
        fa = m.get("false_acceptances", 0)
        fc = m.get("failed_candidates_retained", 0)
        av = m.get("approval_violations", 0)
        req = m.get("synthetic_model_requests", 0)
        lines.append(f"| {arm} | {repairs} | {fa} | {fc} | {av} | {req} |")

    lines.append("")
    lines.append("Boundary Notes:")
    lines.append("- 历史摘要：本输出仅为受控离线实验的历史摘要，不是当前Git状态或发布批准。")
    lines.append("- 公开固定答案/合成模型，不代表真实模型准确率；真实模型请求为0。")
    lines.append("- JUnit是工作流契约测试，仅反映流程与回滚契约，不能将JUnit契约通过当修复成功。")
    lines.append("- 审批拒绝场景不计入修复分母；负结果真实保留。")

    return "\n".join(lines)
