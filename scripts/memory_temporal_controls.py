"""Private evaluation controls. Never imported by the application or UI.

Delegated windows only reduce Pi's allowance; the original UIRegistry still
reserves, settles, and quarantines unknown consumption. Frozen search re-resolves
every selected original item, rather than treating a snapshot as authority.
"""
from contextlib import contextmanager
from copy import deepcopy

from repo_maintainer.models import SafetyError
from repo_maintainer.task_session import fingerprint, require
from repo_maintainer.ui_registry import DurableProvider


class RequestWindow:
    def __init__(self, registry, ceiling):
        require(type(ceiling) is int and 1 <= ceiling <= 100, 'Invalid delegated ceiling')
        budget = registry.budget()
        require(budget['requests_reserved_unknown'] == 0, 'Unknown consumption blocks evaluation')
        require(ceiling <= budget['requests_remaining'], 'Delegation cannot expand the original budget')
        self.registry, self.ceiling, self.start = registry, ceiling, budget['requests_used']

    def remaining(self, minimum=1):
        budget = self.registry.budget()
        require(not budget['requests_reserved_unknown'], 'Unknown consumption blocks evaluation')
        used = budget['requests_used'] - self.start
        require(0 <= used <= self.ceiling, 'Delegated usage moved outside its original window')
        left = min(self.ceiling - used, budget['requests_remaining'])
        require(left >= minimum, 'Delegated evaluation window exhausted before reservation')
        return left


class _LimitedRegistry:
    def __init__(self, original, window):
        self.original, self.window = original, window

    def __getattr__(self, name):
        return getattr(self.original, name)

    def reserve(self):
        left = self.window.remaining()
        # Same original journal, atomic bounded reservation. Historical unknown
        # reservations are never changed or retroactively reduced.
        return self.original.reserve(maximum=left)


class LimitedDurableProvider(DurableProvider):
    def __init__(self, original, window):
        require(original.registry is window.registry, 'Delegation requires the same original journal')
        super().__init__(original.inner, _LimitedRegistry(original.registry, window))
        self.task_planner = original.task_planner
        self.window = window

    def _reserved_invoke(self, method, *args, task_turn=None):
        # Compact requires two available requests in the existing controller.
        # Known local exhaustion must not consume a fresh durable reservation.
        self.window.remaining(2 if method == 'compact' else 1)
        return super()._reserved_invoke(method, *args, task_turn=task_turn)


class FrozenMaintenance:
    def __init__(self, memory, issue):
        self.memory, self.issue = memory, issue
        self.packet = deepcopy(memory.search(issue))
        self.sha256 = fingerprint(self.packet)

    def search(self, issue):
        require(issue == self.issue and fingerprint(self.packet) == self.sha256,
                'Frozen maintenance query or packet changed')
        for row in self.packet['hits']:
            ref = row['source_ref']
            require(self.memory.resolve(ref['kind'], ref['id']) == row,
                    'Original frozen maintenance evidence changed')
        return deepcopy(self.packet)


@contextmanager
def profile(session, arm, owned_root, frozen):
    """Ablate optional memory only inside an explicitly owned disposable fixture.

    No product switch exists. Git/Policy/source/test/approval/budget gates stay
    original. All instance wiring is restored even when a run fails.
    """
    from pathlib import Path
    from repo_maintainer.git import RepositoryGateway
    arms = ('no_memory', 'finite_summary', 'native_compaction',
            'native_maintenance_experience', 'native_maintenance_experience_knowledge')
    root = Path(owned_root).resolve()
    require(arm in arms and session.repo == root / 'source'
            and RepositoryGateway(session.repo).common_dir == session.repo / '.git'
            and (root / 'temporal-owner.json').is_file(), 'Profile requires its owned disposable repository')
    import json
    require(json.loads((root / 'temporal-owner.json').read_bytes()) ==
            {'schema_version': 1, 'kind': 'authored-temporal-evaluation'}, 'Fixture ownership receipt changed')
    require(frozen.memory is session.maintenance, 'Frozen projection belongs to another session')
    saved = []

    def setvalue(obj, name, value):
        saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    try:
        rich = arm in arms[3:]
        inner = session.provider.inner
        if not rich:
            for name in ('experience', 'maintenance'):
                setvalue(session.task_planner, name, None)
                setvalue(session, name, None)
                setvalue(inner, '_' + name + '_context', None)
        else:
            setvalue(session.maintenance, 'search', frozen.search)
        if arm != arms[-1]:
            setvalue(session.task_planner, 'knowledge', None)
            setvalue(session, 'knowledge', None)
            setvalue(inner, '_knowledge_context', None)
        if arm in arms[:2]:
            for obj, name in ((session, 'task_planner'), (session.provider, 'task_planner'),
                              (session, 'failure_feedback'), (session, 'feedback_cleanup'),
                              (session, 'compaction')):
                setvalue(obj, name, None)
            if arm == arms[0]:
                setvalue(inner, 'revise', lambda repository, issue, tests, feedback:
                         inner.plan(repository, issue, tests))
        yield
    finally:
        for obj, name, value in reversed(saved):
            setattr(obj, name, value)
