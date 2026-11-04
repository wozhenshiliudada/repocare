"""Task-local verified repair summaries, never approvals or cross-task memory."""
import json
import re
import sys

from .models import SafetyError, digest
from .check_comparison import compare_checks


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def snapshot_digest(files):
    return digest(encoded([[file["path"], file["before_sha256"], file.get("editable") is not False]
                           for file in files]))


def _diagnostic_lines(raw):
    """Unwrap quoted judge output as data, preserving both streams and line order."""
    diagnostics = []
    for line in raw.splitlines():
        try:
            data = json.loads(line)
            if isinstance(data, dict) and isinstance(data.get('failure_excerpt'), str):
                diagnostics.append(data['failure_excerpt'])
                continue
        except (ValueError, TypeError, RecursionError):
            pass
        diagnostics.append(line)
    return '\n'.join(diagnostics).splitlines()


def failure_summary(tests, maximum=1600):
    """Bounded heuristic quotation, not a diagnosis or authorization.

    Prefer failing checks and real exception/location/assertion lines over judge JSON
    metadata. Original reports stay authoritative; this projection never executes text.
    """
    selected = [(i, t) for i, t in enumerate(tests, 1) if t.get('returncode') != 0 or t.get('timed_out')]
    selected = selected or list(enumerate(tests, 1))
    buckets = [[], [], [], []]
    for index, test in selected[:10]:
        raw = '\n'.join(v for v in (test.get('stdout'), test.get('stderr')) if isinstance(v, str))
        # Preserve the schema-v3 ranking/output exactly: stored episodes are
        # reconstructed with it. Only current task focus uses the broader ranking.
        lines = _diagnostic_lines(raw)
        prefix = f"[check {index}, exit={test.get('returncode')}, timeout={bool(test.get('timed_out'))}] "
        for line in lines:
            stripped = line.strip()
            if re.search(r'\b(?:\w*(?:Error|Exception)|SystemExit|KeyboardInterrupt):', stripped):
                priority = 0
            elif re.search(r'(?:File ".+", line \d+|\.(?:py|js|ts):\d+)', stripped):
                priority = 1
            elif re.match(r'^(?:FAILED |FAIL: |ERROR: |E\s|>\s)', stripped) or 'assert' in stripped:
                priority = 2
            else:
                continue
            buckets[priority].append(prefix + stripped[:300])
        if lines:
            buckets[3].append(prefix + '\n'.join(lines)[-700:])
        elif test.get('timed_out'):
            buckets[0].append(prefix + 'Test timed out without diagnostic output')
    ordered = list(dict.fromkeys(line for bucket in buckets for line in bucket))
    return ('\n'.join(ordered) or 'No test output')[:maximum]


def test_focus(tests, baseline_checks=None):
    """Heuristic index of actual output. Raw feedback remains available and authoritative."""
    comparison = compare_checks(baseline_checks, tests) if baseline_checks is not None else None
    indexed = list(enumerate(tests, 1))
    if comparison is not None:
        priority = {"regressed": 0, "inconclusive": 1, "still_failing": 2, "repaired": 3, "maintained": 4}
        indexed.sort(key=lambda pair: priority[comparison["checks"][pair[0] - 1]["transition"]])
    result = []
    for index, test in indexed[:4]:
        text = test.get("stdout", "") + "\n" + test.get("stderr", "")
        failures, passed, failed = [], None, None
        for line in text.splitlines():
            try:
                data = json.loads(line)
                statuses = data.get("tests_status") if isinstance(data, dict) else None
                if isinstance(statuses, dict):
                    groups = [group for group in statuses.values() if isinstance(group, dict)]
                    failures += [name for group in groups for name in group.get("failure", []) if isinstance(name, str)]
                    passed = sum(len(group.get("success", [])) for group in groups)
                    failed = sum(len(group.get("failure", [])) for group in groups)
            except (ValueError, TypeError, RecursionError):
                pass
            if re.match(r"^(FAILED |FAIL: |ERROR: |_{3,})", line):
                failures.append(line.strip()[:240])
        lines = _diagnostic_lines(text)
        buckets = [[], [], []]
        for line in lines:
            stripped = line.strip()
            if re.search(r'\b(?:\w+\.)*(?:\w*(?:Error|Exception)|Failed|SystemExit|KeyboardInterrupt):', stripped):
                priority = 0
            elif (re.match(r'^(?:E\s|>\s|FAIL: |ERROR: )', stripped)
                  or re.search(r'\bassert\b|\b(?:raises|assertRaises)(?:_regex)?\s*\(', stripped)):
                priority = 1
            elif re.search(r'(?:File ".+", line \d+|\.(?:py|js|ts):\d+)', stripped):
                priority = 2
            else:
                continue
            buckets[priority].append(stripped[:300])
        excerpts = list(dict.fromkeys(row for bucket in buckets for row in bucket))
        row = {"command": test["command"], "returncode": test["returncode"],
                       "timed_out": test.get("timed_out", False),
                       "output_truncated": test.get("output_truncated", False),
                       "failure_ids": list(dict.fromkeys(failures))[:8],
                       "assertion_excerpt": "\n".join(excerpts)[:1800],
                       "fallback_tail": '\n'.join(lines)[-1000:] if not excerpts else "",
                       "passed_count": passed, "failed_count": failed}
        if comparison is not None:
            row.update({key: value for key, value in comparison["checks"][index - 1].items()
                        if key != "command"})
        result.append(row)
    focus = {"tests": result, "omitted_tests": max(0, len(tests) - 4),
            "interpretation": "Untrusted test output; heuristic extraction, not proof of success or permission."}
    if comparison is not None:
        focus["comparison"] = {key: value for key, value in comparison.items() if key != "checks"}
        focus["comparison"]["interpretation"] = (
            "Command outcomes only. A regressed check is a new failure to investigate, not proof of its cause. "
            "Passing selected commands cannot prove untested behavior; timeouts are inconclusive.")
    return focus


