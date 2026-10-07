"""Hub task intents and evidence. SQLite never confers execution authority."""
from __future__ import annotations

import contextlib
import json
import os
import secrets
import sqlite3
import stat
import time
from pathlib import Path

from .service_contract import ServiceError
from .workbench_store import canonical, digest
from .design_records import ID_RE
from .task_storage import StorageGuard, blocked, components

SCHEMA_VERSION = 2
MAX_TASKS = 128
MAX_EVENTS = 2048
MAX_DATABASE_BYTES = 16 * 1024 * 1024
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


def stable_id(value):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ServiceError('INVALID_TASK_ID')
    return value


def parent_binding_matches(intent, parent):
    binding = intent.get('parent_binding')
    return (parent is not None and type(binding) is dict and set(binding) == {'thread_id', 'turn_id'}
            and all(type(binding[key]) is str and binding[key] for key in ('thread_id', 'turn_id'))
            and binding == {'thread_id': parent['thread_id'], 'turn_id': parent['turn_id']})


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
        ledger_profile=self._ledger_profile(allow_legacy=readonly)
        if ledger_profile is not None and ledger_profile!=profile:raise blocked('PROFILE_VERSION_STALE')
        return result if readonly else self.storage_guard.check_space()

    def _ledger_profile(self,*,allow_legacy=False):
        try:
            with contextlib.closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as connection:
                versions=connection.execute('SELECT version FROM meta').fetchall()
                if versions==[(1,)]:
                    if allow_legacy:return None
                    raise blocked('PROFILE_REGISTRATION_REQUIRED')
                if versions!=[(SCHEMA_VERSION,)]:raise ServiceError('TASK_SCHEMA_UNSUPPORTED',status=503)
                rows=connection.execute('SELECT body,hash FROM storage_profile').fetchall()
                if len(rows)!=1:raise blocked('PROFILE_REJECTED')
                return self._decode(*rows[0])
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
                self.check_storage()
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
    def _decode(raw, expected):
        try:
            value = json.loads(raw)
            if digest(value) != expected or canonical(value).decode() != raw: raise ValueError()
            return value
        except (ValueError, TypeError, UnicodeError):
            raise ServiceError('TASK_STORE_CORRUPT', status=503) from None

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

    def grants(self):
        with self._connection() as connection:
            return [self._decode(row['body'],row['hash'])for row in connection.execute('SELECT * FROM grants ORDER BY id')]if connection else []

    def admit(self, payload, intent, *, authorize=None):
        ident = stable_id(payload['request_id']); hashed = digest(payload)
        if len(canonical(payload)) > 8192 or len(canonical(intent)) > 49152:
            raise ServiceError('TASK_PAYLOAD_CAPACITY', status=413)
        with self._connection(True,guard=authorize) as connection:
            old = connection.execute('SELECT * FROM tasks WHERE id=?', (ident,)).fetchone()
            if old:
                if old['payload_hash'] != hashed: raise ServiceError('TASK_REQUEST_CONFLICT', status=409)
                return self._task(old), True
            if connection.execute('SELECT count(*) FROM tasks').fetchone()[0] >= MAX_TASKS:
                raise ServiceError('TASK_CAPACITY', status=507)
            connection.execute('INSERT INTO tasks(id,payload,payload_hash,intent,intent_hash,project,parent,status,created) VALUES(?,?,?,?,?,?,?,?,?)',
                (ident, canonical(payload).decode(), hashed, canonical(intent).decode(), digest(intent), payload['project_id'], payload['parent_task_id'], 'queued', time.time()))
            row = connection.execute('SELECT * FROM tasks WHERE id=?', (ident,)).fetchone()
            return self._task(row), False

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

    def task(self, ident):
        stable_id(ident)
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM tasks WHERE id=?', (ident,)).fetchone() if connection else None
            return self._task(row) if row else None

    def tasks(self):
        with self._connection() as connection:
            return [self._task(row) for row in connection.execute('SELECT * FROM tasks ORDER BY sequence DESC')] if connection else []

    def claim(self, owner, now=None, *, only_task_id=None):
        stable_id(owner); now = time.time() if now is None else now
        self.check_storage()
        with self._connection(True) as connection:
            # Expired claims never become queued: dispatch effects may already exist.
            connection.execute("UPDATE tasks SET status='requires_reconcile' WHERE status IN ('running','validating','waiting_input','waiting_approval') AND lease_until<?", (now,))
            if only_task_id is not None:stable_id(only_task_id)
            rows = connection.execute("SELECT * FROM tasks WHERE status='queued'"+
                (" AND id=?"if only_task_id is not None else"")+" ORDER BY sequence",
                (only_task_id,)if only_task_id is not None else()).fetchall()
            for row in rows:
                parent = connection.execute('SELECT * FROM tasks WHERE id=?', (row['parent'],)).fetchone() if row['parent'] else None
                task = self._task(row)
                if (row['parent'] or task['payload']['mode'] == 'busy_feedback') and not parent_binding_matches(task['intent'], parent):
                    connection.execute("UPDATE tasks SET status='requires_reconcile' WHERE id=?", (row['id'],)); continue
                if parent and parent['status'] not in TERMINAL: continue
                if connection.execute("SELECT 1 FROM tasks WHERE project=? AND status IN ('running','validating','waiting_input','waiting_approval','requires_reconcile') LIMIT 1", (row['project'],)).fetchone(): continue
                if parent and parent['status'] != 'checks_complete':
                    connection.execute("UPDATE tasks SET status='requires_reconcile' WHERE id=?", (row['id'],)); continue
                if parent:
                    if parent['provider_status']!='completed':
                        connection.execute("UPDATE tasks SET status='requires_reconcile' WHERE id=?",(row['id'],));continue
                connection.execute("UPDATE tasks SET owner=?,lease_until=?,status='running' WHERE id=?", (owner, now+15, row['id']))
                return self._task(connection.execute('SELECT * FROM tasks WHERE id=?', (row['id'],)).fetchone())
            return None

    def update(self, ident, owner, **values):
        if not values or set(values) - {'status','thread_id','turn_id','provider_status','result'}:
            raise ServiceError('INVALID_TASK_UPDATE')
        if 'status' in values and values['status'] not in STATES: raise ServiceError('INVALID_TASK_UPDATE')
        if 'result' in values:
            if len(canonical(values['result'])) > 16384: raise ServiceError('TASK_RESULT_CAPACITY', status=507)
            values['result'] = canonical({'data':values['result'],'hash':digest(values['result'])}).decode()
        with self._connection(True) as connection:
            row = connection.execute('SELECT * FROM tasks WHERE id=? AND owner=?',(ident,owner)).fetchone()
            if not row or row['status'] in TERMINAL or row['lease_until'] < time.time():
                raise ServiceError('TASK_OWNER_CONFLICT', status=409)
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
            columns = ','.join(key+'=?' for key in values)
            success_guard=' AND cancel=0'if values.get('status')=='checks_complete'else''
            cursor = connection.execute('UPDATE tasks SET '+columns+',lease_until=? WHERE id=? AND owner=?'+success_guard, (*values.values(),time.time()+15,ident,owner))
            if cursor.rowcount != 1: raise ServiceError('TASK_OWNER_CONFLICT', status=409)

    def heartbeat(self, ident, owner):
        with self._connection(True) as connection:
            now=time.time()
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
                task['provider_status']!='completed'or 'css_trial'not in task['intent']['grant']or
                digest(task['result'])!=expected_result_hash or not task['result'].get('trial_ready')):
                raise ServiceError('TASK_LOCAL_PROMOTION_REJECTED',status=409)
            g=connection.execute('SELECT *FROM grants WHERE id=?',(task['intent']['grant']['grant_id'],)).fetchone()
            if not g or self._decode(g['body'],g['hash'])!=task['intent']['grant']:
                raise ServiceError('CSS_PROMOTION_GRANT_STALE',status=409)
            if not connection.execute('SELECT 1 FROM events WHERE task_id=?AND event_key=?',(ident,'css-promotion-intent')).fetchone()or \
                connection.execute('SELECT 1 FROM events WHERE task_id=?AND event_key=?',(ident,'css-promoted')).fetchone():
                raise ServiceError('CSS_PROMOTION_REQUIRES_RECONCILE',status=409)
            result=action()
            self._append_event(connection,ident,'css-promoted',result)
            return result

    def handoff_validation(self, ident, owner, expected_result_hash):
        """Owned worker has closed its adapter; Root completes local checks."""
        with self._connection(True)as connection:
            row=connection.execute('SELECT *FROM tasks WHERE id=? AND owner=?',(stable_id(ident),owner)).fetchone()
            task=self._task(row)if row else None
            if (not task or task['status']!='validating' or task['lease_until']<time.time() or task['cancel'] or
                task['provider_status']!='completed' or not task['thread_id']or not task['turn_id']or
                'css_trial'not in task['intent']['grant']or digest(task['result'])!=expected_result_hash or
                not task['result'].get('trial_ready')or task['result'].get('source_promoted')is not False):
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
                task['provider_status']!='completed'or 'css_trial'not in task['intent']['grant']or
                digest(task['result'])!=expected_result_hash or not task['result'].get('trial_ready')or
                result.get('source_promoted')is not True or result.get('validation_scope')!='canonical_project' or
                not result.get('checks')or any(c.get('exit')!=0 for c in result['checks'])):
                raise ServiceError('TASK_LOCAL_FINALIZATION_REJECTED',status=409)
            self._append_event(connection,ident,'local-validation-complete',{'before_result_hash':expected_result_hash,
                'result_hash':digest(result),'human_acceptance':'pending'})
            cursor=connection.execute("UPDATE tasks SET status='checks_complete',result=? WHERE id=? AND status='validating'AND cancel=0 AND owner IS NULL",
                (canonical({'data':result,'hash':digest(result)}).decode(),ident))
            if cursor.rowcount!=1:raise ServiceError('TASK_LOCAL_FINALIZATION_REJECTED',status=409)

    def cancel(self, ident):
        with self._connection(True) as connection:
            row = connection.execute('SELECT status FROM tasks WHERE id=?', (stable_id(ident),)).fetchone()
            if not row: raise ServiceError('TASK_NOT_FOUND', status=404)
            if row['status'] not in TERMINAL:
                connection.execute("UPDATE tasks SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE id=?",(ident,))

    def reconcile_owned(self, ident, observation):
        """Root-only reconciliation; no HTTP route and no post-turn replay.

        Native proof must establish no dispatched turn, or a stopped owned turn
        with no file effects. Only Root supplies this fresh observation after
        exact old processes close; preserve failed checks/IDs before requeuing.
        """
        self.check_storage()
        if type(observation)is not dict:raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
        unstarted=observation.get('source')=='app_server_stdio.thread/list'
        expected={'source','root','thread_ids','archived_checked','observed_at'}if unstarted else \
            {'source','root','thread_id','turn_id','thread_status','turn_status','observed_at'}
        if set(observation)!=expected or (unstarted and (observation['thread_ids']!=[] or observation['archived_checked']is not True)) or \
            (not unstarted and (observation['source']!='app_server_stdio.thread/read' or observation['turn_status']not in {'interrupted','completed'} or observation['thread_status']not in {'idle','notLoaded'}))or \
            type(observation['observed_at'])not in {int,float} or not 0<=time.time()-observation['observed_at']<30:
            raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
        with self._connection(True)as connection:
            row=connection.execute('SELECT * FROM tasks WHERE id=?',(stable_id(ident),)).fetchone()
            if not row:raise ServiceError('TASK_NOT_FOUND',status=404)
            task=self._task(row)
            if unstarted and (task['status']not in {'failed','requires_reconcile'} or task['cancel'] or \
                task['thread_id']is not None or task['turn_id']is not None or task['provider_status']is not None):
                raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            check_failure=not unstarted and observation['turn_status']=='completed'
            if not unstarted and (task['status']not in ({'failed'}if check_failure else{'cancelled','requires_reconcile'}) or not task['thread_id']or not task['turn_id']or
                task['thread_id']!=observation['thread_id']or task['turn_id']!=observation['turn_id']):
                raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            if check_failure and (task['provider_status']!='completed' or not task['result']or
                not any(c['exit']!=0 for c in task['result'].get('checks',[])) or task['result'].get('changed_paths')!=[]):
                raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            events=connection.execute('SELECT * FROM events WHERE task_id=? ORDER BY sequence',(ident,)).fetchall()
            if unstarted and any(e['event_key'].startswith('turn-intent')for e in events):
                raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            for event in events:
                value=self._decode(event['body'],event['hash'])
                process=value.get('worker')if event['event_key'].startswith('dispatch-intent')else value if event['event_key'].startswith('stdio-child')else None
                if process:
                    try:current=Path('/proc/'+str(process['pid'])+'/stat').read_text().rsplit(')',1)[1].split()[19]
                    except FileNotFoundError:continue
                    if current==process['start_ticks']:raise ServiceError('TASK_OWNER_STILL_ACTIVE',status=409)
            from .task_service import fingerprint,root_identity
            grant=task['intent']['grant']
            if observation['root']!=grant['root'] or root_identity(grant['root'])!=grant['root_identity'] or \
                fingerprint(grant['root'])!=task['intent']['preimage']:
                raise ServiceError('TASK_RECONCILE_PROOF_REJECTED',status=409)
            evidence={'observation':observation,'original_status':task['status'],'original_result':task['result'],
                'intent_hash':digest(task['intent']),'original_thread_id':task['thread_id'],'original_turn_id':task['turn_id'],
                'effects_rolled_back':False,'action':'same_id_requeue_before_any_turn'if unstarted else
                'same_owned_thread_new_turn_after_verified_check_failure'if check_failure else'same_owned_thread_new_turn_after_verified_interruption'}
            body=canonical(evidence).decode();hashed=digest(evidence)
            if len(body.encode())>8192 or connection.execute('SELECT count(*)FROM events').fetchone()[0]>=MAX_EVENTS:
                raise ServiceError('TASK_EVENT_CAPACITY',status=507)
            connection.execute('INSERT INTO events(task_id,event_key,body,hash,received)VALUES(?,?,?,?,?)',
                (ident,'reconcile-'+hashed,body,hashed,time.time()))
            connection.execute("UPDATE tasks SET status='queued',owner=NULL,lease_until=NULL,result=NULL,cancel=0,turn_id=NULL,provider_status=NULL WHERE id=?",(ident,))

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

    def open_control(self, ident, owner, *, request_hash, method, thread_id, turn_id, expires_at):
        """Worker-only request record. Never retain provider command, form, or path."""
        kinds={'item/commandExecution/requestApproval':'approval','item/fileChange/requestApproval':'approval','item/tool/requestUserInput':'input'}
        if method not in kinds or not isinstance(request_hash,str) or len(request_hash)!=64 or any(c not in '0123456789abcdef' for c in request_hash):raise ServiceError('TASK_CONTROL_REJECTED')
        now=time.time()
        if type(expires_at) not in (int,float) or not now<expires_at<=now+120:raise ServiceError('TASK_CONTROL_REJECTED')
        control_id='control-'+request_hash
        with self._connection(True) as connection:
            row=connection.execute('SELECT * FROM tasks WHERE id=? AND owner=?',(stable_id(ident),stable_id(owner))).fetchone()
            if not row or row['lease_until']<now or row['cancel'] or row['status']!='running' or row['thread_id']!=thread_id or row['turn_id']!=turn_id:raise ServiceError('TASK_CONTROL_BINDING_REJECTED',status=409)
            task=self._task(row);grant=task['intent']['grant'];current=connection.execute('SELECT hash FROM grants WHERE id=?',(grant['grant_id'],)).fetchone()
            if not current or current['hash']!=task['intent']['grant_hash'] or grant['expires_at']<=now:raise ServiceError('GRANT_VERSION_STALE',status=409)
            value={'id':control_id,'version':1,'kind':kinds[method],'method':method,'request_digest':request_hash,
                   'thread_id':thread_id,'turn_id':turn_id,'worker_owner':owner,'owner_context':task['intent'].get('owner_context','trusted_local_owner'),
                   'grant_hash':task['intent']['grant_hash'],'expires_at':min(expires_at,grant['expires_at']),
                   'status':'pending','permissions':'unverified_beyond_grant' if kinds[method]=='approval' else 'input_unavailable',
                   'allowed_decisions':['decline']}
            self._append_event(connection,ident,'control-open-'+request_hash,value)
            connection.execute('UPDATE tasks SET status=? WHERE id=?',('waiting_approval' if kinds[method]=='approval' else 'waiting_input',ident))
        return control_id

    def respond_control(self, ident, control_id, version, decision, *, owner_context='trusted_local_owner', authorize=None):
        """Authenticated deny-only CAS. No HTTP decision can expand a grant."""
        if decision!='decline' or type(version)is not int or version!=1:raise ServiceError('TASK_CONTROL_DECISION_REJECTED',status=409)
        if authorize is not None:authorize()
        with self._connection(True,guard=authorize) as connection:
            row=connection.execute('SELECT * FROM tasks WHERE id=?',(stable_id(ident),)).fetchone()
            controls=self._controls(connection,ident);control=next((c for c in controls if c['id']==stable_id(control_id)),None)
            if not row or not control:raise ServiceError('TASK_CONTROL_NOT_FOUND',status=404)
            if control['owner_context']!=owner_context:raise ServiceError('TASK_CONTROL_OWNER_REJECTED',status=403)
            if control['version']==2:
                if control.get('decision')=='decline':return True
                raise ServiceError('TASK_CONTROL_VERSION_STALE',status=409)
            now=time.time();task=self._task(row);current=connection.execute('SELECT hash FROM grants WHERE id=?',(task['intent']['grant']['grant_id'],)).fetchone()
            if row['owner']!=control['worker_owner'] or row['lease_until']<now or row['cancel'] or row['status'] not in {'waiting_input','waiting_approval'} or row['thread_id']!=control['thread_id'] or row['turn_id']!=control['turn_id'] or control['expires_at']<=now or not current or current['hash']!=control['grant_hash']:raise ServiceError('TASK_CONTROL_VERSION_STALE',status=409)
            self._append_event(connection,ident,'control-decision-'+control['request_digest'],{'id':control_id,'decision':'decline','status':'declined','at':now})
        if authorize is not None:authorize()
        return False

    def close_control(self, ident, owner, control_id, reason):
        if reason not in {'expired','cancelled','grant_revoked'}:raise ServiceError('TASK_CONTROL_REJECTED')
        with self._connection(True) as connection:
            row=connection.execute('SELECT * FROM tasks WHERE id=? AND owner=?',(stable_id(ident),stable_id(owner))).fetchone()
            control=next((c for c in self._controls(connection,ident) if c['id']==control_id),None)
            if not row or not control or row['owner']!=control['worker_owner'] or row['lease_until']<time.time() or row['thread_id']!=control['thread_id'] or row['turn_id']!=control['turn_id']:raise ServiceError('TASK_CONTROL_BINDING_REJECTED',status=409)
            if control['version']==1:self._append_event(connection,ident,'control-decision-'+control['request_digest'],{'id':control_id,'decision':'decline','status':reason,'at':time.time()})

    def events(self, ident):
        with self._connection() as connection:
            return [{'sequence':r['sequence'],'key':r['event_key'],'received':r['received'],'data':self._decode(r['body'],r['hash'])}
                    for r in connection.execute('SELECT * FROM events WHERE task_id=? ORDER BY sequence',(stable_id(ident),))] if connection else []
