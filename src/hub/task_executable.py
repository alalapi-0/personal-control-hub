"""An explicit Root-owned executable identity, never discovered from a request."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat

from .service_contract import ServiceError

MAX_EXECUTABLE_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True)
class TaskExecutable:
    path: Path
    sha256: str
    device: int
    inode: int
    size: int
    task_id: str
    candidate: str

    def __post_init__(self):
        if not isinstance(self.path, (str, Path)):
            raise ServiceError('CODEX_EXECUTABLE_REJECTED', status=503)
        path = Path(self.path)
        if (not path.is_absolute() or '..' in path.parts
                or any(type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value)
                       for value in (self.sha256, self.candidate))
                or type(self.task_id) is not str
                or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', self.task_id)
                or any(type(value) is not int or value < 0 for value in (self.device, self.inode))
                or type(self.size) is not int or not 4 <= self.size <= MAX_EXECUTABLE_BYTES):
            raise ServiceError('CODEX_EXECUTABLE_REJECTED', status=503)
        object.__setattr__(self, 'path', path)

    def check(self):
        """Recheck content and bindings; this is not kernel-bound inode execution."""
        descriptors = []
        file_descriptor = None
        try:
            descriptors.append(os.open(self.path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            parts = self.path.parts[1:-1]
            for part in parts:
                descriptors.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                           dir_fd=descriptors[-1]))
            file_descriptor = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                      dir_fd=descriptors[-1])
            before = os.fstat(file_descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != os.getuid() or not before.st_mode & stat.S_IXUSR
                    or before.st_mode & (0o022 | stat.S_ISUID | stat.S_ISGID)):
                raise ServiceError('CODEX_EXECUTABLE_REJECTED', status=503)
            if (before.st_dev, before.st_ino, before.st_size) != (self.device, self.inode, self.size):
                raise ServiceError('CODEX_EXECUTABLE_DRIFT', status=503)
            hashed = hashlib.sha256()
            size = 0
            magic = b''
            while chunk := os.read(file_descriptor, 65536):
                if not magic:
                    magic = chunk[:4]
                size += len(chunk)
                if size > self.size:
                    raise ServiceError('CODEX_EXECUTABLE_DRIFT', status=503)
                hashed.update(chunk)
            after = os.fstat(file_descriptor)
            def fingerprint(info):
                return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
                        info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            current = os.stat(self.path.name, dir_fd=descriptors[-1], follow_symlinks=False)
            if (fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(current)
                    or size != self.size or hashed.hexdigest() != self.sha256):
                raise ServiceError('CODEX_EXECUTABLE_DRIFT', status=503)
            if magic != b'\x7fELF':
                raise ServiceError('CODEX_EXECUTABLE_REJECTED', status=503)
            for index, part in enumerate(parts):
                named = os.stat(part, dir_fd=descriptors[index], follow_symlinks=False)
                pinned = os.fstat(descriptors[index + 1])
                if not stat.S_ISDIR(named.st_mode) or (named.st_dev, named.st_ino) != (pinned.st_dev, pinned.st_ino):
                    raise ServiceError('CODEX_EXECUTABLE_DRIFT', status=503)
            return self.path
        except OSError:
            raise ServiceError('CODEX_EXECUTABLE_REJECTED', status=503) from None
        finally:
            if file_descriptor is not None:
                os.close(file_descriptor)
            for descriptor in reversed(descriptors):
                os.close(descriptor)
