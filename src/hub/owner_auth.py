"""Single-owner proof from an environment reference or explicit OS projection."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import stat
import threading
import time

from .service_contract import ServiceError

OWNER_CREDENTIAL_NAME = 'pch-owner-token'
MAX_PROJECTED_BYTES = 2048


def valid_projected_proof(value):
    return isinstance(value, str) and 32 <= len(value) <= 512 and value.isprintable()


@contextmanager
def credential_directory(path, *, create=False):
    """Walk without following aliases; callers validate their final directory."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Credential directory rejected')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptors, bindings = [], []
    try:
        descriptors.append(os.open(path.anchor, flags))
        for part in path.parts[1:]:
            parent = descriptors[-1]
            try:
                descriptor = os.open(part, flags, dir_fd=parent)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=parent)
                descriptor = os.open(part, flags, dir_fd=parent)
            descriptors.append(descriptor)
            bindings.append((parent, part, descriptor))
        def check():
            for parent, part, descriptor in bindings:
                bound = os.stat(part, dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISDIR(bound.st_mode) or not os.path.samestat(bound, os.fstat(descriptor)):
                    raise ValueError('Credential directory changed')
        check()
        yield descriptors[-1], check
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def private_directory(metadata):
    return (stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == os.geteuid()
            and metadata.st_mode & 0o077 == 0)


def _file_identity(metadata):
    return tuple(getattr(metadata, key) for key in (
        'st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_nlink', 'st_size',
        'st_mtime_ns', 'st_ctime_ns'))


class SystemdCredentialProvider:
    """Read only systemd's fixed-name runtime projection; never use env fallback.

    The service must explicitly select this provider. It does not register,
    decrypt, initialize, or persist a credential, and never caches plaintext.
    """
    def __call__(self):
        directory = os.environ.get('CREDENTIALS_DIRECTORY')
        if not directory:
            return None
        try:
            with credential_directory(directory) as (parent, check):
                if not private_directory(os.fstat(parent)):
                    return None
                descriptor = os.open(OWNER_CREDENTIAL_NAME,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                    dir_fd=parent)
                try:
                    before = os.fstat(descriptor)
                    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                            or before.st_uid != os.geteuid() or before.st_mode & 0o077
                            or not 1 <= before.st_size <= MAX_PROJECTED_BYTES):
                        return None
                    raw = b''
                    while len(raw) <= MAX_PROJECTED_BYTES:
                        part = os.read(descriptor, MAX_PROJECTED_BYTES + 1 - len(raw))
                        if not part:
                            break
                        raw += part
                    after = os.fstat(descriptor)
                    bound = os.stat(OWNER_CREDENTIAL_NAME, dir_fd=parent, follow_symlinks=False)
                    check()
                    if (not private_directory(os.fstat(parent)) or len(raw) != before.st_size
                            or _file_identity(before) != _file_identity(after)
                            or _file_identity(before) != _file_identity(bound)):
                        return None
                    value = raw.decode('utf-8')
                    return value if valid_projected_proof(value) else None
                finally:
                    os.close(descriptor)
        except (OSError, ValueError, UnicodeError):
            return None


class OwnerAuth:
    reference = 'HUB_OWNER_TOKEN'

    def __init__(self, *, provider=None, clock=time.monotonic):
        # An injected provider belongs only to trusted server code/isolated tests.
        self.provider = provider or (lambda: os.environ.get(self.reference))
        self.clock = clock
        self.attempts = deque(maxlen=8)
        self.lock = threading.Lock()

    def _credential(self):
        try:
            value = self.provider()
            if not isinstance(value, str) or not 32 <= len(value) <= 512:
                return None
            return value.encode('utf-8')
        except Exception:
            return None

    @property
    def configured(self):
        return self._credential() is not None

    @property
    def version(self):
        secret = self._credential()
        return hashlib.sha256(secret).digest() if secret is not None else None

    def verify(self, proof):
        secret = self._credential()
        if secret is None:
            raise ServiceError('OWNER_AUTH_UNAVAILABLE', status=503)
        with self.lock:
            now = self.clock()
            while self.attempts and self.attempts[0] <= now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 8:
                raise ServiceError('OWNER_AUTH_RATE_LIMITED', status=429)
            self.attempts.append(now)
        try:
            encoded = proof.encode('utf-8') if isinstance(proof, str) and 32 <= len(proof) <= 512 else None
        except UnicodeError:
            encoded = None
        if encoded is None or not hmac.compare_digest(hashlib.sha256(secret).digest(), hashlib.sha256(encoded).digest()):
            raise ServiceError('OWNER_AUTH_FAILED', status=401)
        return hashlib.sha256(secret).digest()
