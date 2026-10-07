"""Actual isolated HTTP/worker lifetime; provider and seeded states are MOCK."""
import argparse
import hashlib
import json
import multiprocessing
import signal
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
import test_hub_task_runtime as fixture
from test_hub_task_recovery import owned_process_worker
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth
from hub.service_contract import ServiceError
from hub.task_service import fingerprint


class OwnedHTTPServer(HubHTTPServer):
    allow_reuse_address=True  # Disposable candidate only; production setting unchanged.


def owned_guard():
    signal.signal(signal.SIGINT,signal.SIG_IGN)
    time.sleep(150)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--ui-only',action='store_true');args=parser.parse_args()
    f=fixture.TaskRuntimeTests();f.setUp()
    f.helper.projects.list_projects=lambda **_: {'projects':[{'project_id':'fixture-project','name':'隔离恢复测试 · 模拟模型'}]}
    auth=OwnerAuth(provider=lambda:'Recovery-fixture-owner-'+'c'*64)
    actor=hashlib.sha256(b'hub-task-owner-v1'+auth.version).hexdigest()
    f.store.register_grant({**f.grant,'version':2,'max_turn_seconds':120})
    f.payload['grant_version']=2;f.service.submit(f.payload,owner_context=actor)
    ctx=multiprocessing.get_context('fork');started,release=ctx.Event(),ctx.Event()
    worker=None if args.ui_only else ctx.Process(target=owned_process_worker,args=(str(f.control),str(f.workspace),started,release))
    guard=ctx.Process(target=owned_guard)
    if worker is not None:worker.start()
    guard.start()
    server=thread=None;stop=threading.Event();port=0;active=None;mode=None;sequence=0;snapshots=[];storage_faults=[]
    missing_backup=f.store.path.with_name('owned-original-tasks.sqlite3')
    root_backup=f.control.with_name('owned-original-root')
    control=f.control/'recovery-control.json';control.write_text('{"mode":"running"}')
    metadata=f.control/'recovery-browser.json'
    original_space=f.store.storage_guard.space
    signal.signal(signal.SIGINT,lambda *_:stop.set());signal.signal(signal.SIGTERM,lambda *_:stop.set())
    def start_server():
        nonlocal server,thread,port
        server=OwnedHTTPServer(f.helper.projects,f.helper.service.designs,port=port,owner_auth=auth,previews=f.helper.service,tasks=f.service)
        port=server.server_address[1];thread=threading.Thread(target=server.serve_forever);thread.start()
    def close_server():
        if server is not None:server.shutdown();server.server_close();thread.join(3);assert not thread.is_alive()
    def snapshot(label):
        value={'label':label,'worker_pid':worker.pid if worker else None,'worker_alive':worker.is_alive()if worker else False,'worker_exit':worker.exitcode if worker else None,
               'guard_pid':guard.pid,'guard_alive':guard.is_alive(),'backend_instance':server.backend_instance,
               'task_status':f.store.task('fixture-task')['status'],'events':len(f.store.events('fixture-task'))}
        snapshots.append(value);return value
    def scenario(kind):
        nonlocal active,sequence
        if active:
            old=f.store.task(active['id'])
            if old['status']in {'waiting_approval','waiting_input'}:
                f.store.close_control(active['id'],active['owner'],active['control'],'cancelled')
                f.store.update(active['id'],active['owner'],status='cancelled',provider_status='interrupted')
        sequence+=1;ident=f'recovery-mock-{kind}-{sequence}'
        grant={**f.grant,'grant_id':ident+'-grant','task_id':ident,'fingerprint':fingerprint(f.workspace)}
        payload={**f.payload,'request_id':ident,'grant_id':grant['grant_id'],'grant_version':1}
        f.store.register_grant(grant);f.service.submit(payload,owner_context=actor)
        owner=ident+'-owner'
        # Explicit MOCK state seeding only. No provider, writer, or resume is started.
        state={'approval':'running','input':'running','reconcile':'requires_reconcile','lost':'lost'}[kind]
        with f.store._connection(True)as c:
            c.execute('UPDATE tasks SET status=?,owner=?,lease_until=?,thread_id=?,turn_id=?,provider_status=? WHERE id=?',
                (state,owner,time.time()+15,ident+'-thread',ident+'-turn','inProgress',ident))
        active={'id':ident,'owner':owner,'classification':'MOCK imported state,0provider calls'}
        if kind in {'approval','input'}:
            active['control']=f.store.open_control(ident,owner,request_hash=hashlib.sha256(ident.encode()).hexdigest(),
                method='item/commandExecution/requestApproval'if kind=='approval'else'item/tool/requestUserInput',
                thread_id=ident+'-thread',turn_id=ident+'-turn',expires_at=time.time()+90)
        return ident
    try:
        if worker is not None:assert started.wait(3)
        start_server()
        def publish():
            info={'origin':server.origin,'root':str(f.control),'task_id':active['id']if active else'fixture-task',
                  'classification':'actual HTTP/SQLite/owned worker process; MOCK provider'if worker else'actual HTTP/SQLite; MOCK imported UI states, no executor/provider',
                  'worker_pid':worker.pid if worker else None,'guard_pid':guard.pid,'mode':mode,'port':port,'ui_only':args.ui_only}
            metadata.write_text(json.dumps(info));return info
        mode='approval'if args.ui_only else'running'
        if args.ui_only:scenario('approval')
        publish();snapshot('initial');print(metadata.read_text(),flush=True)
        deadline=time.monotonic()+145
        while not stop.wait(.1)and time.monotonic()<deadline:
            requested=json.loads(control.read_text())['mode']
            if requested!=mode:
                if requested=='restart':
                    snapshot('before_restart');close_server();start_server();snapshot('after_restart')
                elif requested=='release':
                    assert worker is not None;release.set();worker.join(5);assert worker.exitcode==0;snapshot('after_actual_mock_execution')
                elif requested=='low_space':f.store.storage_guard.space=lambda _:SimpleNamespace(f_bavail=0,f_frsize=4096)
                elif requested=='restore_space':f.store.storage_guard.space=original_space
                elif requested=='missing_ledger':
                    before=hashlib.sha256(f.store.path.read_bytes()).hexdigest();f.store.path.rename(missing_backup)
                    storage_faults.append({'action':'Root-owned fixture ledger rename','before_sha256':before,
                        'storage':f.store.storage_status(),'ledger_absent':not f.store.path.exists()})
                elif requested=='restore_ledger':
                    missing_backup.rename(f.store.path)
                    storage_faults.append({'action':'Root-owned exact fixture restoration','after_sha256':hashlib.sha256(f.store.path.read_bytes()).hexdigest(),'storage':f.store.storage_status()})
                elif requested=='replaced_root':
                    assert args.ui_only
                    before=hashlib.sha256(f.store.path.read_bytes()).hexdigest();f.control.rename(root_backup);shutil.copytree(root_backup,f.control)
                    with sqlite3.connect(f.store.path)as connection:connection.execute("UPDATE tasks SET status='checks_complete'")
                    storage_faults.append({'action':'Root-owned copied root with altered fake status','before_sha256':before,
                        'storage':f.store.storage_status(),'fake_record_not_read':f.service.list()['tasks_available'] is False})
                elif requested=='restore_root':
                    assert args.ui_only and root_backup.exists()
                    shutil.rmtree(f.control);root_backup.rename(f.control)
                    control.write_text(json.dumps({'mode':'restore_root'}))  # Do not replay the old fixture control file.
                    storage_faults.append({'action':'Root exact original fixture root restored','after_sha256':hashlib.sha256(f.store.path.read_bytes()).hexdigest(),'storage':f.store.storage_status()})
                elif requested=='stop':stop.set()
                elif requested in {'approval','input','reconcile','lost'}:scenario(requested)
                else:assert requested=='running'
                mode=requested;publish()
            if active:
                try:
                    if f.store.task(active['id'])['status']in {'waiting_input','waiting_approval'}:f.store.heartbeat(active['id'],active['owner'])
                except ServiceError:pass
    finally:
        release.set()
        if server is not None:close_server()
        processes=[p for p in (worker,guard)if p is not None]
        for process in processes:
            process.join(3)
            if process.is_alive():process.terminate();process.join(3)
            assert not process.is_alive()
        if root_backup.exists():shutil.rmtree(f.control);root_backup.rename(f.control)  # Exact owned fixture only.
        if missing_backup.exists():missing_backup.rename(f.store.path)  # Exact owned fixture only.
        summary={'root':str(f.control),'classification':'MOCK provider; actual local processes/HTTP/check',
                 'processes':[{'pid':p.pid,'exit':p.exitcode}for p in processes],'snapshots':snapshots,'storage_faults':storage_faults,
                 'effects':json.loads((f.control/'owned-process-effects.json').read_text())if worker else {'effects':0,'classification':'MOCK imported UI states; no execution'},
                 'tasks':[{'id':t['id'],'status':t['status']}for t in f.store.tasks()],
                 'database_sha256':hashlib.sha256(f.store.path.read_bytes()).hexdigest(),'new_model_turns':0,'backend_closed':True}
        print(json.dumps(summary),flush=True);f.doCleanups()


if __name__=='__main__':main()
