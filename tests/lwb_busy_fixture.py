"""Disposable browser fixture. Provider requests and worker facts are MOCK."""
import hashlib
import json
import signal
import sys
import threading
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
import test_hub_task_runtime as fixture
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth

PROOF='Synthetic-fixture-owner-'+'a'*64
auth=OwnerAuth(provider=lambda:PROOF)
context=hashlib.sha256(b'hub-task-owner-v1'+auth.version).hexdigest()
f=fixture.TaskRuntimeTests();f.setUp()
f.helper.projects.list_projects=lambda **_: {'projects':[{'project_id':'fixture-project','name':'隔离请求测试'}]}
server=HubHTTPServer(f.helper.projects,f.helper.service.designs,owner_auth=auth,previews=f.helper.service,tasks=f.service)
control=f.control/'control.json';control.write_text('{"mode":"approval"}')
metadata=f.control/'browser.json';stop=threading.Event();sequence=0;current=None


def start_case(mode):
    global sequence,current
    sequence+=1;ident=f'browser-task-{sequence}'
    grant={**f.grant,'grant_id':ident+'-grant','task_id':ident,'max_turn_seconds':30}
    payload={**f.payload,'request_id':ident,'grant_id':grant['grant_id']}
    f.store.register_grant(grant);f.service.submit(payload,owner_context=context)
    owner=ident+'-mock-owner';task=f.store.claim(owner);assert task['id']==ident
    f.store.update(ident,owner,thread_id=ident+'-thread',turn_id=ident+'-turn',provider_status='inProgress')
    kind='item/tool/requestUserInput' if mode=='input' else 'item/commandExecution/requestApproval'
    request=f.store.open_control(ident,owner,request_hash=hashlib.sha256(ident.encode()).hexdigest(),method=kind,
        thread_id=ident+'-thread',turn_id=ident+'-turn',expires_at=time.time()+(2 if mode=='expired' else 90))
    feedback=[]
    for index in (1,2):
        child=ident+f'-feedback-{index}';g={**grant,'grant_id':child+'-grant','task_id':child,'mode':'busy_feedback','parent_task_id':ident}
        p={**payload,'request_id':child,'grant_id':g['grant_id'],'mode':'busy_feedback','parent_task_id':ident}
        f.store.register_grant(g);f.service.submit(p,owner_context=context);feedback.append(child)
    current={'id':ident,'owner':owner,'request':request,'mode':mode,'feedback':feedback}
    metadata.write_text(json.dumps({'origin':server.origin,'root':str(f.control),'classification':'MOCK provider, actual Hub HTTP/SQLite',**current}))


def simulate_provider():
    seen='approval';start_case(seen)
    while not stop.wait(.1):
        mode=json.loads(control.read_text())['mode'];assert mode in {'approval','input','expired'}
        task=f.store.task(current['id'])
        if task['status'] in {'waiting_approval','waiting_input'}:
            f.store.heartbeat(current['id'],current['owner']);req=f.store.controls(current['id'])[0]
            reason='cancelled' if task['cancel'] else 'expired' if time.time()>=req['expires_at'] else None
            if req['version']==2 or reason:
                if reason:f.store.close_control(current['id'],current['owner'],current['request'],reason)
                f.store.update(current['id'],current['owner'],status='cancelled',provider_status='interrupted',
                    result={'error_class':'MOCK_REQUEST_CLOSED','effects_rolled_back':False,'mock_provider':True})
        if mode!=seen and f.store.task(current['id'])['status']=='cancelled':
            for ident in current['feedback']:f.store.cancel(ident)
            start_case(mode);seen=mode


thread=threading.Thread(target=server.serve_forever);thread.start()
worker=threading.Thread(target=simulate_provider);worker.start()
signal.signal(signal.SIGINT,lambda *_:stop.set());signal.signal(signal.SIGTERM,lambda *_:stop.set())
deadline=time.monotonic()+3
while not metadata.exists() and time.monotonic()<deadline:time.sleep(.01)
print(metadata.read_text(),flush=True)
stop.wait()
worker.join(3);server.shutdown();server.server_close();thread.join(3)
assert not worker.is_alive() and not thread.is_alive()
print(json.dumps({'classification':'MOCK','task_count':len(f.store.tasks()),'events':sum(len(f.store.events(t['id'])) for t in f.store.tasks()),
                  'actual_provider_calls':0,'root_removed_after_cleanup':str(f.control)}),flush=True)
f.doCleanups()
