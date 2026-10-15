"""Store CAS tests with synthetic requests, never native execution evidence."""
import json
import threading
import time
import unittest
from unittest.mock import patch

import test_hub_task_runtime as fixture
from hub.service_contract import ServiceError
from hub.task_store import TaskStore
from hub.workbench_store import digest


class ExactTaskControlTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TaskRuntimeTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.grant={**self.f.grant,'version':2,'approved_commands':[
            {'command':'python check.py','cwd':str(self.f.workspace)}]}
        self.f.store.register_grant(self.grant)
        # Trusted internal admission fixture. Browser input never supplies a grant.
        self.f.store.admit(self.f.payload,{'grant':self.grant,'grant_hash':digest(self.grant),
                                         'owner_context':'trusted_local_owner'})
        self.f.store.claim('owned-parent')
        self.f.store.update('fixture-task','owned-parent',thread_id='fixture-thread',turn_id='fixture-turn')
        self.request={'id':19,'method':'item/commandExecution/requestApproval','params':{
            'threadId':'fixture-thread','turnId':'fixture-turn','itemId':'item-1','startedAtMs':1,
            'command':'python check.py','cwd':str(self.f.workspace)}}

    def open(self,request=None,**overrides):
        request=self.request if request is None else request
        args=dict(request_hash=digest(request),method=request['method'],thread_id='fixture-thread',
                  turn_id='fixture-turn',expires_at=time.time()+10,provider_request=request)
        args.update(overrides)
        return self.f.store.open_control('fixture-task','owned-parent',**args)

    def respond(self,ident,decision='accept',**kwargs):
        return self.f.store.respond_control('fixture-task',ident,1,decision,**kwargs)

    def test_accept_frozen_summary_and_same_decision_replay(self):
        ident=self.open();control=self.f.store.controls('fixture-task')[0]
        self.assertEqual(control['allowed_decisions'],['decline','accept'])
        self.assertEqual(len(control['operation_sha256']),64)
        encoded=json.dumps(self.f.store.events('fixture-task'))
        self.assertNotIn('python check.py',encoded);self.assertNotIn(str(self.f.workspace),encoded)
        self.assertFalse(self.respond(ident))
        self.assertEqual(self.f.store.controls('fixture-task')[0]['status'],'accepted')
        reopened=TaskStore(self.f.control)
        self.assertTrue(reopened.respond_control('fixture-task',ident,1,'accept'))
        with self.assertRaises(ServiceError):self.respond(ident,'decline')

    def test_decline_then_accept_conflicts(self):
        ident=self.open();self.assertFalse(self.respond(ident,'decline'))
        self.assertTrue(self.respond(ident,'decline'))
        with self.assertRaises(ServiceError):self.respond(ident)

    def test_old_request_stays_deny_only(self):
        ident=self.open(provider_request=None)
        self.assertEqual(self.f.store.controls('fixture-task')[0]['allowed_decisions'],['decline'])
        with self.assertRaises(ServiceError):self.respond(ident)
        self.assertFalse(self.respond(ident,'decline'))

    def test_wrong_request_hash_or_method_never_opens(self):
        with self.assertRaises(ServiceError):self.open(request_hash='b'*64)
        with self.assertRaises(ServiceError):self.open(method='item/fileChange/requestApproval')
        self.assertEqual(self.f.store.controls('fixture-task'),[])

    def test_wrong_request_binding_cannot_accept(self):
        self.request['params']['turnId']='other'
        ident=self.open()
        self.assertEqual(self.f.store.controls('fixture-task')[0]['allowed_decisions'],['decline'])
        with self.assertRaises(ServiceError):self.respond(ident)

    def test_network_or_unknown_permissions_cannot_accept(self):
        self.request['params']['additionalPermissions']={'network':{'enabled':True}}
        ident=self.open()
        with self.assertRaises(ServiceError):self.respond(ident)
        self.assertFalse(self.respond(ident,'decline'))

    def test_expired_and_revoked_grant_cannot_accept(self):
        ident=self.open()
        with patch('hub.task_store.time.time',return_value=time.time()+11):
            with self.assertRaises(ServiceError):self.respond(ident)
        self.f.store.register_grant({**self.grant,'version':3})
        with self.assertRaises(ServiceError):self.respond(ident)
        self.assertEqual(self.f.store.controls('fixture-task')[0]['status'],'pending')

    def test_owner_cancel_version_and_lease_fences(self):
        ident=self.open()
        with self.assertRaises(ServiceError):self.respond(ident,owner_context='another-owner')
        with self.assertRaises(ServiceError):self.f.store.respond_control('fixture-task',ident,2,'accept')
        with patch('hub.task_store.time.time',return_value=time.time()+20):
            with self.assertRaises(ServiceError):self.respond(ident)
        self.f.store.cancel('fixture-task')
        with self.assertRaises(ServiceError):self.respond(ident)

    def test_concurrent_distinct_decisions_commit_only_one(self):
        ident=self.open();barrier=threading.Barrier(2);results=[]
        def respond(decision):
            other=TaskStore(self.f.control)
            barrier.wait()
            try:results.append((decision,other.respond_control('fixture-task',ident,1,decision)))
            except ServiceError:results.append((decision,'rejected'))
        threads=[threading.Thread(target=respond,args=(value,))for value in ('accept','decline')]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=5)
        self.assertEqual(len(results),2)
        self.assertEqual(sum(result is False for _,result in results),1)
        self.assertEqual(sum(result=='rejected'for _,result in results),1)
        events=[event for event in self.f.store.events('fixture-task')if event['key'].startswith('control-decision-')]
        self.assertEqual(len(events),1)

    def test_auth_guard_revoked_before_commit_rolls_back(self):
        ident=self.open();valid=[True]
        def guard():
            if not valid[0]:raise ServiceError('OWNER_AUTH_FAILED',status=401)
        self.f.store.fault=lambda phase:valid.__setitem__(0,False)if phase=='before_commit'else None
        with self.assertRaises(ServiceError):self.respond(ident,authorize=guard)
        self.assertEqual(self.f.store.controls('fixture-task')[0]['version'],1)
