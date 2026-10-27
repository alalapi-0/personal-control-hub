"""Owned ledger/storage fixtures; provider computation is explicitly MOCK."""
import copy
import hashlib
import json
import multiprocessing
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_hub_task_runtime as fixture
from hub.service_contract import ServiceError
from hub.task_storage import StorageGuard, MIN_FREE_BYTES
from hub.task_store import TaskStore
from hub.task_worker import TaskWorker
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth


def owned_process_worker(control, root, started, release, crash=False):
    class OwnedAdapter(fixture.FakeAdapter):
        def start(self,*args):
            turn=super().start(*args)
            (Path(control)/'owned-process-effects.json').write_text(json.dumps({'effects':1,'pid':os.getpid(),'provider':'MOCK'}))
            return turn
        def poll(self,timeout):
            if len(self.events)<5 and not release.is_set():started.set();time.sleep(min(timeout,.1));return None
            return super().poll(timeout)
        def check(self,argv):
            result=subprocess.run(argv,cwd=self.root,capture_output=True,text=True,timeout=3,check=False)
            return {'exitCode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
    def fault(phase):
        if crash and phase=='after_turn_before_binding':os._exit(82)
    TaskWorker(TaskStore(control),adapter_factory=OwnedAdapter,writer_check=lambda grant:grant['root']==root,fault=fault).run_one()


def owned_event_crash(control, phase):
    def fault(current):
        if current==phase:os._exit(83 if phase=='after_commit' else 84)
    TaskStore(control,fault=fault).event('fixture-task','process-crash-event',{'method':'owned/mock-crash'})


def owned_thread_binding_failure(control, root, ready, release=None, stdio_pid=None):
    class BoundAdapter(fixture.FakeAdapter):
        def __init__(self, directory):
            super().__init__(directory)
            if stdio_pid is not None:
                self.proc=SimpleNamespace(pid=stdio_pid);self.schema_hashes={}
        def start(self,*args):
            (Path(control)/'unexpected-start').write_text('MOCK start called')
            return super().start(*args)
    def fault(phase):
        if phase=='after_thread_binding':raise ServiceError('OWNED_AFTER_THREAD_BINDING',outcome='UNKNOWN')
    TaskWorker(TaskStore(control),adapter_factory=BoundAdapter,
        writer_check=lambda grant:grant['root']==root,fault=fault).run_one()
    ready.set()
    if release is not None:release.wait(10)


def owned_fresh_storage_probe(control,output):
    store=TaskStore(control);fixture.FakeAdapter.opened=fixture.FakeAdapter.effects=0
    payload={'request_id':'same-id','project_id':'fixture-project','parent_task_id':None,'changed':'replacement'}
    results=[]
    for operation in (store.check_storage,lambda:store.admit(payload,{'changed':'replacement'}),
                      lambda:store.register_grant({'grant_id':'fresh-grant','version':1}),
                      lambda:TaskWorker(store,adapter_factory=fixture.FakeAdapter).run_one(),
                      lambda:store.task('same-id'),store.tasks,store.grants,lambda:store.events('same-id')):
        try:operation();results.append('UNEXPECTED_SUCCESS')
        except ServiceError as error:results.append(error.details.get('reason_code',error.code))
    output.put({'pid':os.getpid(),'results':results,'opened':fixture.FakeAdapter.opened,'effects':fixture.FakeAdapter.effects})


def owned_registration_crash(control,phase):
    def fault(current):
        if current==phase:os._exit(91 if phase=='after_storage_registration'else 92)
    TaskStore(control,fault=fault).register_storage()


class TaskStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)/'control';self.root.mkdir()
        self.guard=StorageGuard.register_internal(self.root)
        self.grant={'grant_id':'guard-fixture','version':1,'source':'owned_test_only'}

    def initialized(self,root=None,guard=None):
        store=TaskStore(root or self.root,storage_guard=guard);store.register_storage();return store

    def blocked(self,store,reason,*,initial=False):
        with self.assertRaises(ServiceError)as caught:
            store.register_storage() if initial else store.register_grant(self.grant)
        self.assertEqual(caught.exception.code,'TASK_STORAGE_BLOCKED')
        self.assertEqual(caught.exception.details,{'reason_code':reason})

    def test_actual_mount_pin_survives_restart_and_has_no_public_paths(self):
        store=self.initialized(guard=self.guard);store.register_grant(self.grant)
        reopened=TaskStore(self.root);self.assertEqual(reopened.check_storage()['state'],'ready')
        self.assertEqual(reopened.storage_guard.profile,self.guard.profile)
        self.assertEqual(set(reopened.storage_status()),{'state','profile_version','reason_code'})
        self.assertTrue(self.guard.profile['mount']['source']);self.assertGreaterEqual(MIN_FREE_BYTES,64*1024*1024)

    def fresh_probe(self,root,reason):
        ctx=multiprocessing.get_context('fork');output=ctx.Queue();child=ctx.Process(target=owned_fresh_storage_probe,args=(str(root),output))
        child.start();child.join(3)
        if child.is_alive():child.terminate();child.join(3);self.fail('Owned probe timeout')
        self.assertEqual(child.exitcode,0);result=output.get(timeout=1);output.close();output.join_thread()
        self.assertEqual(result['results'],[reason]*8);self.assertEqual((result['opened'],result['effects']),(0,0))

    def test_ordinary_startup_and_grant_never_initialize_unregistered_storage(self):
        self.fresh_probe(self.root,'REGISTRATION_MISSING');self.assertEqual(list(self.root.iterdir()),[])
        store=self.initialized();self.assertEqual(store.check_storage()['state'],'ready')
        with self.assertRaises(ServiceError):store.register_storage()

    def test_fresh_process_missing_ledger_empty_root_marker_loss_and_replaced_ledger(self):
        for scenario,reason in [('ledger_missing','LEDGER_MISSING'),('empty_root','REGISTRATION_MISSING'),
                                ('marker_missing','REGISTRATION_MISSING'),('ledger_replaced','LEDGER_IDENTITY_CHANGED')]:
            with self.subTest(scenario=scenario):
                root=self.root/scenario;root.mkdir();store=self.initialized(root)
                payload={'request_id':'same-id','project_id':'fixture-project','parent_task_id':None}
                store.admit(payload,{'original':'accepted'});original=store.path.read_bytes()
                if scenario=='ledger_missing':store.path.unlink()
                elif scenario=='empty_root':root.rename(root.with_name(scenario+'-original'));root.mkdir()
                elif scenario=='marker_missing':store.registration_path.unlink()
                else:
                    moved=store.path.with_name('original.sqlite3');store.path.rename(moved);shutil.copyfile(moved,store.path)
                before={str(p.relative_to(root)):p.read_bytes()for p in root.rglob('*')if p.is_file()}
                self.fresh_probe(root,reason)
                after={str(p.relative_to(root)):p.read_bytes()for p in root.rglob('*')if p.is_file()}
                self.assertEqual(after,before)
                if scenario=='empty_root':self.assertEqual(list(root.iterdir()),[])
                elif scenario=='ledger_missing':self.assertFalse(store.path.exists())
                else:self.assertEqual(store.path.read_bytes(),original)

    def test_actual_registration_process_crashes_never_auto_finish_or_reinitialize(self):
        for phase,code in [('after_storage_registration',91),('before_storage_registration_ready',92)]:
            with self.subTest(phase=phase):
                root=self.root/phase;root.mkdir();ctx=multiprocessing.get_context('fork')
                child=ctx.Process(target=owned_registration_crash,args=(str(root),phase));child.start();child.join(3)
                if child.is_alive():child.terminate();child.join(3);self.fail('Owned registration timeout')
                self.assertEqual(child.exitcode,code)
                before={str(p.relative_to(root)):p.read_bytes()for p in root.rglob('*')if p.is_file()}
                self.fresh_probe(root,'REGISTRATION_INCOMPLETE')
                self.assertEqual({str(p.relative_to(root)):p.read_bytes()for p in root.rglob('*')if p.is_file()},before)
                self.assertEqual(TaskStore(root).path.exists(),phase=='before_storage_registration_ready')

    def test_registration_link_permissions_corruption_and_capacity_block_without_writes(self):
        for scenario in ['symlink','hardlink','mode','corrupt','oversize']:
            with self.subTest(scenario=scenario):
                root=self.root/scenario;root.mkdir();store=self.initialized(root);store.register_grant(self.grant)
                before=store.path.read_bytes();marker=store.registration_path
                if scenario in {'symlink','hardlink'}:
                    original=marker.with_name('owned-original-registration.json');marker.rename(original)
                    marker.symlink_to(original)if scenario=='symlink'else marker.hardlink_to(original)
                elif scenario=='mode':marker.chmod(0o644)
                elif scenario=='corrupt':marker.write_text('unconfirmed registration')
                else:marker.write_bytes(b'x'*16385)
                self.fresh_probe(root,'REGISTRATION_REJECTED');self.assertEqual(store.path.read_bytes(),before)

    def test_missing_mount_low_disk_unknown_profile_create_nothing(self):
        store=TaskStore(self.root,storage_guard=self.guard)
        with patch.object(self.guard,'space',return_value=SimpleNamespace(f_bavail=0,f_frsize=4096)):
            self.blocked(store,'LOW_SPACE',initial=True)
        with patch.object(self.guard,'mount',side_effect=ServiceError('TASK_STORAGE_BLOCKED',status=503,details={'reason_code':'MOUNT_UNAVAILABLE'})):
            self.blocked(store,'MOUNT_UNAVAILABLE',initial=True)
        with patch.object(self.guard,'profile',{**self.guard.profile,'version':99}):self.blocked(store,'PROFILE_REJECTED',initial=True)
        self.assertEqual(list(self.root.iterdir()),[])
        missing=Path(self.directory.name)/'missing-mount'
        self.blocked(TaskStore(missing),'ROOT_UNAVAILABLE');self.assertFalse(missing.exists())

    def test_mount_uuid_drift_and_profile_change_never_repin_after_restart(self):
        store=self.initialized(guard=self.guard);store.register_grant(self.grant)
        before=store.path.read_bytes();profile=copy.deepcopy(self.guard.profile)
        drift={**profile['mount'],'uuid':'different-owned-fixture-uuid'}
        reopened=TaskStore(self.root,storage_guard=StorageGuard(self.root,profile,mount=lambda _:drift))
        self.blocked(reopened,'MOUNT_IDENTITY_CHANGED');self.assertEqual(reopened.path.read_bytes(),before)
        altered=StorageGuard(self.root,{**profile,'inode':profile['inode']+1})
        self.blocked(TaskStore(self.root,storage_guard=altered),'PROFILE_VERSION_STALE')
        self.assertEqual(store.path.read_bytes(),before)

    def test_root_replacement_symlink_and_traversal_have_no_fallback(self):
        store=self.initialized();store.register_grant(self.grant);before=store.path.read_bytes()
        original=self.root.with_name('original');self.root.rename(original);self.root.mkdir()
        shutil.copytree(original/'data',self.root/'data')
        self.blocked(TaskStore(self.root),'ROOT_IDENTITY_CHANGED')
        self.assertEqual(store.path.read_bytes(),before)
        link=Path(self.directory.name)/'alias';link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ServiceError)as caught:TaskStore(link).register_grant(self.grant)
        self.assertEqual(caught.exception.code,'TASK_LOCATION_REJECTED')
        self.blocked(TaskStore(self.root/'..'/'escape'),'PATH_REJECTED')
        self.assertFalse((Path(self.directory.name)/'escape').exists())

    def test_precommit_low_space_rolls_back_and_preserves_grant(self):
        store=self.initialized(guard=self.guard);store.register_grant(self.grant)
        original=store.grant('guard-fixture')
        def fault(phase):
            if phase=='before_commit':self.guard.space=lambda _:SimpleNamespace(f_bavail=0,f_frsize=4096)
        store.fault=fault
        with self.assertRaises(ServiceError):store.register_grant({**self.grant,'version':2})
        self.assertEqual(store.grant('guard-fixture'),original)
        self.assertEqual(store.storage_status()['reason_code'],'LOW_SPACE')

    def test_parent_replacement_even_with_same_root_inode_is_rejected(self):
        parent=Path(self.directory.name)/'parent';parent.mkdir();root=parent/'ledger';root.mkdir()
        store=self.initialized(root);store.register_grant(self.grant);before=store.path.read_bytes()
        old=parent.with_name('old-parent');parent.rename(old);parent.mkdir();(old/'ledger').rename(root)
        self.blocked(TaskStore(root),'PARENT_IDENTITY_CHANGED');self.assertEqual(store.path.read_bytes(),before)
        self.fresh_probe(root,'PARENT_IDENTITY_CHANGED')

    def test_schema1_remains_readable_but_never_silently_upgraded(self):
        f=fixture.TaskRuntimeTests();f.setUp();self.addCleanup(f.doCleanups);f.service.submit(f.payload)
        with sqlite3.connect(f.store.path)as c:
            c.executescript('DROP TABLE storage_profile; DROP TABLE meta; CREATE TABLE meta(version INTEGER CHECK(version=1)); INSERT INTO meta VALUES(1);')
        before=f.store.path.read_bytes();reopened=TaskStore(f.control)
        self.assertEqual(reopened.task('fixture-task')['id'],'fixture-task')
        with self.assertRaises(ServiceError)as caught:reopened.claim('new-worker')
        self.assertEqual(caught.exception.details,{'reason_code':'PROFILE_REGISTRATION_REQUIRED'})
        self.assertEqual(reopened.path.read_bytes(),before)
        self.assertEqual(reopened.storage_status()['state'],'blocked')

    def test_corrupt_database_and_hardlink_are_preserved(self):
        store=self.initialized();store.register_grant(self.grant)
        store.path.write_bytes(b'owned corrupted fixture, preserve original');before=store.path.read_bytes()
        with self.assertRaises(ServiceError)as caught:store.register_grant(self.grant)
        self.assertEqual(caught.exception.code,'TASK_STORE_CORRUPT');self.assertEqual(store.path.read_bytes(),before)
        linked=self.root/'hardlink';linked.hardlink_to(store.path)
        with self.assertRaises(ServiceError)as caught:store.register_grant(self.grant)
        self.assertEqual(caught.exception.code,'TASK_LOCATION_REJECTED');self.assertEqual(linked.read_bytes(),before)

    def test_database_and_event_capacity_preserve_original_records(self):
        store=self.initialized();store.register_grant(self.grant);accepted=[]
        with patch('hub.task_store.MAX_DATABASE_BYTES',256*1024):
            for index in range(20):
                item={'grant_id':f'capacity-{index}','version':1,'synthetic':'x'*20000}
                try:store.register_grant(item);accepted.append(item)
                except ServiceError as error:
                    self.assertEqual(error.code,'TASK_STORAGE_CAPACITY');break
            else:self.fail('Expected bounded SQLite FULL')
            self.assertLessEqual(store.path.stat().st_size,256*1024)
        self.assertGreater(len(accepted),0)
        for item in accepted:self.assertEqual(store.grant(item['grant_id']),item)
        self.assertEqual(store.grant('guard-fixture'),self.grant)
        f=fixture.TaskRuntimeTests();f.setUp();self.addCleanup(f.doCleanups);f.service.submit(f.payload)
        with patch('hub.task_store.MAX_EVENTS',2):
            f.store.event('fixture-task','cap-event-one',{'method':'owned/one'})
            f.store.event('fixture-task','cap-event-two',{'method':'owned/two'})
            before=f.store.events('fixture-task')
            with self.assertRaises(ServiceError)as caught:f.store.event('fixture-task','cap-event-three',{'method':'owned/three'})
            self.assertEqual(caught.exception.code,'TASK_EVENT_CAPACITY')
            self.assertEqual(f.store.events('fixture-task'),before)
            self.assertEqual([e['sequence']for e in before],[1,2])


class RecoveryWindowTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TaskRuntimeTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_missing_ledger_public_view_never_reports_empty_new_workspace(self):
        f=self.f;f.service.submit(f.payload);f.store.path.unlink()
        listing=f.service.list();self.assertFalse(listing['tasks_available']);self.assertFalse(listing['execution_enabled'])
        self.assertEqual(listing['storage']['reason_code'],'LEDGER_MISSING')
        self.assertNotIn(str(f.control),json.dumps(listing))
        with self.assertRaises(ServiceError)as caught:f.service.get('fixture-task')
        self.assertEqual(caught.exception.details,{'reason_code':'LEDGER_MISSING'})
        self.assertFalse(f.store.path.exists());self.assertEqual(fixture.FakeAdapter.opened,0)

    def test_low_or_unknown_space_reads_only_identity_verified_original_records(self):
        f=self.f;f.service.submit(f.payload)
        for reason,space in [('LOW_SPACE',lambda _:SimpleNamespace(f_bavail=0,f_frsize=4096)),
                             ('SPACE_UNAVAILABLE',lambda _:(_ for _ in ()).throw(OSError()))]:
            with self.subTest(reason=reason):
                f.store.storage_guard.space=space
                self.assertEqual(f.service.list()['tasks'][0]['id'],'fixture-task')
                self.assertEqual(f.service.get('fixture-task')['storage']['reason_code'],reason)
                self.assertEqual(f.store.grant('fixture-grant'),f.grant)
                with self.assertRaises(ServiceError):f.worker().run_one()
        self.assertEqual(fixture.FakeAdapter.opened,0)

    def test_actual_http_absent_empty_and_altered_copied_root_never_publish_replacement(self):
        for scenario,reason in [('missing','ROOT_UNAVAILABLE'),('empty','REGISTRATION_MISSING'),
                               ('copied_altered','ROOT_IDENTITY_CHANGED'),('marker_missing','REGISTRATION_MISSING'),
                               ('ledger_replaced','LEDGER_IDENTITY_CHANGED'),('parent_replaced','PARENT_IDENTITY_CHANGED'),
                               ('mount_drift','MOUNT_IDENTITY_CHANGED')]:
            with self.subTest(scenario=scenario):
                f=fixture.TaskRuntimeTests();f.setUp();self.addCleanup(f.doCleanups);f.service.submit(f.payload)
                client=fixture.http_fixture.LocalHTTPTests();client.projects=f.helper.projects;client.designs=f.helper.fixture.service
                client.owner_proof='owned-read-fence-'+'b'*64;client.cookie=client.csrf=None
                client.server=HubHTTPServer(client.projects,client.designs,tasks=f.service,owner_auth=OwnerAuth(provider=lambda:client.owner_proof))
                client.thread=threading.Thread(target=client.server.serve_forever,kwargs={'poll_interval':.01});client.thread.start();self.addCleanup(client.stop);client.session()
                if scenario in {'missing','empty','copied_altered'}:
                    original=f.control.with_name('original-'+scenario);f.control.rename(original)
                    if scenario=='empty':f.control.mkdir()
                    elif scenario=='copied_altered':
                        shutil.copytree(original,f.control)
                        with sqlite3.connect(f.store.path)as connection:connection.execute("UPDATE tasks SET status='checks_complete'")
                elif scenario=='marker_missing':f.store.registration_path.unlink()
                elif scenario=='ledger_replaced':
                    original=f.store.path.with_name('owned-original.sqlite3');f.store.path.rename(original);shutil.copyfile(original,f.store.path)
                elif scenario=='parent_replaced':
                    parent=f.control.parent;original=parent.with_name(parent.name+'-owned-original');parent.rename(original);parent.mkdir();(original/f.control.name).rename(f.control)
                    self.addCleanup(shutil.rmtree,original)
                else:
                    from hub.workbench_store import canonical,digest
                    wrapper=json.loads(f.store.registration_path.read_bytes());wrapper['record']['profile']['mount']['uuid']='owned-synthetic-mount-drift'
                    wrapper['hash']=digest(wrapper['record']);f.store.registration_path.write_bytes(canonical(wrapper))
                    f.store=TaskStore(f.control);f.service.store=f.store
                before={str(p.relative_to(f.control)):p.read_bytes()for p in f.control.rglob('*')if p.is_file()}if f.control.exists()else{}
                with patch('hub.task_store.sqlite3.connect',side_effect=AssertionError('No SQLite open on invalid identity')):
                    status,_,body=client.call(path='/api/tasks');self.assertEqual(status,200)
                    self.assertFalse(body['data']['tasks_available']);self.assertEqual(body['data']['tasks'],[])
                    self.assertEqual(body['data']['storage']['reason_code'],reason)
                    status,_,body=client.call(path='/api/tasks/fixture-task');self.assertEqual(status,503)
                    self.assertNotIn('checks_complete',json.dumps(body));self.assertNotIn(str(f.control),json.dumps(body))
                    for operation in (lambda:f.store.task('fixture-task'),f.store.tasks,f.store.grants,lambda:f.store.events('fixture-task'),lambda:f.worker().run_one()):
                        with self.assertRaises(ServiceError)as caught:operation()
                        self.assertEqual(caught.exception.details,{'reason_code':reason})
                after={str(p.relative_to(f.control)):p.read_bytes()for p in f.control.rglob('*')if p.is_file()}if f.control.exists()else{}
                self.assertEqual(after,before);self.assertEqual(fixture.FakeAdapter.opened,0)
                if scenario=='missing':self.assertFalse(f.control.exists())

    def test_commit_before_and_after_intent_are_read_before_retry(self):
        f=self.f
        f.store.fault=lambda phase:(_ for _ in ()).throw(OSError())if phase=='before_commit'else None
        with self.assertRaises(ServiceError)as caught:f.service.submit(f.payload)
        self.assertEqual(caught.exception.outcome,'NOT_COMMITTED');self.assertIsNone(f.store.task('fixture-task'))
        f.store.fault=lambda phase:(_ for _ in ()).throw(OSError())if phase=='after_commit'else None
        with self.assertRaises(ServiceError)as caught:f.service.submit(f.payload)
        self.assertEqual(caught.exception.outcome,'UNKNOWN')
        f.store.fault=lambda _:None
        self.assertTrue(f.service.submit(f.payload)['replayed']);self.assertEqual(len(f.store.tasks()),1)
        self.assertEqual(fixture.FakeAdapter.effects,0)

    def test_pinned_storage_checked_after_dispatch_fault_before_adapter_open(self):
        f=self.f;f.service.submit(f.payload);guard=f.store.storage_guard
        def fault(phase):
            if phase=='before_dispatch':guard.space=lambda _:SimpleNamespace(f_bavail=0,f_frsize=4096)
        f.worker(fault=fault).run_one()
        self.assertEqual((fixture.FakeAdapter.opened,fixture.FakeAdapter.effects),(0,0))
        self.assertEqual(f.store.task('fixture-task')['status'],'running')
        self.assertEqual(f.service.get('fixture-task')['storage']['reason_code'],'LOW_SPACE')
        # Writes cannot report a new durable state while the guard blocks them.
        guard.space=StorageGuard.register_internal(f.control).space
        with patch('hub.task_store.time.time',return_value=time.time()+16):self.assertIsNone(f.store.claim('restarted-worker'))
        self.assertEqual(f.store.task('fixture-task')['status'],'requires_reconcile')

    def test_provider_accept_before_binding_effect_is_not_replayed(self):
        f=self.f;f.service.submit(f.payload)
        def crash(phase):
            if phase=='after_turn_before_binding':raise ServiceError('OWNED_MOCK_ACCEPT_WINDOW',outcome='UNKNOWN')
        f.worker(fault=crash).run_one();task=f.store.task('fixture-task')
        self.assertEqual(task['status'],'requires_reconcile');self.assertIsNone(task['turn_id'])
        self.assertEqual((f.workspace/'index.html').read_text(),fixture.AFTER)
        self.assertEqual(fixture.FakeAdapter.effects,1);self.assertFalse(f.worker().run_one())
        self.assertTrue(f.service.submit(f.payload)['replayed']);self.assertEqual(fixture.FakeAdapter.effects,1)

    def test_event_unknown_commit_and_terminal_commit_survive_read_only_reconnect(self):
        f=self.f;f.service.submit(f.payload)
        f.store.fault=lambda phase:(_ for _ in ()).throw(OSError())if phase=='after_commit'else None
        with self.assertRaises(ServiceError):f.store.event('fixture-task','owned-event',{'method':'owned/mock'})
        f.store.fault=lambda _:None
        self.assertFalse(f.store.event('fixture-task','owned-event',{'method':'owned/mock'}))
        self.assertEqual(len(f.store.events('fixture-task')),1)
        f.worker().run_one();before=f.store.path.read_bytes();task=f.service.get('fixture-task')
        for _ in range(3):self.assertEqual(f.service.get('fixture-task')['task'],task['task'])
        self.assertEqual(f.store.path.read_bytes(),before);self.assertFalse(f.worker().run_one());self.assertEqual(fixture.FakeAdapter.effects,1)


class OwnedProcessRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TaskRuntimeTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.ctx=multiprocessing.get_context('fork');self.children=[];self.addCleanup(self.cleanup_children)
    def cleanup_children(self):
        for child in self.children:
            if child.is_alive():child.terminate()
            child.join(3)
            if child.is_alive():self.fail('Owned fixture child did not close')
    def child(self,target,args):
        process=self.ctx.Process(target=target,args=args);process.start();self.children.append(process);return process

    def test_known_thread_no_turn_recovery_preserves_thread_and_starts_once(self):
        f=self.f;f.service.submit(f.payload)
        ready,release=self.ctx.Event(),self.ctx.Event()
        stdio=self.child(time.sleep,(30,))
        worker=self.child(owned_thread_binding_failure,(str(f.control),str(f.workspace),ready,release,stdio.pid))
        self.assertTrue(ready.wait(3))
        original=f.store.task('fixture-task')
        self.assertEqual(original['status'],'requires_reconcile')
        self.assertEqual(original['thread_id'],'fixture-thread');self.assertIsNone(original['turn_id'])
        self.assertFalse((f.control/'unexpected-start').exists())
        self.assertEqual((f.workspace/'index.html').read_text(),fixture.BEFORE)
        proof={'source':'app_server_stdio.thread/read','root':str(f.workspace),'thread_id':'fixture-thread',
            'thread_status':'idle','turns_complete':True,'turns':[],'observed_at':time.time()}
        with self.assertRaisesRegex(ServiceError,'TASK_OWNER_STILL_ACTIVE'):
            f.store.reconcile_owned('fixture-task',proof)
        release.set();worker.join(3);self.assertEqual(worker.exitcode,0)
        # The recorded stdio identity must close independently of its worker.
        with self.assertRaisesRegex(ServiceError,'TASK_OWNER_STILL_ACTIVE'):
            f.store.reconcile_owned('fixture-task',proof)
        stdio.terminate();stdio.join(3);self.assertFalse(stdio.is_alive())
        history=f.store.events('fixture-task')
        f.store.reconcile_owned('fixture-task',proof)
        current=f.store.task('fixture-task');events=f.store.events('fixture-task')
        self.assertEqual(current['id'],original['id']);self.assertEqual(current['thread_id'],original['thread_id'])
        self.assertEqual(current['status'],'queued');self.assertIsNone(current['turn_id'])
        self.assertEqual(events[:-1],history)
        self.assertEqual(events[-1]['data']['original_result'],original['result'])
        self.assertEqual(events[-1]['data']['original_status'],original['status'])
        self.assertEqual(events[-1]['data']['original_thread_id'],original['thread_id'])
        self.assertEqual(events[-1]['data']['action'],'same_owned_thread_requeue_before_any_turn')
        with self.assertRaises(ServiceError):f.store.reconcile_owned('fixture-task',proof)
        calls=[]
        class ResumingAdapter(fixture.FakeAdapter):
            def thread(self,previous=None):
                calls.append(('resume',previous));return super().thread(previous)
            def start(self,thread,text,image):
                calls.append(('start',thread));return super().start(thread,text,image)
        resumed=TaskWorker(f.store,adapter_factory=ResumingAdapter,writer_check=lambda _:True)
        self.assertTrue(resumed.run_one());self.assertFalse(resumed.run_one())
        self.assertEqual(calls,[('resume','fixture-thread'),('start','fixture-thread')])
        self.assertEqual(len(f.store.tasks()),1)
        self.assertEqual(f.store.task('fixture-task')['status'],'checks_complete')

    def test_known_thread_recovery_rejects_incomplete_stale_foreign_and_turn_intent(self):
        f=self.f;f.service.submit(f.payload);ready=self.ctx.Event()
        worker=self.child(owned_thread_binding_failure,(str(f.control),str(f.workspace),ready))
        self.assertTrue(ready.wait(3));worker.join(3);self.assertEqual(worker.exitcode,0)
        proof={'source':'app_server_stdio.thread/read','root':str(f.workspace),'thread_id':'fixture-thread',
            'thread_status':'idle','turns_complete':True,'turns':[],'observed_at':time.time()}
        original=f.store.task('fixture-task');history=f.store.events('fixture-task')
        invalid=[{'turns':[{'id':'unattributed-turn','status':'completed'}]},{'turns_complete':False},
            {'thread_id':'foreign-thread'},{'observed_at':time.time()-31},{'observed_at':time.time()+10},
            {'thread_status':'active'},{'turns':None},{'turn_id':'unattributed-turn'},{'source':'caller_claim'}]
        for changes in invalid:
            with self.subTest(changes=changes),self.assertRaises(ServiceError):
                f.store.reconcile_owned('fixture-task',{**proof,**changes})
            self.assertEqual(f.store.task('fixture-task'),original)
            self.assertEqual(f.store.events('fixture-task'),history)
        incomplete=dict(proof);del incomplete['turns_complete']
        with self.assertRaises(ServiceError):f.store.reconcile_owned('fixture-task',incomplete)
        (f.workspace/'index.html').write_text(fixture.AFTER)
        with self.assertRaises(ServiceError):f.store.reconcile_owned('fixture-task',proof)
        (f.workspace/'index.html').write_text(fixture.BEFORE)
        # Even empty metadata cannot erase the provider-acceptance ambiguity.
        f.store.event('fixture-task','turn-intent-ambiguous',{'method':'turn/start','outcome':'UNKNOWN'})
        history=f.store.events('fixture-task')
        with self.assertRaises(ServiceError):f.store.reconcile_owned('fixture-task',proof)
        self.assertEqual(f.store.task('fixture-task'),original)
        self.assertEqual(f.store.events('fixture-task'),history)
        self.assertFalse((f.control/'unexpected-start').exists())

    def test_known_thread_recovery_rejects_changed_authorization(self):
        f=self.f;f.service.submit(f.payload);ready=self.ctx.Event()
        worker=self.child(owned_thread_binding_failure,(str(f.control),str(f.workspace),ready))
        self.assertTrue(ready.wait(3));worker.join(3);self.assertEqual(worker.exitcode,0)
        proof={'source':'app_server_stdio.thread/read','root':str(f.workspace),'thread_id':'fixture-thread',
            'thread_status':'idle','turns_complete':True,'turns':[],'observed_at':time.time()}
        original=f.store.task('fixture-task');history=f.store.events('fixture-task')
        f.store.register_grant({**f.grant,'version':2})
        with self.assertRaises(ServiceError):f.store.reconcile_owned('fixture-task',proof)
        self.assertEqual(f.store.task('fixture-task'),original)
        self.assertEqual(f.store.events('fixture-task'),history)

    def test_actual_http_restart_does_not_own_worker_and_same_request_effects_once(self):
        f=self.f;f.service.submit(f.payload)
        started,release=self.ctx.Event(),self.ctx.Event()
        worker=self.child(owned_process_worker,(str(f.control),str(f.workspace),started,release))
        guard=self.child(time.sleep,(30,))
        self.assertTrue(started.wait(3));self.assertTrue(worker.is_alive())
        def client():
            helper=fixture.http_fixture.LocalHTTPTests();helper.projects=f.helper.projects;helper.designs=f.helper.fixture.service
            helper.owner_proof='owned-process-fixture-proof-'+'b'*64
            helper.server=HubHTTPServer(helper.projects,helper.designs,tasks=f.service,owner_auth=OwnerAuth(provider=lambda:helper.owner_proof))
            helper.thread=threading.Thread(target=helper.server.serve_forever,kwargs={'poll_interval':.01});helper.thread.start()
            self.addCleanup(helper.stop);helper.cookie=helper.csrf=None;helper.session();return helper
        first=client();first_instance=first.server.backend_instance
        status,_,body=first.call(path='/api/tasks/fixture-task');self.assertEqual(status,200)
        self.assertEqual(body['data']['task']['status'],'running')
        self.assertEqual(body['data']['task']['turn_id'],'fixture-turn')
        first.stop();self.assertTrue(worker.is_alive());self.assertTrue(guard.is_alive())
        second=client();self.assertNotEqual(second.server.backend_instance,first_instance)
        self.assertEqual(second.call(path='/api/tasks/fixture-task')[2]['data']['task']['id'],'fixture-task')
        release.set();worker.join(5);self.assertFalse(worker.is_alive());self.assertEqual(worker.exitcode,0)
        received=second.call(path='/api/tasks/fixture-task')[2]['data']
        self.assertEqual(received['task']['status'],'checks_complete');self.assertEqual(received['task']['result']['checks'][0]['exit'],0)
        self.assertEqual(json.loads((f.control/'owned-process-effects.json').read_text())['effects'],1)
        for _ in range(3):self.assertEqual(second.call(path='/api/tasks/fixture-task')[2]['data']['task']['id'],'fixture-task')
        self.assertTrue(f.service.submit(f.payload)['replayed']);self.assertFalse(f.worker().run_one())
        self.assertTrue(guard.is_alive());self.assertEqual(fixture.FakeAdapter.effects,0)

    def test_actual_executor_exit_after_provider_accept_never_replays(self):
        f=self.f;f.service.submit(f.payload)
        worker=self.child(owned_process_worker,(str(f.control),str(f.workspace),self.ctx.Event(),self.ctx.Event(),True))
        worker.join(3);self.assertEqual(worker.exitcode,82)
        effects=json.loads((f.control/'owned-process-effects.json').read_text());self.assertEqual(effects['effects'],1)
        self.assertEqual(f.store.task('fixture-task')['thread_id'],'fixture-thread');self.assertIsNone(f.store.task('fixture-task')['turn_id'])
        with patch('hub.task_store.time.time',return_value=time.time()+16):self.assertIsNone(f.store.claim('next-owned-worker'))
        self.assertEqual(f.store.task('fixture-task')['status'],'requires_reconcile');self.assertFalse(f.worker().run_one())
        self.assertEqual(json.loads((f.control/'owned-process-effects.json').read_text()),effects)

    def test_actual_event_commit_crashes_keep_original_ids_and_sequence(self):
        f=self.f;f.service.submit(f.payload)
        before=self.child(owned_event_crash,(str(f.control),'before_commit'));before.join(3);self.assertEqual(before.exitcode,84)
        self.assertEqual(f.store.events('fixture-task'),[])
        after=self.child(owned_event_crash,(str(f.control),'after_commit'));after.join(3);self.assertEqual(after.exitcode,83)
        records=f.store.events('fixture-task');self.assertEqual(len(records),1);self.assertEqual(records[0]['key'],'process-crash-event')
        self.assertFalse(f.store.event('fixture-task','process-crash-event',{'method':'owned/mock-crash'}))
        self.assertEqual(f.store.events('fixture-task'),records);self.assertEqual(fixture.FakeAdapter.effects,0)


