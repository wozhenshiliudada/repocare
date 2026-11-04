from .models import Edit, Plan, SafetyError, digest


class MockProvider:
    """Deterministic demonstration provider. Does not claim to diagnose arbitrary code."""

    def __init__(self, scenario: str = "fix"):
        if scenario not in {"fix", "broken", "regression"}:
            raise SafetyError("Unknown mock scenario")
        self.scenario = scenario

    def plan(self, repository, issue: str, tests: tuple[tuple[str, ...], ...]) -> Plan:
        path = "calculator.py"
        original = repository.read(path)
        needle = b"return a - b"
        if original.count(needle) != 1:
            raise SafetyError("Mock provider requires the supplied calculator bug fixture")
        replacement = {"fix": b"return a + b", "broken": b"return a * b",
                       "regression": b"return abs(a + b)"}[self.scenario]
        return Plan(issue, repository.head,
                    "Mock diagnosis: add() subtracts its operands; use addition and run regressions.",
                    "low (fixture only)",
                    (Edit(path, digest(original), original.replace(needle, replacement).decode()),),
                    tests, "deterministic-mock/" + self.scenario)
