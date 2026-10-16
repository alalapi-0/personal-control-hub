"""Synthetic file-identity checks; these bytes are never executed as a CLI."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.service_contract import ServiceError
from hub.task_executable import TaskExecutable


class TaskExecutableTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.path=self.root/'synthetic-runtime'
        self.path.write_bytes(b'\x7fELF'+b'\x00'*124);self.path.chmod(0o700)

    def values(self,path=None):
        path=path or self.path;info=path.stat()
        return dict(path=path,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            device=info.st_dev,inode=info.st_ino,size=info.st_size,
            task_id='PCH-FUNCTIONAL-OPT-20261016',candidate='a'*64)

    def rejected(self,operation):
        with self.assertRaises(ServiceError)as caught:operation()
        self.assertIn(caught.exception.code,{'CODEX_EXECUTABLE_REJECTED','CODEX_EXECUTABLE_DRIFT'})
        self.assertNotIn(str(self.root),str(caught.exception.as_dict()))
        return caught.exception

    def test_exact_owned_leaf_and_frozen_registration(self):
        registered=TaskExecutable(**self.values())
        self.assertEqual(registered.check(),self.path)
        with self.assertRaises(FrozenInstanceError):registered.sha256='b'*64

    def test_invalid_descriptor_fields_fail_without_filesystem_or_process_use(self):
        baseline=self.values()
        bad={'path':[Path('relative'),self.root/'..'/'alias'],
            'sha256':['A'*64,'a'*63,None], 'candidate':['A'*64,'x'*64,None],
            'task_id':['','x'*129,None], 'device':[-1,True,1.0],
            'inode':[-1,True,1.0], 'size':[-1,True,1.0,1024**3+1]}
        for field,values in bad.items():
            for value in values:
                with self.subTest(field=field,value=value):
                    self.rejected(lambda:TaskExecutable(**{**baseline,field:value}))

    def test_same_length_mutation_and_inode_replacement_invalidate_registration(self):
        registered=TaskExecutable(**self.values())
        self.path.write_bytes(b'\x7fELF'+b'1'*124)
        self.rejected(registered.check)
        self.path.write_bytes(b'\x7fELF'+b'\x00'*124)
        replacement=self.root/'replacement';replacement.write_bytes(self.path.read_bytes());replacement.chmod(0o700)
        os.replace(replacement,self.path)
        self.rejected(registered.check)

    def test_permission_and_file_type_rejections(self):
        for mode in (0o600,0o720,0o702,0o4700,0o2700):
            self.path.chmod(mode)
            with self.subTest(mode=oct(mode)):
                registered=TaskExecutable(**self.values());self.rejected(registered.check)
        self.path.chmod(0o700)
        self.path.write_bytes(b'not an ELF binary')
        self.rejected(TaskExecutable(**self.values()).check)

    def test_symlink_leaf_parent_and_additional_hardlink_are_rejected(self):
        registered=TaskExecutable(**self.values())
        link=self.root/'alias';link.symlink_to(self.path)
        self.rejected(TaskExecutable(**{**self.values(),'path':link}).check)
        directory=self.root/'directory';directory.mkdir()
        nested=directory/'runtime';nested.write_bytes(self.path.read_bytes());nested.chmod(0o700)
        alias=self.root/'directory-alias';alias.symlink_to(directory,target_is_directory=True)
        self.rejected(TaskExecutable(**{**self.values(nested),'path':alias/'runtime'}).check)
        os.link(self.path,self.root/'hardlink')
        self.rejected(registered.check)

    def test_fifo_directory_and_missing_leaf_do_not_block_or_leak_paths(self):
        baseline=self.values();fifo=self.root/'fifo';os.mkfifo(fifo,0o700)
        for path in (fifo,self.root,self.root/'missing'):
            with self.subTest(kind=path.name):
                self.rejected(TaskExecutable(**{**baseline,'path':path}).check)

    def test_owner_identity_and_registered_size_are_enforced(self):
        registered=TaskExecutable(**self.values())
        with mock.patch('hub.task_executable.os.getuid',return_value=os.getuid()+1):
            self.rejected(registered.check)
        for field,value in [('size',registered.size+1),('inode',registered.inode+1),('device',registered.device+1),('sha256','b'*64)]:
            with self.subTest(field=field):
                self.rejected(TaskExecutable(**{**self.values(),field:value}).check)

    def test_binding_replacement_during_streaming_is_detected(self):
        for kind in ('leaf','parent'):
            directory=self.root/kind;directory.mkdir()
            path=directory/'runtime';path.write_bytes(self.path.read_bytes());path.chmod(0o700)
            registered=TaskExecutable(**self.values(path));original_read=os.read;changed=False
            def read(descriptor,count):
                nonlocal changed
                result=original_read(descriptor,count)
                if result and not changed:
                    changed=True
                    if kind=='leaf':
                        replacement=directory/'replacement';replacement.write_bytes(path.read_bytes());replacement.chmod(0o700)
                        os.replace(replacement,path)
                    else:
                        directory.rename(self.root/(kind+'-retired'));directory.mkdir()
                        path.write_bytes(self.path.read_bytes());path.chmod(0o700)
                return result
            with self.subTest(kind=kind),mock.patch('hub.task_executable.os.read',side_effect=read):
                self.rejected(registered.check)
            self.assertTrue(changed)

    def test_same_inode_mutation_during_hashing_is_detected(self):
        registered=TaskExecutable(**self.values());original_read=os.read;changed=False
        def read(descriptor,count):
            nonlocal changed
            result=original_read(descriptor,count)
            if result and not changed:
                changed=True;self.path.write_bytes(b'\x7fELF'+b'2'*124)
            return result
        with mock.patch('hub.task_executable.os.read',side_effect=read):self.rejected(registered.check)
        self.assertTrue(changed)


if __name__=='__main__':unittest.main()
