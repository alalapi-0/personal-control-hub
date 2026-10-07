"""Offline transport samples; never launch a provider or replay a model."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub import codex_adapter as c
from hub.css_read_diagnostic import atomic_record
from hub.service_contract import ServiceError


class CapabilityEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name);self.calls=[]
        schemas={name:{'type':'object'}for name in ('ListMcpServerStatusParams','ListMcpServerStatusResponse',
            'ExperimentalFeatureListParams','ExperimentalFeatureListResponse')}
        schemas['ServerNotification']={'type':'object','required':['method','params'],'properties':{
            'method':{'enum':['remoteControl/status/changed','mcpServer/startupStatus/updated']},'params':{'type':'object'}},'additionalProperties':False}
        self.a=c.AppServerAdapter('/fixture',schemas=schemas);self.a.active_profile=c.PERMISSION_PROFILE
        self.a.disabled_servers=c.REGISTERED_SERVERS;self.a.launch_command=('non-executable-fixture',)
        self.a.capability_recorder=lambda n,f:atomic_record(self.folder/(str(n)+'.json'),f)
        def send(request):
            method=request['method'];params=request['params'];self.calls.append((method,params))
            if method=='mcpServerStatus/list':result={'data':[{'name':n,'runtimeStatus':'disabled','tools':{},
                'resources':[],'resourceTemplates':[],'serverCapabilities':None}for n in c.REGISTERED_SERVERS],'nextCursor':None}
            else:
                names=list(c.DISABLED_FEATURES)
                first=params.get('cursor')is None
                result={'data':[{'name':n,'enabled':False}for n in (names[:7]if first else names[7:])],
                    'nextCursor':'private-cursor'if first else None}
                if first:result['data'].append({'name':'code_mode_host','enabled':True})
            self.a.inbox.put({'id':request['id'],'result':result})
        self.a._send=send
        pin=mock.patch.object(c,'config_fingerprint',return_value=c.REGISTERED_CONFIG_SHA256)
        pin.start();self.addCleanup(pin.stop)

    def note(self,status):
        self.a.inbox.put({'method':'remoteControl/status/changed','params':{'status':status}})
        return self.a.poll(0)

    def test_drained_notifications_keep_remote_state_and_complete_two_pages(self):
        self.note('disabled');self.assertFalse(self.a.notifications)
        self.assertEqual(self.a.verify_capabilities('fixture-thread')['remote_control_status'],'disabled')
        self.assertEqual(self.a.verify_capabilities('fixture-thread')['remote_control_status'],'disabled')
        facts=self.a.capability_observations
        pages=[f for f in facts if f.get('method')=='experimentalFeature/list']
        self.assertEqual([(f['request_cursor_present'],f['has_next_page'])for f in pages],[(False,True),(True,False)]*2)
        self.assertEqual(set(r['name']for f in pages for r in f['features']),{*c.DISABLED_FEATURES,'code_mode_host'})
        self.assertEqual(len(list(self.folder.glob('*.json'))),len(facts))
        self.assertNotIn('private-cursor',json.dumps(facts))

    def test_latest_remote_state_wins_regardless_of_order_or_drain(self):
        self.note('disabled');self.note('enabled')
        with self.assertRaises(ServiceError):self.a.verify_capabilities('fixture-thread')
        self.note('disabled');self.assertEqual(self.a.verify_capabilities('fixture-thread')['remote_control_status'],'disabled')

    def test_startup_notice_remains_rejected_after_drain(self):
        self.note('disabled');self.a.inbox.put({'method':'mcpServer/startupStatus/updated','params':{}});self.a.poll(0)
        with self.assertRaises(ServiceError)as e:self.a.verify_capabilities('fixture-thread')
        self.assertTrue(e.exception.details['mcp_startup_seen'])

    def test_successful_response_survives_later_unknown_notification(self):
        self.note('disabled');self.a.call('mcpServerStatus/list',{'threadId':'fixture-thread','limit':100})
        saved=(self.folder/'2.json').read_bytes()
        self.a.inbox.put({'method':'unknown-notification','params':{'secret':'never-retain'}})
        with self.assertRaises(ServiceError):self.a.poll(0)
        self.assertEqual(saved,(self.folder/'2.json').read_bytes())
        self.assertNotIn('never-retain',str(self.a.capability_observations))

    def test_post_response_failure_preserves_mcp_and_feature_results(self):
        self.note('enabled')
        with self.assertRaises(ServiceError):self.a.verify_capabilities('fixture-thread')
        facts=self.a.capability_observations
        self.assertEqual(len(facts[1]['servers']),6);self.assertEqual(facts[1]['unknown_server_count'],0)
        self.assertFalse(facts[-1]['has_next_page']);self.assertEqual(len(list(self.folder.glob('*.json'))),4)

    def test_evidence_is_immutable(self):
        self.note('disabled');saved=(self.folder/'1.json').read_bytes()
        with self.assertRaises(ServiceError):atomic_record(self.folder/'1.json',{'status':'enabled'})
        self.assertEqual(saved,(self.folder/'1.json').read_bytes())


if __name__=='__main__':unittest.main()
