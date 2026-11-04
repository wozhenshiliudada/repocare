"""Explicit recovery from an already metered and committed readonly Native round.

This module never invokes a provider, reserves/refunds requests, edits Native
history or approves a contract. Uncommitted and unknown outcomes stay blocked.
"""
import copy

from .pi_evidence import validate_evidence
from .store import now
from .task_session import fingerprint, strict_json, require
from .models import digest
from .validation_proposal import ValidationPlanner, proposal_contract, PURPOSE


def _selected_proposal(raw, leaf):
    entries=[strict_json(line.decode('utf-8')) for line in raw.splitlines()]
    by_id={entry['id']:entry for entry in entries[1:]}
    branch=[]
    while leaf is not None:
        branch.append(by_id[leaf]);leaf=by_id[leaf]['parentId']
    calls={};accepted=[]
    for entry in reversed(branch):
        message=entry.get('message',{})
        if message.get('role')=='assistant':
            for part in message['content']:
                if part.get('type')=='toolCall' and part.get('name')=='propose_validation':
                    calls[part['id']]=part['arguments']
        elif message.get('role')=='toolResult' and message.get('toolName')=='propose_validation' and not message['isError']:
            accepted.append((calls[message['toolCallId']],message['details']['proposal_sha256']))
    require(accepted,'Committed validation Native has no accepted proposal')
    return accepted[-1]


def reconcile(service, revision):
    service._fresh()
    registry=service.registry
    row=registry.load('validation_proposal').get(service.identity)
    require(isinstance(row,dict) and row.get('id')==service.identity and row.get('source')==service.source
            and row.get('status') in {'started','interrupted_no_replay'} and row.get('runtime_authorized') is False
            and revision==service.revision(row), 'Validation reconciliation source or review changed')
    require(not any(key in row for key in ('proposal','proposal_sha256','contract','review','reconciled')),
            'Validation result already exists; reconcile cannot replace or review it')
    planner=ValidationPlanner(registry,service.repository,service.provider,service.source,service._fresh)
    scope,context=planner.current_scope(service.source['issue'])
    require(context.get('purpose')==PURPOSE,'Validation reconciliation purpose changed')
    task_id=row.get('task_id')
    record=planner.store._load(task_id,scope)
    require(record['revision']>0 and record['context']==context and record['checkpoint'] is not None,
            'Validation Native is uncommitted; reconciliation cannot infer a result')
    with planner.store.lease(task_id,scope,record['revision']) as lease:
        descriptor=lease.descriptor()  # Strictly verifies all JSONL, including uncommitted tails.
        checkpoint=record['checkpoint']
        require(checkpoint['snapshot_sha256']==fingerprint(service.source['files']),
                'Validation checkpoint source snapshot changed')
        attempt_id=checkpoint['attempt_id']
        attempt=registry.load('attempt').get(attempt_id)
        require(isinstance(attempt,dict) and attempt.get('id')==attempt_id
                and attempt.get('status')=='finished' and attempt.get('outcome')=='completed',
                'Validation reconciliation requires its settled original successful attempt')
        evidence=attempt.get('evidence',{})
        require(evidence.get('attempt_id')==attempt_id and evidence.get('task_id')==task_id,
                'Validation original attempt belongs to another task')
        safe={k:v for k,v in evidence.items() if k not in {'attempt_id','task_id'}}
        validate_evidence(safe,attempt['reserved'])
        require(safe.get('status')=='completed' and safe.get('purpose')==PURPOSE and safe.get('requests',0)>0
                and safe.get('validation_context_sha256')==fingerprint(service.source)
                and safe.get('source_snapshot_sha256')==fingerprint(service.source['files']),
                'Validation settled evidence source or purpose changed')
        binding=registry.load('pi_checkpoint_attempt').get(attempt_id)
        require(binding=={'task_id':task_id,'revision':record['revision']},
                'Validation checkpoint is not bound to its original attempt')
        prior_evidence=row.get('evidence',{})
        require(all(key not in prior_evidence or prior_evidence[key]==value for key,value in
                    {'attempt_id':attempt_id,'task_id':task_id}.items()),
                'Validation failure record points to another original attempt')
        file=lease._file(checkpoint['file']);raw=lease._bytes(file)
        require(digest(raw)==descriptor['file_sha256'],'Validation Native changed during reconciliation')
        proposal,proposal_sha=_selected_proposal(raw,descriptor['leaf_id'])
        require(proposal_sha==safe.get('validation_proposal_sha256'),
                'Validation settled evidence differs from its accepted Native proposal')
        contract=proposal_contract(service.source,proposal)
        service._fresh()
        require(planner.current_scope(service.source['issue'])==(scope,context),
                'Validation scope changed during reconciliation')
        when=now()
        origin={'schema_version':1,'previous_status':row['status'],'previous_row_sha256':fingerprint(row),
                'attempt_id':attempt_id,'attempt_evidence_sha256':fingerprint(evidence),
                'checkpoint_sha256':fingerprint(checkpoint),'native_file_sha256':descriptor['file_sha256'],
                'reconciled_at':when}
        audit_id=fingerprint({'proposal_id':service.identity,**origin})
        origin['id']=audit_id
        updated=copy.deepcopy(row)
        updated.update(proposal=copy.deepcopy(proposal),proposal_sha256=proposal_sha,contract=contract,
                       status='clarification_needed' if contract is None else 'awaiting_validation_review',
                       evidence=copy.deepcopy(evidence),finished_at=when,reconciled=origin)
        with registry.transaction() as db:
            saved=db.execute("SELECT body FROM objects WHERE kind='validation_proposal' AND id=?",
                             (service.identity,)).fetchone()
            require(saved is not None and strict_json(saved[0])==row,
                    'Validation reconciliation record changed before commit')
            require(planner.store._load(task_id,scope,db=db)==record and digest(lease._bytes(file))==descriptor['file_sha256'],
                    'Validation original checkpoint changed before reconciliation commit')
            saved_attempt=db.execute("SELECT body FROM objects WHERE kind='attempt' AND id=?",(attempt_id,)).fetchone()
            saved_binding=db.execute("SELECT body FROM objects WHERE kind='pi_checkpoint_attempt' AND id=?",(attempt_id,)).fetchone()
            require(saved_attempt is not None and strict_json(saved_attempt[0])==attempt
                    and saved_binding is not None and strict_json(saved_binding[0])==binding,
                    'Validation original settled attempt changed before reconciliation commit')
            require(not db.execute("SELECT 1 FROM objects WHERE kind='validation_reconcile' AND id=?",(audit_id,)).fetchone(),
                    'Validation reconciliation already recorded')
            # Preserve the previous failure/start record exactly in a separate audit object.
            registry.put('validation_reconcile',audit_id,{'id':audit_id,'proposal_id':service.identity,
                         'previous_row':row,'reconciled':origin},db)
            registry.put('validation_proposal',service.identity,updated,db)
        return copy.deepcopy(updated)
