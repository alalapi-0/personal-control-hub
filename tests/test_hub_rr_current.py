"""Current-process drift checks on disposable registered stores only."""
import base64
import copy
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import struct
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

from hub.rr_auth import GatewayAuth, canonical
from hub.rr_binding import load_binding, ORIGIN, KEY_PROVIDER
from hub.rr_feedback import RRFeedbackStore
from hub.rr_guard import CurrentGuard
from hub.rr_payload import canonical_json, RRFeedbackError
from hub.rr_service import RRApplication, _Handler
from hub.rr_runtime import attach_rr_gateway
from hub.task_store import TaskStore
from test_hub_rr_feedback import document

DEVICE='a'*64
KEY=b'public-current-guard-fixture-key!'


class CurrentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]);self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'business';self.root.mkdir(mode=0o700)
        self.store=TaskStore(self.root);self.store.register_storage()
        self.code=self.base/'code';(self.code/'src').mkdir(mode=0o700,parents=True)
        self.asset=self.code/'src/view.svg';self.asset.write_bytes(b'<svg/>');self.asset.chmod(0o644)
        self.binding_path=self.root/'data/rr-adapter.json'
        files={'src/view.svg':{'size':6,'sha256':self.sha(self.asset)}}
        program=hashlib.sha256(canonical_json(files)).hexdigest()
        source={'commit':'a'*40,'dirty':False}
        self.write(self.code/'rr-runtime-manifest.json',{'schema':1,'program_id':program,'files':files,'source':source})
        self.write(self.code/'rr-installation.json',{'schema':1,'project_id':'personal-control-hub','program_id':program,
                    'code_root':str(self.code),'data_root':str(self.root),'binding_path':str(self.binding_path)})
        binding={'schema':2,'project_id':'personal-control-hub','program_id':program,'code_root':str(self.code),
                 'data_root':str(self.root),'browser_enabled':True,'agent_uid':os.getuid(),'gateway_uid':os.getuid()+1,
                 'gateway_gid':os.getgid()+1,'origin':ORIGIN,'devices':{DEVICE:'personal-control-hub'},
                 'review_project_ids':['fixture'],'ledger_identity':[self.store.path.stat().st_dev,self.store.path.stat().st_ino],
                 'storage_registration_sha256':self.sha(self.store.registration_path),'key_provider_socket':str(KEY_PROVIDER)}
        self.write(self.binding_path,binding)
        self.server=SimpleNamespace(projects=SimpleNamespace(root=self.root),tasks=SimpleNamespace(store=self.store))
        self.guard=CurrentGuard(self.server,self.code,self.binding_path)
        loaded,_,_=load_binding(self.binding_path,code_root=self.code,store=self.store,reader=self.guard.read)
        self.guard.freeze(loaded)
        self.auth=GatewayAuth(KEY,{DEVICE:'personal-control-hub'},'personal-control-hub',ORIGIN,os.getuid(),os.getuid(),fixture=True,clock=lambda:1000)
        self.resource={'id':'pch-icon','sha256':self.sha(self.asset),'mode':'readonly','preview_version':self.sha(self.asset)}
        self.manifest={'schema':1,'project_id':'personal-control-hub','source':document()['source'],
                       'resources':[self.resource],'page_paths':['/review']}
        self.app=RRApplication(RRFeedbackStore(self.store),self.auth,self.manifest,{'pch-icon':self.asset},current_guard=self.guard.check)

    @staticmethod
    def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def write(path,value):path.write_bytes(canonical_json(value));path.chmod(0o600)

    def headers(self,method,path,body):
        value={'v':1,'challenge':self.auth.challenge,'device_sha256':DEVICE,'project_id':'personal-control-hub',
               'origin':ORIGIN,'method':method,'path':path,'body_sha256':hashlib.sha256(body).hexdigest(),
               'nonce':str(uuid.uuid4()),'issued_at':1000,'expires':1030}
        value['mac']=hmac.new(KEY,canonical(value),hashlib.sha256).hexdigest()
        return {'origin':ORIGIN,'x-pch-rr-auth':base64.urlsafe_b64encode(canonical(value)).decode().rstrip('=')}

    def call(self,method='GET',path='/__rr/v1/manifest',body=b''):
        headers=self.headers(method,path,body)
        if method=='POST':headers['content-type']='multipart/form-data; boundary=fixture'
        return self.app.dispatch(method,path,headers,body,os.getuid())

    def submit_body(self):
        doc=document();doc['resource']=self.resource
        body=b'--fixture\r\nContent-Disposition: form-data; name="feedback"\r\n\r\n'+canonical_json(doc)+b'\r\n--fixture--\r\n'
        return doc,body

    def reject(self):
        with self.assertRaises(RRFeedbackError)as error:self.call()
        self.assertEqual(error.exception.code,'RR_CURRENT_BINDING_CHANGED')

    def test_verified_metadata_hot_path_has_no_file_reads_hashes_or_subprocesses(self):
        with mock.patch('hub.rr_cli._regular',side_effect=AssertionError('unexpected content read')),\
             mock.patch('hashlib.sha256',side_effect=AssertionError('unexpected rehash')),\
             mock.patch('subprocess.run',side_effect=AssertionError('unexpected process')):
            self.guard.check();self.guard.check()

    def test_normal_registered_feedback_writes_remain_valid(self):
        before=self.sha(self.store.path);doc,body=self.submit_body()
        self.assertEqual(self.call('POST','/__rr/v1/feedback',body)[0],201)
        self.assertNotEqual(self.sha(self.store.path),before)
        self.guard.check();self.assertEqual(self.call()[0],200)

    def test_business_root_replacement_is_rejected_and_latched(self):
        old=self.base/'old-business';self.root.rename(old);self.root.mkdir(mode=0o700)
        self.reject();self.root.rmdir();old.rename(self.root);self.reject()

    def test_same_bytes_replaced_ledger_is_rejected(self):
        old=self.store.path.with_suffix('.old');self.store.path.rename(old);shutil.copyfile(old,self.store.path);self.store.path.chmod(0o600)
        self.reject()

    def test_registration_replacement_and_runtime_inplace_change_are_rejected(self):
        raw=self.store.registration_path.read_bytes();self.store.registration_path.unlink();self.store.registration_path.write_bytes(raw);self.store.registration_path.chmod(0o600)
        self.reject()

    def test_runtime_change_with_same_size_and_restored_mtime_is_rejected(self):
        before=self.asset.stat();self.asset.write_bytes(b'<SVG/>');os.utime(self.asset,ns=(before.st_atime_ns,before.st_mtime_ns))
        self.assertEqual(self.asset.stat().st_size,before.st_size);self.reject()

    def test_service_object_rebinding_is_rejected(self):
        self.server.tasks.store=TaskStore(self.root);self.reject()

    def test_registration_drift_at_commit_rolls_back_every_feedback_effect(self):
        before=self.store.path.read_bytes();doc,body=self.submit_body()
        def fault(stage):
            if stage=='rr_before_commit':
                old=self.store.registration_path;raw=old.read_bytes();old.unlink();old.write_bytes(raw);old.chmod(0o600)
        self.store.fault=fault
        with self.assertRaises(RRFeedbackError):self.call('POST','/__rr/v1/feedback',body)
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertEqual(RRFeedbackStore(self.store).pending()['items'],[])

    def test_last_store_before_commit_hook_cannot_bypass_runtime_guard(self):
        before=self.store.path.read_bytes();doc,body=self.submit_body()
        self.store.fault=lambda stage:self.asset.write_bytes(b'<SVG/>')if stage=='before_commit'else None
        with self.assertRaises(RRFeedbackError):self.call('POST','/__rr/v1/feedback',body)
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_chunked_return_stops_after_drift_without_sending_later_bytes(self):
        class Output(io.BytesIO):
            def write(stream,value):
                result=super().write(value);self.asset.write_bytes(b'<SVG/>');return result
        output=Output();handler=SimpleNamespace(server=SimpleNamespace(app=self.app),wfile=output)
        with self.assertRaises(RRFeedbackError):_Handler._write_body(handler,b'x'*150000,True)
        self.assertEqual(len(output.getvalue()),65536)

    def handler(self,app,path,output):
        headers=self.headers('GET',path,b'');status=[];sent={}
        handler=SimpleNamespace(server=SimpleNamespace(app=app),connection=mock.Mock(),headers=headers,
                                rfile=io.BytesIO(),wfile=output,command='GET',path=path,close_connection=False)
        handler.connection.getsockopt.return_value=struct.pack('3i',os.getpid(),os.getuid(),os.getgid())
        handler.send_response=status.append;handler.send_header=lambda k,v:sent.update({k:v});handler.end_headers=lambda:None
        handler._write_body=lambda data,guarded:_Handler._write_body(handler,data,guarded)
        return handler,status,sent

    def test_preheader_drift_returns_only_fixed_503(self):
        def deny():raise RRFeedbackError(503,'RR_CURRENT_BINDING_CHANGED')
        app=SimpleNamespace(dispatch=lambda *args:(200,b'private-fixture','application/octet-stream'),check_current=deny)
        output=io.BytesIO();handler,status,_=self.handler(app,'/__rr/v1/manifest',output)
        _Handler._run(handler);self.assertEqual(status,[503]);self.assertNotIn(b'private-fixture',output.getvalue())
        self.assertEqual(json.loads(output.getvalue()),{'code':'RR_CURRENT_BINDING_CHANGED'})

    def test_http_partial_response_is_closed_when_next_chunk_guard_fails(self):
        class Output(io.BytesIO):
            def write(stream,value):
                result=super().write(value);self.asset.write_bytes(b'<SVG/>');return result
        self.app.resources['pch-icon']=b'x'*150000
        output=Output();handler,status,sent=self.handler(self.app,'/__rr/v1/resources/pch-icon',output)
        _Handler._run(handler);self.assertEqual(status,[200]);self.assertEqual(sent['Content-Length'],'150000')
        self.assertTrue(handler.close_connection);self.assertEqual(len(output.getvalue()),65536)

    def test_attachment_installs_real_current_guard_on_exact_server_objects(self):
        class Gateway:
            def __init__(this,path,app,**kw):this.app=app;this.stop=threading.Event()
            def serve_forever(this,**kw):this.stop.wait(3)
            def shutdown(this):this.stop.set()
            def server_close(this):pass
        from test_hub_rr_v2 import sealed
        self.server.rr_runtime=None;self.server.server_close=lambda:None
        with mock.patch('hub.rr_runtime.browser_binding'),mock.patch('hub.rr_runtime.GatewayAuth',return_value=self.auth),\
             mock.patch('hub.rr_runtime.public_resources',return_value={'pch-icon':self.asset}),\
             mock.patch('hub.rr_runtime.build_manifest',return_value=self.manifest),mock.patch('hub.rr_runtime.UnixRRServer',side_effect=Gateway):
            runtime=attach_rr_gateway(self.server,code_root=self.code,binding_path=self.binding_path,key_fd=sealed())
        try:
            current=runtime.gateway.app.current_guard.__self__
            self.assertIs(current.store,self.store);self.assertIs(current.projects,self.server.projects)
            runtime.gateway.app.check_current();self.asset.write_bytes(b'<SVG/>')
            with self.assertRaises(RRFeedbackError):runtime.gateway.app.check_current()
        finally:self.server.server_close()
        self.assertFalse(runtime.thread.is_alive())

    def test_nonfixture_application_cannot_dispatch_without_current_guard(self):
        self.app.current_guard=None;self.auth.fixture=False
        with self.assertRaises(RRFeedbackError)as error:self.call()
        self.assertEqual(error.exception.code,'RR_CURRENT_GUARD_REQUIRED')


if __name__=='__main__':unittest.main()
