"""Synthetic local RR gateway fixtures; no installed identities or network."""
import base64
import copy
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.rr_auth import GatewayAuth, GatewayDenied, canonical
from hub.rr_service import RRApplication, UnixRRServer, RequestError, parse_multipart

DEVICE='a'*64
KEY=b'synthetic-public-fixture-key-only!'
ORIGIN='https://review.example.test'


class MemoryStore:
    """Transport-only oracle; real SQLite/atomic cases live in store tests."""
    def __init__(self):
        self.saved={}
    def submit(self,device,document,attachments,guard=None):
        from hub.rr_payload import validate_submission,RRFeedbackError
        validate_submission(document,attachments,'personal-control-hub')
        guard()
        ident=document['client_request_id'];key=(device,ident)
        if key in self.saved:
            if self.saved[key][0]!=document:
                raise RRFeedbackError(409,'RR_CONFLICT')
            return 200,self.saved[key][1]
        receipt={'feedback_id':'f1','revision':1}
        self.saved[key]=(copy.deepcopy(document),receipt,attachments)
        return 201,receipt
    def by_request(self,device,ident):
        if (device,ident) not in self.saved:
            raise RequestError(404,'RR_NOT_FOUND')
        return self.saved[device,ident][1]
    def get(self,device,ident):
        for (owner,_),value in self.saved.items():
            if owner==device and ident==value[1]['feedback_id']:
                return value[0]
        raise RequestError(404,'RR_NOT_FOUND')
    def attachment(self,device,ident,aid):
        self.get(device,ident)
        for (owner,_),value in self.saved.items():
            for item in value[2]:
                if owner==device and item['attachment_id']==aid:
                    return item
        raise RequestError(404,'RR_NOT_FOUND')


