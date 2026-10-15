"""Explicit local metadata registration in the sole PCH State; no auto-adoption."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

from .connections import parse_owned_hub_state
from .service_contract import ServiceError

TASK = 'PCH-REFACTOR-20261014'
FIELDS = {'schema_version', 'task_id', 'purpose', 'root', 'platform', 'config_sha256',
          'cli_version', 'binary_realpath', 'binary_sha256', 'mcp_servers'}
SHA = re.compile(r'^[a-f0-9]{64}$')


@dataclass(frozen=True)
class NativeMetadataRegistration:
    root: Path
    config_sha256: str
    cli_version: str
    binary_realpath: str
    binary_sha256: str
    mcp_servers: tuple[str, ...]
    semantic_sha256: str


def load_registration(root):
    """Root-authored registration only. Missing/changed inputs never enroll themselves."""
    from .codex_adapter import VERSION, REGISTERED_SERVERS, child_environment, config_fingerprint
    try:
        root = Path(root)
        if not root.is_absolute() or '..' in root.parts or root.resolve() != root:
            raise ValueError('root')
        current = Path(root.anchor)
        for part in root.parts[1:]:
            current /= part
            if current.is_symlink():
                raise ValueError('alias')
        path = root / 'STATE.yaml'
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.getuid():
                raise ValueError('file identity')
            raw = stream.read(1048577)
            after = os.fstat(stream.fileno())
        if len(raw) > 1048576 or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('changed')
        task = parse_owned_hub_state(raw)['pch_refactor']
        value = task['r51_implementation']['native_metadata_registration']
        if task['task_id'] != TASK or type(value) is not dict or set(value) != FIELDS:
            raise ValueError('shape')
        if sys.platform != 'linux' or (value['schema_version'], value['task_id'], value['purpose'], value['root'], value['platform']) != (
                '1.0', TASK, 'metadata_discovery', str(root), 'linux'):
            raise ValueError('scope')
        if value['cli_version'] != VERSION or value['mcp_servers'] != sorted(REGISTERED_SERVERS):
            raise ValueError('capability registration')
        if any(type(value[k]) is not str or not SHA.fullmatch(value[k]) for k in ('config_sha256', 'binary_sha256')):
            raise ValueError('pin')
        env = child_environment()
        binary = shutil.which('codex', path=env['PATH'])
        if binary is None or str(Path(binary).resolve()) != value['binary_realpath']:
            raise ValueError('binary selector')
        if hashlib.sha256(Path(binary).read_bytes()).hexdigest() != value['binary_sha256']:
            raise ValueError('binary changed')
        if config_fingerprint(Path(env['HOME']) / '.codex/config.toml') != value['config_sha256']:
            raise ServiceError('CODEX_CONFIG_DRIFT', status=503)
        digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return NativeMetadataRegistration(root, value['config_sha256'], value['cli_version'],
                                          value['binary_realpath'], value['binary_sha256'],
                                          tuple(value['mcp_servers']), digest)
    except ServiceError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise ServiceError('NATIVE_REGISTRATION_REJECTED', status=503) from None