class PlanHistoryRecoveryStatusTests(unittest.TestCase):
    """Only disposable registered SQLite; status never retries migration."""
    def fixture(self):
        import test_hub_plan_admission as plans
        f=plans.PlanRevisionTests();f.setUp();self.addCleanup(f.doCleanups)
        return f

    def bind(self,f,*,authorize=lambda:None,readers=lambda _:True,command=None):
        from hub.plan_admission import PlanHistoryMigrationAuthority,HISTORY_V2
        from hub.workbench_store import digest
        with f.store._connection()as db:raw=f.store._decode(*db.execute('SELECT body,hash FROM plan_inputs').fetchone())
        command=command or {'request_id':'synthetic-history-query','plan_id':f.p['plan_id'],
            'plan_sha256':digest(f.p),'expected_input_sha256':digest(raw),
            'reader_set_sha256':'c'*64,'target_format':HISTORY_V2}
        cap=PlanHistoryMigrationAuthority.configured(command,plan_authority=f.cap,authorize=authorize,readers_compatible=readers)
        reader=TaskStore(f.store.root,plan_authority=f.cap,plan_history_recovery_authority=cap)
        return command,cap,reader

    def status(self,reader,command,expected):
        from hub.workbench_store import digest
        before=reader.path.read_bytes();registration=reader.registration_path.read_bytes()
        result=reader.plan_history_migration_status(command['plan_id'],command)
        self.assertEqual(result,{'state':expected,'request_sha256':digest(command)})
        self.assertEqual(reader.path.read_bytes(),before);self.assertEqual(reader.registration_path.read_bytes(),registration)
        self.assertNotIn(str(reader.root),json.dumps(result))
        return result

    def migrate(self,f,command,cap):
        return f.store.migrate_plan_history(command['plan_id'],command,authority=cap)

    def test_raw_v1_preimage_and_failed_transaction_are_read_without_retry(self):
        from hub.workbench_store import digest
        f=self.fixture();command,cap,reader=self.bind(f)
        self.status(reader,command,'legacy_preimage_intact')
        old=copy.deepcopy(f.p);target=f.target();f.bind(target,digest(old))
        f.store.reconcile_plan(target,expected_sha256=digest(old),request_id='synthetic-v1',authority=f.cap)
        self.status(reader,command,'requires_reconcile')
        command,cap,reader=self.bind(f)
        self.status(reader,command,'legacy_preimage_intact');before=f.store.path.read_bytes()
        for phase in ('before_plan_history_write','before_plan_history_commit','before_commit'):
            def fail(where):
                if where==phase:raise OSError('synthetic rollback')
            f.store.fault=fail
            with self.subTest(phase=phase),self.assertRaises((OSError,ServiceError)):self.migrate(f,command,cap)
            self.assertEqual(f.store.path.read_bytes(),before)
            self.status(reader,command,'legacy_preimage_intact')
        self.assertEqual(f.store.tasks(),[])

    def test_lost_commit_receipt_and_later_versions_are_observed_without_replay(self):
        from hub.workbench_store import digest
        f=self.fixture();command,cap,reader=self.bind(f)
        def lost(where):
            if where=='after_commit':raise OSError('synthetic lost response')
        f.store.fault=lost
        with self.assertRaises(ServiceError)as error:self.migrate(f,command,cap)
        self.assertEqual(error.exception.outcome,'UNKNOWN');f.store.fault=lambda _:None
        with patch.object(TaskStore,'migrate_plan_history',side_effect=AssertionError('Status must not retry')):
            self.status(reader,command,'migration_committed')
            old=copy.deepcopy(f.p);target=f.target();f.bind(target,digest(old))
            f.store.reconcile_plan(target,expected_sha256=digest(old),request_id='after-migration',authority=f.cap)
            self.status(reader,command,'migration_committed')
            # Read authorization does not reactivate a paused plan or certify
            # reader deployment. The frozen original capability is retained.
            f.control={'state':'paused','revision':2}
            self.status(reader,command,'migration_committed')
        self.assertEqual(f.store.tasks(),[])

    def test_host_capability_same_plan_and_exact_request_are_required(self):
        from hub.plan_admission import PlanAuthority
        f=self.fixture();command,cap,reader=self.bind(f);before=f.store.path.read_bytes()
        with self.assertRaises(ServiceError)as error:f.store.plan_history_migration_status(f.p['plan_id'],command)
        self.assertEqual(error.exception.code,'PLAN_HISTORY_AUTHORITY_REQUIRED')
        with self.assertRaises(ServiceError):TaskStore(f.store.root,plan_authority=f.cap,plan_history_recovery_authority={})
        clone=PlanAuthority.configured(f.cap.snapshot(),authorize=f.cap._authorize,controls=f.cap._controls,routes=f.cap._routes,package_for=f.cap._package)
        with self.assertRaises(ServiceError)as error:TaskStore(f.store.root,plan_authority=clone,plan_history_recovery_authority=cap)
        self.assertEqual(error.exception.code,'PLAN_HISTORY_AUTHORITY_MISMATCH')
        for field in command:
            changed={**command,field:'wrong-request'if field in ('request_id','plan_id')else'f'*64}
            with self.subTest(field=field),self.assertRaises(ServiceError):reader.plan_history_migration_status(f.p['plan_id'],changed)
        for extra in ({'authority':cap},{'authorize':lambda:None},{'readers_compatible':lambda _:True}):
            with self.subTest(extra=list(extra)),self.assertRaises(TypeError):reader.plan_history_migration_status(f.p['plan_id'],command,**extra)
        with self.assertRaises(ServiceError):reader.plan_history_migration_status('OTHER-PLAN',command)
        f.store.plan_history_recovery_authority=cap
        with self.assertRaises(ServiceError):f.store.plan_history_migration_status(f.p['plan_id'],command)
        self.assertEqual(f.store.path.read_bytes(),before)

    def test_reader_unavailable_does_not_block_facts_or_authorize_migration(self):
        from hub.workbench_store import digest
        f=self.fixture();old=copy.deepcopy(f.p);target=f.target();f.bind(target,digest(old))
        f.store.reconcile_plan(target,expected_sha256=digest(old),request_id='legacy-v1',authority=f.cap)
        calls=[];answer=[False]
        def proof(observed):
            calls.append(copy.deepcopy(observed))
            if isinstance(answer[0],Exception):raise answer[0]
            return answer[0]
        command,cap,reader=self.bind(f,readers=proof)
        def query_with_unavailable_readers(state):
            for value in (False,None,1,{'verified':True},OSError('synthetic reader observation unavailable')):
                answer[0]=value;calls.clear()
                with self.subTest(state=state,reader=type(value).__name__):self.status(reader,command,state)
                self.assertEqual(calls,[]) # A callback failure cannot be swallowed: it must never be called.
        query_with_unavailable_readers('legacy_preimage_intact')
        for value in (False,None):
            answer[0]=value;calls.clear();before=f.store.path.read_bytes()
            with self.subTest(migration_reader=value),self.assertRaises(ServiceError)as error:self.migrate(f,command,cap)
            self.assertEqual(error.exception.code,'PLAN_HISTORY_READERS_UNVERIFIED')
            self.assertTrue(calls);self.assertTrue(all(c==command for c in calls))
            self.assertEqual(f.store.path.read_bytes(),before)
        # The same capability still rejects a migration if its reader witness
        # is revoked during the real isolated transaction, including commit.
        for phase in ('before_plan_history_write','before_plan_history_commit','before_commit'):
            answer[0]=True;before=f.store.path.read_bytes()
            def revoke(where):
                if where==phase:answer[0]=False
            f.store.fault=revoke
            with self.subTest(phase=phase),self.assertRaises(ServiceError)as error:self.migrate(f,command,cap)
            self.assertEqual(error.exception.code,'PLAN_HISTORY_READERS_UNVERIFIED')
            self.assertEqual(f.store.path.read_bytes(),before)
        f.store.fault=lambda _:None;answer[0]=True
        self.migrate(f,command,cap)
        query_with_unavailable_readers('migration_committed')
        old=copy.deepcopy(f.p);target={**copy.deepcopy(old),'revision':'later-valid'};f.bind(target,digest(old))
        f.store.reconcile_plan(target,expected_sha256=digest(old),request_id='later-valid',authority=f.cap)
        query_with_unavailable_readers('migration_committed')
        with f.store._connection(True)as db:
            value=f.store._decode(*db.execute('SELECT body,hash FROM plan_inputs').fetchone())
            value['migration']['sha256']='f'*64;f.store._replace_plan_input(db,f.p['plan_id'],value)
        query_with_unavailable_readers('requires_reconcile')
        self.assertEqual(f.store.tasks(),[])

    def test_both_authorizers_remain_live_and_errors_are_not_reconcile_results(self):
        from hub.plan_admission import PlanAuthority
        f=self.fixture();before=f.store.path.read_bytes()
        counts=[0]
        def revoked():
            counts[0]+=1
            if counts[0]>=3:raise ServiceError('SYNTHETIC_AUTH_REVOKED',status=403)
        command,cap,reader=self.bind(f,authorize=revoked)
        with self.assertRaises(ServiceError)as error:reader.plan_history_migration_status(f.p['plan_id'],command)
        self.assertEqual(error.exception.code,'SYNTHETIC_AUTH_REVOKED')
        self.assertEqual(f.store.path.read_bytes(),before)
        def plan_revoked():raise ServiceError('SYNTHETIC_PLAN_AUTH_REVOKED',status=403)
        f.cap=PlanAuthority.configured(f.cap.snapshot(),authorize=plan_revoked,
            controls=f.cap._controls,routes=f.cap._routes,package_for=f.cap._package)
        command,cap,reader=self.bind(f)
        with self.assertRaises(ServiceError)as error:reader.plan_history_migration_status(f.p['plan_id'],command)
        self.assertEqual(error.exception.code,'SYNTHETIC_PLAN_AUTH_REVOKED')
        self.assertEqual(f.store.path.read_bytes(),before)

    def test_valid_access_mismatched_or_damaged_rows_never_claim_committed(self):
        from hub.workbench_store import canonical,digest
        for case in ('receipt-request','receipt-digest','version','blob','row-hash','json','budget','source','foreign-plan','missing'):
            with self.subTest(case=case):
                f=self.fixture();command,cap,reader=self.bind(f);self.migrate(f,command,cap)
                with f.store._connection(True)as db:
                    value=f.store._decode(*db.execute('SELECT body,hash FROM plan_inputs').fetchone())
                    if case=='receipt-request':
                        value['migration']['request']['request_id']='other-original-request'
                        value['migration']['sha256']=digest(value['migration']['request'])
                    elif case=='receipt-digest':value['migration']['sha256']='f'*64
                    elif case=='version':value['versions'][0]['sha256']='f'*64
                    elif case=='blob':next(iter(value['blobs'].values()))['data']='!invalid!'
                    elif case=='budget':value={'schema_version':'plan-dispatch-budget/1','plan_history':value,'dispatch_budget':{}}
                    elif case=='source':value={'schema_version':'plan-source-evidence/1','plan_history':value,'records':[{}]}
                    elif case=='foreign-plan':value={**f.p,'plan_id':'OTHER-PLAN'}
                    if case=='missing':db.execute('DELETE FROM plan_inputs')
                    else:
                        body='not JSON'if case=='json'else canonical(value).decode()
                        db.execute('UPDATE plan_inputs SET body=?,hash=?',(body,'f'*64 if case=='row-hash'else digest(value)))
                self.status(reader,command,'requires_reconcile')
                self.assertEqual(f.store.tasks(),[])

    def test_original_plan_absent_and_legacy_row_hash_mismatch_are_unknown(self):
        from hub.workbench_store import canonical,digest
        f=self.fixture();command,cap,reader=self.bind(f)
        altered={**copy.deepcopy(f.p),'revision':'other-version'}
        with f.store._connection(True)as db:f.store._replace_plan_input(db,f.p['plan_id'],altered)
        self.status(reader,command,'requires_reconcile')
        # A valid independently authorized migration for another version is
        # not evidence that our original request committed.
        f.bind(altered,None);other,other_cap,_=self.bind(f)
        self.migrate(f,other,other_cap);self.status(reader,command,'requires_reconcile')
        f=self.fixture();command,cap,reader=self.bind(f)
        with f.store._connection(True)as db:
            value={**f.p,'observation':'changed-row'}
            db.execute('UPDATE plan_inputs SET body=?,hash=?',(canonical(value).decode(),digest(value)))
        self.status(reader,command,'requires_reconcile')

    def test_storage_identity_and_io_errors_propagate_instead_of_reconcile(self):
        f=self.fixture();command,cap,reader=self.bind(f);before=f.store.path.read_bytes()
        saved=f.store.registration_path.read_bytes();f.store.registration_path.unlink()
        with self.assertRaises(ServiceError)as error:reader.plan_history_migration_status(f.p['plan_id'],command)
        self.assertEqual(error.exception.details['reason_code'],'REGISTRATION_MISSING')
        self.assertEqual(f.store.path.read_bytes(),before)
        f=self.fixture();command,cap,reader=self.bind(f)
        moved=f.store.path.with_name('original.sqlite3');f.store.path.rename(moved);shutil.copyfile(moved,f.store.path)
        before=f.store.path.read_bytes()
        with self.assertRaises(ServiceError)as error:reader.plan_history_migration_status(f.p['plan_id'],command)
        self.assertEqual(error.exception.details['reason_code'],'LEDGER_IDENTITY_CHANGED');self.assertEqual(f.store.path.read_bytes(),before)
        f=self.fixture();command,cap,reader=self.bind(f);before=f.store.path.read_bytes()
        denied=PermissionError('synthetic IO denial')
        with patch.object(reader,'_ledger_profile',side_effect=denied),self.assertRaises(PermissionError)as error:
            reader.plan_history_migration_status(f.p['plan_id'],command)
        self.assertIs(error.exception,denied);self.assertEqual(f.store.path.read_bytes(),before)
        # A physical SQLite error is outside the pure row-decoding boundary.
        f.store.path.write_bytes(b'not a SQLite database')
        with self.assertRaises(ServiceError)as error:reader.plan_history_migration_status(f.p['plan_id'],command)
        self.assertEqual(error.exception.code,'TASK_STORE_CORRUPT')
        self.assertEqual(f.store.path.read_bytes(),b'not a SQLite database')

    def test_source_budget_unknown_task_events_and_receipts_remain_exact(self):
        import test_hub_source_admission as sources
        import test_hub_dispatch_budget as budgets
        from hub.workbench_store import canonical,digest
        from hub.dispatch_budget import split
        from hub.source_admission import split as split_source
        f=sources.SourceAdmissionTests();f.setUp();self.addCleanup(f.doCleanups)
        receipt=f.f.evidence(sources.BASE);receipt['plan_sha256']=digest(f.p);f.records[sources.BASE]=receipt;f.configure()
        f.store.record_plan_evidence(f.p['plan_id'],receipt,authority=f.cap);f.record(f.candidate(status='unknown'))
        b=budgets.DispatchBudgetTests();b.store=f.store;b.p=f.p;b.plan_cap=f.cap;b.control=f.control
        b.policy={'schema_version':'1.0','lineage_id':f.p['plan_id'],'source_state_sha256':'a'*64,'cutoff_id':'synthetic-cutoff','covered_dispatch_ids':['covered-call'],'observed_model_dispatches':1,'historical_unknown_allowance':8,'closure_reserve':12,'conservative_deterministic_dispatches':0,'limit':480,'provenance_ref':'synthetic/state-cutoff.json'}
        b.initialize();dispatch=b.req();b.reserve(dispatch);b.close(dispatch,outcome='unknown',resources_closed=False)
        f.store.admit({'request_id':'retained-task','project_id':'synthetic','parent_task_id':None},{'fixture':'unchanged'})
        f.store.event('retained-task','unchanged-event',{'method':'synthetic/observation'})
        def retained():
            with f.store._connection()as db:
                raw=f.store._decode(*db.execute('SELECT body,hash FROM plan_inputs').fetchone())
                tables={t:[tuple(r)for r in db.execute('SELECT *FROM '+t)]for t in ('tasks','events','plan_receipts','plan_conflicts')}
            history,budget=split(raw);_,source=split_source(history)
            return tables,canonical(budget),canonical(source)
        before=retained();command,cap,reader=self.bind(f)
        self.status(reader,command,'legacy_preimage_intact');self.migrate(f,command,cap)
        self.status(reader,command,'migration_committed');self.assertEqual(retained(),before)
        self.assertNotIn(sources.GRAPH,f.view()['ready']);self.assertEqual(b.status()['held_dispatch_ids'],[dispatch['dispatch_id']])
