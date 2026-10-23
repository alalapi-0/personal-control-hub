"""Hub task intents and evidence. SQLite never confers execution authority."""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import stat
import time
from pathlib import Path

from .service_contract import ServiceError
from .workbench_store import canonical, digest
from .design_records import ID_RE
from .task_storage import StorageGuard, blocked, components
from .task_approvals import approval_policy

SCHEMA_VERSION = 2
MAX_TASKS = 128
MAX_EVENTS = 2048
MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_ORDINARY_DRAFT_BYTES = 8192
MAX_ORDINARY_DRAFTS = 256
MAX_ORDINARY_DRAFT_REQUESTS = 1024
ORDINARY_DRAFT_TABLES = {
    'ordinary_drafts': 'CREATE TABLE ordinary_drafts(owner TEXT NOT NULL, project TEXT NOT NULL, parent TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(owner,project,parent))',
    'ordinary_draft_requests': 'CREATE TABLE ordinary_draft_requests(owner TEXT NOT NULL, id TEXT NOT NULL, command_hash TEXT NOT NULL, body TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(owner,id))',
}
TERMINAL = {'checks_complete', 'failed', 'cancelled', 'lost', 'requires_reconcile'}
STATES = TERMINAL | {'queued', 'running', 'waiting_input', 'waiting_approval', 'validating'}
SCHEMA = '''
CREATE TABLE meta(version INTEGER NOT NULL CHECK(version=2));
INSERT INTO meta VALUES(2);
CREATE TABLE storage_profile(body TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE grants(id TEXT PRIMARY KEY, version INTEGER NOT NULL, body TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE tasks(sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
 payload TEXT NOT NULL, payload_hash TEXT NOT NULL, intent TEXT NOT NULL, intent_hash TEXT NOT NULL,
 project TEXT NOT NULL, parent TEXT, status TEXT NOT NULL, owner TEXT, lease_until REAL,
 thread_id TEXT, turn_id TEXT, provider_status TEXT, cancel INTEGER NOT NULL DEFAULT 0,
 result TEXT, created REAL NOT NULL);
CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id TEXT NOT NULL REFERENCES tasks(id), event_key TEXT NOT NULL, body TEXT NOT NULL,
 hash TEXT NOT NULL, received REAL NOT NULL, UNIQUE(task_id,event_key));
'''


PLAN_TABLES = ('plan_schema','plan_inputs','plan_receipts','plan_conflicts','plan_archive_tasks','plan_archive_events')
PLAN_DDL = (
    'CREATE TABLE plan_schema(version INTEGER NOT NULL CHECK(version=1))',
    'INSERT INTO plan_schema VALUES(1)',
    'CREATE TABLE plan_inputs(id TEXT PRIMARY KEY,body TEXT NOT NULL,hash TEXT NOT NULL)',
    'CREATE TABLE plan_receipts(plan_id TEXT NOT NULL,task_id TEXT NOT NULL,body TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(plan_id,task_id))',
    'CREATE TABLE plan_conflicts(plan_id TEXT NOT NULL,task_id TEXT NOT NULL,body TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(plan_id,task_id,hash))',
    'CREATE TABLE plan_archive_tasks AS SELECT * FROM tasks WHERE 0',
    'CREATE UNIQUE INDEX plan_archive_task_id ON plan_archive_tasks(id)',
    'CREATE TABLE plan_archive_events AS SELECT * FROM events WHERE 0',
    'CREATE UNIQUE INDEX plan_archive_event_sequence ON plan_archive_events(sequence)',
    'CREATE UNIQUE INDEX plan_archive_event_id ON plan_archive_events(task_id,event_key)')


def stable_id(value):
    if not isinstance(value, str) or not (ID_RE.fullmatch(value) or re.fullmatch(r'P[0-9]+-[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*',value)):
        raise ServiceError('INVALID_TASK_ID')
    return value


def parent_binding_matches(intent, parent):
    binding = intent.get('parent_binding')
    return (parent is not None and type(binding) is dict and set(binding) == {'thread_id', 'turn_id'}
            and all(type(binding[key]) is str and binding[key] for key in ('thread_id', 'turn_id'))
            and binding == {'thread_id': parent['thread_id'], 'turn_id': parent['turn_id']})


def _local_kind(task):
    grant=task['intent']['grant']
    return 'css'if 'css_trial'in grant else'story'if grant.get('kind')=='story_exchange'else None


def _local_ready(task):
    kind=_local_kind(task)
    return bool(kind and task['result']and task['result'].get('trial_ready'if kind=='css'else'business_ready'))


def _effect_root(grant):
    return grant.get('story_exchange',{}).get('canonical_root',grant['root'])


