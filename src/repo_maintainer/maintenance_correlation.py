"""Durable correlation facts and dispatch exclusion on an existing UI journal.

This layer does not grant execution, share budgets, or move Pi/Native tasks.
Callers must retain their original freshness/authorization guards before effects.
References should name immutable source/intent facts in the original journals.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from .issue_intake import fingerprint
from .models import SafetyError
from .store import now

OBSERVATIONS = 'maintenance_correlation_observation'
LINKS = 'maintenance_correlation_link'
CLAIMS = 'maintenance_correlation_claim'
APPROVAL_KIND = 'maintenance_correlation_link_approval'
SOURCE_KIND = 'maintenance_correlation_source'
DISPATCH_KIND = 'maintenance_correlation_dispatch_intent'
MAX_FACTS = 200
_HASH = re.compile(r'[a-f0-9]{64}')
_SHA = re.compile(r'[a-f0-9]{40}')
_KIND = re.compile(r'[a-z][a-z0-9_]{0,63}')
_REFERENCE = {'ledger_id', 'kind', 'object_id', 'object_sha256'}
_SOURCE = {'source_type', 'source_id', 'source_version_sha256', 'reference', 'repository_id', 'repository', 'branch',
           'head_sha', 'checks_sha256', 'environment_sha256', 'fault_sha256',
           'authority_sha256'}
_COMPARISON = ('repository_id', 'repository', 'branch', 'head_sha',
               'checks_sha256', 'environment_sha256', 'fault_sha256', 'authority_sha256')
_OUTCOMES = {'started', 'candidate_ready', 'finished', 'failed', 'unknown', 'interrupted'}
_PROGRESSION = {
    'claimed': _OUTCOMES,
    'started': _OUTCOMES - {'started'},
    'candidate_ready': {'finished', 'failed', 'unknown', 'interrupted'},
    'finished': set(), 'failed': set(), 'unknown': set(), 'interrupted': set(),
}
_INTENT = {'schema_version', 'coordinator_id', 'observation_id', 'source_reference',
           'authority_reference', 'authority_sha256'}


def _require(value, message):
    if not value:
        raise SafetyError(message)


def _text(value, maximum=128):
    return isinstance(value, str) and 0 < len(value) <= maximum and not any(ord(c) < 32 for c in value)


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _body(db, kind, identity):
    row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (kind, identity)).fetchone()
    _require(row and len(row[0].encode('utf-8')) <= 262144, 'Original correlation reference missing or oversized')
    try:
        value = json.loads(row[0])
    except (ValueError, TypeError) as error:
        raise SafetyError('Original correlation fact is not valid JSON') from error
    _require(isinstance(value, dict), 'Original correlation fact must be an object')
    return value


def _metadata(db):
    rows = db.execute('SELECT body FROM metadata').fetchall()
    _require(len(rows) == 1, 'Original correlation journal metadata unavailable')
    try:
        value = json.loads(rows[0][0])
    except (ValueError, TypeError) as error:
        raise SafetyError('Original correlation metadata is not valid JSON') from error
    _require(isinstance(value, dict), 'Invalid original correlation journal metadata')
    return value


def registry_identity(registry):
    """Identify an already-open original journal; never create another journal."""
    with registry.transaction() as db:
        metadata = _metadata(db)
    return fingerprint({'path': str(registry.path.resolve()), 'metadata': metadata})


def source_reference(registry, kind, object_id):
    """Pin an existing original fact. A reference itself never grants authority."""
    _require(isinstance(kind, str) and _KIND.fullmatch(kind) and _text(object_id), 'Invalid source fact identity')
    with registry.transaction() as db:
        metadata = _metadata(db)
        value = _body(db, kind, object_id)
    return {'ledger_id': fingerprint({'path': str(registry.path.resolve()), 'metadata': metadata}),
            'kind': kind, 'object_id': object_id, 'object_sha256': fingerprint(value)}


class MaintenanceCorrelation:
    """Coordinate only explicitly linked work on one existing journal.

    Every original source journal must be explicitly supplied by the controller.
    Comparability includes the exact target, failure/check/environment identity,
    and compiled authority scope. Similar text never creates an alias.
    """
    def __init__(self, coordinator_registry, registries=(), *, owner_validator=None):
        self.registry = coordinator_registry
        _require(owner_validator is None or callable(owner_validator), 'Invalid controller correlation verifier')
        self.owner_validator = owner_validator
        self.registries = {}
        self.paths = {}
        for registry in (coordinator_registry, *tuple(registries)):
            identity = registry_identity(registry)
            path = str(registry.path.resolve())
            _require(path not in self.paths or self.paths[path] == identity, 'Correlation journal identity changed')
            _require(identity not in self.registries or self.registries[identity] is registry,
                     'Duplicate original correlation journal handle')
            self.registries[identity] = registry
            self.paths[path] = identity
        self.coordinator_id = self.paths[str(self.registry.path.resolve())]

    def _reference(self, reference, db=None):
        _require(isinstance(reference, dict) and set(reference) == _REFERENCE
                 and _hash(reference['ledger_id']) and _hash(reference['object_sha256'])
                 and isinstance(reference['kind'], str) and _KIND.fullmatch(reference['kind'])
                 and _text(reference['object_id']), 'Invalid original correlation reference schema')
        original = self.registries.get(reference['ledger_id'])
        _require(original is not None, 'Correlation reference is not an explicitly registered original journal')

        def read(connection):
            _require(fingerprint({'path': str(original.path.resolve()), 'metadata': _metadata(connection)})
                     == reference['ledger_id'], 'Original correlation journal binding changed')
            value = _body(connection, reference['kind'], reference['object_id'])
            _require(fingerprint(value) == reference['object_sha256'], 'Original correlation reference changed; refresh source')
            return value

        if original is self.registry and db is not None:
            return read(db)
        with original.transaction() as connection:
            return read(connection)

    def _source(self, source):
        _require(isinstance(source, dict) and set(source) == _SOURCE, 'Invalid correlation observation schema')
        _require(isinstance(source['source_type'], str) and source['source_type'] in {'commit', 'ci', 'issue', 'review'}
                 and _text(source['source_id'], 128) and _hash(source['source_version_sha256'])
                 and type(source['repository_id']) is int and 0 < source['repository_id'] <= 2**53-1
                 and _text(source['repository'], 201) and source['repository'] == source['repository'].casefold()
                 and re.fullmatch(r'[a-z0-9_.-]+/[a-z0-9_.-]+', source['repository'])
                 and _text(source['branch'], 255) and isinstance(source['head_sha'], str)
                 and _SHA.fullmatch(source['head_sha'])
                 and all(_hash(source[k]) for k in ('checks_sha256', 'environment_sha256',
                                                   'fault_sha256', 'authority_sha256')),
                 'Invalid exact correlation source identity')
        return deepcopy(source)

    def _observation(self, db, identity, *, current=False):
        _require(_hash(identity), 'Invalid correlation observation identity')
        row = _body(db, OBSERVATIONS, identity)
        _require(set(row) == {'id', 'source', 'created_at'} and row['id'] == identity
                 and _text(row['created_at'], 64), 'Correlation observation schema changed')
        source = self._source(row['source'])
        _require(fingerprint(source) == identity, 'Correlation observation hash changed')
        if current:
            self._source_fact(source, db)
        return row

    def _source_fact(self, source, db=None):
        fact = self._reference(source['reference'], db)
        _require(source['reference']['kind'] == SOURCE_KIND
                 and set(fact) == {'schema_version', 'source'}
                 and type(fact['schema_version']) is int and fact['schema_version'] == 1
                 and isinstance(fact['source'], dict)
                 and set(fact['source']) == _SOURCE - {'reference'},
                 'Original source fact does not bind the exact correlation scope')
        self._source({**fact['source'], 'reference': source['reference']})
        _require(fingerprint(fact['source']) == fingerprint({k: v for k, v in source.items() if k != 'reference'}),
                 'Original source fact does not bind the typed canonical correlation scope')

    def _owner(self, db, observation_id, reference):
        """Read an immutable controller intent, never synthesize execution authority."""
        source = self._observation(db, observation_id, current=True)['source']
        intent = self._reference(reference, db)
        _require(reference['kind'] == DISPATCH_KIND
                 and reference['ledger_id'] == source['reference']['ledger_id']
                 and set(intent) == _INTENT and type(intent['schema_version']) is int
                 and intent['schema_version'] == 1 and _hash(intent['coordinator_id'])
                 and _hash(intent['observation_id']) and _hash(intent['authority_sha256'])
                 and intent['coordinator_id'] == self.coordinator_id
                 and intent['observation_id'] == observation_id
                 and fingerprint(intent['source_reference']) == fingerprint(source['reference'])
                 and intent['authority_sha256'] == source['authority_sha256'],
                 'Original dispatch intent does not bind this coordinator, observation, source and authority')
        authority = self._reference(intent['authority_reference'], db)
        _require(intent['authority_reference']['ledger_id'] == source['reference']['ledger_id'],
                 'Dispatch authority must remain in the original source journal')
        return source, intent, authority

    def observe_source(self, original_registry, source):
        """Record immutable controller-derived source facts in their OWN journal.

        ``source`` contains the observe schema except ``reference``. This helper
        does not infer source freshness or permissions from Issue/CI content.
        """
        _require(isinstance(source, dict) and set(source) == _SOURCE - {'reference'},
                 'Invalid original correlation source schema')
        ledger_id = self.paths.get(str(original_registry.path.resolve()))
        _require(ledger_id is not None and self.registries[ledger_id] is original_registry,
                 'Original source journal is not registered')
        self._source({**source, 'reference': {}})
        identity = fingerprint(source)
        reference = {'ledger_id': ledger_id, 'kind': SOURCE_KIND,
                     'object_id': identity, 'object_sha256': fingerprint({'schema_version': 1, 'source': source})}
        self._source({**source, 'reference': reference})
        fact = {'schema_version': 1, 'source': deepcopy(source)}
        with original_registry.transaction() as db:
            _require(fingerprint({'path': str(original_registry.path.resolve()), 'metadata': _metadata(db)})
                     == ledger_id, 'Original source journal binding changed')
            existing = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (SOURCE_KIND, identity)).fetchone()
            _require(not existing or json.loads(existing[0]) == fact, 'Original immutable source fact changed')
            if not existing:
                _require(db.execute('SELECT count(*) FROM objects WHERE kind=?', (SOURCE_KIND,)).fetchone()[0]
                         < MAX_FACTS, 'Original source fact capacity reached')
                original_registry.put(SOURCE_KIND, identity, fact, db)
        return self.observe({**source, 'reference': reference})

    def _rows(self, db, kind):
        rows = db.execute('SELECT id,body FROM objects WHERE kind=?', (kind,)).fetchall()
        _require(len(rows) <= MAX_FACTS, 'Original correlation history at capacity; preserve it')
        return rows

    @staticmethod
    def _comparison(source):
        return {key: source[key] for key in _COMPARISON}

    def observe(self, source):
        source = self._source(source)
        self._source_fact(source)
        identity = fingerprint(source)
        with self.registry.transaction() as db:
            rows = self._rows(db, OBSERVATIONS)
            existing = next((body for key, body in rows if key == identity), None)
            if existing is None:
                _require(len(rows) < MAX_FACTS, 'Original correlation observation capacity reached')
                self.registry.put(OBSERVATIONS, identity, {'id': identity, 'source': source, 'created_at': now()}, db)
            self._observation(db, identity, current=True)
            related = []
            for key, _ in rows:
                other = self._observation(db, key)
                if key != identity and self._comparison(other['source']) == self._comparison(source):
                    related.append(key)
        return {'status': 'observed', 'observation_id': identity, 'related': sorted(related),
                'related_is_authority': False}

    def comparison_fingerprint(self, observation_id):
        """Scope digest for an authenticated, separately confirmed link proposal."""
        with self.registry.transaction() as db:
            source = self._observation(db, observation_id, current=True)['source']
        return fingerprint(self._comparison(source))

    def _link(self, db, identity):
        row = _body(db, LINKS, identity)
        _require(set(row) == {'id', 'core', 'created_at'} and row['id'] == identity
                 and _text(row['created_at'], 64) and isinstance(row['core'], dict)
                 and set(row['core']) == {'observations', 'comparison_sha256', 'proof_reference'}
                 and fingerprint(row['core']) == identity, 'Correlation link schema or hash changed')
        core = row['core']
        members = core['observations']
        _require(isinstance(members, list) and len(members) == 2 and all(_hash(v) for v in members)
                 and members == sorted(set(members)), 'Invalid correlation link members')
        sources = [self._observation(db, member)['source'] for member in members]
        comparison = self._comparison(sources[0])
        _require(comparison == self._comparison(sources[1])
                 and fingerprint(comparison) == core['comparison_sha256'], 'Linked correlation scope changed')
        self._proof(db, core['proof_reference'], members, core['comparison_sha256'])
        return row

    def _proof(self, db, reference, members, comparison_sha256):
        proof = self._reference(reference, db)
        _require(reference['kind'] == APPROVAL_KIND and reference['ledger_id'] == self.coordinator_id
                 and set(proof) == {'schema_version', 'approved', 'actor', 'observations', 'comparison_sha256'}
                 and type(proof['schema_version']) is int and proof['schema_version'] == 1
                 and proof['approved'] is True and proof['actor'] == 'local-operator'
                 and proof['observations'] == members and proof['comparison_sha256'] == comparison_sha256,
                 'A current explicit local correlation approval for these exact facts is required')

    def _component(self, db, identity):
        members = {identity}
        links = [self._link(db, key)['core']['observations'] for key, _ in self._rows(db, LINKS)]
        changed = True
        while changed:
            changed = False
            for pair in links:
                if members.intersection(pair) and not set(pair).issubset(members):
                    members.update(pair)
                    changed = True
        for member in members:
            self._observation(db, member, current=True)
        return members

    def _claim(self, db, identity, *, include_authority=False):
        row = _body(db, CLAIMS, identity)
        _require(set(row) == {'id', 'core', 'status', 'revision', 'created_at', 'history'}
                 and row['id'] == identity and isinstance(row['core'], dict)
                 and set(row['core']) == {'observation_id', 'owner_reference'}
                 and fingerprint(row['core']) == identity
                 and isinstance(row['status'], str) and row['status'] in {'claimed', *_OUTCOMES}
                 and type(row['revision']) is int and 0 <= row['revision'] <= 30
                 and isinstance(row['history'], list) and len(row['history']) == row['revision']
                 and _text(row['created_at'], 64), 'Correlation dispatch claim schema or hash changed')
        _, _, authority = self._owner(db, row['core']['observation_id'], row['core']['owner_reference'])
        previous = 'claimed'
        for item in row['history']:
            _require(isinstance(item, dict) and set(item) == {'status', 'at'}
                     and isinstance(item['status'], str) and item['status'] in _OUTCOMES
                     and _text(item['at'], 64), 'Correlation claim history changed')
            _require(item['status'] in _PROGRESSION[previous], 'Correlation claim history has invalid progression')
            previous = item['status']
        _require((row['history'][-1]['status'] if row['history'] else 'claimed') == row['status'],
                 'Correlation claim outcome does not match history')
        return (row, authority) if include_authority else row

    def _claims(self, db, members):
        result = []
        for identity, _ in self._rows(db, CLAIMS):
            claim = self._claim(db, identity)
            if claim['core']['observation_id'] in members:
                result.append(claim)
        return result

    @staticmethod
    def _conflict(error):
        return {'status': 'conflict', 'dispatch_allowed': False, 'reason': str(error)}

    def link(self, left_id, right_id, proof_reference):
        """Link exact comparable facts only after separately recorded approval.

        Existing separate dispatches cannot be merged retroactively. The caller
        must create approval through its authenticated maintainer action; remote
        Issue text or CI output must never be written as an approval object.
        """
        try:
            with self.registry.transaction() as db:
                left = self._observation(db, left_id, current=True)
                right = self._observation(db, right_id, current=True)
                _require(left_id != right_id, 'A source cannot be linked to itself')
                comparison = self._comparison(left['source'])
                _require(comparison == self._comparison(right['source']),
                         'Sources are not comparable; do not suppress either task')
                members = sorted([left_id, right_id])
                self._proof(db, proof_reference, members, fingerprint(comparison))
                group = self._component(db, left_id) | self._component(db, right_id)
                _require(len(self._claims(db, group)) <= 1, 'Both sources already own dispatches; preserve independent work')
                core = {'observations': members, 'comparison_sha256': fingerprint(comparison),
                        'proof_reference': deepcopy(proof_reference)}
                identity = fingerprint(core)
                rows = self._rows(db, LINKS)
                if not any(key == identity for key, _ in rows):
                    _require(len(rows) < MAX_FACTS, 'Original correlation link capacity reached')
                    self.registry.put(LINKS, identity, {'id': identity, 'core': core, 'created_at': now()}, db)
                self._link(db, identity)
            return {'status': 'related', 'link_id': identity, 'observations': members, 'dispatch_allowed': False}
        except SafetyError as error:
            return self._conflict(error)

    def claim(self, observation_id, owner_reference):
        """Persist exclusion BEFORE any external effect or model reservation.

        Repeated calls, including by the same owner, never authorize a replay.
        No claim is released when a process restarts or its outcome is unknown.
        The trusted controller verifier must only read/check original authority;
        it runs outside the coordinator write transaction and must not reserve
        a model request, dispatch work, or write an authorization. The original
        controller must revalidate external freshness immediately before effects.
        """
        try:
            _require(self.owner_validator is not None, 'Original controller authority verifier is required before dispatch')
            with self.registry.transaction() as db:
                prepared = self._owner(db, observation_id, owner_reference)
            # Existing controller guards can acquire their own original locks.
            # Never invoke them while holding the coordinator's write lock.
            try:
                authorized = self.owner_validator(*(deepcopy(value) for value in prepared))
            except Exception:
                raise SafetyError('Original controller dispatch authority could not be verified') from None
            _require(authorized is True, 'Original controller has not authorized this exact dispatch intent')
            with self.registry.transaction() as db:
                current = self._owner(db, observation_id, owner_reference)
                _require(fingerprint(current) == fingerprint(prepared),
                         'Original dispatch facts changed during authority verification')
                members = self._component(db, observation_id)
                claims = self._claims(db, members)
                _require(len(claims) <= 1, 'Multiple original owners require inspection')
                if claims:
                    return {'status': 'held_by', 'dispatch_allowed': False, 'claim_id': claims[0]['id'],
                            'held_by': deepcopy(claims[0]['core']), 'outcome': claims[0]['status']}
                _require(len(self._rows(db, CLAIMS)) < MAX_FACTS, 'Original correlation claim capacity reached')
                core = {'observation_id': observation_id, 'owner_reference': deepcopy(owner_reference)}
                identity = fingerprint(core)
                row = {'id': identity, 'core': core, 'status': 'claimed', 'revision': 0,
                       'created_at': now(), 'history': []}
                self.registry.put(CLAIMS, identity, row, db)
                self._claim(db, identity)
            return {'status': 'dispatch_allowed', 'dispatch_allowed': True, 'claim_id': identity,
                    'owner': core, 'revision': 0}
        except SafetyError as error:
            return self._conflict(error)

    def mark_outcome(self, claim_id, owner_reference, status, *, revision):
        """Append an owner outcome. All outcomes retain dispatch exclusion."""
        _require(isinstance(status, str) and status in _OUTCOMES and type(revision) is int and revision >= 0,
                 'Invalid correlation claim outcome')
        with self.registry.transaction() as db:
            row = self._claim(db, claim_id)
            _require(fingerprint(row['core']['owner_reference']) == fingerprint(owner_reference),
                     'Correlation owner changed')
            if row['status'] == status and row['revision'] in {revision, revision + 1}:
                return {'status': status, 'claim_id': claim_id, 'revision': row['revision'], 'dispatch_allowed': False}
            _require(row['revision'] == revision, 'Correlation outcome revision changed')
            _require(row['revision'] < 30, 'Original correlation claim history at capacity')
            _require(status in _PROGRESSION[row['status']],
                     'Correlation outcome cannot repeat work or leave a retained terminal state')
            row['history'].append({'status': status, 'at': now()})
            row.update(status=status, revision=revision + 1)
            self.registry.put(CLAIMS, claim_id, row, db)
        return {'status': status, 'claim_id': claim_id, 'revision': row['revision'], 'dispatch_allowed': False}
