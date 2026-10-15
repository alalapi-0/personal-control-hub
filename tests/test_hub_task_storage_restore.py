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