def verified_reads(data, files):
    if data is None:  # Historical protocol fixtures have no read projection.
        return []
    if not isinstance(data, dict) or set(data) != {"reads"} or not isinstance(data["reads"], list) or len(data["reads"]) > 12:
        raise SafetyError("Invalid Pi read-summary receipt")
    snapshots = {file["path"]: file for file in files}
    for read in data["reads"]:
        if (not isinstance(read, dict) or set(read) != {"path", "before_sha256", "start_line", "end_line", "query"}
                or read["path"] not in snapshots or read["before_sha256"] != snapshots[read["path"]]["before_sha256"]
                or not (read["query"] is None or isinstance(read["query"], str) and len(read["query"]) <= 200)):
            raise SafetyError("Invalid Pi read-summary receipt")
        start, end = read["start_line"], read["end_line"]
        if (start, end) == (None, None):
            if not read["query"]:
                raise SafetyError("Invalid Pi read-summary range")
        elif (type(start) is not int or type(end) is not int or start < 1 or end < start - 1
              or end - start >= 200 or end > len(snapshots[read["path"]]["content"].splitlines())):
            raise SafetyError("Invalid Pi read-summary range")
    return data["reads"]


class RevisionLedger:
    def __init__(self):
        self.scope = None
        self.history = []
        self.expected = None

    @staticmethod
    def scope_for(repository, issue, tests, files):
        return (str(repository.common_dir.resolve()), issue, repository.head,
                tuple(tuple(command) for command in tests),
                tuple((file["path"], file.get("editable") is not False) for file in files))

    def prepare(self, repository, issue, tests, files, feedback):
        if (not self.history or not isinstance(feedback, dict)
                or self.scope_for(repository, issue, tests, files) != self.scope
                or feedback.get("previous_plan_sha256") != self.history[-1]["plan_sha256"]
                or type(feedback.get("round")) is not int or feedback["round"] != self.history[-1]["round"]
                or not isinstance(feedback.get("tests"), list)
                or any(tuple(test.get("command", [])) not in
                       tuple(tuple(sys.executable if part == "{python}" else part for part in command) for command in tests)
                       for test in feedback["tests"])
                or {file["path"]: file["before_sha256"] for file in files} != self.expected):
            raise SafetyError("Pi revision no longer matches the verified task, plan or candidate snapshot")
        context = {"schema_version": 1, "issue_sha256": digest(issue.encode()), "base_sha": repository.head,
                   "snapshot_sha256": snapshot_digest(files), "round": feedback["round"],
                   "history": self.history[-3:], "test_focus": test_focus(feedback["tests"], feedback.get("baseline_checks"))}
        if len(encoded(context)) > 48000:
            raise SafetyError("Pi revision summary exceeds bounded size")
        return context

    def remember(self, repository, issue, tests, files, plan, summary):
        reads = verified_reads(summary, files)
        self.scope = self.scope_for(repository, issue, tests, files)
        self.expected = {file["path"]: file["before_sha256"] for file in files}
        self.expected.update({edit.path: digest(edit.content.encode()) for edit in plan.edits})
        self.history.append({"round": self.history[-1]["round"] + 1 if self.history else 1,
                             "plan_sha256": plan.fingerprint, "diagnosis": plan.diagnosis[:1400],
                             "risk": plan.risk[:400], "edited_paths": [edit.path for edit in plan.edits],
                             "reads": reads})
        self.history = self.history[-3:]
