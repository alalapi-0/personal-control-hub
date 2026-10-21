"""Owner-only bounded native metadata; never a continuation or writer authority."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import stat
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time

from .codex_adapter import (ReadonlyAppServerAdapter, DISCOVERY_SCHEMA_PINS,
                            REGISTERED_CONFIG_SHA256, VERSION, child_environment,
                            config_fingerprint, OfficialMetadataProfile, OfficialMetadataAdapter,
                            OFFICIAL_METADATA_VERSION, OFFICIAL_PAGE_LIMIT, OFFICIAL_MAX_PAGES)
from .service_contract import ServiceError
from .workbench_store import canonical

HUB_ROOT = Path(__file__).resolve().parents[2]
SOURCES = ('cli', 'appServer', 'vscode')
THREAD_ID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
TTL = 10


@dataclass(frozen=True)
class OfficialMetadataBinding:
    """Immutable local input guard, not execution or OS-enforcement authority."""
    profile: OfficialMetadataProfile
    path: Path
    sha256: str
    components: tuple

    def check(self):
        current = load_official_metadata_binding(self.path, self.sha256,
                                                server_root=self.profile.canonical_root)
        if current != self:
            raise ServiceError('OFFICIAL_METADATA_BINDING_REJECTED', status=503)


def load_official_metadata_binding(path, sha256, *, server_root):
    """Read a pinned server-only projection; it never grants execution authority."""
    descriptors = []
    components = {}
    def remember(path, info):
        identity = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
                    stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)
        # Directory entries legitimately churn. Pin identity/type/security, not
        # directory times, sizes or link counts. Files retain full change checks.
        if not stat.S_ISDIR(info.st_mode):
            identity += (info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        key = str(path)
        if key in components and components[key] != identity:
            raise ValueError('component changed')
        components[key] = identity
    def absolute(value):
        if (type(value) is not str or not Path(value).is_absolute()
                or str(Path(value)) != value or '..' in Path(value).parts):
            raise ValueError('path')
        return Path(value)
    def parent(path):
        descriptors.append(os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        current = Path('/')
        remember(current, os.fstat(descriptors[-1]))
        for part in path.parts[1:-1]:
            descriptors.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=descriptors[-1]))
            current = current / part
            remember(current, os.fstat(descriptors[-1]))
        return descriptors[-1]
    def fingerprint(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_uid,
                info.st_gid, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result
    try:
        path = absolute(str(path) if isinstance(path, Path) else path)
        root = absolute(str(server_root) if isinstance(server_root, Path) else server_root)
        if type(sha256) is not str or not re.fullmatch('[0-9a-f]{64}', sha256):
            raise ValueError('pin')
        directory = parent(path)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
                    or not 0 < before.st_size <= 16384):
                raise ValueError('file')
            raw = stream.read(16385)
            after = os.fstat(stream.fileno())
            current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if (len(raw) != before.st_size or fingerprint(before) != fingerprint(after)
                    or fingerprint(after) != fingerprint(current)
                    or hashlib.sha256(raw).hexdigest() != sha256):
                raise ValueError('changed')
            remember(path, after)
        binding = json.loads(raw, object_pairs_hook=pairs,
                             parse_constant=lambda value: (_ for _ in ()).throw(ValueError('constant')))
        keys = {'schema', 'profile', 'expected_thread', 'canonical_root', 'delivery_root',
                'schema_dir', 'binary', 'binary_sha256'}
        if (type(binding) is not dict or set(binding) != keys
                or binding['schema'] != 'pch-official-metadata-binding/1'
                or binding['profile'] != 'official_metadata_0.163'):
            raise ValueError('binding')
        paths = {key: absolute(binding[key]) for key in ('canonical_root', 'delivery_root', 'schema_dir', 'binary')}
        if paths['canonical_root'] != root:
            raise ValueError('root')
        # Check every component without resolving aliases or reading binary/schema content.
        for key, target in paths.items():
            directory = parent(target)
            info = os.stat(target.name, dir_fd=directory, follow_symlinks=False)
            if not (stat.S_ISREG(info.st_mode) if key == 'binary' else stat.S_ISDIR(info.st_mode)):
                raise ValueError('alias or type')
            remember(target, info)
        profile = OfficialMetadataProfile(binding['expected_thread'], paths['canonical_root'],
                                          paths['delivery_root'], paths['schema_dir'], paths['binary'],
                                          binding['binary_sha256'])
        return OfficialMetadataBinding(profile, path, sha256, tuple(sorted(components.items())))
    except (OSError, ValueError, TypeError, ServiceError, RecursionError):
        raise ServiceError('OFFICIAL_METADATA_BINDING_REJECTED', status=503) from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def environment_identity(root=HUB_ROOT):
    from .native_config_registration import load_registration
    registration = load_registration(root)
    env = child_environment()
    if config_fingerprint(Path(env['HOME']) / '.codex/config.toml') != registration.config_sha256:
        raise ServiceError('CODEX_CONFIG_DRIFT', status=503)
    binary = shutil.which('codex', path=env['PATH'])
    if binary is None:
        raise ServiceError('CODEX_VERSION_UNSUPPORTED', status=503)
    value = os.stat(binary)
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns,
            VERSION, registration.semantic_sha256, tuple(DISCOVERY_SCHEMA_PINS.items()))


class SessionService:
    def __init__(self, *, adapter_factory=None, clock=time.monotonic,
                 wall=time.time, identity=None, metadata_read_id=None, excluded_read_ids=(),
                 hub_root=HUB_ROOT, official_metadata_profile=None):
        if (not isinstance(hub_root, (str, Path)) or not Path(hub_root).is_absolute()
                or '..' in Path(hub_root).parts):
            raise ServiceError('SESSION_ROOT_REJECTED')
        self.hub_root = Path(hub_root)
        # The existing server-only injection accepts a loaded guarded binding;
        # adapters still receive the unchanged frozen protocol profile.
        self.official_metadata_binding = (official_metadata_profile
            if type(official_metadata_profile) is OfficialMetadataBinding else None)
        if self.official_metadata_binding is not None:
            official_metadata_profile = self.official_metadata_binding.profile
        self.official_metadata_profile = official_metadata_profile
        if official_metadata_profile is not None:
            if (type(official_metadata_profile) is not OfficialMetadataProfile
                    or official_metadata_profile.canonical_root != self.hub_root
                    or metadata_read_id is not None or excluded_read_ids):
                raise ServiceError('SESSION_SELECTION_REJECTED')
        from .native_config_registration import load_registration
        self.adapter_factory = adapter_factory or (lambda launch_root: ReadonlyAppServerAdapter(
            launch_root, registration=load_registration(self.hub_root)))
        if official_metadata_profile is not None:
            self.adapter_factory = adapter_factory or (lambda root: OfficialMetadataAdapter(official_metadata_profile))
        self.clock, self.wall = clock, wall
        self.identity = identity or (official_metadata_profile.identity if official_metadata_profile else
                                     lambda: environment_identity(self.hub_root))
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

    def _check_metadata_binding(self):
        if self.official_metadata_binding is not None:
            self.official_metadata_binding.check()

    def _view(self):
        data = copy.deepcopy(self.cache['data'])
        data['age_seconds'] = max(0, self.clock()-self.cache['created'])
        return data

    def _row(self, thread, source, allowed):
        if type(thread) is not dict or set(thread) - allowed:
            raise ServiceError('SESSION_METADATA_REJECTED', status=503)
        ident, status = thread.get('id'), thread.get('status')
        if (type(ident) is not str or not THREAD_ID.fullmatch(ident)
                or thread.get('cwd') != str(self.hub_root) or thread.get('source') != source
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
            self._check_metadata_binding()
            environment = self.identity()
            now = self.clock()
            if self.cache and (self.cache['owner'], self.cache['environment']) == (owner, environment):
                age = now-self.cache['created']
                if 0 <= age < TTL:
                    self._authorize(authorize, owner)
                    self._check_metadata_binding()
                    return self._view()
            self.cache = None
            if self.official_metadata_profile is not None:
                return self._official_catalog(authorize, owner, environment)
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
                            result = adapter.call('thread/list', {'cwd': str(self.hub_root), 'sourceKinds': [source],
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

    def _official_catalog(self, authorize, owner, environment):
        profile = self.official_metadata_profile
        self._check_metadata_binding()
        adapter = self.adapter_factory(profile.delivery_root)
        turns, seen, cursor, complete = [], set(), None, False
        try:
            self._check_metadata_binding()
            adapter.open()
            self._authorize(authorize, owner)
            result = adapter.call('thread/read', {'threadId': profile.expected_thread, 'includeTurns': False})
            thread = result['thread']
            if thread['id'] != profile.expected_thread or thread['cwd'] != str(profile.delivery_root):
                raise ServiceError('SESSION_BINDING_MISMATCH', status=503)
            for page in range(OFFICIAL_MAX_PAGES):
                self._authorize(authorize, owner)
                params = {'threadId': profile.expected_thread, 'itemsView': 'notLoaded',
                          'limit': OFFICIAL_PAGE_LIMIT, 'sortDirection': 'desc'}
                if page:
                    params['cursor'] = cursor
                result = adapter.call('thread/turns/list', params)
                for turn in result['data']:
                    if turn['id'] in seen or turn.get('items') != [] or turn.get('itemsView') != 'notLoaded':
                        raise ServiceError('SESSION_METADATA_REJECTED', status=503)
                    seen.add(turn['id'])
                    turns.append({key: turn.get(key) for key in ('id', 'status', 'startedAt', 'completedAt',
                                                                'durationMs', 'rootTurnId', 'itemsView')})
                cursor = result.get('nextCursor')
                if 'nextCursor' not in result or cursor is None:
                    complete = 'nextCursor' in result
                    break
            self._authorize(authorize, owner)
            if self.identity() != environment:
                raise ServiceError('SESSION_GENERATION_CHANGED', status=409)
            row = {'id': thread['id'], 'session_id': thread.get('sessionId'), 'source': thread['source'] if type(thread['source']) is str else 'unknown',
                   'state': {'idle': 'idle_unverified', 'notLoaded': 'notLoaded', 'active': 'busy',
                             'systemError': 'error'}.get(thread['status']['type'], 'unknown'),
                   'createdAt': thread['createdAt'], 'updatedAt': thread['updatedAt'],
                   'continue_enabled': False, 'resume_verified': False, 'reason': 'METADATA_IS_NOT_EXECUTION_AUTHORITY'}
            data = {'project': 'Personal Control Hub', 'scope': 'hub_only', 'profile': 'official_metadata_0.163',
                    'native_execution_admission': False,
                    'version': OFFICIAL_METADATA_VERSION, 'classification': 'native_metadata' if adapter.transport ==
                    'app_server_stdio' else 'mock', 'observed_at': self.wall(), 'ttl_seconds': TTL,
                    'sources': [{'source': row['source'], 'state': 'readonly', 'rows': [row],
                                 'has_cursor': not complete}], 'unprobed': ['cloud', 'remote'],
                    'metadata_read': {'id': row['id'], 'session_id': row['session_id'], 'turns': turns,
                                      'pages_complete': complete, 'has_next_cursor': cursor is not None,
                                      'activity_authority': 'unknown', 'resume_verified': False},
                    'continue_enabled': False, 'resume_verified': False, 'native_calls': adapter.next_id}
            data['generation'] = hashlib.sha256(canonical(data)).hexdigest()
            self.cache = {'owner': owner, 'environment': environment, 'created': self.clock(), 'data': data}
            return self._view()
        finally:
            adapter.close()
            self._check_metadata_binding()
            self._authorize(authorize, owner)
            if self.identity() != environment:
                raise ServiceError('SESSION_GENERATION_CHANGED', status=409)
