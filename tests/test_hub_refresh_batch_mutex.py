"""Real HTTP admission contends with the shared cross-process batch mutex."""
import selectors
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock
from connection_fixtures import Fixture
from hub.connection_records import validate_result
from hub.connection_refresh import RefreshLedger
from hub.connection_sources import SourceResolver
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth
from hub.project_service import ProjectService
import test_hub_local_service as transport

LOCK_CHILD=r'''
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from hub.connection_records import validate_result
from hub.connection_refresh import RefreshLedger
from hub.connection_sync import sync_mutex
ledger=RefreshLedger(Path(sys.argv[2]),result_validator=validate_result)
with sync_mutex(ledger):
    print('locked',flush=True)
    sys.stdin.readline()
'''

class RefreshBatchMutexTests(unittest.TestCase):
    call=transport.LocalHTTPTests.call
    stop=transport.LocalHTTPTests.stop
    session=transport.LocalHTTPTests.session

    def setUp(self):
        self.fixture=Fixture();self.addCleanup(self.fixture.close)
        self.fixture.add('a')
        self.ledger=RefreshLedger(self.fixture.hub,result_validator=validate_result)
        self.initial=self.ledger.head
        self.service=ProjectService(self.fixture.hub)
        self.owner_proof='synthetic-fixture-owner-proof-not-a-real-secret-00000'
        self.server=HubHTTPServer(self.service,transport.DesignSpy(),
            owner_auth=OwnerAuth(provider=lambda:self.owner_proof))
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01})
        self.thread.start();self.addCleanup(self.stop)
        self.cookie=self.csrf=None;self.session()
        self.children=[];self.addCleanup(self.close_children)

    def close_children(self):
        for child in self.children:
            try:child.communicate('\n',timeout=5)
            except (subprocess.TimeoutExpired,BrokenPipeError,ValueError):
                if child.poll() is None:child.terminate()
                child.communicate(timeout=5)

    def payload(self,ident,head=None):
        return {'request_id':ident,'project_ids':['a'],'expected_head':head or self.initial}

    def assert_busy(self,result,ident):
        status,_,body=result
        self.assertEqual(status,409,body)
        self.assertEqual(body['error']['code'],'REFRESH_BATCH_BUSY')
        self.assertTrue(body['error']['retryable'])
        self.assertEqual(body['error']['outcome'],'NOT_COMMITTED')
        self.assertEqual(body['error']['details']['request_id'],ident)

    def test_real_process_lock_denies_http_before_source_read_then_exact_request_replays(self):
        child=subprocess.Popen([sys.executable,'-c',LOCK_CHILD,
            str(Path(__file__).resolve().parents[1]/'src'),str(self.fixture.hub)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.children.append(child)
        with selectors.DefaultSelector() as selected:
            selected.register(child.stdout,selectors.EVENT_READ)
            self.assertTrue(selected.select(5),'child failed to acquire batch mutex')
            self.assertEqual(child.stdout.readline().strip(),'locked')
        payload=self.payload('same-after-busy')
        with mock.patch.object(SourceResolver,'refresh',side_effect=AssertionError('busy read')) as read:
            self.assert_busy(self.call('POST','/api/refresh',payload),'same-after-busy')
            read.assert_not_called()
        self.assertEqual(self.ledger.head,self.initial)
        self.assertEqual(self.ledger.history()['requests'],[])
        stdout,stderr=child.communicate('\n',timeout=5)
        self.assertEqual(child.returncode,0,stderr)
        original=SourceResolver.refresh
        with mock.patch.object(SourceResolver,'refresh',autospec=True,side_effect=original) as read:
            status,_,body=self.call('POST','/api/refresh',payload)
            self.assertEqual(status,200,body);self.assertEqual(read.call_count,1)
            committed=self.ledger.head
            status,_,body=self.call('POST','/api/refresh',payload)
            self.assertEqual(status,200,body);self.assertEqual(read.call_count,1)
            self.assertEqual(self.ledger.head,committed)
        self.assertEqual(len(self.ledger.history()['results']),1)

    def test_simultaneous_manual_http_request_busy_then_cas_and_same_id_recovery(self):
        entered=threading.Event();release=threading.Event();result={}
        original=SourceResolver.refresh
        def gated(resolver,ident):
            entered.set()
            if not release.wait(5):raise TimeoutError('test resolver gate timed out')
            return original(resolver,ident)
        def first():result['first']=self.call('POST','/api/refresh',self.payload('first'))
        with mock.patch.object(SourceResolver,'refresh',autospec=True,side_effect=gated) as read:
            client=threading.Thread(target=first);client.start()
            try:
                self.assertTrue(entered.wait(3),'first HTTP refresh did not reach source gate')
                self.assert_busy(self.call('POST','/api/refresh',self.payload('second')),'second')
                self.assertEqual(read.call_count,1)
            finally:
                release.set();client.join(5)
            self.assertFalse(client.is_alive())
        self.assertEqual(result['first'][0],200,result)
        status,_,body=self.call('POST','/api/refresh',self.payload('second'))
        self.assertEqual(status,409);self.assertEqual(body['error']['code'],'REFRESH_HEAD_CONFLICT')
        status,_,body=self.call('POST','/api/refresh',self.payload('second',self.ledger.head))
        self.assertEqual(status,200,body)
        committed=self.ledger.head
        status,_,body=self.call('POST','/api/refresh',self.payload('second',self.initial))
        self.assertEqual(status,200,body);self.assertEqual(self.ledger.head,committed)
        self.assertEqual(len(self.ledger.history()['requests']),2)
        self.assertEqual(len(self.ledger.history()['results']),2)

if __name__=='__main__':unittest.main()
