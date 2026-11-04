"""Reuse journal-local memory on controller-owned tasks without moving Native or budget.

Only this read-through view joins original run links with the original accounting
journal. It cannot reserve requests, change execution records, or open a new DB.
"""
from dataclasses import asdict
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from .dashboard import read_json, unlinked
from .experience_memory import ExperienceMemory
from .git import RepositoryGateway
from .models import Policy, SafetyError
from .store import redact
from .task_session import fingerprint, repository_identity, require, TASK_ID

LOCAL = {'run', 'plan', 'pi_task_link'}
PINS = {'experience_task', 'knowledge_task', 'maintenance_task', 'pi_context_policy',
        'pi_compaction_intent', 'pi_compaction_receipt', 'pi_task'}
WRITABLE = {'repository_experience', 'experience_settings', 'experience_review', 'experience_capture',
    'experience_task', 'repository_knowledge', 'knowledge_task', 'knowledge_review', 'maintenance_settings',
    'maintenance_note', 'maintenance_note_version', 'maintenance_task', 'context_settings',
    'pi_context_policy', 'pi_compaction_intent', 'retrieval_task'}


def valid_task(identity):
    return isinstance(identity, str) and TASK_ID.fullmatch(identity) is not None


def policy(value):
    result = Policy(tuple(value['allowed_paths']), tuple(tuple(c) for c in value['allowed_tests']),
                    value['timeout_seconds'], tuple(value.get('readonly_paths', ())))
    result.validate_scope()
    return result


def run_base(record):
    if 'base_sha' in record: return record['base_sha']
    require(record.get('status') == 'inspecting' and not record.get('finished_at')
            and not record.get('rounds') and 'plan' not in record,
            'Only an unfinished pre-plan run can use its original source baseline')
    return record['source_before']['head']


class JournalView:
    def __init__(self, original, links, planner, focus_run=None):
        self.original, self.links, self.planner = original, links, planner
        self.path, self.root = original.path, original.root
        self.focus_run = focus_run
        self.reference_task = None  # Set only after the original store verifies a continuation scope.

    def link_rows(self, db=None):
        if db is not None and self.links.path == self.original.path:
            rows = {k:json.loads(v) for k,v in db.execute("SELECT id,body FROM objects WHERE kind='pi_task_link'")}
        else: rows = self.links.load('pi_task_link')
        # A read-through view must not turn damaged metadata into task ownership.
        # Keep the original rows untouched; the recovery projection reports them.
        rows = {k:v for k,v in rows.items() if isinstance(v, dict)
                and set(v) == {'task_id'} and valid_task(v['task_id'])}
        if self.focus_run:
            task = rows.get('run:'+self.focus_run, {}).get('task_id') or self.planner.identity or self.reference_task
            rows = {k:v for k,v in rows.items() if k == 'run:'+self.focus_run or task and v['task_id'] == task}
        return rows

    def owned_tasks(self, db=None):
        tasks = {v['task_id'] for v in self.link_rows(db).values()}
        if valid_task(self.planner.identity): tasks.add(self.planner.identity)
        return tasks

    def load(self, kind):
        if kind == 'pi_task_link': return self.link_rows()
        rows = (self.links if kind in LOCAL else self.original).load(kind)
        if self.focus_run and kind in {'run', 'plan'}:
            rows = {k:v for k,v in rows.items() if kind+':'+k in self.link_rows() or k == self.focus_run}
        if kind in PINS:
            tasks = self.owned_tasks()
            rows = {k:v for k,v in rows.items() if isinstance(v, dict)
                    and valid_task(v.get('task_id', k)) and v.get('task_id', k) in tasks}
        return rows

    def put(self, kind, identity, value, db=None):
        require(kind in WRITABLE, 'Memory cannot write execution, Native links, or accounting records')
        if kind in PINS:
            task = value.get('task_id', identity) if isinstance(value, dict) else None
            require(valid_task(task) and task in self.owned_tasks(db), 'Memory task belongs to another controller')
        self.original.put(kind, identity, value, db)

    def transaction(self): return self.original.transaction()
    def budget(self): return self.original.budget()


