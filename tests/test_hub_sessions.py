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


    def test_official_owner_only_read_forbids_http_selectors_and_redacts_failure(self):
        from hub.codex_adapter import OfficialMetadataProfile
        calls = []
        class Metadata:
            transport = 'mock'
            next_id = 1
            fail = False
            def open(adapter):
                if adapter.fail:
                    raise ServiceError('CODEX_PROTOCOL_REJECTED', status=503,
                                       details={'private_path': '/PRIVATE-PROFILE'})
            def call(adapter, method, params):
                calls.append((method, params)); adapter.next_id += 1
                if method == 'thread/read':
                    return {'thread': {**row(source='exec'), 'cwd': '/synthetic/delivery', 'sessionId': OTHER}}
                return {'data': [], 'nextCursor': None}
            def close(adapter): pass
        adapter = Metadata()
        profile = OfficialMetadataProfile(ID, HUB_ROOT, Path('/synthetic/delivery'),
            Path('/synthetic/schemas'), Path('/synthetic/binary'), 'a'*64)
        self.server.session_view = SessionService(hub_root=HUB_ROOT, official_metadata_profile=profile,
            adapter_factory=lambda root: adapter, identity=lambda: 'synthetic-generation')
        self.assertEqual(self.call(path='/api/sessions')[0], 401)
        self.assertEqual(calls, [])
        self.login()
        for query in ('profile=official_metadata_0.163', 'thread_id='+ID, 'root=/synthetic',
                      'binding=/synthetic/binding', 'sha256='+'a'*64):
            self.assertEqual(self.call(path='/api/sessions?'+query)[0], 400)
        self.assertEqual(calls, [])
        status, _, body = self.call(path='/api/sessions')
        self.assertEqual(status, 200)
        self.assertEqual(body['data']['sources'][0]['rows'][0]['session_id'], OTHER)
        self.assertFalse(body['data']['native_execution_admission'])
        self.server.session_view.cache = None; adapter.fail = True
        status, _, body = self.call(path='/api/sessions')
        self.assertEqual(status, 503)
        self.assertNotIn('PRIVATE', str(body))


