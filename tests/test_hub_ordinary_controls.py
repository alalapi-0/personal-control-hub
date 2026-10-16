"""Local ordinary controls, including generated provider protocol wire validation."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

from hub.codex_adapter import AppServerAdapter, INPUT_RESPONSE
from hub.service_contract import ServiceError
from hub.task_service import TaskService
from hub.task_worker import TaskWorker
from hub.workbench_store import digest
import test_hub_ordinary_worker as fixtures


def request():
    return {'id':99,'method':'item/tool/requestUserInput','params':{
        'threadId':'ordinary-thread','turnId':'ordinary-turn','itemId':'input-1','isBlocking':True,
        'questions':[{'id':'choice','header':'Scope','question':'Which summary?',
                      'options':[{'label':'Brief','description':'A short summary'},{'label':'Long','description':'A longer summary'}]}]}}

ANSWERS={'choice':{'answers':['Brief']}}

class OrdinaryControlTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.OrdinaryWorkerTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.store=self.f.store;self.store.admit(self.f.spec,self.f.intent)
        self.service=TaskService(None,None,self.store,ordinary_enabled=True,test_gate=False)
        self.store.claim('worker-owner')
        self.store.update('ordinary-task','worker-owner',thread_id='ordinary-thread',turn_id='ordinary-turn')
    def open(self,value=None):
        value=request()if value is None else value
        return self.store.open_control('ordinary-task','worker-owner',request_hash=digest(value),method=value['method'],
            thread_id='ordinary-thread',turn_id='ordinary-turn',expires_at=time.time()+10,provider_request=value)
    def respond(self,ident,answers=None,decision='answer',owner='synthetic-owner'):
        command={'control_id':ident,'version':1,'decision':decision}
        if decision=='answer':command['answers']=ANSWERS if answers is None else answers
        return self.service.respond_control('ordinary-task',command,owner_context=owner,authorize=lambda:None)
    def test_answer_owner_cas_and_public_redaction(self):
        ident=self.open()
        with self.assertRaises(ServiceError):self.respond(ident,owner='foreign')
        first=self.respond(ident);self.assertFalse(first['replayed'])
        self.assertTrue(self.respond(ident)['replayed'])
        with self.assertRaises(ServiceError):self.respond(ident,{'choice':{'answers':['Long']}})
        public=self.service.get('ordinary-task');self.assertEqual(public['controls'][0]['status'],'answered')
        self.assertNotIn('answers',public['controls'][0]);self.assertNotIn('answer_sha256',json.dumps(public))
        self.assertEqual(self.store.controls('ordinary-task')[0]['answers'],ANSWERS)
    def test_foreign_question_invalid_option_and_version_rejected(self):
        ident=self.open()
        for answer in ({'foreign':{'answers':['Brief']}},{'choice':{'answers':['invented']}},{'choice':{'answers':['Brief','Long']}}):
            with self.assertRaises(ServiceError):self.respond(ident,answer)
        with self.assertRaises(ServiceError):self.service.respond_control('ordinary-task',{'control_id':ident,'version':2,'decision':'answer','answers':ANSWERS},owner_context='synthetic-owner',authorize=lambda:None)
        self.assertEqual(self.store.controls('ordinary-task')[0]['version'],1)
    def test_secret_and_unknown_input_never_retain_text(self):
        value=request();value['params']['questions'][0].update(isSecret=True,question='SECRET BODY MUST NOT PERSIST')
        ident=self.open(value);control=self.store.controls('ordinary-task')[0]
        self.assertEqual(control['allowed_decisions'],['decline']);self.assertNotIn('questions',control)
        self.assertNotIn('SECRET BODY',json.dumps(self.store.events('ordinary-task')))
        self.assertEqual(self.service.get('ordinary-task')['controls'][0]['questions'],[])
        with self.assertRaises(ServiceError):self.respond(ident)
        self.respond(ident,decision='decline')
    def test_revoke_expiry_cancel_and_registration_fences(self):
        for mutation in ('revoke','expiry','cancel','registration','lease'):
            other=OrdinaryControlTests();other.setUp()
            try:
                ident=other.open()
                if mutation=='revoke':other.store.register_grant({**other.f.grant,'version':2})
                elif mutation=='cancel':other.service.cancel('ordinary-task',owner_context='synthetic-owner',authorize=lambda:None)
                elif mutation=='registration':other.f.registry_path.write_text('projects: []\n')
                if mutation in ('expiry','lease'):
                    offset=11 if mutation=='expiry'else 31
                    with patch('hub.task_store.time.time',return_value=time.time()+offset):
                        with self.assertRaises(ServiceError):other.respond(ident)
                else:
                    with self.assertRaises(ServiceError):other.respond(ident)
            finally:other.doCleanups()
    def test_cancel_disabled_test_gate_checks_owner_and_auth_but_can_stop_revoked_grant(self):
        with self.assertRaises(ServiceError):self.service.cancel('ordinary-task',owner_context='foreign',authorize=lambda:None)
        with self.assertRaises(ServiceError):self.service.cancel('ordinary-task',owner_context='synthetic-owner')
        self.store.register_grant({**self.f.grant,'version':2})
        self.service.ordinary_enabled=False
        with self.assertRaises(ServiceError):self.service.cancel('ordinary-task',owner_context='foreign',authorize=lambda:None)
        self.assertFalse(self.store.task('ordinary-task')['cancel'])
        self.service.cancel('ordinary-task',owner_context='synthetic-owner',authorize=lambda:None)
        self.assertTrue(self.store.task('ordinary-task')['cancel'])
    def test_plain_text_other_option_and_unknown_field_policy(self):
        from hub.task_inputs import input_policy, validate_answers
        value=request();question=value['params']['questions'][0]
        question.pop('options');policy=input_policy(value,'ordinary-thread','ordinary-turn')
        self.assertIn('answer',policy['decisions'])
        self.assertEqual(validate_answers(policy['questions'],{'choice':{'answers':['Free text']}})['choice']['answers'],['Free text'])
        question['options']=[{'label':'Brief','description':'Short'}];question['isOther']=True
        policy=input_policy(value,'ordinary-thread','ordinary-turn')
        validate_answers(policy['questions'],{'choice':{'answers':['Something else']}})
        question['unknown']='do not retain'
        self.assertNotIn('questions',input_policy(value,'ordinary-thread','ordinary-turn'))
    def test_store_cancel_without_authority_rejected(self):
        with self.assertRaises(ServiceError):self.store.cancel('ordinary-task')
        self.assertFalse(self.store.task('ordinary-task')['cancel'])
    def test_store_cancel_owner_without_auth_rejected_and_authenticated_owner_succeeds(self):
        with self.assertRaises(ServiceError):self.store.cancel('ordinary-task',owner_context='synthetic-owner')
        self.assertFalse(self.store.task('ordinary-task')['cancel'])
        self.store.register_grant({**self.f.grant,'version':2})
        self.store.cancel('ordinary-task',owner_context='synthetic-owner',authorize=lambda:None)
        self.assertTrue(self.store.task('ordinary-task')['cancel'])
    def test_cancel_worker_identity_and_lease(self):
        with self.assertRaises(ServiceError):self.store.cancel('ordinary-task',worker_owner='foreign-worker')
        with patch('hub.task_store.time.time',return_value=time.time()+20):
            with self.assertRaises(ServiceError):self.store.cancel('ordinary-task',worker_owner='worker-owner')
        self.assertFalse(self.store.task('ordinary-task')['cancel'])
        self.store.cancel('ordinary-task',worker_owner='worker-owner')
        self.assertTrue(self.store.task('ordinary-task')['cancel'])
    def test_auth_revoked_before_commit_rolls_back(self):
        ident=self.open();valid=[True]
        def authorize():
            if not valid[0]:raise ServiceError('OWNER_AUTH_FAILED',status=403)
        self.store.fault=lambda phase:valid.__setitem__(0,False)if phase=='before_commit'else None
        with self.assertRaises(ServiceError):self.service.respond_control('ordinary-task',{'control_id':ident,'version':1,'decision':'answer','answers':ANSWERS},owner_context='synthetic-owner',authorize=authorize)
        self.assertEqual(self.store.controls('ordinary-task')[0]['version'],1)

class WorkerInputTests(unittest.TestCase):
    def test_worker_waits_for_local_answer_and_returns_exact_wire(self):
        f=fixtures.OrdinaryWorkerTests();f.setUp();self.addCleanup(f.doCleanups)
        base=f.Adapter;result=[]
        class Adapter(base):
            def poll(self,timeout):
                if not getattr(self,'asked',False):
                    self.asked=True
                    result.append(self.request_handler(request()))
                return super().poll(timeout)
        f.store.admit(f.spec,f.intent)
        worker=TaskWorker(f.store,adapter_factory=Adapter,writer_check=lambda _:True)
        service=TaskService(None,None,f.store,ordinary_enabled=True,test_gate=False)
        thread=threading.Thread(target=worker.run_one);thread.start()
        try:
            deadline=time.monotonic()+3
            while not f.store.controls('ordinary-task')and time.monotonic()<deadline:time.sleep(.01)
            controls=f.store.controls('ordinary-task');self.assertTrue(controls)
            service.respond_control('ordinary-task',{'control_id':controls[0]['id'],'version':1,'decision':'answer','answers':ANSWERS},owner_context='synthetic-owner',authorize=lambda:None)
        finally:
            thread.join(timeout=5)
            if thread.is_alive():
                f.store.cancel('ordinary-task',worker_owner=worker.owner);thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]['decision'],'answer');self.assertEqual(result[0]['status'],'answered')
        self.assertEqual(result[0]['answers'],ANSWERS);self.assertEqual(result[0]['request_digest'],digest(request()))
        self.assertEqual(f.store.task('ordinary-task')['status'],'checks_complete')

class InputProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        result=subprocess.run(['codex','app-server','generate-json-schema','--experimental','--out',cls.temp.name],capture_output=True,timeout=10)
        if result.returncode:raise RuntimeError('Provider schema export failed')
        cls.schemas={name:json.loads(next(Path(cls.temp.name).rglob(name+'.json')).read_text())for name in ('ServerRequest',INPUT_RESPONSE)}
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def adapter(self,handler):
        adapter=AppServerAdapter('/fixture',schemas=self.schemas,read_only=True);adapter.request_handler=handler
        sent=[];adapter._send=sent.append;return adapter,sent
    def test_actual_schema_wire_answer_roundtrip_and_no_raw_evidence(self):
        value=request()
        adapter,sent=self.adapter(lambda req:{'decision':'answer','status':'answered','request_digest':digest(req),'answers':ANSWERS})
        adapter.inbox.put(value);adapter._message(.01)
        self.assertEqual(sent,[{'id':99,'result':{'answers':ANSWERS}}]);self.assertEqual(adapter.denials,[])
        self.assertNotIn('answers',adapter.inputs[0]);self.assertNotIn('Which summary',json.dumps(adapter.inputs))
    def test_forged_binding_invalid_answer_and_secret_decline(self):
        for scenario in ('digest','answer','secret','missing_binding'):
            value=request();answer=copy.deepcopy(ANSWERS)
            if scenario=='answer':answer['choice']['answers']=['arbitrary']
            if scenario=='secret':value['params']['questions'][0]['isSecret']=True
            if scenario=='missing_binding':del value['params']['turnId']
            adapter,sent=self.adapter(lambda req:{'decision':'answer','status':'answered','request_digest':'f'*64 if scenario=='digest'else digest(req),'answers':answer})
            adapter.inbox.put(value)
            try:adapter._message(.01)
            except ServiceError:pass
            self.assertEqual(sent[0]['result'],{'answers':{}})
            self.assertEqual(len(adapter.denials),1)
    def test_old_fixture_input_without_answer_handler_declines(self):
        adapter,sent=self.adapter(lambda req:{'decision':'decline','status':'declined','request_digest':digest(req)})
        adapter.inbox.put(request());adapter._message(.01)
        self.assertEqual(sent[0]['result'],{'answers':{}});self.assertEqual(len(adapter.denials),1)

if __name__=='__main__':unittest.main()
