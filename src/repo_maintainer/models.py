from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Protocol


class SafetyError(RuntimeError):
    """A precondition failed; no integration may follow."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path) -> str:
    """Hash large local evidence without buffering a whole workspace file."""
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or "\\" in value or ":" in value or path.is_absolute()
            or any(part.casefold() in ("", ".", "..", ".git") for part in value.split("/"))
            or any(character in value for character in '\x00<>"|?*')
            or any(part.endswith((".", " ")) for part in path.parts)
            or any(part.split(".")[0].upper() in
                   {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                    *(f"LPT{i}" for i in range(10))} for part in path.parts)):
        raise SafetyError(f"Unsafe repository path: {value!r}")
    return value


def safe_context_path(value: str) -> str:
    canonical_path(value)
    name = PurePosixPath(value).name.lower()
    if (name == ".env" or name.startswith(".env.") or name.endswith((".key", ".pem"))
            or name.startswith(("credentials", "secrets"))):
        raise SafetyError("Sensitive configuration files cannot enter model context")
    return value


@dataclass(frozen=True)
class Edit:
    path: str
    before_sha256: str
    content: str


@dataclass(frozen=True)
class Plan:
    issue: str
    base_sha: str
    diagnosis: str
    risk: str
    edits: tuple[Edit, ...]
    tests: tuple[tuple[str, ...], ...]
    provider: str

    @property
    def fingerprint(self) -> str:
        return digest(json.dumps(asdict(self), sort_keys=True, ensure_ascii=False).encode())


@dataclass(frozen=True)
class Policy:
    allowed_paths: tuple[str, ...]
    allowed_tests: tuple[tuple[str, ...], ...]
    timeout_seconds: float = 30
    readonly_paths: tuple[str, ...] = ()

    def validate_scope(self) -> None:
        from .maintenance_limits import LIMITS
        if not 0 < self.timeout_seconds <= LIMITS['test_timeout_max_seconds']:
            raise SafetyError("Test timeout exceeds configured seconds")
        for path in (*self.allowed_paths, *self.readonly_paths):
            safe_context_path(path)
        if not self.allowed_paths or not self.allowed_tests:
            raise SafetyError("Policy requires editable files and test commands")
        edits = {path.casefold() for path in self.allowed_paths}
        reads = {path.casefold() for path in self.readonly_paths}
        if (len(edits) != len(self.allowed_paths) or len(reads) != len(self.readonly_paths)
                or edits & reads):
            raise SafetyError("Policy paths overlap or are duplicated")
        for command in self.allowed_tests:
            if not command or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command):
                raise SafetyError("Invalid authorized test command")

    def validate(self, plan: Plan) -> None:
        self.validate_scope()
        if not plan.edits or not plan.tests:
            raise SafetyError("A repair requires edits and at least one test command")
        if len({edit.path.casefold() for edit in plan.edits}) != len(plan.edits):
            raise SafetyError("Duplicate edit paths")
        for edit in plan.edits:
            safe_context_path(edit.path)
            if edit.path not in self.allowed_paths:
                raise SafetyError(f"Edit outside authorized paths: {edit.path}")
        for command in plan.tests:
            if (not command or any(not isinstance(arg, str) or not arg or "\x00" in arg
                                   for arg in command)
                    or command not in self.allowed_tests):
                raise SafetyError("Test command is not explicitly authorized")


class Provider(Protocol):
    """A provider proposes data only; the workflow owns all writes and tests."""

    def plan(self, repository, issue: str, tests: tuple[tuple[str, ...], ...]) -> Plan: ...
