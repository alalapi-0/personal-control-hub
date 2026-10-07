"""Offline tests only: no native helper or authentication subprocess."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub import css_config_precheck as q
from hub.service_contract import ServiceError
import test_hub_css_process_config as fixtures


class ConfigPrecheckTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        trial=self.root/'trial';trial.mkdir();(trial/'.codex').mkdir()
        environment={**q.p.process_environment(),'HOME':str(self.root)}
        for patch in (mock.patch.object(q.c,'TRIAL',trial),mock.patch.object(q.p,'process_environment',return_value=environment),
            mock.patch('hub.codex_adapter.child_environment',return_value=environment),
            mock.patch.object(q.r.time,'time',return_value=100.0)):
            patch.start();self.addCleanup(patch.stop)
        self.authority={'created_at':90.,'expires_at':400.}
        self.a=q.adapter({},self.authority)
        self.user=fixtures.ProcessConfigTests().semantic_fixture();self.pin=q.p.semantic_projection(self.user)
        self.original_reader=q.original_timeout
        patch=mock.patch.object(q,'original_timeout',side_effect=lambda _:self.user['mcp_servers']['node_repl']['startup_timeout_sec'])
        patch.start();self.addCleanup(patch.stop)

    def native(self):
        user=str(self.root/'.codex/config.toml')
        layer=lambda kind,body,**name:{'name':{'type':kind,**name},'config':body,'version':'NEVER_RETAIN_PRIVATE_LAYER_HASH','disabledReason':None}
        layers=[layer('sessionFlags',q.session_config()),layer('project',{},dotCodexFolder=str(q.c.TRIAL/'.codex')),
            layer('user',copy.deepcopy(self.user),file=user),layer('system',{},file='/etc/codex/config.toml')]
        layers[1]['disabledReason']=f'To load project-local config, hooks, and exec policies, add {q.c.TRIAL} as a trusted project in {user}.'
        layers[2]['config']['mcp_servers']['node_repl']['startup_timeout_sec']=float(self.user['mcp_servers']['node_repl']['startup_timeout_sec'])
        config=copy.deepcopy(q.PACKAGED_DEFAULTS)
        for x in reversed(layers):
            if x['disabledReason']is None:config=q.merged(config,x['config'])
        return {'layers':layers,'config':q.effective_config(config),'origins':{'private':'NEVER_RETAIN_ORIGIN'}}

    def test_fixed_layers_and_effective_profile_have_no_private_body_or_version(self):
        self.user['mcp_servers']['github']['command']='NEVER_RETAIN_COMMAND'
        self.pin=q.p.semantic_projection(self.user)
        fact=q.config_fact(self.native(),self.pin);text=json.dumps(fact)
        self.assertNotIn('NEVER_RETAIN',text);self.assertFalse(fact['raw_config_or_layer_body_retained'])
        self.assertEqual(fact['layer_count'],4);self.assertEqual(fact['active_project_trust'],None)
        self.assertEqual(fact['configured_profile'],q.EVIDENCE_PERMISSION_PROFILE)
        self.assertEqual(fact['layers'][1]['disabled_reason'],'missing_trust')
        self.assertTrue(fact['packaged_defaults_filtered']);self.assertEqual(fact['wire_precedence'],'high_to_low')
        self.assertTrue(fact['native_profile_echo'].startswith('NOT_RUN'))

    def test_unknown_enabled_reordered_wrong_source_and_mutated_layers_are_rejected(self):
        changes=[lambda x:x['layers'].append(copy.deepcopy(x['layers'][0])),
            lambda x:x['layers'][0]['name'].update(type='enterpriseManaged'),
            lambda x:x['layers'][3]['config'].update(hooks={}),
            lambda x:x['layers'][2]['name'].update(file='/other/config.toml'),
            lambda x:x['layers'][1].update(disabledReason=None),
            lambda x:x['layers'][1]['config'].update(exec_policy={}),
            lambda x:x['layers'][1]['name'].update(dotCodexFolder='/other/.codex'),
            lambda x:x['layers'][0]['config']['features'].update(hooks=True),
            lambda x:x['layers'].reverse(),lambda x:x['config'].update(default_permissions='writer'),
            lambda x:x['config'].update(unclassified_effect=True),lambda x:x['config'].update(unclassified_effect=None)]
        for index,change in enumerate(changes):
            value=self.native();change(value)
            with self.subTest(index=index),self.assertRaises(ServiceError):q.config_fact(value,self.pin)
        pin=copy.deepcopy(self.pin);pin['sensitive_structure']['projects']['bound_active_context']['trust']['selected']['trust_level']='trusted'
        with self.assertRaises(ServiceError):q.config_fact(self.native(),pin)

    def test_api_serialization_and_resolved_flags_are_separate_from_raw_merge(self):
        native=self.native();effective=native['config'];raw=copy.deepcopy(q.PACKAGED_DEFAULTS)
        for layer in reversed(native['layers']):
            if layer['disabledReason']is None:raw=q.merged(raw,layer['config'])
        self.assertNotEqual(raw,effective)
        self.assertIs(effective['allow_login_shell'],True)
        self.assertIsNone(effective['shell_environment_policy']['inherit'])
        self.assertIsNone(effective['model_instructions_file'])
        self.assertEqual(effective['model_providers'],{})
        self.assertIsNone(effective['features']['network_proxy'])
        self.assertIs(effective['features']['auth_elicitation'],True)
        self.assertIs(effective['features']['remote_plugin'],False)
        self.assertIs(effective['features']['background_paginated_rollout_migration'],False)
        self.assertEqual(effective['mcp_servers']['github']['environment_id'],'local')
        self.assertIsNone(effective['mcp_servers']['github']['tool_timeout_sec'])
        profile=effective['permissions'][q.EVIDENCE_PERMISSION_PROFILE]
        self.assertIsNone(profile['extends']);self.assertIsNone(profile['filesystem']['glob_scan_max_depth'])
        with self.assertRaises(ServiceError):q.config_fact(dict(native,config=raw),self.pin)
        changes=[lambda x:x['features'].update(auth_elicitation=False),
            lambda x:x['features'].update(auth_elicitation=1),lambda x:x['features'].update(network_proxy={}),
            lambda x:x.update(allow_login_shell=False),lambda x:x.update(unknown_nullable=None),
            lambda x:x['permissions'][q.EVIDENCE_PERMISSION_PROFILE]['network'].update(proxy_url='private'),
            lambda x:x['mcp_servers']['github'].update(environment_id='remote'),
            lambda x:x['agents'].update(default_subagent_model='another'),
            lambda x:next(iter(x['marketplaces'].values())).update(last_revision='private')]
        for index,change in enumerate(changes):
            value=self.native();change(value['config'])
            with self.subTest(index=index),self.assertRaises(ServiceError):q.config_fact(value,self.pin)

    def test_packaged_layer_filtered_and_failure_shape_does_not_keep_unknown_names(self):
        value=self.native();value['layers'].append({'name':{'type':'packagedDefaults','file':'private'},'config':{},'version':'private'})
        with self.assertRaises(ServiceError):q.config_fact(value,self.pin)
        shape=q.layer_shape(value);self.assertEqual(shape['count'],5);self.assertEqual(shape['names'][-1],'unknown')
        value['layers'][-1]['name']['type']='NEVER_RETAIN_UNKNOWN';value['layers'][-1]['disabledReason']='NEVER_RETAIN_REASON'
        self.assertNotIn('NEVER_RETAIN',json.dumps(q.layer_shape(value)))
        self.assertEqual(q.layer_shape({'layers':None}),{'array_present':False,'count':None,'names':[]})

    def test_only_exact_duration_roundtrip_equal_to_original_integer_is_accepted(self):
        native=self.native();body=native['layers'][2]['config'];old=copy.deepcopy(body)
        self.assertEqual(q.user_projection(body,self.pin),self.pin);self.assertEqual(body,old)
        timeout=self.user['mcp_servers']['node_repl']['startup_timeout_sec']
        for value in (timeout,True,float(timeout)+.5,float('nan'),float('inf'),float('-inf'),0.,121.,float(timeout)+1):
            changed=copy.deepcopy(body);changed['mcp_servers']['node_repl']['startup_timeout_sec']=value
            with self.subTest(value_type=type(value).__name__),self.assertRaises(ServiceError):q.user_projection(changed,self.pin)
        for change in (lambda x:x['mcp_servers']['node_repl'].pop('startup_timeout_sec'),
            lambda x:x['mcp_servers']['github'].update(startup_timeout_sec=float(timeout)),
            lambda x:x['mcp_servers']['node_repl'].update(unknown_timeout=float(timeout)),
            lambda x:x['agents'].update(max_concurrent_threads_per_session=3.),
            lambda x:x['mcp_servers'].update(wrong_server=x['mcp_servers'].pop('node_repl'))):
            changed=copy.deepcopy(body);change(changed)
            with self.assertRaises(ServiceError):q.user_projection(changed,self.pin)

    def test_private_original_integer_read_is_identity_guarded_and_not_retained(self):
        folder=self.root/'.codex';folder.mkdir();path=folder/'config.toml'
        path.write_text('[mcp_servers.node_repl]\nstartup_timeout_sec=30\n')
        def snapshot():
            st=path.lstat()
            return {'projection':self.pin,'identity':[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns],
                '_private_content_hash':'never-output','_private_projects_hash':'never-output'}
        with mock.patch.object(q.p,'observe',side_effect=lambda **_:snapshot()):
            self.assertEqual(self.original_reader(self.pin),30)
            for value in ('30.0','true','0','121'):
                path.write_text('[mcp_servers.node_repl]\nstartup_timeout_sec='+value+'\n')
                with self.assertRaises(ServiceError):self.original_reader(self.pin)
            path.write_text('[mcp_servers.node_repl]\nstartup_timeout_sec=30\n')
            before=snapshot();after=dict(before,identity=[0,0,0,0,0])
            with mock.patch.object(q.p,'observe',side_effect=[before,after]),self.assertRaises(ServiceError):
                self.original_reader(self.pin)

    def test_every_execution_method_and_raw_send_is_closed(self):
        with mock.patch.object(q.AppServerAdapter,'call')as call,mock.patch.object(q.AppServerAdapter,'_send')as send:
            for method in ('thread/start','thread/read','thread/resume','thread/list','turn/start','turn/steer','command/exec','fs/writeFile'):
                with self.subTest(method=method),self.assertRaises(ServiceError):self.a.call(method,{})
                with self.assertRaises(ServiceError):self.a._send({'id':1,'method':method,'params':{}})
            with self.assertRaises(ServiceError):self.a._send({'id':1,'method':'config/read','params':q.config_params()})
            with self.assertRaises(ServiceError):self.a._send({'method':'initialized','params':{}})
        call.assert_not_called();send.assert_not_called()

    def test_exact_config_once_and_no_scoped_mcp_or_features(self):
        with mock.patch.object(q.AppServerAdapter,'call',return_value={})as call:
            for params in ({'includeLayers':False},{'cwd':'/other','includeLayers':True}):
                with self.assertRaises(ServiceError):self.a.call('config/read',params)
            self.a.call('config/read',q.config_params())
            with self.assertRaises(ServiceError):self.a.call('config/read',q.config_params())
            with self.assertRaises(ServiceError):self.a.call('mcpServerStatus/list',{'threadId':'other','limit':100,'detail':'toolsAndAuthOnly'})
            with self.assertRaises(ServiceError):self.a.call('experimentalFeature/list',{'threadId':'other','limit':100,'cursor':None})
        self.assertEqual(call.call_count,1)

    def test_failed_config_is_consumed_without_replay_and_expiry_blocks_rpc(self):
        with mock.patch.object(q.AppServerAdapter,'call',side_effect=ServiceError('LOST_RESPONSE'))as call:
            with self.assertRaises(ServiceError):self.a.call('config/read',q.config_params())
            with self.assertRaises(ServiceError):self.a.call('config/read',q.config_params())
        self.assertEqual(call.call_count,1)
        self.authority['expires_at']=99.
        with mock.patch.object(q.AppServerAdapter,'call')as call:
            with self.assertRaises(ServiceError):self.a.call('initialize',{})
        call.assert_not_called()

    def test_feature_pagination_rejects_arbitrary_cursor_duplicates_and_more_than_eight(self):
        with mock.patch.object(q.AppServerAdapter,'call',return_value={'data':[],'nextCursor':'private-cursor'})as call:
            for _ in range(8):self.a.call('experimentalFeature/list',{'threadId':None,'limit':100,'cursor':self.a.cursor})
            with self.assertRaises(ServiceError):self.a.call('experimentalFeature/list',{'threadId':None,'limit':100,'cursor':self.a.cursor})
            with self.assertRaises(ServiceError):self.a.call('experimentalFeature/list',{'threadId':None,'limit':100,'cursor':'different'})
        self.assertEqual(call.call_count,8);self.assertNotIn('private-cursor',json.dumps(self.a.audit))

    def test_no_helper_without_independent_authority(self):
        with mock.patch.object(q,'admit',side_effect=ServiceError('NO_AUTHORITY')),mock.patch.object(q.AppServerAdapter,'open')as opened:
            with self.assertRaises(ServiceError):self.a.open()
        opened.assert_not_called()

    def test_registration_integrity_exact_state_counter_and_consumed_intent_gate(self):
        reg={'files':{},'evidence_files':{},'semantic_pin':{},'source':{},'trial':{},'auth_identity':[],'task_hash':'task','created_at':1.}
        reg['candidate_sha256']=q.c.digest(reg['files'])
        reg['evidence_sha256']=q.c.digest({k:v for k,v in reg.items()if k not in ('candidate_sha256','evidence_sha256','created_at')})
        authority={**self.authority,'contract':q.CONTRACT,'task_id':q.c.TASK,'methods':list(q.METHODS),
            'config_read_params':q.config_params(),'profile_arguments':list(q.profile_arguments(q.c.TRIAL,read_only=True)),
            'helpers_allowed':1,'helper_wall_seconds':120,'threads_allowed':0,'resumes_allowed':0,'model_turns_allowed':0,
            'renewal_allowed':False,'auth_mode':'ChatGPT','approved_candidate':reg['candidate_sha256'],'approved_evidence':reg['evidence_sha256'],
            **{k:reg[k]for k in ('semantic_pin','source','trial','auth_identity','task_hash')}}
        state={'linux_visual_workbench':{'contract':{'id':q.CONTRACT,'decision':'APPROVE_NATIVE_PRECHECK','status':'APPROVED_SINGLE_NATIVE_PRECHECK'},
            'authorization':{'conditional_pilot_write_grant':{'state':'active_exact_css_trial','task_id':q.c.TASK,
                'grant_id':'csp-css-trial-grant-v1','project_id':'computer-study-plan'}},'trial_registration':{'resume_calls':4}}}
        def read(path,*args,**kwargs):return q.yaml.safe_dump(state)if path.name=='STATE.yaml'else json.dumps(reg)
        with mock.patch.object(Path,'read_text',read),mock.patch.object(Path,'exists',return_value=False):
            self.assertEqual(q.admit(authority),reg)
            state['linux_visual_workbench']['trial_registration']['resume_calls']=5
            with self.assertRaises(ServiceError):q.admit(authority)
            state['linux_visual_workbench']['trial_registration']['resume_calls']=4
            reg['source']={'tampered':True}
            with self.assertRaises(ServiceError):q.admit(authority)
            del reg['source']['tampered']
            with mock.patch.object(Path,'exists',return_value=True),self.assertRaises(ServiceError):q.admit(authority)
            with self.assertRaises(ServiceError):q.admit(dict(authority,model_turns_allowed=1))

    def test_zero_model_fixture_flow_retains_positive_facts_after_preservation_failure(self):
        folder=self.root/q.c.PRIVATE;folder.mkdir(parents=True)
        task={'intent':{'grant':{}}};authority={**self.authority,'semantic_pin':self.pin,'source':{},'trial':{},'auth_identity':[],
            'task_hash':q.c.digest(task),'approved_candidate':'fixture','approved_evidence':'fixture'}
        (folder/q.AUTHORITY).write_text(json.dumps(authority))
        flags={k:False for k in q.p.FROZEN_DISABLES}|{'code_mode_host':True}
        servers={'nextCursor':None,'data':[{'name':name,'runtimeStatus':'disabled','tools':{},'resources':[],
            'resourceTemplates':[],'serverCapabilities':None}for name in q.REGISTERED_SERVERS]}
        fake=SimpleNamespace(proc=SimpleNamespace(pid=123,returncode=0,poll=lambda:0),audit=[],cursor=None,feature_complete=False,
            wall_exceeded=False,remote_control_status='disabled',mcp_startup_seen=False,denials=[],close=lambda:None)
        def opened():q.atomic_record(folder/q.INTENT,{'fixture':True});return fake
        fake.open=opened
        def call(method,params):
            fake.audit.append({'method':method,'unscoped':True})
            if method=='config/read':return self.native()
            if method=='mcpServerStatus/list':return servers
            fake.feature_complete=True;return {'data':[{'name':k,'enabled':v}for k,v in flags.items()],'nextCursor':None}
        fake.call=call
        before={'identity':[1,2,3,4,5],'_private_content_hash':'private','_private_projects_hash':'private-projects'}
        after=dict(before,identity=[1,2,3,4,6])
        with mock.patch.object(q.c,'HUB_ROOT',self.root),mock.patch.object(q,'admit',return_value={}),\
            mock.patch.object(q.p,'verify',side_effect=[before,after]),mock.patch.object(q.r,'source_snapshot',return_value={}),\
            mock.patch.object(q.c,'placeholder_snapshot',return_value={}),mock.patch.object(q.r,'auth_identity',return_value=[]),\
            mock.patch.object(q.r,'TaskStore',return_value=SimpleNamespace(task=lambda _:task)),\
            mock.patch.object(q.r,'mapping_check'),mock.patch.object(q,'adapter',return_value=fake),mock.patch.object(q,'drain'),\
            mock.patch.object(q.r,'final_capability_state',return_value=True):
            result=q.run()
        self.assertEqual(result['status'],'PARTIAL_BLOCKED');self.assertFalse(result['global_config_preserved'])
        self.assertEqual(result['configuration']['layer_count'],4);self.assertEqual(result['resumes'],0)
        self.assertEqual(result['model_turns'],0);self.assertNotIn('NEVER_RETAIN',(folder/q.RESULT).read_text())

    def test_unscoped_capabilities_are_exact_and_do_not_claim_loaded_thread(self):
        servers={'nextCursor':None,'data':[{'name':name,'runtimeStatus':'disabled','tools':{},'resources':[],
            'resourceTemplates':[],'serverCapabilities':None}for name in q.REGISTERED_SERVERS]}
        flags={k:False for k in q.p.FROZEN_DISABLES}|{'code_mode_host':True}
        a=SimpleNamespace(remote_control_status='disabled',mcp_startup_seen=False,denials=[])
        fact=q.capability_fact(a,servers,flags);self.assertIn('no loaded thread claim',fact['scope'])
        for change in (lambda:servers['data'][0].update(runtimeStatus='enabled'),lambda:flags.update(hooks=True),lambda:setattr(a,'remote_control_status','enabled')):
            change()
            with self.assertRaises(ServiceError):q.capability_fact(a,servers,flags)

if __name__=='__main__':unittest.main()
