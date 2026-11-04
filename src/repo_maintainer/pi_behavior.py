"""Read-only behavior expectations for the existing repair AgentSession.

The policy already pins the entire contract. This projection does not select,
approve, execute or change a contract and grants no additional model tools.
"""
from contextlib import contextmanager
import copy
import json

from .behavior_validation import validate_contract
from .models import SafetyError, digest


def planning_context(contract, repository, issue, tests, paths, *, source=None):
    if contract is None:
        if any(command and command[0] == 'repocare:behavior-v1' for command in tests):
            raise SafetyError('Pinned behavior checks need their planning expectations')
        return None
    from .task_session import repository_identity
    from .recovery import unlinked
    validate_contract(contract)
    origin = source if source is not None else repository
    markers = [tuple(command) for command in tests
               if command and command[0] == 'repocare:behavior-v1']
    if (markers != [('repocare:behavior-v1', contract['contract_sha256'])]
            or contract['repository_id'] != repository_identity(origin.root, origin.common_dir)
            or unlinked(repository.common_dir) != unlinked(origin.common_dir)
            or contract['base_sha'] != repository.head or contract['base_sha'] != origin.head
            or contract['request_sha256'] != digest(issue.encode('utf-8'))
            or any(case['module'] not in paths for case in contract['cases'])):
        raise SafetyError('Behavior planning expectations differ from the original repair scope')
    # Preserve Python's exact canonical bytes, including 1.0 vs 1. A Node
    # JSON reserialization is not an equivalent cross-language byte hash.
    body = {key: value for key, value in contract.items() if key != 'contract_sha256'}
    return {'body_json': json.dumps(body, sort_keys=True, separators=(',', ':'),
                                  ensure_ascii=False, allow_nan=False),
            'contract_sha256': contract['contract_sha256']}


@contextmanager
def bound_expectations(provider, contract, source=None):
    """Temporary controller binding; reused provider instances cannot leak it."""
    from .pi_provider import PiProvider
    from .ui_registry import DurableProvider
    target = provider.inner if isinstance(provider, DurableProvider) else provider
    if not isinstance(target, PiProvider):
        yield
        return
    old = target._behavior_contract
    old_source = target._behavior_source
    if old is not None or old_source is not None:
        raise SafetyError('Pi behavior expectations are already owned by another repair')
    target._behavior_contract = copy.deepcopy(contract)
    target._behavior_source = source if contract is not None else None
    try:
        yield
    finally:
        target._behavior_contract = old
        target._behavior_source = old_source
