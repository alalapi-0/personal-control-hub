"""Explicit owner capture policy and bounded Hub-owned image reads.

Inventory metadata grants no capture or filesystem authority. These helpers never
start a browser, perform network requests, discover roots, or write files.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from .connection_records import (
    FORBIDDEN_PARTS, SECRET, RecordError, credential_path_component, exact,
    fingerprint, identifier, require,
)

MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
MAX_POLICY_ENTRIES = 1000
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
SAFE_COMPONENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
POLICY_FIELDS = {'schema_version', 'project_id', 'allowed_urls', 'allowed_read_paths', 'max_redirects', 'retention'}


def _sensitive_component(value):
    folded = value.casefold()
    tokens = set(filter(None, re.split(r'[^a-z0-9]+', folded)))
    return (folded in FORBIDDEN_PARTS or folded.startswith('.env')
            or credential_path_component(value)
            or bool(tokens & {'session', 'sessions', 'sessionid', 'sid', 'csrf', 'csrftoken'}))


def validate_artifact_ref(value):
    """Validate a lexical image reference only; no file is inspected or granted."""
    require(type(value) is str and 0 < len(value) <= 1024, 'invalid preview artifact reference')
    require(not any(character in value for character in ('\\', '%', '\x00'))
            and not SECRET.search(value), 'unsafe preview artifact reference')
    parts = value.split('/')
    require(len(parts) >= 3 and parts[:2] == ['data', 'previews'], 'preview artifact outside allowed namespace')
    require(all(SAFE_COMPONENT.fullmatch(part) and not _sensitive_component(part) for part in parts),
            'unsafe preview artifact path')
    require(PurePosixPath(value).suffix.lower() in IMAGE_SUFFIXES, 'unsupported preview image suffix')
    return value


def _validate_url(value):
    require(type(value) is str and 0 < len(value) <= 2048, 'invalid capture URL')
    require(not any(ord(character) <= 32 or ord(character) == 127 for character in value)
            and not any(character in value for character in ('\\', '%'))
            and not SECRET.search(value), 'unsafe capture URL')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise RecordError('invalid capture URL') from None
    require(parsed.scheme in ('http', 'https') and parsed.hostname in ('127.0.0.1', '::1')
            and port is not None and 1 <= port <= 65535
            and parsed.username is None and parsed.password is None, 'capture URL must be explicit loopback')
    # Canonical host spelling prevents parser/browser disagreements; preserve the
    # complete literal URL for the actual exact allowlist comparison.
    host = '[::1]' if parsed.hostname == '::1' else '127.0.0.1'
    require(parsed.netloc == host + ':' + str(port), 'invalid capture URL authority')
    require(not parsed.path or parsed.path.startswith('/'), 'invalid capture URL path')
    for part in parsed.path.split('/'):
        require(part not in ('.', '..') and not _sensitive_component(part), 'unsafe capture URL path')
    require(bool(re.fullmatch(r'[A-Za-z0-9_./~!$&()*+,;=:@-]*', parsed.path)), 'invalid capture URL path')
    # No opaque query payloads: only bounded plain key/value pairs are admitted.
    if parsed.query:
        for pair in parsed.query.split('&'):
            pieces = pair.split('=')
            require(len(pieces) <= 2 and bool(re.fullmatch(r'[A-Za-z0-9_.-]+', pieces[0]))
                    and not _sensitive_component(pieces[0]), 'unsafe capture URL query')
            require(all(bool(re.fullmatch(r'[A-Za-z0-9_./~-]*', item)) and not _sensitive_component(item)
                        for item in pieces[1:]), 'unsafe capture URL query')
    if parsed.fragment:
        require(bool(re.fullmatch(r'!?/?[A-Za-z0-9_./~-]+', parsed.fragment))
                and '//' not in parsed.fragment, 'unsafe capture URL fragment')
        require(all(part not in ('.', '..') and not _sensitive_component(part)
                    for part in parsed.fragment.lstrip('!').split('/')), 'unsafe capture URL fragment')
    return value


def validate_capture_policy(value):
    """Validate trusted owner input; source inventories must never supply policy."""
    exact(value, POLICY_FIELDS, 'capture policy')
    require(value['schema_version'] == '1.0', 'unsupported capture policy')
    identifier(value['project_id'], 'capture project')
    require(type(value['max_redirects']) is int and value['max_redirects'] == 0,
            'capture redirects are disabled')
    require(value['retention'] == 'local_only', 'preview retention must be local')
    for name, validator in (('allowed_urls', _validate_url), ('allowed_read_paths', validate_artifact_ref)):
        items = value[name]
        require(type(items) is list and len(items) <= MAX_POLICY_ENTRIES, 'invalid capture allowlist')
        for item in items:
            validator(item)
        require(len(items) == len(set(items)), 'duplicate capture allowlist entry')
    return copy.deepcopy(value)


def authorize_capture_url(policy, *, project_id, url, redirect_chain=()):
    """Return an exact permitted URL, without resolving or requesting it."""
    checked = validate_capture_policy(policy)
    identifier(project_id, 'capture project')
    require(project_id == checked['project_id'], 'capture project mismatch')
    require(type(redirect_chain) in (list, tuple) and len(redirect_chain) == 0, 'capture redirects are disabled')
    _validate_url(url)
    require(url in checked['allowed_urls'], 'capture URL not permitted')
    return url


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_preview_artifact(root, ref, *, expected_sha256, max_bytes=MAX_ARTIFACT_BYTES):
    """Read one explicitly rooted image with no-follow and stable name bindings."""
    validate_artifact_ref(ref)
    fingerprint(expected_sha256, 'preview artifact')
    require(type(max_bytes) is int and 0 < max_bytes <= MAX_ARTIFACT_BYTES, 'invalid preview read budget')
    require(isinstance(root, (str, Path)), 'explicit Hub root required')
    root = Path(root)
    require(root.is_absolute() and '..' not in root.parts, 'explicit absolute Hub root required')
    opened = []
    file_descriptor = None
    try:
        directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened.append(directory)
        directories = (*root.parts[1:], *PurePosixPath(ref).parts[:-1])
        for part in directories:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            opened.append(directory)
        file_descriptor = os.open(PurePosixPath(ref).name,
                                  os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        before = os.fstat(file_descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'unsafe preview image file')
        require(before.st_size <= max_bytes, 'preview image exceeds read budget')
        with os.fdopen(file_descriptor, 'rb') as stream:
            file_descriptor = None
            data = stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
        require(len(data) <= max_bytes and _identity(before) == _identity(after), 'preview image changed during read')
        for index, part in enumerate(directories):
            current = os.stat(part, dir_fd=opened[index], follow_symlinks=False)
            pinned = os.fstat(opened[index + 1])
            require(stat.S_ISDIR(current.st_mode) and (current.st_dev, current.st_ino) == (pinned.st_dev, pinned.st_ino),
                    'preview parent binding changed during read')
        current = os.stat(PurePosixPath(ref).name, dir_fd=directory, follow_symlinks=False)
        require(_identity(current) == _identity(after), 'preview path binding changed during read')
        require(hashlib.sha256(data).hexdigest() == expected_sha256, 'preview image hash mismatch')
        return data
    except (OSError, ValueError, TypeError):
        raise RecordError('preview artifact read rejected') from None
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for descriptor in reversed(opened):
            os.close(descriptor)