class ReadOnlyJournal:
    def __init__(self, root): self.root = unlinked(root)

    def read(self, statement, parameters=()):
        from .monitor_history import journal
        try:
            with journal(self.root) as db: return db.execute(statement, parameters).fetchall()
        except sqlite3.Error:
            raise SafetyError('Original memory journal is unreadable; preserve it without reconstruction') from None

    def load(self, kind):
        rows = self.read('SELECT id,body FROM objects WHERE kind=? ORDER BY rowid LIMIT 101', (kind,))
        require(len(rows) <= 100, 'Memory history journal exceeds bounded lookup')
        return {k:json.loads(v) for k,v in rows}

    def metadata(self):
        rows = self.read('SELECT body FROM metadata')
        require(len(rows) == 1, 'Original memory source metadata missing')
        return json.loads(rows[0][0])


class RunOwner:
    def __init__(self, root, source, rules, registry, base=None, readonly=None):
        self.runs_dir, self.repo = unlinked(root), unlinked(Path(source))
        self.policy, self.registry = policy(rules), registry
        self.base = base
        self.provider = SimpleNamespace(inner=SimpleNamespace(
            readonly_paths=self.policy.readonly_paths if readonly is None else readonly))

    def _run_dir(self, identity):
        require(TASK_ID.fullmatch(identity) and identity in self.registry.load('run'), 'Unregistered memory run')
        directory = unlinked(self.runs_dir/identity)
        record, _ = read_json(directory/'run.json')
        require(record['run_id'] == identity and record['source_repo'] == str(self.repo)
                and record['workspace'] == str(directory/'workspace') and record['branch'] == 'maintenance/'+identity
                and policy(record['policy']) == self.policy, 'Memory run repository, path or policy differs')
        require(self.base is None or run_base(record) == self.base, 'Memory run baseline differs from original owner')
        return directory

    def experience_commands(self, record, commands):
        """Compare actual argv, never rewrite the persisted run or guess executables."""
        import sys
        expected = [[sys.executable if a == '{python}' else a for a in cmd] for cmd in commands]
        if not record.get('environment'): return expected
        from .target_environment import validate_grant
        require(record['environment'].get('receipt') == 'environment/environment.json', 'Unexpected environment receipt path')
        receipt, _ = read_json(self._run_dir(record['run_id'])/'environment/environment.json')
        env = record['environment']; binding = env['binding']; plan, grant = receipt['plan'], receipt['grant']
        validate_grant(plan, grant)
        require(env['status'] == receipt['status'] == 'prepared' and receipt['environment_binding'] == binding
                and fingerprint(binding) == env['environment_sha256'] == receipt['environment_sha256']
                and binding['base_sha'] == plan['head_sha'] == record['base_sha']
                and binding['grant_sha256'] == grant['grant_sha256']
                and binding['checks_sha256'] == fingerprint(plan['checks']), 'Original environment evidence differs')
        result = []
        for command in commands:
            require(list(command) in [c['argv'] for c in plan['checks']], 'Historical check is outside original environment')
            argv = [binding['runtime']['executable'] if a == '{python}' else a for a in command]
            if argv[0] == 'node': argv[0] = grant['node']
            elif argv[0] in {'npm', 'npm.cmd'}: argv = [grant['node'], grant['npm_cli'], *argv[1:]]
            result.append(argv)
        return result


