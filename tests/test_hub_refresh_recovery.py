"""Real owned-process death and fresh CLI recovery of isolated source ledgers."""
from __future__ import annotations

import copy
import json
import os
import select
import signal
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

from connection_fixtures import Fixture
from hub.connection_records import validate_result
from hub.connection_refresh import RefreshLedger

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
CLI = ROOT / 'scripts/hub_sync.py'

CHILD = r'''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from hub.connection_sources import SourceResolver
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_records import validate_result
root, ready, mode = Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
def stop_here():
    os.write(ready, b'READY\n')
    sys.stdin.buffer.read(1)
    raise AssertionError('parent must terminate blocked child')
resolver = SourceResolver(root)
if mode == 'between':
    class PausedResolver:
        authority = resolver.authority
        validate_result = staticmethod(validate_result)
        def refresh(self, project_id):
            if project_id == 'b':
                stop_here()
            return resolver.refresh(project_id)
    ledger = RefreshLedger(root, result_validator=validate_result)
    refresh(ledger, PausedResolver(), 'kill-between', ['a', 'b'])
else:
    def fault(operation, context):
        if operation == 'append_project_result':
            stop_here()
    ledger = RefreshLedger(root, result_validator=validate_result, precommit_fault=fault)
    ledger.append_project_result('kill-precommit', 'a', resolver.refresh('a'))
'''

GUARDED_CLI = r'''
import os, runpy, sys
from pathlib import Path
script, root, request_id, *protected = sys.argv[1:]
protected = [os.path.abspath(p) for p in protected]
def guard(event, args):
    if event not in {'open', 'os.listdir', 'os.scandir'} or not args:
        return
    path = args[0]
    if not isinstance(path, (str, bytes, os.PathLike)):
        return
    path = os.path.abspath(os.fsdecode(path))
    if any(path == base or path.startswith(base + os.sep) for base in protected):
        raise AssertionError('finished replay attempted a project-root read')
sys.addaudithook(guard)
sys.argv = [script, '--root', root, 'sync', '--request-id', request_id]
runpy.run_path(script, run_name='__main__')
'''


class ProcessRefreshRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.a, _ = self.fixture.add('a')
        self.b, _ = self.fixture.add('b')
        self.root = self.fixture.hub
        scope = self.root / 'data/connections/sync_scope.yaml'
        scope.parent.mkdir(parents=True)
        scope.write_text(yaml.safe_dump({'schema_version': '1.0', 'scope_id': 'isolated-recovery',
                                        'baseline_total': 2, 'project_ids': ['a', 'b'],
                                        'excluded_project_ids': {}}))

    def ledger(self):
        return RefreshLedger(self.root, result_validator=validate_result)

    @staticmethod
    def _collect(child):
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)

    def blocked_child(self, mode):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        try:
            child = subprocess.Popen([sys.executable, '-c', CHILD, str(SRC), str(self.root), str(write_fd), mode],
                                     pass_fds=(write_fd,), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.fixture.base)
        finally:
            os.close(write_fd)
        self.addCleanup(self._collect, child)
        ready, _, _ = select.select([read_fd], [], [], 10)
        self.assertTrue(ready, 'owned child did not reach the exact bounded crash point')
        self.assertEqual(b'READY\n', os.read(read_fd, 64))
        self.assertIsNone(child.poll())
        return child

    def kill(self, child):
        child.kill()
        stdout, stderr = child.communicate(timeout=10)
        self.assertEqual(-signal.SIGKILL, child.returncode, (stdout, stderr))
        self.assertEqual(b'', stderr)

    def cli(self, request_id, *, no_project_reads=False):
        command = ([sys.executable, '-c', GUARDED_CLI, str(CLI), str(self.root), request_id, str(self.a), str(self.b)]
                   if no_project_reads else
                   [sys.executable, str(CLI), '--root', str(self.root), 'sync', '--request-id', request_id])
        result = subprocess.run(command, cwd=self.fixture.base, capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, (result.stdout, result.stderr))
        self.assertEqual('', result.stderr)
        return json.loads(result.stdout)

    def test_sigkill_after_one_commit_resumes_only_missing_project_and_replay_reads_no_roots(self):
        child = self.blocked_child('between')
        before = self.ledger().history('kill-between')
        request = before['requests'][0]
        self.assertEqual('OPEN', request['status'])
        self.assertEqual(['a'], request['completed_project_ids'])
        self.assertEqual(['b'], request['remaining_project_ids'])
        self.assertEqual(2, before['head']['sequence'])
        committed = copy.deepcopy(before['results'][0])
        self.assertEqual('a', committed['project_id'])
        self.assertTrue(committed['result']['success'])
        self.assertTrue(committed['result']['field_provenance']['current_work.objective']['sha256'])
        self.kill(child)
        self.assertEqual(before, self.ledger().history('kill-between'))
        # The resumed CLI must not revisit a. Its original immutable proof wins.
        (self.a / 'STATE.yaml').write_text('this is not valid mapped current state\n')
        resumed = self.cli('kill-between')
        self.assertEqual('FINISHED', resumed['status'])
        self.assertEqual(1, resumed['appended_count'])
        after = self.ledger().history('kill-between')
        self.assertEqual(['a', 'b'], after['requests'][0]['completed_project_ids'])
        self.assertEqual([], after['requests'][0]['remaining_project_ids'])
        self.assertEqual(4, after['head']['sequence'])
        self.assertEqual(committed, after['results'][0])  # ref/hash/observed_at/source SHA and whole row
        self.assertTrue(after['results'][1]['result']['success'])
        replay = self.cli('kill-between', no_project_reads=True)
        self.assertEqual(0, replay['appended_count'])
        self.assertEqual(after, self.ledger().history('kill-between'))
        # A deliberately new ID is allowed to collect a fresh source fact.
        changed = copy.deepcopy(self.fixture.business)
        changed['current_work']['objective'] = 'Fresh explicit request sees this new fact'
        (self.a / 'STATE.yaml').write_text(yaml.safe_dump(changed))
        fresh = self.cli('new-explicit-request')
        self.assertEqual(2, fresh['appended_count'])
        latest = self.ledger().history('new-explicit-request')['results'][0]
        self.assertEqual(changed['current_work']['objective'], latest['result']['business']['current_work']['objective'])
        self.assertNotEqual(committed['result_ref'], latest['result_ref'])
        self.assertNotEqual(committed['result']['sources'][0]['sha256'], latest['result']['sources'][0]['sha256'])
        self.assertEqual(after['results'], self.ledger().history('kill-between')['results'])

    def test_sigkill_inside_precommit_transaction_publishes_no_partial_result_or_head(self):
        ledger = self.ledger()
        ledger.begin_request('kill-precommit', ['a', 'b'], self.fixture.resolver().authority)
        before = ledger.history('kill-precommit')
        child = self.blocked_child('precommit')
        # A concurrent reader cannot see the writer's uncommitted event/result.
        self.assertEqual(before, self.ledger().history('kill-precommit'))
        self.kill(child)
        recovered = self.ledger().history('kill-precommit')
        self.assertEqual(before, recovered)
        self.assertEqual([], recovered['results'])
        self.assertEqual(1, recovered['head']['sequence'])
        result = self.cli('kill-precommit')
        self.assertEqual('FINISHED', result['status'])
        self.assertEqual(2, result['appended_count'])
        final = self.ledger().history('kill-precommit')
        self.assertEqual(4, final['head']['sequence'])
        self.assertEqual(['REQUEST_BEGUN', 'PROJECT_RESULT', 'PROJECT_RESULT', 'REQUEST_FINISHED'],
                         [event['event_type'] for event in final['events']])
        self.assertTrue(all(row['result']['success'] for row in final['results']))
