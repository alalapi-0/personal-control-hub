"""Owner-only bounded native metadata; never a continuation or writer authority."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time

from .codex_adapter import (ReadonlyAppServerAdapter, DISCOVERY_SCHEMA_PINS,
                            REGISTERED_CONFIG_SHA256, VERSION, child_environment,
                            config_fingerprint)
from .service_contract import ServiceError
from .workbench_store import canonical

HUB_ROOT = Path(__file__).resolve().parents[2]
SOURCES = ('cli', 'appServer', 'vscode')
THREAD_ID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
TTL = 10


def environment_identity():
    env = child_environment()
    if config_fingerprint(Path(env['HOME']) / '.codex/config.toml') != REGISTERED_CONFIG_SHA256:
        raise ServiceError('CODEX_CONFIG_DRIFT', status=503)
    binary = shutil.which('codex', path=env['PATH'])
    if binary is None:
        raise ServiceError('CODEX_VERSION_UNSUPPORTED', status=503)
    value = os.stat(binary)
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns,
            VERSION, REGISTERED_CONFIG_SHA256, tuple(DISCOVERY_SCHEMA_PINS.items()))


class SessionService:
    def __init__(self, *, adapter_factory=ReadonlyAppServerAdapter, clock=time.monotonic,
                 wall=time.time, identity=environment_identity, metadata_read_id=None, excluded_read_ids=()):
        self.adapter_factory, self.clock, self.wall, self.identity = adapter_factory, clock, wall, identity
        # Optional exact selection is trusted server configuration, never an HTTP field.
        # Ordinary browser discovery performs zero reads and cannot select a thread.
        if metadata_read_id is not None and (type(metadata_read_id) is not str or not THREAD_ID.fullmatch(metadata_read_id)):
            raise ServiceError('SESSION_SELECTION_REJECTED')
        self.metadata_read_id, self.excluded_read_ids = metadata_read_id, frozenset(excluded_read_ids)
        self.lock = threading.Lock()
        self.cache = None

    def _authorize(self, authorize, expected=None):
        try:
            key = authorize()
            if key is None or key is False or expected is not None and key != expected:
                raise ServiceError('OWNER_AUTH_FAILED', status=401)
            return key
        except BaseException:
            self.cache = None
            raise

    def _view(self):
        data = copy.deepcopy(self.cache['data'])
        data['age_seconds'] = max(0, self.clock()-self.cache['created'])
        return data

    def _row(self, thread, source, allowed):
        if type(thread) is not dict or set(thread) - allowed:
            raise ServiceError('SESSION_METADATA_REJECTED', status=503)
        ident, status = thread.get('id'), thread.get('status')
        if (type(ident) is not str or not THREAD_ID.fullmatch(ident)
                or thread.get('cwd') != str(HUB_ROOT) or thread.get('source') != source
                or thread.get('modelProvider') != 'openai' or type(status) is not dict):
            raise ServiceError('SESSION_BINDING_MISMATCH', status=503)
        kind = status.get('type')
        state = {'active': 'busy', 'idle': 'idle_unverified', 'notLoaded': 'notLoaded',
                 'systemError': 'error'}.get(kind, 'unknown')
        dates = {k: thread.get(k) for k in ('createdAt', 'updatedAt')}
        if any(type(v) is not int or not 0 <= v <= 2**53-1 for v in dates.values()):
            raise ServiceError('SESSION_METADATA_REJECTED', status=503)
        return {'id': ident, 'source': source, 'provider': 'openai', 'state': state,
                **dates, 'continue_enabled': False, 'resume_verified': False,
                'reason': {'busy': 'ACTIVE_WRITER_NOT_OWNED', 'idle_unverified': 'IDLE_AUTHORITY_UNVERIFIED',
                           'notLoaded': 'NOT_LOADED_IS_NOT_IDLE', 'error': 'PROVIDER_STATUS_ERROR',
                           'unknown': 'ACTIVITY_UNKNOWN'}[state]}

    def catalog(self, authorize):
        owner = self._authorize(authorize)
        if not self.lock.acquire(blocking=False):
            raise ServiceError('SESSION_DISCOVERY_BUSY', status=503, retryable=True)
        try:
            self._authorize(authorize, owner)
            environment = self.identity()
            now = self.clock()
            if self.cache and (self.cache['owner'], self.cache['environment']) == (owner, environment):
                age = now-self.cache['created']
                if 0 <= age < TTL:
                    self._authorize(authorize, owner)
                    return self._view()
            self.cache = None
            groups, seen = [], {}
            read = None
            with tempfile.TemporaryDirectory(prefix='hub-session-discovery-') as directory:
                adapter = self.adapter_factory(Path(directory))
                try:
                    adapter.open()
                    if any(adapter.schema_hashes.get(k) != v for k, v in DISCOVERY_SCHEMA_PINS.items()):
                        raise ServiceError('CODEX_SCHEMA_CHANGED', status=503)
                    allowed = set(adapter.schemas['ThreadListResponse']['definitions']['Thread']['properties'])
                    for source in SOURCES:
                        self._authorize(authorize, owner)
                        group = {'source': source, 'state': 'supported_empty', 'rows': [], 'has_cursor': False}
                        try:
                            result = adapter.call('thread/list', {'cwd': str(HUB_ROOT), 'sourceKinds': [source],
                                'limit': 3, 'archived': False, 'useStateDbOnly': True})
                            if (type(result) is not dict or set(result)-{'backwardsCursor', 'data', 'nextCursor'}
                                    or 'data' not in result
                                    or type(result['data']) is not list or len(result['data']) > 3
                                    or result.get('backwardsCursor') is not None and type(result['backwardsCursor']) is not str
                                    or result.get('nextCursor') is not None and type(result['nextCursor']) is not str):
                                raise ServiceError('SESSION_METADATA_REJECTED', status=503)
                            rows = [self._row(t, source, allowed) for t in result['data']]
                            group.update(rows=rows, state='readonly' if rows else 'supported_empty',
                                         has_cursor=any(result.get(k) is not None for k in ('backwardsCursor','nextCursor')))
                            for row in rows:
                                if row['id'] in seen:
                                    seen[row['id']]['state'] = 'error'; seen[row['id']]['rows'] = []
                                    seen[row['id']]['reason'] = 'SESSION_DUPLICATE_ID'
                                    raise ServiceError('SESSION_DUPLICATE_ID', status=503)
                                seen[row['id']] = group
                        except ServiceError as error:
                            unsupported = error.code == 'CODEX_RPC_REJECTED' and error.details.get('rpc_code') == -32601
                            group.update(state='unsupported' if unsupported else 'error', rows=[], reason=error.code)
                        groups.append(group)
                    # Pagination and unloaded/busy/unknown state cannot justify a read.
                    selected = next((r for g in groups if g['state'] == 'readonly' and not g['has_cursor']
                                     for r in g['rows'] if r['state'] == 'idle_unverified'
                                     and r['id'] == self.metadata_read_id and r['id'] not in self.excluded_read_ids), None)
                    if selected:
                        self._authorize(authorize, owner)
                        result = adapter.call('thread/read', {'threadId': selected['id'], 'includeTurns': False})
                        if type(result) is not dict or set(result) != {'thread'}:
                            raise ServiceError('SESSION_READ_MISMATCH', status=503)
                        thread = result['thread']
                        row = self._row(thread, selected['source'], allowed)
                        if (row['id'] != selected['id'] or thread.get('turns') not in (None, [])
                                or any(row[k] != selected[k] for k in ('createdAt', 'updatedAt'))):
                            raise ServiceError('SESSION_READ_MISMATCH', status=503)
                        read = {'id': row['id'], 'source': row['source'], 'state': row['state'],
                                'include_turns': False, 'resume_verified': False}
                    self._authorize(authorize, owner)
                    calls = adapter.next_id
                    classification = 'native_metadata' if adapter.transport == 'app_server_stdio' else 'mock'
                finally:
                    adapter.close()
            if self.identity() != environment:
                raise ServiceError('SESSION_GENERATION_CHANGED', status=409)
            self._authorize(authorize, owner)
            data = {'project': 'Personal Control Hub', 'scope': 'hub_only', 'classification': classification,
                    'version': VERSION, 'observed_at': self.wall(), 'ttl_seconds': TTL,
                    'sources': groups, 'unprobed': ['cloud', 'remote'], 'metadata_read': read,
                    'continue_enabled': False, 'resume_verified': False, 'native_calls': calls}
            data['generation'] = hashlib.sha256(canonical(data)).hexdigest()
            self.cache = {'owner': owner, 'environment': environment, 'created': self.clock(), 'data': data}
            return self._view()
        except ServiceError as error:
            self.cache = None
            # Native error details/paths or provider payloads are not a browser contract.
            raise ServiceError(error.code, status=error.status, retryable=error.retryable) from None
        except BaseException:
            self.cache = None
            raise
        finally:
            self.lock.release()
