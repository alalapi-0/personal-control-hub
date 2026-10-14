"""Real file, process and failure-path checks for explicit management overlays."""
import copy
import fcntl
import hashlib
import multiprocessing
import os
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hub.connection_records import RecordError
from hub.management_store import ManagementStore, relation_warnings, validate_overlay, validate_management_state
from hub.metric_management import CONFIG_RELATIVE, load_management_config
from hub.service_contract import ServiceError


def overlay(**values):
    result = dict(priority=None, start_date=None, end_date=None, watch=None, order=None, layout=None, relations=None)
    result.update(values); return result


def process_apply(root, command, start, results):
    start.wait()
    try:
        receipt = ManagementStore(root).apply(command, project_ids={'a', 'b'}, authorize=lambda: None)
        results.put(('ok', receipt['revision']))
    except ServiceError as error: results.put(('error', error.code))


class ManagementStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root / CONFIG_RELATIVE
        self.path.parent.mkdir(parents=True)
        self.base = yaml.safe_load((Path(__file__).parents[1] / CONFIG_RELATIVE).read_text())
        # Public fixture starts at the existing schema 1.0, preserving all configured base values.
        self.base['schema_version'] = '1.0'; self.base['projects'] = {}
        self.base.pop('revision', None); self.base.pop('history', None)
        self.path.write_text(yaml.safe_dump(self.base, allow_unicode=True, sort_keys=False))
        self.store = ManagementStore(self.root)

    def command(self, request='request-1', project='a', value=None, operation='set'):
        view = self.store.snapshot()
        return dict(request_id=request, expected_revision=view['revision'], expected_hash=view['config_hash'],
                    project_id=project, operation=operation, value=value if value is not None else (None if operation == 'retract' else overlay()))

    def apply(self, command, store=None, authorize=lambda: None):
        return (store or self.store).apply(command, project_ids={'a', 'b'}, authorize=authorize)

    def assert_code(self, code, action):
        with self.assertRaises(ServiceError) as context: action()
        self.assertEqual(context.exception.code, code)
        return context.exception

    def test_missing_query_constructor_and_write_never_initialize(self):
        with tempfile.TemporaryDirectory() as root:
            store = ManagementStore(root)
            self.assertFalse(store.snapshot()['available']); self.assertEqual(list(Path(root).iterdir()), [])
            self.assert_code('MANAGEMENT_NOT_INITIALIZED', lambda: self.apply(self.command(), store))
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_shared_reader_accepts_committed_overlay_and_literal_dates(self):
        value = overlay(priority=3, start_date='2026-10-14', end_date='2026-10-15')
        self.apply(self.command(value=value))
        loaded, _metadata = load_management_config(self.root)
        self.assertEqual(loaded['projects'], self.store.snapshot()['projects'])
        self.assertEqual(loaded['projects']['a']['start_date'], '2026-10-14')
        self.assertEqual(loaded['projects']['a']['end_date'], '2026-10-15')

    def test_both_readers_reject_alias_anchor_and_unquoted_calendar_dates(self):
        self.apply(self.command(value=overlay(start_date='2026-10-14')))
        original = self.path.read_text()
        self.assertIn("'2026-10-14'", original)
        malformed = [original + 'unrelated: &anchor ignored\n',
                     original + 'unrelated: &anchor ignored\nother: *anchor\n',
                     original.replace("'2026-10-14'", '2026-10-14')]
        for raw in malformed:
            with self.subTest(raw=raw):
                self.path.write_text(raw)
                self.assert_code('MANAGEMENT_CONFIG_REJECTED', self.store.snapshot)
                with self.assertRaises(RecordError): load_management_config(self.root)
        self.path.write_text(original)
        self.assertEqual(load_management_config(self.root)[0]['projects'], self.store.snapshot()['projects'])

    def test_set_preserves_all_base_values_and_literal_calendar_dates(self):
        value = overlay(priority=2, watch=True, start_date='2026-10-14', end_date='2026-10-15', layout={'x': 10.5, 'y': 20})
        result = self.apply(self.command(value=value))
        self.assertEqual((result['revision'], result['committed_revision'], result['replayed']), (1, 1, False))
        loaded = yaml.safe_load(self.path.read_text())
        for key in self.base:
            if key not in ('schema_version', 'projects'): self.assertEqual(loaded[key], self.base[key])
        view = self.store.snapshot(); self.assertEqual(view['projects']['a'], value)
        self.assertEqual(view['source_sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertIsInstance(view['projects']['a']['start_date'], str)

    def test_replay_tracks_current_head_and_original_commit(self):
        first = self.command(value=overlay(priority=1)); self.apply(first)
        self.apply(self.command('request-2', 'b', overlay(priority=2)))
        before = self.path.read_bytes(); replay = copy.deepcopy(first)
        replay.update(expected_revision=999, expected_hash='f' * 64)
        calls = []; result = self.apply(replay, authorize=lambda: calls.append(True))
        self.assertEqual((result['revision'], result['committed_revision'], result['replayed']), (2, 1, True))
        self.assertEqual(result['config_hash'], self.store.snapshot()['config_hash'])
        self.assertEqual(self.path.read_bytes(), before); self.assertEqual(len(calls), 2)
        conflict = copy.deepcopy(first); conflict['value']['priority'] = 9
        error = self.assert_code('MANAGEMENT_REQUEST_CONFLICT', lambda: self.apply(conflict))
        self.assertEqual(error.status, 409); self.assertEqual(self.path.read_bytes(), before)

    def test_stale_cas_between_instances(self):
        command = self.command(); other = ManagementStore(self.root)
        self.apply(command, other)
        command['request_id'] = 'request-2'
        error = self.assert_code('MANAGEMENT_CONFLICT', lambda: self.apply(command))
        self.assertEqual(error.outcome, 'NOT_COMMITTED'); self.assertEqual(self.store.snapshot()['revision'], 1)

    def test_real_process_same_revision_race_has_one_commit(self):
        context = multiprocessing.get_context('fork'); start = context.Event(); results = context.Queue()
        commands = [self.command('request-' + str(i)) for i in range(2)]
        processes = [context.Process(target=process_apply, args=(str(self.root), command, start, results)) for command in commands]
        for process in processes: process.start()
        start.set()
        answers = [results.get(timeout=10) for _ in processes]
        for process in processes:
            process.join(10); self.assertEqual(process.exitcode, 0)
        self.assertEqual(sum(row[0] == 'ok' for row in answers), 1)
        self.assertTrue(all(row[0] == 'ok' or row[1] in ('MANAGEMENT_BUSY', 'MANAGEMENT_CONFLICT') for row in answers))
        self.assertEqual(self.store.snapshot()['revision'], 1)

    def test_nonblocking_directory_lock_across_instances(self):
        fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_code('MANAGEMENT_BUSY', lambda: self.apply(self.command(), ManagementStore(self.root)))
        finally: os.close(fd)
        self.assertEqual(self.store.snapshot()['revision'], 0)

    def test_authorization_rechecked_before_commit(self):
        calls = []
        def authorize():
            calls.append(True)
            if len(calls) == 2: raise ServiceError('OWNER_SESSION_EXPIRED', status=403)
        before = self.path.read_bytes()
        self.assert_code('OWNER_SESSION_EXPIRED', lambda: self.apply(self.command(), authorize=authorize))
        self.assertEqual(len(calls), 2); self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_retract_history_and_orphans_preserved(self):
        self.apply(self.command(value=overlay(priority=3)))
        self.apply(self.command('request-2', 'b'))
        self.apply(self.command('request-3', operation='retract'))
        self.assertEqual(self.store.snapshot()['projects'], {'b': overlay()})
        # Orphan b survives when only a remains permitted, because the command targets a.
        self.store.apply(self.command('request-4'), project_ids={'a'}, authorize=lambda: None)
        self.assertIn('b', self.store.snapshot()['projects'])
        self.assertEqual(len(self.store.snapshot()['history']), 4)

    def test_unknown_commit_replays_without_duplicate(self):
        command = self.command()
        def fault(stage):
            if stage == 'after_replace': raise OSError('simulated durability fault')
        error = self.assert_code('MANAGEMENT_COMMIT_UNCONFIRMED', lambda: self.apply(command, ManagementStore(self.root, fault=fault)))
        self.assertEqual(error.outcome, 'UNKNOWN')
        self.assertEqual(self.store.snapshot()['revision'], 1)
        self.assertTrue(self.apply(command)['replayed']); self.assertEqual(self.store.snapshot()['revision'], 1)

    def test_replace_syscall_unknown_effect_is_not_reported_not_committed(self):
        command = self.command(); replace = os.replace
        def uncertain_replace(*args, **kwargs):
            replace(*args, **kwargs)
            raise OSError('replace completed but result was unavailable')
        with patch('hub.management_store.os.replace', side_effect=uncertain_replace):
            error = self.assert_code('MANAGEMENT_COMMIT_UNCONFIRMED', lambda: self.apply(command))
        self.assertEqual(error.outcome, 'UNKNOWN')
        self.assertTrue(self.apply(command)['replayed']); self.assertEqual(self.store.snapshot()['revision'], 1)

    def test_postcommit_directory_change_is_unknown_and_preserves_committed_image(self):
        command = self.command(); moved = self.root / 'published-directory'; original = self.path.read_bytes()
        def fault(stage):
            if stage == 'after_replace':
                self.path.parent.rename(moved); self.path.parent.mkdir(); self.path.write_bytes(original)
        error = self.assert_code('MANAGEMENT_COMMIT_UNCONFIRMED', lambda: self.apply(command, ManagementStore(self.root, fault=fault)))
        self.assertEqual(error.outcome, 'UNKNOWN')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(yaml.safe_load((moved / self.path.name).read_text())['revision'], 1)

    def test_hardlink_and_fifo_rejected_without_blocking(self):
        original = self.path.read_bytes(); other = self.root / 'hardlink'
        os.link(self.path, other)
        self.assert_code('MANAGEMENT_CONFIG_REJECTED', self.store.snapshot)
        other.unlink(); self.path.unlink(); os.mkfifo(self.path)
        self.assert_code('MANAGEMENT_CONFIG_REJECTED', self.store.snapshot)
        self.path.unlink(); self.path.write_bytes(original)

    def test_denied_initial_authorization_has_no_file_effects(self):
        before = self.path.read_bytes()
        def deny(): raise ServiceError('OWNER_REQUIRED', status=403)
        self.assert_code('OWNER_REQUIRED', lambda: self.apply(self.command(), authorize=deny))
        self.assertEqual(self.path.read_bytes(), before); self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_related_network_and_unconfigured_independence_remain_literal(self):
        relation = lambda target: {'belongs_to': None, 'depends_on': [], 'related_to': [target], 'independent': None}
        self.apply(self.command(value=overlay(relations=relation('b'))))
        self.apply(self.command('request-2', 'b', overlay(relations=relation('a'))))
        projects = self.store.snapshot()['projects']
        self.assertEqual(relation_warnings(projects), [])
        self.assertIsNone(projects['a']['relations']['independent'])

    def test_precommit_fault_is_not_committed(self):
        def fault(stage):
            if stage == 'before_replace': raise OSError('simulated write fault')
        before = self.path.read_bytes()
        error = self.assert_code('MANAGEMENT_CONFIG_REJECTED', lambda: self.apply(self.command(), ManagementStore(self.root, fault=fault)))
        self.assertEqual(error.outcome, 'NOT_COMMITTED'); self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_manual_same_length_restore_mtime_change_conflicts(self):
        original = self.path.stat()
        def fault(stage):
            if stage == 'before_replace':
                raw = self.path.read_bytes().replace(b'86400', b'86401')
                self.path.write_bytes(raw); os.utime(self.path, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assert_code('MANAGEMENT_CONFLICT', lambda: self.apply(self.command(), ManagementStore(self.root, fault=fault)))
        self.assertIn(b'86401', self.path.read_bytes()); self.assertEqual(self.store.snapshot()['revision'], 0)

    def test_symlink_file_and_parent_are_rejected_without_effects(self):
        original = self.path.read_bytes(); target = self.root / 'original.yaml'; target.write_bytes(original)
        self.path.unlink(); self.path.symlink_to(target)
        self.assert_code('MANAGEMENT_CONFIG_REJECTED', self.store.snapshot)
        self.assert_code('MANAGEMENT_CONFIG_REJECTED', lambda: self.apply(dict(request_id='x', expected_revision=0, expected_hash='0'*64, project_id='a', operation='set', value=overlay())))
        self.assertEqual(target.read_bytes(), original)
        self.path.unlink(); self.path.write_bytes(original)
        directory = self.path.parent; moved = self.root / 'moved'; directory.rename(moved); directory.symlink_to(moved, target_is_directory=True)
        self.assert_code('MANAGEMENT_CONFIG_REJECTED', self.store.snapshot)

    def test_directory_replace_mid_apply_does_not_write_detached_or_new(self):
        old = self.path.read_bytes(); moved = self.root / 'old-connections'
        def fault(stage):
            if stage == 'before_replace':
                self.path.parent.rename(moved); self.path.parent.mkdir(); self.path.write_bytes(old)
        self.assert_code('MANAGEMENT_CONFIG_REJECTED', lambda: self.apply(self.command(), ManagementStore(self.root, fault=fault)))
        self.assertEqual(self.path.read_bytes(), old); self.assertEqual((moved / self.path.name).read_bytes(), old)
        self.assertEqual(list(moved.iterdir()), [moved / self.path.name])

    def test_invalid_fields_references_and_capacity_no_effect(self):
        before = self.path.read_bytes()
        bad_values = [overlay(priority=True), overlay(start_date='2026-02-30'), overlay(start_date='2026-02-02', end_date='2026-01-01'), overlay(layout={'x': float('nan'), 'y': 1}), overlay(relations={'belongs_to': 'a', 'depends_on': [], 'related_to': [], 'independent': None})]
        for value in bad_values:
            with self.subTest(value=value): self.assert_code('MANAGEMENT_INVALID_COMMAND', lambda: self.apply(self.command(value=value)))
        bad = overlay(relations={'belongs_to': None, 'depends_on': ['outside'], 'related_to': [], 'independent': True})
        self.assert_code('MANAGEMENT_INVALID_COMMAND', lambda: self.apply(self.command(value=bad)))
        with patch('hub.management_store.MAX_BYTES', self.path.stat().st_size + 1):
            self.assert_code('MANAGEMENT_CAPACITY', lambda: self.apply(self.command()))
        self.assertEqual(self.path.read_bytes(), before); self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_belongs_cycle_reject_dependency_cycle_warn_independent_preserved(self):
        relation = lambda parent=None, dep=None: {'belongs_to': parent, 'depends_on': dep or [], 'related_to': [], 'independent': True}
        self.apply(self.command(value=overlay(relations=relation('b', ['b']))))
        before = self.path.read_bytes()
        error = self.assert_code('MANAGEMENT_RELATION_CONFLICT', lambda: self.apply(self.command('request-2', 'b', overlay(relations=relation('a')))))
        self.assertEqual(error.status, 409)
        self.assertEqual(error.outcome, 'NOT_COMMITTED')
        self.assertEqual(self.path.read_bytes(), before)
        self.apply(self.command('request-3', 'b', overlay(relations=relation(dep=['a']))))
        projects = self.store.snapshot()['projects']; self.assertEqual(relation_warnings(projects, 'a'), ['PROJECT_DEPENDENCY_CYCLE'])
        self.assertTrue(projects['a']['relations']['independent'])
        self.assertIsNone(overlay()['relations'])

    def test_history_tampering_and_duplicate_yaml_rejected(self):
        self.apply(self.command()); good = yaml.safe_load(self.path.read_text())
        for mutate in [lambda state: state['history'][0].update(before=overlay()), lambda state: state['history'][0].update(request_hash='f'*64), lambda state: state.update(projects={}), lambda state: state.update(revision=2)]:
            with self.subTest(mutate=mutate):
                bad = copy.deepcopy(good); mutate(bad)
                with self.assertRaises(RecordError): validate_management_state(bad)
        self.path.write_text(self.path.read_text() + 'kind: duplicate\n')
        self.assert_code('MANAGEMENT_CONFIG_REJECTED', self.store.snapshot)


if __name__ == '__main__': unittest.main()
