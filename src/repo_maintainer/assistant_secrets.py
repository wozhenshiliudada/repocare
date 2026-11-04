"""Inspect decoded JSON values; serialization can hide quotes and backslashes."""


def _credentials(values):
    if isinstance(values, str):
        values = (values,)
    return tuple(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def contains_credential(value, credentials):
    credentials = _credentials(credentials)
    pending, seen = [value], set()
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if any(key in current for key in credentials):
                return True
        elif isinstance(current, (dict, list, tuple)):
            if id(current) in seen:
                continue
            seen.add(id(current))
            if isinstance(current, dict):
                pending.extend(current.keys())
                pending.extend(current.values())
            else:
                pending.extend(current)
    return False


def redact_credentials(value, credentials):
    """Return a public copy only. Original records and Native bytes stay untouched."""
    credentials = _credentials(credentials)
    def public(current):
        if isinstance(current, str):
            for key in credentials:
                current = current.replace(key, '[REDACTED]')
            return current
        if isinstance(current, dict):
            return {public(key): public(item) for key, item in current.items()}
        if isinstance(current, list):
            return [public(item) for item in current]
        return current
    return public(value)
