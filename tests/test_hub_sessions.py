from __future__ import annotations
import copy
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.codex_adapter import ReadonlyAppServerAdapter, DISCOVERY_SCHEMA_PINS
from hub.session_service import SessionService, HUB_ROOT, SOURCES, TTL
from hub.service_contract import ServiceError
import test_hub_owner_auth as auth_fixture

ID = '01a10627-3ebe-75d0-a286-f0e5ee9a056d'
OTHER = '01a10627-3ebe-75d0-a286-f0e5ee9a056e'

def row(source='vscode', ident=ID, state='notLoaded'):
    return {'id': ident, 'source': source, 'cwd': str(HUB_ROOT), 'modelProvider': 'openai',
            'status': {'type': state}, 'createdAt': 123, 'updatedAt': 124,
            'preview': 'PRIVATE-PREVIEW', 'name': 'PRIVATE-TITLE', 'path': '/PRIVATE-STORE', 'turns': []}

class FakeDiscovery:
    transport = 'mock'
    calls = []
    closed = 0
    results = {}
    hook = None
    open_error = None
    def __init__(self, root):
        self.root = root; self.next_id = 0; self.schema_hashes = copy.deepcopy(DISCOVERY_SCHEMA_PINS)
        self.schemas = {'ThreadListResponse': {'definitions': {'Thread': {'properties': dict.fromkeys(row())}}}}
    def open(self):
        if type(self).open_error: raise type(self).open_error
        self.next_id += 1; return self
    def call(self, method, params):
        type(self).calls.append((method, copy.deepcopy(params))); self.next_id += 1
        if type(self).hook: type(self).hook(method)
        value = type(self).results.get(params['sourceKinds'][0], {'backwardsCursor':None,'data': [], 'nextCursor': None}) if method == 'thread/list' else type(self).results.get('read', {'thread': row(state='idle')})
        if isinstance(value, Exception): raise value
        result=copy.deepcopy(value)
        if method=='thread/list' and 'backwardsCursor'not in result:result['backwardsCursor']=None
        return result
    def close(self): type(self).closed += 1

