"""One read-only view snapshot, fully revalidated before it can be returned.

No global/TTL cache and no execution authority. Planning and approvals do not
enter this scope. Validity cannot survive a request, error or source drift.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from pathlib import Path

from .models import SafetyError
from .recovery import unlinked

_view = ContextVar('repo_maintainer_verified_source_view', default=None)
MAX_ROOTS = 32


@contextmanager
def verified_source_view():
    if _view.get() is not None:
        yield
        return
    entries = {}
    token = _view.set(entries)
    try:
        yield
        # Closing reads must execute original validation, including every archive
        # dependency. Comparing just catalog bytes would miss changed references.
        _view.set(None)
        for (root, allow), (loader, expected) in entries.items():
            if loader(Path(root), _allow_collection=allow) != expected:
                raise SafetyError('Memory source changed while rendering; refresh the view')
    finally:
        _view.reset(token)


def source_snapshot(loader):
    @wraps(loader)
    def read(root, *, _allow_collection=True):
        entries = _view.get()
        if entries is None:
            return loader(root, _allow_collection=_allow_collection)
        key = (str(unlinked(Path(root))), _allow_collection)
        if key not in entries:
            if len(entries) >= MAX_ROOTS:
                raise SafetyError('Read-only source view exceeds its root limit')
            value = loader(root, _allow_collection=_allow_collection)
            entries[key] = (loader, deepcopy(value))
        return deepcopy(entries[key][1])
    return read
