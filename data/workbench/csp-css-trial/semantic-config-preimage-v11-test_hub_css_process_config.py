import copy
import json
import sys
import unittest
from unittest import mock
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub import css_trial as c
from hub import css_process_config as p
from hub.service_contract import ServiceError


class ProcessConfigTests(unittest.TestCase):
    def fixture(self):
        v={k:{} if t=='dict'else 'fixture'for k,t in c.CONFIG_TOP_TYPES.items()}
        v.update(model=p.MODEL,model_reasoning_effort='xhigh',approval_policy='never',sandbox_mode='danger-full-access')
        v['mcp_servers']={name:{k:{} if t=='dict'else [] if t=='list'else 10 if t=='int'else 'private-command'
            for k,t in row.items()}for name,row in c.CONFIG_MCP_TYPES.items()}
        return v

    def test_only_two_default_values_may_change(self):
        v=self.fixture();before=p.protected_projection(v)
        v['model']='another-default';v['model_reasoning_effort']='ultra'
        self.assertEqual(before,p.protected_projection(v))
        v['approval_policy']='always'
        self.assertNotEqual(before,p.protected_projection(v))
        v['extra_effect']=True
        with self.assertRaises(ServiceError):p.protected_projection(v)

    def test_sensitive_values_have_no_values_or_value_fingerprints(self):
        v=self.fixture();v['mcp_servers']['node_repl']['env']={'AUTH_TOKEN':'first-private-value'}
        before=p.protected_projection(v);v['mcp_servers']['node_repl']['env']['AUTH_TOKEN']='second-private-value'
        self.assertEqual(before,p.protected_projection(v))
        self.assertNotIn('private',json.dumps(before));self.assertFalse(before['secret_values_or_hashes_retained'])
        v['mcp_servers']['unknown']={}
        with self.assertRaises(ServiceError):p.protected_projection(v)

    def test_native_snapshot_must_contain_frozen_process_values(self):
        self.assertEqual(p.configuration_fact({'config':{'model':p.MODEL,'model_reasoning_effort':p.EFFORT,
            'credentials':'private'}})['reasoning_effort'],p.EFFORT)
        with self.assertRaises(ServiceError):p.configuration_fact({'config':{'model':p.MODEL,'model_reasoning_effort':'ultra'}})
        self.assertNotIn('private',json.dumps(p.configuration_fact({'config':{'model':p.MODEL,'model_reasoning_effort':p.EFFORT}})))

    def test_marketplace_nonsecret_values_frozen_sensitive_children_redacted(self):
        v=self.fixture();v['marketplaces']={'official':{'enabled':True,'url':'https://example.test/one','headers':{'AUTH':'first'}}}
        before=p.protected_projection(v)
        v['marketplaces']['official']['headers']['AUTH']='second'
        self.assertEqual(before,p.protected_projection(v))
        v['marketplaces']['official']['enabled']=False
        self.assertNotEqual(before,p.protected_projection(v))
        v['marketplaces']['official']['enabled']=True
        v['marketplaces']['official']['url']='https://example.test/two'
        self.assertNotEqual(before,p.protected_projection(v))

    def test_actual_adapter_launch_resume_and_turn_freeze_same_values(self):
        from hub.codex_adapter import AppServerAdapter,PERMISSION_PROFILE
        events=[];proof={'after_start':{'directories':{}},'before':{'directories':{}}}
        store=SimpleNamespace(event=lambda ident,key,data:events.append({'key':key,'data':data}),
            events=lambda ident:[{'key':'css-placeholder-thread-start-v5','data':proof},*events])
        task={'intent':{'grant':{},'annotation':{}}};a=c.replacement_adapter(store,task,'fixture-owner')
        authority={'contract':p.CONTRACT,'thread_id':'fixture-existing-thread','protected_config_pin':{}}
        with mock.patch.object(c,'runtime_authority',return_value=authority),mock.patch.object(c,'source_check'),\
             mock.patch.object(c,'placeholder_snapshot',return_value={'directories':{}}),\
             mock.patch.object(AppServerAdapter,'open',side_effect=ServiceError('TEST_LAUNCH_BOUNDARY')):
            with self.assertRaises(ServiceError):a.open()
        self.assertEqual(a.command,('codex','app-server','--stdio',*p.OVERRIDES))
        calls=[]
        def call(method,params,timeout=15):
            calls.append((method,params))
            if method=='thread/resume':return {'thread':{'id':'fixture-existing-thread','cwd':str(c.TRIAL)},
                'model':p.MODEL,'reasoningEffort':p.EFFORT,'activePermissionProfile':{'id':PERMISSION_PROFILE,'extends':None}}
            return {'turn':{'id':'fixture-turn'}}
        a.call=call
        with mock.patch.object(c,'runtime_authority',return_value=authority),mock.patch.object(c,'observe_thread',return_value={'status':'idle'}):
            self.assertEqual(a.thread(previous='fixture-existing-thread'),'fixture-existing-thread')
        params=calls[0][1]
        self.assertTrue(params['excludeTurns']);self.assertEqual(params['model'],p.MODEL)
        self.assertEqual(params['config'],{'model_reasoning_effort':p.EFFORT})
        a.capability_thread='fixture-existing-thread'
        with mock.patch.object(c,'runtime_authority',return_value=authority),\
             mock.patch.object(c,'placeholder_snapshot',return_value=proof['after_start']),\
             mock.patch.object(c,'prompt',return_value='fixed prompt'),\
             mock.patch.object(a,'_configuration_hash',return_value='nonsecret-fixture'),\
             mock.patch('hub.codex_adapter.config_fingerprint',return_value='nonsecret-fixture'):
            self.assertEqual(a.start('fixture-existing-thread','fixed prompt'),'fixture-turn')
        self.assertEqual(calls[-1][0],'turn/start')
        self.assertEqual({k:calls[-1][1][k]for k in ('model','effort')},{'model':p.MODEL,'effort':p.EFFORT})
        self.assertEqual(sum(e['key']=='css-unique-model-turn-v5'for e in events),1)