class SessionTests(unittest.TestCase):
    def setUp(self):
        FakeDiscovery.calls=[];FakeDiscovery.closed=0;FakeDiscovery.results={};FakeDiscovery.hook=None;FakeDiscovery.open_error=None
        self.clock=[100.0];self.environment=['env-v1'];self.owner=['owner-session-1']
        self.service=SessionService(adapter_factory=FakeDiscovery,clock=lambda:self.clock[0],identity=lambda:self.environment[0],wall=lambda:1000)
    def catalog(self):return self.service.catalog(lambda:self.owner[0])
    def test_fixed_scope_cap_projection_empty_unprobed_and_cache(self):
        FakeDiscovery.results['vscode']={'data':[row()], 'nextCursor':'PRIVATE-CURSOR'}
        data=self.catalog();self.assertEqual(data['native_calls'],4);self.assertEqual(data['classification'],'mock')
        self.assertEqual(data['sources'][0]['state'],'supported_empty');self.assertTrue(data['sources'][2]['has_cursor'])
        self.assertEqual(data['sources'][2]['rows'][0]['state'],'notLoaded');self.assertEqual(data['unprobed'],['cloud','remote'])
        for (method,params),source in zip(FakeDiscovery.calls,SOURCES):
            self.assertEqual(method,'thread/list');self.assertEqual(params,{'cwd':str(HUB_ROOT),'sourceKinds':[source],'limit':3,'archived':False,'useStateDbOnly':True})
        text=str(data)
        for private in ('PRIVATE-PREVIEW','PRIVATE-TITLE','PRIVATE-STORE','PRIVATE-CURSOR',str(HUB_ROOT),'turns'):self.assertNotIn(private,text)
        self.assertFalse(data['continue_enabled']);self.assertIsNone(data['metadata_read'])
        again=self.catalog();self.assertEqual(len(FakeDiscovery.calls),3);self.assertEqual(again['generation'],data['generation'])
        again['sources'].clear();self.assertEqual(len(self.catalog()['sources']),3)
        self.clock[0]+=TTL;self.catalog();self.assertEqual(len(FakeDiscovery.calls),6)
        self.environment[0]='env-v2';self.catalog();self.assertEqual(len(FakeDiscovery.calls),9)
        self.owner[0]='owner-session-2';self.catalog();self.assertEqual(len(FakeDiscovery.calls),12)
    def test_wrong_binding_extra_fields_oversize_duplicate_and_unsupported(self):
        for change in ({'cwd':'/wrong'},{'source':'cli'},{'modelProvider':'api-other'},{'id':'guess'},{'extraPrivate':'PRIVATE'}):
            with self.subTest(change=change):
                self.service.cache=None;FakeDiscovery.results={'vscode':{'data':[{**row(),**change}], 'nextCursor':None}}
                self.assertEqual(self.catalog()['sources'][2]['state'],'error')
        for data in ([row()]*4,[row(),row()]):
            self.service.cache=None;FakeDiscovery.results={'vscode':{'data':data,'nextCursor':None}}
            self.assertEqual(self.catalog()['sources'][2]['state'],'error')
        self.service.cache=None;FakeDiscovery.results={'cli':{'data':[row('cli')],'nextCursor':None},'vscode':{'data':[row()],'nextCursor':None}}
        data=self.catalog();self.assertEqual([g['state']for g in data['sources']],['error','supported_empty','error'])
        self.service.cache=None;FakeDiscovery.results={'cli':ServiceError('CODEX_RPC_REJECTED',details={'rpc_code':-32601,'private':'PRIVATE'})}
        self.assertEqual(self.catalog()['sources'][0]['state'],'unsupported')
    def test_exact_trusted_read_only_idle_generation_and_mismatch(self):
        FakeDiscovery.results={'vscode':{'data':[row(state='idle')],'nextCursor':None},'read':{'thread':row(state='idle')}}
        self.service.metadata_read_id=ID
        data=self.catalog();self.assertEqual(FakeDiscovery.calls[-1],('thread/read',{'threadId':ID,'includeTurns':False}))
        self.assertEqual(data['native_calls'],5);self.assertFalse(data['metadata_read']['resume_verified'])
        for state in ('active','notLoaded','systemError','unrecognised'):
            self.service.cache=None;FakeDiscovery.calls=[];FakeDiscovery.results['vscode']['data']=[row(state=state)]
            self.catalog();self.assertEqual(len(FakeDiscovery.calls),3)
        FakeDiscovery.results['vscode']['data']=[row(state='idle')]
        for change in ({'id':OTHER},{'cwd':'/wrong'},{'source':'cli'},{'updatedAt':999},{'turns':[{'private':'PRIVATE'}]}):
            self.service.cache=None;FakeDiscovery.results['read']={'thread':{**row(state='idle'),**change}}
            with self.assertRaises(ServiceError):self.catalog()
        self.service.cache=None;self.service.excluded_read_ids=frozenset({ID});FakeDiscovery.calls=[]
        self.catalog();self.assertEqual(len(FakeDiscovery.calls),3)
        self.service.excluded_read_ids=frozenset()
        for cursor in ('backwardsCursor','nextCursor'):
            self.service.cache=None;FakeDiscovery.calls=[];FakeDiscovery.results['vscode']={'data':[row(state='idle')],cursor:'PRIVATE-CURSOR'}
            self.catalog();self.assertEqual(len(FakeDiscovery.calls),3)
    def test_owner_loss_mid_probe_and_generation_change_close_purge(self):
        for trigger in ('revoke','environment','schema'):
            self.service.cache=None;self.owner[0]='owner';self.environment[0]='env-v1';FakeDiscovery.calls=[]
            if trigger=='schema':FakeDiscovery.open_error=ServiceError('CODEX_SCHEMA_CHANGED',details={'path':'PRIVATE'})
            else:
                def hook(method):
                    if trigger=='revoke':self.owner[0]=None
                    else:self.environment[0]='env-v2'
                FakeDiscovery.hook=hook
            with self.assertRaises(ServiceError)as caught:self.catalog()
            self.assertIsNone(self.service.cache);self.assertNotIn('PRIVATE',str(caught.exception.as_dict()))
            FakeDiscovery.open_error=None;FakeDiscovery.hook=None
        self.assertGreaterEqual(FakeDiscovery.closed,3)
    def test_serialization_and_method_boundaries_zero_effect(self):
        self.service.lock.acquire()
        try:
            with self.assertRaises(ServiceError)as caught:self.catalog()
            self.assertEqual(caught.exception.code,'SESSION_DISCOVERY_BUSY');self.assertEqual(FakeDiscovery.calls,[])
        finally:self.service.lock.release()
        adapter=ReadonlyAppServerAdapter(Path('/unused-fixture'))
        for method in ('thread/start','thread/resume','turn/start','turn/steer','turn/interrupt','command/exec','mcpServerStatus/list','unknown'):
            with self.assertRaises(ServiceError):adapter.call(method,{})
            with self.assertRaises(ServiceError):adapter._send({'method':method,'params':{}})
        adapter.inbox.put({'id':1,'method':'unexpected/provider/request','params':{'private':'PRIVATE'}})
        with patch.object(adapter,'_send')as denied:
            with self.assertRaises(ServiceError)as caught:adapter._message(0)
            self.assertEqual(caught.exception.code,'DISCOVERY_PROVIDER_REQUEST_DENIED')
            self.assertEqual(denied.call_args.args[0]['error']['code'],-32601)

    def test_concurrent_refresh_has_one_native_reader(self):
        entered=threading.Event();release=threading.Event();result=[]
        def hook(method):
            if not entered.is_set():entered.set();release.wait(2)
        FakeDiscovery.hook=hook
        thread=threading.Thread(target=lambda:result.append(self.catalog()));thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaises(ServiceError):self.catalog()
            self.assertEqual(len(FakeDiscovery.calls),1)
        finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(len(FakeDiscovery.calls),3);self.assertEqual(len(result),1)

