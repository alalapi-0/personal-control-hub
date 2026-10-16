"""Process-spawn boundaries use mocks; no native CLI or provider is invoked."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import hub.codex_adapter as adapter_module
from hub.codex_adapter import AppServerAdapter
from hub.service_contract import ServiceError
from hub.task_executable import TaskExecutable


class RuntimeExecutableAdapterTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.directory=Path(temporary.name);self.root=self.directory/'business';self.root.mkdir()
        (self.root/'AGENTS.md').write_text('Synthetic file-only scope\n')
        (self.root/'README.md').write_text('Synthetic permitted leaf\n')
        self.path=self.directory/'trusted-runtime';self.path.write_bytes(b'\x7fELF'+b'\x00'*124);self.path.chmod(0o700)
        self.descriptor=self.descriptor_for(self.path)
        self.command=(str(self.path),'app-server','--stdio')

    def descriptor_for(self,path):
        info=path.stat()
        return TaskExecutable(path=path,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            device=info.st_dev,inode=info.st_ino,size=info.st_size,task_id='fixture-task',candidate='a'*64)

    def adapter(self,**kwargs):
        return AppServerAdapter(self.root,command=self.command,read_only=True,
            readable_paths=['AGENTS.md','README.md'],runtime_executable=self.descriptor,**kwargs)

    def test_opt_in_profile_grants_only_business_leaves_and_trusted_runtime_leaf(self):
        entries=adapter_module._profile_entries(self.root,read_only=True,
            readable_paths=['AGENTS.md','README.md'],runtime_executable=self.descriptor)
        self.assertEqual(entries,{':root':'deny',':minimal':'read',str(self.root/'AGENTS.md'):'read',
            str(self.root/'README.md'):'read',str(self.path):'read'})
        self.assertNotIn('write',entries.values())
        self.assertNotIn(str(self.path.parent),entries)
        default=adapter_module._profile_entries(self.root,read_only=True,readable_paths=['README.md'])
        self.assertNotIn(str(self.path),default)
        self.assertEqual(default[str(self.root)],'deny')
        self.assertEqual(default[':slash_tmp'],'deny')

    def test_descriptor_cannot_authorize_writable_unbounded_or_mismatched_command(self):
        cases=[dict(read_only=False,readable_paths=['README.md']),dict(read_only=True,readable_paths=None),
            dict(read_only=True,readable_paths=['README.md'],command=('codex','app-server','--stdio')),
            dict(read_only=True,readable_paths=['README.md'],command=(*self.command,'--extra'))]
        with mock.patch.object(adapter_module.subprocess,'Popen')as popen,mock.patch.object(adapter_module.subprocess,'run')as run:
            for changes in cases:
                values=dict(command=self.command,read_only=True,readable_paths=['README.md'],runtime_executable=self.descriptor)
                values.update(changes)
                with self.subTest(changes=changes),self.assertRaises(ServiceError):AppServerAdapter(self.root,**values)
            popen.assert_not_called();run.assert_not_called()
        inside=self.root/'runtime';inside.write_bytes(self.path.read_bytes());inside.chmod(0o700)
        with self.assertRaises(ServiceError):
            AppServerAdapter(self.root,command=(str(inside),'app-server','--stdio'),read_only=True,
                readable_paths=['README.md'],runtime_executable=self.descriptor_for(inside))

    def test_version_and_schema_probes_qualify_exact_runtime_instead_of_path_codex(self):
        calls=[]
        def run(command,**kwargs):
            calls.append(tuple(command))
            if command[1:]==['--version']:return SimpleNamespace(returncode=0,stdout=adapter_module.VERSION+'\n')
            directory=Path(command[command.index('--out')+1])
            names=set(adapter_module.PARAMS.values())|set(adapter_module.RESPONSES.values())|set(adapter_module.NOTIFICATIONS.values())|{'ServerNotification','ServerRequest',adapter_module.APPROVAL_RESPONSE,adapter_module.INPUT_RESPONSE}
            for name in names:(directory/(name+'.json')).write_text('{}')
            return SimpleNamespace(returncode=0)
        with mock.patch.object(adapter_module,'config_fingerprint',return_value=adapter_module.REGISTERED_CONFIG_SHA256),\
             mock.patch.object(adapter_module,'SCHEMA_PINS',{}),\
             mock.patch.object(adapter_module.subprocess,'run',side_effect=run),\
             mock.patch.object(adapter_module.subprocess,'Popen',side_effect=RuntimeError('synthetic stop before process'))as popen:
            with self.assertRaisesRegex(RuntimeError,'synthetic stop'):self.adapter().open()
        self.assertEqual(calls[0],(str(self.path),'--version'))
        self.assertEqual(calls[1][:3],(str(self.path),'app-server','generate-json-schema'))
        self.assertEqual(popen.call_args.args[0][:3],self.command)

    def test_supplied_schema_cannot_bypass_executable_drift_before_spawn(self):
        adapter=self.adapter(schemas={})
        self.path.write_bytes(b'\x7fELF'+b'1'*124)
        with mock.patch.object(adapter_module,'config_fingerprint',return_value=adapter_module.REGISTERED_CONFIG_SHA256),\
             mock.patch.object(adapter_module.subprocess,'Popen')as popen,\
             mock.patch.object(adapter_module.subprocess,'run')as run:
            with self.assertRaises(ServiceError):adapter.open()
            popen.assert_not_called();run.assert_not_called()

    def test_mutation_during_schema_probe_is_caught_before_spawn(self):
        def run(command,**kwargs):
            self.path.write_bytes(b'\x7fELF'+b'1'*124)
            return SimpleNamespace(returncode=0,stdout=adapter_module.VERSION+'\n')
        with mock.patch.object(adapter_module,'config_fingerprint',return_value=adapter_module.REGISTERED_CONFIG_SHA256),\
             mock.patch.object(adapter_module.subprocess,'run',side_effect=run),\
             mock.patch.object(adapter_module.subprocess,'Popen')as popen:
            with self.assertRaises(ServiceError):self.adapter().open()
            popen.assert_not_called()

    def test_guard_prevents_new_rpc_effects_after_drift_but_allows_interrupt(self):
        adapter=self.adapter(schemas={});self.path.write_bytes(b'\x7fELF'+b'1'*124)
        with mock.patch.object(adapter,'_validate'),mock.patch.object(adapter,'_send')as send:
            for method in ('thread/start','thread/resume','turn/start','command/exec'):
                with self.subTest(method=method),self.assertRaises(ServiceError):adapter.call(method,{})
            send.assert_not_called()
            with mock.patch.object(adapter,'_message',side_effect=lambda _: {'id':adapter.next_id,'result':{}}):
                adapter.call('turn/interrupt',{'threadId':'fixture-thread','turnId':'fixture-turn'})
            self.assertEqual(send.call_args.args[0]['method'],'turn/interrupt')

    def test_config_echo_rejects_extra_directory_wrong_runtime_or_network(self):
        adapter=self.adapter(schemas={})
        entries=adapter_module._profile_entries(self.root,read_only=True,
            readable_paths=['AGENTS.md','README.md'],runtime_executable=self.descriptor)
        profile={'filesystem':entries,'network':{'enabled':False}}
        config={'default_permissions':adapter.permission_profile,'permissions':{adapter.permission_profile:profile}}
        result={'activePermissionProfile':{'id':adapter.permission_profile,'extends':None}}
        with mock.patch.object(adapter,'call',return_value={'config':config}):adapter._profile_echo(result)
        self.assertEqual(adapter.active_profile,adapter.permission_profile)
        for kind in ('directory','wrong_runtime','network'):
            changed=copy.deepcopy(config);active=changed['permissions'][adapter.permission_profile]
            if kind=='directory':active['filesystem'][str(self.directory)]='read'
            elif kind=='wrong_runtime':
                del active['filesystem'][str(self.path)];active['filesystem'][str(self.directory/'other')]='read'
            else:active['network']['enabled']=True
            with self.subTest(kind=kind),mock.patch.object(adapter,'call',return_value={'config':changed}),self.assertRaises(ServiceError):
                adapter._profile_echo(result)
            self.assertIsNone(adapter.active_profile)


if __name__=='__main__':unittest.main()
