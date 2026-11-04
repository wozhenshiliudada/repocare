"""Untrusted proposed knowledge is data, never a capability or confirmed policy."""
from .models import SafetyError
from .store import redact
from .task_session import require

FIELDS = {'kind', 'title', 'summary', 'triggers', 'preconditions', 'steps', 'verification', 'stop_conditions'}


def validate_note(value):
    require(isinstance(value, dict) and set(value) == FIELDS, 'Invalid knowledge candidate fields')
    require(value['kind'] in {'knowledge', 'procedure'}, 'Invalid knowledge type')
    for key, maximum in [('title', 160), ('summary', 1200)]:
        _text(value[key], maximum)
    for key in FIELDS - {'kind', 'title', 'summary'}:
        rows = value[key]
        require(isinstance(rows, list) and 1 <= len(rows) <= 8, 'Knowledge lists require 1-8 items')
        for row in rows:
            _text(row, 500)
    import json
    require(len(json.dumps(value, ensure_ascii=False).encode()) <= 10000, 'Knowledge candidate exceeds byte limit')
    return value


def _text(value, maximum):
    require(isinstance(value, str) and value.strip() and len(value) <= maximum
            and not any(ord(c) < 32 for c in value), 'Invalid knowledge text')
    if redact(value) != value:
        raise SafetyError('Knowledge candidate contains recognized sensitive content')
