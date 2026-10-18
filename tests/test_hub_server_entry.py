"""Frozen code may serve an explicit data root without initializing stores."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from hub.codex_adapter import AppServerAdapter, ReadonlyAppServerAdapter


class ServerEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data-root'
        self.data.mkdir()
        spec = importlib.util.spec_from_file_location('server_entry_test', ROOT / 'scripts/hub_server.py')
        self.entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.entry)

    def run_entry(self, args, *, default=None, preview=None):
        captured = {}

        class Observer:
            def __init__(self, roots):
                captured['observer_roots'] = roots
            def start(self):
                captured['observer_started'] = True
            def stop(self):
                captured['observer_stopped'] = True

        class Server:
            origin = 'http://127.0.0.1:8766'
            def __init__(self, projects, designs, **values):
                captured.update(projects=projects, designs=designs, **values)
                self.tasks = values['tasks']
            def serve_forever(self, **values):
                captured['served'] = True
            def server_close(self):
                captured['closed'] = True

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(self.entry, 'HostObserver', Observer))
            stack.enter_context(patch.object(self.entry, 'HubHTTPServer', Server))
            stack.enter_context(patch.object(AppServerAdapter, 'open', side_effect=AssertionError('Agent startup')))
            stack.enter_context(patch.object(ReadonlyAppServerAdapter, 'open', side_effect=AssertionError('Agent discovery')))
            if default is not None:
                stack.enter_context(patch.object(self.entry, 'CODE_ROOT', default))
            if preview is not None:
                stack.enter_context(patch.object(self.entry, 'ReadOnlyPreview', preview))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            code = self.entry.main(args)
        return code, captured

    def test_explicit_data_root_binds_all_stores_and_native_metadata_without_writes(self):
        before = list(self.data.iterdir())
        code, state = self.run_entry(['--root', str(self.data), '--host', '127.0.0.1', '--port', '0'])
        self.assertEqual(code, 0)
        self.assertEqual(state['projects'].root, self.data)
        self.assertEqual(state['designs'].store.path, self.data / 'data/design_governance/design-store.json')
        self.assertEqual(state['previews'].store.root, self.data)
        self.assertEqual(state['tasks'].store.root, self.data)
        self.assertEqual(state['session_view'].hub_root, self.data)
        self.assertEqual(state['observer_roots']['hub'], self.data)
        self.assertEqual(state['host'], '127.0.0.1')
        self.assertEqual(state['port'], 0)
        self.assertTrue(state['served'] and state['closed'] and state['observer_stopped'])
        self.assertEqual(list(self.data.iterdir()), before)

    def test_default_root_and_host_port_behavior_stay_compatible(self):
        code, state = self.run_entry([], default=self.data)
        self.assertEqual(code, 0)
        self.assertEqual(state['projects'].root, self.data)
        self.assertEqual((state['host'], state['port']), ('127.0.0.1', 8766))
        self.assertFalse(list(self.data.iterdir()))

    def test_explicit_os_owner_provider_does_not_enable_or_start_agent_runtime(self):
        from hub.owner_auth import SystemdCredentialProvider
        directory = Path(self.temp.name) / 'os-projection'; directory.mkdir(mode=0o700)
        projection = directory / 'pch-owner-token'
        projection.write_text('synthetic-projected-owner-proof-' + 'p' * 40); projection.chmod(0o600)
        with patch.dict(os.environ, {'CREDENTIALS_DIRECTORY':str(directory),
                                     'HUB_OWNER_TOKEN':'synthetic-env-owner-proof-' + 'e' * 40}):
            code, state = self.run_entry(['--root',str(self.data),'--owner-auth=systemd-credential'])
            self.assertEqual(code,0)
            self.assertIsInstance(state['owner_auth'].provider,SystemdCredentialProvider)
            self.assertTrue(state['owner_auth'].configured)
            projection.unlink()
            self.assertFalse(state['owner_auth'].configured)
            self.assertFalse(state['tasks'].ordinary_enabled)
            code, default = self.run_entry(['--root',str(self.data)])
            self.assertEqual(code,0);self.assertNotIn('owner_auth',default)
        self.assertFalse(list(self.data.iterdir()))

    def test_missing_ordinary_grant_never_probes_provider_or_initializes_storage(self):
        with patch.object(self.entry, 'ordinary_provider_status', side_effect=AssertionError('unexpected auth probe')):
            code, state = self.run_entry(['--root', str(self.data), '--ordinary-readonly-grant', 'missing-grant'])
        self.assertEqual(code, 1)
        self.assertTrue(state['closed'])
        self.assertNotIn('served', state)
        self.assertFalse(list(self.data.iterdir()))

    def test_provider_readiness_reduces_native_status_without_retaining_output(self):
        import subprocess
        for text, expected in [(b'Logged in using ChatGPT\n', True), (b'Not logged in', False)]:
            result = subprocess.CompletedProcess([], 0, b'', text)
            with patch.object(self.entry.subprocess, 'run', return_value=result) as command:
                self.assertEqual(self.entry.ordinary_provider_status(), {'available': expected, 'classification': 'real'})
            self.assertEqual(command.call_args.args[0], ['codex', 'login', 'status'])
            self.assertNotIn('shell', command.call_args.kwargs)

    def test_explicit_runtime_attachment_does_not_start_and_close_owns_its_handle(self):
        from types import SimpleNamespace
        from hub.task_service import TaskService
        store = object(); calls = []
        tasks = TaskService(None,None,store)
        server = SimpleNamespace(tasks=tasks, server_close=lambda: calls.append('http-close'))
        runtime = SimpleNamespace(store=store, grant_ids=('selected-grant',),
                                  grant_projects=(('selected-grant','fixture-project'),),writer_check=lambda _: False,
                                  status=lambda: {}, stop=lambda: calls.append('runtime-stop'))
        self.entry.attach_ordinary_runtime(server, runtime)
        self.assertTrue(tasks.ordinary_enabled)
        self.assertEqual(tasks.ordinary_grant_ids, runtime.grant_ids)
        self.assertEqual(calls, [])
        server.server_close()
        self.assertEqual(calls, ['runtime-stop', 'http-close'])
        with self.assertRaises(Exception):
            self.entry.attach_ordinary_runtime(server, runtime)

    def test_attachment_rejects_missing_empty_or_malformed_selected_scope_before_enable(self):
        from types import SimpleNamespace
        from hub.service_contract import ServiceError
        from hub.task_service import TaskService
        for scope in (None, (), [], ['selected'], ('same', 'same'), ('bad/id',), (42,)):
            with self.subTest(scope=scope):
                store = object(); calls = []
                tasks = TaskService(None,None,store)
                close = lambda: calls.append('http-close')
                server = SimpleNamespace(tasks=tasks, server_close=close)
                runtime = SimpleNamespace(store=store, grant_ids=scope,grant_projects=(('selected','fixture-project'),),
                    writer_check=lambda _: False, status=lambda: {},
                    stop=lambda: calls.append('runtime-stop'))
                with self.assertRaises(ServiceError):
                    self.entry.attach_ordinary_runtime(server, runtime)
                self.assertFalse(tasks.ordinary_enabled)
                self.assertIs(server.server_close, close)
                self.assertEqual(calls, [])

    def test_attachment_rejects_invalid_project_bindings_before_enable(self):
        from types import SimpleNamespace
        from hub.service_contract import ServiceError
        from hub.task_service import TaskService
        for projects in (None,(),[],(('other-id','fixture-project'),),
                         (('selected','bad/project'),),(('selected',False),),('selected',)):
            with self.subTest(projects=projects):
                store=object();tasks=TaskService(None,None,store);calls=[]
                close=lambda:calls.append('close')
                server=SimpleNamespace(tasks=tasks,server_close=close)
                runtime=SimpleNamespace(store=store,grant_ids=('selected',),grant_projects=projects,
                    writer_check=lambda _:False,status=lambda:{},stop=lambda:calls.append('stop'))
                with self.assertRaises(ServiceError):
                    self.entry.attach_ordinary_runtime(server,runtime)
                self.assertFalse(tasks.ordinary_enabled)
                self.assertIsNone(tasks.ordinary_grant_ids)
                self.assertIs(server.server_close,close)
                self.assertEqual(calls,[])

    def test_missing_or_aliased_root_fails_before_startup_and_does_not_initialize(self):
        missing = self.data / 'absent'
        alias = Path(self.temp.name) / 'alias'
        alias.symlink_to(self.data, target_is_directory=True)
        for root in [missing, alias, self.data / '..' / 'data-root']:
            with self.subTest(root=root):
                code, state = self.run_entry(['--root', str(root)])
                self.assertEqual(code, 1)
                self.assertEqual(state, {})
        self.assertFalse(missing.exists())

    def test_present_preview_is_data_bound_but_never_started_implicitly(self):
        manifest = self.data / self.entry.MANIFEST
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{}')
        calls = []

        class Preview:
            def __init__(self, root, projects, designs):
                calls.append(('construct', root))
            def start(self):
                raise AssertionError('implicit sibling runtime')
            def stop(self):
                calls.append(('stop',))

        before = {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
        code, state = self.run_entry(['--root', str(self.data)], preview=Preview)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [('construct', self.data), ('stop',)])
        self.assertEqual({str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}, before)
