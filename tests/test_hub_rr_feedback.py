"""Public, disk-local RR fixtures. Never open the live ledger or a socket."""
import concurrent.futures
import base64
import copy
import hashlib
import json
import shutil
import sqlite3
import struct
import sys
import tempfile
import threading
import unittest
import uuid
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from hub.rr_payload import (RRFeedbackError, canonical_json, parse_document,
                            validate_submission, MAX_ATTACHMENT)
from hub.rr_feedback import RRFeedbackStore, RR_TABLES, validate_snapshot
from hub.task_store import TaskStore
from hub.task_storage_restore import _snapshot
from hub.service_contract import ServiceError


def sha(data):
    return hashlib.sha256(data).hexdigest()


def png():
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0\xff')) + chunk(b'IEND', b''))


def document(attachments=None):
    return {'schema': 1, 'client_request_id': str(uuid.uuid4()), 'project_id': 'personal-control-hub',
            'page_path': '/review', 'source': {'commit': 'a' * 40, 'build_id': 'fixture-v1', 'dirty': False},
            'resource': {'id': 'review', 'sha256': 'b' * 64, 'mode': 'fixed', 'preview_version': 'v1'},
            'description': '公开验证反馈', 'expected': 'Expected fixture', 'actual': 'Observed fixture',
            'steps': ['Open the public fixture'], 'submitted_at': '2026-10-19T00:00:00+00:00',
            'attachments': [{'attachment_id': a['attachment_id'], 'size': len(a['data']), 'sha256': sha(a['data'])}
                            for a in attachments or []]}


def attachment(data=b'public text\n', content_type='text/plain', ident='note'):
    return {'attachment_id': ident, 'filename': 'fixture.txt', 'content_type': content_type, 'data': data}


