"""Bounded model projection of already verified claims; no policy selection."""
from .models import SafetyError, digest
from .source_memory import canonical
from .memory_collection import HEX


def with_relations(context, verified):
    fields = {"schema_version", "index_sha256", "query_sha256", "hits", "context_sha256"}
    if (not isinstance(context, dict) or set(context) != fields
            or type(context["schema_version"]) is not int or context["schema_version"] not in {1, 2}
            or context["context_sha256"] != digest(canonical({k: v for k, v in context.items() if k != "context_sha256"}))
            or not isinstance(verified, dict) or verified.get("status") != "verified"
            or verified.get("claims_only") is not True or verified.get("runtime_authorized") is not False
            or not isinstance(verified.get("manifest_sha256"), str) or not HEX.fullmatch(verified["manifest_sha256"])
            or not isinstance(verified.get("relations"), list) or len(verified["relations"]) > 50):
        raise SafetyError("Verified source and relation evidence required for Pi context")
    hits = {row["id"] for row in context["hits"]}
    # The underlying verifier permits only source-marker document endpoints.
    relations = [{**row, **{role: {**row[role], "kind": "document", "classification_origin": "source_marker"}
                           for role in ("newer", "older")}}
                 for row in verified["relations"] if row["newer"]["id"] in hits or row["older"]["id"] in hits]
    data = {"schema_version": 3, "source_schema_version": context["schema_version"],
            **{key: context[key] for key in ("index_sha256", "query_sha256", "hits")},
            "decision_relations": {"schema_version": 1, "manifest_sha256": verified["manifest_sha256"],
                                   "claims_only": True, "runtime_authorized": False, "relations": relations}}
    result = {**data, "context_sha256": digest(canonical(data))}
    if len(canonical(result)) > 256*1024:
        raise SafetyError("Pi relation context exceeds its bounded size")
    return result