class SessionDataRootTests(unittest.TestCase):
    def test_explicit_data_root_keeps_native_cwd_separate_from_code_root(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SessionService(hub_root=root)
            actual = row()
            actual['cwd'] = str(root)
            self.assertEqual(service._row(actual, 'vscode', set(actual))['id'], ID)
            actual['cwd'] = str(HUB_ROOT)
            with self.assertRaises(ServiceError) as error:
                service._row(actual, 'vscode', set(actual))
            self.assertEqual(error.exception.code, 'SESSION_BINDING_MISMATCH')
            FakeDiscovery.calls = []; FakeDiscovery.results = {}; FakeDiscovery.open_error = None; FakeDiscovery.hook = None
            metadata = SessionService(hub_root=root, adapter_factory=FakeDiscovery, identity=lambda: 'fixture')
            metadata.catalog(lambda: 'owner')
            self.assertEqual([params['cwd'] for method, params in FakeDiscovery.calls if method == 'thread/list'], [str(root)] * 3)
            self.assertEqual(SessionService().hub_root, HUB_ROOT)

    def test_invalid_trusted_data_root_is_rejected_without_provider_calls(self):
        for root in ['relative', '/tmp/../other', None, 3]:
            with self.subTest(root=root), self.assertRaises(ServiceError) as error:
                SessionService(hub_root=root)
            self.assertEqual(error.exception.code, 'SESSION_ROOT_REJECTED')


class OfficialMetadataTests(unittest.TestCase):
    """Synthetic wire frames only: no native process, model, or account access."""
    def setUp(self):
        import io
        import tempfile
        from types import SimpleNamespace
        from hub.codex_adapter import OfficialMetadataProfile, OfficialMetadataAdapter
        self.temporary = tempfile.TemporaryDirectory(prefix='hub-official-metadata-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profile = OfficialMetadataProfile(ID, self.root/'canonical', self.root/'delivery',
                                               self.root/'schemas', self.root/'binary', 'a'*64)
        self.adapter = OfficialMetadataAdapter(self.profile)
        self.adapter.schemas = {name: {} for pair in self.adapter.methods.values() for name in pair}
        self.adapter.schemas['ThreadReadResponse'] = {'definitions': {'Thread': {
            'type': 'object', 'required': ['sessionId'], 'properties': {'sessionId': {'type': 'string'}}}},
            'type': 'object', 'properties': {'thread': {'$ref': '#/definitions/Thread'}}}
        self.adapter.proc = SimpleNamespace(stdin=io.BytesIO())
        self.adapter.schemas['RemoteControlStatusChangedNotification'] = {
            'type': 'object', 'properties': {
                'environmentId': {'type': ['string', 'null']}, 'installationId': {'type': 'string'},
                'serverName': {'type': 'string'}, 'status': {'enum': ['disabled', 'connecting', 'connected', 'errored']}},
            'required': ['installationId', 'serverName', 'status']}
        self.owner, self.environment = ['owner'], ['v1']

    def initialize(self):
        from hub.codex_adapter import OFFICIAL_INITIALIZE
        self.adapter.inbox.put({'id': 1, 'result': {'userAgent': 'synthetic'}})
        self.adapter.call('initialize', copy.deepcopy(OFFICIAL_INITIALIZE))
        self.adapter._send({'method': 'initialized', 'params': {}})

    def thread(self):
        return {**row(source='exec'), 'cwd': str(self.profile.delivery_root), 'sessionId': OTHER}

    def read(self, thread=None):
        self.adapter.inbox.put({'id': self.adapter.next_id+1, 'result': {'thread': thread or self.thread()}})
        return self.adapter.call('thread/read', {'threadId': ID, 'includeTurns': False})

    def page(self, cursor=None, result=None):
        from hub.codex_adapter import OFFICIAL_PAGE_LIMIT
        params = {'threadId': ID, 'itemsView': 'notLoaded', 'limit': OFFICIAL_PAGE_LIMIT, 'sortDirection': 'desc'}
        if self.adapter.page_count:
            params['cursor'] = cursor
        self.adapter.inbox.put({'id': self.adapter.next_id+1, 'result': result or {
            'data': [], 'nextCursor': None, 'backwardsCursor': None}})
        return self.adapter.call('thread/turns/list', params)

    def service(self):
        service = SessionService(hub_root=self.profile.canonical_root, official_metadata_profile=self.profile,
                                 adapter_factory=lambda root: self.adapter, identity=lambda: self.environment[0])
        self.adapter.open = lambda: self.adapter
        self.adapter.close = lambda: None
        return service

    def test_explicit_default_open_wire_has_no_legacy_launch_or_diagnostics(self):
        import hub.codex_adapter as module
        from types import SimpleNamespace
        self.adapter.inbox.put({'id': 1, 'result': {'userAgent': 'synthetic'}})
        with patch.object(self.adapter, '_load_schemas'), patch.object(module, '_official_binary_hash', return_value='a'*64),\
             patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=module.OFFICIAL_METADATA_VERSION)),\
             patch.object(module.subprocess, 'Popen', return_value=self.adapter.proc) as spawn,\
             patch.object(module.threading, 'Thread'):
            self.adapter.open()
        self.assertEqual(spawn.call_args.args[0], (str(self.profile.binary), 'app-server'))
        self.assertEqual(spawn.call_args.kwargs['cwd'], self.profile.delivery_root)
        self.assertNotIn('CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED', spawn.call_args.kwargs['env'])
        import json
        frames = [json.loads(line) for line in self.adapter.proc.stdin.getvalue().splitlines()]
        self.assertEqual(frames, [{'id': 1, 'method': 'initialize', 'params': module.OFFICIAL_INITIALIZE},
                                  {'method': 'initialized', 'params': {}}])

    def test_forbidden_methods_and_parameters_are_zero_wire(self):
        from hub.codex_adapter import OFFICIAL_INITIALIZE
        for method in ('thread/list', 'thread/start', 'thread/resume', 'turn/start', 'turn/steer',
                       'turn/interrupt', 'config/read', 'account/read', 'goal/read', 'command/exec', 'unknown'):
            with self.subTest(method=method), self.assertRaises(ServiceError):
                self.adapter.call(method, {})
            with self.assertRaises(ServiceError):
                self.adapter._send({'id': 1, 'method': method, 'params': {}})
        for changes in ({'experimentalApi': True}, {'experimentalApi': 0}, {'explicitGatewayOauth': False}, {'extensions': {}}):
            params = copy.deepcopy(OFFICIAL_INITIALIZE); params['capabilities'].update(changes)
            with self.assertRaises(ServiceError): self.adapter.call('initialize', params)
        self.assertEqual(self.adapter.proc.stdin.getvalue(), b'')
        self.initialize(); before = self.adapter.proc.stdin.getvalue()
        for params in ({'threadId': OTHER, 'includeTurns': False}, {'threadId': ID, 'includeTurns': True},
                       {'threadId': ID, 'includeTurns': 0}, {'threadId': ID, 'includeTurns': False, 'cwd': '/forged'}):
            with self.assertRaises(ServiceError): self.adapter.call('thread/read', params)
        self.assertEqual(self.adapter.proc.stdin.getvalue(), before)

    def test_thread_session_independent_missing_and_strict_binding(self):
        for field, value in [('sessionId', OTHER), ('sessionId', None), ('missing', None)]:
            self.adapter.metadata_read = False
            if not self.adapter.initialized: self.initialize()
            thread = self.thread()
            if field == 'missing': del thread['sessionId']
            else: thread[field] = value
            result = self.read(thread)['thread']
            self.assertEqual(result.get('sessionId'), value)
            self.assertEqual(result['source'], 'exec')
        for changes in ({'id': OTHER}, {'cwd': str(self.profile.canonical_root)}, {'cwd': '/forged'}, {'turns': [1]}):
            self.adapter.metadata_read = False
            with self.assertRaises(ServiceError): self.read({**self.thread(), **changes})

    def test_bounded_opaque_pagination_and_no_full_items(self):
        from hub.codex_adapter import OFFICIAL_MAX_PAGES, OFFICIAL_PAGE_LIMIT
        self.initialize(); self.read()
        opaque = '{not a path: PRIVATE cursor}'
        for index in range(OFFICIAL_MAX_PAGES):
            self.page(opaque if index else None, {'data': [], 'nextCursor': opaque})
        before = self.adapter.proc.stdin.getvalue()
        with self.assertRaises(ServiceError):
            self.adapter.call('thread/turns/list', {'threadId': ID, 'itemsView': 'notLoaded',
                'limit': OFFICIAL_PAGE_LIMIT, 'sortDirection': 'desc', 'cursor': opaque})
        self.assertEqual(before, self.adapter.proc.stdin.getvalue())
        self.adapter.page_count = 0
        for params in ({'threadId': ID, 'itemsView': 'full', 'limit': OFFICIAL_PAGE_LIMIT, 'sortDirection': 'desc'},
                       {'threadId': ID, 'itemsView': 'notLoaded', 'limit': 999, 'sortDirection': 'desc'},
                       {'threadId': ID, 'itemsView': 'notLoaded', 'limit': OFFICIAL_PAGE_LIMIT, 'sortDirection': 'desc', 'cursor': 'invented'}):
            with self.assertRaises(ServiceError): self.adapter.call('thread/turns/list', params)
        for turn in ({'id': ID, 'items': [1], 'itemsView': 'notLoaded', 'status': 'interrupted'},
                     {'id': ID, 'items': [], 'itemsView': 'full', 'status': 'interrupted'}):
            with self.assertRaises(ServiceError): self.page(result={'data': [turn], 'nextCursor': None})

    def test_malformed_server_request_eof_never_answers_or_grants(self):
        import io
        for raw in (b'[]\n', b'{"id":1,"id":2}\n', b'bad\n', b''):
            self.adapter.failed = None; self.adapter.proc.stdout = io.BytesIO(raw)
            self.adapter._read()
            with self.assertRaises(ServiceError): self.adapter._message(0)
        self.adapter.failed = None
        before = self.adapter.proc.stdin.getvalue()
        self.adapter.inbox.put({'id': 1, 'method': 'item/commandExecution/requestApproval', 'params': {}})
        with self.assertRaises(ServiceError): self.adapter._message(0)
        self.assertEqual(self.adapter.proc.stdin.getvalue(), before)

    def test_service_exec_notloaded_interrupted_are_not_admission_and_cache_safe(self):
        self.initialize(); service = self.service()
        turn = {'id': ID, 'status': 'interrupted', 'items': [], 'itemsView': 'notLoaded', 'completedAt': None}
        self.adapter.inbox.put({'id': 2, 'result': {'thread': self.thread()}})
        self.adapter.inbox.put({'id': 3, 'result': {'data': [turn], 'nextCursor': None}})
        data = service.catalog(lambda: self.owner[0])
        self.assertEqual(data['sources'][0]['source'], 'exec')
        self.assertEqual(data['sources'][0]['rows'][0]['session_id'], OTHER)
        self.assertIsNone(data['metadata_read']['turns'][0]['completedAt'])
        self.assertEqual(data['metadata_read']['activity_authority'], 'unknown')
        self.assertFalse(data['continue_enabled']); self.assertFalse(data['resume_verified'])
        self.assertNotIn('PRIVATE', str(data)); self.assertNotIn(str(self.profile.delivery_root), str(data))
        self.assertEqual(service.catalog(lambda: self.owner[0])['generation'], data['generation'])
        self.owner[0] = None
        with self.assertRaises(ServiceError): service.catalog(lambda: self.owner[0])
        self.assertIsNone(service.cache)

    def test_service_owner_generation_change_and_incomplete_pages(self):
        for mode in ('owner', 'generation', 'incomplete', 'missing_cursor'):
            self.adapter.metadata_read = False; self.adapter.page_count = 0
            if not self.adapter.initialized: self.initialize()
            service = self.service(); self.owner[0] = 'owner'; self.environment[0] = 'v1'
            self.adapter.inbox.put({'id': self.adapter.next_id+1, 'result': {'thread': self.thread()}})
            original = self.adapter.call
            def call(method, params):
                result = original(method, params)
                if mode == 'owner': self.owner[0] = None
                if mode == 'generation': self.environment[0] = 'v2'
                return result
            if mode in ('owner', 'generation'):
                if mode == 'generation':
                    self.adapter.inbox.put({'id': self.adapter.next_id+2, 'result': {'data': [], 'nextCursor': None}})
                with patch.object(self.adapter, 'call', side_effect=call), self.assertRaises(ServiceError):
                    service.catalog(lambda: self.owner[0])
                self.assertIsNone(service.cache)
            else:
                pages = 3 if mode == 'incomplete' else 1
                for i in range(pages):
                    result = {'data': [], 'nextCursor': 'opaque'} if mode == 'incomplete' else {'data': []}
                    self.adapter.inbox.put({'id': self.adapter.next_id+2+i, 'result': result})
                data = service.catalog(lambda: self.owner[0])
                self.assertFalse(data['metadata_read']['pages_complete'])
                self.assertTrue(data['sources'][0]['has_cursor'])

    def test_profile_is_server_configured_and_canonical_binding_required(self):
        from dataclasses import FrozenInstanceError
        with self.assertRaises(FrozenInstanceError): self.profile.expected_thread = OTHER
        for kwargs in ({'hub_root': self.profile.delivery_root}, {'hub_root': self.profile.canonical_root, 'metadata_read_id': ID}):
            with self.assertRaises(ServiceError): SessionService(official_metadata_profile=self.profile, **kwargs)

    def test_rpc_refusal_and_malformed_response_do_not_fallback(self):
        from hub.codex_adapter import OFFICIAL_INITIALIZE
        for value, code in [({'id': True, 'result': {}}, 'CODEX_RESPONSE_CONFLICT'),
                            ({'id': 1, 'error': {'code': -32601, 'message': 'unsupported'}}, 'CODEX_RPC_REJECTED')]:
            self.adapter.next_id = 0
            self.adapter.inbox.put(value)
            before = len(self.adapter.proc.stdin.getvalue().splitlines())
            with self.assertRaises(ServiceError) as caught:
                self.adapter.call('initialize', copy.deepcopy(OFFICIAL_INITIALIZE))
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(len(self.adapter.proc.stdin.getvalue().splitlines()), before+1)
            self.assertFalse(self.adapter.initialized)

    def test_missing_session_public_unknown_and_idle_still_unverified(self):
        self.initialize(); service = self.service()
        thread = self.thread(); thread.pop('sessionId'); thread['status'] = {'type': 'idle'}
        self.adapter.inbox.put({'id': 2, 'result': {'thread': thread}})
        self.adapter.inbox.put({'id': 3, 'result': {'data': [], 'nextCursor': None}})
        data = service.catalog(lambda: 'owner')
        self.assertIsNone(data['metadata_read']['session_id'])
        self.assertIsNone(data['sources'][0]['rows'][0]['session_id'])
        self.assertEqual(data['sources'][0]['rows'][0]['state'], 'idle_unverified')
        self.assertFalse(data['native_execution_admission'])
        self.assertFalse(data['continue_enabled']); self.assertFalse(data['resume_verified'])

    @staticmethod
    def remote_notification(status='connected'):
        return {'emittedAtMs': 123, 'method': 'remoteControl/status/changed', 'params': {
            'environmentId': None, 'installationId': 'PRIVATE-INSTALLATION',
            'serverName': 'PRIVATE-SERVER', 'status': status}}

    def test_known_official_notification_interleaving_keeps_metadata_readonly(self):
        from hub.codex_adapter import OFFICIAL_INITIALIZE
        self.adapter.schemas['RemoteControlStatusChangedNotification'] = {
            'type': 'object', 'properties': {
                'environmentId': {'type': ['string', 'null']}, 'installationId': {'type': 'string'},
                'serverName': {'type': 'string'}, 'status': {'enum': ['disabled', 'connecting', 'connected', 'errored']}},
            'required': ['installationId', 'serverName', 'status']}
        self.adapter.inbox.put(self.remote_notification('connecting'))
        self.adapter.inbox.put({'id': 1, 'result': {'userAgent': 'synthetic'}})
        self.adapter.inbox.put(self.remote_notification('connected'))
        self.adapter.call('initialize', copy.deepcopy(OFFICIAL_INITIALIZE))
        self.adapter._send({'method': 'initialized', 'params': {}})
        self.adapter.inbox.put(self.remote_notification('errored'))
        self.read()
        self.adapter.inbox.put(self.remote_notification('disabled'))
        self.page()
        self.assertTrue(self.adapter.metadata_read)
        self.assertIsNone(self.adapter.active_profile)
        self.assertEqual(self.adapter.notifications, [])
        self.assertEqual(self.adapter.denials, [])
        import json
        frames = [json.loads(line) for line in self.adapter.proc.stdin.getvalue().splitlines()]
        self.assertEqual([frame['method'] for frame in frames],
                         ['initialize', 'initialized', 'thread/read', 'thread/turns/list'])
        self.assertNotIn('PRIVATE', str(frames))

    def test_only_known_no_id_notification_is_passive_and_never_answered(self):
        frames = [self.remote_notification(), self.remote_notification(), self.remote_notification()]
        frames[2]['id'] = None
        frames[0]['method'] = 'remoteControl/unknown'
        frames[1]['id'] = 999
        for frame in frames:
            self.adapter.inbox.put(frame)
            before = self.adapter.proc.stdin.getvalue()
            with self.assertRaises(ServiceError) as error:
                self.adapter._message(0)
            self.assertEqual(error.exception.code, 'DISCOVERY_PROVIDER_REQUEST_DENIED')
            self.assertEqual(self.adapter.proc.stdin.getvalue(), before)
            self.assertEqual(self.adapter.metadata_notification_count, 0)
            self.assertIsNone(self.adapter.metadata_remote_status)
        for method in ('remoteControl/read', 'remoteControl/start', 'goal/read', 'account/read',
                       'config/read', 'permissions/read', 'command/exec'):
            with self.assertRaises(ServiceError): self.adapter.call(method, {})
        self.assertEqual(self.adapter.proc.stdin.getvalue(), b'')

    def test_notification_bad_schema_extra_parameters_envelope_and_size_fail_closed(self):
        from hub.codex_adapter import OFFICIAL_NOTIFICATION_BYTES
        frames = []
        for change in ({'status': 'unknown'}, {'installationId': 1}, {'serverName': None},
                       {'environmentId': {}}, {'extraCapability': True}):
            frame = self.remote_notification(); frame['params'].update(change); frames.append(frame)
        frame = self.remote_notification(); del frame['params']['status']; frames.append(frame)
        frame = self.remote_notification(); frame['params'] = []; frames.append(frame)
        for key, value in [('extra', 'data'), ('emittedAtMs', True), ('emittedAtMs', '123'),
                           ('emittedAtMs', -1), ('emittedAtMs', 2**53), ('emittedAtMs', float('inf'))]:
            frame = self.remote_notification(); frame[key] = value; frames.append(frame)
        frame = self.remote_notification(); frame['params']['serverName'] = 'x'*OFFICIAL_NOTIFICATION_BYTES
        frames.append(frame)
        for frame in frames:
            with self.subTest(frame_keys=list(frame), params_type=type(frame['params']).__name__):
                self.adapter.inbox.put(frame)
                with self.assertRaises(ServiceError): self.adapter._message(0)
                self.assertEqual(self.adapter.metadata_notification_count, 0)
                self.assertIsNone(self.adapter.metadata_remote_status)
                self.assertEqual(self.adapter.proc.stdin.getvalue(), b'')

    def test_notification_flood_is_bounded_across_calls(self):
        from hub.codex_adapter import OFFICIAL_INITIALIZE, OFFICIAL_MAX_NOTIFICATIONS
        for _ in range(OFFICIAL_MAX_NOTIFICATIONS): self.adapter.inbox.put(self.remote_notification())
        self.adapter.inbox.put({'id': 1, 'result': {'userAgent': 'synthetic'}})
        self.adapter.call('initialize', copy.deepcopy(OFFICIAL_INITIALIZE))
        self.adapter._send({'method': 'initialized', 'params': {}})
        self.adapter.inbox.put(self.remote_notification())
        with self.assertRaises(ServiceError) as error: self.read()
        self.assertEqual(error.exception.code, 'DISCOVERY_NOTIFICATION_CAPACITY')
        self.assertEqual(self.adapter.metadata_notification_count, OFFICIAL_MAX_NOTIFICATIONS)
        self.assertFalse(self.adapter.metadata_read)
        import json
        frames = [json.loads(line) for line in self.adapter.proc.stdin.getvalue().splitlines()]
        self.assertEqual([f['method'] for f in frames], ['initialize', 'initialized', 'thread/read'])

    def test_total_deadline_is_not_renewed_by_known_notifications_or_late_response(self):
        from hub.codex_adapter import OFFICIAL_INITIALIZE
        self.adapter.inbox.put(self.remote_notification())
        self.adapter.inbox.put(self.remote_notification('errored'))
        self.adapter.inbox.put({'id': 1, 'result': {'userAgent': 'synthetic'}})
        now, waits = [0.0], []
        original = self.adapter._message
        def delayed_message(timeout):
            waits.append(timeout)
            value = original(timeout)
            now[0] += .4
            return value
        with patch('hub.codex_adapter.time.monotonic', side_effect=lambda: now[0]), \
             patch.object(self.adapter, '_message', side_effect=delayed_message):
            with self.assertRaises(ServiceError) as error:
                self.adapter.call('initialize', copy.deepcopy(OFFICIAL_INITIALIZE), timeout=1)
        self.assertEqual(error.exception.code, 'CODEX_TIMEOUT')
        self.assertFalse(self.adapter.initialized)
        self.assertEqual(self.adapter.metadata_notification_count, 2)
        self.assertEqual(len(waits), 3)
        self.assertLessEqual(waits[-1], 1-.8+.0001)
        self.assertEqual(len(self.adapter.proc.stdin.getvalue().splitlines()), 1)

    def test_connected_errored_observations_do_not_leak_or_enable_execution(self):
        self.adapter.inbox.put(self.remote_notification('connected'))
        self.initialize()
        service = self.service()
        self.adapter.inbox.put(self.remote_notification('errored'))
        self.adapter.inbox.put({'id': 2, 'result': {'thread': self.thread()}})
        self.adapter.inbox.put(self.remote_notification('connecting'))
        self.adapter.inbox.put({'id': 3, 'result': {'data': [], 'nextCursor': None}})
        result = service.catalog(lambda: 'owner')
        self.assertFalse(result['native_execution_admission'])
        self.assertFalse(result['continue_enabled']); self.assertFalse(result['resume_verified'])
        self.assertEqual(self.adapter.metadata_remote_status, 'connecting')
        self.assertIsNone(self.adapter.remote_control_status)
        self.assertIsNone(self.adapter.active_profile)
        self.assertEqual(self.adapter.notifications, [])
        for private in ('PRIVATE', 'installationId', 'environmentId', 'serverName', 'connecting', 'connected', 'errored'):
            self.assertNotIn(private, str(result))

    def test_observed_read_state_outer_field_is_ignored_and_discarded(self):
        self.initialize()
        for read_state in (None, 'notLoaded', {'thread': {'id': OTHER, 'cwd': '/forged'},
                           'continue_enabled': True, 'resume_verified': True, 'native_execution_admission': True,
                           'private': 'PRIVATE-READSTATE'}):
            with self.subTest(read_state_type=type(read_state).__name__):
                self.adapter.metadata_read = False
                original = {'thread': self.thread(), 'readState': read_state}
                snapshot = copy.deepcopy(original)
                self.adapter.inbox.put({'id': self.adapter.next_id+1, 'result': original})
                result = self.adapter.call('thread/read', {'threadId': ID, 'includeTurns': False})
                self.assertEqual(result, {'thread': self.thread()})
                self.assertNotIn('readState', result)
                self.assertEqual(original, snapshot)
                self.assertEqual(result['thread']['sessionId'], OTHER)
                self.assertIsNone(self.adapter.active_profile)

    def test_read_state_cannot_repair_bad_core_binding_or_unknown_outer_fields(self):
        self.initialize()
        good_state = {'thread': self.thread(), 'continue_enabled': True}
        variants = [{'readState': good_state}]
        for change in ({'id': OTHER}, {'cwd': '/forged'}, {'turns': [{'body': 'PRIVATE'}]}, {'sessionId': 'forged'}):
            variants.append({'thread': {**self.thread(), **change}, 'readState': good_state})
        variants += [{'thread': self.thread(), 'unknownOuter': None},
                     {'thread': self.thread(), 'readState': None, 'unknownOuter': {'id': ID}}]
        for result in variants:
            with self.subTest(keys=list(result)):
                self.adapter.metadata_read = False
                self.adapter.inbox.put({'id': self.adapter.next_id+1, 'result': result})
                with self.assertRaises(ServiceError):
                    self.adapter.call('thread/read', {'threadId': ID, 'includeTurns': False})
                self.assertFalse(self.adapter.metadata_read)
                self.assertIsNone(self.adapter.active_profile)

    def test_read_state_never_enters_public_metadata_or_execution_projection(self):
        self.initialize(); service = self.service()
        malicious = {'thread': {'id': OTHER, 'sessionId': ID, 'cwd': '/PRIVATE-OVERRIDE', 'source': 'cli'},
                     'status': 'idle', 'writer_absent': True, 'goal_stopped': True,
                     'continue_enabled': True, 'resume_verified': True, 'native_execution_admission': True}
        self.adapter.inbox.put({'id': 2, 'result': {'thread': self.thread(), 'readState': malicious}})
        self.adapter.inbox.put({'id': 3, 'result': {'data': [], 'nextCursor': None}})
        result = service.catalog(lambda: 'owner')
        observed = result['sources'][0]['rows'][0]
        self.assertEqual(observed['id'], ID)
        self.assertEqual(observed['session_id'], OTHER)
        self.assertEqual(observed['source'], 'exec')
        self.assertEqual(observed['state'], 'notLoaded')
        self.assertEqual(result['metadata_read']['activity_authority'], 'unknown')
        self.assertFalse(result['continue_enabled']); self.assertFalse(result['resume_verified'])
        self.assertFalse(result['native_execution_admission'])
        for value in ('readState', 'PRIVATE', 'writer_absent', 'goal_stopped'):
            self.assertNotIn(value, str(result))


