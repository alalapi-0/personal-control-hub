"""Targeted real-view projection, anonymous FD, same-process and native deltas."""
import base64
import array
import copy
import fcntl
import hashlib
import hmac
import json
import os
import socket
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

from hub.rr_auth import GatewayAuth,canonical
from hub.rr_binding import ORIGIN
from hub.rr_cli import local_context,agent_action
from hub.rr_feedback import RRFeedbackStore
from hub.rr_install import prepare,check,install,enable,rollback
from hub.rr_payload import RRFeedbackError,validate_submission,canonical_json
from hub.rr_read_model import RRReadModel
from hub.rr_runtime import consume_key_fd,request_key_fd,RRRuntime,attach_rr_gateway
from hub.rr_service import RRApplication,UnixRRServer,build_manifest,public_resources,RequestError
from hub.task_store import TaskStore

REPO=Path(__file__).resolve().parents[1]
KEY=b'public synthetic v2 fixture key!!'
DEVICE='a'*64


def sealed(raw=KEY):
    fd=os.memfd_create('pch-rr-fixture',os.MFD_ALLOW_SEALING|os.MFD_CLOEXEC);os.write(fd,raw)
    fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
    return fd


def project(ident='a',state='fresh'):
    fields={'current_work':{'objective':'实际目标 /home/private/path token=private-value','phase':'phase-one','round':'R1','status':'active',
                           'next_action':'继续当前工作','completed':False,'accepted':False},
            'progress':{'completed':2,'total':5,'counting_basis':'实际五项'},'blockers':[]}
    return {'project_id':ident,'name':'实际项目 '+ident,'declared':{'connection_read_allowed':True,'project_type':'creative'},
            'freshness':{'state':state,'authority_drift':False,'observed_at':'2026-10-19T00:00:00Z'},
            'definition':{'fields':{k:{'state':'known','value':v,'source':{'path':'/private/source'}}for k,v in
                                    [('purpose','实际创作项目'),('outputs',['候选','审核稿件']),('aliases',['实际别名'])]}},
            'business':{'state':'current' if state=='fresh'else'unknown','normalized_status':'active','fields':fields},
            'last_success_snapshot':{'fields':fields,'observed_at':'2026-10-18T00:00:00Z'},
            'source':{'path':'/private/source'},'errors':[{'message':'private-error'}]}


class Projects:
    def __init__(self,root):self.root=root;self.rows=[project(),project('b','stale'),project('c')]
    def list_projects(self,*args):return {'projects':copy.deepcopy(self.rows),'head':{'sequence':1,'hash':'f'*64}}
    def get_project(self,ident):return next(copy.deepcopy(x)for x in self.rows if x['project_id']==ident)