def local_owner(registry, run_root, source, identity):
    entry = registry.load('run').get(identity)
    require(entry, 'Run has no original registration')
    record, _ = read_json(unlinked(run_root/identity/'run.json'))
    readonly = None
    if entry.get('issue_execution_id'):
        row = registry.load('issue_execution').get(entry['issue_execution_id'])
        require(row and row['run_id'] == identity and row['source_before'] == record['source_before']
                and row['source_before']['head'] == run_base(record)
                and record.get('issue_execution', {}).get('execution_id') == row['id']
                and record['issue_execution']['policy_binding_sha256'] == row['policy_binding']['binding_sha256']
                and row['policy_binding']['binding_sha256'] == fingerprint({k:v for k,v in row['policy_binding'].items() if k!='binding_sha256'}),
                'Historical Issue execution binding differs')
        require(record['issue_execution']['task_id'] == row.get('task_id')
                and registry.load('pi_task_link').get('run:'+identity) == {'task_id':row['task_id']},
                'Historical Issue Native binding differs')
        rules = row['policy_binding']['policy']
        base = row['source_before']['head']
    else:
        with registry.transaction() as db: metadata = json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
        require(metadata.get('repository') == str(source), 'Original memory repository differs')
        rules = metadata['policy']
        base = None
        # Legacy manual providers may have explicitly read-only test files that
        # are absent from Policy.readonly_paths. Recover the original Native
        # scope, never infer it from today's provider or filesystem.
        from .task_session import TaskSessionStore
        link = registry.load('pi_task_link').get('run:'+identity)
        require(isinstance(link, dict) and set(link) == {'task_id'}, 'Original memory Native binding missing')
        task = registry.load('pi_task').get(link['task_id'])
        require(isinstance(task, dict), 'Original memory Native task missing')
        repo = RepositoryGateway(source)
        task = TaskSessionStore(registry, source, repo.common_dir)._load(link['task_id'], task['scope'])
        original_policy = asdict(policy(rules)); original_policy['timeout_seconds'] = float(original_policy['timeout_seconds'])
        paths, editable = task['context']['paths'], list(rules['allowed_paths'])
        require(task['scope']['policy_sha256'] == fingerprint(original_policy)
                and task['scope']['base_sha'] == run_base(record)
                and task['scope']['issue_sha256'] == fingerprint(record['issue'])
                and paths[:len(editable)] == editable, 'Original memory Native scope differs')
        readonly = tuple(paths[len(editable):])
        require(set(rules.get('readonly_paths', ())) <= set(readonly), 'Original read-only context differs')
    return RunOwner(run_root, source, rules, registry, base, readonly)


def monitor_owner(monitor, identity, run_id):
    registry = monitor.registries[identity]
    events = registry.load('event')
    require(len(events) <= 100, 'Monitor memory history exceeds bounded lookup')
    for event_id, event in events.items():
        if not event.get('root_execution'): continue
        require(event_id == event['id'] and len(event_id) == 16 and all(c in '0123456789abcdef' for c in event_id),
                'Memory monitor event identity differs')
        root = unlinked(monitor.root/identity/'r'/event_id)
        if not (root/'ui-state/registry.sqlite3').is_file(): continue
        journal = ReadOnlyJournal(root)
        if run_id not in journal.load('run'): continue
        metadata = journal.metadata(); binding = event['root_execution']['binding']
        source = unlinked(monitor.root/identity/'events'/event_id/'source')
        require(metadata['repository'] == str(source) and metadata['shared_budget'] == str(registry.path)
                and metadata['target_environment']['controller'] == binding
                and binding['event_id'] == event_id and binding['monitor_id'] == identity
                and binding['base_sha'] == event['sha']
                and fingerprint(asdict(policy(metadata['policy']))) == binding['policy_sha256'],
                'Historical monitor memory ownership differs')
        return RunOwner(root, source, metadata['policy'], journal, event['sha'])
    raise ValueError('No original monitor journal owns this memory run')


class ControllerExperience(ExperienceMemory):
    def __init__(self, host, repository): self.session, self.repository = host, repository

    def enabled(self):
        # Explicit opt-out remains authoritative; only newly begun controller
        # tasks default to reference lookup under the user's maintenance scope.
        if not self.session.registry.load('experience_settings') and self.session.controller_owned: return True
        return super().enabled()

    def original(self, identity, *, version=None):
        owner = self.session.owner(identity)
        original = ExperienceMemory.__new__(ExperienceMemory)
        original.session, original.repository = owner, self.repository
        return original.original(identity, version=version)

    def validate(self, identity, entry, *, applicable=False):
        owner = self.session.owner(identity)
        original = ExperienceMemory.__new__(ExperienceMemory)
        original.session, original.repository = owner, self.repository
        payload = original.validate(identity, entry)
        if applicable:
            require(payload['policy_sha256'] == self.policy() and set(payload['source_files']) == set(self.files()),
                    'Historical memory policy or current authorized paths differ')
        return payload

    def verify_task(self, identity, issue):
        require_pin(self.session, 'experience_task', identity, 'experience_context_sha256')
        return super().verify_task(identity, issue)


def require_pin(host, kind, identity, receipt):
    if host.registry.load(kind).get(identity) is None:
        require(not any(a.get('evidence', {}).get('task_id') == identity and a.get('evidence', {}).get(receipt)
                        for a in host.budget_registry.load('attempt').values()), 'Original controller memory selection is missing')