class TaskStore:
    def __init__(self, root, *, fault=None, storage_guard=None):
        self.root = Path(root).absolute()
        self.path = self.root / 'data/workbench/tasks.sqlite3'
        # This server-owned record is independent of SQLite. Ordinary startup
        # never creates either file, even when an entire storage tree is gone.
        self.registration_path = self.path.with_name('storage-registration.json')
        self.fault = fault or (lambda _: None)
        self.storage_guard = storage_guard

    def check_storage(self,*,readonly=False):
        return self._check_storage(readonly=readonly)

    def _check_storage(self,*,readonly=False,connection=None):
        # Only _connection supplies its held transaction here. Public storage
        # checks still open an independent read-only profile connection.
        exists = self._location()
        components(self.root)
        registration = self._registration()
        if registration is None:
            raise blocked('REGISTRATION_MISSING')
        profile = registration['profile']
        if self.storage_guard is None:self.storage_guard=StorageGuard(self.root,profile)
        if self.storage_guard.profile!=profile:raise blocked('PROFILE_VERSION_STALE')
        result = self.storage_guard.check_identity()
        if registration['ledger_identity'] is None:raise blocked('REGISTRATION_INCOMPLETE')
        if not exists:raise blocked('LEDGER_MISSING')
        info=self.path.lstat()
        if [info.st_dev,info.st_ino]!=registration['ledger_identity']:
            raise blocked('LEDGER_IDENTITY_CHANGED')
        ledger_profile=self._ledger_profile(allow_legacy=readonly,connection=connection)
        if ledger_profile is not None and ledger_profile!=profile:raise blocked('PROFILE_VERSION_STALE')
        return result if readonly else self.storage_guard.check_space()

    def _ledger_profile(self,*,allow_legacy=False,connection=None):
        def read_profile(db):
            # The held writer uses sqlite3.Row; standalone readers use tuples.
            versions=[tuple(row) for row in db.execute('SELECT version FROM meta')]
            if versions==[(1,)]:
                if allow_legacy:return None
                raise blocked('PROFILE_REGISTRATION_REQUIRED')
            if versions!=[(SCHEMA_VERSION,)]:raise ServiceError('TASK_SCHEMA_UNSUPPORTED',status=503)
            rows=db.execute('SELECT body,hash FROM storage_profile').fetchall()
            if len(rows)!=1:raise blocked('PROFILE_REJECTED')
            return self._decode(*rows[0])
        try:
            if connection is not None:
                # An independent reader can deadlock on this writer's spilled
                # pages. The held connection also sees uncommitted profile drift.
                return read_profile(connection)
            with contextlib.closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as reader:
                return read_profile(reader)
        except sqlite3.Error:raise ServiceError('TASK_STORE_CORRUPT',status=503)from None

    def _registration(self):
        try:
            fd=os.open(self.registration_path,os.O_RDONLY|os.O_NOFOLLOW)
        except FileNotFoundError:return None
        except OSError:raise blocked('REGISTRATION_REJECTED')from None
        try:
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid()
                    or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>16384):
                raise blocked('REGISTRATION_REJECTED')
            try:
                with os.fdopen(fd,'rb',closefd=False)as stream:raw=stream.read(16385)
            except OSError:raise blocked('REGISTRATION_REJECTED')from None
            try:
                wrapper=json.loads(raw);record=wrapper['record']
                identity=record['ledger_identity']
                if (set(wrapper)!={'record','hash'} or wrapper['hash']!=digest(record) or canonical(wrapper)!=raw
                        or type(record)is not dict or set(record)!={'version','profile','ledger_identity'}
                        or type(record['version'])is not int or record['version']!=1
                        or (identity is not None and (type(identity)is not list or len(identity)!=2
                            or any(type(v)is not int or v<0 for v in identity)))):
                    raise ValueError()
                return record
            except (ValueError,TypeError,KeyError,UnicodeError):raise blocked('REGISTRATION_REJECTED')from None
        finally:os.close(fd)

    def _publish_registration(self,record,*,expected=None):
        self.storage_guard.check()
        self._location()
        temporary=self.registration_path.with_name('.registration-'+secrets.token_hex(16))
        body=canonical({'record':record,'hash':digest(record)})
        if len(body)>16384:raise blocked('REGISTRATION_REJECTED')
        try:
            fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb')as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
            self.storage_guard.check()
            if expected is None:
                try:os.link(temporary,self.registration_path)
                except FileExistsError:raise blocked('REGISTRATION_ALREADY_EXISTS')from None
            else:
                if self._registration()!=expected:raise blocked('REGISTRATION_REJECTED')
                os.replace(temporary,self.registration_path)
            parent=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(parent)
            finally:os.close(parent)
        finally:
            if temporary.exists():temporary.unlink()

    def register_storage(self):
        """Explicit trusted local initialization, never called by HTTP/startup/grants.

        An interrupted registration remains blocked and needs owner reconciliation.
        It is never automatically retried, adopted or repaired by a fresh process.
        """
        if self._location() or self._registration() is not None:raise blocked('REGISTRATION_ALREADY_EXISTS')
        if self.storage_guard is None:self.storage_guard=StorageGuard.register_internal(self.root)
        self.storage_guard.check()
        self._location(create=True)
        pending={'version':1,'profile':self.storage_guard.profile,'ledger_identity':None}
        self._publish_registration(pending)
        self.fault('after_storage_registration')
        self._initialize(pending)
        info=self.path.lstat()
        ready={**pending,'ledger_identity':[info.st_dev,info.st_ino]}
        self.fault('before_storage_registration_ready')
        self._publish_registration(ready,expected=pending)
        return self.check_storage()

    def storage_status(self):
        try:return self.check_storage()
        except ServiceError as error:
            return {'state':'blocked','profile_version':None,'reason_code':error.details.get('reason_code',error.code)}

    def restore_storage(self, *, database_sha256, registration_sha256, confirm_offline=False):
        """Explicit owner-only rebind of a pinned offline copy; never a startup repair."""
        from .task_storage_restore import restore_storage
        return restore_storage(self, database_sha256=database_sha256,
                               registration_sha256=registration_sha256,
                               confirm_offline=confirm_offline)

    def _location(self, create=False):
        current = Path(self.root.anchor)
        for part in self.path.parent.parts[1:]:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if not create: return False
                current.mkdir(mode=0o700)
                info = current.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ServiceError('TASK_LOCATION_REJECTED', status=503)
        for suffix in ('', '-journal', '-wal', '-shm'):
            target = Path(str(self.path) + suffix)
            try: info = target.lstat()
            except FileNotFoundError: continue
            # SQLite can unlink a sidecar after pathname lookup but before
            # getattr, exposing its detached (zero-link) inode. Reobserve that
            # narrow race once; the ledger and every unsafe file still fail.
            if suffix and stat.S_ISREG(info.st_mode) and info.st_nlink == 0 and info.st_size <= MAX_DATABASE_BYTES:
                try: info = target.lstat()
                except FileNotFoundError: continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_DATABASE_BYTES:
                raise ServiceError('TASK_LOCATION_REJECTED', status=503)
        return self.path.exists()

    def _initialize(self,pending):
        if self._location() or self._registration()!=pending:raise blocked('REGISTRATION_REJECTED')
        self.storage_guard.check()
        temporary = self.path.with_name('.tasks-' + secrets.token_hex(16))
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            connection = sqlite3.connect(temporary)
            try:
                connection.execute('PRAGMA synchronous=FULL')
                connection.executescript(SCHEMA)
                connection.execute('INSERT INTO storage_profile VALUES(?,?)',
                    (canonical(self.storage_guard.profile).decode(),digest(self.storage_guard.profile)))
                self.storage_guard.check()
                connection.commit()
            finally: connection.close()
            with temporary.open('rb') as stream: os.fsync(stream.fileno())
            self.storage_guard.check()
            if self._location() or self._registration()!=pending:raise blocked('REGISTRATION_REJECTED')
            # Atomic no-clobber publication; any competing ledger blocks setup.
            try: os.link(temporary, self.path)
            except FileExistsError:raise blocked('LEDGER_IDENTITY_CHANGED')from None
            finally: temporary.unlink()
            parent = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(parent)
            finally: os.close(parent)
        finally:
            if temporary.exists(): temporary.unlink()

    @contextlib.contextmanager
    def _connection(self, write=False, guard=None):
        connection = None
        try:
            if write:
                self.check_storage()
            else:
                self.check_storage(readonly=True)
            if not self._location():
                raise blocked('LEDGER_MISSING')
            connection = sqlite3.connect(self.path.as_uri() + '?mode=rw' if write else self.path.as_uri() + '?mode=ro', uri=True, timeout=.5)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA synchronous=FULL')
            row = connection.execute('SELECT version FROM meta').fetchall()
            if len(row) != 1 or row[0][0] not in {1,SCHEMA_VERSION}:
                raise ServiceError('TASK_SCHEMA_UNSUPPORTED', status=503)
            if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ServiceError('TASK_STORE_CORRUPT', status=503)
            if write:
                page_size=connection.execute('PRAGMA page_size').fetchone()[0]
                connection.execute('PRAGMA max_page_count='+str(MAX_DATABASE_BYTES//page_size))
                connection.execute('BEGIN IMMEDIATE')
            yield connection
            if write:
                try:self.fault('before_commit')
                except OSError:raise ServiceError('TASK_NOT_COMMITTED',status=503,outcome='NOT_COMMITTED')from None
                self._check_storage(connection=connection)
                if connection.execute('PRAGMA page_count').fetchone()[0]*connection.execute('PRAGMA page_size').fetchone()[0]>MAX_DATABASE_BYTES:
                    raise ServiceError('TASK_STORAGE_CAPACITY',status=507)
                if guard is not None:guard()
                connection.commit()
                try: self.fault('after_commit')
                except OSError: raise ServiceError('TASK_COMMIT_UNCONFIRMED', status=503, outcome='UNKNOWN') from None
        except sqlite3.Error as error:
            if getattr(error,'sqlite_errorcode',None)==sqlite3.SQLITE_FULL:
                raise ServiceError('TASK_STORAGE_CAPACITY',status=507)from None
            code = 'TASK_STORE_BUSY' if 'locked' in str(error).lower() else 'TASK_STORE_CORRUPT'
            raise ServiceError(code, status=503) from None
        finally:
            if connection is not None: connection.close()

    @staticmethod
    def _replace_plan_input(db, plan_id, value):
        """Reuse overflow pages in the caller's transaction, never publish a placeholder.

        Encode and hash before either write. No callbacks or reads are allowed
        between the two statements; the existing outer guard owns commit/rollback.
        """
        encoded = canonical(value).decode()
        hashed = digest(value)
        if not db.in_transaction:
            raise ServiceError('TASK_TRANSACTION_REQUIRED', status=503)
        if db.execute('SELECT COUNT(*) FROM plan_inputs WHERE id=?', (plan_id,)).fetchone()[0] != 1:
            raise ServiceError('TASK_STORE_CORRUPT', status=503)
        db.execute("UPDATE plan_inputs SET body='',hash='' WHERE id=?", (plan_id,))
        db.execute('UPDATE plan_inputs SET body=?,hash=? WHERE id=?', (encoded, hashed, plan_id))

    @staticmethod
    def _decode(raw, expected):
        try:
            value = json.loads(raw)
            if digest(value) != expected or canonical(value).decode() != raw: raise ValueError()
            return value
        except (ValueError, TypeError, UnicodeError):
            raise ServiceError('TASK_STORE_CORRUPT', status=503) from None

    @staticmethod
    def result_version(task):
        if task['result'] is None:return None
        return digest({'task_id':task['id'],'thread_id':task['thread_id'],
                       'turn_id':task['turn_id'],'result_sha256':digest(task['result'])})

    @staticmethod
    def review_state(task, events):
        version=TaskStore.result_version(task)
        reviews=[e['data']for e in events if e['key'].startswith('result-review-')]
        latest=reviews[-1]if reviews else None
        current=latest is not None and latest['result_version']==version and task['status']=='checks_complete'
        return {'revision':latest['revision']if latest else 0,'result_version':version,
                'decision':latest['decision']if current else 'pending',
                'current':current,'git_delivery':'not_recorded'}

    def review_result(self, ident, command, *, owner_context, authorize=None):
        """An owner receipt for one exact result, never an execution or Git grant."""
        fields={'request_id','expected_review_revision','expected_result_version','decision'}
        if type(command)is not dict or set(command)!=fields:raise ServiceError('INVALID_RESULT_REVIEW')
        stable_id(command['request_id']);stable_id(ident)
        version=command['expected_result_version']
        if (type(command['expected_review_revision'])is not int or command['expected_review_revision']<0 or
                type(version)is not str or len(version)!=64 or any(c not in '0123456789abcdef'for c in version) or
                type(command['decision'])is not str or command['decision']not in {'accept','request_changes','defer'}):raise ServiceError('INVALID_RESULT_REVIEW')
        with self._connection(True,guard=authorize)as connection:
            row=connection.execute('SELECT *FROM tasks WHERE id=?',(ident,)).fetchone()
            if row is None:raise ServiceError('TASK_NOT_FOUND',status=404)
            task=self._task(row)
            if owner_context!=task['intent'].get('owner_context'):raise ServiceError('RESULT_REVIEW_OWNER_REJECTED',status=403)
            if self.result_version(task)!=version:raise ServiceError('RESULT_VERSION_STALE',status=409)
            key='result-review-'+command['request_id']
            old=connection.execute('SELECT body,hash FROM events WHERE task_id=?AND event_key=?',(ident,key)).fetchone()
            if old:
                receipt=self._decode(old['body'],old['hash'])
                if receipt['command']!=command or receipt['owner_context']!=owner_context:raise ServiceError('RESULT_REVIEW_CONFLICT',status=409)
                return {'replayed':True}
            events=[{'key':r['event_key'],'data':self._decode(r['body'],r['hash'])}for r in connection.execute('SELECT *FROM events WHERE task_id=?ORDER BY sequence',(ident,))]
            state=self.review_state(task,events)
            if state['revision']!=command['expected_review_revision']:raise ServiceError('RESULT_REVIEW_REVISION_CONFLICT',status=409)
            checks=task['result'].get('checks')if type(task['result'])is dict else None
            if (task['status']!='checks_complete' or task['provider_status']!='completed' or task['cancel'] or
                    not task['thread_id']or not task['turn_id']or type(checks)is not list or not checks or
                    any(type(c)is not dict or type(c.get('exit'))is not int or c['exit']!=0 for c in checks)):
                raise ServiceError('RESULT_NOT_REVIEWABLE',status=409)
            receipt={'command':command,'owner_context':owner_context,'revision':state['revision']+1,
                     'result_version':version,'decision':command['decision']}
            self._append_event(connection,ident,key,receipt)
            return {'replayed':False}

    def register_grant(self, grant):
        """Trusted local owner-control operation, intentionally absent from HTTP."""
        stable_id(grant['grant_id']); body = canonical(grant).decode(); hashed = digest(grant)
        if type(grant.get('version')) is not int or grant['version'] < 1 or len(body.encode()) > 32768:
            raise ServiceError('INVALID_GRANT')
        with self._connection(True) as connection:
            old = connection.execute('SELECT * FROM grants WHERE id=?', (grant['grant_id'],)).fetchone()
            if old and old['version'] >= grant['version'] and old['hash'] != hashed:
                raise ServiceError('GRANT_VERSION_CONFLICT', status=409)
            connection.execute('INSERT INTO grants VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,body=excluded.body,hash=excluded.hash',
                               (grant['grant_id'], grant['version'], body, hashed))

    def grant(self, grant_id):
        stable_id(grant_id)
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM grants WHERE id=?', (grant_id,)).fetchone() if connection else None
            return self._decode(row['body'], row['hash']) if row else None

    def grants(self, *, project_id=None):
        if project_id is not None:stable_id(project_id)
        with self._connection() as connection:
            query='SELECT * FROM grants'
            if project_id is not None:
                query+=" WHERE CASE WHEN json_valid(body) THEN json_extract(body,'$.project_id') END=?"
            return [self._decode(row['body'],row['hash'])for row in connection.execute(
                query+' ORDER BY id',(project_id,)if project_id is not None else())]if connection else []

    @staticmethod
    def _ordinary_key(owner_context,project_id,parent_task_id):
        if type(owner_context)is not str or not 1<=len(owner_context)<=256 or '\x00'in owner_context:
            raise ServiceError('INVALID_ORDINARY_DRAFT')
        stable_id(project_id)
        if parent_task_id is not None:stable_id(parent_task_id)
        return owner_context,project_id,parent_task_id or ''

    @staticmethod
    def _ordinary_features(connection,*,create=False):
        rows=connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name IN ('ordinary_drafts','ordinary_draft_requests')").fetchall()
        if not rows:
            if create:
                for statement in ORDINARY_DRAFT_TABLES.values():connection.execute(statement)
            return create
        if len(rows)!=2 or any(row['type']!='table' or row['sql']!=ORDINARY_DRAFT_TABLES[row['name']] for row in rows):
            raise ServiceError('TASK_STORE_CORRUPT',status=503)
        return True

    def _ordinary_view(self,row,project_id,parent_task_id):
        if row is None:return {'project_id':project_id,'parent_task_id':parent_task_id,'revision':0,'value':None}
        value=self._decode(row['body'],row['hash'])
        if (type(value)is not dict or set(value)!={'project_id','parent_task_id','revision','value'}
                or value['project_id']!=project_id or value['parent_task_id']!=parent_task_id
                or type(value['revision'])is not int or value['revision']!=row['revision'] or value['revision']<1
                or (value['value'] is not None and type(value['value'])is not dict)
                or len(canonical(value['value']))>MAX_ORDINARY_DRAFT_BYTES):
            raise ServiceError('TASK_STORE_CORRUPT',status=503)
        return value

    def _validate_ordinary_tables(self,connection):
        """Offline copy validation preserves the optional feature and receipts."""
        if not self._ordinary_features(connection):return False
        drafts=connection.execute('SELECT * FROM ordinary_drafts').fetchall()
        receipts=connection.execute('SELECT * FROM ordinary_draft_requests').fetchall()
        if len(drafts)>MAX_ORDINARY_DRAFTS or len(receipts)>MAX_ORDINARY_DRAFT_REQUESTS:
            raise ServiceError('TASK_STORE_CORRUPT',status=503)
        current={};confirmed=set()
        def check_view(row,project,parent):
            view=self._ordinary_view(row,project,parent)
            if len(canonical(view['value']))>MAX_ORDINARY_DRAFT_BYTES:
                raise ServiceError('TASK_STORE_CORRUPT',status=503)
            return view
        for row in drafts:
            parent=row['parent'] or None
            key=self._ordinary_key(row['owner'],row['project'],parent)
            current[key]=check_view(row,row['project'],parent)
        for row in receipts:
            stable_id(row['id'])
            decoded=self._decode(row['body'],row['hash'])
            if type(decoded)is not dict:raise ServiceError('TASK_STORE_CORRUPT',status=503)
            project=decoded.get('project_id');parent=decoded.get('parent_task_id')
            key=self._ordinary_key(row['owner'],project,parent)
            view=check_view({**dict(row),'revision':decoded.get('revision')},project,parent)
            command={'request_id':row['id'],'expected_revision':view['revision']-1,
                     'project_id':project,'parent_task_id':parent,'value':view['value']}
            if (digest(command)!=row['command_hash'] or key not in current
                    or current[key]['revision']<view['revision']):
                raise ServiceError('TASK_STORE_CORRUPT',status=503)
            if current[key]==view:confirmed.add(key)
        if confirmed!=set(current):raise ServiceError('TASK_STORE_CORRUPT',status=503)
        return True

    def ordinary_draft(self,owner_context,project_id,parent_task_id=None):
        key=self._ordinary_key(owner_context,project_id,parent_task_id)
        with self._connection() as connection:
            row=(connection.execute('SELECT * FROM ordinary_drafts WHERE owner=?AND project=?AND parent=?',key).fetchone()
                 if self._ordinary_features(connection) else None)
            return self._ordinary_view(row,project_id,parent_task_id)

    def save_ordinary_draft(self,command,owner_context,authorize):
        """Explicit owner save only: the service validates TaskSpec and scope."""
        try:
            if type(command)is not dict or set(command)!={'request_id','expected_revision','project_id','parent_task_id','value'}:
                raise ValueError()
            stable_id(command['request_id'])
            key=self._ordinary_key(owner_context,command['project_id'],command['parent_task_id'])
            if (type(command['expected_revision'])is not int or command['expected_revision']<0
                    or command['value'] is not None and type(command['value'])is not dict):raise ValueError()
            if len(canonical(command['value']))>MAX_ORDINARY_DRAFT_BYTES:
                raise ServiceError('ORDINARY_DRAFT_CAPACITY',status=413)
            command_hash=digest(command)
            # Freeze caller-owned data before authorization and transaction work.
            command=json.loads(canonical(command))
        except (ValueError,TypeError,UnicodeError,OverflowError):
            raise ServiceError('INVALID_ORDINARY_DRAFT') from None
        authorize()
        with self._connection(True,guard=authorize) as connection:
            self._ordinary_features(connection,create=True)
            old=connection.execute('SELECT * FROM ordinary_draft_requests WHERE owner=?AND id=?',
                                   (owner_context,command['request_id'])).fetchone()
            if old:
                if old['command_hash']!=command_hash:raise ServiceError('ORDINARY_DRAFT_REQUEST_CONFLICT',status=409)
                receipt=self._decode(old['body'],old['hash'])
                return {'draft':receipt,'replayed':True,'request_id':command['request_id']}
            row=connection.execute('SELECT * FROM ordinary_drafts WHERE owner=?AND project=?AND parent=?',key).fetchone()
            current=self._ordinary_view(row,command['project_id'],command['parent_task_id'])
            if current['revision']!=command['expected_revision']:
                raise ServiceError('ORDINARY_DRAFT_CONFLICT',status=409)
            if (row is None and connection.execute('SELECT count(*) FROM ordinary_drafts').fetchone()[0]>=MAX_ORDINARY_DRAFTS
                    or connection.execute('SELECT count(*) FROM ordinary_draft_requests').fetchone()[0]>=MAX_ORDINARY_DRAFT_REQUESTS):
                raise ServiceError('ORDINARY_DRAFT_CAPACITY',status=507)
            view={'project_id':command['project_id'],'parent_task_id':command['parent_task_id'],
                  'revision':current['revision']+1,'value':command['value']}
            body=canonical(view).decode();hashed=digest(view)
            connection.execute('INSERT INTO ordinary_drafts VALUES(?,?,?,?,?,?) ON CONFLICT(owner,project,parent) DO UPDATE SET revision=excluded.revision,body=excluded.body,hash=excluded.hash',
                               (*key,view['revision'],body,hashed))
            connection.execute('INSERT INTO ordinary_draft_requests VALUES(?,?,?,?,?)',
                               (owner_context,command['request_id'],command_hash,body,hashed))
            return {'draft':view,'replayed':False,'request_id':command['request_id']}

    def admit(self, payload, intent, *, authorize=None):
        ident = stable_id(payload['request_id']); hashed = digest(payload)
        if len(canonical(payload)) > 8192 or len(canonical(intent)) > 49152:
            raise ServiceError('TASK_PAYLOAD_CAPACITY', status=413)
        with self._connection(True,guard=authorize) as connection:
            old = self._task_row(connection,ident)
            if old:
                if old['payload_hash'] != hashed: raise ServiceError('TASK_REQUEST_CONFLICT', status=409)
                return self._task(old), True
            if connection.execute('SELECT count(*) FROM tasks').fetchone()[0] >= MAX_TASKS:
                raise ServiceError('TASK_CAPACITY', status=507)
            connection.execute('INSERT INTO tasks(id,payload,payload_hash,intent,intent_hash,project,parent,status,created) VALUES(?,?,?,?,?,?,?,?,?)',
                (ident, canonical(payload).decode(), hashed, canonical(intent).decode(), digest(intent), payload['project_id'], payload['parent_task_id'], 'queued', time.time()))
            row = connection.execute('SELECT * FROM tasks WHERE id=?', (ident,)).fetchone()
            return self._task(row), False

    def _rebuild_rows(self,connection,ident):
        return self._history_rows(connection,ident,prefix='rebuild-')

    def rebuild_package(self,ident):
        from .rebuild_contracts import validate
        with self._connection() as connection:
            rows=self._rebuild_rows(connection,stable_id(ident)) if connection else []
            values=[r['data'] for r in rows if r['key']=='rebuild-package']
            return validate(values[0],'package') if values else None

    def _rebuild_check(self,connection,package,authority,receipt=None):
        from .rebuild_contracts import require_authority,reject
        authority=require_authority(authority);authority._authorize()
        if digest(package)!=authority.snapshot()['package_sha256']:reject('REBUILD_AUTHORITY_MISMATCH')
        row=connection.execute('SELECT body,hash FROM grants WHERE id=?',(package['grant']['grant_id'],)).fetchone()
        if not row:reject('REBUILD_GRANT_REQUIRED')
        grant=self._decode(row['body'],row['hash']);authority.check(self,package,grant,receipt)
        return grant

    def prepare_rebuild(self,package,*,authority):
        from .rebuild_contracts import validate,require_authority
        package=validate(package,'package');authority=require_authority(authority);authority._authorize()
        with self._connection(True) as connection:
            return self._prepare_rebuild(connection,package,authority)

    def _prepare_rebuild(self,connection,package,authority):
        from .rebuild_contracts import reject
        grant=self._rebuild_check(connection,package,authority)
        # Legacy prepare is not execution admission; preserve its deferred checks.
        # Explicit bootstrap wiring additionally validates its plan at preparation.
        if authority._plan is not None:self._rebuild_dependencies(connection,package,authority)
        row=self._task_row(connection,package['task_id'])
        if row:
            prior=self._rebuild_rows(connection,package['task_id'])
            old=next((r['data'] for r in prior if r['key']=='rebuild-package'),None)
            if old!=package:reject('REBUILD_PACKAGE_CONFLICT')
            replayed=True
        else:
            if connection.execute('SELECT count(*) FROM tasks').fetchone()[0]>=MAX_TASKS:reject('TASK_CAPACITY')
            payload={'kind':'rebuild_project','request_id':package['task_id'],'project_id':package['project_id'],'parent_task_id':None,'mode':'new','effect':package['effects']['mode']}
            intent={'grant':grant,'grant_hash':digest(grant),'package_sha256':digest(package),'owner_context':authority.snapshot()['owner_context']}
            connection.execute('INSERT INTO tasks(id,payload,payload_hash,intent,intent_hash,project,parent,status,created) VALUES(?,?,?,?,?,?,?,?,?)',(package['task_id'],canonical(payload).decode(),digest(payload),canonical(intent).decode(),digest(intent),package['project_id'],None,'queued',time.time()))
            self._append_event(connection,package['task_id'],'rebuild-package',package);replayed=False
        self._rebuild_check(connection,package,authority)
        if authority._plan is not None:self._rebuild_dependencies(connection,package,authority)
        return {'replayed':replayed,'execution_started_by_this_call':False}

    def claim_rebuild(self,ident,owner,*,authority):
        """Internal contract-only claim. Default TaskWorker never consumes this kind."""
        from .rebuild_contracts import require_authority,validate,fence,reject
        authority=require_authority(authority);authority._authorize();stable_id(owner)
        with self._connection(True) as connection:
            row=connection.execute('SELECT * FROM tasks WHERE id=?',(stable_id(ident),)).fetchone()
            if not row:reject('TASK_NOT_FOUND')
            rows=self._rebuild_rows(connection,ident);package=validate(next(r['data'] for r in rows if r['key']=='rebuild-package'),'package')
            self._rebuild_check(connection,package,authority)
            if row['status']!='queued' or row['cancel'] or self._task(row)['payload']['kind']!='rebuild_project':reject('REBUILD_CLAIM_REJECTED')
            now=time.time();claim={'schema_version':'2.0','provenance':'task_store','task_id':ident,'owner':owner,'generation':secrets.token_hex(16),'started_at':now,'expires_at':now+15}
            claim['fence']=fence(claim)
            self._append_event(connection,ident,'rebuild-claim-'+claim['generation'],claim)
            connection.execute("UPDATE tasks SET owner=?,lease_until=?,status='running' WHERE id=?",(owner,now+15,ident))
            self._rebuild_check(connection,package,authority)
        return claim

    def _lease_coverage(self,connection,task,*,start,end,now):
        from .rebuild_contracts import fence,reject
        rows=self._rebuild_rows(connection,task['id']);claims=[r for r in rows if r['key'].startswith('rebuild-claim-')]
        if not claims:reject('REBUILD_LEASE_REJECTED')
        first=claims[-1];claim=first['data'];until=claim['expires_at']
        if (claim['provenance']!='task_store' or claim['fence']!=fence(claim) or first['key']!='rebuild-claim-'+claim['generation']
                or not 0<=first['received']-claim['started_at']<=1 or until!=claim['started_at']+15
                or task['owner']!=claim['owner'] or task['cancel'] or task['status']!='running'
                or not claim['started_at']<=start<=end<=now):reject('REBUILD_LEASE_REJECTED')
        for row in rows:
            if row['sequence']<=first['sequence']:continue
            if row['key'].startswith('rebuild-lease-stop-') and row['data']['at']<=end:reject('REBUILD_LEASE_REJECTED')
            if not row['key'].startswith('rebuild-renew-'):continue
            r=row['data']
            if (r['provenance']!='task_store' or r['generation']!=claim['generation'] or r['owner']!=claim['owner'] or r['fence']!=claim['fence']
                    or r['task_id']!=task['id'] or r['prior_expires_at']!=until or not claim['started_at']<=r['at']<=until
                    or r['expires_at']!=r['at']+15 or not 0<=row['received']-r['at']<=1):reject('REBUILD_LEASE_REJECTED')
            until=r['expires_at']
        if end>until or task['lease_until']!=until or now>=until:reject('REBUILD_LEASE_REJECTED')
        return claim,until

    def _renew_rebuild(self,connection,row,now,status=None):
        if not row:return
        if not connection.execute("SELECT 1 FROM events WHERE task_id=? AND event_key LIKE 'rebuild-claim-%' LIMIT 1",(row['id'],)).fetchone():return
        task=self._task(row)
        if task['payload'].get('kind')!='rebuild_project':return
        from .rebuild_contracts import reject
        if task['status']!='running' and status in TERMINAL:return
        claim,_=self._lease_coverage(connection,task,start=now,end=now,now=now)
        if status is not None and status!='running':
            self._append_event(connection,task['id'],'rebuild-lease-stop-'+secrets.token_hex(8),{'at':now,'status':status,'generation':claim['generation']});return
        self._append_event(connection,task['id'],'rebuild-renew-'+secrets.token_hex(8),{'provenance':'task_store','task_id':task['id'],'owner':task['owner'],'generation':claim['generation'],'fence':claim['fence'],'prior_expires_at':task['lease_until'],'at':now,'expires_at':now+15})

    def reconcile_rebuild(self,ident,*,authority):
        from .rebuild_contracts import require_authority,validate
        authority=require_authority(authority);authority._authorize()
        with self._connection(True) as connection:
            rows=self._rebuild_rows(connection,ident)
            package=validate(next(r['data'] for r in rows if r['key']=='rebuild-package'),'package')
            self._rebuild_check(connection,package,authority)
            connection.execute("UPDATE tasks SET status='requires_reconcile' WHERE id=? AND cancel=0 AND status IN ('running','validating')",(ident,))
            self._append_event(connection,ident,'rebuild-reconcile-'+secrets.token_hex(8),{'reason':'invalid_lease_coverage','requires_reconcile':True,'at':time.time()})

    def record_rebuild_attempt(self,package,attempt,*,authority):
        from .rebuild_contracts import validate,require_authority,bind,receipt_binding,settled_evidence,reject
        package=validate(package,'package');attempt=validate(attempt,'attempt');authority=require_authority(authority);authority._authorize()
        with self._connection(True) as connection:
            grant=self._rebuild_check(connection,package,authority)
            self._rebuild_dependencies(connection,package,authority)
            row=self._task_row(connection,package['task_id'])
            if not row:reject('TASK_NOT_FOUND')
            task=self._task(row);rows=self._rebuild_rows(connection,task['id']);prior={r['key']:r['data'] for r in rows}
            if prior.get('rebuild-package')!=package:reject('REBUILD_PACKAGE_CONFLICT')
            key='rebuild-attempt-'+attempt['attempt_id']
            self._dispatch_attempt_budget(connection,package,attempt,authority,replayed=prior.get(key)==attempt)
            if key in prior:
                if prior[key]!=attempt:reject('REBUILD_ATTEMPT_CONFLICT')
                replayed=True
            else:
                claim,_=self._lease_coverage(connection,task,start=attempt['started_at'],end=time.time(),now=time.time())
                bind(package,attempt,task,grant,authority,claim,now=time.time())
                attempts=[v for k,v in prior.items() if k.startswith('rebuild-attempt-')]
                receipts=[v for k,v in prior.items() if k.startswith('rebuild-receipt-')]
                if len(attempts)>=package['budget']['remaining_attempts'] or sum(a['budget_reservation']['seconds'] for a in attempts)+attempt['budget_reservation']['seconds']>package['budget']['remaining_seconds']:reject('REBUILD_BUDGET_EXHAUSTED')
                if any(a['attempt_id'] not in {r['attempt_id'] for r in receipts} for a in attempts):reject('REBUILD_ATTEMPT_UNRESOLVED')
                if any(r['effect_state']=='unknown' or any(c['exit']!=0 for c in r['checks']) for r in receipts):reject('REBUILD_RETRY_DIAGNOSIS_REQUIRED')
                active=connection.execute("SELECT count(*) FROM tasks WHERE status IN ('running','waiting_input','waiting_approval','validating','requires_reconcile')").fetchone()[0]
                if active>package['budget']['capacity']:reject('REBUILD_CAPACITY')
                self._append_event(connection,task['id'],key,attempt);replayed=False
            self._rebuild_check(connection,package,authority)
            self._rebuild_dependencies(connection,package,authority)
        return {'replayed':replayed,'attempt_sha256':digest(attempt),'execution_started_by_this_call':False}

    def _dispatch_attempt_budget(self,db,package,attempt,root_authority,*,replayed):
        from .dispatch_budget import split,authority,reject,executor_binding_matches
        if not self._plan_schema(db):
            if root_authority._budget is not None:reject('DISPATCH_POLICY_REQUIRED')
            return # Pre-policy legacy behavior, not PCH runtime adoption.
        raw=db.execute('SELECT body,hash FROM plan_inputs WHERE id=?',(package['lineage']['goal_id'],)).fetchone()
        if raw is None:
            if root_authority._budget is not None:reject('DISPATCH_POLICY_REQUIRED')
            return
        _,budget=split(self._decode(*raw))
        if budget is None:
            if root_authority._budget is not None:reject('DISPATCH_POLICY_REQUIRED')
            return
        cap=authority(root_authority._budget);plan=self._plan_input(db,package['lineage']['goal_id'],cap._plan)
        from .dispatch_budget import validate
        validate(budget,plan['plan_id'])
        ident=root_authority.snapshot().get('dispatch_id');rows=[r for r in budget['reservations']if r['request']['dispatch_id']==ident]
        if len(rows)!=1:reject('DISPATCH_RESERVATION_REQUIRED')
        row=rows[0];r=row['request'];link={'dispatch_id':ident,'task_id':package['task_id'],'attempt_id':attempt['attempt_id'],'package_sha256':digest(package),'executor_sha256':digest(attempt['executor'])}
        cap.check(plan,budget['policy'],'attempt',link)
        if (r['task_id']!=package['task_id']or r['attempt_id']!=attempt['attempt_id']or r['package_sha256']!=digest(package)
                or not executor_binding_matches(r['executor'],attempt['executor'])or r['effect']!=package['effects']['mode']
                or r['seconds']<attempt['budget_reservation']['seconds']or attempt['started_at']+attempt['budget_reservation']['seconds']>r['deadline']
                or not replayed and row['closures']):reject('DISPATCH_RESERVATION_MISMATCH')

    def _dispatch_budget_operation(self,plan_id,operation,body,cap):
        import copy
        from .dispatch_budget import authority,policy,request,closure,amendment,split,wrap,validate,status,reject
        cap=authority(cap);observed=[];context={}
        def guard():
            cap._authorize()
            if context:
                controls=cap.check(context['plan'],context['policy'],operation,body)
                if observed and controls!=observed[0]:reject('DISPATCH_CONTROL_CHANGED')
        with self._connection(True,guard=guard)as db:
            plan=self._plan_input(db,plan_id,cap._plan)
            raw=db.execute('SELECT body,hash FROM plan_inputs WHERE id=?',(plan_id,)).fetchone();history,budget=split(self._decode(*raw))
            p=policy(body)if operation=='baseline'else budget['policy']if budget else None
            if p is None:reject('DISPATCH_POLICY_REQUIRED')
            change=None;prior_amendment=None
            if operation=='amend':
                change=amendment(body)
                prior_amendment=next((r for r in budget.get('amendments',[])if r['request']['amendment_id']==change['amendment_id']),None)
                if prior_amendment is not None:
                    if prior_amendment['request']!=change:reject('DISPATCH_AMENDMENT_CONFLICT')
                    # Exact replay still checks the original trusted policy and
                    # request authority, never silently adopts a new binding.
                    p=prior_amendment['previous_policy']
            context.update(plan=plan,policy=p);observed.append(cap.check(plan,p,operation,body));replayed=False
            if operation=='baseline':
                if p['lineage_id']!=plan_id:reject('DISPATCH_LINEAGE_MISMATCH')
                if budget is not None:
                    if budget['policy']!=p:reject('DISPATCH_BASELINE_CONFLICT')
                    replayed=True
                else:budget={'schema_version':'dispatch-budget/1','policy':p,'reservations':[]}
            elif operation=='reserve':
                r=request(body);old=next((x for x in budget['reservations']if x['request']['dispatch_id']==r['dispatch_id']),None)
                if old:
                    if old['request']!=r:reject('DISPATCH_REQUEST_CONFLICT')
                    replayed=True
                else:
                    if r['dispatch_id']in p['covered_dispatch_ids']:reject('DISPATCH_ALREADY_COVERED')
                    now=time.time()
                    if not now+r['seconds']<=r['deadline']<=now+900:reject('DISPATCH_DEADLINE')
                    if len(budget['reservations'])>=p['limit']:reject('DISPATCH_CAPACITY')
                    budget['reservations'].append({'request':r,'sha256':digest(r),'reserved_at':now,'closures':[]})
            elif operation=='amend':
                if prior_amendment is not None:replayed=True
                else:
                    if digest(p)!=change['expected_policy_sha256']:reject('DISPATCH_AMENDMENT_STALE')
                    if p['limit']!=480 or budget.get('amendments'):reject('DISPATCH_AMENDMENT_CONFLICT')
                    new_policy={**copy.deepcopy(p),'limit':960}
                    receipt={'request':change,'sha256':digest(change),'previous_policy':copy.deepcopy(p),
                        'new_policy_sha256':digest(new_policy),'authorized_binding_sha256':digest(cap.snapshot()),'applied_at':time.time()}
                    budget={**budget,'policy':new_policy,'amendments':[receipt]}
            elif operation=='close':
                proof=closure(body);row=next((x for x in budget['reservations']if x['request']['dispatch_id']==proof['dispatch_id']),None)
                if row is None or row['sha256']!=proof['reservation_sha256']:reject('DISPATCH_CLOSURE_MISMATCH')
                old=next((c for x in budget['reservations']for c in x['closures']if c['closure_id']==proof['closure_id']),None)
                if old:
                    if old!=proof:reject('DISPATCH_CLOSURE_CONFLICT')
                    replayed=True
                else:row['closures'].append(proof)
            elif operation!='status':reject()
            validate(budget,plan_id);guard()
            if operation!='status'and not replayed:
                value=wrap(history,budget)
                self._replace_plan_input(db,plan_id,value)
                self.fault('before_dispatch_budget_commit');guard()
            result=status(budget)
        return {'replayed':replayed,**result,'invoked':False}

    def initialize_dispatch_budget(self,plan_id,baseline,*,authority):
        return self._dispatch_budget_operation(plan_id,'baseline',baseline,authority)
    def reserve_dispatch(self,plan_id,request,*,authority):
        return self._dispatch_budget_operation(plan_id,'reserve',request,authority)
    def close_dispatch_resources(self,plan_id,proof,*,authority):
        return self._dispatch_budget_operation(plan_id,'close',proof,authority)
    def dispatch_budget_status(self,plan_id,*,authority):
        return self._dispatch_budget_operation(plan_id,'status',{},authority)

    def amend_dispatch_budget(self,plan_id,amendment,*,authority):
        """Internal owner-authorized 480→960 CAS; no execution/HTTP capability."""
        return self._dispatch_budget_operation(plan_id,'amend',amendment,authority)

    def _normal_dispatch_event(self,db,ident,stage,marker):
        # The first generation used singleton keys. They apply only to their
        # exact marker; subsequent dispatches never inherit their start permit.
        base='normal-dispatch-'+stage
        rows=db.execute('SELECT body,hash FROM events WHERE task_id=? AND event_key IN (?,?)',
            (ident,base,base+'-'+digest(marker))).fetchall()
        values=[self._decode(*r)for r in rows]
        matched=[v for v in values if v.get('marker')==marker]
        if len(matched)>1:raise ServiceError('NORMAL_DISPATCH_HISTORY_CONFLICT',status=409)
        return matched[0]if matched else None

    def _normal_dispatch_key(self,db,ident,stage,marker):
        base='normal-dispatch-'+stage
        row=db.execute('SELECT body,hash FROM events WHERE task_id=? AND event_key=?',(ident,base)).fetchone()
        return base if row is None or self._decode(*row).get('marker')==marker else base+'-'+digest(marker)

    def normal_dispatch_control(self,ident,*,binding,owner=None,worker=None,thread_id=None,turn_id=None):
        """Internal read-only observation; never reserves or grants a second RPC."""
        from .dispatch_budget import NormalDispatchBinding,need,split,validate
        need(type(binding)is NormalDispatchBinding,'NORMAL_DISPATCH_BINDING_REQUIRED')
        with self._connection()as db:
            row=db.execute('SELECT *FROM tasks WHERE id=?',(stable_id(ident),)).fetchone()
            need(row is not None,'TASK_NOT_FOUND');task=self._task(row);grant=task['intent']['grant']
            marker=binding.marker(ident,grant);b=binding.snapshot();cap=binding._authority
            need(task['payload'].get('kind')=='ordinary_project'and task['intent'].get('normal_dispatch_budget')==marker,
                 'NORMAL_DISPATCH_BINDING_REQUIRED')
            live=db.execute('SELECT body,hash FROM grants WHERE id=?',(grant['grant_id'],)).fetchone()
            need(live is not None and self._decode(*live)==grant and grant['expires_at']>time.time(),'GRANT_VERSION_STALE')
            plan=self._plan_input(db,b['lineage_id'],cap._plan)
            raw=db.execute('SELECT body,hash FROM plan_inputs WHERE id=?',(b['lineage_id'],)).fetchone()
            _,budget=split(self._decode(*raw));need(budget is not None,'DISPATCH_POLICY_REQUIRED');validate(budget,b['lineage_id'])
            baseline=None
            if owner is None:
                need(task['status']=='queued'and not task['cancel'],'NORMAL_DISPATCH_CLAIM_STALE')
            else:
                need(task['owner']==owner and task['status']in {'running','waiting_approval','waiting_input','validating'}
                     and task['lease_until']and task['lease_until']>time.time()
                     and task['thread_id']==thread_id and task['turn_id']==turn_id,'NORMAL_DISPATCH_CLAIM_STALE')
                link=self._normal_dispatch_event(db,ident,'reserved',marker)
                need(link is not None and link['worker']==worker and worker.get('owner')==owner,'NORMAL_DISPATCH_WORKER_REJECTED')
                if turn_id is not None:
                    started=self._normal_dispatch_event(db,ident,'start',marker)
                    need(started is not None and started['worker']==worker and started['thread_id']==thread_id,'NORMAL_DISPATCH_WORKER_REJECTED')
                rows=[r for r in budget['reservations']if r['request']['dispatch_id']==marker['dispatch_id']]
                need(len(rows)==1 and rows[0]['request']==b['request']and rows[0]['sha256']==marker['request_sha256']
                     and not rows[0]['closures'],'DISPATCH_RESERVATION_MISMATCH')
                need(time.time()<min(b['request']['deadline'],rows[0]['reserved_at']+b['request']['seconds']), 'DISPATCH_DEADLINE')
                stored=self._normal_dispatch_event(db,ident,'control',marker)
                need(stored is not None and stored['worker']==worker,'NORMAL_CONTROL_BASELINE_REQUIRED')
                baseline=stored['control']
            observed=cap.observe_controls(plan,budget['policy'],'reserve',b['request'])
            if owner is not None:
                need(task['lease_until']>time.time()and time.time()<min(b['request']['deadline'],rows[0]['reserved_at']+b['request']['seconds']), 'NORMAL_DISPATCH_CLAIM_STALE')
            return {'control':observed,'baseline':baseline,'observed_at':time.time(),
                    'progress_allowed':observed['state']=='active'and(baseline is None or observed==baseline)and not task['cancel']}

    def _normal_dispatch_check(self,db,ident,owner,expected,grant,worker,binding,*,starting=False):
        from .dispatch_budget import NormalDispatchBinding,need,split,validate
        need(type(binding)is NormalDispatchBinding,'NORMAL_DISPATCH_BINDING_REQUIRED')
        row=db.execute('SELECT * FROM tasks WHERE id=?',(ident,)).fetchone()
        need(row is not None,'NORMAL_DISPATCH_TASK_REQUIRED');task=self._task(row)
        need(task['payload'].get('kind')=='ordinary_project' and not task['cancel']
             and task['owner']==owner and task['status']=='running' and task['lease_until']>time.time()
             and task['intent']==expected['intent'] and task['payload']==expected['payload'], 'NORMAL_DISPATCH_CLAIM_STALE')
        marker=binding.marker(ident,grant)
        need(task['intent'].get('normal_dispatch_budget')==marker,'NORMAL_DISPATCH_BINDING_REQUIRED')
        need(type(worker)is dict and worker.get('owner')==owner and type(worker.get('pid'))is int
             and worker['pid']>0 and type(worker.get('start_ticks'))is str and worker['start_ticks'].isdecimal(),
             'NORMAL_DISPATCH_WORKER_REJECTED')
        live=db.execute('SELECT body,hash FROM grants WHERE id=?',(grant['grant_id'],)).fetchone()
        need(live is not None and self._decode(*live)==grant and grant['expires_at']>time.time(),'GRANT_VERSION_STALE')
        b=binding.snapshot();cap=binding._authority
        plan=self._plan_input(db,b['lineage_id'],cap._plan)
        raw=db.execute('SELECT body,hash FROM plan_inputs WHERE id=?',(b['lineage_id'],)).fetchone()
        _,budget=split(self._decode(*raw));need(budget is not None,'DISPATCH_POLICY_REQUIRED');validate(budget,b['lineage_id'])
        controls=cap.check(plan,budget['policy'],'reserve',b['request'])
        rows=[r for r in budget['reservations']if r['request']['dispatch_id']==marker['dispatch_id']]
        need(len(rows)==1,'DISPATCH_RESERVATION_REQUIRED');r=rows[0];request=b['request']
        need(r['request']==request and r['sha256']==marker['request_sha256'] and not r['closures'],
             'DISPATCH_RESERVATION_MISMATCH')
        now=time.time()
        need(now<min(request['deadline'],r['reserved_at']+request['seconds'])
             and request['seconds']>=grant['max_turn_seconds'] and now+grant['max_turn_seconds']<=request['deadline'],
             'DISPATCH_DEADLINE')
        if starting:
            baseline=self._normal_dispatch_event(db,ident,'control',marker)
            need(baseline is not None and baseline['control']==controls,'DISPATCH_CONTROL_CHANGED')
        if starting and request['executor'].get('thread_id')is not None:
            need(request['executor']['thread_id']==task['thread_id'],'DISPATCH_RESERVATION_MISMATCH')
        link={'marker':marker,'worker':worker,'intent_sha256':digest(task['intent']),
              'payload_sha256':digest(task['payload']),'executor':request['executor']}
        return link,controls

    def reserve_normal_dispatch(self,ident,owner,expected,grant,worker,binding):
        """Reserve before open; a durable open intent cannot authorize a replay."""
        from .dispatch_budget import NormalDispatchBinding,need
        marker=expected['intent'].get('normal_dispatch_budget')
        if marker is None and binding is None:return None
        need(type(binding)is NormalDispatchBinding,'NORMAL_DISPATCH_BINDING_REQUIRED')
        need(marker==binding.marker(ident,grant),'NORMAL_DISPATCH_BINDING_REQUIRED')
        b=binding.snapshot()
        self.reserve_dispatch(b['lineage_id'],b['request'],authority=binding._authority)
        observed=[]
        def guard():
            _,controls=self._normal_dispatch_check(db,ident,owner,expected,grant,worker,binding)
            need(controls==observed[0],'DISPATCH_CONTROL_CHANGED')
        with self._connection(True,guard=guard)as db:
            link,controls=self._normal_dispatch_check(db,ident,owner,expected,grant,worker,binding);observed.append(controls)
            need(self._normal_dispatch_event(db,ident,'reserved',marker)is None,'NORMAL_DISPATCH_REQUIRES_RECONCILE')
            self._append_event(db,ident,self._normal_dispatch_key(db,ident,'reserved',marker),link)
            self._append_event(db,ident,self._normal_dispatch_key(db,ident,'control',marker),
                {'marker':marker,'worker':worker,'control':controls,'observed_at':time.time()})
        return link

    def _rebuild_bootstrap_dependency(self,connection,package,dep,authority):
        """Absent contract rows only; use current, verified plan witnesses in-place.

        A serialized hash mapping is not this capability. Historical receipts
        remain in plan tables; no retrospective task/grant/attempt is created.
        """
        from .plan_admission import BOOTSTRAP_IDS,authority as require_plan
        from .rebuild_contracts import reject
        if authority._plan is None:reject('REBUILD_DEPENDENCY_AUTHORITY_REQUIRED')
        cap=require_plan(authority._plan)
        plan=self._plan_input(connection,package['lineage']['goal_id'],cap)
        definitions={t['id']:t for t in plan['tasks']};definition=definitions.get(package['task_id'])
        if (package['plan']!={'revision':plan['revision'],'sha256':digest(plan)} or definition is None
                or any(package[k]!=definition[v] for k,v in [('task_id','id'),('project_id','project_id'),('phase_id','phase_id'),('round_id','round_id')])
                or package['route']['owner_route']!=definition['owner_route']
                or {d['task_id']for d in package['dependencies']}!=set(definition['depends_on'])):reject('REBUILD_DEPENDENCY_PLAN_MISMATCH')
        witnesses={}
        _,accepted,controls=self._plan_state(connection,plan,cap,witnesses=witnesses)
        ident=dep['task_id'];record=witnesses.get(ident)
        if (controls['state']!='active' or ident not in BOOTSTRAP_IDS or ident not in accepted or record is None
                or definitions[ident]['admission_class']!='bootstrap' or record['origin']!='bootstrap'
                or record['receipt_sha256']!=dep['receipt_sha256']):reject('REBUILD_DEPENDENCY_FAILED')
        # _plan_state selects one exact applicable version and verifies its full
        # immutable record hash, known outcome, conflicts and transitive closure.
        cap.check(plan)

    def _rebuild_dependencies(self,connection,package,authority):
        from .rebuild_contracts import reject,validate,receipt_binding,settled_evidence
        for dep in package['dependencies']:
            drow=self._task_row(connection,dep['task_id'])
            if drow is None:
                self._rebuild_bootstrap_dependency(connection,package,dep,authority)
                continue
            if drow['status']!='checks_complete' or drow['cancel']:reject('REBUILD_DEPENDENCY_FAILED')
            history={r['key']:r['data'] for r in self._rebuild_rows(connection,dep['task_id'])}
            matches=[r for k,r in history.items() if k.startswith('rebuild-receipt-') and digest(r)==dep['receipt_sha256']]
            if len(matches)!=1:reject('REBUILD_DEPENDENCY_FAILED')
            receipt=matches[0];dp=validate(history.get('rebuild-package'),'package');da=validate(history.get('rebuild-attempt-'+receipt['attempt_id']),'attempt');receipt_binding(receipt,dp,da)
            proof=authority.snapshot().get('verified_dependencies',{}).get(dep['receipt_sha256'])
            settled_evidence(dp,receipt,proof,da)
            from .ordinary_tasks import check_registration,validate_grant,validate_scoped_write_grant
            from .rebuild_contracts import current_versions
            dg=self._task(drow)['intent']['grant']
            stored_dep=connection.execute('SELECT body,hash FROM grants WHERE id=?',(dg['grant_id'],)).fetchone()
            if not stored_dep or self._decode(stored_dep['body'],stored_dep['hash'])!=dg:reject('REBUILD_DEPENDENCY_FAILED')
            check_registration(dg,self.root)
            if dg['kind']=='rebuild_scoped_write':validate_scoped_write_grant(dg)
            else:validate_grant(dg,live=True)
            if current_versions(dg)!=receipt['end_versions']:reject('REBUILD_DEPENDENCY_FAILED')

    def record_rebuild_receipt(self,receipt,*,authority):
        from .rebuild_contracts import validate,require_authority,receipt_binding,reject
        receipt=validate(receipt,'receipt');authority=require_authority(authority);authority._authorize();lease_error=False
        with self._connection(True) as connection:
            rows=self._rebuild_rows(connection,receipt['task_id']);evidence={r['key']:r['data'] for r in rows}
            package=validate(evidence.get('rebuild-package'),'package');attempt=validate(evidence.get('rebuild-attempt-'+receipt['attempt_id']),'attempt')
            self._rebuild_check(connection,package,authority,receipt);receipt_binding(receipt,package,attempt)
            key='rebuild-receipt-'+receipt['receipt_id']
            if key in evidence:
                if evidence[key]!=receipt:reject('REBUILD_RECEIPT_CONFLICT')
                replayed=True
            else:
                if any(v['attempt_id']==receipt['attempt_id'] for k,v in evidence.items() if k.startswith('rebuild-receipt-')):reject('REBUILD_RECEIPT_AMBIGUOUS')
                task=self._task(connection.execute('SELECT * FROM tasks WHERE id=?',(receipt['task_id'],)).fetchone())
                try:
                    claim,_=self._lease_coverage(connection,task,start=receipt['started_at'],end=receipt['ended_at'],now=time.time())
                    if claim['generation']!=attempt['lease']['generation'] or claim['fence']!=attempt['lease']['fence']:reject('REBUILD_LEASE_REJECTED')
                except ServiceError as error:
                    if error.code!='REBUILD_LEASE_REJECTED':raise
                    if not task['cancel'] and task['status'] in {'running','validating'}:
                        connection.execute("UPDATE tasks SET status='requires_reconcile' WHERE id=?",(task['id'],))
                    self._append_event(connection,task['id'],'rebuild-reconcile-'+secrets.token_hex(8),{'reason':'invalid_lease_coverage','requires_reconcile':True,'at':time.time()});lease_error=True
                if not lease_error:
                    self._append_event(connection,task['id'],key,receipt)
                    if receipt['effect_state']=='unknown':
                        if not task['cancel'] and task['status'] in {'running','validating'}:
                            connection.execute("UPDATE tasks SET status='requires_reconcile' WHERE id=?",(task['id'],))
                        self._append_event(connection,task['id'],'rebuild-reconcile-'+receipt['receipt_id'],{'reason':'unknown_effect','requires_reconcile':True,'receipt_sha256':digest(receipt),'at':time.time()})
                replayed=False
            self._rebuild_check(connection,package,authority,receipt)
        if lease_error:raise ServiceError('REBUILD_LEASE_REJECTED',status=409,outcome='REQUIRES_RECONCILE')
        return {'replayed':replayed,'settled':False,**({'requires_reconcile':True} if receipt['effect_state']=='unknown' else {})}

    def _task(self, row):
        task = dict(row)
        task['payload'] = self._decode(task['payload'], task.pop('payload_hash'))
        task['intent'] = self._decode(task['intent'], task.pop('intent_hash'))
        if task['status'] not in STATES: raise ServiceError('TASK_STORE_CORRUPT', status=503)
        if task['result']:
            try:
                result = json.loads(task['result'])
                task['result'] = self._decode(canonical(result['data']).decode(), result['hash'])
            except (KeyError, TypeError, ValueError):
                raise ServiceError('TASK_STORE_CORRUPT', status=503) from None
        else: task['result'] = None
        task['human_acceptance'] = 'pending'
        return task

    @staticmethod
    def _selected_scope(selected_grant_ids):
        if selected_grant_ids is None:return '', ()
        if (type(selected_grant_ids) not in (list,tuple) or not selected_grant_ids
                or len(selected_grant_ids)>MAX_TASKS):
            raise ServiceError('ORDINARY_RUNTIME_SCOPE_REJECTED',status=403)
        ids=tuple(stable_id(ident)for ident in selected_grant_ids)
        return (" AND CASE WHEN json_valid(intent) THEN json_extract(intent,'$.grant.grant_id') END IN ("
                +','.join('?'for _ in ids)+')'),ids

    def task(self, ident, *, selected_grant_ids=None):
        stable_id(ident)
        with self._connection() as connection:
            scope,ids=self._selected_scope(selected_grant_ids)
            row=connection.execute('SELECT *FROM tasks WHERE id=?'+scope,(ident,*ids)).fetchone()
            if row is None and self._plan_schema(connection):row=connection.execute('SELECT *FROM plan_archive_tasks WHERE id=?'+scope,(ident,*ids)).fetchone()
            return self._task(row) if row else None

    def tasks(self, *, selected_grant_ids=None):
        scope,ids=self._selected_scope(selected_grant_ids)
        with self._connection() as connection:
            rows=list(connection.execute('SELECT *FROM tasks WHERE 1=1'+scope,ids))
            if self._plan_schema(connection):rows+=list(connection.execute('SELECT *FROM plan_archive_tasks WHERE 1=1'+scope,ids))
            return [self._task(row) for row in sorted(rows,key=lambda r:r['sequence'],reverse=True)]

    def _parallel_read_claim(self, connection, tasks):
        """Narrow concurrency exception, revalidated inside the atomic claim.

        Stored markers alone confer no authority: every participant must still
        have the exact registered grant and current Hub scope before its granted
        workspace is inspected. Unknown or mixed effects keep global seriality.
        """
        from .ordinary_tasks import check_registration, validate_grant, validate_spec
        from .writer_handoff import workspace_identity, workspaces_overlap
        scopes=[]; projects=set(); classification=None
        try:
            for task in tasks:
                grant=task['intent']['grant']; payload=task['payload']
                if (payload.get('kind')!='ordinary_project' or payload.get('effect')!='read_only'
                        or grant.get('kind')!='ordinary_project' or grant.get('effect')!='read_only'
                        or grant.get('classification') not in {'synthetic_fixture','real'}
                        or (classification is not None and grant.get('classification')!=classification)
                        or (grant.get('classification')=='real' and len(tasks)>2)
                        or task['project']!=grant.get('project_id') or task['project']in projects
                        or payload.get('project_id')!=task['project']
                        or payload.get('input_version')!=grant.get('input_version')
                        or task['intent'].get('grant_hash')!=digest(grant)
                        or task['intent'].get('registry_hash')!=grant.get('registry_hash')
                        or task['intent'].get('task_spec')!=payload):
                    return False
                classification=grant['classification']
                validate_spec(payload)
                current=connection.execute('SELECT * FROM grants WHERE id=?',(grant['grant_id'],)).fetchone()
                if (not current or current['version']!=grant['version']
                        or self._decode(current['body'],current['hash'])!=grant):return False
                check_registration(grant,self.root)
                validate_grant(grant,live=True)
                scope=workspace_identity(grant['root'])
                if any(workspaces_overlap(scope,other)for other in scopes):return False
                scopes.append(scope);projects.add(task['project'])
            return True
        except (ServiceError,OSError,ValueError,TypeError,KeyError):
            return False

    def _foreign_fence_scopes(self, connection, row, *, deadlines=None):
        """Read administrative metadata only; never decode a foreign task body.

        Exact recorded/current grant equality is required before any workspace
        identity probe. Unknown registrations keep the global fence closed.
        Expiry does not release a fence or authorize historical execution.
        """
        from .ordinary_tasks import check_registration
        from .task_service import root_identity
        from .writer_handoff import workspace_identity
        connection.create_function('scope_sha256',1,
            lambda value: hashlib.sha256(value.encode('utf-8')).hexdigest())
        metadata=connection.execute("""
            SELECT g.id,g.version,
              json_extract(g.body,'$.project_id') AS project,
              json_extract(g.body,'$.kind') AS kind,
              json_type(g.body,'$.kind') AS kind_type,
              json_extract(g.body,'$.checks') AS checks,
              json_extract(g.body,'$.isolated_root') AS isolated_root,
              json_extract(g.body,'$.writable_scope') AS writable_scope,
              json_extract(g.body,'$.prohibited') AS prohibited,
              json_extract(g.body,'$.root') AS root,
              json_extract(g.body,'$.root_identity') AS root_identity,
              json_extract(g.body,'$.registry_hash') AS registry_hash,
              json_extract(g.body,'$.story_exchange.canonical_root') AS canonical_root,
              json_extract(g.body,'$.story_exchange.canonical_root_identity') AS canonical_identity,
              json_extract(g.body,'$.story_exchange.registry_hash') AS canonical_registry
            FROM tasks t JOIN grants g ON g.id=CASE WHEN json_valid(t.intent)
                THEN json_extract(t.intent,'$.grant.grant_id') END
            WHERE t.id=? AND json_valid(g.body)
              AND json_extract(t.intent,'$.grant')=g.body
              AND json_extract(t.intent,'$.grant_hash')=g.hash
              AND scope_sha256(g.body)=g.hash
              AND scope_sha256(t.intent)=t.intent_hash
              AND json_extract(g.body,'$.grant_id')=g.id
              AND json_extract(g.body,'$.version')=g.version
              AND json_extract(g.body,'$.source')='verified_owner_decision'
              AND json_extract(g.body,'$.auth')='ChatGPT'
        """,(row['id'],)).fetchone()
        if not metadata or metadata['project']!=row['project']:
            raise ServiceError('TASK_FOREIGN_SCOPE_UNKNOWN',status=409)
        stable_id(metadata['project']);stable_id(metadata['id'])
        if metadata['kind']=='ordinary_project':
            roots=[(metadata['root'],metadata['root_identity'])]
            registry_hash=metadata['registry_hash'];canonical_root=metadata['root']
        elif metadata['kind']=='story_exchange':
            roots=[(metadata['root'],metadata['root_identity']),
                   (metadata['canonical_root'],metadata['canonical_identity'])]
            registry_hash=metadata['canonical_registry'];canonical_root=metadata['canonical_root']
        elif metadata['kind_type'] is None:
            if metadata['checks']=='["csp_css_trial"]':
                return self._resolved_terminal_scopes(connection,row['id'],deadlines=deadlines)
            # Supported legacy fixture grants predate a kind/registry hash.
            # This only proves a conservative unresolved-workspace fence; it
            # never validates a task, command, fingerprint or historical result.
            from .task_service import GRANT_FIELDS
            keys={item[0]for item in connection.execute(
                'SELECT key FROM grants,json_each(grants.body) WHERE grants.id=?',(metadata['id'],))}
            if (keys not in (GRANT_FIELDS,GRANT_FIELDS|{'approved_commands'})
                    or metadata['checks']!='["fixture_sample"]' or metadata['isolated_root']!=1
                    or metadata['writable_scope']!='["."]' or metadata['prohibited']!='[]'):
                raise ServiceError('TASK_FOREIGN_SCOPE_UNKNOWN',status=409)
            from .project_service import ProjectService
            service=ProjectService(self.root);resolver=service._resolver();resolver._check_registry()
            eligible,_=service._management_context(resolver)
            project=resolver.projects.get(metadata['project'])
            if (metadata['project']not in eligible or not project
                    or project.get('root_path')!=metadata['root']):
                raise ServiceError('TASK_FOREIGN_SCOPE_UNKNOWN',status=409)
            roots=[(metadata['root'],metadata['root_identity'])]
            canonical_root=metadata['root'];registry_hash=resolver.authority['registry_hash']
        else:raise ServiceError('TASK_FOREIGN_SCOPE_UNKNOWN',status=409)
        for root,identity in roots:
            if (type(root)is not str or not Path(root).is_absolute() or '..'in Path(root).parts
                    or type(identity)is not str or len(identity)!=64):
                raise ServiceError('TASK_FOREIGN_SCOPE_UNKNOWN',status=409)
        check_registration({'project_id':metadata['project'],'root':canonical_root,
                            'registry_hash':registry_hash},self.root)
        scopes=[]
        for root,identity in roots:
            if root_identity(root)!=identity:raise ServiceError('TASK_FOREIGN_SCOPE_UNKNOWN',status=409)
            scopes.append(workspace_identity(root))
        return scopes

    @staticmethod
    def _terminal_process_closed(pid, start_ticks):
        """A missing/reused exact historical identity is closed; unknown is not."""
        if (type(pid)is not int or pid<=0 or type(start_ticks)is not str
                or not start_ticks.isdecimal() or int(start_ticks)<=0
                or str(int(start_ticks))!=start_ticks):
            raise ServiceError('TERMINAL_SCOPE_PROCESS_UNKNOWN',status=409)
        try:
            fields=Path('/proc',str(pid),'stat').read_text().rsplit(') ',1)[1].split()
            current=fields[19]
            if not current.isdecimal():raise ValueError()
        except FileNotFoundError:return
        except (OSError,IndexError,ValueError):
            raise ServiceError('TERMINAL_SCOPE_PROCESS_UNKNOWN',status=409)from None
        if current==start_ticks and fields[0]!='Z':
            raise ServiceError('TERMINAL_SCOPE_PROCESS_ACTIVE',status=409)

    def _terminal_scope_snapshot(self, connection, ident, *, before_sequence=None):
        """Exact legacy CSS administrative metadata, never a historical result check.

        All task columns bind through raw-body hashes. Only process identities
        are extracted from old events; task payload/result/intent are not decoded.
        The canonical directory identity is CURRENT, not a repaired old grant.
        """
        from .css_trial import FIELDS as css_fields
        from .task_service import GRANT_FIELDS,root_identity
        from .project_service import ProjectService
        from .ordinary_tasks import check_registration
        from .writer_handoff import workspace_identity
        connection.create_function('scope_sha256',1,lambda value:
            hashlib.sha256(value.encode('utf-8')).hexdigest()if type(value)is str else None)
        row=connection.execute("""
            SELECT t.sequence,t.id,t.project,t.parent,t.status,t.owner,t.lease_until,
              t.thread_id,t.turn_id,t.provider_status,t.cancel,t.created,
              t.payload_hash,t.intent_hash,scope_sha256(t.payload) payload_bytes,
              scope_sha256(t.intent) intent_bytes,scope_sha256(t.result) result_bytes,
              g.id grant_id,g.version grant_version,g.hash grant_hash,
              json_extract(g.body,'$.root') root,
              json_extract(g.body,'$.root_identity') root_identity,
              json_extract(g.body,'$.css_trial.canonical_root') canonical_root
            FROM tasks t JOIN grants g ON g.id=CASE WHEN json_valid(t.intent)
                THEN json_extract(t.intent,'$.grant.grant_id') END
            WHERE t.id=? AND json_valid(g.body)
              AND json_extract(t.intent,'$.grant')=g.body
              AND json_extract(t.intent,'$.grant_hash')=g.hash
              AND scope_sha256(g.body)=g.hash AND scope_sha256(t.intent)=t.intent_hash
              AND json_extract(g.body,'$.grant_id')=g.id
              AND json_extract(g.body,'$.version')=g.version
              AND json_extract(g.body,'$.project_id')=t.project
              AND json_extract(g.body,'$.task_id')=t.id
              AND json_extract(g.body,'$.source')='verified_owner_decision'
              AND json_extract(g.body,'$.auth')='ChatGPT'
              AND json_type(g.body,'$.kind') IS NULL
              AND json_extract(g.body,'$.checks')='["csp_css_trial"]'
              AND json_extract(g.body,'$.isolated_root')=1
              AND json_extract(g.body,'$.writable_scope')='["."]'
              AND json_extract(g.body,'$.prohibited')='[]'
              AND json_type(g.body,'$.css_trial')='object'
        """,(stable_id(ident),)).fetchone()
        now=time.time()
        if (not row or row['status']!='requires_reconcile' or row['provider_status']!='completed'
                or not row['owner'] or row['cancel'] or not row['thread_id'] or not row['turn_id']
                or type(row['lease_until'])not in (int,float) or not math.isfinite(row['lease_until'])
                or row['lease_until']>=now or row['payload_hash']!=row['payload_bytes']):
            raise ServiceError('TERMINAL_SCOPE_FENCE_CHANGED',status=409)
        keys={v[0]for v in connection.execute(
            'SELECT key FROM grants,json_each(grants.body) WHERE grants.id=?',(row['grant_id'],))}
        nested={v[0]for v in connection.execute(
            "SELECT key FROM grants,json_each(grants.body,'$.css_trial') WHERE grants.id=?",(row['grant_id'],))}
        if keys!=GRANT_FIELDS|{'css_trial'} or nested!=css_fields:
            raise ServiceError('TERMINAL_SCOPE_UNKNOWN_SCOPE',status=409)
        for name in ('root','canonical_root'):
            value=row[name]
            if type(value)is not str or not Path(value).is_absolute() or '..'in Path(value).parts:
                raise ServiceError('TERMINAL_SCOPE_UNKNOWN_SCOPE',status=409)
        service=ProjectService(self.root);resolver=service._resolver();resolver._check_registry()
        registration={'project_id':row['project'],'root':row['canonical_root'],
                      'registry_hash':resolver.authority['registry_hash']}
        # Eligibility and exact canonical registration precede both root probes.
        check_registration(registration,self.root)
        try:
            if root_identity(row['root'])!=row['root_identity']:
                raise ServiceError('TERMINAL_SCOPE_ROOT_CHANGED',status=409)
            scopes=[workspace_identity(row[name])for name in ('root','canonical_root')]
        except OSError:
            raise ServiceError('TERMINAL_SCOPE_ROOT_CHANGED',status=409)from None
        events=[];processes=[];dispatch=set();children=set()
        for event in connection.execute('SELECT * FROM events WHERE task_id=? ORDER BY sequence',(ident,)):
            if before_sequence is not None and event['sequence']>=before_sequence:continue
            if (type(event['body'])is not str or type(event['hash'])is not str
                    or hashlib.sha256(event['body'].encode()).hexdigest()!=event['hash']):
                raise ServiceError('TERMINAL_SCOPE_EVENT_CHANGED',status=409)
            events.append(dict(event))
            key=event['event_key']
            if key.startswith(('dispatch-intent-','stdio-child-')):
                try:
                    value=json.loads(event['body'])
                    if key.startswith('dispatch-intent-'):
                        owner=key[len('dispatch-intent-'):];dispatch.add(owner)
                        identity=value['worker']
                        if identity.get('owner')!=owner:raise ValueError()
                    else:
                        owner=key[len('stdio-child-'):];children.add(owner);identity=value
                    pid,ticks=identity['pid'],identity['start_ticks']
                    self._terminal_process_closed(pid,ticks)
                except (ValueError,TypeError,KeyError,AttributeError):
                    raise ServiceError('TERMINAL_SCOPE_PROCESS_UNKNOWN',status=409)from None
                processes.append({'event_key':key,'pid':pid,'start_ticks':ticks})
        if not dispatch or dispatch!=children or row['owner']not in dispatch:
            raise ServiceError('TERMINAL_SCOPE_PROCESS_UNKNOWN',status=409)
        check_registration(registration,self.root)
        try:
            if scopes!=[workspace_identity(row[name])for name in ('root','canonical_root')]:
                raise ServiceError('TERMINAL_SCOPE_ROOT_CHANGED',status=409)
        except OSError:
            raise ServiceError('TERMINAL_SCOPE_ROOT_CHANGED',status=409)from None
        # Do not persist old bodies in the receipt, even administrative evidence.
        history=[{key:e[key]for key in ('sequence','event_key','hash','received')}for e in events]
        return {'task_id':ident,'project_id':row['project'],'grant_hash':row['grant_hash'],
                'task_hash':digest(dict(row)),'events_hash':digest(history),
                'registry_hash':registration['registry_hash'],'thread_id':row['thread_id'],
                'turn_id':row['turn_id'],'root':row['root'],'scopes':scopes,'processes':processes}

    def terminal_scope_snapshot(self, ident):
        """Root-only preparation. No mutation, HTTP route or execution authority."""
        with self._connection()as connection:
            connection.execute('BEGIN')
            return self._terminal_scope_snapshot(connection,ident)

    @staticmethod
    def _terminal_native(observation, snapshot, now):
        fields={'source','cwd','thread_id','thread_status','turns','turns_complete','observed_at'}
        if (type(observation)is not dict or set(observation)!=fields
                or type(observation['source'])is not str
                or observation['source']not in {'codex_app.read_thread','app_server_stdio.thread/read'}
                or observation['cwd']!=snapshot['root'] or observation['thread_id']!=snapshot['thread_id']
                or type(observation['thread_status'])is not str
                or observation['thread_status']not in {'idle','notLoaded'}
                or type(observation['observed_at'])not in (int,float)
                or not math.isfinite(observation['observed_at'])
                or not 0<=now-observation['observed_at']<30
                or observation['turns_complete'] is not True
                or type(observation['turns'])is not list or not 1<=len(observation['turns'])<=32):
            raise ServiceError('TERMINAL_SCOPE_NATIVE_REJECTED',status=409)
        seen=set()
        for turn in observation['turns']:
            if (type(turn)is not dict or set(turn)!={'id','status'}
                    or type(turn['id'])is not str or not turn['id'] or turn['id']in seen
                    or type(turn['status'])is not str
                    or turn['status']not in {'completed','interrupted','cancelled'}):
                raise ServiceError('TERMINAL_SCOPE_NATIVE_REJECTED',status=409)
            seen.add(turn['id'])
        if observation['turns'][-1]!={'id':snapshot['turn_id'],'status':'completed'}:
            raise ServiceError('TERMINAL_SCOPE_NATIVE_REJECTED',status=409)

    def _terminal_receipt(self, connection, ident, event):
        receipt=self._decode(event['body'],event['hash']);now=time.time()
        fields={'version','action','actor','snapshot','native','issued_at','expires_at'}
        if (type(receipt)is not dict or set(receipt)!=fields or type(receipt['version'])is not int
                or receipt['version']!=1 or receipt['actor']!='trusted_local_owner'
                or receipt['action']!='terminal_scope_resolution_v1'
                or event['event_key']!='terminal-scope-resolution-v1-'+digest(receipt)
                or type(receipt['issued_at'])not in (int,float) or not math.isfinite(receipt['issued_at'])
                or not 0<=now-receipt['issued_at']<30
                or type(receipt['expires_at'])not in (int,float) or not math.isfinite(receipt['expires_at'])
                or not now<receipt['expires_at']<=receipt['issued_at']+30):
            raise ServiceError('TERMINAL_SCOPE_RECEIPT_REJECTED',status=409)
        snapshot=receipt['snapshot']
        snapshot_fields={'task_id','project_id','grant_hash','task_hash','events_hash','registry_hash',
                         'thread_id','turn_id','root','scopes','processes'}
        if type(snapshot)is not dict or set(snapshot)!=snapshot_fields or snapshot['task_id']!=ident:
            raise ServiceError('TERMINAL_SCOPE_RECEIPT_REJECTED',status=409)
        self._terminal_native(receipt['native'],snapshot,now)
        if (receipt['expires_at']!=receipt['native']['observed_at']+30
                or connection.execute('SELECT 1 FROM events WHERE task_id=? AND sequence>?',
                                      (ident,event['sequence'])).fetchone()
                or snapshot!=self._terminal_scope_snapshot(connection,ident,before_sequence=event['sequence'])):
            raise ServiceError('TERMINAL_SCOPE_FENCE_CHANGED',status=409)
        # Filesystem/Git probes can consume the remaining lifetime.
        self._terminal_native(receipt['native'],snapshot,time.time())
        if time.time()>=receipt['expires_at']:
            raise ServiceError('TERMINAL_SCOPE_RECEIPT_REJECTED',status=409)
        return receipt

    def _resolved_terminal_scopes(self, connection, ident, *, deadlines=None):
        event=connection.execute("SELECT * FROM events WHERE task_id=? AND event_key LIKE 'terminal-scope-resolution-v1-%' ORDER BY sequence DESC LIMIT 1",(ident,)).fetchone()
        if event is None:raise ServiceError('TASK_FOREIGN_SCOPE_UNKNOWN',status=409)
        receipt=self._terminal_receipt(connection,ident,event)
        if deadlines is not None:deadlines.append(receipt['expires_at'])
        return receipt['snapshot']['scopes']

    def record_terminal_scope_resolution(self, ident, proof):
        """Trusted Root adds a short-lived scope proof, never closes/replays a task.

        Source strings/hashes bind evidence; they do not authenticate a caller.
        Only the existing trusted backend may supply real native observations.
        Receipt expiry also applies at claim; renewals need fresh observations
        and a new snapshot covering the entire previous event history.
        """
        if (type(proof)is not dict or set(proof)!={'actor','action','snapshot','native'}
                or proof['actor']!='trusted_local_owner' or proof['action']!='terminal_scope_resolution_v1'):
            raise ServiceError('TERMINAL_SCOPE_AUTHORITY_REJECTED',status=409)
        with self._connection(True)as connection:
            prior=connection.execute("SELECT * FROM events WHERE task_id=? AND event_key LIKE 'terminal-scope-resolution-v1-%' ORDER BY sequence DESC LIMIT 1",(stable_id(ident),)).fetchone()
            if prior:
                value=self._decode(prior['body'],prior['hash'])
                if type(value)is not dict:raise ServiceError('TERMINAL_SCOPE_RECEIPT_REJECTED',status=409)
                if all(value.get(key)==item for key,item in proof.items()):
                    return self._terminal_receipt(connection,ident,prior)
            snapshot=self._terminal_scope_snapshot(connection,ident)
            if snapshot!=proof['snapshot']:raise ServiceError('TERMINAL_SCOPE_FENCE_CHANGED',status=409)
            now=time.time();self._terminal_native(proof['native'],snapshot,now)
            receipt={'version':1,**proof,'issued_at':now,'expires_at':proof['native']['observed_at']+30}
            self._append_event(connection,ident,'terminal-scope-resolution-v1-'+digest(receipt),receipt)
            return receipt

    def claim(self, owner, now=None, *, only_task_id=None, selected_grant_ids=None):
        stable_id(owner); now = time.time() if now is None else now
        selection,ids=self._selected_scope(selected_grant_ids)
        self.check_storage()
        with self._connection(True) as connection:
            # Expired claims never become queued: dispatch effects may already exist.
            connection.execute("UPDATE tasks SET status='requires_reconcile' WHERE status IN ('running','validating','waiting_input','waiting_approval') AND lease_until<?"+selection, (now,*ids))
            if only_task_id is not None:stable_id(only_task_id)
            rows = connection.execute("SELECT * FROM tasks WHERE status='queued'"+
                (" AND id=?"if only_task_id is not None else"")+selection+" ORDER BY sequence",
                (*((only_task_id,)if only_task_id is not None else()),*ids)).fetchall()
            for row in rows:
                parent = connection.execute('SELECT id,status,thread_id,turn_id,provider_status,cancel FROM tasks WHERE id=?'+selection, (row['parent'],*ids)).fetchone() if row['parent'] else None
                task = self._task(row)
                if task['payload'].get('kind')=='rebuild_project':continue
                if (row['parent'] or task['payload']['mode'] == 'busy_feedback') and not parent_binding_matches(task['intent'], parent):
                    connection.execute("UPDATE tasks SET status='requires_reconcile' WHERE id=?", (row['id'],)); continue
                if parent and parent['status'] not in TERMINAL: continue
                if connection.execute("SELECT 1 FROM tasks WHERE project=? AND status IN ('running','validating','waiting_input','waiting_approval','requires_reconcile') LIMIT 1", (row['project'],)).fetchone(): continue
                if selected_grant_ids is not None and not self._parallel_read_claim(connection,[task]):
                    continue
                # SQLite serializes claim decisions, including the real two-task
                # bound across executors. Only fully verified, same-class read
                # participants may execute in disjoint workspaces together.
                # An unresolved task fences its actual workspace, including
                # differently named projects and shared Git worktrees.
                active=connection.execute("SELECT "+("*"if selected_grant_ids is None else"id,project,status")+" FROM tasks WHERE status IN ('running','validating','waiting_input','waiting_approval','requires_reconcile')").fetchall()
                owned_active=active
                if selected_grant_ids is not None:
                    owned_ids={other['id']for other in connection.execute(
                        'SELECT id FROM tasks WHERE 1=1'+selection,ids)}
                    owned_active=connection.execute(
                        "SELECT * FROM tasks WHERE status IN ('running','validating','waiting_input','waiting_approval','requires_reconcile')"+selection,ids).fetchall()
                    owned_rows={other['id']:other for other in owned_active}
                    if any(other['id']not in owned_ids and other['status']!='requires_reconcile'for other in active):
                        continue
                running=[self._task(other)for other in owned_active if other['status']!='requires_reconcile']
                if running and not self._parallel_read_claim(connection,[task,*running]):continue
                terminal_deadlines=[]
                if active:
                    from .writer_handoff import workspace_identity,workspaces_overlap
                    try:scope=workspace_identity(_effect_root(task['intent']['grant']))
                    except (ServiceError,OSError):
                        connection.execute("UPDATE tasks SET status='requires_reconcile' WHERE id=?",(row['id'],));continue
                    conflict=False
                    for other in active:
                        if selected_grant_ids is not None and other['id']not in owned_ids:
                            try:foreign_scopes=self._foreign_fence_scopes(connection,other,deadlines=terminal_deadlines)
                            except (ServiceError,OSError,ValueError,TypeError,KeyError):
                                conflict=True;break
                            if any(workspaces_overlap(scope,foreign)for foreign in foreign_scopes):
                                conflict=True;break
                            continue
                        own_row=other if selected_grant_ids is None else owned_rows[other['id']]
                        other_root=_effect_root(self._task(own_row)['intent']['grant'])
                        if other_root==scope['canonical_root']:conflict=True;break
                        try:other_scope=workspace_identity(other_root)
                        except FileNotFoundError:
                            # An absent historical root cannot alias an unrelated
                            # live root. Its own project remains fenced above.
                            a,b=Path(other_root),Path(scope['canonical_root'])
                            if a.is_relative_to(b)or b.is_relative_to(a):conflict=True
                            continue
                        except (ServiceError,OSError):
                            # An existing but aliased/unverifiable workspace
                            # cannot be treated as an absent historical root.
                            conflict=True;break
                        if workspaces_overlap(scope,other_scope):conflict=True;break
                    if conflict:continue
                if parent and parent['status'] != 'checks_complete':
                    connection.execute("UPDATE tasks SET status='requires_reconcile' WHERE id=?", (row['id'],)); continue
                if parent:
                    if parent['provider_status']!='completed':
                        connection.execute("UPDATE tasks SET status='requires_reconcile' WHERE id=?",(row['id'],));continue
                if any(time.time()>=expiry for expiry in terminal_deadlines):continue
                connection.execute("UPDATE tasks SET owner=?,lease_until=?,status='running' WHERE id=?", (owner, now+15, row['id']))
                return self._task(connection.execute('SELECT * FROM tasks WHERE id=?', (row['id'],)).fetchone())
            return None

    def update(self, ident, owner, **values):
        if not values or set(values) - {'status','thread_id','turn_id','provider_status','result'}:
            raise ServiceError('INVALID_TASK_UPDATE')
        if 'status' in values and values['status'] not in STATES: raise ServiceError('INVALID_TASK_UPDATE')
        if 'result' in values:
            result_bytes=len(canonical(values['result']))
            values['result'] = canonical({'data':values['result'],'hash':digest(values['result'])}).decode()
        with self._connection(True) as connection:
            row = connection.execute('SELECT * FROM tasks WHERE id=? AND owner=?',(ident,owner)).fetchone()
            if not row or row['status'] in TERMINAL or row['lease_until'] < time.time():
                raise ServiceError('TASK_OWNER_CONFLICT', status=409)
            if 'result'in values:
                limit=131072 if self._task(row)['payload'].get('kind')=='ordinary_project' else 16384
                if result_bytes>limit:raise ServiceError('TASK_RESULT_CAPACITY',status=507)
            transitions = {'running':{'running','waiting_input','waiting_approval','validating','failed','cancelled','requires_reconcile'},
                'waiting_input':{'running','failed','cancelled','requires_reconcile'},
                'waiting_approval':{'running','failed','cancelled','requires_reconcile'},
                'validating':{'validating','checks_complete','failed','cancelled','requires_reconcile'}}
            if values.get('status',row['status']) not in transitions[row['status']]:
                raise ServiceError('TASK_TRANSITION_REJECTED', status=409)
            # BEGIN IMMEDIATE serializes this observation with cancellation.
            # A cancellation that committed first cannot publish success.
            if values.get('status')=='checks_complete' and row['cancel']:
                raise ServiceError('TASK_CANCEL_AFTER_EFFECT',status=409)
            for key in ('thread_id','turn_id'):
                if key in values and row[key] is not None and values[key] != row[key]:
                    raise ServiceError('TASK_BINDING_CONFLICT', status=409)
            # Column names come only from the fixed allowlist, values remain parameters.
            renewed_at=time.time()
            self._renew_rebuild(connection,row,renewed_at,values.get('status'))
            columns = ','.join(key+'=?' for key in values)
            success_guard=' AND cancel=0'if values.get('status')=='checks_complete'else''
            cursor = connection.execute('UPDATE tasks SET '+columns+',lease_until=? WHERE id=? AND owner=?'+success_guard, (*values.values(),renewed_at+15,ident,owner))
            if cursor.rowcount != 1: raise ServiceError('TASK_OWNER_CONFLICT', status=409)
            task=self._task(row);marker=task['intent'].get('normal_dispatch_budget')
            if marker and ('thread_id'in values or 'turn_id'in values):
                from .dispatch_budget import need
                link=self._normal_dispatch_event(connection,ident,'reserved',marker)
                need(link is not None,'DISPATCH_RESERVATION_REQUIRED')
                need(link['worker']['owner']==owner,'NORMAL_DISPATCH_WORKER_REJECTED')
                if 'turn_id'in values:
                    need(self._normal_dispatch_event(connection,ident,'start',marker)is not None,'NORMAL_DISPATCH_ALREADY_CONSUMED')
                thread=values.get('thread_id',row['thread_id'])
                if link['executor'].get('thread_id')is not None:
                    need(link['executor']['thread_id']==thread,'DISPATCH_RESERVATION_MISMATCH')
                native={'marker':marker,'worker':link['worker'],'thread_id':thread,
                        'turn_id':values.get('turn_id',row['turn_id']),'session_id':None}
                self._append_event(connection,ident,'normal-dispatch-native-'+digest(native),native)

    def assert_turn_start(self,ident,owner,expected_task,grant,thread_id,*,normal_budget_binding=None,worker_identity=None):
        """Final persisted pre-RPC check, not a lock across provider acceptance.

        The random claim owner, live lease, immutable intent/payload and native
        thread bind the existing worker fence. One joined observation includes
        grant revocation; no new execution authority or RPC is introduced.
        """
        bound=expected_task['intent'].get('normal_dispatch_budget')is not None or normal_budget_binding is not None
        observed=[]
        def guard():
            if bound:
                from .dispatch_budget import need
                _,controls=self._normal_dispatch_check(connection,ident,owner,expected_task,grant,worker_identity,normal_budget_binding,starting=True)
                need(controls==observed[0],'DISPATCH_CONTROL_CHANGED')
        with self._connection(bound,guard=guard if bound else None)as connection:
            row=connection.execute('SELECT t.*,g.body AS live_grant_body,g.hash AS live_grant_hash '
                'FROM tasks t LEFT JOIN grants g ON g.id=? WHERE t.id=?',
                (grant['grant_id'],stable_id(ident))).fetchone()
            if not row:raise ServiceError('TASK_NOT_FOUND',status=404)
            if row['cancel']:raise ServiceError('TASK_CANCEL_BEFORE_DISPATCH',status=409)
            self._task(row) # Preserve existing canonical payload/intent/result hash validation.
            if (row['owner']!=owner or row['status']!='running' or not row['lease_until']or row['lease_until']<=time.time()
                    or row['intent_hash']!=digest(expected_task['intent'])or row['payload_hash']!=digest(expected_task['payload'])
                    or row['thread_id']!=thread_id or row['turn_id']is not None):
                raise ServiceError('TASK_LEASE_LOST',status=409,outcome='UNKNOWN')
            if (row['live_grant_body']is None or row['live_grant_hash']!=digest(grant)
                    or self._decode(row['live_grant_body'],row['live_grant_hash'])!=grant or grant['expires_at']<=time.time()):
                raise ServiceError('GRANT_VERSION_STALE',status=409)

            if bound:
                from .dispatch_budget import need
                link,controls=self._normal_dispatch_check(connection,ident,owner,expected_task,grant,worker_identity,normal_budget_binding,starting=True)
                observed.append(controls)
                marker=link['marker']
                need(self._normal_dispatch_event(connection,ident,'reserved',marker)==link,'NORMAL_DISPATCH_WORKER_REJECTED')
                need(self._normal_dispatch_event(connection,ident,'start',marker)is None,'NORMAL_DISPATCH_ALREADY_CONSUMED')
                self._append_event(connection,ident,self._normal_dispatch_key(connection,ident,'start',marker),{**link,'thread_id':thread_id})

    def heartbeat(self, ident, owner):
        with self._connection(True) as connection:
            now=time.time()
            row=connection.execute('SELECT * FROM tasks WHERE id=? AND owner=?',(ident,owner)).fetchone()
            self._renew_rebuild(connection,row,now)
            cursor=connection.execute('UPDATE tasks SET lease_until=? WHERE id=? AND owner=? AND lease_until>=? AND status NOT IN (?,?,?,?,?)',
                (now+15,ident,owner,now,*sorted(TERMINAL)))
            if cursor.rowcount!=1: raise ServiceError('TASK_OWNER_CONFLICT',status=409)

    def perform_local_promotion(self, ident, expected_result_hash, action):
        """Serialize a trusted CAS with cancellation after a durable intent.

        An uncertain commit after the file effect requires reconciliation, never
        another model turn. This method has no HTTP route.
        """
        with self._connection(True)as connection:
            row=connection.execute('SELECT *FROM tasks WHERE id=?',(stable_id(ident),)).fetchone()
            task=self._task(row)if row else None
            if (not task or task['status']!='validating'or task['owner']is not None or task['cancel']or
                task['provider_status']!='completed'or not _local_ready(task)or
                digest(task['result'])!=expected_result_hash):
                raise ServiceError('TASK_LOCAL_PROMOTION_REJECTED',status=409)
            g=connection.execute('SELECT *FROM grants WHERE id=?',(task['intent']['grant']['grant_id'],)).fetchone()
            if not g or self._decode(g['body'],g['hash'])!=task['intent']['grant']:
                raise ServiceError('CSS_PROMOTION_GRANT_STALE',status=409)
            prefix=_local_kind(task)
            if prefix=='story'and task['intent']['grant']['expires_at']<=time.time():raise ServiceError('GRANT_EXPIRED',status=409)
            if not connection.execute('SELECT 1 FROM events WHERE task_id=?AND event_key=?',(ident,prefix+'-promotion-intent')).fetchone()or \
                connection.execute('SELECT 1 FROM events WHERE task_id=?AND event_key=?',(ident,prefix+'-promoted')).fetchone():
                raise ServiceError('CSS_PROMOTION_REQUIRES_RECONCILE',status=409)
            result=action()
            self._append_event(connection,ident,prefix+'-promoted',result)
            return result

    def handoff_validation(self, ident, owner, expected_result_hash):
        """Owned worker has closed its adapter; Root completes local checks."""
        with self._connection(True)as connection:
            row=connection.execute('SELECT *FROM tasks WHERE id=? AND owner=?',(stable_id(ident),owner)).fetchone()
            task=self._task(row)if row else None
            if (not task or task['status']!='validating' or task['lease_until']<time.time() or task['cancel'] or
                task['provider_status']!='completed' or not task['thread_id']or not task['turn_id']or
                not _local_ready(task)or digest(task['result'])!=expected_result_hash or
                task['result'].get('source_promoted')is not False):
                raise ServiceError('TASK_VALIDATION_HANDOFF_REJECTED',status=409)
            self._append_event(connection,ident,'local-validation-handoff',{'result_hash':expected_result_hash,
                'thread_id':task['thread_id'],'turn_id':task['turn_id'],'adapter_closed':True})
            connection.execute('UPDATE tasks SET owner=NULL,lease_until=NULL WHERE id=? AND owner=?',(ident,owner))

    def finalize_local_validation(self, ident, expected_result_hash, result):
        """Trusted local operation, never exposed as an HTTP mutation."""
        if len(canonical(result))>16384:raise ServiceError('TASK_RESULT_CAPACITY',status=507)
        with self._connection(True)as connection:
            row=connection.execute('SELECT *FROM tasks WHERE id=?',(stable_id(ident),)).fetchone()
            task=self._task(row)if row else None
            if (not task or task['status']!='validating'or task['owner']is not None or task['cancel']or
                task['provider_status']!='completed'or not _local_ready(task)or
                digest(task['result'])!=expected_result_hash or
                result.get('source_promoted')is not True or result.get('validation_scope')!='canonical_project' or
                not result.get('checks')or any(c.get('exit')!=0 for c in result['checks'])):
                raise ServiceError('TASK_LOCAL_FINALIZATION_REJECTED',status=409)
            if _local_kind(task)=='story':
                event=connection.execute('SELECT *FROM events WHERE task_id=?AND event_key=?',(ident,'story-promoted')).fetchone()
                promoted=self._decode(event['body'],event['hash'])if event else None
                binding=result.get('source_binding',{});scope=task['intent']['grant']['story_exchange']
                if (not promoted or binding!={'workspace_revision':promoted['workspace_revision'],
                        'workspace_sha256':promoted['workspace_sha256'],'request_id':scope['request_id'],
                        'request_sha256':scope['request_sha256'],'output_sha256':task['result']['output_sha256']}
                    or result.get('output_refs')!=promoted['output_refs']
                    or result.get('import_result_sha256')!=promoted['import_result_sha256']):
                    raise ServiceError('TASK_LOCAL_FINALIZATION_REJECTED',status=409)
            self._append_event(connection,ident,'local-validation-complete',{'before_result_hash':expected_result_hash,
                'result_hash':digest(result),'human_acceptance':'pending'})
            cursor=connection.execute("UPDATE tasks SET status='checks_complete',result=? WHERE id=? AND status='validating'AND cancel=0 AND owner IS NULL",
                (canonical({'data':result,'hash':digest(result)}).decode(),ident))
            if cursor.rowcount!=1:raise ServiceError('TASK_LOCAL_FINALIZATION_REJECTED',status=409)

    def cancel(self, ident, *, owner_context=None, authorize=None, worker_owner=None):
        if authorize is not None:authorize()
        with self._connection(True,guard=authorize) as connection:
            row = connection.execute('SELECT * FROM tasks WHERE id=?', (stable_id(ident),)).fetchone()
            if not row: raise ServiceError('TASK_NOT_FOUND', status=404)
            task=self._task(row)
            if worker_owner is not None and (row['owner']!=worker_owner or not row['lease_until'] or row['lease_until']<=time.time()):
                raise ServiceError('TASK_LEASE_LOST',status=409)
            if task['intent'].get('grant',{}).get('kind')=='ordinary_project':
                if worker_owner is None and (owner_context is None or authorize is None):
                    raise ServiceError('OWNER_AUTH_REQUIRED',status=403)
                if owner_context is not None and task['intent'].get('owner_context')!=owner_context:raise ServiceError('TASK_CONTROL_OWNER_REJECTED',status=403)
            if row['status'] not in TERMINAL:
                connection.execute("UPDATE tasks SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE id=?",(ident,))

    def _normal_known_history(self,observation,*,fresh=True):
        """Bounded host projection of existing official metadata, not authority."""
        from .dispatch_budget import need,hash_value,finite
        fields={'schema_version','source','root','thread_id','thread_status','turns_complete',
                'next_cursor','sort_direction','turns','resume_evidence_sha256','observed_at'}
        need(set(observation)==fields and observation['schema_version']=='normal-terminal-history/1'
             and observation['source']=='app_server_stdio.thread/read+thread/turns/list'
             and observation['thread_status']=='idle'and observation['turns_complete']is True
             and observation['next_cursor']is None and observation['sort_direction']=='desc'
             and hash_value(observation['resume_evidence_sha256']), 'NORMAL_HISTORY_REJECTED')
        def native_id(value):
            # Native opaque IDs include digit-leading UUIDs; task IDs do not.
            # Keep this local: no paths, controls, aliases or unbounded strings.
            need(type(value)is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',value)is not None,
                 'NORMAL_HISTORY_REJECTED')
        native_id(observation['thread_id']);rows=observation['turns']
        need(type(rows)is list and 1<=len(rows)<=32 and len(canonical(observation))<=6144,'NORMAL_HISTORY_REJECTED')
        seen=set();previous=None
        for row in rows:
            need(type(row)is dict and set(row)=={'id','status','startedAt','completedAt','itemsView'},'NORMAL_HISTORY_REJECTED')
            native_id(row['id']);need(row['id']not in seen and row['status']in {'completed','failed','interrupted'}
                and row['itemsView']=='notLoaded'and finite(row['startedAt'])and finite(row['completedAt'])
                and 0<=row['startedAt']<=row['completedAt']and(previous is None or row['startedAt']<previous), 'NORMAL_HISTORY_REJECTED')
            seen.add(row['id']);previous=row['startedAt']
        need(finite(observation['observed_at'])and(not fresh or 0<=time.time()-observation['observed_at']<30),'NORMAL_HISTORY_REJECTED')
        return rows

    def _normal_terminal_witness(self,db,task,observation,marker,events):
        """Verify this generation and require separately pinned Root authority.

        Existing legacy permission-table evidence and broad readOnly snapshots
        cannot satisfy this scope/capability chain. No native API is invoked.
        """
        from .dispatch_budget import need,hash_value
        archived=[];prior=[]
        for e in events:
            value=self._decode(e['body'],e['hash'])
            if e['event_key'].startswith('reconcile-')and value.get('new_marker')==marker:
                prior.append(value)
        need(len(prior)<=1,'NORMAL_HISTORY_REJECTED')
        if prior:
            value=prior[0]
            if value.get('verified_history_sha256')is not None:
                archived=self._normal_known_history(value['observation'],fresh=False)
                need(digest(archived)==value['verified_history_sha256'],'NORMAL_HISTORY_REJECTED')
            else:need(value.get('original_turn_id')is None,'NORMAL_HISTORY_REJECTED')
        reserved=self._normal_dispatch_event(db,task['id'],'reserved',marker)
        need(reserved is not None and task['owner']==reserved['worker']['owner']
             and reserved['intent_sha256']==digest(task['intent'])and reserved['payload_sha256']==digest(task['payload']), 'NORMAL_GENERATION_REJECTED')
        started=self._normal_dispatch_event(db,task['id'],'start',marker)
        need(started is None or task['turn_id']is not None,'NORMAL_DISPATCH_START_UNCERTAIN')
        rows=self._normal_known_history(observation)
        need(task['thread_id']==observation['thread_id']and task['status']in {'failed','requires_reconcile'},'NORMAL_HISTORY_REJECTED')
        if started is None:
            need(task['turn_id']is None and rows==archived,'NORMAL_HISTORY_REJECTED')
            return {'operation':'normal_known_history_recovery','marker':marker,'task_sha256':digest(task),
                'observation_sha256':digest(observation),'prior_archive_sha256':digest(prior),
                'capability_sha256':None},None
        reserved=self._normal_dispatch_event(db,task['id'],'reserved',marker)
        need(reserved is not None and task['owner']==reserved['worker']['owner']and started=={**reserved,'thread_id':task['thread_id']}
             and reserved['intent_sha256']==digest(task['intent'])and reserved['payload_sha256']==digest(task['payload']), 'NORMAL_GENERATION_REJECTED')
        need(rows[0]['id']==task['turn_id']and rows[1:]==archived,'NORMAL_HISTORY_REJECTED')
        native=[];capability=[];stdio=[]
        for event in events:
            v=self._decode(event['body'],event['hash'])
            if event['event_key'].startswith('normal-dispatch-native-')and v.get('marker')==marker and v.get('turn_id')is not None:native.append((event,v))
            if event['event_key']=='capability-proof-'+task['owner']:capability.append((event,v))
            if event['event_key']=='stdio-child-'+task['owner']:stdio.append((event,v))
        need(len(native)==len(capability)==len(stdio)==1,'NORMAL_CAPABILITY_UNVERIFIED')
        ne,native=native[0];ce,proof=capability[0];se,child=stdio[0]
        start=next(e for e in events if e['event_key'].startswith('normal-dispatch-start')and self._decode(e['body'],e['hash']).get('marker')==marker)
        need(se['sequence']<ce['sequence']<start['sequence']<ne['sequence']and native['worker']==reserved['worker']
             and native['thread_id']==task['thread_id']and native['turn_id']==task['turn_id'],'NORMAL_GENERATION_REJECTED')
        grant=task['intent']['grant']
        keys={'schema_version','thread_id','root_identity','input_version','input_files_sha256','native_profile',
              'binary_sha256','schema_sha256','process'}
        need(type(proof)is dict and set(proof)==keys and proof['schema_version']=='ordinary-readonly-capability/1'
             and proof['thread_id']==task['thread_id']and proof['root_identity']==grant['root_identity']
             and proof['input_version']==grant['input_version']and proof['input_files_sha256']==digest(grant['input_files'])
             and proof['native_profile']=={'sandbox':'readOnly','approval_policy':'never','network_access':False,
                'exact_input_leaves_verified':True,'enabled_capabilities':[]}
             and hash_value(proof['binary_sha256'])and proof['schema_sha256']==child.get('schema_hash'), 'NORMAL_CAPABILITY_UNVERIFIED')
        process=proof['process']
        need(type(process)is dict and set(process)=={'pid','start_ticks','pgid'}and type(process['pid'])is int
             and type(process['pgid'])is int and process['pgid']==process['pid']and process['pid']>0
             and process['pid']==child.get('pid')and process['start_ticks']==child.get('start_ticks'), 'NORMAL_CAPABILITY_UNVERIFIED')
        self._terminal_process_closed(process['pid'],process['start_ticks'])
        try:os.killpg(process['pgid'],0)
        except ProcessLookupError:pass
        except OSError:raise ServiceError('TASK_RECONCILE_PROCESS_UNKNOWN',status=409)from None
        else:raise ServiceError('TASK_OWNER_STILL_ACTIVE',status=409)
        witness={'operation':'normal_terminal_readonly_recovery','marker':marker,'task_sha256':digest(task),
                 'observation_sha256':digest(observation),'prior_archive_sha256':digest(prior),
                 'capability_sha256':digest(proof),'native_sha256':digest(native),'process':process}
        return witness,process

    def reconcile_owned(self, ident, observation, *, normal_budget_binding=None,
                        expected_task_sha256=None, expected_marker=None, expected_events_sha256=None):
        """Root-only reconciliation; no HTTP route and no post-turn replay.

        Native proof must establish no dispatched turn, or a stopped owned turn
        with no file effects. Only Root supplies this fresh observation after
        exact old processes close; preserve failed checks/IDs before requeuing.
        """
        self.check_storage()
        if type(observation)is not dict:raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
        unstarted=observation.get('source')=='app_server_stdio.thread/list'
        known_history=observation.get('schema_version')=='normal-terminal-history/1'
        if known_history:self._normal_known_history(observation)
        empty_thread=not unstarted and not known_history and 'turns_complete'in observation
        expected={'source','root','thread_ids','archived_checked','observed_at'}if unstarted else \
            {'source','root','thread_id','thread_status','turns_complete','turns','observed_at'}if empty_thread else \
            {'source','root','thread_id','turn_id','thread_status','turn_status','observed_at'}
        if not known_history and (set(observation)!=expected or (unstarted and (observation['thread_ids']!=[] or observation['archived_checked']is not True)) or \
            (empty_thread and (observation['source']!='app_server_stdio.thread/read'
                or type(observation['thread_status'])is not str or observation['thread_status']not in {'idle','notLoaded'}
                or observation['turns_complete']is not True or type(observation['turns'])is not list or observation['turns']!=[]))or \
            (not unstarted and not empty_thread and (observation['source']!='app_server_stdio.thread/read' or observation['turn_status']not in {'interrupted','completed'} or observation['thread_status']not in {'idle','notLoaded'}))or \
            type(observation['observed_at'])not in {int,float} or not 0<=time.time()-observation['observed_at']<30):
            raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
        recovery=[]
        def guard():
            if recovery:
                verify,controls=recovery[0]
                if verify()!=controls:raise ServiceError('DISPATCH_CONTROL_CHANGED',status=409)
        with self._connection(True,guard=guard)as connection:
            row=connection.execute('SELECT * FROM tasks WHERE id=?',(stable_id(ident),)).fetchone()
            if not row:raise ServiceError('TASK_NOT_FOUND',status=404)
            task=self._task(row)
            ordinary=task['intent']['grant'].get('kind')=='ordinary_project'
            budget_bound=ordinary and task['intent'].get('normal_dispatch_budget')is not None
            if known_history and not budget_bound:raise ServiceError('NORMAL_HISTORY_REJECTED',status=409)
            if normal_budget_binding is not None and not budget_bound:
                raise ServiceError('NORMAL_DISPATCH_BINDING_REQUIRED',status=409)
            if task['cancel']:raise ServiceError('TASK_CANCELLED',status=409)
            if unstarted and (task['status']not in {'failed','requires_reconcile'} or task['cancel'] or \
                task['thread_id']is not None or task['turn_id']is not None or task['provider_status']is not None):
                raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            if empty_thread and (task['status']not in {'failed','requires_reconcile'}
                or not task['thread_id'] or task['thread_id']!=observation['thread_id']
                or task['turn_id']is not None or task['provider_status']is not None):
                raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            check_failure=not known_history and not unstarted and not empty_thread and observation['turn_status']=='completed'
            if not known_history and not unstarted and not empty_thread and (task['status']not in ({'failed'}if check_failure else{'cancelled','requires_reconcile'}) or not task['thread_id']or not task['turn_id']or
                task['thread_id']!=observation['thread_id']or task['turn_id']!=observation['turn_id']):
                raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            if check_failure and (task['provider_status']!='completed' or not task['result']or
                not any(c['exit']!=0 for c in task['result'].get('checks',[])) or task['result'].get('changed_paths')!=[]):
                raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            events=connection.execute('SELECT * FROM events WHERE task_id=? ORDER BY sequence',(ident,)).fetchall()
            if (unstarted or empty_thread) and not budget_bound and any(e['event_key'].startswith('turn-intent')for e in events):
                raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            worker_seen=False
            for event in events:
                value=self._decode(event['body'],event['hash'])
                process=value.get('worker')if event['event_key'].startswith('dispatch-intent')else value if event['event_key'].startswith('stdio-child')else None
                if (empty_thread or budget_bound) and event['event_key'].startswith(('dispatch-intent','stdio-child')):
                    if type(process)is not dict:raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
                    try:self._terminal_process_closed(process.get('pid'),process.get('start_ticks'))
                    except ServiceError as error:
                        raise ServiceError('TASK_OWNER_STILL_ACTIVE'if error.code=='TERMINAL_SCOPE_PROCESS_ACTIVE'
                            else'TASK_RECONCILE_PROOF_REJECTED',status=409)from None
                    worker_seen=worker_seen or event['event_key'].startswith('dispatch-intent')
                    continue
                if process:
                    try:current=Path('/proc/'+str(process['pid'])+'/stat').read_text().rsplit(')',1)[1].split()[19]
                    except FileNotFoundError:continue
                    if current==process['start_ticks']:raise ServiceError('TASK_OWNER_STILL_ACTIVE',status=409)
            if (empty_thread or budget_bound) and not worker_seen:raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            from .task_service import fingerprint,root_identity
            grant=task['intent']['grant']
            if empty_thread and not ordinary:
                from .task_service import validate_grant
                current=connection.execute('SELECT body,hash FROM grants WHERE id=?',(grant['grant_id'],)).fetchone()
                if not current or self._decode(current['body'],current['hash'])!=grant or digest(grant)!=task['intent']['grant_hash']:
                    raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
                validate_grant(grant,live=True)
            if ordinary:
                from . import ordinary_tasks
                # Registration and live grant identity precede every declared
                # source read. Never traverse a real ordinary project root.
                ordinary_tasks.check_registration(grant,self.root)
                current=connection.execute('SELECT body,hash FROM grants WHERE id=?',(grant['grant_id'],)).fetchone()
                if not current or self._decode(*current)!=grant or digest(grant)!=task['intent']['grant_hash']:
                    raise ServiceError('GRANT_VERSION_STALE',status=409)
                ordinary_tasks.validate_grant(grant,live=True)
            elif grant.get('kind')=='story_exchange':
                from .story_exchange import validate
                from .story_promotion import source_snapshot
                validate(grant,live=True);source_snapshot(grant,expected=True)
                if (unstarted or check_failure or Path(grant['root'],'output/result.json').exists()
                    or any(e['event_key'].startswith('reconcile-')for e in events)):
                    raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            elif fingerprint(grant['root'])!=task['intent']['preimage']:
                raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            if observation['root']!=grant['root'] or root_identity(grant['root'])!=grant['root_identity']:
                raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            if empty_thread and not 0<=time.time()-observation['observed_at']<30:
                raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            new_intent=task['intent']
            if budget_bound:
                from .dispatch_budget import NormalDispatchBinding,need,split,validate
                need(type(normal_budget_binding)is NormalDispatchBinding,'NORMAL_DISPATCH_BINDING_REQUIRED')
                old_marker=task['intent']['normal_dispatch_budget'];b=normal_budget_binding.snapshot()
                need(digest(task)==expected_task_sha256 and old_marker==expected_marker
                     and digest([{'sequence':e['sequence'],'key':e['event_key'],'data':self._decode(e['body'],e['hash'])}for e in events])==expected_events_sha256,
                     'TASK_RECONCILE_CAS_STALE')
                # Only this generation's start matters; unknown ID remains refused.
                started=self._normal_dispatch_event(connection,ident,'start',old_marker)
                need(known_history or (unstarted or empty_thread)and started is None,'NORMAL_DISPATCH_START_UNCERTAIN')
                witness=process=None
                if known_history:
                    witness,process=self._normal_terminal_witness(connection,task,observation,old_marker,events)
                elif any(e['event_key'].startswith('reconcile-')and self._decode(e['body'],e['hash']).get('verified_history_sha256')for e in events):
                    need(False,'NORMAL_HISTORY_REJECTED')
                marker=normal_budget_binding.marker(ident,grant);cap=normal_budget_binding._authority
                need(marker['lineage_id']==old_marker['lineage_id'] and marker['dispatch_id']!=old_marker['dispatch_id'],
                     'NORMAL_DISPATCH_RECOVERY_REJECTED')
                plan=self._plan_input(connection,b['lineage_id'],cap._plan)
                raw=connection.execute('SELECT body,hash FROM plan_inputs WHERE id=?',(b['lineage_id'],)).fetchone()
                _,budget=split(self._decode(*raw));need(budget is not None,'DISPATCH_POLICY_REQUIRED');validate(budget,b['lineage_id'])
                old=next((r for r in budget['reservations']if r['request']['dispatch_id']==old_marker['dispatch_id']),None)
                need(old is not None and old['sha256']==old_marker['request_sha256']
                     and old['request']['task_id']==ident and old['request']['package_sha256']is None
                     and old['request']['attempt_id']!=b['request']['attempt_id']
                     and any(c['resources_closed']is True for c in old['closures']), 'DISPATCH_CLOSURE_UNVERIFIED')
                need(not any(r['request']['dispatch_id']==marker['dispatch_id']or r['request']['attempt_id']==b['request']['attempt_id']for r in budget['reservations']),
                     'NORMAL_DISPATCH_RECOVERY_REJECTED')
                need(b['request']['executor']['thread_id']in (None,task['thread_id'])
                     and b['request']['seconds']>=grant['max_turn_seconds']
                     and time.time()+b['request']['seconds']<=b['request']['deadline']<=time.time()+900,
                     'DISPATCH_DEADLINE')
                if witness is not None:
                    witness={**witness,'closed_reservation_sha256':digest(old)}
                def verify():
                    if witness is not None:
                        need(cap.snapshot().get('resource_authority_sha256')==digest(witness)
                             and cap._resource(json.loads(canonical(witness)))is True,'NORMAL_CAPABILITY_UNVERIFIED')
                        if process is not None:
                            self._terminal_process_closed(process['pid'],process['start_ticks'])
                            try:os.killpg(process['pgid'],0)
                            except ProcessLookupError:pass
                            except OSError:need(False,'TASK_RECONCILE_PROCESS_UNKNOWN')
                            else:need(False,'TASK_OWNER_STILL_ACTIVE')
                    ordinary_tasks.check_registration(grant,self.root);ordinary_tasks.validate_grant(grant,live=True)
                    need(0<=time.time()-observation['observed_at']<30,'TASK_RECONCILE_PROOF_REJECTED')
                    need(time.time()+b['request']['seconds']<=b['request']['deadline']<=time.time()+900,'DISPATCH_DEADLINE')
                    for event in events:
                        value=self._decode(event['body'],event['hash'])
                        proc=value.get('worker')if event['event_key'].startswith('dispatch-intent')else value if event['event_key'].startswith('stdio-child')else None
                        if proc:self._terminal_process_closed(proc.get('pid'),proc.get('start_ticks'))
                    return cap.check(plan,budget['policy'],'reserve',b['request'])
                recovery.append((verify,verify()))
                new_intent={**task['intent'],'normal_dispatch_budget':marker}
            evidence={'observation':observation,'original_status':task['status'],'original_result':task['result'],
                'intent_hash':digest(task['intent']),'original_thread_id':task['thread_id'],'original_turn_id':task['turn_id'],
                **({'original_task':task,'original_intent':task['intent'],'new_marker':new_intent['normal_dispatch_budget'],
                    'closed_reservation_sha256':digest(old),'closure_hashes':[digest(c)for c in old['closures']],
                    **({'verified_history_sha256':digest(observation['turns']),'recovery_witness_sha256':digest(witness)}if known_history else{})}if budget_bound else {}),
                'effects_rolled_back':False,'action':'same_owned_thread_new_budget_after_verified_terminal_history'if known_history else
                'same_id_requeue_before_any_turn'if unstarted else
                'same_owned_thread_requeue_before_any_turn'if empty_thread else
                'same_owned_thread_new_turn_after_verified_check_failure'if check_failure else'same_owned_thread_new_turn_after_verified_interruption'}
            body=canonical(evidence).decode();hashed=digest(evidence)
            if len(body.encode())>8192 or connection.execute('SELECT count(*)FROM events').fetchone()[0]>=MAX_EVENTS:
                raise ServiceError('TASK_EVENT_CAPACITY',status=507)
            connection.execute('INSERT INTO events(task_id,event_key,body,hash,received)VALUES(?,?,?,?,?)',
                (ident,'reconcile-'+hashed,body,hashed,time.time()))
            connection.execute("UPDATE tasks SET intent=?,intent_hash=?,status='queued',owner=NULL,lease_until=NULL,result=NULL,turn_id=NULL,provider_status=NULL WHERE id=?",
                (canonical(new_intent).decode(),digest(new_intent),ident))

    def authorize_business_runtime_amendment(self, ident, *, authority, observation, grant):
        """One explicit owner amendment; retain the same interrupted task/thread.

        This backend-only operation preserves the entire previous intent and
        failed outcome before atomically enrolling the longer bounded attempt.
        """
        from copy import deepcopy
        from .story_exchange import validate
        from .story_promotion import source_snapshot
        key='business-runtime-amendment-v1'
        fields={'contract','actor','owner_decision_sha256','task_hash','grant_hash','thread_id','turn_id','max_turn_seconds','additional_turn_limit'}
        proof_fields={'source','root','thread_id','turn_id','thread_status','turn_status','observed_at'}
        if (type(authority)is not dict or set(authority)!=fields
            or authority['contract']!='PCH-STORY-RUNTIME-600-OWNER-v1' or authority['actor']!='trusted_local_owner'
            or authority['owner_decision_sha256']!=digest('允许全部改为10 分钟上限')
            or type(authority['max_turn_seconds'])is not int or authority['max_turn_seconds']!=600
            or type(authority['additional_turn_limit'])is not int or authority['additional_turn_limit']!=1
            or type(observation)is not dict or set(observation)!=proof_fields
            or observation['source']!='app_server_stdio.thread/read' or observation['turn_status']!='interrupted'
            or observation['thread_status']not in {'idle','notLoaded'}
            or type(observation['observed_at'])not in {int,float}or not 0<=time.time()-observation['observed_at']<30):
            raise ServiceError('BUSINESS_RUNTIME_AMENDMENT_REJECTED',status=409)
        grant=validate(grant,live=True);source_snapshot(grant,expected=True)
        if grant['max_turn_seconds']!=600 or Path(grant['root'],'output/result.json').exists():
            raise ServiceError('BUSINESS_RUNTIME_AMENDMENT_REJECTED',status=409)
        with self._connection(True)as connection:
            row=connection.execute('SELECT *FROM tasks WHERE id=?',(stable_id(ident),)).fetchone()
            task=self._task(row)if row else None
            if (not task or task['status']!='requires_reconcile' or task['cancel']or digest(task)!=authority['task_hash']
                or task['payload'].get('kind')!='story_exchange' or task['intent']['grant_hash']!=authority['grant_hash']
                or task['thread_id']!=authority['thread_id']or task['turn_id']!=authority['turn_id']
                or observation['thread_id']!=task['thread_id']or observation['turn_id']!=task['turn_id']
                or observation['root']!=grant['root']):
                raise ServiceError('BUSINESS_RUNTIME_AMENDMENT_REJECTED',status=409)
            old=task['intent']['grant'];expected=deepcopy(old)
            expected.update(version=old['version']+1,expires_at=grant['expires_at'],max_turn_seconds=600)
            expected['story_exchange']['registration_sha256']=grant['story_exchange']['registration_sha256']
            expected['story_exchange']['source_preconditions']['source_state_sha256']=grant['story_exchange']['source_preconditions']['source_state_sha256']
            if grant!=expected:raise ServiceError('BUSINESS_RUNTIME_SCOPE_CHANGED',status=409)
            registered=connection.execute('SELECT *FROM grants WHERE id=?',(old['grant_id'],)).fetchone()
            if not registered or self._decode(registered['body'],registered['hash'])!=old:
                raise ServiceError('GRANT_VERSION_STALE',status=409)
            events=connection.execute('SELECT *FROM events WHERE task_id=?ORDER BY sequence',(ident,)).fetchall()
            if any(e['event_key']==key for e in events):raise ServiceError('BUSINESS_RUNTIME_AMENDMENT_CONSUMED',status=409)
            for event in events:
                value=self._decode(event['body'],event['hash'])
                process=value.get('worker')if event['event_key'].startswith('dispatch-intent')else value if event['event_key'].startswith('stdio-child')else None
                if process:
                    try:current=Path('/proc',str(process['pid']),'stat').read_text().rsplit(')',1)[1].split()[19]
                    except FileNotFoundError:continue
                    if current==process['start_ticks']:raise ServiceError('TASK_OWNER_STILL_ACTIVE',status=409)
            evidence={'authority':authority,'observation':observation,'original_status':task['status'],
                'original_thread_id':task['thread_id'],'original_turn_id':task['turn_id'],'original_payload':task['payload'],
                'original_intent':task['intent'],'original_result':task['result'],
                'action':'same_owned_thread_new_turn_after_verified_interruption','effects_rolled_back':False}
            self._append_event(connection,ident,key,evidence)
            payload={**task['payload'],'grant_version':grant['version']}
            intent={**task['intent'],'grant':grant,'grant_hash':digest(grant),
                'source_binding':deepcopy(grant['story_exchange']['source_preconditions']),
                'runtime_amendment_authority_sha256':digest(authority)}
            connection.execute('UPDATE grants SET version=?,body=?,hash=?WHERE id=?',
                (grant['version'],canonical(grant).decode(),digest(grant),grant['grant_id']))
            connection.execute("UPDATE tasks SET payload=?,payload_hash=?,intent=?,intent_hash=?,status='queued',owner=NULL,lease_until=NULL,result=NULL,turn_id=NULL,provider_status=NULL WHERE id=?",
                (canonical(payload).decode(),digest(payload),canonical(intent).decode(),digest(intent),ident))
            return {'event_key':key,'grant_version':grant['version'],'thread_id':task['thread_id'],'additional_turn_limit':1}

    def reconcile_business_preturn_rejection(self, ident, *, authority, observation, grant):
        """Recover one definitively rejected resume without replaying a turn.

        Root supplies fresh same-history/lifecycle evidence. The old rejected
        dispatch and its one-shot intent stay immutable; this receipt has its
        own one-shot native fence. No HTTP caller can enroll this operation.
        """
        import hashlib
        from copy import deepcopy
        from .story_exchange import validate
        from .story_promotion import source_snapshot
        key='business-preturn-reconciled-v1'
        fields={'contract','actor','owner_decision_sha256','task_hash','events_hash','thread_id','turn_history_sha256','additional_turn_limit'}
        proof_fields={'source','root','thread_id','thread_status','turns','turn_history_sha256','observed_at','lifecycle'}
        if (type(authority)is not dict or set(authority)!=fields
            or authority['contract']!='PCH-STORY-PRETURN-RECOVERY-v1'or authority['actor']!='trusted_local_owner'
            or authority['owner_decision_sha256']!=digest('允许全部改为10 分钟上限')
            or type(authority['additional_turn_limit'])is not int or authority['additional_turn_limit']!=1
            or type(authority['turn_history_sha256'])is not str or len(authority['turn_history_sha256'])!=64
            or any(c not in '0123456789abcdef'for c in authority['turn_history_sha256'])
            or type(observation)is not dict or set(observation)!=proof_fields
            or observation['source']!='app_server_stdio.thread/read'or observation['thread_status']not in {'idle','notLoaded'}
            or observation['thread_id']!=authority['thread_id']or observation['turn_history_sha256']!=authority['turn_history_sha256']
            or type(observation['observed_at'])not in {int,float}or not 0<=time.time()-observation['observed_at']<30):
            raise ServiceError('BUSINESS_PRETURN_RECOVERY_REJECTED',status=409)
        lifecycle=observation['lifecycle']
        if (type(lifecycle)is not dict or set(lifecycle)!={'method','cycles','same_history','prior_holder'}
            or lifecycle['method']!='supported_owned_thread_archive_unarchive'or type(lifecycle['cycles'])is not int
            or lifecycle['cycles']!=1 or lifecycle['same_history']is not True):
            raise ServiceError('BUSINESS_PRETURN_RECOVERY_REJECTED',status=409)
        holder=lifecycle['prior_holder']
        if (type(holder)is not dict or set(holder)!={'pid','start_ticks','fd','device','inode'}
            or any(type(holder[k])is not int or holder[k]<(1 if k in {'pid','inode'}else 0)for k in ('pid','fd','device','inode'))
            or type(holder['start_ticks'])is not str or not holder['start_ticks'].isdecimal()):
            raise ServiceError('BUSINESS_PRETURN_RECOVERY_REJECTED',status=409)
        try:
            current=Path('/proc',str(holder['pid']),'stat').read_text().rsplit(')',1)[1].split()[19]
            fd=Path('/proc',str(holder['pid']),'fd',str(holder['fd'])).stat()
            if current==holder['start_ticks']and (fd.st_dev,fd.st_ino)==(holder['device'],holder['inode']):
                raise ServiceError('TASK_OWNER_STILL_ACTIVE',status=409)
        except FileNotFoundError:pass
        grant=validate(grant,live=True);source_snapshot(grant,expected=True)
        output=Path(grant['root'],'output/result.json')
        if grant['max_turn_seconds']!=600 or observation['root']!=grant['root']or output.exists()or output.is_symlink():
            raise ServiceError('BUSINESS_PRETURN_RECOVERY_REJECTED',status=409)
        with self._connection(True)as connection:
            row=connection.execute('SELECT *FROM tasks WHERE id=?',(stable_id(ident),)).fetchone()
            task=self._task(row)if row else None
            events=[{'sequence':r['sequence'],'key':r['event_key'],'received':r['received'],'data':self._decode(r['body'],r['hash'])}
                    for r in connection.execute('SELECT *FROM events WHERE task_id=?ORDER BY sequence',(ident,))]
            by_key={e['key']:e for e in events};amend=by_key.get('business-runtime-amendment-v1',{}).get('data',{})
            consumed=by_key.get('business-runtime-amendment-resume-intent')
            prior=by_key.get('business-resumed',{}).get('data',{})
            if (not task or digest(task)!=authority['task_hash']or digest(events)!=authority['events_hash']
                or task['status']!='failed'or task['cancel']or task['thread_id']!=authority['thread_id']
                or task['turn_id']is not None or task['provider_status']is not None or task['payload'].get('kind')!='story_exchange'
                or key in by_key or not consumed or consumed['data'].get('thread_id')!=task['thread_id']
                or amend.get('original_thread_id')!=task['thread_id']or prior.get('thread_id')!=task['thread_id']
                or not prior.get('prior_turn_id')or not amend.get('original_turn_id')
                or amend.get('authority',{}).get('owner_decision_sha256')!=authority['owner_decision_sha256']
                or observation['turns']!=[{'id':prior['prior_turn_id'],'status':'interrupted'},
                                          {'id':amend['original_turn_id'],'status':'interrupted'}]
                or any(e['sequence']>consumed['sequence']and
                       (e['key'].startswith('turn-intent-')or e['key']in {'business-runtime-amendment-resumed','business-preturn-resumed'})for e in events)):
                raise ServiceError('BUSINESS_PRETURN_RECOVERY_REJECTED',status=409)
            result=task['result']or{};details=result.get('details',{})
            message=('thread '+task['thread_id']+' already has an active writer').encode()
            if (result.get('error_class')!='CODEX_RPC_REJECTED'or result.get('outcome')=='UNKNOWN'
                or details.get('method')!='thread/resume'or details.get('rpc_code')!=-32600
                or details.get('message_sha256')!=hashlib.sha256(message).hexdigest()or details.get('message_bytes')!=len(message)
                or details.get('raw_message_retained')is not False or result.get('request_denials')
                or result.get('stream_observation',{}).get('count',0)!=0):
                raise ServiceError('BUSINESS_PRETURN_RECOVERY_REJECTED',status=409)
            old=task['intent']['grant'];expected=deepcopy(old)
            expected.update(version=old['version']+1,expires_at=grant['expires_at'])
            expected['story_exchange']['registration_sha256']=grant['story_exchange']['registration_sha256']
            expected['story_exchange']['source_preconditions']['source_state_sha256']=grant['story_exchange']['source_preconditions']['source_state_sha256']
            registered=connection.execute('SELECT *FROM grants WHERE id=?',(old['grant_id'],)).fetchone()
            if old['max_turn_seconds']!=600 or grant!=expected or not registered or self._decode(registered['body'],registered['hash'])!=old:
                raise ServiceError('BUSINESS_RUNTIME_SCOPE_CHANGED',status=409)
            for event in events:
                value=event['data'];process=value.get('worker')if event['key'].startswith('dispatch-intent')else value if event['key'].startswith('stdio-child')else None
                if process:
                    try:current=Path('/proc',str(process['pid']),'stat').read_text().rsplit(')',1)[1].split()[19]
                    except FileNotFoundError:continue
                    if current==process['start_ticks']:raise ServiceError('TASK_OWNER_STILL_ACTIVE',status=409)
            evidence={'authority':authority,'observation':observation,'original_thread_id':task['thread_id'],
                'original_turn_id':amend['original_turn_id'],'original_payload':task['payload'],
                'original_intent':task['intent'],'original_result':task['result'],
                'action':'same_owned_thread_new_turn_after_verified_interruption','effects_rolled_back':False}
            self._append_event(connection,ident,key,evidence)
            payload={**task['payload'],'grant_version':grant['version']}
            intent={**task['intent'],'grant':grant,'grant_hash':digest(grant),
                    'source_binding':deepcopy(grant['story_exchange']['source_preconditions']),
                    'preturn_recovery_authority_sha256':digest(authority)}
            connection.execute('UPDATE grants SET version=?,body=?,hash=?WHERE id=?',
                (grant['version'],canonical(grant).decode(),digest(grant),grant['grant_id']))
            connection.execute("UPDATE tasks SET payload=?,payload_hash=?,intent=?,intent_hash=?,status='queued',owner=NULL,lease_until=NULL,result=NULL,turn_id=NULL,provider_status=NULL WHERE id=?",
                (canonical(payload).decode(),digest(payload),canonical(intent).decode(),digest(intent),ident))
            return {'event_key':key,'grant_version':grant['version'],'thread_id':task['thread_id'],'additional_turn_limit':1}

    def clear_closed_terminal_owner(self, ident, proof):
        """Root-only terminal fence cleanup; never a lease takeover or HTTP action."""
        versions={'HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v5-REPLACEMENT':'v5',
            'HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v6-OWNED-RESUME':'v6',
            'HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v7-PROCESS-CONFIG':'v7'}
        key='closed-terminal-owner-'+versions.get(proof.get('contract'),'rejected'); now=time.time()
        if set(proof)!={'contract','task_hash','observed_at','worker','child','actor'} or \
            proof['contract']not in versions or \
            proof['actor']!='trusted_local_owner':
            raise ServiceError('CLOSED_OWNER_PROOF_REJECTED',status=409)
        with self._connection(True)as connection:
            old=connection.execute('SELECT body,hash FROM events WHERE task_id=? AND event_key=?',(ident,key)).fetchone()
            if old:
                prior=json.loads(old['body'])
                if digest(prior)!=old['hash']or prior['proof']!=proof:raise ServiceError('TASK_EVENT_CONFLICT',status=409)
                return prior
            if not 0<=now-proof['observed_at']<30:raise ServiceError('CLOSED_OWNER_PROOF_REJECTED',status=409)
            row=connection.execute('SELECT * FROM tasks WHERE id=?',(ident,)).fetchone()
            task=self._task(row)if row else None
            if not task or digest(task)!=proof['task_hash']or task['status']!='requires_reconcile' or \
                not task['owner']or task['lease_until']is None or task['lease_until']>=now or \
                task['cancel']or task['turn_id']is not None or task['provider_status']is not None or not task['thread_id']:
                raise ServiceError('CLOSED_OWNER_FENCE_CHANGED',status=409)
            identities={}
            for event in connection.execute('SELECT event_key,body FROM events WHERE task_id=?',(ident,)):
                body=json.loads(event['body'])
                if event['event_key']=='dispatch-intent-'+task['owner']:identities['worker']=body['worker']
                if event['event_key']=='stdio-child-'+task['owner']:identities['child']=body
            for name in ('worker','child'):
                expected=identities.get(name,{})
                identity=proof[name]
                if set(identity)!={'pid','start_ticks'}or any(identity.get(k)!=expected.get(k)for k in identity):
                    raise ServiceError('CLOSED_OWNER_IDENTITY_UNVERIFIED',status=409)
                path=Path('/proc/'+str(identity['pid'])+'/stat')
                try:live=path.read_text().rsplit(')',1)[1].split()[19]
                except FileNotFoundError:live=None
                except (OSError,IndexError):raise ServiceError('CLOSED_OWNER_IDENTITY_UNVERIFIED',status=409)from None
                if live==identity['start_ticks']:raise ServiceError('CLOSED_OWNER_STILL_ALIVE',status=409)
            receipt={'proof':proof,'old_owner':task['owner'],'old_lease_until':task['lease_until'],
                'old_thread_id':task['thread_id'],'old_result_hash':digest(task['result']),
                'method':'exact proc PID/start ticks absent or reused; expired terminal lease; semantic row CAS','version':1}
            self._append_event(connection,ident,key,receipt)
            connection.execute('UPDATE tasks SET owner=NULL,lease_until=NULL WHERE id=?',(ident,))
            return receipt

    def authorize_css_replacement(self, ident, authority):
        """One Root-owned recovery CAS; original grant/request/intent stay immutable."""
        now=time.time();key='css-recovery-authority-v5'
        if authority.get('contract')!='HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v5-REPLACEMENT'or \
            authority.get('task_id')!=ident or authority.get('actor')!='trusted_local_owner'or \
            authority.get('version')!=1 or authority.get('replacement_limit')!=1 or authority.get('model_turn_limit')!=1 or \
            not now<authority.get('expires_at',0)<now+7201:
            raise ServiceError('CSS_RECOVERY_AUTHORITY_REJECTED',status=409)
        with self._connection(True)as connection:
            if connection.execute('SELECT 1 FROM events WHERE task_id=? AND event_key=?',(ident,key)).fetchone():
                raise ServiceError('CSS_RECOVERY_ALREADY_CONSUMED',status=409)
            row=connection.execute('SELECT * FROM tasks WHERE id=?',(ident,)).fetchone();task=self._task(row)if row else None
            if not task or digest(task)!=authority.get('task_hash')or task['status']!='requires_reconcile'or \
                task['owner']is not None or task['lease_until']is not None or task['cancel']or \
                task['thread_id']!=authority.get('old_thread_id')or task['turn_id']is not None or task['provider_status']is not None or \
                task['intent'].get('grant',{}).get('checks')!=['csp_css_trial']or \
                digest(task['intent'])!=authority.get('intent_hash')or digest(task['payload'])!=authority.get('request_hash')or \
                task['intent']['grant_hash']!=authority.get('grant_hash')or \
                not connection.execute('SELECT 1 FROM events WHERE task_id=? AND event_key=?',(ident,'closed-terminal-owner-v5')).fetchone():
                raise ServiceError('CSS_RECOVERY_FENCE_CHANGED',status=409)
            receipt={'authority':authority,'prior_thread':{'thread_id':task['thread_id'],'turn_id':None,
                'provider_status':None,'result':task['result'],'intent_hash':digest(task['intent'])},'version':1,'action':'one_replacement_same_task'}
            self._append_event(connection,ident,key,receipt)
            connection.execute("UPDATE tasks SET status='queued',thread_id=NULL,result=NULL WHERE id=?",(ident,))
            return receipt

    def authorize_css_owned_resume(self, ident, authority):
        """One exact owned load; retains the bound thread and all prior effects."""
        key='css-owned-resume-authority-v6'
        if authority.get('contract')!='HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v6-OWNED-RESUME'or \
            authority.get('actor')!='trusted_local_owner'or authority.get('resume_limit')!=1 or \
            authority.get('model_turn_limit')!=1 or authority.get('thread_limit')!=2:
            raise ServiceError('CSS_RESUME_AUTHORITY_REJECTED')
        with self._connection(True)as connection:
            if connection.execute('SELECT 1 FROM events WHERE task_id=? AND event_key=?',(ident,key)).fetchone():
                raise ServiceError('CSS_RESUME_ALREADY_CONSUMED')
            row=connection.execute('SELECT * FROM tasks WHERE id=?',(ident,)).fetchone();task=self._task(row)if row else None
            if not task or digest(task)!=authority.get('task_hash')or task['status']!='requires_reconcile'or \
                task['owner']is not None or task['lease_until']is not None or task['cancel']or task['turn_id']is not None or \
                task['provider_status']is not None or task['thread_id']!=authority.get('thread_id')or \
                digest(task['intent'])!=authority.get('intent_hash')or \
                not connection.execute('SELECT 1 FROM events WHERE task_id=? AND event_key=?',(ident,'closed-terminal-owner-v6')).fetchone()or \
                connection.execute("SELECT 1 FROM events WHERE task_id=? AND (event_key LIKE 'turn-intent-%' OR event_key='css-unique-model-turn-v5')",(ident,)).fetchone():
                raise ServiceError('CSS_RESUME_FENCE_CHANGED')
            receipt={'authority':authority,'prior_result':task['result'],'version':1}
            self._append_event(connection,ident,key,receipt)
            connection.execute("UPDATE tasks SET status='queued' WHERE id=?",(ident,))
            return receipt

    def authorize_css_process_resume(self, ident, authority):
        """Root-only final exact resume; prior resume and original grant stay fixed."""
        key='css-process-resume-authority-v7'
        if authority.get('contract')!='HUB-LWB-2.3-CSP-CSS-TRIAL-GOV-v7-PROCESS-CONFIG'or \
            authority.get('actor')!='trusted_local_owner'or authority.get('resume_limit')!=2 or \
            authority.get('model_turn_limit')!=1 or authority.get('thread_limit')!=2:
            raise ServiceError('CSS_PROCESS_RESUME_AUTHORITY_REJECTED')
        with self._connection(True)as connection:
            events={row['event_key']:json.loads(row['body'])for row in connection.execute(
                'SELECT event_key,body FROM events WHERE task_id=?',(ident,))}
            if key in events or 'css-owned-load-resume-v7'in events:raise ServiceError('CSS_PROCESS_RESUME_ALREADY_CONSUMED')
            row=connection.execute('SELECT * FROM tasks WHERE id=?',(ident,)).fetchone();task=self._task(row)if row else None
            prior=events.get('css-owned-resume-authority-v6',{}).get('authority',{})
            if not task or digest(task)!=authority.get('task_hash')or task['status']!='requires_reconcile'or \
                task['owner']is not None or task['lease_until']is not None or task['cancel']or task['turn_id']is not None or \
                task['provider_status']is not None or task['thread_id']!=authority.get('thread_id')or \
                digest(task['intent'])!=authority.get('intent_hash')or \
                task['intent'].get('grant',{}).get('checks')!=['csp_css_trial']or \
                digest(prior)!=authority.get('prior_authority_hash')or \
                not time.time()<authority.get('expires_at',0)<=task['intent']['grant']['expires_at']or \
                'closed-terminal-owner-v7'not in events or \
                events.get('css-owned-load-resume-v6',{}).get('thread_id')!=task['thread_id']or \
                any(k.startswith('turn-intent-')or k=='css-unique-model-turn-v5'for k in events):
                raise ServiceError('CSS_PROCESS_RESUME_FENCE_CHANGED')
            receipt={'authority':authority,'prior_result':task['result'],'version':1,'total_resume_limit':2}
            self._append_event(connection,ident,key,receipt)
            connection.execute("UPDATE tasks SET status='queued' WHERE id=?",(ident,))
            return receipt

    def _append_event(self, connection, ident, key, value):
        if self._plan_schema(connection) and connection.execute('SELECT id FROM plan_archive_tasks WHERE id=?',(ident,)).fetchone():raise ServiceError('TASK_ARCHIVED_READONLY',status=409)
        stable_id(key); body = canonical(value).decode()
        if len(body.encode()) > 8192: raise ServiceError('TASK_EVENT_CAPACITY', status=507)
        old = connection.execute('SELECT hash FROM events WHERE task_id=? AND event_key=?',(ident,key)).fetchone()
        if old:
            if old['hash'] != digest(value): raise ServiceError('TASK_EVENT_CONFLICT', status=409)
            return False
        if connection.execute('SELECT count(*) FROM events').fetchone()[0] >= MAX_EVENTS:
            raise ServiceError('TASK_EVENT_CAPACITY', status=507)
        connection.execute('INSERT INTO events(task_id,event_key,body,hash,received) VALUES(?,?,?,?,?)',(ident,key,body,digest(value),time.time()))
        return True

    def event(self, ident, key, value):
        if key.startswith('rebuild-'):raise ServiceError('REBUILD_TRUSTED_EVENT_REQUIRED',status=403)
        with self._connection(True) as connection:return self._append_event(connection,ident,key,value)

    def _controls(self, connection, ident):
        opened={};decisions={}
        for row in connection.execute('SELECT * FROM events WHERE task_id=? ORDER BY sequence',(ident,)):
            if row['event_key'].startswith(('control-open-','control-decision-')):
                value=self._decode(row['body'],row['hash'])
                (opened if row['event_key'].startswith('control-open-') else decisions)[value['id']]=value
        return [{**value,**decisions.get(key,{}),'version':2 if key in decisions else 1} for key,value in opened.items()]

    def controls(self, ident):
        with self._connection() as connection:return self._controls(connection,stable_id(ident)) if connection else []

    def _ordinary_control_check(self, connection, task):
        grant=task['intent']['grant']
        if grant.get('kind')!='ordinary_project':return
        from .ordinary_tasks import check_registration, validate_grant
        check_registration(grant,self.root);validate_grant(grant,live=True)
        current=connection.execute('SELECT hash FROM grants WHERE id=?',(grant['grant_id'],)).fetchone()
        if not current or current['hash']!=task['intent']['grant_hash']:
            raise ServiceError('TASK_CONTROL_VERSION_STALE',status=409)

    def open_control(self, ident, owner, *, request_hash, method, thread_id, turn_id, expires_at, provider_request=None):
        """Worker-only request record; retain only bounded nonsecret input forms."""
        kinds={'item/commandExecution/requestApproval':'approval','item/fileChange/requestApproval':'approval','item/tool/requestUserInput':'input'}
        if method not in kinds or not isinstance(request_hash,str) or len(request_hash)!=64 or any(c not in '0123456789abcdef' for c in request_hash):raise ServiceError('TASK_CONTROL_REJECTED')
        now=time.time()
        if type(expires_at) not in (int,float) or not now<expires_at<=now+120:raise ServiceError('TASK_CONTROL_REJECTED')
        control_id='control-'+request_hash
        with self._connection(True) as connection:
            row=connection.execute('SELECT * FROM tasks WHERE id=? AND owner=?',(stable_id(ident),stable_id(owner))).fetchone()
            if not row or row['lease_until']<now or row['cancel'] or row['status']!='running' or row['thread_id']!=thread_id or row['turn_id']!=turn_id:raise ServiceError('TASK_CONTROL_BINDING_REJECTED',status=409)
            task=self._task(row);self._ordinary_control_check(connection,task);grant=task['intent']['grant'];current=connection.execute('SELECT hash FROM grants WHERE id=?',(grant['grant_id'],)).fetchone()
            if not current or current['hash']!=task['intent']['grant_hash'] or grant['expires_at']<=now:raise ServiceError('GRANT_VERSION_STALE',status=409)
            policy={'decisions':['decline'],'reason':'provider_request_unavailable'}
            if provider_request is not None:
                try:verified_request=type(provider_request)is dict and digest(provider_request)==request_hash and provider_request.get('method')==method
                except (TypeError,ValueError,OverflowError):verified_request=False
                if not verified_request:raise ServiceError('TASK_CONTROL_BINDING_REJECTED',status=409)
                params=provider_request.get('params')
                policy=approval_policy(grant,provider_request,thread_id=thread_id,turn_id=turn_id,
                    item_id=params.get('itemId')if type(params)is dict else None,
                    request_id=provider_request.get('id'),grant_root=grant['root'],now=now)
            if grant.get('kind')=='ordinary_project' and method=='item/tool/requestUserInput':
                from .task_inputs import input_policy
                policy=input_policy(provider_request,thread_id,turn_id)
            value={'id':control_id,'version':1,'kind':kinds[method],'method':method,'request_digest':request_hash,
                   'thread_id':thread_id,'turn_id':turn_id,'worker_owner':owner,'owner_context':task['intent'].get('owner_context','trusted_local_owner'),
                   'grant_hash':task['intent']['grant_hash'],'expires_at':min(expires_at,grant['expires_at']),
                   'status':'pending','permissions':'unverified_beyond_grant' if kinds[method]=='approval' else 'input_unavailable',
                   'allowed_decisions':policy['decisions'],'reason':policy['reason']}
            if 'questions'in policy:
                value.update(questions=policy['questions'],isBlocking=policy['isBlocking'],permissions='bounded_local_text_input')
            if 'operation_sha256'in policy:
                value.update(operation_sha256=policy['operation_sha256'],permissions='exact_registered_command')
            self._append_event(connection,ident,'control-open-'+request_hash,value)
            connection.execute('UPDATE tasks SET status=? WHERE id=?',('waiting_approval' if kinds[method]=='approval' else 'waiting_input',ident))
        return control_id

    def respond_control(self, ident, control_id, version, decision, *, owner_context='trusted_local_owner', authorize=None, answers=None):
        """Authenticated single-decision CAS against the frozen request policy."""
        if decision not in ('accept','decline','answer') or type(version)is not int or version!=1:raise ServiceError('TASK_CONTROL_DECISION_REJECTED',status=409)
        if authorize is not None:authorize()
        with self._connection(True,guard=authorize) as connection:
            row=connection.execute('SELECT * FROM tasks WHERE id=?',(stable_id(ident),)).fetchone()
            controls=self._controls(connection,ident);control=next((c for c in controls if c['id']==stable_id(control_id)),None)
            if not row or not control:raise ServiceError('TASK_CONTROL_NOT_FOUND',status=404)
            if control['owner_context']!=owner_context:raise ServiceError('TASK_CONTROL_OWNER_REJECTED',status=403)
            if decision not in control['allowed_decisions']:raise ServiceError('TASK_CONTROL_DECISION_REJECTED',status=409)
            task=self._task(row);self._ordinary_control_check(connection,task)
            saved_answers=None
            if decision=='answer':
                from .task_inputs import validate_answers
                saved_answers=validate_answers(control.get('questions',[]),answers)
            elif answers is not None:raise ServiceError('TASK_INPUT_ANSWER_REJECTED',status=409)
            if control['version']==2:
                if control.get('decision')==decision and (decision!='answer' or control.get('answer_sha256')==digest(saved_answers)):
                    if task['intent']['grant'].get('kind')=='ordinary_project':
                        now=time.time()
                        if row['cancel'] or not row['lease_until'] or row['lease_until']<=now or control['expires_at']<=now or row['owner']!=control['worker_owner'] or row['thread_id']!=control['thread_id'] or row['turn_id']!=control['turn_id']:
                            raise ServiceError('TASK_CONTROL_VERSION_STALE',status=409)
                    return True
                raise ServiceError('TASK_CONTROL_VERSION_STALE',status=409)
            now=time.time();task=self._task(row);current=connection.execute('SELECT hash FROM grants WHERE id=?',(task['intent']['grant']['grant_id'],)).fetchone()
            if row['owner']!=control['worker_owner'] or row['lease_until']<now or row['cancel'] or row['status'] not in {'waiting_input','waiting_approval'} or row['thread_id']!=control['thread_id'] or row['turn_id']!=control['turn_id'] or control['expires_at']<=now or task['intent']['grant']['expires_at']<=now or task['intent']['grant_hash']!=control['grant_hash'] or not current or current['hash']!=control['grant_hash']:raise ServiceError('TASK_CONTROL_VERSION_STALE',status=409)
            result={'id':control_id,'decision':decision,'status':'answered'if decision=='answer'else'accepted'if decision=='accept'else'declined','at':now}
            if saved_answers is not None:result.update(answers=saved_answers,answer_sha256=digest(saved_answers))
            self._append_event(connection,ident,'control-decision-'+control['request_digest'],result)
        if authorize is not None:authorize()
        return False

    def close_control(self, ident, owner, control_id, reason):
        if reason not in {'expired','cancelled','grant_revoked'}:raise ServiceError('TASK_CONTROL_REJECTED')
        with self._connection(True) as connection:
            row=connection.execute('SELECT * FROM tasks WHERE id=? AND owner=?',(stable_id(ident),stable_id(owner))).fetchone()
            control=next((c for c in self._controls(connection,ident) if c['id']==control_id),None)
            if not row or not control or row['owner']!=control['worker_owner'] or row['lease_until']<time.time() or row['thread_id']!=control['thread_id'] or row['turn_id']!=control['turn_id']:raise ServiceError('TASK_CONTROL_BINDING_REJECTED',status=409)
            if control['version']==1:self._append_event(connection,ident,'control-decision-'+control['request_digest'],{'id':control_id,'decision':'decline','status':reason,'at':time.time()})

    def _history_rows(self,connection,ident,*,prefix=None):
        clause=' AND event_key LIKE ?' if prefix else ''
        args=(ident,prefix+'%') if prefix else (ident,)
        rows=list(connection.execute('SELECT *FROM events WHERE task_id=?'+clause,args))
        if self._plan_schema(connection):rows+=list(connection.execute('SELECT *FROM plan_archive_events WHERE task_id=?'+clause,args))
        return [{'sequence':r['sequence'],'key':r['event_key'],'received':r['received'],'data':self._decode(r['body'],r['hash'])} for r in sorted(rows,key=lambda r:r['sequence'])]

    def events(self, ident):
        with self._connection() as connection:return self._history_rows(connection,stable_id(ident))

    # Additive plan storage v1. Existing task/event/byte limits remain unchanged.
    def _plan_schema(self,connection,*,create=False):
        present=connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='plan_schema'").fetchone()
        if not present:
            if not create:return False
            for sql in PLAN_DDL:connection.execute(sql)
            self.fault('after_plan_schema')
        if [r[0]for r in connection.execute('SELECT version FROM plan_schema')]!=[1]:raise ServiceError('PLAN_SCHEMA_UNSUPPORTED',status=503)
        return True

    def _task_row(self,connection,ident):
        row=connection.execute('SELECT * FROM tasks WHERE id=?',(ident,)).fetchone()
        if row is None and self._plan_schema(connection):row=connection.execute('SELECT * FROM plan_archive_tasks WHERE id=?',(ident,)).fetchone()
        return row

    def _plan_input(self,connection,ident,authority):
        from .plan_admission import authority as require,validate_plan,plan_id,reject,plan_history
        authority=require(authority)
        if not self._plan_schema(connection):reject('PLAN_NOT_IMPORTED')
        row=connection.execute('SELECT body,hash FROM plan_inputs WHERE id=?',(plan_id(ident),)).fetchone()
        if not row:reject('PLAN_NOT_IMPORTED')
        plan=plan_history(self._decode(*row))[0];authority.check(plan)
        return plan

    def import_plan(self,plan,*,authority):
        from .plan_admission import authority as require,validate_plan,reject,plan_history
        authority=require(authority);plan=validate_plan(plan);authority.check(plan)
        with self._connection(True) as connection:
            self._plan_schema(connection,create=True)
            row=connection.execute('SELECT body,hash FROM plan_inputs WHERE id=?',(plan['plan_id'],)).fetchone()
            if row and plan_history(self._decode(*row))[0]!=plan:reject('PLAN_CONFLICT_REQUIRES_RECONCILE')
            if not row:
                if connection.execute('SELECT count(*)FROM plan_inputs').fetchone()[0]>=4:reject('PLAN_CAPACITY')
                connection.execute('INSERT INTO plan_inputs VALUES(?,?,?)',(plan['plan_id'],canonical(plan).decode(),digest(plan)))
            authority.check(plan);self.fault('before_plan_import_commit')
        return {'replayed':row is not None,'execution_rows_created':0,'plan_sha256':digest(plan)}

    def plan_revision_status(self,plan_id,*,authority):
        from .plan_admission import authority as require,plan_id as checked,plan_history,reject
        cap=require(authority);cap._authorize()
        with self._connection() as db:
            if not self._plan_schema(db):reject('PLAN_NOT_IMPORTED')
            row=db.execute('SELECT body,hash FROM plan_inputs WHERE id=?',(checked(plan_id),)).fetchone()
            if not row:reject('PLAN_NOT_IMPORTED')
            current,versions,requests=plan_history(self._decode(*row));binding=cap.snapshot()
            if digest(current)not in {binding.get('plan_sha256'),binding.get('reconcile_from_sha256')}:reject('PLAN_AUTHORITY_MISMATCH')
            cap._authorize()
            return {'plan_id':plan_id,'current_sha256':digest(current),'revision':current['revision'],'version_hashes':list(versions),'requests':requests,'execution_started':False}

    def reconcile_plan(self,plan,*,expected_sha256,request_id,authority,rollback=False):
        """Root-wired CAS only; immutable packages/attempts/receipts never reset."""
        from .plan_admission import authority as require,validate_plan,plan_history,changed_definitions,reject
        cap=require(authority);target=validate_plan(plan);cap.check(target);stable_id(request_id)
        if type(rollback)is not bool or cap.snapshot().get('reconcile_from_sha256')!=expected_sha256:reject('PLAN_REVISION_AUTHORITY')
        command={'request_id':request_id,'from_sha256':expected_sha256,'to_sha256':digest(target),'operation':'rollback'if rollback else'reconcile','affected_task_ids':[]}
        guard_controls=[]
        def guarded():
            cap.check(target)
            if guard_controls and cap._controls()!=guard_controls[0]:reject('PLAN_CONTROL_CHANGED')
        with self._connection(True,guard=guarded) as db:
            if not self._plan_schema(db):reject('PLAN_NOT_IMPORTED')
            row=db.execute('SELECT body,hash FROM plan_inputs WHERE id=?',(target['plan_id'],)).fetchone()
            if not row:reject('PLAN_NOT_IMPORTED')
            old,versions,requests=plan_history(self._decode(*row))
            previous=versions.get(expected_sha256)
            if previous is None:reject('PLAN_CAS_STALE')
            command['affected_task_ids']=changed_definitions(previous,target)
            replay=next((r for r in requests if r['request_id']==request_id),None)
            if replay:
                if replay!=command:reject('PLAN_REVISION_REQUEST_CONFLICT')
                cap.check(target)
                return {'replayed':True,'current_sha256':digest(old),'request':replay,'execution_rows_created':0}
            if digest(old)!=expected_sha256:reject('PLAN_CAS_STALE')
            if digest(target)==expected_sha256:reject('PLAN_REVISION_NO_CHANGE')
            if rollback!=(digest(target)in versions):reject('PLAN_REVISION_TARGET')
            if not rollback and any(p['revision']==target['revision']for p in versions.values()):reject('PLAN_REVISION_TARGET')
            controls=cap._controls()
            if type(controls)is not dict or controls.get('state')!='active' or type(controls.get('revision'))is not int:reject('PLAN_CONTROL_BLOCKED')
            guard_controls.append(json.loads(canonical(controls)))
            for ident in command['affected_task_ids']:
                # Even a terminal old package has immutable singleton identity.
                # Re-execution of changed existing packages needs later explicit reconciliation.
                if self._task_row(db,ident)is not None:reject('PLAN_REVISION_EXISTING_EFFECT')
                records=[]
                for table in ('plan_receipts','plan_conflicts'):
                    records.extend(self._decode(r['body'],r['hash'])for r in db.execute('SELECT body,hash FROM '+table+' WHERE plan_id=? AND task_id=?',(target['plan_id'],ident)))
                if any(r['effect_state']=='unknown'or r['unknowns']for r in records):reject('PLAN_REVISION_UNKNOWN_EFFECT')
                if len({r['plan_sha256']for r in records})!=len(records):reject('PLAN_REVISION_UNRESOLVED_CONFLICT')
            if not rollback:versions[digest(target)]=target
            requests.append(command)
            body={'schema_version':'plan-revisions/1','plan_id':target['plan_id'],'current_sha256':digest(target),'versions':[{'sha256':h,'plan':p}for h,p in versions.items()],'requests':requests}
            from .dispatch_budget import split as split_budget,wrap as wrap_budget
            _,retained_budget=split_budget(self._decode(*row))
            from .source_admission import split as split_source,wrap as wrap_source
            original_history,_=split_budget(self._decode(*row))
            _,retained_source=split_source(original_history)
            body=wrap_budget(wrap_source(body,retained_source),retained_budget)
            plan_history(body)
            self._replace_plan_input(db,target['plan_id'],body)
            def guard():
                cap.check(target)
                if cap._controls()!=controls:reject('PLAN_CONTROL_CHANGED')
            guard();self.fault('before_plan_revision_commit');guard()
        return {'replayed':False,'current_sha256':digest(target),'request':command,'execution_rows_created':0}

    def record_plan_evidence(self,plan_id,record,*,authority):
        from .plan_admission import BOOTSTRAP_IDS,reject
        with self._connection(True) as connection:
            plan=self._plan_input(connection,plan_id,authority);accepted=authority.verify(record)
            definition=next((t for t in plan['tasks']if t['id']==record['task_id']),None)
            if not definition:reject('PLAN_TASK_UNKNOWN')
            if record['origin']=='bootstrap' and record['task_id']not in BOOTSTRAP_IDS:reject('PLAN_BOOTSTRAP_REJECTED')
            if record['origin']=='historical' and definition['status']!='done':reject('PLAN_EVIDENCE_UNVERIFIED')
            task=self._task_row(connection,record['task_id'])
            if record['origin']=='contract':
                if not task or task['status']!='checks_complete' or task['cancel']:reject('PLAN_EVIDENCE_UNVERIFIED')
                from .rebuild_contracts import settled_evidence
                history={r['key']:r['data']for r in self._rebuild_rows(connection,record['task_id'])}
                package=history.get('rebuild-package');attempt=history.get('rebuild-attempt-'+record['attempt_id']);receipt=history.get('rebuild-receipt-'+record['receipt_id'])
                if not receipt or digest(receipt)!=record['receipt_sha256']:reject('PLAN_EVIDENCE_UNVERIFIED')
                if package['plan']!={'revision':plan['revision'],'sha256':digest(plan)}:reject('PLAN_EVIDENCE_UNVERIFIED')
                settled_evidence(package,receipt,record['provenance'],attempt)
            elif task is not None:reject('PLAN_BOOTSTRAP_EXISTING_EFFECT_REQUIRES_RECONCILE')
            old=connection.execute('SELECT body,hash FROM plan_receipts WHERE plan_id=? AND task_id=?',(plan_id,record['task_id'])).fetchone()
            same=[]
            if old:same.append(self._decode(*old))
            same.extend(self._decode(r['body'],r['hash'])for r in connection.execute('SELECT body,hash FROM plan_conflicts WHERE plan_id=? AND task_id=?',(plan_id,record['task_id'])))
            version_records=[r for r in same if r['plan_sha256']==record['plan_sha256']]
            if record in version_records:
                result={'replayed':True,'requires_reconcile':not accepted or len(version_records)>1}
            else:
                table='plan_receipts'if not old else'plan_conflicts'
                values=(plan_id,record['task_id'],canonical(record).decode(),digest(record))
                connection.execute('INSERT INTO '+table+' VALUES(?,?,?,?)',values)
                result={'replayed':False,'requires_reconcile':not accepted or bool(version_records)}
            authority.check(plan);authority.verify(record)
        return result

    def _plan_state(self,connection,plan,authority,*,witnesses=None):
        from .plan_admission import reject,plan_history
        raw=connection.execute('SELECT body,hash FROM plan_inputs WHERE id=?',(plan['plan_id'],)).fetchone()
        _,versions,_=plan_history(self._decode(*raw));definitions={t['id']:t for t in plan['tasks']}
        accepted=set();states={};groups={}
        if witnesses is not None:witnesses.clear()
        for table in ('plan_receipts','plan_conflicts'):
            for row in connection.execute('SELECT task_id,body,hash FROM '+table+' WHERE plan_id=?',(plan['plan_id'],)):
                value=self._decode(row['body'],row['hash']);ident=row['task_id'];h=value['plan_sha256']
                if value['task_id']!=ident or h not in versions:reject('PLAN_EVIDENCE_UNVERIFIED')
                origin=next((t for t in versions[h]['tasks']if t['id']==ident),None)
                if origin is None:reject('PLAN_EVIDENCE_UNVERIFIED')
                if ident in definitions and digest(origin)==digest(definitions[ident]):groups.setdefault(ident,{}).setdefault(h,[]).append(value)
        for ident,by_version in groups.items():
            h=next(h for h in [digest(plan),*reversed(versions)]if h in by_version);records=by_version[h]
            try:verified=len(records)==1 and authority.verify(records[0],plan_sha256=h)
            except ServiceError:verified=False
            if verified:
                accepted.add(ident)
                if witnesses is not None:witnesses[ident]=records[0]
            else:states[ident]='requires_reconcile'
        for row in connection.execute('SELECT id,status,cancel FROM tasks'):
            states[row['id']]=row['status']
            if row['cancel'] or row['status']!='checks_complete':accepted.discard(row['id'])
        if self._plan_schema(connection):
            for row in connection.execute('SELECT id,status,cancel FROM plan_archive_tasks'):
                states[row['id']]=row['status']
                if row['cancel'] or row['status']!='checks_complete':accepted.discard(row['id'])
        definitions={t['id']:t for t in plan['tasks']}
        while True:
            invalid={ident for ident in accepted if not set(definitions[ident]['depends_on'])<=accepted}
            if not invalid:break
            accepted-=invalid
            for ident in invalid:states[ident]='requires_reconcile'
        controls=authority._controls()
        if type(controls)is not dict or controls.get('state')not in {'active','paused','cancelled'} or type(controls.get('revision'))is not int:reject('PLAN_CONTROL_UNKNOWN')
        if witnesses is not None:
            for ident in set(witnesses)-accepted:del witnesses[ident]
        return states,accepted,controls

    def plan_ready(self,plan_id,*,authority):
        from .plan_admission import ready_window,ROUTES
        with self._connection() as connection:
            plan=self._plan_input(connection,plan_id,authority);states,accepted,controls=self._plan_state(connection,plan,authority)
            eligible=lambda task:ROUTES in accepted and authority._routes(task) is True
            ready=ready_window(plan,states,accepted,eligible) if controls['state']=='active' else []
        return {'ready':ready,'accepted':sorted(accepted),'control_revision':controls['revision'],'execution_authority':False}

    def _plan_source_context(self,connection,plan_id,authority):
        from .plan_admission import plan_history,reject
        from .dispatch_budget import split as split_budget
        from .source_admission import split
        plan=self._plan_input(connection,plan_id,authority)
        states,accepted,controls=self._plan_state(connection,plan,authority)
        row=connection.execute('SELECT body,hash FROM plan_inputs WHERE id=?',(plan_id,)).fetchone()
        raw=self._decode(*row);history,budget=split_budget(raw);inner,records=split(history)
        blocked_sources=set()
        for definition in plan['tasks']:
            if 'source_admission'not in definition:continue
            root=definition['source_admission']['workspace']['canonical_root']
            if definition['id']in {'P5-personal-style-hub-01','P5-personal-style-hub-02'}:
                if (root==str(self.root) or not authority.registered_source_workspace(definition)
                        or not authority.development(definition)):
                    blocked_sources.add(definition['id'])
            elif root!=str(self.root):reject('PLAN_SOURCE_WORKSPACE_MISMATCH')
        # A revoked selected workspace is local unavailability, not a corrupt
        # PCH ledger. Preserve persisted statuses and freeze its actual dependents.
        while True:
            descendants={t['id']for t in plan['tasks']
                if set(t['depends_on'])&blocked_sources or
                {d['task_id']for d in t.get('source_admission',{}).get('dependencies',[])}&blocked_sources}
            if descendants<=blocked_sources:break
            blocked_sources|=descendants
        for ident in blocked_sources:states.setdefault(ident,'source_workspace_blocked')
        return plan,states,accepted,controls,inner,budget,records,plan_history(raw)[1]

    def plan_source_ready(self,plan_id,*,authority):
        from .source_admission import source_ready
        from .plan_admission import reject
        with self._connection() as db:
            plan,states,accepted,controls,_,_,records,_=self._plan_source_context(db,plan_id,authority)
            ready=source_ready(plan,states,accepted,records,authority.development,authority.verify_source) if controls['state']=='active' and authority._development is not None and authority._source_verifier is not None else []
            authority.check(plan)
            if authority._controls()!=controls:reject('PLAN_CONTROL_CHANGED')
            return {'ready':ready,'accepted':sorted(accepted),'source_sha256':digest(records),
                    'source_records':json.loads(canonical(records)),'control_revision':controls['revision'],
                    'execution_authority':False,'whole_task_acceptance':False}

    def record_plan_source_evidence(self,plan_id,record,*,expected_source_sha256,authority):
        from .source_admission import record as validate_record,source_ready,wrap,need
        from .dispatch_budget import wrap as wrap_budget
        from .plan_admission import reject,plan_history
        context={}
        def guard():
            if context:
                authority.check(context['plan'])
                if authority._controls()!=context['controls']:reject('PLAN_CONTROL_CHANGED')
                if authority.development(context['definition']) is not True or authority.verify_source(record) is not True:reject('PLAN_SOURCE_UNVERIFIED')
                if 'predecessors' in context:
                    states,accepted,_=self._plan_state(db,context['plan'],authority)
                    current=source_ready(context['plan'],states,accepted,context['predecessors'],authority.development,authority.verify_source,
                        reconciliation=record if 'reconciles' in record else None)
                    need(record['task_id'] in current,'PLAN_SOURCE_NOT_READY')
        with self._connection(True,guard=guard) as db:
            plan,states,accepted,controls,history,budget,records,versions=self._plan_source_context(db,plan_id,authority)
            record=validate_record(record,versions)
            need(record['plan_sha256']==digest(plan),'PLAN_SOURCE_VERSION_MISMATCH')
            definition=next(t for t in plan['tasks']if t['id']==record['task_id'])
            context.update(plan=plan,controls=controls,definition=definition)
            need(controls['state']=='active','PLAN_CONTROL_BLOCKED');guard()
            previous=next((r for r in records if r['record']['request_id']==record['request_id']),None)
            command={'expected_source_sha256':expected_source_sha256,'record':record}
            if previous:
                need(previous==command,'PLAN_SOURCE_REQUEST_CONFLICT')
                return {'replayed':True,'source_sha256':digest(records),'execution_rows_created':0}
            need(expected_source_sha256==digest(records),'PLAN_SOURCE_CAS_STALE')
            ready=source_ready(plan,states,accepted,records,authority.development,authority.verify_source,
                reconciliation=record if 'reconciles' in record else None)
            need(record['task_id'] in ready,'PLAN_SOURCE_NOT_READY')
            # Revalidate dependency evidence at commit, not just this candidate.
            context['predecessors']=json.loads(canonical(records))
            records.append(command)
            body=wrap_budget(wrap(history,records),budget);plan_history(body)
            self._replace_plan_input(db,plan_id,body)
            self.fault('before_plan_source_commit');guard()
            return {'replayed':False,'source_sha256':digest(records),'execution_rows_created':0}

    def admit_plan_window(self,plan_id,*,authority):
        from .plan_admission import ready_window,ROUTES,WINDOW,reject
        from .rebuild_contracts import validate,require_authority
        with self._connection(True) as connection:
            plan=self._plan_input(connection,plan_id,authority);states,accepted,controls=self._plan_state(connection,plan,authority)
            definitions={t['id']:t for t in plan['tasks']}
            pending=sum(1 for ident in states if ident in definitions and ident not in accepted)
            ready=ready_window(plan,states,accepted,lambda task:ROUTES in accepted and authority._routes(task) is True,limit=max(0,WINDOW-pending)) if controls['state']=='active' else []
            admitted=[];unavailable=[];dependencies_unavailable=[];budget_unavailable=[]
            for ident in ready:
                supplied=authority._package(ident)
                if supplied is None:unavailable.append(ident);continue
                package,cap=supplied;package=validate(package,'package');cap=require_authority(cap)
                definition=definitions[ident]
                if any(package[k]!=definition[v] for k,v in [('task_id','id'),('project_id','project_id'),('phase_id','phase_id'),('round_id','round_id')]) or package['route']['owner_route']!=definition['owner_route']:reject('PLAN_PACKAGE_MISMATCH')
                if package['plan']!={'revision':plan['revision'],'sha256':digest(plan)} or package['lineage']['goal_id']!=plan['plan_id']:reject('PLAN_PACKAGE_MISMATCH')
                if {x['task_id']for x in package['dependencies']}!=set(definition['depends_on']):reject('PLAN_DEPENDENCY_MISMATCH')
                if authority._controls()!=controls:reject('PLAN_CONTROL_CHANGED')
                if package['budget']['deadline']<=time.time():budget_unavailable.append(ident);continue
                try:self._rebuild_dependencies(connection,package,cap)
                except ServiceError as error:
                    if error.code.startswith('REBUILD_DEPENDENCY'):
                        dependencies_unavailable.append(ident);continue
                    raise
                self._prepare_rebuild(connection,package,cap);admitted.append(ident)
            if authority._controls()!=controls:reject('PLAN_CONTROL_CHANGED')
            authority.check(plan)
        return {'admitted':admitted,'unavailable_authority':unavailable,'unavailable_dependency_receipts':dependencies_unavailable,'unavailable_budget':budget_unavailable,'window':WINDOW,'execution_started':False}

    def archive_plan_completed(self,plan_id,*,authority):
        """Same SQLite, exact original rows/sequences. Only verified settled work."""
        from .plan_admission import WINDOW,reject
        with self._connection(True) as connection:
            plan=self._plan_input(connection,plan_id,authority);states,accepted,controls=self._plan_state(connection,plan,authority)
            if controls['state']!='active':reject('PLAN_CONTROL_BLOCKED')
            moved=[]
            for ident in sorted(accepted):
                row=connection.execute('SELECT * FROM tasks WHERE id=?',(ident,)).fetchone()
                if not row:continue
                if row['status']!='checks_complete' or row['cancel']:reject('PLAN_ARCHIVE_UNSETTLED')
                # Original settled receipt was verified when recorded; revalidate
                # complete contract evidence, not static planning status.
                evidence=connection.execute('SELECT body,hash FROM plan_receipts WHERE plan_id=? AND task_id=?',(plan_id,ident)).fetchone()
                record=self._decode(*evidence)
                if record['plan_sha256']!=digest(plan):
                    candidates=[self._decode(r['body'],r['hash'])for r in connection.execute('SELECT body,hash FROM plan_conflicts WHERE plan_id=? AND task_id=?',(plan_id,ident))]
                    current=[r for r in candidates if r['plan_sha256']==digest(plan)]
                    if current:
                        if len(current)!=1:reject('PLAN_ARCHIVE_UNSETTLED')
                        record=current[0]
                if record['origin']!='contract':reject('PLAN_ARCHIVE_UNSETTLED')
                from .rebuild_contracts import settled_evidence
                history={r['key']:r['data']for r in self._rebuild_rows(connection,ident)}
                settled_evidence(history['rebuild-package'],history['rebuild-receipt-'+record['receipt_id']],record['provenance'],history['rebuild-attempt-'+record['attempt_id']])
                self._task(row)
                for event in connection.execute('SELECT body,hash FROM events WHERE task_id=?',(ident,)):self._decode(*event)
                connection.execute('INSERT INTO plan_archive_tasks SELECT * FROM tasks WHERE id=?',(ident,))
                connection.execute('INSERT INTO plan_archive_events SELECT * FROM events WHERE task_id=?',(ident,))
                connection.execute('DELETE FROM events WHERE task_id=?',(ident,));connection.execute('DELETE FROM tasks WHERE id=?',(ident,));moved.append(ident)
            if connection.execute('SELECT count(*)FROM plan_archive_tasks').fetchone()[0]>len(plan['tasks']):reject('PLAN_ARCHIVE_CAPACITY')
            event_cap=2048*((len(plan['tasks'])+WINDOW-1)//WINDOW)
            if connection.execute('SELECT count(*)FROM plan_archive_events').fetchone()[0]>event_cap:reject('PLAN_ARCHIVE_CAPACITY')
            if authority._controls()!=controls:reject('PLAN_CONTROL_CHANGED')
            authority.check(plan);self.fault('before_plan_archive_commit')
        return {'archived':moved,'history_deleted':False,'schema_version':1}

    def restore_plan_archive(self,plan_id,*,authority):
        """Rollback storage placement only, never reverse accepted effects."""
        from .plan_admission import reject
        with self._connection(True) as connection:
            plan=self._plan_input(connection,plan_id,authority)
            if connection.execute('SELECT count(*)FROM tasks').fetchone()[0]+connection.execute('SELECT count(*)FROM plan_archive_tasks').fetchone()[0]>MAX_TASKS:reject('PLAN_ROLLBACK_CAPACITY')
            if connection.execute('SELECT count(*)FROM events').fetchone()[0]+connection.execute('SELECT count(*)FROM plan_archive_events').fetchone()[0]>MAX_EVENTS:reject('PLAN_ROLLBACK_CAPACITY')
            connection.execute('INSERT INTO tasks SELECT *FROM plan_archive_tasks');connection.execute('INSERT INTO events SELECT *FROM plan_archive_events')
            connection.execute('DELETE FROM plan_archive_events');connection.execute('DELETE FROM plan_archive_tasks')
            authority.check(plan);self.fault('before_plan_restore_commit')
        return {'restored':True,'effects_reverted':False,'static_inputs_retained':True}