class PayloadTests(unittest.TestCase):
    def reject(self, doc=None, parts=None, status=400):
        with self.assertRaises(RRFeedbackError) as error:
            validate_submission(document() if doc is None else doc, [] if parts is None else parts, 'personal-control-hub')
        self.assertEqual(error.exception.status, status)

    def test_strict_json_rejects_unknown_duplicate_float_constant_utf8_and_surrogates(self):
        for raw in (b'{"schema":1,"schema":1}', b'{"x":0.5}', b'{"x":NaN}', b'{"x":"\xff"}',
                    b'{"x":"\\ud800"}', b'[]', b'{' + b' ' * 16384 + b'}'):
            with self.subTest(raw=raw[:30]), self.assertRaises(RRFeedbackError):
                parse_document(raw)
        doc = document(); doc['unknown'] = 'not accepted'; self.reject(doc)
        doc = document(); doc['source']['unknown'] = False; self.reject(doc)
        self.assertEqual(parse_document(canonical_json(document()))['schema'], 1)

    def test_page_paths_never_select_paths_queries_fragments_or_nested_escapes(self):
        for path in ('/../secret', '/a/./b', '//host/path', '/a?token=x', '/a#x', '/a\\x', '/%2e%2e/secret',
                     '/%252e%252e/secret', '/%0a', '/a b', '/%3fsecret', '/bad%xx'):
            doc = document(); doc['page_path'] = path; self.reject(doc)
        for field in ('project_id', 'client_request_id', 'submitted_at'):
            doc = document(); doc[field] = '../bad'; self.reject(doc)
        doc = document(); doc['submitted_at'] = '2026-10-19T00:00:00'; self.reject(doc)

    def test_types_and_limits_are_enforced_before_store(self):
        for field, value in [('schema', True), ('steps', [1]), ('description', 5), ('source', []), ('resource', None)]:
            doc = document(); doc[field] = value; self.reject(doc)
        doc = document(); doc['source']['dirty'] = 1; self.reject(doc)
        doc = document(); doc['description'] = 'x' * 17000; self.reject(doc, status=413)
        doc = document(); doc['description'] = 'x' * 16000; self.reject(doc, status=413)
        parts = [attachment(ident=str(i)) for i in range(3)]
        self.reject(document(parts), parts, 413)
        parts = [attachment(b'x' * (MAX_ATTACHMENT + 1))]
        self.reject(document(parts), parts, 413)

    def test_attachment_exact_identity_size_digest_and_filename(self):
        parts = [attachment()]
        for field, value in [('size', 0), ('sha256', '0' * 64), ('attachment_id', '../note')]:
            doc = document(parts); doc['attachments'][0][field] = value; self.reject(doc, parts)
        parts = [attachment(ident='same'), attachment(ident='same')]; self.reject(document(parts), parts)
        for name in ('../secret', 'a/b', 'a\\b', 'newline\nname'):
            parts = [attachment()]; parts[0]['filename'] = name; self.reject(document(parts), parts)
        self.reject(document([attachment()]), [])

    def test_content_matches_mime_and_png_is_structurally_validated(self):
        parts = [attachment(png(), 'image/png')]
        checked = validate_submission(document(parts), parts, 'personal-control-hub')
        self.assertEqual(checked['attachments'][0]['sha256'], sha(png()))
        self.assertEqual(checked['body_sha256'], sha(checked['body']))
        for data, kind in ((b'not png', 'image/png'), (png(), 'image/jpeg'), (b'\xff\xd8\xff\xd9', 'image/jpeg'),
                           (b'RIFF\x04\0\0\0WEBP', 'image/webp'), (b'\xff', 'text/plain'),
                           (b'hello\0secret', 'text/plain'), (b'hello', 'application/octet-stream')):
            parts = [attachment(data, kind)]; self.reject(document(parts), parts, 415)
        corrupted = bytearray(png()); corrupted[-1] ^= 1
        parts = [attachment(bytes(corrupted), 'image/png')]; self.reject(document(parts), parts, 415)


    def test_real_encoded_jpeg_webp_positive_and_structural_damage(self):
        # Fixed public 2x2 red fixtures encoded once with ffmpeg; no runtime codec dependency.
        encoded = [
            ('image/jpeg', '/9j/4AAQSkZJRgABAgAAAQABAAD//gAQTGF2YzYyLjExLjEwMAD/2wBDAAgEBAQEBAUFBQUFBQYGBgYGBgYGBgYGBgYHBwcICAgHBwcGBgcHCAgICAkJCQgICAgJCQoKCgwMCwsODg4RERT/xABMAAEBAAAAAAAAAAAAAAAAAAAABgEBAQAAAAAAAAAAAAAAAAAABgcQAQAAAAAAAAAAAAAAAAAAAAARAQAAAAAAAAAAAAAAAAAAAAD/wAARCAACAAIDASIAAhEAAxEA/9oADAMBAAIRAxEAPwCLAE1/f//Z'),
            ('image/webp', 'UklGRjwAAABXRUJQVlA4IDAAAADQAQCdASoCAAIAAgA0JaACdLoB+AADsAD+8Oj3/yC5YXXI1/8gP+QH/ID/+PIAAAA='),
        ]
        for mime, text in encoded:
            raw = base64.b64decode(text)
            parts = [attachment(raw, mime)]
            self.assertEqual(validate_submission(document(parts), parts, 'personal-control-hub')['attachments'][0]['data'], raw)
            for corrupted in (raw[:-1], raw + b'extra'):
                parts = [attachment(corrupted, mime)]
                self.reject(document(parts), parts, 415)


class FeedbackStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = TaskStore(self.root)
        self.task.register_storage()
        self.rr = RRFeedbackStore(self.task)

    def submit(self, parts=None, doc=None):
        parts = [] if parts is None else parts
        return self.rr.submit('device-1', document(parts) if doc is None else doc, parts)

    def tables(self):
        with sqlite3.connect(self.task.path) as db:
            return db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()

    def test_gets_do_not_create_tables_or_database(self):
        before = self.task.path.read_bytes(); tables = self.tables()
        for method, args in ((self.rr.get, ('device-1', str(uuid.uuid4()))),
                             (self.rr.by_request, ('device-1', str(uuid.uuid4()))),
                             (self.rr.attachment, ('device-1', str(uuid.uuid4()), 'a'))):
            with self.assertRaises(RRFeedbackError) as error:
                method(*args)
            self.assertEqual(error.exception.status, 404)
        self.assertEqual(self.rr.pending(), {'items': [], 'next_cursor': None})
        self.assertEqual(self.tables(), tables); self.assertEqual(self.task.path.read_bytes(), before)
        missing = self.root / 'missing'; missing.mkdir()
        rr = RRFeedbackStore(TaskStore(missing))
        with self.assertRaises(RRFeedbackError) as error:
            rr.pending()
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(list(missing.iterdir()), [])

    def test_new_retry_conflict_and_recovery_bind_actual_bytes(self):
        parts = [attachment(), attachment(png(), 'image/png', 'screen')]; doc = document(parts)
        status, receipt = self.submit(parts, doc)
        self.assertEqual(status, 201); self.assertEqual(receipt['revision'], 1)
        self.assertEqual(receipt['body_sha256'], sha(canonical_json(doc)))
        self.assertEqual(self.submit(parts, doc), (200, receipt))
        item = self.rr.by_request('device-1', doc['client_request_id'])
        self.assertEqual(item['receipt'], receipt); self.assertEqual(item['document'], doc)
        self.assertEqual(item['state'], 'pending')
        self.assertEqual(self.rr.attachment('device-1', receipt['feedback_id'], 'screen')['data'], png())
        changed = copy.deepcopy(doc); changed['description'] = 'different'
        with self.assertRaises(RRFeedbackError) as error:
            self.submit(parts, changed)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.task.tasks(), [])
        with sqlite3.connect(self.task.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM rr_feedback').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM rr_attachments').fetchone()[0], 2)

    def test_device_and_project_are_not_cross_readable(self):
        _, receipt = self.submit([attachment()]); ident = receipt['feedback_id']
        for method, args in ((self.rr.get, ('device-2', ident)),
                             (self.rr.by_request, ('device-2', receipt['client_request_id'])),
                             (self.rr.attachment, ('device-2', ident, 'note'))):
            with self.assertRaises(RRFeedbackError) as error:
                method(*args)
            self.assertEqual(error.exception.status, 404)
        with self.assertRaises(RRFeedbackError) as error:
            RRFeedbackStore(self.task, 'other').get('device-1', ident)
        self.assertEqual(error.exception.status, 404)
        # The same client UUID belongs to another authenticated device independently.
        doc = self.rr.get('device-1', ident)['document']
        self.assertEqual(self.rr.submit('device-2', doc, [attachment()])[0], 201)

    def test_before_commit_failure_rolls_back_ddl_body_attachments_and_receipt(self):
        for phase in ('rr_before_commit', 'before_commit'):
            def fault(current):
                if current == phase:
                    raise OSError('injected fixture failure')
            self.task.fault = fault
            before = self.task.path.read_bytes()
            with self.assertRaises(RRFeedbackError) as error:
                self.submit([attachment()])
            self.assertEqual(error.exception.status, 503)
            self.assertEqual(error.exception.outcome, 'NOT_COMMITTED')
            self.assertEqual(self.task.path.read_bytes(), before)
            self.assertFalse(any(table[0].startswith('rr_') for table in self.tables()))
        self.task.fault = lambda _: None
        self.assertEqual(self.submit()[0], 201)

    def test_guard_failure_is_atomic_and_unknown_commit_is_queryable(self):
        def guard():
            raise RRFeedbackError(503, 'RR_GATEWAY_REVOKED')
        before = self.task.path.read_bytes()
        with self.assertRaises(RRFeedbackError):
            self.rr.submit('device-1', document(), [], guard=guard)
        self.assertEqual(self.task.path.read_bytes(), before)
        doc = document()
        def fault(phase):
            if phase == 'after_commit':
                raise OSError('lost reply fixture')
        self.task.fault = fault
        with self.assertRaises(RRFeedbackError) as error:
            self.submit(doc=doc)
        self.assertEqual(error.exception.outcome, 'UNKNOWN')
        recovered = self.rr.by_request('device-1', doc['client_request_id'])
        self.task.fault = lambda _: None
        self.assertEqual(self.submit(doc=doc), (200, recovered['receipt']))

    def test_concurrent_identical_and_conflicting_requests_stay_unique(self):
        doc = document(); barrier = threading.Barrier(4)
        def run(index):
            barrier.wait(timeout=5)
            rr = RRFeedbackStore(TaskStore(self.root))
            return rr.submit('device-1', copy.deepcopy(doc), [])
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run, range(4)))
        self.assertEqual(sorted(status for status, _ in results), [200, 200, 200, 201])
        self.assertEqual(len({r['feedback_id'] for _, r in results}), 1)
        # Actual competing mismatched payloads must not overwrite an established row.
        changed = copy.deepcopy(doc); changed['actual'] = 'conflict'
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(RRFeedbackStore(TaskStore(self.root)).submit, 'device-1', d, []) for d in (doc, changed)]
            self.assertEqual(futures[0].result()[0], 200)
            with self.assertRaises(RRFeedbackError) as error:
                futures[1].result()
            self.assertEqual(error.exception.status, 409)

    def test_agent_must_read_all_actual_bytes_then_ack_exact_proof(self):
        parts = [attachment(), attachment(png(), 'image/png', 'screen')]
        _, receipt = self.submit(parts); ident = receipt['feedback_id']
        browser = self.rr.get('device-1', ident)
        fabricated = {'read_id': str(uuid.uuid4()), 'revision': 1, 'body_sha256': receipt['body_sha256'],
                      'attachments': []}
        with self.assertRaises(RRFeedbackError):
            self.rr.ack(ident, 'uid-1000', fabricated)
        self.assertEqual(browser['state'], 'pending')
        read = self.rr.read_for_agent(ident, 'uid-1000')
        self.assertEqual([a['data'] for a in read['attachments']], [a['data'] for a in parts])
        self.assertEqual(read['document'], browser['document'])
        proof = read['read_receipt']
        self.assertEqual(self.rr.get('device-1', ident)['state'], 'pending')
        # A browser read alone and another Agent's read never confer ack authority.
        with self.assertRaises(RRFeedbackError):
            self.rr.ack(ident, 'uid-1001', proof)
        for mutation in ('revision', 'body', 'attachment', 'missing', 'read_id'):
            bad = copy.deepcopy(proof)
            if mutation == 'revision':bad['revision'] = 2
            if mutation == 'body':bad['body_sha256'] = '0' * 64
            if mutation == 'attachment':bad['attachments'][0]['sha256'] = '0' * 64
            if mutation == 'missing':bad['attachments'].pop()
            if mutation == 'read_id':bad['read_id'] = str(uuid.uuid4())
            with self.assertRaises(RRFeedbackError) as error:
                self.rr.ack(ident, 'uid-1000', bad)
            self.assertEqual(error.exception.status, 409)
            self.assertEqual(self.rr.get('device-1', ident)['state'], 'pending')
        ack = self.rr.ack(ident, 'uid-1000', proof)
        self.assertEqual(self.rr.ack(ident, 'uid-1000', proof), ack)
        self.assertEqual(self.rr.get('device-1', ident)['state'], 'agent_read')
        result = self.rr.result(ident, 'uid-1000', proof, 'Fixture reviewed, no code execution', ['review:fixture'])
        self.assertEqual(result['ack_id'], ack['ack_id'])
        self.assertEqual(self.rr.result(ident, 'uid-1000', proof, 'Fixture reviewed, no code execution', ['review:fixture']), result)
        with self.assertRaises(RRFeedbackError) as error:
            self.rr.result(ident, 'uid-1000', proof, 'changed', [])
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.rr.get('device-1', ident)['state'], 'processed')
        self.assertEqual(self.rr.pending()['items'], [])
        self.assertEqual(self.task.tasks(), [])

    def test_result_requires_ack_even_after_real_read(self):
        _, receipt = self.submit(); ident = receipt['feedback_id']
        proof = self.rr.read_for_agent(ident, 'uid-1000')['read_receipt']
        with self.assertRaises(RRFeedbackError) as error:
            self.rr.result(ident, 'uid-1000', proof, 'not yet acknowledged', [])
        self.assertEqual(error.exception.code, 'RR_ACK_REQUIRED')

    def test_pending_stable_pagination_and_get_after_existing_rr_is_zero_write(self):
        ids = [self.submit()[1]['feedback_id'] for _ in range(3)]
        first = self.rr.pending(limit=2)
        self.assertEqual([i['receipt']['feedback_id'] for i in first['items']], ids[:2])
        second = self.rr.pending(limit=2, cursor=first['next_cursor'])
        self.assertEqual([i['receipt']['feedback_id'] for i in second['items']], ids[2:])
        before = self.task.path.read_bytes()
        self.rr.get('device-1', ids[0]); self.rr.pending()
        self.assertEqual(self.task.path.read_bytes(), before)
        for cursor in ('-1', '1 OR 1', '01', '9999999999999999999999999'):
            with self.assertRaises(RRFeedbackError):
                self.rr.pending(cursor=cursor)

    def test_corrupt_attachment_blocks_read_and_ack_without_status_promotion(self):
        _, receipt = self.submit([attachment()]); ident = receipt['feedback_id']
        proof = self.rr.read_for_agent(ident, 'uid-1000')['read_receipt']
        with sqlite3.connect(self.task.path) as db:
            db.execute('UPDATE rr_attachments SET data=?', (b'changed',))
        for call in (lambda: self.rr.read_for_agent(ident, 'uid-1000'), lambda: self.rr.ack(ident, 'uid-1000', proof)):
            with self.assertRaises(RRFeedbackError) as error:
                call()
            self.assertEqual(error.exception.status, 503)
        with sqlite3.connect(self.task.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM rr_acks').fetchone()[0], 0)

    def test_total_database_capacity_still_uses_taskstore_rollback(self):
        before = self.task.path.read_bytes()
        parts = [attachment(b'x' * (128 * 1024))]
        with patch('hub.task_store.MAX_DATABASE_BYTES', 80 * 1024):
            with self.assertRaises(RRFeedbackError) as error:
                self.submit(parts)
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(self.task.path.read_bytes(), before)

    def test_restore_accepts_optional_rr_and_ordinary_drafts_preserving_full_content(self):
        command = {'request_id': 'fixture-draft', 'expected_revision': 0, 'project_id': 'fixture',
                   'parent_task_id': None, 'value': {'objective': 'retained public fixture'}}
        self.task.save_ordinary_draft(command, 'fixture-owner', lambda: None)
        _, receipt = self.submit([attachment()]); ident = receipt['feedback_id']
        proof = self.rr.read_for_agent(ident, 'uid-1000')['read_receipt']
        self.rr.ack(ident, 'uid-1000', proof)
        self.rr.result(ident, 'uid-1000', proof, 'Fixture handled', [])
        original = _snapshot(self.task, self.task.path.read_bytes())[1]
        target_root = self.root / 'copy'; target_root.mkdir(mode=0o700)
        copied = TaskStore(target_root)
        copied.path.parent.mkdir(parents=True, mode=0o700)
        shutil.copy2(self.task.path, copied.path); shutil.copy2(self.task.registration_path, copied.registration_path)
        result = copied.restore_storage(database_sha256=sha(copied.path.read_bytes()),
                                        registration_sha256=sha(copied.registration_path.read_bytes()), confirm_offline=True)
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(_snapshot(copied, copied.path.read_bytes())[1], original)
        self.assertEqual(RRFeedbackStore(copied).get('device-1', ident)['state'], 'processed')

    def test_restore_rejects_partial_tables_extra_triggers_and_row_corruption(self):
        _, receipt = self.submit([attachment()]); ident = receipt['feedback_id']
        proof = self.rr.read_for_agent(ident, 'uid-1000')['read_receipt']; self.rr.ack(ident, 'uid-1000', proof)
        raw = self.task.path.read_bytes()
        statements = ["DROP TABLE rr_results", "CREATE TRIGGER surprise AFTER INSERT ON rr_feedback BEGIN DELETE FROM rr_reads; END",
                      "UPDATE rr_attachments SET data=X'00'", "UPDATE rr_feedback SET body_sha256='bad'",
                      "UPDATE rr_reads SET hash='bad'", "UPDATE rr_acks SET read_id='absent'",
                      "UPDATE rr_feedback SET body=X'7b7d'", "UPDATE rr_feedback SET sequence=-1"]
        for statement in statements:
            with self.subTest(statement=statement):
                with sqlite3.connect(':memory:') as db:
                    db.deserialize(raw); db.execute(statement); db.commit(); corrupted = db.serialize()
                with self.assertRaises(ServiceError):
                    _snapshot(self.task, corrupted)


if __name__ == '__main__':
    unittest.main()