class MemoryHost:
    def __init__(self, source, rules, durable, planner, original, links, run_root, guard, owner,
                 *, state=None, bindings=None, controller_owned=True, focus_run=None):
        import secrets
        self.repo, self.policy, self.provider, self.task_planner = Path(source), rules, durable, planner
        self.registry, self.budget_registry = JournalView(original, links, planner, focus_run), original
        self.runs_dir, self.guard, self.owner = Path(run_root), guard, owner
        self.state, self.controller_owned = state, controller_owned
        self.token = state.token if state else secrets.token_hex(32)
        bindings = bindings or {}
        self.memory = state.memory if state else SimpleNamespace(binding=bindings.get('memory'))
        self.structure = state.structure if state else SimpleNamespace(binding=bindings.get('structure'))
        self.retrieval = None

    @property
    def active(self): return self.state.active if self.state else None
    @property
    def control(self): return self.state.control if self.state else None
    @property
    def runs(self): return self.registry.load('run')
    @property
    def plans(self):
        return {i:{**v, 'path':unlinked(self.runs_dir/'ui-sessions'/v['session_id']/(i+'.json'))}
                for i,v in self.registry.load('plan').items()}
    def _run_dir(self, identity): return self.owner(identity)._run_dir(identity)
    def _task_sources(self, issue): self.guard(issue)

    def attach(self, repository):
        from .knowledge_memory import KnowledgeMemory
        from .maintenance_memory import MaintenanceMemory
        from .task_compaction import TaskCompaction
        from .memory_catalog import MemoryCatalog
        self.experience = ControllerExperience(self, repository)
        class ControllerKnowledge(KnowledgeMemory):
            def verify_task(memory, identity, issue):
                require_pin(memory.session, 'knowledge_task', identity, 'knowledge_context_sha256')
                return super().verify_task(identity, issue)
        self.knowledge, self.maintenance = ControllerKnowledge(self), MaintenanceMemory(self)
        self.compaction = TaskCompaction(self)
        for name in ('experience', 'knowledge', 'maintenance', 'compaction'):
            setattr(self.task_planner, name, getattr(self, name))
        inner = self.provider.inner
        inner._experience_context, inner._knowledge_context = self.experience.context, self.knowledge.context
        inner._maintenance_context = self.maintenance.context
        self.catalog = MemoryCatalog(self)
        if self.controller_owned and self.budget_registry.path != self.registry.links.path:
            self.catalog.task_type = 'commit_inspection'
        inner._memory_catalog = self.catalog
        return self

    def capture(self, identity):
        try: return self.experience.capture(identity)
        except (ValueError, TypeError, KeyError, OSError) as error:
            self.registry.put('experience_capture', identity, {'status':'blocked', 'reason':redact(str(error))})


def attach_ui(session, *, monitor=None, identity=None):
    require(session.task_planner is not None, 'Controller memory needs the original task planner')
    if monitor is None:
        owner = lambda run: local_owner(session.registry, session.runs_dir, session.repo, run)
        repo = RepositoryGateway(session.repo)
        repository = repository_identity(repo.root, repo.common_dir)
    else:
        owner = lambda run: monitor_owner(monitor, identity, run)
        repository = fingerprint({'monitor_journal':str(session.budget_registry.path)})
    host = MemoryHost(session.repo, session.policy, session.provider, session.task_planner, session.budget_registry,
        session.registry, session.runs_dir, session._task_sources, owner, state=session,
        controller_owned=monitor is not None).attach(repository)
    host.retrieval = getattr(session, 'retrieval', None)
    session.controller_memory = host
    for name in ('experience', 'knowledge', 'maintenance', 'compaction', 'catalog'):
        setattr(session, name, getattr(host, name))
    return host


def attach_issue(service, row, rules, durable, planner, guard, bindings=None):
    source = service.source
    host = MemoryHost(source.root, rules, durable, planner, service.registry, service.registry,
        service.runs, lambda _:guard(), lambda run:local_owner(service.registry, service.runs, source.root, run),
        bindings=bindings,focus_run=row['run_id']).attach(repository_identity(source.root, source.common_dir))
    service.memory = host
    return host
