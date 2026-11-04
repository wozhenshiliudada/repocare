"""Validate the adapter's bounded safe evidence; never accept provider text as metadata."""
from __future__ import annotations

import json
import copy
import math
import re

from .models import SafetyError
from .runtime_limits import (PROCESS_TIMEOUT_SECONDS, MAX_SAFE_TOKEN_COUNT, PLANNING_TIMEOUT_SECONDS,
                             MAX_USAGE_RECORDS, MAX_PROGRESS_BYTES)

PREFIX = b"PI_EVIDENCE_V1 "
CATEGORIES = {"runtime_error", "provider_error", "runtime_timeout", "request_budget", "no_plan", "output_truncated", "tool_budget", "context_budget"}
INVESTIGATION_PURPOSE = 'repair-investigation-v1'
INVESTIGATION_TOOLS = {'read_source', 'propose_repair',
                       'search_approved_sources', 'read_approved_source'}


def matches_model_limits(evidence, limits):
    """New selected limits require an exact receipt; old omitted fields stay readable."""
    return (evidence.get('max_output_tokens') == limits['max_output_tokens']
            if limits['limits_explicit'] or 'max_output_tokens' in evidence else True)


def validate_evidence(data: dict, allowance: int) -> dict:
    fields = {"schema_version", "status", "failure_category", "pi_version", "active_tools", "requests",
              "requests_started", "count_scope", "consumption", "terminal_count", "tool_calls", "events", "usage",
              "events_complete", "events_dropped", "transport", "source_snapshot_sha256", "credential_source",
              "model_cost_verified", "upstream_commit", "memory_context_sha256", "structure_context_sha256",
              "edit_protocol", "tool_budget", "max_output_tokens", "planning_timeout_seconds",
              "revision_context_sha256", "read_stats", "continuation_feedback_sha256", "experience_context_sha256", "baseline_context_sha256", "review_context_sha256", "behavior_contract_sha256",
              "purpose", "reflection_context_sha256", "knowledge_context_sha256", "knowledge_candidate_sha256",
              "compaction_context_sha256", "compaction_entry_sha256", "compaction_max_output_tokens", "context_policy_sha256", "maintenance_context_sha256", "maintenance_selection_sha256", "memory_load_sha256", "memory_task_type", "validation_context_sha256", "validation_proposal_sha256",
              "investigation_scope_sha256"}
    def require(condition):
        if not condition:
            raise SafetyError("Invalid safe Pi evidence protocol")
    def count(value, maximum):
        return type(value) is int and 0 <= value <= maximum
    require(isinstance(data, dict) and not set(data) - fields and type(data.get("schema_version")) is int and data["schema_version"] == 1)
    reflection = data.get('purpose') == 'memory_reflection'
    compaction = data.get('purpose') == 'task_compaction'
    validation = data.get('purpose') == 'validation-proposal-v1'
    investigation = data.get('purpose') == INVESTIGATION_PURPOSE
    require(investigation == ('investigation_scope_sha256' in data))
    require('purpose' not in data or reflection or compaction or validation or investigation)
    require(not investigation or not (reflection or compaction or validation))
    allowed_tools = (set() if compaction else {'propose_memory'} if reflection else
                     {'read_source','propose_validation'} if validation else
                     INVESTIGATION_TOOLS if investigation else {'propose_repair', 'read_source'})
    for field in ('reflection_context_sha256', 'knowledge_context_sha256', 'knowledge_candidate_sha256',
                  'compaction_context_sha256','compaction_entry_sha256','context_policy_sha256','maintenance_context_sha256','maintenance_selection_sha256','memory_load_sha256','baseline_context_sha256','review_context_sha256','behavior_contract_sha256','validation_context_sha256','validation_proposal_sha256',
                  'investigation_scope_sha256'):
        if field in data:
            require(isinstance(data[field], str) and re.fullmatch('[a-f0-9]{64}', data[field]))
    require(reflection == ('reflection_context_sha256' in data))
    require(('maintenance_context_sha256' in data)==('maintenance_selection_sha256' in data))
    require(('memory_load_sha256' in data)==('memory_task_type' in data))
    if 'memory_task_type' in data:require(not reflection and not compaction and data['memory_task_type'] in {'bug_fix','commit_inspection'})
    require('knowledge_candidate_sha256' not in data or reflection)
    require('baseline_context_sha256' not in data or (not reflection and not compaction))
    require('behavior_contract_sha256' not in data or (not reflection and not compaction))
    require(validation == ('validation_context_sha256' in data))
    require('validation_proposal_sha256' not in data or validation)
    if validation and data.get('status') == 'completed':require('validation_proposal_sha256' in data)
    require(compaction == ('compaction_context_sha256' in data))
    require('compaction_entry_sha256' not in data or compaction)
    require('compaction_max_output_tokens' not in data or (compaction and type(data['compaction_max_output_tokens']) is int
            and 1<=data['compaction_max_output_tokens']<=MAX_SAFE_TOKEN_COUNT))
    if compaction and data.get('status')=='completed':require('compaction_entry_sha256' in data)
    if reflection and data.get('status')=='completed':
        require('knowledge_candidate_sha256' in data)
    status = data.get("status")
    require(status in {"running", "failed", "completed"})
    require(data.get("failure_category") in CATEGORIES | {None})
    require(data.get("count_scope") == "adapter_stream_invocations" and data.get("model_cost_verified") is False)
    counted_failure = 'terminal_count' in data
    if counted_failure:
        receipt = data['terminal_count']
        require(status == 'failed' and not reflection and not compaction
                and data.get('failure_category') in {'request_budget', 'no_plan'})
        require(isinstance(receipt, dict) and set(receipt) == {'schema_version', 'completed_streams'}
                and type(receipt['schema_version']) is int and receipt['schema_version'] == 1)
        require(count(receipt['completed_streams'], allowance) and receipt['completed_streams'] > 0
                and receipt['completed_streams'] == data.get('requests') == data.get('requests_started'))
        if data['failure_category'] == 'request_budget':
            require(data['requests'] == allowance)
        require(data.get('events_complete') is True and data.get('pi_version') == '1.0.4'
                and data.get('upstream_commit') == '7c10bd4337495ee613f2224843ecdf349b80d1df'
                and 'source_snapshot_sha256' in data and data.get('edit_protocol') == 'exact-replacements-v1')
    reported = status == 'completed' or counted_failure
    require(data.get("consumption") == ("reported" if reported else "unknown"))
    require(count(data.get("requests"), allowance) if reported else data.get("requests") is None)
    require(data.get("requests_started") is None or count(data["requests_started"], allowance))
    require(type(data.get("events_complete")) is bool)
    if status == "completed":
        require(data["events_complete"] and data.get("failure_category") is None and data["requests"] == data["requests_started"])
    elif status == "failed":
        require(data.get("failure_category") in CATEGORIES)
    if "pi_version" in data:
        require(data["pi_version"] == "1.0.4")
    if "upstream_commit" in data:
        require(data["upstream_commit"] == "7c10bd4337495ee613f2224843ecdf349b80d1df")
    if "transport" in data:
        require(data["transport"] in {"live", "loopback-mock"})
    if "credential_source" in data:
        require(data["credential_source"] in {"synthetic", "environment", "openclaw-readonly"})
    if "source_snapshot_sha256" in data:
        require(isinstance(data["source_snapshot_sha256"], str) and re.fullmatch("[0-9a-f]{64}", data["source_snapshot_sha256"]))
    if "memory_context_sha256" in data:
        require(isinstance(data["memory_context_sha256"], str) and re.fullmatch("[0-9a-f]{64}", data["memory_context_sha256"]))
    if 'experience_context_sha256' in data:
        require(isinstance(data['experience_context_sha256'], str) and re.fullmatch('[0-9a-f]{64}', data['experience_context_sha256']))
    if "structure_context_sha256" in data:
        require(isinstance(data["structure_context_sha256"], str) and re.fullmatch("[0-9a-f]{64}", data["structure_context_sha256"]))
    if "revision_context_sha256" in data:
        require(isinstance(data["revision_context_sha256"], str) and re.fullmatch("[0-9a-f]{64}", data["revision_context_sha256"]))
    if "continuation_feedback_sha256" in data:
        require(isinstance(data['continuation_feedback_sha256'],str) and re.fullmatch('[0-9a-f]{64}',data['continuation_feedback_sha256']))
    if "active_tools" in data:
        require(data["active_tools"] in [[], sorted(allowed_tools)])
    if investigation:
        # Never hide the added tools by omitting the real active-tool receipt.
        require(data.get("active_tools") == sorted(allowed_tools))
    if "edit_protocol" in data:
        require(data["edit_protocol"] == "exact-replacements-v1")
    # Persisted receipts from the released 30-minute runtime remain readable.
    # This does not grant a historical journal a new budget or resume authority.
    if 'max_output_tokens' in data:
        require(type(data['max_output_tokens']) is int and 1 <= data['max_output_tokens'] <= MAX_SAFE_TOKEN_COUNT)
    for field, accepted in [("planning_timeout_seconds", {1800, PLANNING_TIMEOUT_SECONDS})]:
        if field in data:
            require(type(data[field]) is int and data[field] in accepted)
    if "tool_budget" in data:
        require(type(data["tool_budget"]) is int and 4 <= data["tool_budget"] <= allowance * 4)
    # Historical evidence with the old eight-tool guard remains readable.
    tool_limit = data.get("tool_budget", 8)
    if "read_stats" in data:
        stats = data["read_stats"]
        require(isinstance(stats, dict) and set(stats) == {"read_requests", "read_repeats", "explicit_rereads"})
        require(all(count(value, data.get("tool_calls", tool_limit)) for value in stats.values()))
        require(stats["read_repeats"] <= stats["read_requests"] and stats["explicit_rereads"] <= stats["read_requests"])
    for field, maximum in [("tool_calls", tool_limit + 1), ("events_dropped", 10000)]:
        if field in data:
            require(count(data[field], maximum))
    require(isinstance(data.get("events"), list) and len(data["events"]) <= 128)
    for event in data["events"]:
        require(isinstance(event, dict))
        kind = event.get("type")
        if kind in {"agent_start", "agent_end"}:
            require(set(event) == {"type"})
        elif kind == "assistant_end":
            require(set(event) == {"type", "stop_reason"} and event["stop_reason"] in {"stop", "length", "toolUse", "error", "aborted", "unknown"})
        else:
            require(kind in {"tool_execution_start", "tool_execution_end", "maintenance_tool"})
            require(not set(event) - {"type", "tool", "is_error", "call", "path"} and event.get("tool") in allowed_tools)
            if 'path' in event:
                from .models import safe_context_path
                require(event.get('tool') == 'read_source' and isinstance(event['path'],str) and len(event['path']) <= 256)
                safe_context_path(event['path'])
            require("is_error" not in event or type(event["is_error"]) is bool)
            require("call" not in event or count(event["call"], tool_limit))
    require(isinstance(data.get("usage"), list) and len(data["usage"]) <= MAX_USAGE_RECORDS)
    for usage in data["usage"]:
        require(isinstance(usage, dict) and not set(usage) - {"input", "output", "cacheRead", "cacheWrite", "totalTokens"})
        require(all(type(n) in {int, float} and math.isfinite(n) and n >= 0 for n in usage.values()))
    return data