class SessionHTTPTests(unittest.TestCase):
    # Reuse real HTTP identity/session checks, not real account credentials.
    call=auth_fixture.OwnerHTTPTests.call
    stop=auth_fixture.OwnerHTTPTests.stop
    bootstrap=auth_fixture.OwnerHTTPTests.bootstrap
    login=auth_fixture.OwnerHTTPTests.login
    def setUp(self):
        auth_fixture.OwnerHTTPTests.setUp(self)
        self.server.session_view=SessionService(adapter_factory=FakeDiscovery,identity=lambda:'fixture')
        FakeDiscovery.calls=[];FakeDiscovery.closed=0;FakeDiscovery.results={};FakeDiscovery.hook=None;FakeDiscovery.open_error=None
    def test_unauthorized_untrusted_fields_no_dispatch_and_two_owner_sessions(self):
        self.assertEqual(self.call(path='/api/sessions')[0],401);self.assertEqual(FakeDiscovery.calls,[])
        self.login();first_cookie=self.cookie
        for query in ('cwd=/wrong','thread_id='+ID,'source=vscode','cursor=guess','limit=10','method=thread/resume'):
            self.assertEqual(self.call(path='/api/sessions?'+query)[0],400)
        for path in ('/api/sessions/'+ID,'/api/sessions/resume'):
            self.assertEqual(self.call(path=path)[0],404)
        self.assertEqual(self.call('POST','/api/sessions',{})[0],404);self.assertEqual(FakeDiscovery.calls,[])
        self.assertEqual(self.call(path='/api/sessions')[0],200);self.assertEqual(len(FakeDiscovery.calls),3)
        self.cookie=self.csrf=None;self.bootstrap();self.login();self.assertNotEqual(first_cookie,self.cookie)
        self.assertEqual(self.call(path='/api/sessions')[0],200);self.assertEqual(len(FakeDiscovery.calls),6)
    def test_revocation_during_read_returns_no_metadata(self):
        self.login();FakeDiscovery.results={'vscode':{'data':[row()],'nextCursor':None}}
        FakeDiscovery.hook=lambda method:setattr(self,'proof',None)
        status,_,body=self.call(path='/api/sessions');self.assertEqual(status,503)
        self.assertNotIn(ID,str(body));self.assertIsNone(self.server.session_view.cache)