class RRServiceTests(unittest.TestCase):
    def setUp(self):
        self.auth=GatewayAuth(KEY,{DEVICE:'personal-control-hub'},'personal-control-hub',ORIGIN,os.getuid(),os.getuid(),fixture=True,clock=lambda:1000)
        self.temp=tempfile.TemporaryDirectory(dir=str(Path(__file__).resolve().parents[1]/'..'),prefix='rr-service-')
        self.addCleanup(self.temp.cleanup)
        path=Path(self.temp.name)/'icon.svg';path.write_bytes(b'<svg/>')
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        self.resource={'id':'icon','sha256':sha,'mode':'readonly','preview_version':sha}
        self.manifest={'schema':1,'project_id':'personal-control-hub','source':{'commit':'b'*40,'build_id':'c'*64,'dirty':True},'resources':[self.resource],'page_paths':['/']}
        self.store=MemoryStore();self.app=RRApplication(self.store,self.auth,self.manifest,{'icon':path})
    def assertion(self,method,path,body=b'',**changes):
        value={'v':1,'challenge':self.auth.challenge,'device_sha256':DEVICE,'project_id':'personal-control-hub','origin':ORIGIN,'method':method,'path':path,'body_sha256':hashlib.sha256(body).hexdigest(),'nonce':str(uuid.uuid4()),'issued_at':1000,'expires':1030}
        value.update(changes);value['mac']=hmac.new(KEY,canonical(value),hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(canonical(value)).decode().rstrip('=')
    def call(self,method,path,body=b'',content_type=None,**changes):
        headers={'origin':ORIGIN,'x-pch-rr-auth':self.assertion(method,path,body,**changes)}
        if content_type:headers['content-type']=content_type
        return self.app.dispatch(method,path,headers,body,os.getuid())
    def document(self):
        data=b'fixture text'
        return {'schema':1,'client_request_id':str(uuid.uuid4()),'project_id':'personal-control-hub','page_path':'/','source':self.manifest['source'],'resource':self.resource,'description':'Synthetic review','expected':'expected','actual':'actual','steps':['read'],'submitted_at':'2026-10-19T00:00:00Z','attachments':[{'attachment_id':'a1','size':len(data),'sha256':hashlib.sha256(data).hexdigest()}]}
    def multipart(self,document):
        return (b'--fixture\r\nContent-Disposition: form-data; name="feedback"\r\nContent-Type: application/json; charset=utf-8\r\n\r\n'+canonical(document)+b'\r\n--fixture\r\nContent-Disposition: form-data; name="attachment.0"; filename="note.txt"\r\nContent-Type: text/plain\r\n\r\nfixture text\r\n--fixture--\r\n')
    def test_auth_identity_signature_replay_time_origin_and_body(self):
        path='/__rr/v1/manifest';token=self.assertion('GET',path)
        self.assertEqual(self.auth.verify(os.getuid(),'GET',path,b'',token),DEVICE)
        with self.assertRaises(GatewayDenied):self.auth.verify(os.getuid(),'GET',path,b'',token)
        for change in ({'project_id':'other'},{'device_sha256':'d'*64},{'origin':'https://evil.test'},{'issued_at':990},{'expires':1061},{'v':True}):
            with self.subTest(change=change),self.assertRaises(GatewayDenied):
                self.auth.verify(os.getuid(),'GET',path,b'',self.assertion('GET',path,**change))
        with self.assertRaises(GatewayDenied):self.auth.verify(os.getuid()+1,'GET',path,b'',self.assertion('GET',path))
        with self.assertRaises(GatewayDenied):self.auth.verify(os.getuid(),'GET',path,b'x',self.assertion('GET',path))
        with self.assertRaises(GatewayDenied):self.auth.verify(os.getuid(),'GET',path,b'','untrusted')
        with self.assertRaises(ValueError):GatewayAuth(KEY,{DEVICE:'personal-control-hub'},'personal-control-hub',ORIGIN,os.getuid(),os.getuid())
    def test_strict_multipart_and_transport_idempotency_attachment(self):
        doc=self.document();body=self.multipart(doc);ctype='multipart/form-data; boundary=fixture'
        parsed,attachments=parse_multipart(ctype,body.replace(b'Content-Type: text/plain',b'Content-Type: text/plain; charset=utf-8'))
        self.assertEqual(parsed,doc);self.assertEqual(attachments[0]['data'],b'fixture text');self.assertEqual(attachments[0]['content_type'],'text/plain')
        self.assertEqual(self.call('POST','/__rr/v1/feedback',body,ctype)[0],201)
        self.assertEqual(self.call('POST','/__rr/v1/feedback',body,ctype)[0],200)
        doc['description']='changed'
        with self.assertRaises(Exception) as caught:self.call('POST','/__rr/v1/feedback',self.multipart(doc),ctype)
        self.assertEqual(caught.exception.status,409)
        self.assertEqual(self.call('GET','/__rr/v1/feedback/by-request/'+doc['client_request_id'])[1]['feedback_id'],'f1')
        self.assertEqual(self.call('GET','/__rr/v1/feedback/f1/attachments/a1')[1],b'fixture text')
        for changed in (body.replace(b'name="feedback"',b'name="unknown"'),body.replace(b'Content-Type: text/plain',b'Content-Type: application/octet-stream'),body.replace(b'filename="note.txt"',b'filename="../note.txt"')):
            with self.assertRaises(RequestError):parse_multipart(ctype,changed)
        with self.assertRaises(RequestError):parse_multipart('text/plain',body)
    def test_multipart_malformed_json_mime_and_lengths(self):
        from hub.rr_payload import RRFeedbackError
        doc=self.document();body=self.multipart(doc);ctype='multipart/form-data; boundary=fixture'
        for changed in (body.replace(b'"schema":1',b'"schema":1,"schema":1'),body.replace(b'Synthetic review',b'\xff'),body.replace(b'"schema":1',b'"schema":1.0')):
            with self.assertRaises(RRFeedbackError):parse_multipart(ctype,changed)
        with self.assertRaises(RequestError) as caught:parse_multipart(ctype,body.replace(b'fixture text',b'x'*1048577))
        self.assertEqual(caught.exception.status,413)

    def test_utf8_display_filename_without_header_or_path_authority(self):
        body=self.multipart(self.document()).replace(b'filename="note.txt"', 'filename="截图说明.txt"'.encode())
        ctype='multipart/form-data; boundary=fixture'
        _,parts=parse_multipart(ctype,body)
        self.assertEqual(parts[0]['filename'],'截图说明.txt')
        self.assertEqual(self.call('POST','/__rr/v1/feedback',body,ctype)[0],201)
        for bad in (b'bad\xff.txt',b'bad\x7f.txt','bad\u0085.txt'.encode(),b'../note.txt',b'a\\b.txt'):
            with self.subTest(filename=bad),self.assertRaises(RequestError):
                parse_multipart(ctype,body.replace('截图说明.txt'.encode(),bad))
        for bad in (body.replace(b'name="attachment.0"','name="附件.0"'.encode()),
                    body.replace(b'Content-Type: text/plain','Content-Type: 文本'.encode()),
                    body.replace('filename="截图说明.txt"'.encode(),'filename="截图说明.txt"; unknown="x"'.encode())):
            with self.assertRaises(RequestError):parse_multipart(ctype,bad)
        with self.assertRaises(RequestError) as caught:parse_multipart(ctype,body.replace(b'Content-Type: text/plain',b'Content-Type: application/executable'))
        self.assertEqual(caught.exception.status,415)
        with self.assertRaises(RequestError) as caught:parse_multipart(ctype,body.replace(b'"description":"Synthetic review"',b'"description":"'+b'x'*16384+b'"'))
        self.assertEqual(caught.exception.status,413)

    def test_allowlist_and_source_binding(self):
        for path in ('/api/projects','/__rr/v1/feedback/f1/ack','/__rr/v1/feedback/f1/result','/__rr/v1/resources/../x','/__rr/v1/resources/%2e%2e','/__rr/v1/manifest?token=x'):
            with self.subTest(path=path),self.assertRaises(RequestError) as caught:self.call('GET',path)
            self.assertEqual(caught.exception.status,404)
        doc=self.document();doc['source']=dict(doc['source']);doc['source']['commit']='e'*40
        with self.assertRaises(RequestError) as caught:self.call('POST','/__rr/v1/feedback',self.multipart(doc),'multipart/form-data; boundary=fixture')
        self.assertEqual(caught.exception.status,409)
        self.assertEqual(self.call('GET','/__rr/v1/manifest')[0],200)
        invalid=self.document();invalid.pop('schema')
        with self.assertRaises(Exception) as caught:self.call('POST','/__rr/v1/feedback',self.multipart(invalid),'multipart/form-data; boundary=fixture')
        self.assertEqual(caught.exception.status,400)
    def test_one_serial_unix_fixture_http_bounds_and_cleanup(self):
        repo=Path(__file__).resolve().parents[1]
        common=Path(subprocess.check_output(['git','-C',str(repo),'rev-parse','--path-format=absolute','--git-common-dir'],text=True).strip())
        base=common.parent/'.venv';base.mkdir(exist_ok=True)
        directory=Path(tempfile.mkdtemp(dir=base,prefix='rr-fx-'));os.chmod(directory,0o700)
        path=directory/'s'
        server=UnixRRServer(path,self.app,fixture=True)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.01});thread.start()
        def request(headers):
            sock=socket.socket(socket.AF_UNIX);sock.settimeout(3);sock.connect(str(path))
            try:
                sock.sendall(b'GET /__rr/v1/manifest HTTP/1.0\r\n'+headers+b'\r\n')
                response=http.client.HTTPResponse(sock);response.begin();result=(response.status,response.read());response.close();return result
            finally:sock.close()
        try:
            self.assertEqual(request(b'Content-Length: 2129921\r\n')[0],413)
            self.assertEqual(request(b'Cookie: private\r\n')[0],400)
            self.assertEqual(request(b'Origin: a\r\nOrigin: b\r\n')[0],400)
            self.assertEqual(request(b'')[0],404)
            sock=socket.socket(socket.AF_UNIX);sock.settimeout(3);sock.connect(str(path))
            try:
                sock.sendall(b'GET /private-SYNTHETIC-SECRET HTTP/1.0 extra\r\n\r\n')
                response=http.client.HTTPResponse(sock);response.begin();raw=response.read()
                self.assertEqual(response.status,400);self.assertNotIn(b'SYNTHETIC',raw)
                self.assertEqual(response.getheader('Cache-Control'),'no-store');response.close()
            finally:sock.close()
            token=self.assertion('GET','/__rr/v1/manifest')
            self.assertEqual(request(('Origin: '+ORIGIN+'\r\nX-PCH-RR-Auth: '+token+'\r\n').encode())[0],200)
            original=self.app.dispatch
            self.app.dispatch=lambda *args: (_ for _ in ()).throw(OSError('sensitive fixture detail'))
            status,data=request(b'')
            self.assertEqual(status,503);self.assertNotIn(b'sensitive',data)
            self.app.dispatch=original
        finally:
            server.shutdown();thread.join(3);server.server_close();directory.rmdir()
        self.assertFalse(thread.is_alive());self.assertFalse(path.exists())

if __name__=='__main__':unittest.main()
