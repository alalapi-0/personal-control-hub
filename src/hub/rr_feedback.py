"""PCH-owned RR feedback, isolated from task execution in the same TaskStore.

This module confers no authentication. Its caller must authenticate the gateway
or protected local Agent before invoking it. No text here is executed.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import functools
import hashlib
import json
import sqlite3
import uuid

from .rr_payload import (RRFeedbackError, canonical_json, parse_document,
                         validate_submission, valid_uuid, opaque, timestamp)
from .service_contract import ServiceError

RR_DDL = (
    '''CREATE TABLE rr_feedback(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 feedback_id TEXT UNIQUE NOT NULL, device_id TEXT NOT NULL, project_id TEXT NOT NULL,
 request_uuid TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision=1),
 body TEXT NOT NULL, body_sha256 TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
 receipt TEXT NOT NULL, receipt_sha256 TEXT NOT NULL,
 UNIQUE(device_id,project_id,request_uuid))''',
    '''CREATE TABLE rr_attachments(feedback_id TEXT NOT NULL REFERENCES rr_feedback(feedback_id),
 ordinal INTEGER NOT NULL CHECK(ordinal>=0 AND ordinal<2), attachment_id TEXT NOT NULL,
 filename TEXT NOT NULL, content_type TEXT NOT NULL, size INTEGER NOT NULL CHECK(size>=0 AND size<=1048576),
 sha256 TEXT NOT NULL, data BLOB NOT NULL, PRIMARY KEY(feedback_id,attachment_id), UNIQUE(feedback_id,ordinal))''',
    '''CREATE TABLE rr_reads(read_id TEXT PRIMARY KEY, feedback_id TEXT NOT NULL REFERENCES rr_feedback(feedback_id),
 agent_id TEXT NOT NULL, proof TEXT NOT NULL, hash TEXT NOT NULL, UNIQUE(feedback_id,agent_id))''',
    '''CREATE TABLE rr_acks(feedback_id TEXT PRIMARY KEY REFERENCES rr_feedback(feedback_id),
 ack_id TEXT UNIQUE NOT NULL, agent_id TEXT NOT NULL, read_id TEXT NOT NULL REFERENCES rr_reads(read_id),
 body TEXT NOT NULL, hash TEXT NOT NULL)''',
    '''CREATE TABLE rr_results(feedback_id TEXT PRIMARY KEY REFERENCES rr_feedback(feedback_id),
 result_id TEXT UNIQUE NOT NULL, agent_id TEXT NOT NULL, ack_id TEXT NOT NULL REFERENCES rr_acks(ack_id),
 body TEXT NOT NULL, hash TEXT NOT NULL)''',
)
RR_TABLES = ('rr_feedback', 'rr_attachments', 'rr_reads', 'rr_acks', 'rr_results')
_SCHEMA_QUERY = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _decode(raw, expected):
    try:
        value = json.loads(raw)
        if canonical_json(value).decode() != raw or _sha(raw.encode()) != expected:
            raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError, RRFeedbackError):
        raise RRFeedbackError(503, 'RR_STORE_CORRUPT') from None


@functools.lru_cache(maxsize=8)
def _expected_schema(with_rr, with_drafts=False, with_plan=False):
    from .task_store import SCHEMA, ORDINARY_DRAFT_TABLES, PLAN_DDL
    with contextlib.closing(sqlite3.connect(':memory:')) as db:
        db.executescript(SCHEMA)
        if with_drafts:
            for ddl in ORDINARY_DRAFT_TABLES.values():
                db.execute(ddl)
        if with_rr:
            for ddl in RR_DDL:
                db.execute(ddl)
        if with_plan:
            for ddl in PLAN_DDL:
                db.execute(ddl)
        return tuple(db.execute(_SCHEMA_QUERY).fetchall())


def schema_present(db):
    """Accept only complete known optional schemas; plans confer no RR authority."""
    actual = tuple(tuple(row) for row in db.execute(_SCHEMA_QUERY))
    for drafts in (False, True):
        for plans in (False, True):
            for rr in (False, True):
                if actual == _expected_schema(rr, drafts, plans):
                    if plans and tuple(tuple(row) for row in db.execute(
                            'SELECT version FROM plan_schema')) != ((1,),):
                        raise RRFeedbackError(503, 'RR_STORE_SCHEMA_REJECTED')
                    return rr
    raise RRFeedbackError(503, 'RR_STORE_SCHEMA_REJECTED')


def _attachment_meta(item):
    return {key: item[key] for key in ('attachment_id', 'size', 'sha256', 'filename', 'content_type')}


def _digests(items):
    return [{key: item[key] for key in ('attachment_id', 'size', 'sha256')} for item in items]


class RRFeedbackStore:
    def __init__(self, task_store, project_id='personal-control-hub'):
        opaque(project_id)
        self.task_store, self.project_id = task_store, project_id

    @contextlib.contextmanager
    def _connection(self, write=False, guard=None, create=False):
        try:
            with self.task_store._connection(write, guard=guard) as db:
                present = schema_present(db)
                if create and not present:
                    # executescript would commit the TaskStore transaction implicitly.
                    for ddl in RR_DDL:
                        db.execute(ddl)
                    present = True
                yield db, present
        except ServiceError as error:
            raise RRFeedbackError(503, 'RR_STORE_UNAVAILABLE', outcome=error.outcome) from None
        except (sqlite3.Error, OSError):
            raise RRFeedbackError(503, 'RR_STORE_UNAVAILABLE') from None

    def submit(self, device_id, document, attachments, guard=None):
        opaque(device_id)
        verified = validate_submission(document, attachments, self.project_id)
        document = verified['document']
        body = verified['body'].decode(); digest = verified['body_sha256']
        with self._connection(True, guard=guard, create=True) as (db, _):
            row = db.execute('SELECT feedback_id FROM rr_feedback WHERE device_id=? AND project_id=? AND request_uuid=?',
                             (device_id, self.project_id, document['client_request_id'])).fetchone()
            if row:
                item = self._load(db, row['feedback_id'], device_id)
                if item['receipt']['payload_sha256'] != digest or item['document'] != verified['document']:
                    raise RRFeedbackError(409, 'RR_IDEMPOTENCY_CONFLICT')
                return 200, item['receipt']
            ident = str(uuid.uuid4())
            receipt = {'schema': 1, 'project_id': self.project_id, 'client_request_id': document['client_request_id'],
                       'feedback_id': ident, 'revision': 1, 'accepted_at': _now(), 'body_sha256': digest,
                       'payload_sha256': digest, 'attachments': [_attachment_meta(a) for a in verified['attachments']]}
            raw_receipt = canonical_json(receipt)
            db.execute('INSERT INTO rr_feedback(feedback_id,device_id,project_id,request_uuid,revision,body,body_sha256,payload_sha256,receipt,receipt_sha256) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (ident, device_id, self.project_id, document['client_request_id'], 1, body, digest, digest,
                        raw_receipt.decode(), _sha(raw_receipt)))
            for ordinal, item in enumerate(verified['attachments']):
                db.execute('INSERT INTO rr_attachments VALUES(?,?,?,?,?,?,?,?)',
                           (ident, ordinal, item['attachment_id'], item['filename'], item['content_type'],
                            item['size'], item['sha256'], item['data']))
            # Separate injectable boundary before TaskStore's own commit/fault hooks.
            self.task_store.fault('rr_before_commit')
        return 201, receipt

    def _load(self, db, ident, device_id=None):
        row = db.execute('SELECT * FROM rr_feedback WHERE feedback_id=? AND project_id=?', (ident, self.project_id)).fetchone()
        if row is None or (device_id is not None and row['device_id'] != device_id):
            raise RRFeedbackError(404, 'RR_FEEDBACK_NOT_FOUND')
        try:
            opaque(row['device_id']); valid_uuid(row['feedback_id']); valid_uuid(row['request_uuid'])
            if type(row['body']) is not str or type(row['sequence']) is not int or row['sequence'] < 1:
                raise ValueError()
            attachments = []
            for ordinal, item in enumerate(db.execute('SELECT * FROM rr_attachments WHERE feedback_id=? ORDER BY ordinal', (ident,))):
                if item['ordinal'] != ordinal or type(item['data']) is not bytes:
                    raise ValueError()
                actual = {key: item[key] for key in ('attachment_id', 'filename', 'content_type', 'data')}
                if len(actual['data']) != item['size'] or _sha(actual['data']) != item['sha256']:
                    raise ValueError()
                attachments.append(actual)
            verified = validate_submission(parse_document(row['body'].encode('utf-8')), attachments, self.project_id)
            receipt = _decode(row['receipt'], row['receipt_sha256'])
            expected = {'schema': 1, 'project_id': self.project_id, 'client_request_id': row['request_uuid'],
                        'feedback_id': ident, 'revision': 1, 'accepted_at': receipt['accepted_at'],
                        'body_sha256': verified['body_sha256'], 'payload_sha256': verified['body_sha256'],
                        'attachments': [_attachment_meta(a) for a in verified['attachments']]}
            timestamp(receipt['accepted_at'])
            if (row['revision'] != 1 or row['body'] != verified['body'].decode()
                    or row['body_sha256'] != verified['body_sha256'] or row['payload_sha256'] != verified['body_sha256']
                    or verified['document']['client_request_id'] != row['request_uuid'] or receipt != expected):
                raise ValueError()
            return {'receipt': receipt, 'document': verified['document'], 'attachments': verified['attachments']}
        except (RRFeedbackError, ValueError, TypeError, KeyError, UnicodeError):
            raise RRFeedbackError(503, 'RR_STORE_CORRUPT') from None

    def _state(self, db, item):
        ident = item['receipt']['feedback_id']
        ack = db.execute('SELECT * FROM rr_acks WHERE feedback_id=?', (ident,)).fetchone()
        result = db.execute('SELECT * FROM rr_results WHERE feedback_id=?', (ident,)).fetchone()
        if result and not ack:
            raise RRFeedbackError(503, 'RR_STORE_CORRUPT')
        state = 'processed' if result else 'agent_read' if ack else 'pending'
        value = {'receipt': item['receipt'], 'document': item['document'], 'state': state}
        if ack:
            value['ack'] = self._validate_ack(db, item, ack)
        if result:
            value['result'] = self._validate_result(item, ack, result)
        return value

    def get(self, device_id, feedback_id):
        opaque(device_id); valid_uuid(feedback_id)
        with self._connection() as (db, present):
            if not present:
                raise RRFeedbackError(404, 'RR_FEEDBACK_NOT_FOUND')
            return self._state(db, self._load(db, feedback_id, device_id))

    def by_request(self, device_id, request_uuid):
        opaque(device_id); valid_uuid(request_uuid)
        with self._connection() as (db, present):
            row = db.execute('SELECT feedback_id FROM rr_feedback WHERE device_id=? AND project_id=? AND request_uuid=?',
                             (device_id, self.project_id, request_uuid)).fetchone() if present else None
            if not row:
                raise RRFeedbackError(404, 'RR_FEEDBACK_NOT_FOUND')
            return self._state(db, self._load(db, row['feedback_id'], device_id))

    def attachment(self, device_id, feedback_id, attachment_id):
        opaque(device_id); valid_uuid(feedback_id); opaque(attachment_id)
        with self._connection() as (db, present):
            if not present:
                raise RRFeedbackError(404, 'RR_FEEDBACK_NOT_FOUND')
            item = self._load(db, feedback_id, device_id)
            for attachment in item['attachments']:
                if attachment['attachment_id'] == attachment_id:
                    return dict(attachment)
            raise RRFeedbackError(404, 'RR_FEEDBACK_NOT_FOUND')

    def pending(self, limit=20, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise RRFeedbackError(400, 'RR_QUERY_INVALID')
        if cursor is None:
            after = 0
        elif type(cursor) is str and cursor.isascii() and cursor.isdigit() and 0 < len(cursor) <= 19 and str(int(cursor)) == cursor and int(cursor) < 2**63:
            after = int(cursor)
        else:
            raise RRFeedbackError(400, 'RR_QUERY_INVALID')
        with self._connection() as (db, present):
            rows = db.execute('SELECT sequence,feedback_id FROM rr_feedback f WHERE project_id=? AND sequence>? AND NOT EXISTS(SELECT 1 FROM rr_results r WHERE r.feedback_id=f.feedback_id) ORDER BY sequence LIMIT ?',
                              (self.project_id, after, limit + 1)).fetchall() if present else []
            items = [self._state(db, self._load(db, row['feedback_id'])) for row in rows[:limit]]
            return {'items': items, 'next_cursor': str(rows[limit - 1]['sequence']) if len(rows) > limit else None}

    def _proof(self, item, read_id):
        return {'read_id': read_id, 'revision': item['receipt']['revision'],
                'body_sha256': item['receipt']['body_sha256'], 'attachments': _digests(item['attachments'])}

    def _read_record(self, db, item, agent_id):
        row = db.execute('SELECT * FROM rr_reads WHERE feedback_id=? AND agent_id=?',
                         (item['receipt']['feedback_id'], agent_id)).fetchone()
        if row is None:
            return None
        proof = _decode(row['proof'], row['hash'])
        try:
            valid_uuid(row['read_id']); opaque(row['agent_id'])
            if proof != self._proof(item, row['read_id']):
                raise ValueError()
        except (ValueError, RRFeedbackError):
            raise RRFeedbackError(503, 'RR_STORE_CORRUPT') from None
        return proof

    def read_for_agent(self, feedback_id, agent_id):
        valid_uuid(feedback_id); opaque(agent_id)
        # The protected caller receives actual bytes, never a marker based on HTTP status.
        with self._connection(True) as (db, present):
            if not present:
                raise RRFeedbackError(404, 'RR_FEEDBACK_NOT_FOUND')
            item = self._load(db, feedback_id)
            proof = self._read_record(db, item, agent_id)
            if proof is None:
                proof = self._proof(item, str(uuid.uuid4())); raw = canonical_json(proof)
                db.execute('INSERT INTO rr_reads VALUES(?,?,?,?,?)',
                           (proof['read_id'], feedback_id, agent_id, raw.decode(), _sha(raw)))
            return {**item, 'read_receipt': proof}

    def _check_proof(self, db, item, agent_id, proof):
        expected = self._read_record(db, item, agent_id)
        try:
            matches = type(proof) is dict and expected is not None and canonical_json(proof) == canonical_json(expected)
        except RRFeedbackError:
            matches = False
        if not matches:
            raise RRFeedbackError(409, 'RR_READ_PROOF_CONFLICT')
        return expected

    def _validate_ack(self, db, item, row):
        value = _decode(row['body'], row['hash'])
        try:
            valid_uuid(row['ack_id']); opaque(row['agent_id'])
            proof = self._read_record(db, item, row['agent_id'])
            expected = {'ack_id': row['ack_id'], 'feedback_id': item['receipt']['feedback_id'],
                        'agent_id': row['agent_id'], 'read_id': row['read_id'], 'revision': 1,
                        'body_sha256': item['receipt']['body_sha256'], 'attachments': _digests(item['attachments']),
                        'acknowledged_at': value['acknowledged_at']}
            timestamp(value['acknowledged_at'])
            if value != expected or proof is None or proof['read_id'] != row['read_id']:
                raise ValueError()
        except (ValueError, TypeError, KeyError, RRFeedbackError):
            raise RRFeedbackError(503, 'RR_STORE_CORRUPT') from None
        return value

    def ack(self, feedback_id, agent_id, proof):
        valid_uuid(feedback_id); opaque(agent_id)
        with self._connection(True) as (db, present):
            if not present:
                raise RRFeedbackError(404, 'RR_FEEDBACK_NOT_FOUND')
            item = self._load(db, feedback_id)
            expected = self._check_proof(db, item, agent_id, proof)
            old = db.execute('SELECT * FROM rr_acks WHERE feedback_id=?', (feedback_id,)).fetchone()
            if old:
                value = self._validate_ack(db, item, old)
                if value['agent_id'] != agent_id or value['read_id'] != expected['read_id']:
                    raise RRFeedbackError(409, 'RR_ACK_CONFLICT')
                return value
            value = {'ack_id': str(uuid.uuid4()), 'feedback_id': feedback_id, 'agent_id': agent_id,
                     **expected, 'acknowledged_at': _now()}
            raw = canonical_json(value)
            db.execute('INSERT INTO rr_acks VALUES(?,?,?,?,?,?)',
                       (feedback_id, value['ack_id'], agent_id, expected['read_id'], raw.decode(), _sha(raw)))
            return value

    @staticmethod
    def _handling(summary, references):
        if type(summary) is not str or not summary.strip() or len(summary.encode('utf-8', errors='strict')) > 8192:
            raise RRFeedbackError(400, 'RR_RESULT_INVALID')
        if type(references) is not list or len(references) > 16 or any(type(r) is not str or len(r) > 1024 for r in references):
            raise RRFeedbackError(400, 'RR_RESULT_INVALID')
        raw = canonical_json({'summary': summary, 'references': references})
        if len(raw) > 16384:
            raise RRFeedbackError(413, 'RR_RESULT_TOO_LARGE')

    def _validate_result(self, item, ack, row):
        value = _decode(row['body'], row['hash'])
        try:
            valid_uuid(row['result_id']); opaque(row['agent_id'])
            self._handling(value['summary'], value['references']); timestamp(value['processed_at'])
            expected = {'result_id': row['result_id'], 'feedback_id': item['receipt']['feedback_id'],
                        'agent_id': row['agent_id'], 'ack_id': row['ack_id'], 'revision': 1,
                        'body_sha256': item['receipt']['body_sha256'], 'summary': value['summary'],
                        'references': value['references'], 'processed_at': value['processed_at']}
            if value != expected or ack is None or row['ack_id'] != ack['ack_id'] or row['agent_id'] != ack['agent_id']:
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError, RRFeedbackError):
            raise RRFeedbackError(503, 'RR_STORE_CORRUPT') from None
        return value

    def result(self, feedback_id, agent_id, proof, summary, references):
        valid_uuid(feedback_id); opaque(agent_id)
        try:
            self._handling(summary, references)
        except UnicodeError:
            raise RRFeedbackError(400, 'RR_RESULT_INVALID') from None
        with self._connection(True) as (db, present):
            if not present:
                raise RRFeedbackError(404, 'RR_FEEDBACK_NOT_FOUND')
            item = self._load(db, feedback_id)
            self._check_proof(db, item, agent_id, proof)
            ack = db.execute('SELECT * FROM rr_acks WHERE feedback_id=?', (feedback_id,)).fetchone()
            if ack is None:
                raise RRFeedbackError(409, 'RR_ACK_REQUIRED')
            ack_value = self._validate_ack(db, item, ack)
            if ack_value['agent_id'] != agent_id:
                raise RRFeedbackError(409, 'RR_ACK_CONFLICT')
            old = db.execute('SELECT * FROM rr_results WHERE feedback_id=?', (feedback_id,)).fetchone()
            if old:
                value = self._validate_result(item, ack, old)
                if value['summary'] != summary or value['references'] != references:
                    raise RRFeedbackError(409, 'RR_RESULT_CONFLICT')
                return value
            value = {'result_id': str(uuid.uuid4()), 'feedback_id': feedback_id, 'agent_id': agent_id,
                     'ack_id': ack['ack_id'], 'revision': 1, 'body_sha256': item['receipt']['body_sha256'],
                     'summary': summary, 'references': references, 'processed_at': _now()}
            raw = canonical_json(value)
            db.execute('INSERT INTO rr_results VALUES(?,?,?,?,?,?)',
                       (feedback_id, value['result_id'], agent_id, ack['ack_id'], raw.decode(), _sha(raw)))
            return value


def validate_snapshot(db):
    """Read-only optional-schema and full-row checks for trusted offline restore.

    Byte payloads become verified SHA/size summaries only in the comparison
    projection; restoration still preserves the original pinned database bytes.
    """
    if not schema_present(db):
        return {}
    previous_factory = db.row_factory
    db.row_factory = sqlite3.Row
    try:
        store = object.__new__(RRFeedbackStore)
        identities = db.execute('SELECT feedback_id,project_id FROM rr_feedback ORDER BY sequence').fetchall()
        for row in identities:
            store.project_id = row['project_id']; opaque(store.project_id)
            item = store._load(db, row['feedback_id'])
            store._state(db, item)
            for read in db.execute('SELECT agent_id FROM rr_reads WHERE feedback_id=?', (row['feedback_id'],)):
                store._read_record(db, item, read['agent_id'])
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise RRFeedbackError(503, 'RR_STORE_CORRUPT')
        content = {}
        for table in RR_TABLES:
            rows = []
            for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid'):
                values = list(row)
                if table == 'rr_attachments':
                    index = list(row.keys()).index('data')
                    values[index] = {'size': len(row['data']), 'sha256': _sha(row['data'])}
                rows.append(values)
            content[table] = rows
        return content
    except RRFeedbackError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise RRFeedbackError(503, 'RR_STORE_CORRUPT') from None
    finally:
        db.row_factory = previous_factory
