"""Read explicitly selected metadata files; never discover credentials or payloads."""
from __future__ import annotations

import hashlib
import os
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from hub.connection_records import RecordError, require
from hub.connection_sources import _root_path_allowed
from hub.connections import parse_source

MAX_METADATA_BYTES = 8 * 1024 * 1024
PROTECTED = {".git", ".ssh", ".codex", ".cursor", "credentials", "secrets", "cookies", "node_modules", ".venv", "venv"}


def metadata_path(root, relative):
    path = PurePosixPath(relative)
    require(not path.is_absolute() and path.parts and ".." not in path.parts, "metadata path must be relative")
    require(not any(p.lower() in PROTECTED or p.lower().startswith(".env") for p in path.parts), "protected metadata path")
    require(not any(word in path.name.lower() for word in ("credential", "secret", "cookie", "token", "auth.json")), "protected metadata filename")
    root = Path(root)
    _root_path_allowed(root)
    base = root.resolve(strict=True)
    _root_path_allowed(base)
    cursor = base
    for part in path.parts:
        cursor /= part
        require(not cursor.is_symlink(), "metadata symlink not permitted")
    return cursor


def read_metadata(root, relative):
    path = metadata_path(root, relative)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= MAX_METADATA_BYTES,
                "metadata must be an ordinary bounded file")
        data = handle.read(MAX_METADATA_BYTES + 1)
        after = os.fstat(handle.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ino) == (after.st_size, after.st_mtime_ns, after.st_ino)
                and len(data) <= MAX_METADATA_BYTES, "metadata changed during read")
    return data, {"sha256": hashlib.sha256(data).hexdigest(),
                  "modified_at": datetime.fromtimestamp(after.st_mtime, timezone.utc).isoformat()}


def read_json(root, relative):
    data, metadata = read_metadata(root, relative)
    return parse_source(data, "json"), metadata


def read_structured(root, relative):
    data, metadata = read_metadata(root, relative)
    suffix = Path(relative).suffix
    require(suffix in {".json", ".yaml", ".yml"}, "structured metadata format required")
    return parse_source(data, "json" if suffix == ".json" else "yaml"), metadata
