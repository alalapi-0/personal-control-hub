"""Bounded, server-registered story exchange; this module never promotes source data."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import stat
import time
from pathlib import Path, PurePosixPath

from jsonschema import FormatChecker, validators

from .service_contract import ServiceError
from .task_store import stable_id

_FIELDS = {'kind', 'grant_id', 'version', 'source', 'task_id', 'project_id', 'root',
           'root_identity', 'mode', 'parent_task_id', 'auth', 'expires_at',
           'max_turn_seconds', 'isolated_root', 'writable_scope', 'checks',
           'task_spec', 'story_exchange'}
_EXCHANGE = {'canonical_root', 'canonical_root_identity', 'registry_hash',
             'specification_sha256', 'request_id', 'request_sha256', 'input_files',
             'output_path', 'registration_sha256', 'expected_count', 'source_preconditions'}
_PRECONDITIONS = {'workspace_revision', 'workspace_sha256', 'source_state_sha256',
                  'baseline_id', 'dataset'}
_INPUTS = {'AGENTS.md', 'input/export.json', 'input/context.json',
           'input/result-template.json', 'input/result-schema.json'}
_SUMMARY = ('title', 'goal', 'obstacle', 'action', 'choice', 'consequence',
            'difference', 'strength', 'weakness')
_HASH = re.compile(r'^[0-9a-f]{64}$')


def _reject(code='STORY_EXCHANGE_REJECTED'):
    raise ServiceError(code, status=409)


def is_business(grant):
    return type(grant) is dict and grant.get('kind') == 'story_exchange'


def _text(value, limit=8192):
    return type(value) is str and bool(value.strip()) and len(value) <= limit and '\x00' not in value


def _hash(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _root(value):
    return _text(value, 4096) and Path(value).is_absolute() and str(Path(value)) == value and '..' not in Path(value).parts


def _relative(value):
    return _text(value, 256) and not PurePosixPath(value).is_absolute() and \
        str(PurePosixPath(value)) == value and all(p not in ('.', '..') for p in value.split('/'))


def _read(root, relative, maximum):
    """Open every component without following aliases, including the root itself."""
    descriptors = []
    try:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(fd)
        for part in Path(root).parts[1:] + PurePosixPath(relative).parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        fd = os.open(PurePosixPath(relative).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        descriptors.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
            _reject()
        raw = os.read(fd, maximum + 1)
        after = os.fstat(fd)
        if len(raw) > maximum or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != \
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            _reject()
        return raw
    except OSError as exc:
        raise ServiceError('STORY_EXCHANGE_FILE_REJECTED', status=409) from exc
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _stage(grant):
    from .task_service import root_identity
    exchange = grant['story_exchange']
    if root_identity(grant['root']) != grant['root_identity'] or \
            root_identity(exchange['canonical_root']) != exchange['canonical_root_identity']:
        _reject('GRANT_VERSION_STALE')
    stage = Path(grant['root'])
    canonical = Path(exchange['canonical_root'])
    if stage == canonical or canonical in stage.parents or stage in canonical.parents:
        _reject()
    expected = {row['path']: row['sha256'] for row in exchange['input_files']}
    allowed = set(expected) | {exchange['output_path']}
    found = set()
    # Traverse directory descriptors so a replaced directory cannot redirect reads.
    def visit(fd, prefix=''):
        for name in os.listdir(fd):
            relative = f'{prefix}{name}'
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if relative not in {'input', 'output'}:
                    _reject()
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    visit(child, relative + '/')
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and relative in allowed:
                found.add(relative)
            else:
                _reject()
    descriptors = []
    try:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(fd)
        for part in stage.parts[1:]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        visit(fd)
    except OSError as exc:
        raise ServiceError('STORY_EXCHANGE_FILE_REJECTED', status=409) from exc
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
    if not set(expected) <= found:
        _reject()
    for relative, sha in expected.items():
        if hashlib.sha256(_read(str(stage), relative, 1048576)).hexdigest() != sha:
            _reject('STORY_EXCHANGE_INPUT_CHANGED')
    if exchange['output_path'] in found:
        _read(str(stage), exchange['output_path'], 262144)


def validate(grant, live=False, *, allow_expired=False):
    if type(grant) is not dict or set(grant) != _FIELDS or not is_business(grant):
        _reject('INVALID_GRANT')
    for key in ('grant_id', 'task_id', 'project_id'):
        stable_id(grant[key])
    if type(grant['version']) is not int or grant['version'] < 1 or \
            grant['source'] != 'verified_owner_decision' or grant['mode'] != 'new' or \
            grant['parent_task_id'] is not None or grant['auth'] != 'ChatGPT' or \
            grant['isolated_root'] is not True or grant['writable_scope'] != ['output/'] or \
            grant['checks'] != ['story_exchange']:
        _reject('INVALID_GRANT')
    expiry = grant['expires_at']
    if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= 0 or expiry >= time.time() + 86400 or (not allow_expired and expiry <= time.time()):
        _reject('GRANT_EXPIRED')
    if type(grant['max_turn_seconds']) is not int or not 10 <= grant['max_turn_seconds'] <= 600 or \
            not _root(grant['root']) or not _hash(grant['root_identity']):
        _reject('INVALID_GRANT')
    spec = grant['task_spec']
    if type(spec) is not dict or set(spec) != {'requirement', 'scope', 'acceptance'} or not all(_text(v) for v in spec.values()):
        _reject('INVALID_GRANT')
    ex = grant['story_exchange']
    if type(ex) is not dict or set(ex) != _EXCHANGE or not _root(ex['canonical_root']) or \
            any(not _hash(ex[k]) for k in ('canonical_root_identity', 'registry_hash', 'specification_sha256', 'request_sha256', 'registration_sha256')) or \
            ex['output_path'] != 'output/result.json' or type(ex['expected_count']) is not int or ex['expected_count'] != 3:
        _reject('INVALID_GRANT')
    stable_id(ex['request_id'])
    rows = ex['input_files']
    if type(rows) is not list or len(rows) != len(_INPUTS) or any(type(r) is not dict or set(r) != {'path', 'sha256'} or not _relative(r['path']) or not _hash(r['sha256']) for r in rows) or {r['path'] for r in rows} != _INPUTS:
        _reject('INVALID_GRANT')
    pre = ex['source_preconditions']
    if type(pre) is not dict or set(pre) != _PRECONDITIONS or type(pre['workspace_revision']) is not int or pre['workspace_revision'] < 0 or \
            any(not _hash(pre[k]) for k in ('workspace_sha256', 'source_state_sha256')) or not _text(pre['baseline_id'], 256) or pre['dataset'] != 'live':
        _reject('INVALID_GRANT')
    if live:
        _stage(grant)
    return copy.deepcopy(grant)


def prompt(grant):
    grant = validate(grant, live=True)
    spec = grant['task_spec']
    return ('真实项目 story-faceless-utopia《日常》的受限创作任务。\n'
            f"需求：{spec['requirement']}\n范围：{spec['scope']}\n验收：{spec['acceptance']}\n"
            '仅阅读当前隔离目录的 AGENTS.md、input/export.json、input/context.json、'
            'input/result-template.json、input/result-schema.json；模板不是结果，必须实际填写。'
            '仅写 output/result.json，匹配完整 schema 和导出请求的 request_id/request_sha256。'
            '生成恰好 3 个真实、非模拟、live/candidate 的 story_option，候选间至少在两个叙事维度有实质差异。'
            '遵循上下文与已接受种子；不得写来源库、访问网络、建立正式分集或 Canon，'
            '不得冒充所有者决定或用夹具输出替代。记录实际执行来源；未知 tokens/cost 留 null。')


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _reject('STORY_EXCHANGE_JSON_REJECTED')
            result[key] = value
        return result
    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            _reject('STORY_EXCHANGE_JSON_REJECTED')
        return number
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_float=finite,
                          parse_constant=lambda _: _reject('STORY_EXCHANGE_JSON_REJECTED'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ServiceError('STORY_EXCHANGE_JSON_REJECTED', status=409) from exc


def _local_refs(value):
    if isinstance(value, dict):
        if '$ref' in value and (type(value['$ref']) is not str or not value['$ref'].startswith('#')):
            _reject('STORY_EXCHANGE_SCHEMA_REJECTED')
        for key in ('$dynamicRef', '$recursiveRef'):
            if key in value and (type(value[key]) is not str or not value[key].startswith('#')):
                _reject('STORY_EXCHANGE_SCHEMA_REJECTED')
        for child in value.values():
            _local_refs(child)
    elif isinstance(value, list):
        for child in value:
            _local_refs(child)


def _validated_result(grant, *, allow_expired=False):
    grant = validate(grant, live=True, allow_expired=allow_expired)
    ex = grant['story_exchange']
    raw = _read(grant['root'], ex['output_path'], 262144)
    result = _json(raw)
    schema = _json(_read(grant['root'], 'input/result-schema.json', 1048576))
    _local_refs(schema)
    try:
        validator = validators.validator_for(schema)
        validator.check_schema(schema)
        validator(schema, format_checker=FormatChecker()).validate(result)
    except Exception as exc:
        raise ServiceError('STORY_EXCHANGE_RESULT_SCHEMA_REJECTED', status=409) from exc
    pre = ex['source_preconditions']
    if type(result) is not dict or any(result.get(k) != expected for k, expected in (
            ('request_id', ex['request_id']), ('request_sha256', ex['request_sha256']),
            ('baseline_id', pre['baseline_id']), ('dataset', 'live'))):
        _reject('STORY_EXCHANGE_RESULT_BINDING_REJECTED')
    objects = result.get('objects')
    if type(objects) is not list or len(objects) != 3:
        _reject()
    summaries, ids = [], set()
    for item in objects:
        if type(item) is not dict or item.get('type') != 'story_option' or item.get('status') != 'candidate' or item.get('dataset') != 'live' or \
                item.get('baseline_id') != pre['baseline_id'] or type(item.get('provenance')) is not dict or \
                item['provenance'].get('simulated') is not False or item['provenance'].get('actor') != 'agent' or \
                not _text(item.get('id'), 120) or item['id'] in ids:
            _reject()
        ids.add(item['id'])
        data = item.get('data')
        if type(data) is not dict or data.get('request_id') != ex['request_id'] or any(not _text(data.get(k)) or re.search(r'<[^>]+>', data[k]) for k in _SUMMARY):
            _reject('STORY_EXCHANGE_RESULT_CONTENT_REJECTED')
        # Bound the public review to well below TaskStore's event-size limit.
        summaries.append({k: data[k].replace(grant['root'], '[隔离目录]').replace(ex['canonical_root'], '[项目目录]')[:96] for k in _SUMMARY})
    for first in range(3):
        for second in range(first):
            if sum(objects[first]['data'][k].strip() != objects[second]['data'][k].strip() for k in ('goal', 'obstacle', 'action', 'choice', 'consequence')) < 2:
                _reject('STORY_EXCHANGE_RESULT_CONTENT_REJECTED')
    _stage(grant)
    return result, {'business_ready': True, 'source_promoted': False,
            'output_sha256': hashlib.sha256(raw).hexdigest(), 'output_path': ex['output_path'],
            'objects': summaries, 'request_id': ex['request_id'], 'request_sha256': ex['request_sha256'],
            'validation_scope': 'isolated_exchange'}


def output_result(grant):
    """Execution result summary: a currently valid execution grant is mandatory."""
    return _validated_result(grant)[1]


def read_result(grant):
    """Backend-only immutable result read; this never grants execution authority.

    The caller must establish completed task/registry eligibility and match the
    persisted output digest before serving the returned full envelope. Expiry
    does not erase a completed artifact, but every input/root/schema binding is
    still checked. No grant is modified or renewed.
    """
    return _validated_result(grant, allow_expired=True)[0]
