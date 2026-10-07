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
