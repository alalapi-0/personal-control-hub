"""Trusted-local restoration of a complete, exclusively owned offline task snapshot."""
from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import sqlite3
import stat

from .service_contract import ServiceError
from .task_storage import StorageGuard, blocked, components
from .workbench_store import canonical, digest


def _identity(info):
    return tuple(getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mode', 'st_nlink',
                 'st_uid', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def _pin(path, expected, limit):
    if (type(expected) is not str or len(expected) != 64
            or any(c not in '0123456789abcdef' for c in expected)):
        raise blocked('RESTORE_PIN_REQUIRED')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600
                    or not 0 < info.st_size <= limit):
                raise blocked('RESTORE_FILE_REJECTED')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(limit + 1)
            after = os.fstat(fd)
            if (_identity(info) != _identity(after) or _identity(path.lstat()) != _identity(info)
                    or hashlib.sha256(raw).hexdigest() != expected):
                raise blocked('RESTORE_PIN_CHANGED')
            return raw, _identity(info)
        finally:
            os.close(fd)
    except OSError:
        raise blocked('RESTORE_FILE_REJECTED') from None


def _no_sidecars(store):
    if any(os.path.lexists(str(store.path) + suffix) for suffix in ('-journal', '-wal', '-shm')):
        raise blocked('RESTORE_SIDECAR_PRESENT')


