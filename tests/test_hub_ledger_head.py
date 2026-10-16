"""Lightweight guest-session head reads never collect or read design/provider data."""
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import test_hub_local_service as fixture
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth
from hub.service_contract import ServiceError


class LedgerHeadHTTPTests(unittest.TestCase):
    call=fixture.LocalHTTPTests.call
    stop=fixture.LocalHTTPTests.stop

    def setUp(self):
        self.projects=fixture.ProjectSpy();self.designs=fixture.DesignSpy()
        self.projects.refresh=mock.Mock(side_effect=AssertionError('read must never collect'))
        self.designs.snapshot=mock.Mock(side_effect=AssertionError('read must never load designs'))
        self.server=HubHTTPServer(self.projects,self.designs,owner_auth=OwnerAuth(provider=lambda:None))
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01})
        self.thread.start();self.addCleanup(self.stop)
        self.cookie=self.csrf=None
        status,headers,data=self.call(path='/api/guest',authenticated=False)
        self.assertEqual(status,200)
        self.cookie=headers['Set-Cookie'].split(';',1)[0]
        self.csrf=data['data']['csrf_token']

    def assert_no_side_effects(self):
        self.projects.refresh.assert_not_called()
        self.designs.snapshot.assert_not_called()
        self.assertEqual(self.designs.calls,[])

    def test_empty_and_valid_head_without_owner_or_task_runtime(self):
        for head in (None,{'sequence':0,'hash':'0'*64},{'sequence':7,'hash':'a'*64}):
            with self.subTest(head=head),mock.patch.object(self.projects,'ledger_head',return_value=head) as read:
                status,_,body=self.call(path='/api/ledger-head')
                self.assertEqual(status,200)
                self.assertEqual(body['data'],{'head':head,'backend_instance':self.server.backend_instance})
                read.assert_called_once_with()
        self.assertIsNone(self.server.tasks)
        self.assert_no_side_effects()

    def test_failure_is_503_and_never_empty_success(self):
        for code in ('LEDGER_CORRUPT','LEDGER_BUSY','LEDGER_UNAVAILABLE'):
            with self.subTest(code=code),mock.patch.object(self.projects,'ledger_head',side_effect=ServiceError(code,status=503)):
                status,_,body=self.call(path='/api/ledger-head')
                self.assertEqual(status,503)
                self.assertFalse(body['ok']);self.assertEqual(body['error']['code'],code)
                self.assertNotIn('data',body)
        self.assert_no_side_effects()

    def test_invalid_query_session_host_origin_rejected_before_read(self):
        with mock.patch.object(self.projects,'ledger_head',side_effect=AssertionError('invalid request must not read')) as read:
            self.assertEqual(self.call(path='/api/ledger-head?refresh=true')[0],400)
            self.assertEqual(self.call(path='/api/ledger-head',authenticated=False)[0],401)
            self.assertEqual(self.call(path='/api/ledger-head',headers={'Cookie':'invalid=guest'})[0],401)
            for headers in ({'Host':'evil.example'},{'Origin':'https://evil.example'},{'Sec-Fetch-Site':'cross-site'}):
                self.assertEqual(self.call(path='/api/ledger-head',headers=headers)[0],403)
            read.assert_not_called()
        self.assert_no_side_effects()

if __name__=='__main__':unittest.main()
