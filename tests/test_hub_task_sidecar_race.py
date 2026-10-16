"""A real deleted SQLite journal inode at the pathname-stat race boundary."""
import contextlib
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.service_contract import ServiceError
from hub.task_store import MAX_DATABASE_BYTES, TaskStore


class TaskSidecarRaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.root = self.base / 'hub'
        self.root.mkdir()
        self.store = TaskStore(self.root)
        self.store.register_storage()
        with contextlib.closing(sqlite3.connect(self.store.path)) as db:
            db.execute('CREATE TABLE sidecar_probe(value INTEGER)')
            db.commit()

    def deleted_journal_stat(self):
        """Return actual fstat after SQLite commits and unlinks its open journal."""
        journal = Path(str(self.store.path) + '-journal')
        before = self.store.path.lstat()
        with contextlib.closing(sqlite3.connect(self.store.path)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone(), ('delete',))
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO sidecar_probe VALUES(1)')
            self.assertTrue(journal.exists())
            fd = os.open(journal, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                linked = os.fstat(fd)
                self.assertTrue(stat.S_ISREG(linked.st_mode))
                self.assertEqual(linked.st_nlink, 1)
                self.assertLessEqual(linked.st_size, MAX_DATABASE_BYTES)
                db.commit()
                self.assertFalse(journal.exists())
                detached = os.fstat(fd)
                self.assertEqual(detached.st_nlink, 0)
                self.assertEqual((detached.st_dev, detached.st_ino), (linked.st_dev, linked.st_ino))
            finally:
                os.close(fd)
        after = self.store.path.lstat()
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
        return detached

    @contextlib.contextmanager
    def stat_sequence(self, suffix, sequence):
        target = Path(str(self.store.path) + suffix)
        original = Path.lstat
        observed = []
        def boundary(path, *args, **kwargs):
            if path == target:
                observed.append(path)
                if len(observed) <= len(sequence):
                    value = sequence[len(observed) - 1]
                    if isinstance(value, Exception):
                        raise value
                    return value
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'lstat', boundary):
            yield observed

    def unsafe_stats(self):
        regular = self.base / 'ordinary'
        regular.write_text('synthetic')
        symlink = self.base / 'symlink'
        symlink.symlink_to(regular)
        hardlink = self.base / 'hardlink'
        os.link(regular, hardlink)
        folder = self.base / 'folder'
        folder.mkdir()
        fifo = self.base / 'fifo'
        os.mkfifo(fifo)
        oversized = self.base / 'oversized'
        with oversized.open('wb') as stream:
            stream.truncate(MAX_DATABASE_BYTES + 1)
        oversized_info = oversized.lstat()
        fd = os.open(oversized, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            oversized.unlink()
            detached_oversized = os.fstat(fd)
            self.assertEqual(detached_oversized.st_nlink, 0)
        finally:
            os.close(fd)
        return {'symlink': symlink.lstat(), 'hardlink': hardlink.lstat(),
                'directory': folder.lstat(), 'fifo': fifo.lstat(), 'oversized': oversized_info,
                'oversized_zero_link': detached_oversized}

    def rejected(self, operation):
        with self.assertRaises(ServiceError) as error:
            operation()
        self.assertEqual((error.exception.code, error.exception.status), ('TASK_LOCATION_REJECTED', 503))

    def test_real_committed_journal_disappearance_allows_complete_storage_check(self):
        detached = self.deleted_journal_stat()
        before = (self.store.path.read_bytes(), self.store.registration_path.read_bytes())
        # Only the first pathname-stat outcome is injected; the fresh lstat uses
        # the actual missing pathname after SQLite's own commit/unlink.
        with self.stat_sequence('-journal', [detached]) as observations:
            self.assertEqual(self.store.check_storage(readonly=True)['state'], 'ready')
        self.assertEqual(len(observations), 2)
        self.assertEqual(before, (self.store.path.read_bytes(), self.store.registration_path.read_bytes()))
        with contextlib.closing(sqlite3.connect(self.store.path)) as db:
            self.assertEqual(db.execute('SELECT value FROM sidecar_probe').fetchall(), [(1,)])

    def test_each_auxiliary_suffix_allows_one_zero_link_then_absent(self):
        detached = self.deleted_journal_stat()
        for suffix in ('-journal', '-wal', '-shm'):
            with self.subTest(suffix=suffix), self.stat_sequence(suffix, [detached]) as observations:
                self.assertTrue(self.store._location())
            self.assertEqual(len(observations), 2)

    def test_database_zero_links_is_never_retried(self):
        detached = self.deleted_journal_stat()
        with self.stat_sequence('', [detached]) as observations:
            self.rejected(self.store._location)
        self.assertEqual(len(observations), 1)

    def test_initial_unsafe_sidecars_are_never_retried(self):
        for name, info in self.unsafe_stats().items():
            for suffix in ('-journal', '-wal', '-shm'):
                with self.subTest(name=name, suffix=suffix), self.stat_sequence(suffix, [info]) as observations:
                    self.rejected(self.store._location)
                self.assertEqual(len(observations), 1)

    def test_fresh_stat_must_pass_all_original_guards(self):
        detached = self.deleted_journal_stat()
        candidates = {**self.unsafe_stats(), 'still_detached': detached}
        for name, info in candidates.items():
            with self.subTest(name=name), self.stat_sequence('-journal', [detached, info]) as observations:
                self.rejected(self.store._location)
            self.assertEqual(len(observations), 2)

    def test_one_retry_accepts_current_safe_sidecar(self):
        detached = self.deleted_journal_stat()
        safe = self.base / 'safe-sidecar'
        safe.write_text('bounded regular single-link fixture')
        with self.stat_sequence('-journal', [detached, safe.lstat()]) as observations:
            self.assertTrue(self.store._location())
        self.assertEqual(len(observations), 2)

    def test_auxiliary_zero_link_does_not_bypass_database_inode_identity(self):
        detached = self.deleted_journal_stat()
        original = self.store.path.with_name('retained-original.sqlite3')
        self.store.path.rename(original)
        self.store.path.write_bytes(original.read_bytes())
        with self.stat_sequence('-journal', [detached]):
            with self.assertRaises(ServiceError) as error:
                self.store.check_storage(readonly=True)
        self.assertEqual(error.exception.details.get('reason_code'), 'LEDGER_IDENTITY_CHANGED')


if __name__ == '__main__':
    unittest.main()