class V2Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=REPO);self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'data-root';self.root.mkdir(mode=0o700)
        self.task=TaskStore(self.root);self.task.register_storage();self.projects=Projects(self.root)
        self.source={'commit':'a'*40,'build_id':'b'*64,'dirty':False}
        self.model=RRReadModel(self.projects,self.task,['a','b'],self.source)

    def test_whitelist_uses_actual_store_and_never_exports_raw_messages_paths_or_other_projects(self):
        before=self.task.path.read_bytes()
        directory=self.model.directory();self.assertEqual(directory['data']['total'],2)
        self.assertEqual(directory['data']['projects'][0]['work']['state'],'current')
        self.assertEqual(directory['data']['projects'][1]['work']['state'],'historical')
        encoded=json.dumps(directory,ensure_ascii=False)
        for private in ('/home/private','private-value','/private/source','private-error'):
            self.assertNotIn(private,encoded)
        self.assertEqual(before,self.task.path.read_bytes())
        self.projects.rows[1]['last_success_snapshot']['fields']['blockers']=None
        self.projects.rows[1]['last_success_snapshot']['fields']['progress']=None
        self.assertEqual(self.model.project('b')['data']['work']['blockers'],[])
        with self.assertRaises(RRFeedbackError):self.model.project('c')
        self.task.admit({'request_id':'task-a','project_id':'a','parent_task_id':None,'kind':'ordinary_project','task_spec':'PRIVATE-TASK-MESSAGE'},
                        {'grant':{'root':str(self.root),'kind':'ordinary_project'},'thread_id':'PRIVATE-THREAD'})
        actual=self.model.tasks('a')['data']['tasks'];self.assertEqual(actual[0]['id'],'task-a')
        self.assertNotIn('PRIVATE',json.dumps(actual));self.assertNotIn('intent',actual[0]);self.assertNotIn('payload',actual[0])
        self.projects.rows[0]['declared']['connection_read_allowed']=False
        with self.assertRaises(RRFeedbackError):self.model.project('a')
        self.assertEqual(self.model.directory()['data']['total'],1)

    def test_real_view_assets_data_routes_and_context_feedback_are_explicitly_authenticated(self):
        resources=public_resources(REPO);manifest=build_manifest(REPO,resources,['/__rr/review/','/__rr/review/projects/a'])
        auth=GatewayAuth(KEY,{DEVICE:'personal-control-hub'},'personal-control-hub',ORIGIN,os.getuid(),os.getuid(),fixture=True,clock=lambda:1000)
        app=RRApplication(RRFeedbackStore(self.task),auth,manifest,resources,self.model)
        def call(path,method='GET',body=b'',media=None):
            claims={'v':1,'challenge':auth.challenge,'device_sha256':DEVICE,'project_id':'personal-control-hub','origin':ORIGIN,
                    'method':method,'path':path,'body_sha256':hashlib.sha256(body).hexdigest(),'nonce':str(uuid.uuid4()),'issued_at':1000,'expires':1030}
            claims['mac']=hmac.new(KEY,canonical(claims),hashlib.sha256).hexdigest()
            headers={'origin':ORIGIN,'x-pch-rr-auth':base64.urlsafe_b64encode(canonical(claims)).decode().rstrip('=')}
            if media:headers['content-type']=media
            return app.dispatch(method,path,headers,body,os.getuid())
        self.assertIn(b'<title>PCH',call('/__rr/review/')[1]);self.assertTrue(call('/__rr/v1/resources/review-script')[2].startswith('text/javascript'))
        self.assertEqual(call('/__rr/v1/data/projects')[1]['data']['total'],2)
        for path in ('/api/projects','/api/owner/login','/__rr-agent/v1/feedback','/__rr/v1/data/projects/c'):
            with self.assertRaises((RequestError,RRFeedbackError)):call(path)
        with self.assertRaises(RequestError):app.dispatch('GET','/__rr/review/',{},b'',os.getuid())
        envelope=call('/__rr/v1/data/projects/a')[1]
        doc={'schema':1,'client_request_id':str(uuid.uuid4()),'project_id':'personal-control-hub','page_path':'/__rr/review/projects/a',
             'source':manifest['source'],'resource':next(x for x in manifest['resources']if x['id']=='review-page'),
             'description':'实际界面反馈','expected':'expected','actual':'actual','steps':'read','submitted_at':'2026-10-19T00:00:00Z',
             'attachments':[],'context':{'data_revision':envelope['revision'],'observed_at':envelope['observed_at']}}
        body=b'--b\r\nContent-Disposition: form-data; name="feedback"\r\n\r\n'+canonical(doc)+b'\r\n--b--\r\n'
        code,receipt,_=call('/__rr/v1/feedback','POST',body,'multipart/form-data; boundary=b');self.assertEqual(code,201)
        saved=RRFeedbackStore(self.task).get(DEVICE,receipt['feedback_id']);self.assertEqual(saved['document']['context'],doc['context'])
        bad=copy.deepcopy(doc);bad['context']['data_revision']='not-a-digest'
        with self.assertRaises(RRFeedbackError):validate_submission(bad,[],'personal-control-hub')

    def test_only_sealed_anonymous_fds_are_consumed_and_closed(self):
        fd=sealed();self.assertEqual(consume_key_fd(fd),KEY)
        with self.assertRaises(OSError):os.fstat(fd)
        fd=os.memfd_create('unsealed');os.write(fd,KEY)
        with self.assertRaises(RRFeedbackError):consume_key_fd(fd)
        with self.assertRaises(OSError):os.fstat(fd)
        path=self.base/'ordinary-file';path.write_bytes(KEY);fd=os.open(path,os.O_RDONLY)
        with self.assertRaises(RRFeedbackError):consume_key_fd(fd)
        with self.assertRaises(OSError):os.fstat(fd)

    def test_same_process_attachment_shares_services_and_closes_exact_owned_thread(self):
        class Gateway:
            def __init__(this,path,app,**kwargs):this.app=app;this.stop=threading.Event();this.closed=False
            def serve_forever(this,**kwargs):this.stop.wait(3)
            def shutdown(this):this.stop.set()
            def server_close(this):this.closed=True
        server=mock.Mock();server.projects=self.projects;server.tasks.store=self.task;server.rr_runtime=None;closed=[]
        server.server_close=lambda:closed.append('web')
        binding={'browser_enabled':True,'review_project_ids':['a'],'devices':{DEVICE:'personal-control-hub'},'project_id':'personal-control-hub',
                 'origin':ORIGIN,'gateway_uid':os.getuid(),'gateway_gid':os.getgid(),'agent_uid':os.getuid()}
        original=GatewayAuth
        def auth(*args):return original(*args,fixture=True)
        with mock.patch('hub.rr_runtime.load_binding',return_value=(binding,self.task,{'commit':'a'*40,'dirty':False})),\
             mock.patch('hub.rr_runtime.CurrentGuard'),\
             mock.patch('hub.rr_runtime.browser_binding'),mock.patch('hub.rr_runtime.GatewayAuth',side_effect=auth),\
             mock.patch('hub.rr_runtime.UnixRRServer',side_effect=Gateway):
            runtime=attach_rr_gateway(server,code_root=REPO,binding_path='unused',key_fd=sealed())
        self.assertIs(runtime.gateway.app.store.task_store,self.task)
        self.assertIs(runtime.gateway.app.read_model.projects,self.projects)
        server.server_close();self.assertFalse(runtime.thread.is_alive());self.assertFalse(runtime.thread.daemon)
        self.assertTrue(runtime.gateway.closed);self.assertEqual(closed,['web'])

    def test_native_provider_transfers_exactly_one_sealed_fd_and_rejects_excess(self):
        common=Path(subprocess.check_output(['git','-C',str(REPO),'rev-parse','--path-format=absolute','--git-common-dir'],text=True).strip())
        base=common.parent/'.venv';base.mkdir(exist_ok=True)
        directory=Path(tempfile.mkdtemp(dir=base,prefix='rr-fx-'));directory.chmod(0o750);path=directory/'key'
        listener=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET);listener.bind(str(path));path.chmod(0o660);listener.listen(1);listener.settimeout(3)
        requests=[];errors=[]
        def provider():
            try:
                for count in (1,2):
                    channel,_=listener.accept()
                    with channel:
                        request=json.loads(channel.recv(1024));requests.append(request)
                        descriptors=[sealed() for _ in range(count)]
                        try:
                            reply=canonical({'schema':1,'project_id':'personal-control-hub','key_fd':True})+b'\n'
                            channel.sendmsg([reply],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',descriptors))])
                        finally:
                            for fd in descriptors:os.close(fd)
            except BaseException as error:errors.append(type(error).__name__)
        thread=threading.Thread(target=provider);thread.start()
        binding={'gateway_uid':os.getuid(),'gateway_gid':os.getgid(),'project_id':'personal-control-hub','agent_uid':os.getuid()}
        try:
            with mock.patch('hub.rr_runtime.KEY_PROVIDER',path):
                self.assertEqual(consume_key_fd(request_key_fd(binding)),KEY)
                with self.assertRaises(RRFeedbackError):request_key_fd(binding)
        finally:
            thread.join(4);listener.close();path.unlink();directory.rmdir()
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(len(requests),2)
        self.assertEqual(requests[0]['recipient_uid'],os.getuid())

    def test_setgid_browser_directory_inherits_protected_group_without_chown(self):
        common=Path(subprocess.check_output(['git','-C',str(REPO),'rev-parse','--path-format=absolute','--git-common-dir'],text=True).strip())
        directory=Path(tempfile.mkdtemp(dir=common.parent/'.venv',prefix='rr-fx-'));directory.chmod(0o2750);path=directory/'s'
        app=mock.Mock();app.auth.fixture=False
        try:
            with mock.patch('hub.rr_service.os.getegid',return_value=os.getgid()+1),mock.patch('hub.rr_service.os.chown')as chown:
                gateway=UnixRRServer(path,app,socket_gid=os.getgid())
                self.assertEqual(path.stat().st_gid,os.getgid());chown.assert_not_called();gateway.server_close()
        finally:
            path.unlink(missing_ok=True);directory.rmdir()

    def test_incomplete_http_headers_cannot_hold_runtime_close_forever(self):
        common=Path(subprocess.check_output(['git','-C',str(REPO),'rev-parse','--path-format=absolute','--git-common-dir'],text=True).strip())
        directory=Path(tempfile.mkdtemp(dir=common.parent/'.venv',prefix='rr-fx-'));directory.chmod(0o700);path=directory/'s'
        app=mock.Mock();app.auth.fixture=True;gateway=UnixRRServer(path,app,fixture=True);runtime=RRRuntime(gateway);runtime.start()
        client=socket.socket(socket.AF_UNIX);client.connect(str(path));client.sendall(b'GET /__rr/v1/manifest HTTP/1.0\r\nOrigin:')
        started=time.monotonic()
        try:runtime.close()
        finally:
            client.close();gateway.server_close();directory.rmdir()
        self.assertLess(time.monotonic()-started,6);self.assertFalse(runtime.thread.is_alive());self.assertFalse(path.exists())

    def native(self,ready=False):
        home=self.base/'home';home.mkdir(exist_ok=True)
        (self.root/'.venv/pch-refresh/versions').mkdir(parents=True)
        output=self.base/'candidate'
        with mock.patch('pathlib.Path.home',return_value=home):
            plan=prepare(REPO,self.root,output,python=sys.executable,project_ids=['a'],
                         gateway_uid=os.getuid()+1 if ready else None,gateway_gid=os.getgid()+1 if ready else None,
                         devices={DEVICE:'personal-control-hub'}if ready else None,current_service_pid=os.getpid())
        return home,output,plan

    def test_native_prepare_is_offline_and_pins_source_unique_store_and_missing_inputs(self):
        before=self.task.path.read_bytes();home,out,plan=self.native()
        with mock.patch('pathlib.Path.home',return_value=home):self.assertEqual(check(out)['program_id'],plan['program_id'])
        self.assertEqual(before,self.task.path.read_bytes());self.assertFalse(Path(plan['destination']).exists())
        self.assertFalse(Path(plan['binding_path']).exists());self.assertFalse(Path(plan['dropin_path']).exists())
        self.assertEqual(set(plan['missing']),{'gateway_uid','gateway_gid','devices'})
        (out/'code/src/hub/rr_runtime.py').write_text('# tampered')
        with mock.patch('pathlib.Path.home',return_value=home),self.assertRaises(RRFeedbackError):check(out)

    def test_native_install_enable_rollback_fixture_preserves_same_feedback_and_offline_agent(self):
        home,out,plan=self.native(ready=True);provider=self.base/'provider';provider.touch()
        with mock.patch('pathlib.Path.home',return_value=home),mock.patch('hub.rr_binding.browser_binding'),\
             mock.patch('hub.rr_install.KEY_PROVIDER',provider),mock.patch('hub.rr_install._service')as service:
            install(out);enable(out)
            self.assertEqual(service.call_count,2)
            from test_hub_rr_feedback import document
            _,receipt=RRFeedbackStore(self.task).submit('device-a',document(),[])
            rollback(out);self.assertFalse(Path(plan['dropin_path']).exists())
            self.assertFalse(json.loads(Path(plan['binding_path']).read_bytes())['browser_enabled'])
            config,store=local_context(Path(plan['destination']))
            self.assertEqual(store.task_store.path,self.task.path)
            read=agent_action(plan['destination'],store,'read',feedback_id=receipt['feedback_id'])
            agent_action(plan['destination'],store,'ack',feedback_id=receipt['feedback_id'],proof=read['read_receipt'])
            self.assertEqual(store.get('device-a',receipt['feedback_id'])['state'],'agent_read')
            self.assertEqual(service.call_count,4)

    def test_native_installation_metadata_drift_is_rejected_before_install_or_service_effects(self):
        home,out,plan=self.native(ready=True);provider=self.base/'provider';provider.touch()
        metadata=out/'code/rr-installation.json';before=metadata.read_bytes()
        changed=json.loads(before);changed['data_root']='/wrong-root';metadata.write_bytes(canonical_json(changed))
        with mock.patch('pathlib.Path.home',return_value=home),mock.patch('hub.rr_binding.browser_binding'),\
             mock.patch('hub.rr_install.KEY_PROVIDER',provider),mock.patch('hub.rr_install._service')as service:
            with self.assertRaises(RRFeedbackError):check(out)
            with self.assertRaises(RRFeedbackError):install(out)
            self.assertFalse(Path(plan['destination']).exists())
            metadata.write_bytes(before);install(out)
            installed=Path(plan['destination'])/'rr-installation.json'
            installed.write_bytes(canonical_json(changed))
            with self.assertRaises(RRFeedbackError):enable(out)
            service.assert_not_called()
            self.assertFalse(Path(plan['binding_path']).exists())
            self.assertFalse(Path(plan['dropin_path']).exists())

if __name__=='__main__':unittest.main()
