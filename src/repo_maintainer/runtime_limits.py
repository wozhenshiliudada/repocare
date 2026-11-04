"""Shared local configuration consumed by Python and the pinned Pi adapter."""
import json
from pathlib import Path

# Token metadata crosses JSON into the JavaScript Pi adapter.
MAX_SAFE_TOKEN_COUNT = 2**53 - 1
_values = json.loads(Path(__file__).with_name("pi_runtime_limits.json").read_text(encoding="utf-8"))
if (set(_values) != {"max_output_tokens", "max_requests", "planning_timeout_seconds", "parent_timeout_grace_seconds"}
        or any(type(value) is not int or not 0 < value <= MAX_SAFE_TOKEN_COUNT for value in _values.values())):
    raise ValueError("Invalid Pi runtime limits configuration")
# This historical setting is a default for profiles without declared limits,
# not an upper bound on explicitly selected model output.
DEFAULT_MAX_OUTPUT_TOKENS = _values["max_output_tokens"]
MAX_OUTPUT_TOKENS = DEFAULT_MAX_OUTPUT_TOKENS  # Compatibility for old consumers.
MAX_REQUESTS = _values["max_requests"]
PLANNING_TIMEOUT_SECONDS = _values["planning_timeout_seconds"]
PROCESS_TIMEOUT_SECONDS = PLANNING_TIMEOUT_SECONDS + _values["parent_timeout_grace_seconds"]
MAX_USAGE_RECORDS = MAX_REQUESTS + 1  # Includes a local terminal error after the last invocation.
MAX_PROGRESS_BYTES = 16 * 1024 * 1024  # Bounded safe snapshots from a longer planning session.
