"""Explicit UI memory scope. Retrieval grants no execution or budget rights."""
from __future__ import annotations

from .models import SafetyError, digest
from .recovery import unlinked
from .pi_provider import PiProvider
from .source_memory import LABELS, location, search_memory, sources
from .store import redact
from .ui_backup import read_bytes


def citations(rows):
    # Deliberately exclude excerpts, SQL and absolute paths selected by documents.
    fields = ("id", "topic", "path", "start_line", "end_line", "kind", "classification",
              "classification_origin", "source_sha256", "excerpt_sha256")
    return [{**{key: row[key] for key in fields}, **({"origin": row["origin"]} if "origin" in row else {})}
            for row in rows]


class UIMemory:
    def __init__(self, provider, repository):
        root, index = getattr(provider, "memory_root", None), getattr(provider, "memory_index", None)
        self.relations_path = getattr(provider, "memory_relations", None)
        self.relations_context = getattr(provider, "memory_relations_context", False)
        if type(self.relations_context) is not bool or self.relations_context and not self.relations_path:
            raise SafetyError("Pi relation context requires explicit decision relations")
        if bool(root) != bool(index):
            raise SafetyError("Explicit UI memory root and index must be supplied together")
        if self.relations_path and not root:
            raise SafetyError("UI decision relations require explicit memory root and index")
        if not root:
            self.binding = None
            return
        if not isinstance(provider, PiProvider):
            raise SafetyError("UI source memory requires the real Pi provider")
        self.root, self.repository, self.index = location(root, repository, index)
        self.binding = {"schema_version": 1, "source_root": str(self.root),
                        "index": str(self.index), "index_sha256": digest(read_bytes(self.index, 16*1024*1024))}
        provider.memory_root, provider.memory_index = self.root, self.index
        provider._memory_index_sha = self.binding["index_sha256"]
        provider._memory_relations_guard = None
        if self.relations_path:
            self.relations_path = unlinked(self.relations_path)
            self.binding["decision_relations"] = {"schema_version": 1,
                "path_sha256": digest(str(self.relations_path).encode()),
                "manifest_sha256": digest(read_bytes(self.relations_path, 128*1024))}
            if self.relations_context:
                self.binding["decision_relations"]["model_context"] = True
            provider._memory_relations_guard = self.verify_relations

    def verify_relations(self):
        if not self.relations_path:
            return None
        from .decision_relations import verify_relations
        pinned = self.binding["decision_relations"]["manifest_sha256"]
        if digest(read_bytes(self.relations_path, 128*1024)) != pinned:
            raise SafetyError("UI decision relations file changed; preserve the bound journal")
        result = verify_relations(self.relations_path, self.root, self.repository, self.index)
        if result["manifest_sha256"] != pinned or digest(read_bytes(self.relations_path, 128*1024)) != pinned:
            raise SafetyError("UI decision relations changed during verification")
        return {"enabled": True, "status": "verified", "claims_only": True, "runtime_authorized": False,
                "manifest_sha256": pinned, "path_sha256": self.binding["decision_relations"]["path_sha256"],
                "relations": result["relations"]}

    def related(self, result):
        value = result.get("decision_relations") if result else None
        if value is None:
            return None
        ids = {r["id"] for r in result["context"]["hits"]}
        return {**value, "relations": [r for r in value["relations"]
                                      if r["newer"]["id"] in ids or r["older"]["id"] in ids]}

    def verify(self, query):
        if not self.binding:
            return None
        if digest(read_bytes(self.index, 16*1024*1024)) != self.binding["index_sha256"]:
            raise SafetyError("UI memory index changed; preserve the bound journal and index")
        self.verify_relations()
        result = search_memory(self.root, self.repository, self.index, query)
        if result["context"]["index_sha256"] != self.binding["index_sha256"]:
            raise SafetyError("UI memory index changed during verification")
        relation_result = self.verify_relations()
        if self.relations_context:
            from .pi_memory_relations import with_relations
            result = {**result, "context": with_relations(result["context"], relation_result)}
        return {**result, **({"decision_relations": relation_result} if relation_result else {})}

    def require_current(self, query):
        result = self.verify(query)
        if result and result["mixed_topics"]:
            raise SafetyError("Mixed decision classifications require review before memory injection")
        return result

    def preview(self, query):
        result = self.verify(query)
        if result is None:
            raise SafetyError("Source memory was not enabled at server startup")
        context = result["context"]
        return {"status": "mixed_classifications" if result["mixed_topics"] else "verified",
                "runtime_authorized": False, "mixed_topics": result["mixed_topics"],
                "context_sha256": context["context_sha256"], "query_sha256": context["query_sha256"],
                "index_sha256": context["index_sha256"], "citations": citations(context["hits"]),
                **({"decision_relations": self.related(result)} if self.relations_path else {})}

    def query_rows(self):
        """Verified original excerpts for UI-only ranking; never replaces the Pi source protocol."""
        if not self.binding:return []
        self.verify('verification')
        _,rows=sources(self.root)
        self.verify('verification')
        return rows

    def view(self):
        if not self.binding:
            return {"enabled": False, "status": "disabled", "runtime_authorized": False}
        base = {"enabled": True, "runtime_authorized": False, "binding": self.binding}
        try:
            self.verify("verification")
            _, rows = sources(self.root)
            # Recheck after constructing the projection, including the original index pin.
            checked = self.verify("verification")
            topics = {}
            for row in rows:
                if row["classification"] in LABELS:
                    topics.setdefault(row["topic"], set()).add(row["classification"])
            return {**base, "status": "verified", "sources": citations(rows),
                    "mixed_topics": sorted(topic for topic, values in topics.items() if len(values) > 1),
                    **({"decision_relations": checked["decision_relations"]} if self.relations_path else {})}
        except (SafetyError, OSError, ValueError) as error:
            return {**base, "status": "invalid", "sources": [], "mixed_topics": [],
                    "error": redact(str(error))}
