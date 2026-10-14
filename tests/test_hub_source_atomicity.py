"""Real filesystem changes at read boundaries use only isolated synthetic roots."""
import contextlib
import os
import shutil
import stat
import unittest
from unittest import mock

import yaml

from connection_fixtures import Fixture
from hub.connection_records import empty_business, validate_result
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_sources import SourceResolver, _safe_relative_read
from hub.project_service import ProjectService, project_snapshot


class SourceAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.root, self.declaration = self.fixture.add('a')
        self.fixture.add('b')
        self.source = self.root / 'STATE.yaml'
        self.original = self.source.read_bytes()

    def resolver(self):
        return SourceResolver(self.fixture.hub)

    @contextlib.contextmanager
    def mutate_after_descriptor_read(self, target, mutation):
        """Read real bytes, then change the real file before reader fstat/checks."""
        identity = (target.stat().st_dev, target.stat().st_ino)
        original = os.fdopen
        fired = []

        class Stream:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def read(self, amount):
                data = self.stream.read(amount)
                if not fired:
                    fired.append(True)
                    mutation()
                return data

            def fileno(self):
                return self.stream.fileno()

        def fdopen(descriptor, *args, **kwargs):
            info = os.fstat(descriptor)
            stream = original(descriptor, *args, **kwargs)
            return Stream(stream) if (info.st_dev, info.st_ino) == identity else stream

        with mock.patch('hub.connection_sources.os.fdopen', fdopen):
            yield
        self.assertEqual(fired, [True], 'test did not reach the real source descriptor')

    def assert_rejected(self, result):
        self.assertFalse(result['success'])
        self.assertEqual(result['business'], empty_business())
        self.assertEqual(result['field_provenance'], {})

    def test_inplace_write_cannot_hide_by_restoring_mtime_and_size(self):
        before = self.source.stat()
        changed = self.original.replace(b'current candidate', b'changed candidate')
        self.assertNotEqual(changed, self.original)
        self.assertEqual(len(changed), len(self.original))

        def mutation():
            self.source.write_bytes(changed)
            os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))

        with self.mutate_after_descriptor_read(self.source, mutation):
            result = self.resolver().refresh('a')
        self.assertNotEqual(self.source.stat().st_ctime_ns, before.st_ctime_ns)
        self.assert_rejected(result)

    def test_atomic_file_replace_during_read_does_not_publish_detached_inode(self):
        replacement = self.root / 'replacement.yaml'
        replacement.write_bytes(self.original.replace(b'current candidate', b'changed candidate'))
        with self.mutate_after_descriptor_read(self.source, lambda: os.replace(replacement, self.source)):
            result = self.resolver().refresh('a')
        self.assert_rejected(result)

    def test_nested_directory_replace_cannot_keep_old_open_parent_current(self):
        directory = self.root / 'state'
        directory.mkdir()
        target = directory / 'current.yaml'
        self.source.rename(target)
        self.declaration['source_refs'][0]['path'] = 'state/current.yaml'
        (self.root / 'hub.connection.yaml').write_text(yaml.safe_dump(self.declaration))
        self.fixture.projects[0]['current_state_paths'] = ['state/current.yaml']
        self.fixture.save_registry()

        def mutation():
            directory.rename(self.root / 'detached-state')
            directory.mkdir()
            target.write_bytes(self.original.replace(b'current candidate', b'changed candidate'))

        with self.mutate_after_descriptor_read(target, mutation):
            result = self.resolver().refresh('a')
        self.assert_rejected(result)

    def test_source_replace_after_read_before_mapping_is_rechecked(self):
        fired = []

        def read(root, relative, **kwargs):
            value = _safe_relative_read(root, relative, **kwargs)
            if root == self.root and relative == 'STATE.yaml' and not fired:
                fired.append(True)
                replacement = self.root / 'replacement.yaml'
                replacement.write_bytes(self.original.replace(b'current candidate', b'changed candidate'))
                os.replace(replacement, self.source)
            return value

        with mock.patch('hub.connection_sources._safe_relative_read', read):
            result = self.resolver().refresh('a')
        self.assertEqual(fired, [True])
        self.assert_rejected(result)

    def test_running_root_replacement_rejects_changed_identity(self):
        fired = []

        def read(root, relative, **kwargs):
            value = _safe_relative_read(root, relative, **kwargs)
            if root == self.root and relative == 'hub.connection.yaml' and not fired:
                fired.append(True)
                old = self.root.with_name('detached-a')
                self.root.rename(old)
                shutil.copytree(old, self.root)
            return value

        with mock.patch('hub.connection_sources._safe_relative_read', read):
            result = self.resolver().refresh('a')
        self.assertEqual(result['disposition'], 'authority_drift')
        self.assert_rejected(result)

    def test_actual_chmod_half_yaml_and_missing_file_isolate_failure_and_keep_history(self):
        self.assertNotEqual(os.geteuid(), 0, 'actual chmod denial requires a non-root test user')
        ledger = RefreshLedger(self.fixture.hub, result_validator=validate_result)
        registry_before = self.fixture.resolver().registry_bytes
        refresh(ledger, self.resolver(), 'good', ['a', 'b'])
        previous = next(row['result'] for row in ledger.history('good')['results'] if row['project_id'] == 'a')
        mode = stat.S_IMODE(self.source.stat().st_mode)
        try:
            self.source.chmod(0)
            refresh(ledger, self.resolver(), 'permission', ['a', 'b'])
        finally:
            self.source.chmod(mode)
        for request, disposition, mutation in [
            ('half', 'invalid', lambda: self.source.write_text('current_work: [unfinished')),
            ('missing', 'missing_source', self.source.unlink),
        ]:
            mutation()
            refresh(ledger, self.resolver(), request, ['a', 'b'])
            view = ledger.history(request)
            results = {row['project_id']: row['result'] for row in view['results']}
            self.assertEqual(results['a']['disposition'], disposition)
            self.assert_rejected(results['a'])
            self.assertTrue(results['b']['success'])
        permissions = ledger.history('permission')['results']
        self.assertEqual(next(row['result']['disposition'] for row in permissions if row['project_id'] == 'a'), 'permission_denied')
        projection = project_snapshot(self.resolver().registry, self.resolver().authority, ledger)
        a = next(row for row in projection['projects'] if row['project_id'] == 'a')
        self.assertEqual(a['last_success'], previous)
        self.assertEqual(a['freshness']['state'], 'stale')
        self.assertFalse(ProjectService(self.fixture.hub).get_project('a')['business']['quality']['core_complete'])
        self.assertEqual(registry_before, self.fixture.resolver().registry_bytes)
        self.source.write_bytes(self.original)
        self.source.chmod(mode)
        refresh(ledger, self.resolver(), 'recovered', ['a', 'b'])
        self.assertTrue(all(row['success'] for row in ledger.history('recovered')['results']))


if __name__ == '__main__':
    unittest.main()
