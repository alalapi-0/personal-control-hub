"""Owner proof/session boundary, with transient random secrets and zero real data."""
import http.client
import json
from pathlib import Path
import secrets
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import test_hub_local_service as fixtures
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth
from hub.service_contract import ServiceError


class OwnerHTTPTests(unittest.TestCase):
    call = fixtures.LocalHTTPTests.call
    stop = fixtures.LocalHTTPTests.stop

    def setUp(self):
        self.projects, self.designs = fixtures.ProjectSpy(), fixtures.DesignSpy()
        self.proof = secrets.token_urlsafe(40)
        self.server = HubHTTPServer(self.projects, self.designs,
            owner_auth=OwnerAuth(provider=lambda: self.proof))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':.01})
        self.thread.start(); self.addCleanup(self.stop)
        self.cookie = self.csrf = None
        self.bootstrap()

    def bootstrap(self):
        status, headers, data = self.call(path='/api/session')
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.csrf = data['data']['csrf_token']

    def login(self):
        status, headers, data = self.call('POST','/api/owner/login',{'token':self.proof})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly; SameSite=Strict', headers['Set-Cookie'])
        self.cookie = headers['Set-Cookie'].split(';',1)[0]
        self.csrf = data['data']['csrf_token']
        self.assertNotIn(self.proof, json.dumps(data))
        return data

    def test_owner_rotation_replay_bootstrap_identity_and_logout(self):
        guest, old_csrf = self.cookie, self.csrf
        self.login()
        self.assertEqual(self.projects.calls, [])
        self.assertEqual(self.designs.calls, [])
        owner, owner_csrf = self.cookie, self.csrf
        self.assertNotEqual(guest, owner); self.assertNotEqual(old_csrf, owner_csrf)
        status, _, data = self.call('POST','/api/owner/login',{'token':self.proof},
            headers={'Cookie':guest,'X-Hub-CSRF':old_csrf})
        self.assertEqual(status,401)
        self.assertNotIn('Set-Cookie', _)
        self.bootstrap()
        self.assertEqual(self.cookie,owner); self.assertEqual(self.csrf,owner_csrf)
        status, _, first = self.call(path='/api/identity')
        self.assertEqual(status,200); self.assertTrue(first['data']['owner_authenticated'])
        self.assertFalse(first['data']['execution_enabled'])
        second = self.call(path='/api/identity')[2]
        self.assertEqual(first['data']['backend_instance'],second['data']['backend_instance'])
        self.assertEqual(self.call('POST','/api/owner/logout',{})[0],200)
        self.assertEqual(self.call(path='/api/identity')[0],401)
        self.bootstrap(); self.login()
        self.assertNotEqual(self.cookie,owner)
        self.assertEqual(self.call(path='/api/identity',headers={'Cookie':owner})[0],401)

    def test_wrong_missing_unreadable_malformed_proof_and_config_have_zero_effects(self):
        for proof in [secrets.token_urlsafe(40),None,[], 'short','x'*513,'\ud800'*40]:
            status, headers, _ = self.call('POST','/api/owner/login',{'token':proof})
            self.assertEqual(status,401); self.assertNotIn('Set-Cookie',headers)
        for value in [None,'', 'short', [], 'x'*513]:
            self.proof=value
            status, headers, _ = self.call('POST','/api/owner/login',{'token':secrets.token_urlsafe(40)})
            self.assertEqual(status,503);self.assertNotIn('Set-Cookie',headers)
        with patch.object(self.server.owner_auth,'provider',side_effect=OSError('fixture failure')):
            self.assertEqual(self.call('POST','/api/owner/login',{'token':secrets.token_urlsafe(40)})[0],503)
        self.assertEqual(self.projects.calls,[]);self.assertEqual(self.designs.calls,[])

    def test_guest_cannot_mutate_and_auth_loss_fails_closed(self):
        self.assertEqual(self.call('POST','/api/refresh',{})[0],401)
        self.assertEqual(self.projects.calls,[])
        self.login();self.proof=None
        self.assertEqual(self.call('POST','/api/refresh',{})[0],401)
        self.bootstrap()
        self.assertEqual(self.call('POST','/api/refresh',{})[0],503)
        self.assertEqual(self.projects.calls,[])
        status, _, body = self.call('POST','/api/tasks',{})
        self.assertEqual(status,503)
        self.assertEqual(body['error']['code'],'OWNER_AUTH_UNAVAILABLE')
        self.assertIsNone(self.server.tasks)
        for path in ['/api/execute','/api/codex','/api/grants']:
            self.assertEqual(self.call('POST',path,{})[0],404)
        self.assertEqual(self.projects.calls,[]);self.assertEqual(self.designs.calls,[])

    def test_credential_rotation_revokes_old_owner_session(self):
        self.login();old_cookie=self.cookie
        self.proof=secrets.token_urlsafe(40)
        self.assertEqual(self.call('POST','/api/refresh',{})[0],401)
        self.bootstrap();self.login()
        self.assertEqual(self.call(path='/api/identity',headers={'Cookie':old_cookie})[0],401)
        self.assertEqual(self.projects.calls,[]);self.assertEqual(self.designs.calls,[])

    def test_expired_session_and_wrong_csrf_deny_before_effect(self):
        self.login()
        self.assertEqual(self.call('POST','/api/refresh',{},headers={'X-Hub-CSRF':'wrong'})[0],403)
        clock=[0.0];self.server.sessions.clock=lambda:clock[0]
        self.cookie,self.csrf=self.server.sessions.issue(owner=True)
        self.cookie=f'{self.server.sessions.cookie_name}={self.cookie}'
        clock[0]=3601
        status,headers,_=self.call('POST','/api/owner/login',{'token':self.proof})
        self.assertEqual(status,401);self.assertNotIn('Set-Cookie',headers)
        self.assertEqual(self.projects.calls,[]);self.assertEqual(self.designs.calls,[])

    def test_host_origin_cookie_and_duplicate_header_negatives(self):
        self.assertEqual(self.call(path='/api/session',headers={'Cookie':'different_instance=stale'})[0],200)
        for h in [{'Host':'localhost:'+str(self.server.server_address[1])},
                  {'Host':'127.0.0.1:1'},{'Origin':'null'},{'Origin':'https://evil.example'},
                  {'Sec-Fetch-Site':'cross-site'},{'Authorization':'unexpected'}]:
            self.assertEqual(self.call('POST','/api/owner/login',{'token':self.proof},headers=h)[0],403)
        self.assertEqual(self.call(path='/api/session',headers={'Cookie':self.cookie+'; '+self.cookie})[0],401)
        for duplicate in ['Host','Origin','Cookie','Authorization','Content-Length','Content-Type']:
            conn=http.client.HTTPConnection(*self.server.server_address,timeout=3)
            body=json.dumps({'token':self.proof}).encode()
            headers={'Host':self.server.host_header,'Origin':self.server.origin,'Cookie':self.cookie,
                     'Content-Type':'application/json','Content-Length':str(len(body)),'X-Hub-CSRF':self.csrf}
            if duplicate=='Authorization':headers[duplicate]='unexpected'
            try:
                conn.putrequest('POST','/api/owner/login',skip_host=True,skip_accept_encoding=True)
                for k,v in headers.items():conn.putheader(k,v)
                conn.putheader(duplicate,headers[duplicate]);conn.endheaders(body)
                response=conn.getresponse();response.read();self.assertEqual(response.status,400)
            finally:conn.close()
        self.assertEqual(self.projects.calls,[]);self.assertEqual(self.designs.calls,[])

    def test_bounded_proof_rate_limit_has_no_business_effect(self):
        for _ in range(8):
            self.assertEqual(self.call('POST','/api/owner/login',{'token':secrets.token_urlsafe(40)})[0],401)
        self.assertEqual(self.call('POST','/api/owner/login',{'token':self.proof})[0],429)
        self.assertEqual(self.projects.calls,[]);self.assertEqual(self.designs.calls,[])


if __name__=='__main__':unittest.main()
