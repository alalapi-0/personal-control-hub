"""Synthetic controls; systemd invocation is mocked. Root owns real timer trials."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from pch_functional_fixture import FunctionalFixture


class RefreshTimerFixtureTests(unittest.TestCase):
    def test_source_is_atomic_bound_and_not_collected_until_existing_sync(self):
        with FunctionalFixture() as fixture:
            project = 'fixture-project-a'
            head = fixture.projects.ledger_head()
            before_b = (fixture.roots['fixture-project-b'] / 'STATE.yaml').read_bytes()
            result = fixture.update_source(project, completed=0, objective='Changed synthetic objective')
            self.assertNotEqual(result['old_sha256'], result['new_sha256'])
            self.assertEqual(result['source']['completed'], 0)
            self.assertFalse(result['collected'])
            self.assertEqual(fixture.projects.ledger_head(), head)
            self.assertEqual(before_b, (fixture.roots['fixture-project-b'] / 'STATE.yaml').read_bytes())
            self.assertNotEqual(fixture.projects.get_project(project)['business']['fields']['current_work']['objective'], result['source']['objective'])
            synced = fixture.sync(project)
            self.assertEqual(synced['exit_code'], 0)
            self.assertEqual(fixture.projects.get_project(project)['business']['fields']['current_work']['objective'], result['source']['objective'])
            self.assertNotEqual(fixture.projects.ledger_head(), head)
            for foreign in ('actorforge', '../outside', 'fixture-directory-002'):
                with self.assertRaises(ValueError): fixture.update_source(foreign, completed=1, objective='no')
                with self.assertRaises(ValueError): fixture.sync(foreign)
            self.assertFalse(list(fixture.roots[project].glob('.STATE-fixture-*')))

    def test_render_start_exact_status_and_cleanup_with_mocked_subprocess(self):
        with tempfile.TemporaryDirectory() as directory, FunctionalFixture() as fixture:
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                self.assertFalse(kwargs['check'])
                self.assertEqual(kwargs['timeout'], 15)
                if argv[0] == 'journalctl':
                    prefix = fixture.timer_prefix
                    payload = {'request_id': 'synthetic-request', 'mode': 'incremental', 'head': {'sequence': 9}}
                    rows = [{'_SYSTEMD_USER_UNIT': prefix + '.service', '_SYSTEMD_INVOCATION_ID': 'synthetic-invocation',
                             '_PID': '123', '_TRANSPORT': 'stdout', '__REALTIME_TIMESTAMP': '100000',
                             '__MONOTONIC_TIMESTAMP': '200000', 'MESSAGE': line} for line in json.dumps(payload, indent=2).splitlines()]
                    return SimpleNamespace(returncode=0, stdout='\n'.join(json.dumps(row) for row in rows), stderr='')
                if 'show' in argv:
                    return SimpleNamespace(returncode=0, stdout='Id=' + fixture.timer_prefix + '.timer\nActiveState=active\n', stderr='')
                return SimpleNamespace(returncode=0, stdout='', stderr='')
            fixture.command_run = run
            result = fixture.timer_start(unit_dir=Path(directory))
            prefix = result['unit_prefix']
            self.assertTrue(prefix.startswith('pch-fixture-'))
            timer = (Path(directory) / (prefix + '.timer')).read_text()
            service = (Path(directory) / (prefix + '.service')).read_text()
            self.assertIn('OnCalendar=*-*-* *:*:00 Asia/Tokyo', timer)
            self.assertIn('OnCalendar=*-*-* 09:00:00 Asia/Tokyo', timer)
            self.assertIn(sys.executable, service)
            self.assertIn(str(fixture.root), service)
            self.assertIn('--scheduled', service)
            self.assertIn(['systemctl', '--user', 'start', prefix + '.timer'], calls)
            self.assertFalse(any('start' in row and prefix + '.service' in row for row in calls))
            status = fixture.timer_status()
            self.assertEqual(status['invocations'][0]['request_id'], 'synthetic-request')
            self.assertEqual(status['invocations'][0]['mode'], 'incremental')
            self.assertEqual(status['invocations'][0]['pid'], '123')
            self.assertIsNone(status['journal_reason'])
            fixture.timer_stop()
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertIn(['systemctl', '--user', 'stop', prefix + '.timer', prefix + '.service'], calls)

    def test_collision_and_changed_unit_never_overwrite_or_delete_foreign_content(self):
        with tempfile.TemporaryDirectory() as directory, FunctionalFixture() as fixture:
            fixture.command_run = lambda *a, **k: SimpleNamespace(returncode=0, stdout='', stderr='')
            result = fixture.timer_start(unit_dir=Path(directory))
            path = Path(directory) / (result['unit_prefix'] + '.timer')
            own = path.read_text(); path.write_text('foreign edit')
            with self.assertRaises(RuntimeError): fixture.timer_stop()
            self.assertEqual(path.read_text(), 'foreign edit')
            path.write_text(own)
            fixture.timer_stop()
            with mock.patch('pch_functional_fixture.secrets.token_hex', return_value='collision'):
                path = Path(directory) / 'pch-fixture-collision.timer'
                path.write_text('existing')
                with self.assertRaises(FileExistsError): fixture.timer_start(unit_dir=Path(directory))
                self.assertEqual(path.read_text(), 'existing')
                self.assertIsNone(fixture.timer_prefix)

    def test_journal_denial_retains_unknown_trigger_boundary(self):
        with tempfile.TemporaryDirectory() as directory, FunctionalFixture() as fixture:
            def run(argv, **kwargs):
                return SimpleNamespace(returncode=1 if argv[0] == 'journalctl' else 0, stdout='', stderr='private omitted')
            fixture.command_run = run
            fixture.timer_start(unit_dir=Path(directory))
            status = fixture.timer_status()
            self.assertEqual(status['journal_reason'], 'JOURNAL_UNAVAILABLE_EXIT_1')
            self.assertEqual(status['invocations'], [])
            fixture.timer_stop()


if __name__ == '__main__': unittest.main()
