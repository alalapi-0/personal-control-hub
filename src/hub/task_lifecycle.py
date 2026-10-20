"""Read-only lifecycle projection of the existing durable Hub task queue.

This is not a scheduler or a provider status guess. Callers supply one task and
its integrity-checked TaskStore.events(task_id) snapshot. No executable content
or provider response body is returned.
"""
from __future__ import annotations

import re


_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_PROVIDER = {'inProgress', 'completed', 'failed', 'interrupted'}
_TERMINAL = {'failed', 'cancelled', 'lost', 'requires_reconcile'}


def _identifier(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def lifecycle(task, events):
    """Project a TaskStore task and its events without writes, clocks or I/O.

    ``accepted`` means the worker durably bound the turn/start response. A
    ``running`` claim additionally needs an exact-bound, validated turn/started
    receipt. Older receipts without turn_status do not establish that claim.
    checks_complete always awaits owner review; it never means human accepted.
    """
    engineering = type(task.get('payload')) is dict and task['payload'].get('kind') == 'rebuild_project'
    task_id = _identifier(task.get('id'))
    thread_id = _identifier(task.get('thread_id'))
    turn_id = _identifier(task.get('turn_id'))
    status = task.get('status')
    provider_bound = bool(thread_id and turn_id and task.get('provider_status') in _PROVIDER)
    evidence = []
    dispatch = turn_intent = started = False
    last_sequence = 0
    # TaskStore supplies ascending, unique positive sequences. Reject ambiguous
    # duplicate sequence evidence rather than choose by caller iteration order.
    rows = [e for e in events if type(e) is dict and type(e.get('sequence')) is int
            and e['sequence'] > 0]
    counts = {}
    for event in rows:
        counts[event['sequence']] = counts.get(event['sequence'], 0) + 1
    for event in sorted(rows, key=lambda e: e['sequence']):
        sequence = event['sequence']
        if counts[sequence] != 1:
            continue
        data = event.get('data')
        key = event.get('key')
        if type(data) is not dict or not isinstance(key, str):
            continue
        # An explicitly foreign task is never evidence for this projection.
        if 'task_id' in event and event['task_id'] != task_id:
            continue
        kind = None
        if key.startswith('dispatch-intent-'):
            dispatch = True
            kind = 'dispatch_prepared'
        elif key.startswith('turn-intent-') and thread_id and data.get('thread_id') == thread_id:
            turn_intent = True
            kind = 'turn_prepared'
        elif (provider_bound and data.get('method') == 'turn/started'
              and data.get('thread_id') == thread_id and data.get('turn_id') == turn_id
              and data.get('turn_status') == 'inProgress'
              and isinstance(data.get('digest'), str) and _SHA.fullmatch(data['digest'])
              and key == 'event-' + data['digest']):
            started = True
            kind = 'provider_turn_started'
        if kind:
            last_sequence = max(last_sequence, sequence)
            evidence.append({'kind': kind, 'sequence': sequence})

    if status == 'queued':
        stage, reason = 'queued', 'hub_received'
        # Reconciliation can requeue the same task ID; old receipts never
        # establish a provider state for that newly queued attempt.
        provider_bound = started = False
    elif status in _TERMINAL:
        stage, reason = status, 'durable_task_status'
    elif status == 'checks_complete':
        stage, reason = ('checks_complete', 'engineering_receipt_verification_pending') if engineering else ('await_owner_review', 'checks_complete_not_human_acceptance')
    elif status == 'validating':
        stage, reason = 'validating', 'durable_task_status'
    elif status in {'waiting_input', 'waiting_approval'}:
        stage, reason = status, 'durable_task_status'
    elif status == 'running' and started:
        stage, reason = 'running', 'validated_bound_turn_started'
    elif status == 'running' and provider_bound:
        stage, reason = 'accepted', 'bound_turn_start_response'
    elif status == 'running' and turn_intent:
        stage, reason = 'dispatching', 'turn_prepared_acceptance_unconfirmed'
    elif status == 'running' and dispatch:
        stage, reason = 'dispatching', 'dispatch_prepared_acceptance_unconfirmed'
    elif status == 'running':
        stage, reason = 'preparing', 'hub_claimed_provider_unconfirmed'
    else:
        stage, reason = 'unknown', 'unsupported_task_status'
    return {
        'schema_version': '1.0', 'task_id': task_id,
        'thread_id': thread_id, 'turn_id': turn_id,
        'stage': stage, 'reason': reason,
        'hub_received': task_id is not None,
        'provider_accepted': provider_bound,
        'provider_started': bool(started and status != 'queued'),
        'cancel_requested': task.get('cancel') in (True, 1),
        'owner_review_required': status == 'checks_complete' and not engineering,
        'human_accepted': False,
        'last_evidence_sequence': last_sequence, 'evidence': evidence,
    }
