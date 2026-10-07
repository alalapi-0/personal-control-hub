"""Pure metadata and temp-only transport fixtures; no native provider."""
import hashlib
import json
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.codex_adapter import AppServerAdapter,NOTIFICATIONS
from hub.notification_metadata import observation
from hub.css_read_diagnostic import atomic_record,notification_fact,ALLOWED_NOTIFICATIONS
from hub.css_capability_recovery import final_capability_state
from hub.service_contract import ServiceError

KNOWN=('thread/status/changed','thread/tokenUsage/updated','remoteControl/status/changed')
SCHEMAS={'ServerNotification':{'oneOf':[{'type':'object','required':['method','params'],
    'properties':{'method':{'enum':[name]},'params':{'type':'object'}},'additionalProperties':False}for name in KNOWN]}}
SCHEMAS.update({NOTIFICATIONS[name]:{'type':'object'}for name in KNOWN if name in NOTIFICATIONS})


class NotificationMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.folder=Path(self.tmp.name)
        self.a=AppServerAdapter('/non-executable-fixture',schemas=SCHEMAS)
        self.a.notification_allowed=ALLOWED_NOTIFICATIONS;self.a.notification_thread='bound';self.a.notification_turn='turn'
        self.a.notification_recorder=lambda n,f:atomic_record(self.folder/(str(n)+'.json'),f)

    def fact(self,value,outcome='validated'):
        return observation(value,SCHEMAS,ALLOWED_NOTIFICATIONS,outcome=outcome,expected_thread='bound',expected_turn='turn')

    def test_secret_values_lengths_and_numbers_do_not_enter_digest(self):
        secrets=['local-sensitive-abc','local-sensitive-'+('z'*5000)]
        facts=[]
        for secret in secrets:
            value={'method':'thread/tokenUsage/updated','params':{'threadId':'bound','turnId':'turn',
                'account':{'token':secret},'message':secret,'tokenUsage':[123456 if len(secret)<100 else 789012]}}
            fact=self.fact(value);facts.append(fact);encoded=json.dumps(fact)
            self.assertNotIn(secret,encoded);self.assertNotIn(hashlib.sha256(secret.encode()).hexdigest(),encoded)
            self.assertNotIn('123456',encoded);self.assertNotIn('789012',encoded)
        self.assertEqual(facts[0]['projection_digest'],facts[1]['projection_digest'])
        self.assertNotEqual(facts[0]['body_size_band'],facts[1]['body_size_band'])
        self.assertNotIn('body_bytes',facts[0]);self.assertFalse(facts[0]['raw_body_or_credential_hash_retained'])

    def test_unknown_secret_method_and_arbitrary_keys_are_neither_saved_nor_hashed(self):
        names=['secret-method-short','secret-method-'+('x'*2000)];facts=[]
        for name in names:
            fact=self.fact({'method':name,'params':{name:'secret-body-value'}},'schema_rejected');facts.append(fact)
            self.assertEqual(fact['method_name'],'unregistered');self.assertEqual(fact['method_category'],'unregistered')
            raw=json.dumps(fact);self.assertNotIn(name,raw);self.assertNotIn('secret-body-value',raw)
            self.assertNotIn(hashlib.sha256(name.encode()).hexdigest(),raw)
        self.assertEqual(facts[0]['projection_digest'],facts[1]['projection_digest'])

    def test_known_but_unallowlisted_method_is_persisted_before_rejection(self):
        value={'method':'thread/tokenUsage/updated','params':{'threadId':'bound','turnId':'turn','message':'never-retain'}}
        self.a.inbox.put(value);actual=self.a.poll(0)
        with self.assertRaises(ServiceError):notification_fact(actual,SCHEMAS)
        saved=json.loads((self.folder/'1.json').read_text())
        self.assertEqual(saved['method_name'],value['method']);self.assertEqual(saved['method_category'],'rejected_known')
        self.assertEqual(saved['validation_outcome'],'validated');self.assertTrue(saved['binding']['turn']['matches'])
        self.assertNotIn('never-retain',json.dumps(saved))

    def test_unknown_schema_method_persists_before_adapter_throws(self):
        self.a.inbox.put({'method':'secret-unknown-method','params':{'account':'secret-account'}})
        with self.assertRaises(ServiceError):self.a.poll(0)
        saved=json.loads((self.folder/'1.json').read_text());self.assertEqual(saved['validation_outcome'],'schema_rejected')
        self.assertEqual(saved['method_name'],'unregistered');self.assertNotIn('secret',json.dumps(saved))

    def test_malformed_and_wrong_bindings_keep_only_booleans(self):
        for actual in ['other-private-thread',33,{'token':'hidden'}]:
            fact=self.fact({'method':'thread/status/changed','params':{'threadId':actual,'turn':{'id':'other'}}})
            self.assertTrue(fact['binding']['thread']['wrong']);self.assertTrue(fact['binding']['turn']['wrong'])
            self.assertNotIn('other-private-thread',json.dumps(fact));self.assertNotIn('hidden',json.dumps(fact))
        fact=self.fact({'method':'thread/status/changed','params':{'thread':{'id':'bound'},'turn':{'id':'turn'}}})
        self.assertTrue(fact['binding']['thread']['matches']);self.assertTrue(fact['binding']['turn']['matches'])

    def test_body_schema_failure_is_retained(self):
        self.a.inbox.put({'method':'thread/status/changed','params':'secret-malformed-body'})
        with self.assertRaises(ServiceError):self.a.poll(0)
        saved=json.loads((self.folder/'1.json').read_text());self.assertEqual(saved['validation_outcome'],'schema_rejected')
        self.assertEqual(saved['shape']['body_type'],'string');self.assertNotIn('secret-malformed-body',json.dumps(saved))

    def test_closed_late_queue_retains_order_before_guard_rejection(self):
        self.a.proc=SimpleNamespace(poll=lambda:0);self.a.failed='CODEX_STREAM_EOF'
        self.a.remote_control_status='disabled';self.a.mcp_startup_seen=False
        for value in [{'method':'remoteControl/status/changed','params':{'status':'disabled'}},
            {'method':'thread/tokenUsage/updated','params':{'threadId':'bound','turnId':'turn'}},
            {'method':'remoteControl/status/changed','params':{'status':'enabled'}}]:self.a.inbox.put(value)
        with self.assertRaises(ServiceError):final_capability_state(self.a,{})
        facts=[json.loads((self.folder/(str(n)+'.json')).read_text())for n in (1,2,3)]
        self.assertEqual([f['method_name']for f in facts],['remoteControl/status/changed','thread/tokenUsage/updated','remoteControl/status/changed'])
        self.assertEqual(self.a.remote_control_status,'enabled')

    def test_failed_persistence_stops_before_return_and_prior_evidence_is_immutable(self):
        atomic_record(self.folder/'1.json',{'prior':'immutable'});before=(self.folder/'1.json').read_bytes()
        self.a.inbox.put({'method':'thread/status/changed','params':{'threadId':'bound'}})
        with self.assertRaises(ServiceError):self.a.poll(0)
        self.assertEqual(before,(self.folder/'1.json').read_bytes());self.assertEqual(self.a.notification_sequence,0)

if __name__=='__main__':unittest.main()
