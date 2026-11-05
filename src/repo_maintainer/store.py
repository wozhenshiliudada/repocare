from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(text: str) -> str:
    for name, value in os.environ.items():
        if len(value) >= 8 and re.search(r"token|secret|password|api_?key", name, re.I):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+", r"\1[REDACTED]", text)
    return re.sub(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}", "[REDACTED]", text)


def _replace_run_record(temp: Path, target: Path) -> None:
    # A concurrent dashboard read can briefly deny replacement on Windows.
    # Retry only this atomic rename, never the surrounding event/Git operation.
    # Persistent denial still raises with both old record and new temp intact.
    delays = iter((.01, .02, .04, .08, .16, .32, .5))
    while True:
        try:
            temp.replace(target)
            return
        except PermissionError as error:
            delay = next(delays, None)
            if os.name != 'nt' or getattr(error, 'winerror', None) not in {5, 32} or delay is None:
                raise
            time.sleep(delay)


class RunStore:
    def __init__(self, directory: Path, record: dict):
        self.directory = directory
        self.record = record

    def event(self, stage: str, **details) -> None:
        self.record["status"] = stage
        self.record["events"].append({"time": now(), "stage": stage, **details})
        self.save()

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        serialized = redact(json.dumps(self.record, indent=2, ensure_ascii=False))
        temp = self.directory / "run.json.tmp"
        temp.write_text(serialized + "\n", encoding="utf-8")
        _replace_run_record(temp, self.directory / "run.json")
        report = [f"# Run {self.record['run_id']}", "",
                  f"Status: **{self.record['status']}**", "",
                  f"Mode: `{self.record['mode']}`", "",
                  f"Source unchanged: `{self.record.get('source_unchanged', 'unchecked')}`", "",
                  f"Integration: **{self.record.get('integration', 'disabled')}**", "",
                  "See `run.json` for plan, authorization, commands, timings and recovery evidence.", ""]
        if self.record.get("integration_branch"):
            report += [f"Published branch: `{self.record['integration_branch']}`", "",
                       f"Sealed commit: `{self.record['integration_commit']}`", "",
                       f"Integration approval: `{self.record['integration_review_sha256']}`", "",
                       "Source checkout/branch was not modified; merge/push remains separate.", ""]
        if (self.directory / "repair-execution.json").exists():
            report += ["## Repair process evidence", "",
                       "See `repair-execution.json` for controller/test identities, owned checkpoints and explicit abort audit.", "",
                       "Missing completion is not failure or success. Inspect before aborting; no process/model/test replay.", ""]
        if self.record.get("rounds"):
            if self.record.get("planning_origin"):
                origin = self.record["planning_origin"]
                report += ["## Original planning evidence", "",
                           f"Plan fingerprint: `{origin.get('plan_sha256')}`", "",
                           f"Planning model requests: `{origin.get('provider_evidence', {}).get('requests', 'not_measured')}`", "",
                           "Saved-plan execution makes 0 additional model requests; this is not the total task consumption.", ""]
                experience = origin.get('provider_evidence', {})
                if experience.get('knowledge_context_sha256'):
                    report += ['## Reviewed proposed knowledge', '',
                               f"Context SHA256: `{experience['knowledge_context_sha256']}`", '',
                               'Proposed reference only. Revalidate on current code; fresh approval remains required.', '']
                    for citation in experience.get('knowledge_citations', []):
                        report += [f"- Knowledge `{citation['id']}`, review version `{citation['review_version']}`"]
                    report += ['']
                if experience.get('experience_context_sha256'):
                    report += ["## Reviewed historical experience", "",
                               f"Context SHA256: `{experience['experience_context_sha256']}`", "",
                               "Historical data only; no approval, permission or budget is inherited.", ""]
                    for citation in experience.get('experience_citations', [])[:3]:
                        report += [f"- Run `{citation['id']}`; payload `{citation['payload_sha256']}`; review version `{citation['review_version']}`"]
                    report += [""]
            report += ["## Repair rounds", "",
                       "| Round | Approval | Verification | Test exits | Model requests |", "|---|---|---|---|---|"]
            for item in self.record["rounds"]:
                report += [f"| {item['number']} | {item.get('authorization', {}).get('decision', 'pending')} | "
                           f"{item.get('verification', 'not_run')} | "
                           f"{[t['returncode'] for t in item['tests']]} | "
                           f"{item.get('provider_evidence', {}).get('requests', 'not_measured')} |"]
                if "diff" in item:
                    (self.directory / f"round-{item['number']:02d}.diff").write_text(
                        redact(item["diff"]), encoding="utf-8")
            report += [""]
            for item in self.record["rounds"]:
                comparison = item.get("check_comparison")
                if comparison is None:
                    continue
                report += [f"## Command outcome comparison: round {item['number']}", "",
                           "| Check | Baseline exit | Candidate exit | Change |", "|---|---|---|---|"]
                for check in comparison["checks"]:
                    before = "timeout" if check["baseline_timed_out"] else check["baseline_returncode"]
                    after = "timeout" if check["candidate_timed_out"] else check["candidate_returncode"]
                    report.append(f"| {check['check_index']} | {before} | {after} | {check['transition']} |")
                report += ["", "Commands and original output are in `run.json`. A new failure may be a code or environment issue.", "",
                           "Passing selected commands does not prove untested behavior. Timeout comparisons are inconclusive.", ""]
        structural = [('original planning', self.record.get('planning_origin', {}).get('provider_evidence', {}))]
        structural += [(f"round {row['number']}", row.get('provider_evidence', {})) for row in self.record.get('rounds', [])]
        terminal = self.record.get('provider_evidence', {})
        if terminal.get('structure_context_sha256') and not any(
                evidence.get('structure_context_sha256') == terminal['structure_context_sha256'] for _, evidence in structural):
            structural.append(('terminal provider receipt; no successful plan implied', terminal))
        for stage, evidence in structural:
            if evidence.get('structure_context_sha256'):
                report += ['## Read-only structure provenance', '', f"Stage: `{stage}`", '',
                           f"Context receipt: `{evidence['structure_context_sha256']}`", '',
                           'Original indexed baseline metadata; not current candidate truth, execution authority or model-effect proof.', '',
                           '```json', json.dumps(evidence.get('structure_provenance', {}), ensure_ascii=False, indent=2), '```', '']
        if self.record.get("integration_preparation"):
            report += ["## Integration preparation / recovery", "",
                       "```json", json.dumps(self.record["integration_preparation"], indent=2, ensure_ascii=False), "```", "",
                       "Aborted/partial verification cannot authorize publication; retry must rerun tests.", ""]
        for phase in ("baseline_tests", "candidate_tests"):
            report += [f"## {phase}", ""]
            for result in self.record.get(phase, []):
                report += [f"Command: `{json.dumps(result['command'])}`", "",
                           f"Exit: `{result['returncode']}`; timeout: `{result['timed_out']}`", "",
                           "```text", result["stdout"], result["stderr"], "```", ""]
        if self.record.get("error"):
            evidence = self.record.get("provider_evidence", {})
            if evidence.get("schema_version") == 1 and evidence.get("status") == "failed":
                report += ["## Safe Pi failure evidence", "",
                           f"Failure category: `{evidence.get('failure_category')}`", "",
                           f"Observed adapter request starts: `{evidence.get('requests_started', 'not_measured')}`", "",
                           "Request consumption remains unknown; starts are not provider billing or permission to retry.", "",
                           f"Runtime event stream complete: `{evidence.get('events_complete', False)}`", "",
                           "See run.json provider_evidence for filtered events and observed numeric usage.", ""]
            error = self.record['error']
            report += ["## Error", "", error if isinstance(error,str) else
                       json.dumps(error,ensure_ascii=False,indent=2), ""]
        (self.directory / "report.md").write_text(redact("\n".join(report)), encoding="utf-8")
        if "diff" in self.record:
            (self.directory / "candidate.diff").write_text(redact(self.record["diff"]), encoding="utf-8")
