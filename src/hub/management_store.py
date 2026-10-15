"""Explicit owner overlays in the existing management config, with atomic CAS."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import math
import os
import re
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .connection_records import content_hash, exact, identifier, require
from .service_contract import ServiceError

MAX_BYTES = 1024 * 1024
OVERLAY_FIELDS = {'priority', 'start_date', 'end_date', 'watch', 'order', 'layout', 'relations'}
TEMPORAL_OVERLAY_FIELDS = OVERLAY_FIELDS | {'timezone', 'milestones'}
ROLLING_OVERLAY_FIELDS = TEMPORAL_OVERLAY_FIELDS | {'rolling_plan'}
HISTORY_FIELDS = {'request_id', 'request_hash', 'revision', 'project_id', 'operation', 'before', 'after', 'created_at'}
COMMAND_FIELDS = {'request_id', 'expected_revision', 'expected_hash', 'project_id', 'operation', 'value'}


def validate_overlay(value, project_id=None):
    from .time_semantics import validate_calendar_date, validate_timezone

    require(type(value) is dict and set(value) in (OVERLAY_FIELDS, TEMPORAL_OVERLAY_FIELDS, ROLLING_OVERLAY_FIELDS), 'invalid management overlay fields')
    for key in ('priority', 'order'):
        require(value[key] is None or (type(value[key]) is int and value[key] >= 0), 'invalid ' + key)
    for key in ('start_date', 'end_date'):
        validate_calendar_date(value[key])
    if set(value) in (TEMPORAL_OVERLAY_FIELDS, ROLLING_OVERLAY_FIELDS):
        validate_timezone(value['timezone'])
        milestones = value['milestones']
        if milestones is not None:
            require(type(milestones) is list and len(milestones) <= 1000, 'invalid planned milestones')
            seen = set()
            for milestone in milestones:
                exact(milestone, {'id', 'label', 'date'}, 'planned milestone')
                identifier(milestone['id'], 'milestone')
                require(milestone['id'] not in seen, 'duplicate planned milestone')
                seen.add(milestone['id'])
                require(type(milestone['label']) is str and 0 < len(milestone['label']) <= 200 and bool(milestone['label'].strip()), 'invalid planned milestone label')
                validate_calendar_date(milestone['date'])
    if set(value) == ROLLING_OVERLAY_FIELDS:
        from .rolling_plan import validate_rolling_plan
        validate_rolling_plan(value['rolling_plan'])
    if value['start_date'] is not None and value['end_date'] is not None:
        require(value['start_date'] <= value['end_date'], 'date range reversed')
    require(value['watch'] is None or type(value['watch']) is bool, 'invalid watch')
    if value['layout'] is not None:
        exact(value['layout'], {'x', 'y'}, 'management layout')
        for key, limit in (('x', 320), ('y', 160)):
            number = value['layout'][key]
            require(type(number) in (int, float) and 0 <= number <= limit and math.isfinite(number), 'invalid layout')
    relations = value['relations']
    if relations is not None:
        exact(relations, {'belongs_to', 'depends_on', 'related_to', 'independent'}, 'management relations')
        require(relations['independent'] is None or type(relations['independent']) is bool, 'invalid independence')
        if relations['belongs_to'] is not None: identifier(relations['belongs_to'], 'parent')
        for key in ('depends_on', 'related_to'):
            ids = relations[key]
            require(type(ids) is list and len(ids) <= 10000, 'invalid relation list')
            for item in ids: identifier(item, 'relation')
            require(len(ids) == len(set(ids)), 'duplicate relation')
        if project_id is not None:
            require(project_id != relations['belongs_to'] and project_id not in relations['depends_on'] + relations['related_to'], 'self relation')
    return copy.deepcopy(value)


def _validate_projects(projects, *, version="1.3"):
    require(type(projects) is dict and len(projects) <= 10000, 'invalid overlays')
    for project_id, overlay in projects.items():
        identifier(project_id, 'project'); validate_overlay(overlay, project_id)
        require(version == '1.3' or set(overlay) != ROLLING_OVERLAY_FIELDS, 'rolling overlay requires management 1.3')
        require(version != '1.1' or set(overlay) == OVERLAY_FIELDS, 'temporal overlay requires management 1.2')
    done = set()
    for start in projects:
        current = start; path = set()
        while current in projects and current not in done:
            require(current not in path, 'management belonging cycle')
            path.add(current)
            relation = projects[current]['relations']
            current = relation['belongs_to'] if relation else None
        done.update(path)


def _request_hash(request_id, project_id, operation, value):
    return content_hash({'request_id': request_id, 'project_id': project_id, 'operation': operation, 'value': value})


def validate_management_state(config):
    """Replay exact versioned overlay payloads without normalizing historical values."""
    version = config['schema_version']
    require(version in ('1.1', '1.2', '1.3'), 'unsupported management history version')
    require(type(config['revision']) is int and config['revision'] >= 0, 'invalid management revision')
    history = config['history']
    require(type(history) is list and len(history) == config['revision'], 'invalid management history')
    projects = {}; requests = set()
    for revision, entry in enumerate(history, 1):
        exact(entry, HISTORY_FIELDS, 'management history entry')
        identifier(entry['request_id'], 'request'); identifier(entry['project_id'], 'project')
        require(entry['request_id'] not in requests, 'duplicate management request'); requests.add(entry['request_id'])
        require(type(entry['revision']) is int and entry['revision'] == revision, 'management history revision gap')
        require(entry['operation'] in ('set', 'retract'), 'invalid management operation')
        require(type(entry['created_at']) is str, 'invalid history time')
        try: stamp = datetime.fromisoformat(entry['created_at'])
        except ValueError: require(False, 'invalid history time')
        require(stamp.tzinfo is not None, 'history time needs timezone')
        project_id = entry['project_id']
        require(entry['before'] == projects.get(project_id), 'management history preimage mismatch')
        if entry['operation'] == 'set':
            validate_overlay(entry['after'], project_id); projects[project_id] = copy.deepcopy(entry['after'])
        else:
            require(entry['after'] is None, 'retract must clear overlay'); projects.pop(project_id, None)
        require(entry['request_hash'] == _request_hash(entry['request_id'], project_id, entry['operation'], entry['after']), 'management request hash mismatch')
        _validate_projects(projects, version=version)
    require(config['projects'] == projects, 'management history head mismatch')
    _validate_projects(config['projects'], version=version)
    return config


def relation_warnings(projects, project_id=None):
    """Dependency cycles are warnings, not task-graph rejection or independence inference."""
    _validate_projects(projects)
    warnings = []
    done = set(); cycle_nodes = set()
    # Bound recursion by iterative reachability for potentially large project networks.
    for start in projects:
        stack = [(start, False)]; path = []; active = set()
        while stack:
            node, exit_node = stack.pop()
            if exit_node:
                active.remove(node); path.pop(); done.add(node); continue
            if node in active:
                cycle_nodes.update(path[path.index(node):]); continue
            if node in done or node not in projects: continue
            active.add(node); path.append(node); stack.append((node, True))
            relation = projects[node]['relations']
            stack.extend((target, False) for target in reversed(relation['depends_on'] if relation else []))
    if cycle_nodes and (project_id is None or project_id in cycle_nodes): warnings.append('PROJECT_DEPENDENCY_CYCLE')
    return warnings


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, _value):
        return True


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class ManagementStore:
    def __init__(self, root, *, fault=None):
        from .metric_management import CONFIG_RELATIVE
        self.root = Path(root).absolute()
        self.path = self.root / CONFIG_RELATIVE
        self.fault = fault or (lambda _stage: None)

    def _parent(self):
        fd = os.open(self.path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in self.path.parts[1:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = next_fd
            return fd
        except BaseException:
            os.close(fd); raise

    def _verify_parent(self, parent):
        fresh = self._parent()
        try:
            require((os.fstat(parent).st_dev, os.fstat(parent).st_ino) ==
                    (os.fstat(fresh).st_dev, os.fstat(fresh).st_ino), 'management directory changed')
        finally: os.close(fresh)

    def _read(self, parent):
        fd = os.open(self.path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= MAX_BYTES, 'unsafe management config')
            with os.fdopen(fd, 'rb') as stream:
                fd = None; raw = stream.read(MAX_BYTES + 1); after = os.fstat(stream.fileno())
            named = os.stat(self.path.name, dir_fd=parent, follow_symlinks=False)
            require(len(raw) <= MAX_BYTES and _identity(before) == _identity(after) == _identity(named), 'management changed while reading')
            from .connections import parse_source
            value = parse_source(raw, "yaml")
            from .metric_management import validate_management_config
            value = validate_management_config(value)
            if value['schema_version'] in ('1.1', '1.2', '1.3'): validate_management_state(value)
            return value, raw, before
        finally:
            if fd is not None: os.close(fd)

    def snapshot(self):
        parent = None
        try:
            parent = self._parent(); value, raw, _ = self._read(parent)
            self._verify_parent(parent)
            return {'available': True, 'reason': None, 'revision': value.get('revision', 0), 'config_hash': content_hash(value),
                    'source_sha256': hashlib.sha256(raw).hexdigest(), 'projects': copy.deepcopy(value['projects']), 'history': copy.deepcopy(value.get('history', []))}
        except FileNotFoundError:
            return {'available': False, 'reason': 'MANAGEMENT_NOT_INITIALIZED', 'revision': None, 'config_hash': None, 'source_sha256': None, 'projects': {}, 'history': []}
        except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
            raise ServiceError('MANAGEMENT_CONFIG_REJECTED', status=503) from None
        finally:
            if parent is not None: os.close(parent)

    def apply(self, command, *, project_ids, authorize):
        try:
            exact(command, COMMAND_FIELDS, 'management command')
            identifier(command['request_id'], 'request'); identifier(command['project_id'], 'project')
            require(type(command['expected_revision']) is int and command['expected_revision'] >= 0, 'invalid revision')
            require(type(command['expected_hash']) is str and re.fullmatch('[0-9a-f]{64}', command['expected_hash']), 'invalid config hash')
            require(command['project_id'] in project_ids, 'project not permitted')
            require(command['operation'] in ('set', 'retract'), 'invalid management operation')
            if command['operation'] == 'set':
                validate_overlay(command['value'], command['project_id'])
                relations = command['value']['relations']
                references = ([relations['belongs_to']] if relations and relations['belongs_to'] else []) + (relations['depends_on'] + relations['related_to'] if relations else [])
                require(all(item in project_ids for item in references), 'relation not permitted')
            else: require(command['value'] is None, 'retract value must be null')
            command = copy.deepcopy(command)
        except (ValueError, TypeError, KeyError):
            raise ServiceError('MANAGEMENT_INVALID_COMMAND') from None
        authorize()
        parent = None; temporary = None; replace_started = False
        try:
            parent = self._parent()
            # Reject unsafe/missing configs before taking the directory lock or making files.
            self._read(parent)
            try: fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise ServiceError('MANAGEMENT_BUSY', status=409, retryable=True) from None
            config, raw, info = self._read(parent)
            request_hash = _request_hash(command['request_id'], command['project_id'], command['operation'], command['value'])
            for row in config.get('history', []):
                if row['request_id'] == command['request_id']:
                    if row['request_hash'] != request_hash:
                        raise ServiceError('MANAGEMENT_REQUEST_CONFLICT', status=409)
                    authorize()
                    replay_config, _, _ = self._read(parent)
                    self._verify_parent(parent)
                    require(replay_config == config, 'management changed during replay')
                    return self._receipt(config, row, True)
            if config.get('revision', 0) != command['expected_revision'] or content_hash(config) != command['expected_hash']:
                raise ServiceError('MANAGEMENT_CONFLICT', status=409)
            requested_version = ({7: '1.1', 9: '1.2', 10: '1.3'}[len(command['value'])]
                                 if command['operation'] == 'set' else '1.1')
            version = max(config['schema_version'], requested_version)
            next_config = copy.deepcopy(config); next_config.update(schema_version=version, revision=config.get('revision', 0) + 1)
            row = {'request_id': command['request_id'], 'request_hash': request_hash, 'revision': next_config['revision'],
                   'project_id': command['project_id'], 'operation': command['operation'], 'before': copy.deepcopy(config['projects'].get(command['project_id'])),
                   'after': command['value'], 'created_at': datetime.now(timezone.utc).isoformat()}
            next_config['history'] = copy.deepcopy(config.get('history', [])) + [row]
            if command['operation'] == 'set': next_config['projects'][command['project_id']] = command['value']
            else: next_config['projects'].pop(command['project_id'], None)
            try:
                _validate_projects(next_config['projects'])
            except ValueError:
                raise ServiceError('MANAGEMENT_RELATION_CONFLICT', status=409) from None
            from .metric_management import validate_management_config
            validate_management_config(next_config); validate_management_state(next_config)
            encoded = yaml.dump(next_config, Dumper=_Dumper, allow_unicode=True, sort_keys=False).encode('utf-8')
            if len(encoded) > MAX_BYTES: raise ServiceError('MANAGEMENT_CAPACITY', status=507)
            temporary = '.management-' + secrets.token_hex(16)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, stat.S_IMODE(info.st_mode), dir_fd=parent)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
            self.fault('before_replace'); authorize()
            current, current_raw, current_info = self._read(parent)
            self._verify_parent(parent)
            if current != config or current_raw != raw or _identity(current_info) != _identity(info):
                raise ServiceError('MANAGEMENT_CONFLICT', status=409)
            replace_started = True
            os.replace(temporary, self.path.name, src_dir_fd=parent, dst_dir_fd=parent)
            temporary = None
            self.fault('after_replace'); os.fsync(parent)
            observed, observed_raw, _ = self._read(parent)
            require(observed == next_config and observed_raw == encoded, 'management commit verification failed')
            self._verify_parent(parent)
            return self._receipt(next_config, row, False)
        except ServiceError:
            if replace_started: raise ServiceError('MANAGEMENT_COMMIT_UNCONFIRMED', status=503, retryable=True, outcome='UNKNOWN') from None
            raise
        except FileNotFoundError:
            raise ServiceError('MANAGEMENT_COMMIT_UNCONFIRMED' if replace_started else 'MANAGEMENT_NOT_INITIALIZED', status=503, outcome='UNKNOWN' if replace_started else 'NOT_COMMITTED') from None
        except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
            raise ServiceError('MANAGEMENT_COMMIT_UNCONFIRMED' if replace_started else 'MANAGEMENT_CONFIG_REJECTED', status=503, retryable=replace_started, outcome='UNKNOWN' if replace_started else 'NOT_COMMITTED') from None
        finally:
            if temporary is not None and parent is not None:
                try: os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError: pass
            if parent is not None: os.close(parent)

    @staticmethod
    def _receipt(config, row, replayed):
        return {'request_id': row['request_id'], 'revision': config['revision'], 'committed_revision': row['revision'],
                'config_hash': content_hash(config), 'project_id': row['project_id'], 'operation': row['operation'], 'replayed': replayed}
