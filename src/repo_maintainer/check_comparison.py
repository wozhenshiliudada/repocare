"""Compare controller-run commands, without inferring whole-program correctness."""
from .models import SafetyError


TRANSITIONS = ("repaired", "maintained", "regressed", "still_failing", "inconclusive")


def check_outcomes(tests):
    """Small projection: no repository output or new executable instructions."""
    if not isinstance(tests, list) or not tests:
        raise SafetyError("Check comparison requires completed command receipts")
    result = []
    for test in tests:
        if (not isinstance(test, dict) or not isinstance(test.get("command"), list)
                or not test["command"] or any(not isinstance(arg, str) or not arg or "\0" in arg
                                              for arg in test["command"])
                or type(test.get("returncode")) is not int
                or type(test.get("timed_out")) is not bool):
            raise SafetyError("Invalid command receipt for check comparison")
        result.append({"command": list(test["command"]), "returncode": test["returncode"],
                       "timed_out": test["timed_out"]})
    return result


def compare_checks(baseline, candidate):
    before, after = check_outcomes(baseline), check_outcomes(candidate)
    if [t["command"] for t in before] != [t["command"] for t in after]:
        raise SafetyError("Baseline/candidate check commands differ in number or order")
    checks = []
    for index, (old, new) in enumerate(zip(before, after), 1):
        if old["timed_out"] or new["timed_out"]:
            transition = "inconclusive"
        elif old["returncode"] == 0:
            transition = "maintained" if new["returncode"] == 0 else "regressed"
        else:
            transition = "repaired" if new["returncode"] == 0 else "still_failing"
        checks.append({"check_index": index, "command": new["command"], "transition": transition,
                       "baseline_returncode": old["returncode"], "baseline_timed_out": old["timed_out"],
                       "candidate_returncode": new["returncode"], "candidate_timed_out": new["timed_out"]})
    return {"schema_version": 1, "scope": "authorized_command_exit_status", "checks": checks,
            "counts": {key: sum(c["transition"] == key for c in checks) for key in TRANSITIONS},
            "all_candidate_checks_passed": all(t["returncode"] == 0 and not t["timed_out"] for t in after),
            "semantic_correctness_proven": False}
