"""Explicitly invoked, bounded DeepSeek validation using only self-authored fixtures."""
import json
import os
from pathlib import Path

from .evaluation import cases, create_case
from .models import SafetyError
from .pi_provider import PiProvider
from .store import redact
from .workflow import cleanup_run, repair


def run_deepseek_validation(directory: Path, credential_source: str = "environment") -> dict:
    if credential_source not in {"environment", "openclaw-readonly"}:
        raise SafetyError("Unknown credential source")
    if credential_source == "environment" and (
        os.environ.get("PI_MAINTENANCE_BASE_URL", "").rstrip("/") != "https://api.deepseek.com"
        or os.environ.get("PI_MAINTENANCE_MODEL") != "deepseek-flash"
    ):
        raise SafetyError("DeepSeek fixture validation requires the official endpoint and deepseek-flash")
    directory = directory.resolve()
    if directory.exists():
        raise SafetyError("Live validation directory must be new")
    directory.mkdir(parents=True)
    summaries = []
    remaining = 6
    available = {case.id: case for case in cases()}
    for name in ("config", "syntax", "boundary"):
        if remaining <= 0:
            break
        case = available[name]
        base = directory / case.id
        repository = create_case(case, base / "repository")
        provider = PiProvider(case.policy.allowed_paths, readonly_paths=case.policy.readonly_paths,
                              max_requests=remaining)
        provider.credential_source = credential_source
        record = repair(repository, case.issue, provider, case.policy, "auto", base / "runs",
                        max_attempts=2, actor="authorized-self-fixture-validation")
        passed = record["status"] == "candidate_ready" and record["source_unchanged"]
        summary = {"case": name, "run_id": record["run_id"], "status": record["status"],
                   "passed": passed, "source_unchanged": record["source_unchanged"],
                   "rounds": len(record["rounds"]), "requests": provider.requests_used,
                   "baseline_exit": [t["returncode"] for t in record["baseline_tests"]],
                   "candidate_exit": [t["returncode"] for t in record["candidate_tests"]],
                   "provider_evidence": record.get("provider_evidence", {}),
                   "error": record.get("error")}
        if passed:
            cleaned = cleanup_run(base / "runs" / record["run_id"])
            summary.update(cleanup_status=cleaned["status"], passed=passed and cleaned["source_unchanged"])
        summaries.append(summary)
        remaining -= provider.requests_used
        if not passed:
            # Unknown failed-call counts conservatively end this validation; no new request.
            break
    result = {"model": "deepseek-flash", "endpoint": "https://api.deepseek.com",
              "credential_source": credential_source, "request_limit": 6,
              "completed_requests": sum(case["requests"] for case in summaries),
              "total_cases": len(summaries), "passed_cases": sum(case["passed"] for case in summaries),
              "model_cost_verified": False, "cases": summaries}
    (directory / "summary.json").write_text(redact(json.dumps(result, indent=2)) + "\n", encoding="utf-8")
    return result


def run_mimo_validation(directory: Path, config_file: Path) -> dict:
    """One self-authored fixture, at most three requests, no automatic retries."""
    from .provider_config import load_provider_config
    config = load_provider_config(config_file)
    endpoint = config["PI_MAINTENANCE_BASE_URL"].rstrip("/")
    model = config["PI_MAINTENANCE_MODEL"]
    if endpoint != "https://api.xiaomimimo.com/v1" or not model.startswith("mimo-"):
        raise SafetyError("MiMo fixture validation requires the official standard endpoint and a MiMo model")
    directory = directory.resolve()
    if directory.exists():
        raise SafetyError("Live validation directory must be new")
    directory.mkdir(parents=True)
    case = next(case for case in cases() if case.id == "addition")
    repository = create_case(case, directory / "repository")
    provider = PiProvider(case.policy.allowed_paths, readonly_paths=case.policy.readonly_paths, max_requests=3)
    provider.config_file = config_file
    record = repair(repository, case.issue, provider, case.policy, "auto", directory / "runs",
                    max_attempts=1, actor="self-fixture-mimo-validation")
    passed = record["status"] == "candidate_ready" and record["source_unchanged"]
    result = {"model": model, "endpoint": endpoint, "credential_source": "explicit-local-file",
              "request_limit": 3, "completed_requests": provider.requests_used,
              "status": record["status"], "run_id": record["run_id"], "passed": passed,
              "source_unchanged": record["source_unchanged"],
              "baseline_exit": [t["returncode"] for t in record["baseline_tests"]],
              "candidate_exit": [t["returncode"] for t in record["candidate_tests"]],
              "provider_evidence": record.get("provider_evidence", {}), "error": record.get("error"),
              "model_cost_verified": False}
    if passed:
        cleaned = cleanup_run(directory / "runs" / record["run_id"])
        result.update(cleanup_status=cleaned["status"], passed=passed and cleaned["source_unchanged"])
    (directory / "summary.json").write_text(redact(json.dumps(result, indent=2)) + "\n", encoding="utf-8")
    return result