class OfficialMetadataBindingTests(unittest.TestCase):
    def setUp(self):
        import tempfile,json,hashlib
        self.temp = tempfile.TemporaryDirectory(prefix='hub-binding-')
        self.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name)
        for name in ('canonical', 'delivery', 'schemas'): (self.top/name).mkdir()
        (self.top/'binary').write_text('synthetic non-executable; never run')
        self.path = self.top/'binding.json'
        self.value = {'schema': 'pch-official-metadata-binding/1', 'profile': 'official_metadata_0.163',
            'expected_thread': ID, 'canonical_root': str(self.top/'canonical'),
            'delivery_root': str(self.top/'delivery'), 'schema_dir': str(self.top/'schemas'),
            'binary': str(self.top/'binary'), 'binary_sha256': 'a'*64}
        self.write()

    def write(self, value=None, raw=None):
        import json,hashlib
        self.path.write_bytes(raw if raw is not None else json.dumps(value or self.value).encode())
        self.path.chmod(0o600)
        self.pin = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def load(self, **kwargs):
        from hub.session_service import load_official_metadata_binding
        return load_official_metadata_binding(kwargs.get('path', str(self.path)),
            kwargs.get('pin', self.pin), server_root=kwargs.get('root', self.top/'canonical'))

    def test_valid_pinned_profile_construction_has_no_native_or_store_work(self):
        from hub.codex_adapter import OfficialMetadataAdapter
        with patch.object(OfficialMetadataAdapter, 'open', side_effect=AssertionError('native spawn')), \
             patch('hub.session_service.environment_identity', side_effect=AssertionError('legacy fallback')):
            profile = self.load()
            service = SessionService(hub_root=self.top/'canonical', official_metadata_profile=profile)
        self.assertEqual(profile.profile.expected_thread, ID)
        self.assertEqual(profile.profile.delivery_root, self.top/'delivery')
        self.assertIs(service.official_metadata_binding, profile)
        self.assertIsNone(service.cache)
        self.assertIsNone(service.metadata_read_id)
        self.assertFalse((self.top/'canonical'/'data').exists())

    def test_bad_pin_alias_mode_link_owner_size_and_drift_are_redacted(self):
        import os
        from types import SimpleNamespace
        from hub.session_service import load_official_metadata_binding
        def rejected(**kwargs):
            with self.assertRaises(ServiceError) as error: self.load(**kwargs)
            self.assertEqual(error.exception.code, 'OFFICIAL_METADATA_BINDING_REJECTED')
            self.assertNotIn(str(self.top), str(error.exception.as_dict()))
        rejected(pin='b'*64);rejected(pin=None);rejected(path='relative.json')
        rejected(path=str(self.top)+'/./binding.json')
        link=self.top/'symlink';link.symlink_to(self.path);rejected(path=str(link));link.unlink()
        link=self.top/'hardlink';os.link(self.path,link);rejected();link.unlink()
        self.path.chmod(0o644);rejected();self.path.chmod(0o600)
        with patch('hub.session_service.os.getuid', return_value=os.getuid()+1): rejected()
        self.write(raw=b'x'*16385);rejected();self.write()
        actual=os.fstat
        calls=[0]
        def changed(fd):
            import stat
            info=actual(fd)
            if stat.S_ISREG(info.st_mode):calls[0]+=1
            if stat.S_ISREG(info.st_mode) and calls[0]==2:
                return SimpleNamespace(**{key:getattr(info,key)+(1 if key=='st_mtime_ns' else 0)
                    for key in ('st_dev','st_ino','st_size','st_mode','st_uid','st_gid','st_nlink','st_mtime_ns','st_ctime_ns')})
            return info
        with patch('hub.session_service.os.fstat', side_effect=changed): rejected()

    def test_schema_keys_types_duplicate_root_and_target_aliases_reject(self):
        import json
        for change in ({'schema': 1}, {'profile': 'legacy'}, {'expected_thread': None},
                       {'binary_sha256': False}, {'schema_dir': []}, {'extra': True},
                       {'canonical_root': str(self.top/'delivery')}, {'delivery_root': '/relative/../forged'},
                       {'binary': str(self.top)+'/./binary'}):
            with self.subTest(change=change):
                self.write({**self.value, **change})
                with self.assertRaises(ServiceError):self.load()
        self.write(raw=b'{"schema":1,"schema":2}')
        with self.assertRaises(ServiceError):self.load()
        self.write(raw=b'[]')
        with self.assertRaises(ServiceError):self.load()
        self.write(raw=b'{malformed')
        with self.assertRaises(ServiceError):self.load()
        self.write();link=self.top/'alias';link.symlink_to(self.top/'delivery',target_is_directory=True)
        self.write({**self.value,'delivery_root':str(link)})
        with self.assertRaises(ServiceError):self.load()
        self.write();link=self.top/'parent-alias';link.symlink_to(self.top,target_is_directory=True)
        with self.assertRaises(ServiceError):self.load(path=str(link/'binding.json'))

    def test_post_load_delivery_alias_rejected_before_factory_or_open(self):
        from unittest.mock import Mock
        loaded = self.load()
        factory = Mock(side_effect=AssertionError('factory must not observe invalid inputs'))
        service = SessionService(hub_root=self.top/'canonical', official_metadata_profile=loaded,
                                 adapter_factory=factory, identity=lambda: 'synthetic')
        original=self.top/'delivery';saved=self.top/'saved-delivery';original.rename(saved)
        original.symlink_to(saved,target_is_directory=True)
        with self.assertRaises(ServiceError) as error:service.catalog(lambda:'owner')
        self.assertEqual(error.exception.code,'OFFICIAL_METADATA_BINDING_REJECTED')
        factory.assert_not_called()
        self.assertIsNone(service.cache)

    def guarded_service(self, binding=None):
        from unittest.mock import Mock
        loaded = binding if binding is not None else self.load()
        adapter = Mock(); adapter.transport = 'mock'; adapter.next_id = 1
        def call(method, params):
            adapter.next_id += 1
            if method == 'thread/read':
                return {'thread': {**row(source='exec'), 'cwd': str(loaded.profile.delivery_root), 'sessionId': OTHER}}
            return {'data': [], 'nextCursor': None}
        adapter.call.side_effect = call
        factory = Mock(return_value=adapter)
        service = SessionService(hub_root=self.top/'canonical', official_metadata_profile=loaded,
                                 adapter_factory=factory, identity=Mock(return_value='synthetic'))
        return service, factory, adapter

    def assert_guard_rejects(self, service, factory, adapter):
        with self.assertRaises(ServiceError) as error: service.catalog(lambda:'owner')
        self.assertEqual(error.exception.code,'OFFICIAL_METADATA_BINDING_REJECTED')
        self.assertNotIn(str(self.top),str(error.exception.as_dict()))
        factory.assert_not_called(); adapter.open.assert_not_called()
        self.assertIsNone(service.cache)

    def test_bound_target_replacement_or_alias_rejects_before_factory(self):
        for name in ('delivery', 'schemas', 'binary'):
            for alias in (False, True):
                with self.subTest(target=name, alias=alias):
                    service,factory,adapter=self.guarded_service()
                    target=self.top/name; saved=self.top/('saved-'+name); target.rename(saved)
                    if alias: target.symlink_to(saved,target_is_directory=name!='binary')
                    elif name=='binary':target.write_bytes(saved.read_bytes())
                    else:target.mkdir()
                    self.assert_guard_rejects(service,factory,adapter)
                    service.identity.assert_not_called()
                    if target.is_symlink() or target.is_file():target.unlink()
                    else:target.rmdir()
                    saved.rename(target)

    def test_schema_binary_and_binding_parent_replacements_are_pinned(self):
        import hashlib
        for name in ('schema', 'binary', 'binding'):
            for alias in (False, True):
                with self.subTest(parent=name,alias=alias):
                    self.write()  # Each case starts from the original valid projection.
                    parent=self.top/(name+'-parent-'+str(alias));parent.mkdir()
                    if name=='binding':
                        leaf=parent/'binding.json';raw=self.path.read_bytes();leaf.write_bytes(raw);leaf.chmod(0o600)
                        loaded=self.load(path=str(leaf),pin=hashlib.sha256(raw).hexdigest())
                    else:
                        leaf=parent/('schemas' if name=='schema' else 'binary')
                        if name=='schema':leaf.mkdir()
                        else:leaf.write_text('synthetic')
                        self.write({**self.value,'schema_dir' if name=='schema' else 'binary':str(leaf)})
                        loaded=self.load()
                    service,factory,adapter=self.guarded_service(loaded)
                    saved=parent.with_name(parent.name+'-saved');parent.rename(saved)
                    if alias:parent.symlink_to(saved,target_is_directory=True)
                    else:
                        parent.mkdir()
                        if name=='schema':(parent/leaf.name).mkdir()
                        else:
                            (parent/leaf.name).write_bytes((saved/leaf.name).read_bytes())
                            (parent/leaf.name).chmod(0o600)
                    self.assert_guard_rejects(service,factory,adapter)

    def test_protected_binding_replacement_drift_mode_and_hardlink_reject(self):
        import os,hashlib
        for variant in ('replacement','content','mode','symlink','hardlink'):
            with self.subTest(variant=variant):
                target=self.top/('binding-'+variant+'.json');raw=self.path.read_bytes()
                target.write_bytes(raw);target.chmod(0o600)
                loaded=self.load(path=str(target),pin=hashlib.sha256(raw).hexdigest())
                service,factory,adapter=self.guarded_service(loaded)
                if variant=='content':target.write_bytes(raw+b' ')
                elif variant=='mode':target.chmod(0o644)
                else:
                    saved=target.with_suffix('.saved');target.rename(saved)
                    if variant=='replacement':target.write_bytes(raw);target.chmod(0o600)
                    elif variant=='symlink':target.symlink_to(saved)
                    else:os.link(saved,target)
                self.assert_guard_rejects(service,factory,adapter)

    def test_cache_cannot_hide_binding_or_delivery_drift(self):
        for variant in ('binding','delivery'):
            with self.subTest(variant=variant):
                service,factory,adapter=self.guarded_service()
                first=service.catalog(lambda:'owner')
                self.assertFalse(first['native_execution_admission'])
                self.assertIsNotNone(service.cache)
                factory.reset_mock();adapter.open.reset_mock()
                if variant=='binding':
                    self.path.write_bytes(self.path.read_bytes()+b' ')
                else:
                    delivery=self.top/'delivery';delivery.rename(self.top/'cache-saved-delivery');delivery.mkdir()
                self.assert_guard_rejects(service,factory,adapter)
                if variant=='binding':self.write()

    def test_unchanged_guard_tolerates_unrelated_directory_entry_churn(self):
        from dataclasses import FrozenInstanceError
        loaded=self.load();service,factory,adapter=self.guarded_service(loaded)
        for directory in (self.top,self.top/'delivery',self.top/'schemas',self.top/'canonical'):
            (directory/'unrelated-entry').write_text('synthetic unrelated content')
            (directory/'unrelated-directory').mkdir()
        loaded.check()
        first=service.catalog(lambda:'owner')
        for directory in (self.top,self.top/'delivery',self.top/'schemas',self.top/'canonical'):
            (directory/'unrelated-entry').unlink();(directory/'unrelated-directory').rmdir()
        loaded.check()
        second=service.catalog(lambda:'owner')
        self.assertEqual(first['generation'],second['generation'])
        factory.assert_called_once();adapter.open.assert_called_once()
        with self.assertRaises(FrozenInstanceError):loaded.components=()

    def test_drift_inside_factory_is_rechecked_before_adapter_open(self):
        service,factory,adapter=self.guarded_service()
        def factory_changes_input(root):
            target=self.top/'delivery';target.rename(self.top/'factory-saved');target.symlink_to(self.top/'factory-saved')
            return adapter
        factory.side_effect=factory_changes_input
        with self.assertRaises(ServiceError):service.catalog(lambda:'owner')
        factory.assert_called_once();adapter.open.assert_not_called()
        self.assertIsNone(service.cache)
