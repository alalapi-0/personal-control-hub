"""Synthetic same-ledger RR/plan compatibility; never open live project data."""
import copy
import shutil
import sqlite3
import unittest

import test_hub_rr_feedback as feedback
from test_hub_plan_admission import BASE, plan, task
from hub.plan_admission import PlanAuthority
from hub.rr_feedback import RRFeedbackStore, RR_TABLES, schema_present, validate_snapshot
from hub.rr_payload import RRFeedbackError
from hub.task_store import TaskStore, PLAN_TABLES
from hub.task_storage_restore import _snapshot
from hub.workbench_store import digest


class PlanRRCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = feedback.FeedbackStoreTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store, self.rr = self.fixture.task, self.fixture.rr

    def import_plan(self):
        value = plan([task(BASE)])
        authority = PlanAuthority.configured(
            {'plan_sha256': digest(value)}, authorize=lambda: None,
            controls=lambda: {'state': 'active', 'revision': 1},
            routes=lambda _: False, package_for=lambda _: None)
        self.store.import_plan(value, authority=authority)

    @staticmethod
    def rows(store, tables):
        with sqlite3.connect(store.path) as db:
            return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                    for table in tables}

    def save_draft(self):
        self.store.save_ordinary_draft(
            {'request_id': 'fixture-draft', 'expected_revision': 0,
             'project_id': 'fixture', 'parent_task_id': None,
             'value': {'objective': 'Retained synthetic draft'}},
            'fixture-owner', lambda: None)

    def rr_journey(self, rr):
        parts = [feedback.attachment()]
        doc = feedback.document(parts)
        status, receipt = rr.submit('device-1', doc, parts)
        self.assertEqual(status, 201)
        self.assertEqual(rr.submit('device-1', doc, parts), (200, receipt))
        ident = receipt['feedback_id']
        self.assertEqual(rr.pending()['items'][0]['receipt'], receipt)
        read = rr.read_for_agent(ident, 'fixture-agent')
        self.assertEqual(read['document'], doc)
        self.assertEqual(read['attachments'][0]['data'], parts[0]['data'])
        proof = read['read_receipt']
        bad = copy.deepcopy(proof); bad['body_sha256'] = '0' * 64
        before = rr.task_store.path.read_bytes()
        with self.assertRaises(RRFeedbackError):
            rr.ack(ident, 'fixture-agent', bad)
        with self.assertRaises(RRFeedbackError):
            rr.ack(ident, 'other-agent', proof)
        self.assertEqual(rr.task_store.path.read_bytes(), before)
        ack = rr.ack(ident, 'fixture-agent', proof)
        result = rr.result(ident, 'fixture-agent', proof, 'Synthetic handling only', [])
        self.assertEqual(result['ack_id'], ack['ack_id'])
        self.assertEqual(rr.result(ident, 'fixture-agent', proof, 'Synthetic handling only', []), result)
        self.assertEqual(rr.pending()['items'], [])
        self.assertEqual(rr.get('device-1', ident)['state'], 'processed')
        self.assertEqual(rr.by_request('device-1', doc['client_request_id'])['result'], result)
        with self.assertRaises(RRFeedbackError):
            rr.get('other-device', ident)
        return ident, doc, parts

    def test_public_pending_before_and_after_plan_import_is_read_only(self):
        self.assertEqual(self.rr.pending(), {'items': [], 'next_cursor': None})
        self.import_plan()
        before = self.store.path.read_bytes()
        self.assertEqual(self.rr.pending(), {'items': [], 'next_cursor': None})
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.tasks(), [])

    def test_exact_optional_schema_matrix_and_lazy_rr_creation(self):
        for plans in (False, True):
            for drafts in (False, True):
                for existing_rr in (False, True):
                    with self.subTest(plans=plans, drafts=drafts, rr=existing_rr):
                        fixture = PlanRRCompatibilityTests(); fixture.setUp()
                        try:
                            if drafts:
                                fixture.save_draft()
                            if existing_rr:
                                fixture.fixture.submit()
                            rr_before = (self.rows(fixture.store, RR_TABLES)
                                         if existing_rr else None)
                            if plans:
                                fixture.import_plan()
                            if existing_rr:
                                self.assertEqual(self.rows(fixture.store, RR_TABLES), rr_before)
                            before = fixture.store.path.read_bytes()
                            with sqlite3.connect(fixture.store.path) as db:
                                self.assertEqual(schema_present(db), existing_rr)
                            self.assertEqual(len(fixture.rr.pending()['items']), int(existing_rr))
                            self.assertEqual(fixture.store.path.read_bytes(), before)
                            if not existing_rr:
                                self.assertEqual(fixture.fixture.submit()[0], 201)
                            with sqlite3.connect(fixture.store.path) as db:
                                self.assertTrue(schema_present(db))
                            self.assertEqual(fixture.store.tasks(), [])
                        finally:
                            fixture.doCleanups()

    def test_public_rr_full_journey_preserves_plan_draft_and_archive_history(self):
        from test_hub_plan_admission import ContractPlanIntegrationTests
        f = ContractPlanIntegrationTests(); f.setUp(); self.addCleanup(f.doCleanups)
        f.settled()
        f.store.archive_plan_completed(f.p['plan_id'], authority=f.cap)
        conflict = copy.deepcopy(f.records[BASE])
        conflict['evidence_ref'] = 'synthetic/later-root-record.json'
        f.records[BASE] = conflict; f.configure()
        self.assertTrue(f.store.record_plan_evidence(
            f.p['plan_id'], conflict, authority=f.cap)['requires_reconcile'])
        self.store = f.store
        self.save_draft()
        tables = PLAN_TABLES + ('ordinary_drafts', 'ordinary_draft_requests')
        before = self.rows(self.store, tables)
        original_task, original_events = self.store.task(BASE), self.store.events(BASE)
        rr = RRFeedbackStore(self.store)
        ident, doc, parts = self.rr_journey(rr)
        self.assertEqual(self.rows(self.store, tables), before)
        self.assertEqual(self.store.task(BASE), original_task)
        self.assertEqual(self.store.events(BASE), original_events)
        self.assertEqual(len(before['plan_archive_tasks']), 1)
        self.assertTrue(before['plan_archive_events'])
        rr_before = self.rows(self.store, RR_TABLES)
        with sqlite3.connect(self.store.path) as db:
            verified = validate_snapshot(db)
        self.assertEqual(set(verified), set(RR_TABLES))
        content = _snapshot(self.store, self.store.path.read_bytes())[1]
        for table in tables + RR_TABLES:
            self.assertTrue(content[table], table)
        target = self.store.root.parent / 'rr-plan-restored'; target.mkdir(mode=0o700)
        copied = TaskStore(target); copied.path.parent.mkdir(parents=True, mode=0o700)
        shutil.copy2(self.store.path, copied.path)
        shutil.copy2(self.store.registration_path, copied.registration_path)
        result = copied.restore_storage(
            database_sha256=feedback.sha(copied.path.read_bytes()),
            registration_sha256=feedback.sha(copied.registration_path.read_bytes()),
            confirm_offline=True)
        self.assertEqual(result['business_sha256'], digest(content))
        self.assertEqual(_snapshot(copied, copied.path.read_bytes())[1], content)
        self.assertEqual(self.rows(copied, tables), before)
        self.assertEqual(self.rows(copied, RR_TABLES), rr_before)
        self.assertEqual(RRFeedbackStore(copied).get('device-1', ident)['document'], doc)
        self.assertEqual(RRFeedbackStore(copied).attachment('device-1', ident, 'note')['data'], parts[0]['data'])
        self.assertEqual(self.rows(self.store, tables), before)
        self.assertEqual(self.rows(self.store, RR_TABLES), rr_before)

    def test_partial_custom_schema_and_invalid_plan_versions_fail_without_writes(self):
        self.import_plan(); self.save_draft(); self.fixture.submit()
        original = self.store.path.read_bytes()
        mutations = [
            'DROP TABLE plan_conflicts',
            'DROP TABLE ordinary_draft_requests',
            'DROP TABLE rr_results',
            'DROP INDEX plan_archive_event_id',
            'CREATE INDEX unexpected ON plan_receipts(task_id)',
            'CREATE TABLE plan_surprise(id TEXT)',
            'CREATE VIEW unexpected AS SELECT * FROM plan_inputs',
            'CREATE TRIGGER unexpected AFTER INSERT ON rr_feedback BEGIN DELETE FROM plan_inputs; END',
            'ALTER TABLE plan_receipts ADD COLUMN unexpected TEXT',
            'DELETE FROM plan_schema',
            'INSERT INTO plan_schema VALUES(1)',
            'PRAGMA ignore_check_constraints=ON; UPDATE plan_schema SET version=99',
        ]
        for sql in mutations:
            with self.subTest(sql=sql):
                with sqlite3.connect(':memory:') as db:
                    db.deserialize(original); db.executescript(sql); db.commit()
                    raw = db.serialize()
                    with self.assertRaises(RRFeedbackError) as error:
                        schema_present(db)
                    self.assertEqual(error.exception.code, 'RR_STORE_SCHEMA_REJECTED')
                self.store.path.write_bytes(raw)
                for write, call in ((False, self.rr.pending),
                                    (True, lambda: self.rr.submit('device-1', feedback.document(), []))):
                    with self.assertRaises(RRFeedbackError) as error:
                        call()
                    # SQLite's read-only quick_check does not check CHECK
                    # constraints; the RR version guard must still reject.
                    # Writes fail earlier at TaskStore's integrity gate.
                    expected = ('RR_STORE_UNAVAILABLE' if write and 'version=99' in sql
                                else 'RR_STORE_SCHEMA_REJECTED')
                    self.assertEqual(error.exception.code, expected)
                    self.assertEqual(self.store.path.read_bytes(), raw)
        self.store.path.write_bytes(original)

    def test_rr_corruption_and_proof_checks_still_fail_with_plan_extension(self):
        self.import_plan()
        _, receipt = self.fixture.submit([feedback.attachment()])
        ident = receipt['feedback_id']
        proof = self.rr.read_for_agent(ident, 'fixture-agent')['read_receipt']
        plan_rows = self.rows(self.store, PLAN_TABLES)
        with sqlite3.connect(self.store.path) as db:
            db.execute('UPDATE rr_attachments SET data=?', (b'corrupted',))
        raw = self.store.path.read_bytes()
        for call in (self.rr.pending,
                     lambda: self.rr.read_for_agent(ident, 'fixture-agent'),
                     lambda: self.rr.ack(ident, 'fixture-agent', proof)):
            with self.assertRaises(RRFeedbackError) as error:
                call()
            self.assertEqual(error.exception.code, 'RR_STORE_CORRUPT')
            self.assertEqual(self.store.path.read_bytes(), raw)
        self.assertEqual(self.rows(self.store, PLAN_TABLES), plan_rows)

    def test_original_feedback_proof_and_ack_remain_valid_after_plan_import(self):
        parts = [feedback.attachment()]
        _, receipt = self.fixture.submit(parts)
        ident = receipt['feedback_id']
        proof = self.rr.read_for_agent(ident, 'fixture-agent')['read_receipt']
        ack = self.rr.ack(ident, 'fixture-agent', proof)
        before = self.rows(self.store, RR_TABLES)
        self.import_plan(); self.save_draft()
        self.assertEqual(self.rows(self.store, RR_TABLES), before)
        plans = self.rows(self.store, PLAN_TABLES)
        self.assertEqual(self.rr.read_for_agent(ident, 'fixture-agent')['read_receipt'], proof)
        self.assertEqual(self.rr.ack(ident, 'fixture-agent', proof), ack)
        result = self.rr.result(ident, 'fixture-agent', proof, 'Synthetic existing feedback', [])
        self.assertEqual(result['ack_id'], ack['ack_id'])
        self.assertEqual(self.rr.get('device-1', ident)['state'], 'processed')
        after = self.rows(self.store, RR_TABLES)
        for table in RR_TABLES[:-1]:
            self.assertEqual(after[table], before[table])
        self.assertEqual(self.rows(self.store, PLAN_TABLES), plans)


