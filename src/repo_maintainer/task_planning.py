"""Controller-owned production Pi task binding. Original journal remains authoritative."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .git import RepositoryGateway
from .git_investigation import validate_roots
from .models import SafetyError
from .pi_investigation import InvestigationBridge, investigation_context, validate_config
from .task_investigation import TaskInvestigation
from .task_session import KIND, TaskSessionStore, fingerprint, repository_identity, require, unlinked


class TaskPlanner:
    def __init__(self, registry, source, policy, provider, sources, sources_guard=None, link_registry=None):
        self.registry, self.source, self.policy, self.provider = registry, Path(source), policy, provider
        self.link_registry = link_registry or registry
        self.sources = sources
        self.sources_guard = sources_guard
        self.store = TaskSessionStore(registry, self.source, RepositoryGateway(self.source).common_dir)
        self.identity = None
        self.scope = None
        self.feedback_guard = None
        self.feedback = None
        self.experience = None
        self.knowledge = None
        self.compaction = None
        self.maintenance = None
        # Explicit R4 opt-in only; absent means the historical two-tool task.
        self.investigation_config = None
        self.investigation = None
        self.investigation_expiry = None
        self.investigation_scope_sha256 = None
        self.controller_read_scope = None

    def use_controller_read_roots(self, read_roots, *, max_calls=50, max_output_bytes=262144):
        """Only an original maintenance controller supplies these approved roots.

        New tasks opt in at begin; original legacy continuations stay legacy.
        Reading a saved context does not allocate a scope or reset its counters.
        """
        config={'read_roots':tuple(validate_roots(read_roots)), 'max_calls':max_calls,
                'max_output_bytes':max_output_bytes, 'ttl_seconds':3600}
        validate_config(config)
        require(self.identity is None and self.investigation_config is None,
                'Controller read authority must be configured before a task')
        require(self.controller_read_scope is None or self.controller_read_scope==config,
                'Original controller read authority cannot change')
        self.controller_read_scope=config

    def restore_controller_read_scope(self, original_task_id=None):
        if self.controller_read_scope is None:return
        config=self.controller_read_scope
        if original_task_id is not None:
            self.store.directory(original_task_id)  # Validate the original ID, no allocation.
            record=self.registry.load(KIND).get(original_task_id)
            require(record is not None,'Original Pi task is missing; cannot grant replacement reads')
            context=record['context']
            if context.get('purpose')!='repair-investigation-v1':
                self.investigation_config=None
                return
            from .pi_investigation import validate_investigation_context
            validate_investigation_context(context)
            bound=context['investigation']
            require(bound['read_roots']==list(config['read_roots'])
                    and bound['max_calls']==config['max_calls']
                    and bound['max_output_bytes']==config['max_output_bytes'],
                    'Original investigation differs from current controller authority')
        self.investigation_config=config

    def enable_investigation(self, read_roots, *, max_calls=50, max_output_bytes=262144,
                             ttl_seconds=3600):
        """Explicit controller opt-in for one new native scope. Never implicit."""
        config = {'read_roots': tuple(validate_roots(read_roots)), 'max_calls': max_calls,
                  'max_output_bytes': max_output_bytes, 'ttl_seconds': ttl_seconds}
        validate_config(config)
        require(self.identity is None, 'Investigation scope must be enabled before its Pi task begins')
        if self.investigation_config is not None:
            require(self.investigation_config == config, 'Investigation scope cannot be replaced')
            return
        self.investigation_config = config

    def _investigation_scope_args(self, task_id, scope, expires_at):
        return dict(repository_id=repository_identity(self.source,
                                                      RepositoryGateway(self.source).common_dir),
                    base_sha=scope['base_sha'], read_roots=self.investigation_config['read_roots'],
                    policy_sha256=scope['policy_sha256'], expires_at=expires_at,
                    max_calls=self.investigation_config['max_calls'],
                    max_output_bytes=self.investigation_config['max_output_bytes'])

    def _ensure_investigation(self, task_id, scope):
        if self.investigation_config is None:
            self.investigation = self.investigation_expiry = self.investigation_scope_sha256 = None
            return
        broker = TaskInvestigation(self.registry, RepositoryGateway(self.source), task_id)
        stored = broker.existing_scope()
        if stored is not None:
            # Restart/repeat registration reuses the original expiry and counters.
            require(stored['read_roots'] == list(self.investigation_config['read_roots'])
                    and stored['max_calls'] == self.investigation_config['max_calls']
                    and stored['max_output_bytes'] == self.investigation_config['max_output_bytes'],
                    'Existing investigation scope differs from the explicit controller request')
            expires_at = stored['expires_at']
        else:
            expires_at = (datetime.now(timezone.utc) +
                          timedelta(seconds=self.investigation_config['ttl_seconds'])).isoformat()
        digest = broker.register(**self._investigation_scope_args(task_id, scope, expires_at))
        self.investigation, self.investigation_expiry = broker, expires_at
        self.investigation_scope_sha256 = digest

    def _verify_investigation(self, task_id, scope):
        if self.investigation_config is None:
            require(self.investigation is None, 'Unexpected investigation scope')
            return
        require(self.investigation is not None and self.investigation_expiry is not None
                and self.investigation_scope_sha256 is not None, 'Missing original investigation scope')
        digest = self.investigation.register(
            **self._investigation_scope_args(task_id, scope, self.investigation_expiry))
        require(digest == self.investigation_scope_sha256, 'Original investigation scope changed')

    def current_scope(self, issue):
        context, model_binding = self.provider.task_identity()
        if self.investigation_config is not None:
            context = investigation_context(context, self.investigation_config)
        policy = asdict(self.policy)
        policy['timeout_seconds'] = float(policy['timeout_seconds'])
        scope = dict(repository_sha256=self.store.repository, base_sha=RepositoryGateway(self.source).head,
                     issue_sha256=fingerprint(issue), policy_sha256=fingerprint(policy),
                     provider_sha256=fingerprint(model_binding), source_sha256=fingerprint(self.sources),
                     tests_sha256=fingerprint(self.policy.allowed_tests), context_sha256=fingerprint(context))
        return scope, context

    def planning_checks(self):
        return self.policy.allowed_tests

    def reference(self, kind, identity, issue):
        link = self.link_registry.load('pi_task_link').get(kind + ':' + identity)
        if link is None:
            return None  # Historical runs have no native history to import.
        require(isinstance(link, dict) and set(link) == {'task_id'}, 'Invalid original Pi task link')
        self.restore_controller_read_scope(link['task_id'])
        scope, _context = self.current_scope(issue)
        record = self.store._load(link['task_id'], scope)
        require(record['revision'] > 0, 'Pi task has no completed checkpoint to continue')
        if self.experience:
            self.experience.verify_task(record['task_id'], issue)
        if self.knowledge:
            self.knowledge.verify_task(record['task_id'], issue)
        if self.maintenance:
            self.maintenance.verify_task(record['task_id'], issue)
        with self.store.lease(record['task_id'], scope, record['revision']) as lease:
            lease.descriptor()  # Strict full-file verification, no prompt/model/reservation.
        return {'task_id': record['task_id'], 'revision': record['revision']}

    def begin(self, issue, reference=None, feedback=None, feedback_guard=None):
        self.restore_controller_read_scope(reference['task_id'] if reference is not None else None)
        scope, context = self.current_scope(issue)
        if reference is None:
            record = self.store.allocate(scope, context)
        else:
            require(isinstance(reference, dict) and set(reference) == {'task_id', 'revision'}, 'Invalid Pi continuation reference')
            record = self.store._load(reference['task_id'], scope)
            require(record['revision'] == reference['revision'] and record['revision'] > 0,
                    'Pi task checkpoint changed after continuation review')
        self._ensure_investigation(record['task_id'], scope)
        self.identity, self.scope = record['task_id'], scope
        if self.experience:
            self.experience.begin(self.identity, issue, reference is not None)
        if self.knowledge:
            self.knowledge.begin(self.identity, issue, reference is not None)
        if self.compaction:
            self.compaction.begin(record,reference is not None)
        if self.maintenance:
            self.maintenance.begin(self.identity,issue,reference is not None)
        require(feedback is None or (reference is not None and callable(feedback_guard) and
                feedback.get('task_id') == self.identity), 'Failure feedback needs a verified same-task continuation')
        self.feedback, self.feedback_guard = feedback, feedback_guard
        return self.identity

    def link(self, kind, identity, task_id=None, db=None):
        task_id = task_id or self.identity
        require(kind in {'plan', 'run'} and task_id is not None, 'Missing controller Pi task identity')
        self.store.directory(task_id)
        self.link_registry.put('pi_task_link', kind + ':' + identity, {'task_id': task_id}, db)

    @contextmanager
    def turn(self, repository, issue, tests, feedback=None):
        if self.maintenance:self.maintenance.verify_task(self.identity,issue)
        if self.experience:
            self.experience.verify_task(self.identity, issue)
        if self.knowledge:
            self.knowledge.verify_task(self.identity, issue)
        require(self.identity is not None, 'Pi planning needs an explicit controller task')
        context_policy = self.compaction.before_turn(self.identity,issue,repository,feedback) if self.compaction else None
        if self.feedback_guard:
            self.feedback_guard(repository)
        if self.sources_guard:
            self.sources_guard(issue)
        scope, context = self.current_scope(issue)
        require(scope == self.scope and tuple(tests) == self.planning_checks() and
                unlinked(repository.common_dir) == unlinked(RepositoryGateway(self.source).common_dir) and
                repository.head == scope['base_sha'], 'Pi task source, policy, provider or tests changed')
        self._verify_investigation(self.identity, scope)
        record = self.store._load(self.identity, scope)
        with self.store.lease(self.identity, scope, record['revision']) as lease:
            if record['revision']:
                binding = lease.descriptor()
                mode = 'open'
            else:
                cwd, sessions = lease.directory / 'workspace', lease.directory / 'sessions'
                # Never reinitialize a previous failed first turn or an unknown outcome.
                require(not sessions.exists() or not any(sessions.iterdir()),
                        'Uncommitted initial Pi session requires inspection; start a separate task')
                unlinked(cwd).mkdir(exist_ok=True)
                unlinked(sessions).mkdir(exist_ok=True)
                binding = dict(schema_version=1, task_id=self.identity, revision=0,
                               cwd=str(unlinked(cwd)), sessions_dir=str(unlinked(sessions)))
                mode = 'create'
            self.provider._task_session_request = {'mode': mode, 'binding': binding, 'context': context}
            self.provider._context_policy = context_policy
            self.provider._continuation_feedback = self.feedback
            self.provider._task_checkpoint = None
            bridge = None
            if self.investigation is not None:
                def guard():
                    cancel = getattr(self.provider, '_cancel_event', None)
                    if cancel is not None and cancel.is_set():
                        raise SafetyError('Original investigation stopped by operator')
                self.investigation.guard = guard
                bridge = InvestigationBridge(
                    self.investigation, scope_sha256=self.investigation_scope_sha256,
                    base_sha=scope['base_sha'],
                    read_roots=self.investigation_config['read_roots'])
                self.provider._investigation = {
                    'bridge': bridge, 'scope_sha256': self.investigation_scope_sha256,
                    'base_sha': scope['base_sha'], 'read_roots': list(self.investigation_config['read_roots']),
                    'max_calls': self.investigation_config['max_calls'],
                    'max_output_bytes': self.investigation_config['max_output_bytes']}
            else:
                self.provider._investigation = None
            turn = PlanningTurn(self, lease, record, repository, issue)
            try:
                yield turn
            finally:
                if bridge is not None:
                    bridge.close()
                self.provider._investigation = None
                if self.investigation is not None:
                    self.investigation.guard = lambda: None
                self.provider._task_session_request = None
                self.provider._task_checkpoint = None
                self.provider._continuation_feedback = None
                self.provider._context_policy = None


class PlanningTurn:
    def __init__(self, planner, lease, record, repository, issue):
        self.planner, self.lease, self.record = planner, lease, record
        self.repository, self.issue = repository, issue
        self.experience_pin = (fingerprint(self.planner.experience.verify_task(record['task_id'], issue))
                               if self.planner.experience else None)
        self.knowledge_pin = (fingerprint(self.planner.knowledge.verify_task(record['task_id'], issue))
                              if self.planner.knowledge else None)
        self.context_policy_pin = fingerprint(self.planner.compaction.policy(record)) if self.planner.compaction else None
        self.maintenance_pin = fingerprint(self.planner.maintenance.verify_task(record['task_id'],issue)) if self.planner.maintenance else None
        self.original_snapshot = RepositoryGateway(planner.source).snapshot()
        self.candidate_snapshot = repository.snapshot()

    def validate_sources(self):
        if self.planner.maintenance:
            require(fingerprint(self.planner.maintenance.verify_task(self.record['task_id'],self.issue))==self.maintenance_pin,
                    'Maintenance selection changed during inference')
        if self.planner.compaction:
            require(fingerprint(self.planner.compaction.policy(self.record))==self.context_policy_pin,
                    'Task context policy changed during inference')
        if self.planner.knowledge:
            current = self.planner.knowledge.verify_task(self.record['task_id'], self.issue)
            require(fingerprint(current) == self.knowledge_pin, 'Knowledge selection changed during inference')
        if self.planner.experience:
            current = self.planner.experience.verify_task(self.record['task_id'], self.issue)
            require(fingerprint(current) == self.experience_pin, 'Experience selection changed during inference')
        if self.planner.feedback_guard:
            self.planner.feedback_guard(self.repository)
        if self.planner.sources_guard:
            self.planner.sources_guard(self.issue)
        scope, _context = self.planner.current_scope(self.issue)
        require(scope == self.record['scope'] and
                RepositoryGateway(self.planner.source).snapshot() == self.original_snapshot and
                self.repository.snapshot() == self.candidate_snapshot,
                'Pi task current source, candidate, policy or provider changed during inference')
        self.planner._verify_investigation(self.record['task_id'], self.record['scope'])

    def finish(self, attempt_id):
        self.validate_sources()  # Also reject a change after settlement without changing that settlement.
        receipt = self.planner.provider._task_checkpoint
        require(isinstance(receipt, dict) and set(receipt) == {
            'task_id', 'revision', 'file', 'leaf_id', 'session_id', 'snapshot_sha256'},
            'Missing production Pi checkpoint receipt')
        require(receipt['task_id'] == self.record['task_id'] and type(receipt['revision']) is int and
                receipt['revision'] == self.record['revision'], 'Pi checkpoint task/revision mismatch')
        _file, actual = self.lease._read(receipt['file'], receipt['leaf_id'], receipt['session_id'])
        require(actual['session_id'] == receipt['session_id'], 'Pi checkpoint session identity mismatch')
        updated = self.lease.commit(receipt['file'], receipt['leaf_id'], receipt['snapshot_sha256'], attempt_id)
        return {'task_id': updated['task_id'], 'revision': updated['revision'],
                'session_id': updated['checkpoint']['session_id'], 'rounds': updated['checkpoint']['rounds'],
                'compaction_count':len(actual.get('compactions',[]))}
