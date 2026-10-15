"""Pure, deny-by-default policy for one exactly registered command callback.

This is not grant verification. The caller must verify grant provenance/hash,
root identity, lease, and expiry again immediately before sending a response.
No shell parsing or filesystem/process access belongs in this module.
"""
from __future__ import annotations

import hashlib
import json
import math
import posixpath


_METHOD = 'item/commandExecution/requestApproval'
_PARAMS = {'threadId', 'turnId', 'itemId', 'startedAtMs', 'approvalId',
           'availableDecisions', 'command', 'commandActions', 'cwd',
           'environmentId', 'kind', 'networkApprovalContext',
           'additionalPermissions', 'proposedExecpolicyAmendment',
           'proposedNetworkPolicyAmendments', 'reason'}
_ESCALATIONS = {'networkApprovalContext', 'additionalPermissions',
                'proposedExecpolicyAmendment', 'proposedNetworkPolicyAmendments',
                'environmentId'}


def _text(value):
    return type(value) is str and bool(value) and '\x00' not in value


def _absolute(value):
    return (_text(value) and value.startswith('/') and not value.startswith('//')
            and posixpath.normpath(value) == value)


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _actions(value):
    """Validate only the pinned wire shape, never infer command authority."""
    if value is None:
        return True
    if type(value) is not list:
        return False
    for action in value:
        if type(action) is not dict or not _text(action.get('command')):
            return False
        kind = action.get('type')
        shapes = {'read': ({'type', 'command', 'name', 'path'}, set()),
                  'listFiles': ({'type', 'command'}, {'path'}),
                  'search': ({'type', 'command'}, {'path', 'query'}),
                  'unknown': ({'type', 'command'}, set())}
        if type(kind) is not str or kind not in shapes:
            return False
        required, optional = shapes[kind]
        if not required <= action.keys() or action.keys() - required - optional:
            return False
        for key in required - {'type', 'command'}:
            if type(action[key]) is not str:
                return False
        for key in optional:
            if action.get(key) is not None and type(action[key]) is not str:
                return False
    return True


def approval_policy(grant, request, *, thread_id, turn_id, item_id,
                    request_id, grant_root, now):
    """Return displayable decisions and an opaque operation binding.

    ``approved_commands`` is a list of exact ``{'command': str, 'cwd': str}``
    records in an already verified grant. Only the exact root is supported.
    All inputs are read only; neither returned decision executes an operation.
    ``now`` is supplied by the caller to keep this policy deterministic.
    """
    def deny(reason):
        return {'decisions': ['decline'], 'reason': reason}

    if type(grant) is not dict or not _absolute(grant_root) or grant.get('root') != grant_root:
        return deny('grant_root_mismatch')
    if not _number(now) or not _number(grant.get('expires_at')) or grant['expires_at'] <= now:
        return deny('grant_expired')
    commands = grant.get('approved_commands')
    if type(commands) is not list or not commands:
        return deny('command_not_registered')
    for record in commands:
        if (type(record) is not dict or set(record) != {'command', 'cwd'}
                or not _text(record['command']) or record['cwd'] != grant_root):
            return deny('invalid_command_registration')
    if type(request) is not dict or set(request) != {'id', 'method', 'params'}:
        return deny('unknown_request_shape')
    if request['method'] != _METHOD:
        return deny('unsupported_request')
    if (type(request_id) not in (str, int) or type(request['id']) is not type(request_id)
            or request['id'] != request_id or (type(request_id) is str and not _text(request_id))):
        return deny('request_id_mismatch')
    params = request['params']
    if (type(params) is not dict or set(params) - _PARAMS
            or not {'threadId', 'turnId', 'itemId', 'startedAtMs'} <= params.keys()):
        return deny('unknown_params_shape')
    for key, expected in (('threadId', thread_id), ('turnId', turn_id), ('itemId', item_id)):
        if not _text(expected) or params[key] != expected:
            return deny('operation_id_mismatch')
    if type(params['startedAtMs']) is not int:
        return deny('invalid_request_timestamp')
    if params.get('kind', 'command') != 'command':
        return deny('unsupported_command_kind')
    if any(params.get(key) is not None for key in _ESCALATIONS):
        return deny('additional_authority_requested')
    if params.get('approvalId') is not None and not _text(params['approvalId']):
        return deny('invalid_callback_id')
    if params.get('reason') is not None and type(params['reason']) is not str:
        return deny('invalid_reason')
    if not _actions(params.get('commandActions')):
        return deny('invalid_command_actions')
    decisions = params.get('availableDecisions')
    if decisions is not None:
        if (type(decisions) is not list or not decisions
                or any(type(value) is not str or value not in ('accept', 'decline', 'cancel', 'acceptForSession') for value in decisions)
                or len(set(decisions)) != len(decisions) or 'accept' not in decisions):
            return deny('unsupported_decisions')
    command, cwd = params.get('command'), params.get('cwd')
    if not _text(command) or cwd != grant_root or {'command': command, 'cwd': cwd} not in commands:
        return deny('command_not_registered')
    binding = {'request_id': request_id, 'thread_id': thread_id, 'turn_id': turn_id,
               'item_id': item_id, 'approval_id': params.get('approvalId'),
               'command': command, 'cwd': cwd, 'started_at_ms': params['startedAtMs']}
    encoded = json.dumps(binding, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return {'decisions': ['decline', 'accept'], 'reason': 'exact_registered_command',
            'operation_sha256': hashlib.sha256(encoded).hexdigest()}