if __name__ == '__main__':
    unittest.main()

class RevisionRRRestoreTests(unittest.TestCase):
    def test_revision_history_with_original_contract_archive_rr_and_drafts_restores(self):
        from test_hub_plan_admission import ContractPlanIntegrationTests
        from hub.plan_admission import plan_history
        f=ContractPlanIntegrationTests();f.setUp();self.addCleanup(f.doCleanups)
        f.settled();old=copy.deepcopy(f.p);old_events=f.store.events(BASE)
        f.store.archive_plan_completed(f.p['plan_id'],authority=f.cap)
        target=copy.deepcopy(old);target['revision']='2';f.p=target;f.configure()
        binding=f.cap.snapshot();binding['reconcile_from_sha256']=digest(old)
        cap=PlanAuthority.configured(binding,authorize=lambda:None,controls=lambda:{'state':'active','revision':1},routes=lambda _:False,package_for=lambda _:None)
        f.store.reconcile_plan(target,expected_sha256=digest(old),request_id='revision-archive',authority=cap)
        self.assertIn(BASE,f.store.plan_ready(old['plan_id'],authority=cap)['accepted'])
        # A new revision cannot relabel the old package/receipt as a new PASS.
        forged=copy.deepcopy(f.records[BASE]);forged['plan_sha256']=digest(target)
        bad_binding=copy.deepcopy(binding);bad_binding['verified_evidence'][BASE]=digest(forged)
        bad_cap=PlanAuthority.configured(bad_binding,authorize=lambda:None,controls=lambda:{'state':'active','revision':1},routes=lambda _:False,package_for=lambda _:None)
        from hub.service_contract import ServiceError
        before=f.store.path.read_bytes()
        with self.assertRaises(ServiceError):f.store.record_plan_evidence(old['plan_id'],forged,authority=bad_cap)
        self.assertEqual(f.store.path.read_bytes(),before)
        helper=PlanRRCompatibilityTests();helper.store=f.store
        helper.save_draft();rr=RRFeedbackStore(f.store);ident,_,_=helper.rr_journey(rr)
        content=_snapshot(f.store,f.store.path.read_bytes())[1]
        with f.store._connection()as db:
            history=f.store._decode(*db.execute('SELECT body,hash FROM plan_inputs').fetchone())
        self.assertEqual(len(plan_history(history)[1]),2)
        target_root=f.store.root.parent/'revision-restored';target_root.mkdir(mode=0o700)
        copied=TaskStore(target_root);copied.path.parent.mkdir(parents=True,mode=0o700)
        shutil.copy2(f.store.path,copied.path);shutil.copy2(f.store.registration_path,copied.registration_path)
        result=copied.restore_storage(database_sha256=feedback.sha(copied.path.read_bytes()),registration_sha256=feedback.sha(copied.registration_path.read_bytes()),confirm_offline=True)
        self.assertEqual(result['business_sha256'],digest(content));self.assertEqual(_snapshot(copied,copied.path.read_bytes())[1],content)
        self.assertEqual(copied.events(BASE),old_events)
        self.assertEqual(RRFeedbackStore(copied).get('device-1',ident)['state'],'processed')
        self.assertIn(BASE,copied.plan_ready(old['plan_id'],authority=cap)['accepted'])
