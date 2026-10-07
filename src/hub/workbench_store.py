"""One bounded, versioned Hub workflow store; never an execution authority."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path

from .design_records import ID_RE, HASH_RE
from .service_contract import ServiceError

MAX_BYTES = 1024 * 1024
MAX_RECORDS = 128
COMMAND_FIELDS = {'request_id', 'expected_revision', 'binding', 'kind', 'region',
                  'requested_change', 'preserve_scope', 'view'}
OPTIONAL_FIELDS = {'design_reference', 'element_hint'}
BINDING_FIELDS = {'project_id', 'preview_id', 'candidate_id', 'candidate_revision',
                  'candidate_hash', 'artifact_id', 'artifact_sha256', 'type', 'classification',
                  'registry_hash', 'pages'}
VIEW_FIELDS = {'viewport_width', 'viewport_height', 'dpr', 'scroll_x', 'scroll_y',
               'zoom', 'image_width', 'image_height', 'natural_width', 'natural_height',
               'image_left', 'image_top', 'fit'}
PROVENANCE = {'kind': 'registered_artifact_view', 'live_page': False,
              'capture': False, 'source_mapping': None}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def exact(value, keys):
    if type(value) is not dict or set(value) != keys:
        raise ValueError('invalid fields')


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('invalid measurement')


def validate_binding(value):
    exact(value, BINDING_FIELDS)
    for key in ('project_id', 'preview_id', 'candidate_id', 'artifact_id'):
        if not isinstance(value[key], str) or not ID_RE.fullmatch(value[key]):
            raise ValueError('invalid identity')
    if type(value['candidate_revision']) is not int or value['candidate_revision'] <= 0:
        raise ValueError('invalid version')
    for key in ('candidate_hash', 'artifact_sha256', 'registry_hash'):
        if not isinstance(value[key], str) or not HASH_RE.fullmatch(value[key]):
            raise ValueError('invalid digest')
    if value['type'] not in {'prototype', 'screenshot'} or value['classification'] not in {'real', 'mock', 'dry-run', 'imported'}:
        raise ValueError('invalid material')
    pages = value['pages']
    if type(pages) is not list or not 1 <= len(pages) <= 16 or any(not isinstance(p, str) or not ID_RE.fullmatch(p) for p in pages) or pages != sorted(set(pages)):
        raise ValueError('invalid page scope')


def validate_command(value):
    if type(value) is not dict or not COMMAND_FIELDS <= set(value) or set(value) - COMMAND_FIELDS - OPTIONAL_FIELDS:
        raise ValueError('invalid fields')
    if not isinstance(value['request_id'], str) or not ID_RE.fullmatch(value['request_id']):
        raise ValueError('invalid request')
    if type(value['expected_revision']) is not int or value['expected_revision'] < 0:
        raise ValueError('invalid revision')
    validate_binding(value['binding'])
    for key in ('requested_change', 'preserve_scope'):
        text = value[key]
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError('feedback required')
        text.encode('utf-8')
    exact(value['view'], VIEW_FIELDS)
    for key in ('viewport_width', 'viewport_height', 'image_width', 'image_height', 'natural_width', 'natural_height'):
        number(value['view'][key], 1, 32768)
    for key in ('image_left', 'image_top', 'scroll_x', 'scroll_y'):
        number(value['view'][key], -1000000, 1000000)
    number(value['view']['dpr'], .1, 16)
    number(value['view']['zoom'], .1, 10)
    if value['view']['fit'] != 'image-content-box':
        raise ValueError('unsupported transform')
    width_ratio = value['view']['image_width'] / value['view']['natural_width']
    height_ratio = value['view']['image_height'] / value['view']['natural_height']
    if abs(width_ratio - height_ratio) > max(width_ratio, height_ratio) * .02:
        raise ValueError('image transform mismatch')
    if 'design_reference' in value:
        ref = value['design_reference']
        exact(ref, {'store_revision', 'event_id', 'event_hash', 'action'})
        if type(ref['store_revision']) is not int or ref['store_revision'] < 1 or not isinstance(ref['event_id'], str) or not ID_RE.fullmatch(ref['event_id']):
            raise ValueError('invalid decision reference')
        if not isinstance(ref['event_hash'], str) or not HASH_RE.fullmatch(ref['event_hash']) or ref['action'] not in {'select', 'request_changes', 'defer'}:
            raise ValueError('invalid decision reference')
    if value['kind'] == 'element':
        from .preview_bridge import validate_hint
        validate_hint(value.get('element_hint'))
        if value['region'] is not None:
            raise ValueError('element geometry is separate from image region')
    elif 'element_hint' in value:
        raise ValueError('unexpected element reference')
    elif value['kind'] == 'whole':
        if value['region'] is not None:
            raise ValueError('whole image has no rectangle')
    elif value['kind'] == 'region':
        region = value['region']; exact(region, {'x1', 'y1', 'x2', 'y2'})
        for item in region.values(): number(item, 0, 1)
        if region['x1'] >= region['x2'] or region['y1'] >= region['y2']:
            raise ValueError('empty region')
    else:
        raise ValueError('invalid annotation kind')
    canonical(value)
    return copy.deepcopy(value)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate key')
        result[key] = value
    return result


class WorkbenchStore:
    def __init__(self, root, *, fixture=False, fault=None):
        self.root = Path(root).absolute()
        if self.root.is_symlink():
            raise ServiceError('WORKBENCH_LOCATION_REJECTED')
        self.parts = ('docs', 'reports', 'linux-workbench') if fixture else ('data', 'workbench')
        self.filename = 'workbench-fixture.json' if fixture else 'workbench-store.json'
        self.path = self.root.joinpath(*self.parts, self.filename)
        self.classification = 'synthetic_fixture' if fixture else 'real'
        self.fault = fault or (lambda _stage: None)

    def _parent(self, *, create=False):
        opened = []
        try:
            fd = os.open(self.root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened.append(fd)
            for part in self.root.parts[1:]:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                opened.append(fd)
            for part in self.parts:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError: pass
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                opened.append(fd)
            opened.pop()
            return fd
        except FileNotFoundError:
            if not create: return None
            raise ServiceError('WORKBENCH_UNAVAILABLE', status=503) from None
        except OSError:
            raise ServiceError('WORKBENCH_LOCATION_REJECTED', status=503) from None
        finally:
            for fd in reversed(opened): os.close(fd)

    def empty(self):
        return {'schema_version': 1, 'classification': self.classification, 'revision': 0, 'records': []}

    def _validate(self, value):
        exact(value, {'schema_version', 'classification', 'revision', 'records'})
        if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['classification'] != self.classification:
            raise ValueError('unsupported schema')
        if type(value['revision']) is not int or type(value['records']) is not list or value['revision'] != len(value['records']) or len(value['records']) > MAX_RECORDS:
            raise ValueError('invalid history')
        ids = set()
        for revision, row in enumerate(value['records'], 1):
            exact(row, {'request_id', 'payload_hash', 'revision', 'command', 'record', 'record_hash'})
            command = validate_command(row['command'])
            if row['request_id'] != command['request_id'] or row['request_id'] in ids or type(row['revision']) is not int or row['revision'] != revision or command['expected_revision'] != revision - 1:
                raise ValueError('invalid receipt')
            ids.add(row['request_id'])
            if row['payload_hash'] != digest(command) or row['record_hash'] != digest(row['record']):
                raise ValueError('invalid content hash')
            record = row['record']
            exact(record, {'annotation_id', 'schema_version', 'created_at', 'actor', 'backend_instance', 'provenance', 'annotation'})
            if record['annotation_id'] != row['request_id'] or type(record['schema_version']) is not int or record['schema_version'] != 1 or record['actor'] not in {'owner_session','trusted_local_owner'} or record['provenance'] != PROVENANCE:
                raise ValueError('invalid provenance')
            if not isinstance(record['backend_instance'], str) or len(record['backend_instance']) != 32 or any(c not in '0123456789abcdef' for c in record['backend_instance']):
                raise ValueError('invalid backend')
            stamp = datetime.fromisoformat(record['created_at'])
            if stamp.tzinfo is None or record['annotation'] != {k: v for k, v in command.items() if k not in {'request_id', 'expected_revision'}}:
                raise ValueError('invalid annotation')

    def _read_fd(self, parent):
        try:
            fd = os.open(self.filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            return self.empty()
        except OSError:
            raise ServiceError('WORKBENCH_LOCATION_REJECTED', status=503) from None
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_BYTES:
                raise ValueError('unsafe store')
            with os.fdopen(fd, 'rb') as stream:
                fd = None; raw = stream.read(MAX_BYTES + 1); after = os.fstat(stream.fileno())
            if len(raw) > MAX_BYTES or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError('store changed')
            value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            self._validate(value)
            if canonical(value) + b'\n' != raw: raise ValueError('noncanonical store')
            return value
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            raise ServiceError('WORKBENCH_CORRUPT', status=503) from None
        finally:
            if fd is not None: os.close(fd)

    def read(self):
        parent = self._parent()
        if parent is None: return self.empty()
        try: return self._read_fd(parent)
        finally: os.close(parent)

    def receipt(self, request_id):
        for row in self.read()['records']:
            if row['request_id'] == request_id: return copy.deepcopy(row)
        return None

    def save(self, command, *, backend_instance, guard=lambda: None, actor='owner_session'):
        try: command = validate_command(command)
        except (ValueError, TypeError, UnicodeError):
            raise ServiceError('INVALID_ANNOTATION') from None
        # Reject existing corrupt/unsafe state before even creating lock files.
        self.read()
        parent = self._parent(create=True)
        lock = None; temporary = None; published = False; row = None
        try:
            lock = os.open(self.filename + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ServiceError('WORKBENCH_LOCATION_REJECTED', status=503)
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise ServiceError('WORKBENCH_BUSY', status=409) from None
            state = self._read_fd(parent); payload_hash = digest(command)
            for existing in state['records']:
                if existing['request_id'] == command['request_id']:
                    if existing['payload_hash'] != payload_hash:
                        raise ServiceError('ANNOTATION_REQUEST_CONFLICT', status=409)
                    return {'outcome': 'COMMITTED', 'receipt': existing, 'replayed': True}
            if state['revision'] != command['expected_revision']:
                raise ServiceError('ANNOTATION_REVISION_CONFLICT', status=409)
            if len(state['records']) >= MAX_RECORDS:
                raise ServiceError('WORKBENCH_CAPACITY', status=507)
            record = {'annotation_id': command['request_id'], 'schema_version': 1,
                      'created_at': datetime.now(timezone.utc).isoformat(), 'actor': actor,
                      'backend_instance': backend_instance, 'provenance': copy.deepcopy(PROVENANCE),
                      'annotation': {k: v for k, v in command.items() if k not in {'request_id', 'expected_revision'}}}
            row = {'request_id': command['request_id'], 'payload_hash': payload_hash,
                   'revision': state['revision'] + 1, 'command': command,
                   'record': record, 'record_hash': digest(record)}
            state['revision'] += 1; state['records'].append(row); self._validate(state)
            raw = canonical(state) + b'\n'
            if len(raw) > MAX_BYTES: raise ServiceError('WORKBENCH_CAPACITY', status=507)
            temporary = '.workbench-' + secrets.token_hex(16)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            self.fault('before_replace')
            guard()
            os.replace(temporary, self.filename, src_dir_fd=parent, dst_dir_fd=parent)
            temporary = None; published = True
            self.fault('after_replace'); os.fsync(parent)
            if self.read() != state: raise OSError('verification failed')
            return {'outcome': 'COMMITTED', 'receipt': row, 'replayed': False}
        except ServiceError:
            if published:
                raise ServiceError('ANNOTATION_COMMITTED_UNCONFIRMED', status=503, retryable=True,
                                   outcome='COMMITTED_DURABILITY_UNCONFIRMED', details={'request_id': command['request_id']}) from None
            raise
        except (OSError, ValueError):
            raise ServiceError('ANNOTATION_COMMITTED_UNCONFIRMED' if published else 'ANNOTATION_NOT_COMMITTED',
                               status=503, retryable=True,
                               outcome='COMMITTED_DURABILITY_UNCONFIRMED' if published else 'NOT_COMMITTED',
                               details={'request_id': command['request_id']}) from None
        finally:
            if temporary is not None:
                try: os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError: pass
            if lock is not None: os.close(lock)
            os.close(parent)
