"""Offline copied-store recovery; no real provider or business task is run."""
import hashlib
import json
import multiprocessing
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.service_contract import ServiceError
from hub.task_storage import StorageGuard, blocked
from hub.task_store import TaskStore
from hub.workbench_store import canonical, digest


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def crash_restore(root, pins, phase):
    def fault(current):
        if current == phase:
            os._exit(88)
    TaskStore(root, fault=fault).restore_storage(**pins, confirm_offline=True)


class OfflineTaskRestoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'source'
        self.source.mkdir(mode=0o700)
        self.store = TaskStore(self.source)
        self.store.register_storage()
        self.store.register_grant({'grant_id': 'retained-grant', 'version': 3, 'root': 'fixture-only'})
        self.store.admit({'request_id': 'retained-task', 'project_id': 'fixture', 'parent_task_id': None},
                         {'grant_id': 'retained-grant', 'version': 3})
        self.store.event('retained-task', 'retained-event', {'fixture': True})
        with self.store._connection(True) as db:
            db.execute("UPDATE tasks SET status='requires_reconcile',owner='old-owner',lease_until=123,"
                       "thread_id='old-thread',turn_id='old-turn',provider_status='unknown',cancel=1")
        self.source_bytes = self.bytes(self.store)
        self.number = 0

    def copied(self):
        self.number += 1
        root = self.root / ('copy-' + str(self.number))
        root.mkdir(mode=0o700)
        target = TaskStore(root)
        target.path.parent.mkdir(parents=True, mode=0o700)
        shutil.copy2(self.store.path, target.path)
        shutil.copy2(self.store.registration_path, target.registration_path)
        return target

    def pins(self, store):
        return {'database_sha256': sha(store.path), 'registration_sha256': sha(store.registration_path)}

    def bytes(self, store):
        return (store.path.read_bytes(), store.registration_path.read_bytes())

    def content(self, store):
        with sqlite3.connect(store.path) as db:
            return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                    for table in ('meta', 'tasks', 'events', 'grants', 'sqlite_sequence')}

    def restore(self, store, **kwargs):
        return store.restore_storage(**self.pins(store), confirm_offline=True, **kwargs)

    def rejects_unchanged(self, store, reason=None, **kwargs):
        before = self.bytes(store)
        with self.assertRaises(ServiceError) as error:
            store.restore_storage(**kwargs)
        if reason:
            self.assertEqual(error.exception.details.get('reason_code'), reason)
        self.assertEqual(self.bytes(store), before)
        self.assertEqual(self.bytes(self.store), self.source_bytes)
        return error.exception

    def test_restored_copy_is_ready_with_all_business_rows_and_sequences_preserved(self):
        store = self.copied()
        before = self.content(store)
        self.assertEqual(store.storage_status()['reason_code'], 'PROFILE_REJECTED')
        result = self.restore(store)
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(self.content(store), before)
        self.assertEqual(result['business_sha256'], digest(before))
        reopened = TaskStore(store.root)
        task = reopened.tasks()[0]
        self.assertEqual((task['status'], task['owner'], task['lease_until'], task['thread_id'], task['turn_id']),
                         ('requires_reconcile', 'old-owner', 123, 'old-thread', 'old-turn'))
        self.assertEqual(len(reopened.events('retained-task')), 1)
        self.assertEqual(reopened.grants()[0]['version'], 3)
        self.assertEqual(self.bytes(self.store), self.source_bytes)
        self.rejects_unchanged(store, 'RESTORE_HEALTHY_STORE', **self.pins(store), confirm_offline=True)

    def test_explicit_offline_confirmation_and_exact_two_pins_required(self):
        for confirm in (False, None, 1):
            store = self.copied()
            self.rejects_unchanged(store, 'RESTORE_OFFLINE_CONFIRMATION_REQUIRED', **self.pins(store), confirm_offline=confirm)
        for key, value in [('database_sha256', ''), ('registration_sha256', 'A' * 64),
                           ('database_sha256', '0' * 64), ('registration_sha256', '0' * 64)]:
            store = self.copied()
            pins = self.pins(store); pins[key] = value
            self.rejects_unchanged(store, **pins, confirm_offline=True)

    def test_every_nonterminal_or_unknown_status_is_rejected(self):
        for status in ('queued', 'running', 'waiting_input', 'waiting_approval', 'validating', 'future_status'):
            with self.subTest(status=status):
                store = self.copied()
                with sqlite3.connect(store.path) as db:
                    db.execute('UPDATE tasks SET status=?', (status,))
                self.rejects_unchanged(store, 'RESTORE_TASK_NOT_TERMINAL', **self.pins(store), confirm_offline=True)

    def test_corrupt_database_schema_trigger_and_hashes_do_not_change_copied_files(self):
        for scenario in ('garbage', 'schema1', 'trigger', 'payload', 'event', 'grant', 'foreign-key'):
            with self.subTest(scenario=scenario):
                store = self.copied()
                if scenario == 'garbage':
                    store.path.write_bytes(b'not sqlite')
                else:
                    with sqlite3.connect(store.path) as db:
                        if scenario == 'schema1':
                            db.execute('DROP TABLE meta'); db.execute('CREATE TABLE meta(version INTEGER)'); db.execute('INSERT INTO meta VALUES(1)')
                        if scenario == 'trigger':
                            db.execute('CREATE TRIGGER surprise AFTER UPDATE ON storage_profile BEGIN DELETE FROM tasks; END')
                        if scenario == 'payload':db.execute("UPDATE tasks SET payload_hash='broken'")
                        if scenario == 'event':db.execute("UPDATE events SET hash='broken'")
                        if scenario == 'grant':db.execute("UPDATE grants SET hash='broken'")
                        if scenario == 'foreign-key':db.execute("UPDATE events SET task_id='absent'")
                self.rejects_unchanged(store, **self.pins(store), confirm_offline=True)

    def test_registration_hash_profile_mismatch_or_pending_snapshot_is_rejected(self):
        for scenario in ('hash', 'profile', 'pending'):
            with self.subTest(scenario=scenario):
                store = self.copied()
                wrapper = json.loads(store.registration_path.read_bytes())
                if scenario == 'hash':wrapper['hash'] = '0' * 64
                elif scenario == 'profile':
                    wrapper['record']['profile']['inode'] += 1
                    wrapper['hash'] = digest(wrapper['record'])
                else:
                    wrapper['record']['ledger_identity'] = None
                    wrapper['hash'] = digest(wrapper['record'])
                store.registration_path.write_bytes(canonical(wrapper))
                self.rejects_unchanged(store, **self.pins(store), confirm_offline=True)

    def test_unsafe_files_and_sidecars_are_rejected_without_publication(self):
        for name in ('database', 'registration'):
            for scenario in ('symlink', 'hardlink', 'mode', 'owner'):
                with self.subTest(name=name, scenario=scenario):
                    store = self.copied(); pins = self.pins(store)
                    path = store.path if name == 'database' else store.registration_path
                    if scenario in ('symlink', 'hardlink'):
                        retained = path.with_suffix('.retained'); path.rename(retained)
                        path.symlink_to(retained) if scenario == 'symlink' else os.link(retained, path)
                    elif scenario == 'mode':path.chmod(0o644)
                    if scenario == 'owner':
                        with patch('hub.task_storage_restore.os.getuid', return_value=os.getuid() + 1):
                            self.rejects_unchanged(store, **pins, confirm_offline=True)
                    else:self.rejects_unchanged(store, **pins, confirm_offline=True)
        for suffix in ('-wal', '-shm', '-journal'):
            store = self.copied(); Path(str(store.path) + suffix).write_bytes(b'')
            self.rejects_unchanged(store, 'RESTORE_SIDECAR_PRESENT', **self.pins(store), confirm_offline=True)

    def test_low_space_is_rejected_without_publication(self):
        store = self.copied()
        with patch.object(StorageGuard, 'check_space', side_effect=blocked('LOW_SPACE', capacity=True)):
            self.rejects_unchanged(store, 'LOW_SPACE', **self.pins(store), confirm_offline=True)

    def test_exact_file_identity_replacement_before_publication_is_rejected(self):
        for name in ('database', 'registration'):
            store = self.copied(); pins = self.pins(store); before = self.bytes(store)
            def fault(phase):
                if phase == 'before_restore_pending':
                    path = store.path if name == 'database' else store.registration_path
                    temporary = path.with_suffix('.replacement')
                    shutil.copy2(path, temporary); os.replace(temporary, path)
            store.fault = fault
            with self.assertRaises(ServiceError) as error:
                store.restore_storage(**pins, confirm_offline=True)
            self.assertEqual(error.exception.details['reason_code'], 'RESTORE_PIN_CHANGED')
            self.assertEqual(self.bytes(store), before)
            self.assertEqual(self.bytes(self.store), self.source_bytes)

    def test_mount_change_and_root_replacement_before_publication_are_rejected(self):
        store = self.copied()
        def changed_mount(phase):
            if phase == 'before_restore_pending':
                store.storage_guard.mount = lambda root: {**store.storage_guard.profile['mount'], 'source': 'changed-fixture'}
        store.fault = changed_mount
        self.rejects_unchanged(store, 'MOUNT_IDENTITY_CHANGED', **self.pins(store), confirm_offline=True)
        store = self.copied(); before = self.bytes(store); pins = self.pins(store)
        moved = store.root.with_name('moved-copy')
        def changed_root(phase):
            if phase == 'before_restore_pending':
                store.root.rename(moved); store.root.mkdir(mode=0o700)
        store.fault = changed_root
        with self.assertRaises(ServiceError) as error:
            store.restore_storage(**pins, confirm_offline=True)
        self.assertEqual(error.exception.details['reason_code'], 'ROOT_IDENTITY_CHANGED')
        self.assertEqual(list(store.root.iterdir()), [])
        self.assertEqual(self.bytes(TaskStore(moved)), before)
        self.assertEqual(self.bytes(self.store), self.source_bytes)

    def test_registration_publication_failure_has_unknown_outcome_after_possible_replace(self):
        store = self.copied(); content = self.content(store)
        publish = store._publish_registration
        def uncertain(record, **kwargs):
            publish(record, **kwargs)
            raise OSError('fixture after registration replacement')
        store._publish_registration = uncertain
        with self.assertRaises(ServiceError) as error:
            self.restore(store)
        self.assertEqual(error.exception.outcome, 'UNKNOWN')
        self.assertEqual(TaskStore(store.root).storage_status()['reason_code'], 'REGISTRATION_INCOMPLETE')
        self.assertEqual(self.content(store), content)
        self.assertEqual(self.bytes(self.store), self.source_bytes)

    def test_failures_preserve_explicit_incomplete_state_and_never_auto_recover(self):
        for phase in ('before_restore_pending', 'after_restore_pending', 'after_restore_database',
                      'before_restore_ready', 'after_restore_ready'):
            with self.subTest(phase=phase):
                store = self.copied(); pins = self.pins(store); before = self.bytes(store); content = self.content(store)
                def fault(current):
                    if current == phase:raise OSError('fixture failure')
                store.fault = fault
                with self.assertRaises(ServiceError) as error:
                    store.restore_storage(**pins, confirm_offline=True)
                self.assertEqual(error.exception.code, 'TASK_RESTORE_UNCONFIRMED')
                self.assertEqual(error.exception.outcome, 'NOT_COMMITTED' if phase == 'before_restore_pending' else 'UNKNOWN')
                self.assertEqual(self.content(store), content)
                fresh = TaskStore(store.root)
                if phase == 'before_restore_pending':self.assertEqual(self.bytes(store), before)
                elif phase == 'after_restore_ready':self.assertEqual(fresh.storage_status()['state'], 'ready')
                else:
                    current = self.bytes(store)
                    self.assertEqual(fresh.storage_status()['reason_code'], 'REGISTRATION_INCOMPLETE')
                    for operation in (fresh.tasks, fresh.grants, fresh.register_storage):
                        with self.assertRaises(ServiceError):operation()
                    with self.assertRaises(ServiceError):fresh.restore_storage(**pins, confirm_offline=True)
                    self.assertEqual(self.bytes(store), current)
                self.assertEqual(self.bytes(self.store), self.source_bytes)

    def test_actual_process_crashes_leave_pending_state_blocked_in_a_fresh_process(self):
        for phase in ('after_restore_pending', 'after_restore_database', 'before_restore_ready'):
            store = self.copied(); content = self.content(store)
            child = multiprocessing.get_context('fork').Process(target=crash_restore,
                        args=(str(store.root), self.pins(store), phase))
            child.start(); child.join(5)
            if child.is_alive():
                child.terminate(); child.join(3); self.fail('owned fixture process timed out')
            self.assertEqual(child.exitcode, 88)
            self.assertEqual(TaskStore(store.root).storage_status()['reason_code'], 'REGISTRATION_INCOMPLETE')
            self.assertEqual(self.content(store), content)
            self.assertEqual(self.bytes(self.store), self.source_bytes)

    def test_local_cli_requires_confirmation_and_restores_only_the_selected_copy(self):
        store = self.copied(); pins = self.pins(store); before = self.bytes(store)
        script = Path(__file__).resolve().parents[1] / 'scripts/restore_task_storage.py'
        args = [sys.executable, str(script), '--root', str(store.root),
                '--database-sha256', pins['database_sha256'], '--registration-sha256', pins['registration_sha256']]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2); self.assertEqual(self.bytes(store), before)
        result = subprocess.run(args + ['--confirm-offline'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'], 'ready')
        self.assertEqual(self.bytes(self.store), self.source_bytes)


class PlanOfflineRestoreTests(unittest.TestCase):
    copied = OfflineTaskRestoreTests.copied
    pins = OfflineTaskRestoreTests.pins
    bytes = OfflineTaskRestoreTests.bytes
    restore = OfflineTaskRestoreTests.restore
    rejects_unchanged = OfflineTaskRestoreTests.rejects_unchanged

    def setUp(self):
        import test_hub_plan_admission as fixtures
        self.fixture=fixtures.ContractPlanIntegrationTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.settled();self.store=self.fixture.store
        self.store.archive_plan_completed(self.fixture.p['plan_id'],authority=self.fixture.cap)
        self.root=self.store.root.parent;self.number=0;self.source_bytes=self.bytes(self.store)

    def content(self,store):
        from hub.task_storage_restore import _snapshot
        return _snapshot(store,store.path.read_bytes())[1]

    def mutated(self,statement,params=()):
        with sqlite3.connect(':memory:')as db:
            db.deserialize(self.store.path.read_bytes());db.execute(statement,params);db.commit();return db.serialize()

    def test_extended_snapshot_and_full_offline_restore_preserve_every_table(self):
        original=self.content(self.store);task=self.store.task('P1-R1-PCH-BASE');events=self.store.events('P1-R1-PCH-BASE')
        self.assertEqual(len(original['plan_archive_tasks']),1);self.assertTrue(original['plan_archive_events'])
        copied=self.copied();result=self.restore(copied)
        self.assertEqual(result['state'],'ready');self.assertEqual(self.content(copied),original)
        self.assertEqual(result['business_sha256'],digest(original));self.assertEqual(copied.task(task['id']),task);self.assertEqual(copied.events(task['id']),events)
        self.assertEqual(self.bytes(self.store),self.source_bytes)

    def test_strict_complete_schema_no_partial_custom_ddl_or_unknown_version(self):
        from hub.task_storage_restore import _snapshot
        statements=['DROP TABLE plan_conflicts','DROP INDEX plan_archive_event_id',
                    'CREATE INDEX surprise ON plan_receipts(task_id)',
                    'CREATE VIEW unexpected AS SELECT *FROM plan_inputs',
                    'CREATE TRIGGER unexpected AFTER INSERT ON plan_inputs BEGIN DELETE FROM plan_receipts; END',
                    'PRAGMA ignore_check_constraints=ON']
        for sql in statements[:-1]:
            with self.subTest(sql=sql),self.assertRaises(ServiceError):_snapshot(self.store,self.mutated(sql))
        with sqlite3.connect(':memory:')as db:
            db.deserialize(self.store.path.read_bytes());db.execute(statements[-1]);db.execute('UPDATE plan_schema SET version=99');db.commit()
            with self.assertRaises(ServiceError):_snapshot(self.store,db.serialize())

    def test_hashes_links_sequences_and_nonterminal_archive_are_rejected(self):
        from hub.task_storage_restore import _snapshot
        statements=["UPDATE plan_inputs SET hash='bad'","UPDATE plan_receipts SET hash='bad'",
                    "UPDATE plan_archive_tasks SET payload_hash='bad'","UPDATE plan_archive_events SET hash='bad'",
                    "UPDATE plan_archive_events SET task_id='missing-task'","UPDATE plan_archive_tasks SET status='running'",
                    'UPDATE plan_archive_tasks SET sequence=-1','UPDATE plan_archive_events SET sequence=sequence+10000',
                    "UPDATE sqlite_sequence SET seq=0 WHERE name='tasks'",
                    "UPDATE plan_archive_tasks SET parent='missing-parent'",
                    "UPDATE plan_archive_tasks SET owner='different-owner'",'UPDATE plan_archive_tasks SET lease_until=lease_until+1',
                    'INSERT INTO tasks SELECT *FROM plan_archive_tasks']
        for sql in statements:
            with self.subTest(sql=sql),self.assertRaises(ServiceError):_snapshot(self.store,self.mutated(sql))

    def test_canonical_metadata_mutation_with_recomputed_hash_still_rejects(self):
        from hub.task_storage_restore import _snapshot
        for table,change in [('plan_inputs','id'),('plan_receipts','task'),('plan_receipts','attempt'),('plan_receipts','proof'),('plan_receipts','effect'),('plan_archive_events','fence')]:
            with self.subTest(table=table,change=change):
                with sqlite3.connect(':memory:')as db:
                    db.deserialize(self.store.path.read_bytes());selector=" WHERE event_key LIKE 'rebuild-claim-%'" if change=='fence' else ''
                    value=json.loads(db.execute('SELECT body FROM '+table+selector).fetchone()[0])
                    if change=='id':value['plan_id']='different-plan'
                    if change=='task':value['task_id']='P1-R1-PCH-OFFLINE'
                    if change=='attempt':value['attempt_id']='wrong-attempt'
                    if change=='proof':value['provenance']['receipt_sha256']='e'*64
                    if change=='effect':value['effect_state']='unknown';value['unknowns']=['Unresolved']
                    if change=='fence':value['fence']='e'*64
                    db.execute('UPDATE '+table+' SET body=?,hash=?'+selector,(canonical(value).decode(),digest(value)));db.commit()
                    with self.assertRaises(ServiceError):_snapshot(self.store,db.serialize())

    def test_every_extended_table_contributes_to_business_content_hash(self):
        from hub.task_storage_restore import _snapshot
        original=self.content(self.store)
        raw=self.mutated("UPDATE plan_archive_events SET received=received+0.01 WHERE event_key='rebuild-package'")
        changed=_snapshot(self.store,raw)[1]
        self.assertNotEqual(digest(original),digest(changed));self.assertNotEqual(original['plan_archive_events'],changed['plan_archive_events'])

    def test_historical_proof_expiry_is_preserved_not_reissued(self):
        before=self.content(self.store)
        with patch('time.time',return_value=self.fixture.f.now+86400):after=self.content(self.store)
        self.assertEqual(after,before)

    def test_extended_faults_pins_and_confirmations_keep_original_protection(self):
        for phase in ('before_restore_pending','after_restore_pending','after_restore_database','before_restore_ready','after_restore_ready'):
            with self.subTest(phase=phase):
                copied=self.copied();before=self.content(copied)
                def fault(current):
                    if current==phase:raise OSError('synthetic extended restore failure')
                copied.fault=fault
                with self.assertRaises(ServiceError):self.restore(copied)
                self.assertEqual(self.content(copied),before);self.assertEqual(self.bytes(self.store),self.source_bytes)
        copied=self.copied();self.rejects_unchanged(copied,'RESTORE_OFFLINE_CONFIRMATION_REQUIRED',**self.pins(copied))
        pins=self.pins(copied);pins['database_sha256']='0'*64;self.rejects_unchanged(copied,**pins,confirm_offline=True)

    def test_unlinked_conflict_and_capacity_overflow_reject(self):
        from hub.task_storage_restore import _snapshot
        with sqlite3.connect(':memory:')as db:
            db.deserialize(self.store.path.read_bytes());row=db.execute('SELECT *FROM plan_receipts').fetchone()
            value=json.loads(row[2]);value['attempt_id']='other-attempt'
            db.execute('INSERT INTO plan_conflicts VALUES(?,?,?,?)',('missing-plan',row[1],canonical(value).decode(),digest(value)));db.commit()
            with self.assertRaises(ServiceError):_snapshot(self.store,db.serialize())
        # A second unique archived row exceeds the one-task plan's archive bound.
        with sqlite3.connect(':memory:')as db:
            db.deserialize(self.store.path.read_bytes());row=list(db.execute('SELECT *FROM plan_archive_tasks').fetchone());row[0]=2;row[1]='P1-R1-PCH-OFFLINE'
            db.execute('INSERT INTO plan_archive_tasks VALUES('+','.join('?'for _ in row)+')',row);db.commit()
            with self.assertRaises(ServiceError):_snapshot(self.store,db.serialize())


class PlanRRDraftRestoreTests(unittest.TestCase):
    def test_nonempty_rr_drafts_and_plan_coexist_in_offline_restore(self):
        import test_hub_rr_feedback as rr_fixture
        from test_hub_plan_admission import plan,task,BASE
        from hub.plan_admission import PlanAuthority
        from hub.task_storage_restore import _snapshot
        rr=rr_fixture.FeedbackStoreTests();rr.setUp();self.addCleanup(rr.doCleanups)
        rr.submit([rr_fixture.attachment()])
        command={'request_id':'fixture-draft','expected_revision':0,'project_id':'fixture','parent_task_id':None,'value':{'objective':'Retained synthetic draft'}}
        rr.task.save_ordinary_draft(command,'fixture-owner',lambda:None)
        p=plan([task(BASE)]);cap=PlanAuthority.configured({'plan_sha256':digest(p)},authorize=lambda:None,controls=lambda:{'state':'active','revision':1},routes=lambda _:False,package_for=lambda _:None)
        rr.task.import_plan(p,authority=cap);original=_snapshot(rr.task,rr.task.path.read_bytes())[1]
        target=rr.root/'restored';target.mkdir(mode=0o700);copied=TaskStore(target);copied.path.parent.mkdir(parents=True,mode=0o700)
        shutil.copy2(rr.task.path,copied.path);shutil.copy2(rr.task.registration_path,copied.registration_path)
        result=copied.restore_storage(database_sha256=sha(copied.path),registration_sha256=sha(copied.registration_path),confirm_offline=True)
        self.assertEqual(result['state'],'ready');self.assertEqual(_snapshot(copied,copied.path.read_bytes())[1],original)
        for table in ('plan_schema','plan_inputs','rr_feedback','rr_attachments','ordinary_drafts','ordinary_draft_requests'):self.assertTrue(original[table])
        with sqlite3.connect(rr.task.path)as src,sqlite3.connect(copied.path)as dst:
            self.assertEqual(src.execute('SELECT data FROM rr_attachments').fetchall(),dst.execute('SELECT data FROM rr_attachments').fetchall())


class PlanReceiptHistoryRestoreTests(unittest.TestCase):
    def test_unknown_and_conflicting_bootstrap_history_survive_without_redispatch(self):
        import copy
        import test_hub_plan_admission as fixtures
        from hub.task_storage_restore import _snapshot
        f=fixtures.PlanStoreTests();f.setUp();self.addCleanup(f.doCleanups)
        f.store.import_plan(f.p,authority=f.cap)
        first=f.evidence(fixtures.BASE);f.records[fixtures.BASE]=first;f.configure();f.store.record_plan_evidence(f.p['plan_id'],first,authority=f.cap)
        conflict=copy.deepcopy(first);conflict['attempt_id']='second-original-attempt';f.records[fixtures.BASE]=conflict;f.configure();f.store.record_plan_evidence(f.p['plan_id'],conflict,authority=f.cap)
        unknown=f.evidence(fixtures.POLICY,unknown=True);f.records[fixtures.POLICY]=unknown;f.configure();f.store.record_plan_evidence(f.p['plan_id'],unknown,authority=f.cap)
        before=_snapshot(f.store,f.store.path.read_bytes())[1]
        self.assertEqual(len(before['plan_receipts']),2);self.assertEqual(len(before['plan_conflicts']),1)
        root=f.root.parent/'copy';root.mkdir(mode=0o700);copy_store=TaskStore(root);copy_store.path.parent.mkdir(parents=True,mode=0o700)
        shutil.copy2(f.store.path,copy_store.path);shutil.copy2(f.store.registration_path,copy_store.registration_path)
        result=copy_store.restore_storage(database_sha256=sha(copy_store.path),registration_sha256=sha(copy_store.registration_path),confirm_offline=True)
        self.assertEqual(result['business_sha256'],digest(before));self.assertEqual(_snapshot(copy_store,copy_store.path.read_bytes())[1],before)
        self.assertEqual(copy_store.tasks(),[])


class ArchivedPlanConflictRestoreTests(unittest.TestCase):
    setUp=PlanOfflineRestoreTests.setUp
    copied=PlanOfflineRestoreTests.copied
    pins=PlanOfflineRestoreTests.pins
    restore=PlanOfflineRestoreTests.restore
    bytes=PlanOfflineRestoreTests.bytes
    def test_archived_original_and_later_valid_conflict_are_both_preserved(self):
        import copy
        from hub.task_storage_restore import _snapshot
        r=copy.deepcopy(self.fixture.records['P1-R1-PCH-BASE']);r['evidence_ref']='synthetic/later-root-record.json'
        self.fixture.records[r['task_id']]=r;self.fixture.configure()
        result=self.store.record_plan_evidence(self.fixture.p['plan_id'],r,authority=self.fixture.cap)
        self.assertTrue(result['requires_reconcile'])
        before=_snapshot(self.store,self.store.path.read_bytes())[1]
        self.assertEqual(len(before['plan_conflicts']),1);self.assertEqual(len(before['plan_archive_tasks']),1)
        copied=self.copied();self.restore(copied);self.assertEqual(_snapshot(copied,copied.path.read_bytes())[1],before)
        self.assertEqual(copied.plan_ready(self.fixture.p['plan_id'],authority=self.fixture.cap)['ready'],[])
