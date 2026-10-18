"""Durable, revocable Owner sessions; only token digests are stored locally."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat

from .service_contract import ServiceError


class OwnerSessionStore:
    MAX_BYTES = 16384

    def __init__(self, root, *, capacity=32):
        self.root = Path(root).absolute()
        if (not self.root.is_dir() or '..' in self.root.parts
                or any(p.is_symlink() for p in (self.root, *self.root.parents))
                or type(capacity) is not int or not 1 <= capacity <= 32):
            raise ServiceError('OWNER_SESSION_STORE_UNAVAILABLE', status=503)
        self.capacity = capacity
        st = self.root.stat()
        self.root_identity = (st.st_dev, st.st_ino)
        self.namespace = hashlib.sha256(str(self.root).encode()).hexdigest()

    def cookie_name(self, scope):
        return 'hub_' + hashlib.sha256((self.namespace + scope).encode()).hexdigest()[:24]

    def _digest(self, session, scope):
        if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', session):
            return None
        return hashlib.sha256((self.namespace + '\0' + scope + '\0' + session).encode()).hexdigest()

    @staticmethod
    def _regular(fd):
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or st.st_nlink != 1 or st.st_mode & 0o077):
            raise OSError('Unsafe private session file')
        return st

    @contextmanager
    def _locked(self, create=False):
        opened = []
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            rootfd = os.open(self.root, flags); opened.append(rootfd)
            st = os.fstat(rootfd)
            if (st.st_dev, st.st_ino) != self.root_identity:
                raise OSError('Session root changed')
            parent = rootfd
            for name in ('data', 'owner-auth'):
                if create:
                    try:
                        os.mkdir(name, 0o700, dir_fd=parent)
                    except FileExistsError:
                        pass
                try:
                    child = os.open(name, flags, dir_fd=parent)
                except FileNotFoundError:
                    if not create:
                        yield None
                        return
                    raise
                opened.append(child); parent = child
                if name == 'owner-auth':
                    st = os.fstat(child)
                    if st.st_uid != os.getuid() or st.st_mode & 0o077:
                        raise OSError('Unsafe session directory')
            lockflags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            if create:
                lockflags |= os.O_CREAT
            lock = os.open('sessions.lock', lockflags, 0o600, dir_fd=parent)
            opened.append(lock); self._regular(lock)
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield parent
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            raise ServiceError('OWNER_SESSION_STORE_UNAVAILABLE', status=503) from None
        finally:
            for fd in reversed(opened):
                os.close(fd)

    def _read(self, directory):
        try:
            fd = os.open('sessions.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                         dir_fd=directory)
        except FileNotFoundError:
            return {'version': 1, 'credential_version': None, 'sessions': []}
        try:
            before = self._regular(fd)
            if before.st_size > self.MAX_BYTES:
                raise ValueError('Session record too large')
            data = os.read(fd, self.MAX_BYTES + 1)
            after = os.fstat(fd)
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise OSError('Session record changed')
        finally:
            os.close(fd)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate session field')
                result[key] = value
            return result
        record = json.loads(data, object_pairs_hook=unique)
        if (not isinstance(record, dict) or set(record) != {'version', 'credential_version', 'sessions'}
                or type(record['version']) is not int or record['version'] != 1
                or not isinstance(record['sessions'], list)
                or len(record['sessions']) > 32
                or not isinstance(record['credential_version'], str)
                or not re.fullmatch('[a-f0-9]{64}', record['credential_version'])
                or any(not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v)
                       for v in record['sessions'])
                or len(set(record['sessions'])) != len(record['sessions'])):
            raise ValueError('Invalid session record')
        return record

    def _write(self, directory, record):
        data = (json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n').encode()
        if len(data) > self.MAX_BYTES:
            raise ValueError('Session record too large')
        temporary = '.session-' + secrets.token_hex(12)
        fd = None
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | os.O_CLOEXEC, 0o600, dir_fd=directory)
            with os.fdopen(fd, 'wb') as target:
                fd = None
                target.write(data); target.flush(); os.fsync(target.fileno())
            os.replace(temporary, 'sessions.json', src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def issue(self, session, scope, credential_version):
        digest = self._digest(session, scope)
        if digest is None or not isinstance(credential_version, bytes) or len(credential_version) != 32:
            raise ServiceError('OWNER_SESSION_STORE_UNAVAILABLE', status=503)
        with self._locked(create=True) as directory:
            record = self._read(directory)
            version = credential_version.hex()
            if record['credential_version'] != version:
                record = {'version': 1, 'credential_version': version, 'sessions': []}
            if len(record['sessions']) >= self.capacity:
                raise ServiceError('OWNER_SESSION_CAPACITY_REACHED', status=409)
            record['sessions'].append(digest)
            self._write(directory, record)

    def validate(self, session, scope, credential_version):
        digest = self._digest(session, scope)
        if digest is None or not isinstance(credential_version, bytes) or len(credential_version) != 32:
            return False
        with self._locked() as directory:
            if directory is None:
                return False
            record = self._read(directory)
            version = credential_version.hex()
            if record['credential_version'] != version:
                self._write(directory, {'version': 1, 'credential_version': version, 'sessions': []})
                return False
            return digest in record['sessions']

    def revoke(self, session, scope):
        digest = self._digest(session, scope)
        if digest is None:
            return
        with self._locked() as directory:
            if directory is None:
                return
            record = self._read(directory)
            if digest in record['sessions']:
                record['sessions'].remove(digest)
                self._write(directory, record)