def _snapshot(store, raw):
    from .task_store import SCHEMA, SCHEMA_VERSION, MAX_TASKS, MAX_EVENTS, TERMINAL, ORDINARY_DRAFT_TABLES, PLAN_DDL, PLAN_TABLES
    from .rr_feedback import RR_DDL, RR_TABLES, RRFeedbackError, validate_snapshot

    with contextlib.closing(sqlite3.connect(':memory:')) as db, \
            contextlib.closing(sqlite3.connect(':memory:')) as expected:
        db.deserialize(raw)
        db.execute('PRAGMA trusted_schema=OFF')
        expected.executescript(SCHEMA)
        optional=db.execute("SELECT name FROM sqlite_master WHERE type='table'AND name IN ('ordinary_drafts','ordinary_draft_requests')").fetchall()
        if optional:
            if len(optional)!=2:raise blocked('RESTORE_SCHEMA_UNSUPPORTED')
            for statement in ORDINARY_DRAFT_TABLES.values():expected.execute(statement)
        rr_optional=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'rr_%'").fetchall()
        if rr_optional:
            if {row[0] for row in rr_optional} != set(RR_TABLES):raise blocked('RESTORE_SCHEMA_UNSUPPORTED')
            for statement in RR_DDL:expected.execute(statement)
        plan_optional=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ("+','.join('?'for _ in PLAN_TABLES)+')',PLAN_TABLES).fetchall()
        if plan_optional:
            if {row[0]for row in plan_optional}!=set(PLAN_TABLES):raise blocked('RESTORE_SCHEMA_UNSUPPORTED')
            for statement in PLAN_DDL:expected.execute(statement)
        schema = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
        # Only the known schema is eligible, excluding triggers, views and custom DDL.
        if db.execute(schema).fetchall() != expected.execute(schema).fetchall():
            raise blocked('RESTORE_SCHEMA_UNSUPPORTED')
        if db.execute('SELECT version FROM meta').fetchall() != [(SCHEMA_VERSION,)]:
            raise blocked('RESTORE_SCHEMA_UNSUPPORTED')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():
            raise blocked('RESTORE_DATABASE_CORRUPT')
        db.row_factory = sqlite3.Row
        if optional:
            try:store._validate_ordinary_tables(db)
            except (ServiceError,ValueError,TypeError,KeyError):raise blocked('RESTORE_DATABASE_CORRUPT') from None
        profiles = db.execute('SELECT body,hash FROM storage_profile').fetchall()
        if len(profiles) != 1:
            raise blocked('PROFILE_REJECTED')
        profile = store._decode(*profiles[0])
        tasks = db.execute('SELECT * FROM tasks ORDER BY sequence').fetchall()
        events = db.execute('SELECT * FROM events ORDER BY sequence').fetchall()
        if len(tasks) > MAX_TASKS or len(events) > MAX_EVENTS:
            raise blocked('RESTORE_DATABASE_CORRUPT')
        for row in tasks:
            if row['status'] not in TERMINAL:
                raise blocked('RESTORE_TASK_NOT_TERMINAL')
            store._task(row)
        for row in events:
            store._decode(row['body'], row['hash'])
        for row in db.execute('SELECT * FROM grants'):
            body = store._decode(row['body'], row['hash'])
            if body.get('grant_id') != row['id'] or body.get('version') != row['version']:
                raise blocked('RESTORE_DATABASE_CORRUPT')
        tables=('meta','tasks','events','grants','sqlite_sequence')+tuple(ORDINARY_DRAFT_TABLES if optional else ())
        content = {table: [list(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                   for table in tables}
        if plan_optional:
            from .plan_admission import validate_offline_snapshot
            try:content.update(validate_offline_snapshot(store,db))
            except (ServiceError,ValueError,TypeError,KeyError):raise blocked('RESTORE_DATABASE_CORRUPT') from None
        if rr_optional:
            try:
                content.update(validate_snapshot(db))
            except RRFeedbackError:raise blocked('RESTORE_DATABASE_CORRUPT') from None
        # Also reject non-JSON SQLite values rather than alter their representation.
        canonical(content)
        return profile, content


def restore_storage(store, *, database_sha256, registration_sha256, confirm_offline=False):
    """Rebind only a stopped copied schema2 store; no task or grant mutation.

    The caller must establish exclusive offline ownership. A boolean, terminal
    rows or byte pins do not establish that boundary by themselves. Publication
    spans two files: after pending registration, interruption stays blocked and
    requires explicit recovery from the retained backup, never automatic adoption.
    """
    from .task_store import MAX_DATABASE_BYTES

    if confirm_offline is not True:
        raise blocked('RESTORE_OFFLINE_CONFIRMATION_REQUIRED')
    components(store.root)
    if not store._location():
        raise blocked('LEDGER_MISSING')
    _no_sidecars(store)
    raw, identity = _pin(store.path, database_sha256, MAX_DATABASE_BYTES)
    registration_raw, registration_identity = _pin(store.registration_path, registration_sha256, 16384)
    old = store._registration()
    if old is None or old['ledger_identity'] is None:
        raise blocked('RESTORE_REGISTRATION_INCOMPLETE')
    if store.storage_status()['state'] == 'ready':
        raise blocked('RESTORE_HEALTHY_STORE')
    temporary = store.path.with_name('.restore-tasks-' + secrets.token_hex(16))
    published = False
    try:
        profile, content = _snapshot(store, raw)
        if profile != old['profile'] or type(profile) is not dict or profile.get('version') != 2:
            raise blocked('RESTORE_SOURCE_PROFILE_MISMATCH')
        store.storage_guard = StorageGuard.register_internal(store.root)
        store.storage_guard.check()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        with contextlib.closing(sqlite3.connect(temporary)) as db:
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('UPDATE storage_profile SET body=?,hash=?',
                       (canonical(store.storage_guard.profile).decode(), digest(store.storage_guard.profile)))
            db.commit()
        staged = temporary.read_bytes()
        staged_profile, staged_content = _snapshot(store, staged)
        if staged_profile != store.storage_guard.profile or staged_content != content:
            raise blocked('RESTORE_CONTENT_CHANGED')
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        stage_identity = _identity(temporary.lstat())
        store.fault('before_restore_pending')
        store.storage_guard.check()
        store._location(); _no_sidecars(store)
        if (_pin(store.path, database_sha256, MAX_DATABASE_BYTES) != (raw, identity)
                or _pin(store.registration_path, registration_sha256, 16384) != (registration_raw, registration_identity)
                or _identity(temporary.lstat()) != stage_identity):
            raise blocked('RESTORE_PIN_CHANGED')
        pending = {'version': 1, 'profile': store.storage_guard.profile, 'ledger_identity': None}
        published = True
        store._publish_registration(pending, expected=old)
        pending_sha = hashlib.sha256(store.registration_path.read_bytes()).hexdigest()
        pending_raw, pending_identity = _pin(store.registration_path, pending_sha, 16384)
        store.fault('after_restore_pending')
        store.storage_guard.check()
        store._location(); _no_sidecars(store)
        if (_pin(store.path, database_sha256, MAX_DATABASE_BYTES) != (raw, identity)
                or _pin(store.registration_path, pending_sha, 16384) != (pending_raw, pending_identity)
                or _identity(temporary.lstat()) != stage_identity):
            raise blocked('RESTORE_PIN_CHANGED')
        os.replace(temporary, store.path)
        parent = os.open(store.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        store.fault('after_restore_database')
        info = store.path.lstat()
        ready = {**pending, 'ledger_identity': [info.st_dev, info.st_ino]}
        store.fault('before_restore_ready')
        store.storage_guard.check()
        store._location(); _no_sidecars(store)
        if (_pin(store.path, hashlib.sha256(staged).hexdigest(), MAX_DATABASE_BYTES) != (staged, _identity(info))
                or _pin(store.registration_path, pending_sha, 16384) != (pending_raw, pending_identity)):
            raise blocked('RESTORE_PIN_CHANGED')
        store._publish_registration(ready, expected=pending)
        store.fault('after_restore_ready')
        status = store.check_storage()
        if _snapshot(store, store.path.read_bytes())[1] != content:
            raise blocked('RESTORE_CONTENT_CHANGED')
        return {**status, 'business_sha256': digest(content),
                'row_counts': {table: len(rows) for table, rows in content.items()}}
    except ServiceError as error:
        if published:
            raise ServiceError(error.code, status=error.status, details=error.details, outcome='UNKNOWN') from None
        raise
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        raise ServiceError('TASK_STORAGE_BLOCKED', status=503,
                           details={'reason_code': 'RESTORE_DATABASE_CORRUPT'},
                           outcome='UNKNOWN' if published else 'NOT_COMMITTED') from None
    except OSError:
        raise ServiceError('TASK_RESTORE_UNCONFIRMED', status=503,
                           outcome='UNKNOWN' if published else 'NOT_COMMITTED') from None
    finally:
        if temporary.exists():
            temporary.unlink()