class CountedPlanningFailure(SafetyError):
    """Controller receipt for a validated terminal Pi failure, not a usable plan.

    PiProvider checks process exit, source and context bindings; AssistantControl
    checks process exit, operation binding and the exact appended Native error.
    A generic exception carrying a similar dictionary is insufficient.
    """
    def __init__(self, receipt, allowance):
        if isinstance(receipt, dict) and receipt.get('purpose') == 'conversation-v1':
            # AssistantControl additionally checks the exact operation, process
            # exit and append-only Native error suffix before constructing this.
            from .assistant_receipts import validate_terminal_receipt
            value = validate_terminal_receipt(receipt, allowance)
        else:
            value = validate_evidence(receipt, allowance)
        if value['status'] != 'failed' or 'terminal_count' not in value:
            raise SafetyError('Missing terminal failed-planning count')
        super().__init__('Pi planning failed; inspect safe runtime evidence')
        self.receipt, self.allowance = copy.deepcopy(value), allowance

    def matches(self, evidence, allowance):
        return (allowance == self.allowance
                and all(key in evidence and evidence[key] == value for key, value in self.receipt.items()))


def progress_evidence(raw: bytes, allowance: int) -> dict | None:
    """Use only complete, validated protocol lines, ignoring arbitrary stderr and torn tail."""
    result = None
    if len(raw) > MAX_PROGRESS_BYTES:
        return None
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n") or not line.startswith(PREFIX) or len(line) > 128000:
            continue
        try:
            value = validate_evidence(json.loads(line[len(PREFIX):]), allowance)
            if value["status"] == "running" and not value["events_complete"]:
                result = value
        except (SafetyError, ValueError, TypeError):
            continue
    return result
