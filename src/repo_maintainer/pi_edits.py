"""Independently reconstruct Pi's exact snapshot replacements before accepting a plan."""
from .models import SafetyError

EDIT_PROTOCOL = "exact-replacements-v1"


def verify_materialized_plan(data: dict, files: list[dict]) -> None:
    def require(condition):
        if not condition:
            raise SafetyError("Invalid Pi exact-replacement receipt")

    require(data["evidence"].get("edit_protocol") == EDIT_PROTOCOL)
    patches, edits = data.get("patches"), data["plan"]["edits"]
    require(isinstance(patches, list) and isinstance(edits, list) and 1 <= len(patches) == len(edits) <= len(files))
    snapshots = {file["path"]: file for file in files}
    seen = set()
    for patch, edit in zip(patches, edits):
        require(isinstance(patch, dict) and set(patch) == {"path", "replacements"})
        name = patch["path"]
        require(isinstance(name, str) and name in snapshots and name not in seen)
        seen.add(name)
        source = snapshots[name]
        require(source.get("editable") is not False)
        replacements = patch["replacements"]
        require(isinstance(replacements, list) and 1 <= len(replacements) <= 32)
        original, ranges = source["content"], []
        for replacement in replacements:
            require(isinstance(replacement, dict) and set(replacement) == {"oldText", "newText"})
            old, new = replacement["oldText"], replacement["newText"]
            require(isinstance(old, str) and old and isinstance(new, str) and old != new)
            start = original.find(old)
            require(start >= 0 and original.find(old, start + 1) == -1)
            ranges.append((start, start + len(old), new))
        ranges.sort(key=lambda item: item[0])
        require(all(right[0] >= left[1] for left, right in zip(ranges, ranges[1:])))
        materialized = original
        for start, end, new in reversed(ranges):
            materialized = materialized[:start] + new + materialized[end:]
        require(materialized != original and len(materialized.encode("utf-8")) <= 100000)
        require(isinstance(edit, dict) and set(edit) == {"path", "before_sha256", "content"})
        require(edit == {"path": name, "before_sha256": source["before_sha256"], "content": materialized})
