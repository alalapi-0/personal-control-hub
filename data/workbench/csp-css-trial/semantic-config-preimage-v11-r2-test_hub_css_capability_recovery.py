"""Offline checks for the exact evidence-only load. No provider/model startup."""
import json
import queue
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub import css_capability_recovery as r
from hub.service_contract import ServiceError
REAL_CALL=r.AppServerAdapter.call


class EvidenceRecoveryTests(unittest.TestCase):
    def setUp(self):
        clock=mock.patch.object(r.time,'time',return_value=1791363000.0)
        clock.start();self.addCleanup(clock.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name);self.authority={'created_at':time.time(),'expires_at':time.time()+300}
        self.a=r.adapter({},self.folder,self.authority)
        patch=mock.patch.object(r.AppServerAdapter,'call',return_value={})
        self.base=patch.start();self.addCleanup(patch.stop)

    def closed_adapter(self,a):
        a.notifications=[];a.inbox=queue.Queue();a.remote_control_status='disabled';a.mcp_startup_seen=False;a.denials=[]
        a.schemas={'ServerNotification':{'oneOf':[{'properties':{'method':{'enum':['remoteControl/status/changed']}}}]}}
        return a

    def resume(self):
        return {'threadId':r.THREAD,'excludeTurns':True,'cwd':str(r.c.TRIAL),'permissions':r.PERMISSION_PROFILE,
            'approvalPolicy':'on-request','model':r.p.MODEL,'config':{'model_reasoning_effort':r.p.EFFORT}}

    def test_authority_needs_exact_reviewed_candidate_and_current_owner_authorization(self):
        grant={'state':'active','contract_id':r.c.CONTRACT,'grant_id':'csp-css-trial-grant-v1','task_id':r.c.TASK,
            'root':str(r.c.ROOT),'head':r.c.HEAD,'trial_root':str(r.c.TRIAL),'paths':['progress_ui.css',*r.c.DOCS]}
        state={'linux_visual_workbench':{'contract':{'id':r.CONTRACT,'decision':'APPROVE','status':'APPROVED_EVIDENCE_ONLY'},
            'trial_registration':{'resume_calls':4},'authorization':{'conditional_pilot_write_grant':dict(grant,state='active_exact_css_trial',project_id='computer-study-plan')}}}
        auth=dict(self.authority,contract=r.CONTRACT,task_id=r.c.TASK,thread_id=r.THREAD,turn_id=r.TURN,
            model_turns_allowed=0,total_thread_limit=2,prior_resumes=4,total_resume_limit=5,total_model_turn_limit=1,intent_hash=r.INTENT_HASH,
            grant_hash=r.GRANT_HASH,profile_arguments=list(r.profile_arguments(r.c.TRIAL)),approved_candidate='candidate',
            approved_evidence='evidence',files={},task_hash='task',trial={},protected_pin={},source={},auth_identity=[],history_files={},helper_wall_seconds=120,renewal_allowed=False,auth_mode='ChatGPT')
        reg={**auth,'candidate_sha256':'candidate','evidence_sha256':'evidence'}
        def read(path,*args,**kwargs):
            return r.yaml.safe_dump(state)if path.name=='STATE.yaml'else json.dumps(reg)
        with mock.patch.object(Path,'read_text',read):
            r.authority_check(auth)
            for key,value in [('total_resume_limit',6),('model_turns_allowed',1),('turn_id','other'),('approved_candidate','other')]:
                with self.subTest(key=key),self.assertRaises(ServiceError):r.authority_check(dict(auth,**{key:value}))
            state['linux_visual_workbench']['authorization']['conditional_pilot_write_grant']['state']='revoked'
            with self.assertRaises(ServiceError):r.authority_check(auth)
        self.base.assert_not_called()

    def test_prospective_authority_expires_and_cannot_renew_or_accept_old_grant(self):
        for authority in [dict(self.authority,created_at=time.time()+1),
            dict(self.authority,expires_at=time.time()),dict(self.authority,expires_at=time.time()+901),
            {'expires_at':1791364398}]:
            with self.subTest(authority=authority),self.assertRaises(ServiceError):r.unexpired(authority)
        self.base.assert_not_called()

    def test_fifth_resume_requires_all_three_historical_count_facts(self):
        prior={'resume_calls_consumed':1,'status':'PARTIAL_BLOCKED','error':'DIAGNOSTIC_NOTIFICATION_REJECTED'}
        events=[{'data':{'method':'thread/resume'}}for _ in range(2)]
        with mock.patch.object(Path,'read_text',return_value=json.dumps(prior)):
            r.history_check(events,{'prior_resumes':4})
            for es,auth in [(events[:1],{'prior_resumes':4}),(events,{'prior_resumes':3})]:
                with self.assertRaises(ServiceError):r.history_check(es,auth)
        self.base.assert_not_called()

    def test_unallowlisted_notice_retained_before_failure_without_body(self):
        self.a.schemas={'ServerNotification':{'oneOf':[{'properties':{'method':{'enum':['thread/tokenUsage/updated']}}}]}}
        notice={'method':'thread/tokenUsage/updated','params':{'threadId':r.THREAD,'turnId':r.TURN,'tokenUsage':{'secret':'NEVER_SAVE_THIS'}}}
        with self.assertRaises(ServiceError):self.a._notification_observation(notice,'validated')
        saved=(self.folder/'notification-metadata-v11-1.json').read_text()
        self.assertNotIn('NEVER_SAVE_THIS',saved);self.assertEqual(json.loads(saved)['method_category'],'rejected_known')
        self.base.assert_not_called()

    def test_allowlisted_cross_binding_retained_then_rejected(self):
        self.a.schemas={'ServerNotification':{'oneOf':[{'properties':{'method':{'enum':['account/updated']}}}]}}
        for params in [{'threadId':'other'},{'threadId':r.THREAD,'turnId':'other'},{'threadId':r.THREAD,'thread':{'id':'other'}}]:
            with self.assertRaises(ServiceError):self.a._notification_observation({'method':'account/updated','params':params},'validated')
        facts=[json.loads(path.read_text())for path in self.folder.glob('notification-metadata-v11-*.json')]
        self.assertEqual(len(facts),3);self.assertTrue(all(any(b['wrong']for b in f['binding'].values())for f in facts))
        self.base.assert_not_called()

    def usage_fixture(self):
        names=['thread/tokenUsage/updated','thread/status/changed','configWarning']
        self.a.schemas={'ServerNotification':{'oneOf':[{'type':'object','required':['method','params'],
            'properties':{'method':{'enum':[name]},'params':{'type':'object'}},'additionalProperties':False}for name in names]}}
        self.a.schemas.update({r.NOTIFICATIONS[name]:{'type':'object'}for name in names if name in r.NOTIFICATIONS})
        self.a.phase='baseline_drain';self.a.baseline_completion_verified=True;self.a.active_profile=r.PERMISSION_PROFILE
        return {'method':'thread/tokenUsage/updated','params':{'threadId':r.THREAD,'turnId':r.TURN,
            'tokenUsage':{'inputTokens':123,'secret':'NEVER_PERSIST_USAGE'}}}

    def test_completed_usage_drained_once_separate_from_following_queries(self):
        note=self.usage_fixture()
        for value in [{'method':'configWarning','params':{}},note,{'method':'thread/status/changed','params':{'threadId':r.THREAD}}]:self.a.inbox.put(value)
        result=self.a.drain_baseline(r.THREAD)
        self.assertEqual(result['usage_notification_count'],1);self.assertEqual(self.a.phase,'post_resume_read')
        facts=list(self.folder.glob('notification-metadata-v11-*.json'));self.assertEqual(len(facts),3)
        self.assertTrue(all('NEVER_PERSIST_USAGE'not in p.read_text()and '123'not in p.read_text()for p in facts))
        with self.assertRaises(ServiceError):self.a._notification_observation(note,'validated')
        self.base.assert_not_called()

    def test_empty_completed_baseline_is_permitted(self):
        self.usage_fixture();self.assertEqual(self.a.drain_baseline(r.THREAD)['usage_notification_count'],0)
        self.base.assert_not_called()

    def test_duplicate_baseline_persists_rejected_note_then_throws(self):
        note=self.usage_fixture();self.a._notification_observation(note,'validated')
        with self.assertRaises(ServiceError):self.a._notification_observation(note,'validated')
        self.assertEqual(self.a.baseline_count,1)
        second=json.loads((self.folder/'notification-metadata-v11-2.json').read_text())
        self.assertEqual(second['method_category'],'rejected_known');self.base.assert_not_called()

    def test_baseline_requires_unique_completed_proof_and_exact_both_bindings(self):
        note=self.usage_fixture()
        self.a.baseline_completion_verified=False
        with self.assertRaises(ServiceError):self.a._notification_observation(note,'validated')
        self.a.baseline_completion_verified=True
        for params in [{'threadId':r.THREAD},{'turnId':r.TURN},{'threadId':'other','turnId':r.TURN},
            {'threadId':r.THREAD,'turnId':'new-turn'},{'threadId':r.THREAD,'turnId':None}]:
            with self.assertRaises(ServiceError):self.a._notification_observation({'method':note['method'],'params':params},'validated')
        self.assertEqual(self.a.baseline_count,0);self.base.assert_not_called()

    def test_baseline_persistence_failure_prevents_admission(self):
        note=self.usage_fixture();self.a.notification_recorder=mock.Mock(side_effect=OSError('secret-error-body'))
        with self.assertRaises(OSError):self.a._notification_observation(note,'validated')
        self.assertEqual(self.a.baseline_count,0);self.assertEqual(self.a.notification_sequence,0)

    def test_pending_frame_retained_before_terminal_rejection_never_success(self):
        self.a.proc=SimpleNamespace(poll=lambda:0);self.a.failed='CODEX_STREAM_EOF'
        self.a.remote_control_status='disabled';self.a.mcp_startup_seen=False
        self.a.rpc_pending[7]={'method':'thread/read','phase':'post_resume_read','sent':True,'response_schema_valid':False}
        self.a.schemas={r.RESPONSES['thread/read']:{'type':'object','required':['thread']}}
        self.a.inbox.put({'id':7,'result':{'thread':{'secret':'NEVER_PERSIST_RESPONSE'}}})
        with self.assertRaises(ServiceError):r.final_capability_state(self.a,{})
        fact=json.loads((self.folder/'rpc-terminal-v11-1.json').read_text())
        self.assertEqual(fact['method'],'thread/read');self.assertEqual(fact['request_id'],7)
        self.assertTrue(fact['schema_valid_response_received']);self.assertFalse(fact['accepted_as_success'])
        self.assertNotIn('NEVER_PERSIST_RESPONSE',json.dumps(fact));self.base.assert_not_called()

    def test_unknown_or_invalid_pending_frame_retains_no_arbitrary_identifiers(self):
        self.a.rpc_pending[7]={'method':'thread/read','phase':'post_resume_read','response_schema_valid':False}
        self.a.schemas={r.RESPONSES['thread/read']:{'type':'object','required':['thread']}}
        for frame in [{'id':7,'result':{}},{'id':'private-id','result':{'secret':'NEVER_SAVE'}}]:self.a.pending_frame_observation(frame)
        facts=[json.loads(p.read_text())for p in sorted(self.folder.glob('rpc-terminal-v11-*.json'))]
        self.assertTrue(all(not f['schema_valid_response_received']and not f['accepted_as_success']for f in facts))
        self.assertIsNone(facts[1]['request_id']);self.assertNotIn('private-id',json.dumps(facts));self.assertNotIn('NEVER_SAVE',json.dumps(facts))

    def test_schema_valid_rpc_response_retains_only_safe_outcome(self):
        from hub.codex_adapter import PARAMS
        self.a.next_id=6;self.a.phase='post_resume_read'
        self.a.schemas={PARAMS['thread/read']:{'type':'object'},r.RESPONSES['thread/read']:{'type':'object','required':['thread']}}
        self.a.inbox.put({'id':7,'result':{'thread':{'id':r.THREAD,'secret':'PRIVATE_RESPONSE_VALUE'}}})
        self.base.side_effect=lambda method,params,timeout:REAL_CALL(self.a,method,params,timeout)
        with mock.patch.object(r.AppServerAdapter,'_send',return_value=None):
            result=self.a.call('thread/read',{'threadId':r.THREAD,'includeTurns':False})
        self.assertEqual(result['thread']['id'],r.THREAD)
        saved=(self.folder/'rpc-outcome-v11-7.json').read_text();fact=json.loads(saved)
        self.assertTrue(fact['response_schema_valid']);self.assertTrue(fact['call_returned'])
        self.assertEqual(fact['phase'],'post_resume_read');self.assertNotIn('PRIVATE_RESPONSE_VALUE',saved)

    def test_rpc_intent_and_failed_outcome_retained_before_throw(self):
        with mock.patch.object(r.AppServerAdapter,'_send',return_value=None):
            self.a._send({'id':7,'method':'thread/read','params':{}})
        self.a.next_id=6
        self.base.side_effect=ServiceError('CODEX_TIMEOUT')
        with self.assertRaises(ServiceError):self.a.call('thread/read',{'threadId':r.THREAD,'includeTurns':False})
        fact=json.loads((self.folder/'rpc-outcome-v11-7.json').read_text())
        self.assertEqual(fact['request_id'],7);self.assertFalse(fact['response_schema_valid']);self.assertFalse(fact['call_returned'])
        self.assertTrue((self.folder/'rpc-intent-v11-7.json').exists())

    def test_wall_deadline_prevents_native_rpc_and_watchdog_closes_exact_adapter(self):
        self.a.wall_deadline=0
        with self.assertRaises(ServiceError):self.a.call('config/read',{'includeLayers':False})
        with mock.patch.object(self.a,'close')as close:
            self.a._deadline_close();close.assert_called_once_with()
        self.assertTrue(self.a.wall_exceeded);self.base.assert_not_called()

    def test_execution_methods_and_unbound_metadata_are_rejected(self):
        for method in ['thread/start','thread/list','turn/start','turn/steer','turn/interrupt','fs/writeFile']:
            with self.subTest(method=method),self.assertRaises(ServiceError):self.a.call(method,{})
        for method,params in [('thread/read',{'threadId':'other','includeTurns':False}),
            ('thread/read',{'threadId':r.THREAD,'includeTurns':True}),('mcpServerStatus/list',{'threadId':None}),
            ('experimentalFeature/list',{'threadId':r.THREAD,'limit':100,'cursor':None,'extra':True})]:
            with self.subTest(method=method),self.assertRaises(ServiceError):self.a.call(method,params)
        self.base.assert_not_called()
        with self.assertRaises(ServiceError):self.a._send({'method':'turn/start','params':{}})

    def test_resume_pins_id_cwd_model_effort_profile_and_is_consumed_once(self):
        for key,value in [('threadId','other'),('cwd','/tmp'),('excludeTurns',False),('model','other'),
            ('config',{'model_reasoning_effort':'ultra'}),('permissions','other')]:
            params=self.resume();params[key]=value
            with self.subTest(key=key),self.assertRaises(ServiceError):self.a.call('thread/resume',params)
        self.base.assert_not_called();self.a.call('thread/resume',self.resume())
        saved=(self.folder/'evidence-resume-intent-v11.json').read_bytes()
        with self.assertRaises(ServiceError):self.a.call('thread/resume',self.resume())
        self.assertEqual(self.base.call_count,1)
        self.assertEqual(saved,(self.folder/'evidence-resume-intent-v11.json').read_bytes())

    def test_failed_resume_is_not_replayed(self):
        self.base.side_effect=ServiceError('fixture-response-lost')
        with self.assertRaises(ServiceError):self.a.call('thread/resume',self.resume())
        with self.assertRaises(ServiceError):self.a.call('thread/resume',self.resume())
        self.assertEqual(self.base.call_count,1)
        self.assertTrue((self.folder/'evidence-resume-intent-v11.json').is_file())

    def test_fixed_command_and_config_read_once(self):
        params={'command':r.probe_argv(),'cwd':str(r.c.TRIAL),'permissionProfile':r.PERMISSION_PROFILE,'timeoutMs':5000,'outputBytesCap':4096}
        changed=dict(params,command=['/bin/sh','-c','anything'])
        with self.assertRaises(ServiceError):self.a.call('command/exec',changed)
        self.a.call('command/exec',params)
        with self.assertRaises(ServiceError):self.a.call('command/exec',params)
        self.a.call('config/read',{'includeLayers':False})
        with self.assertRaises(ServiceError):self.a.call('config/read',{'includeLayers':False})
        self.assertEqual(self.base.call_count,2)

    def test_expiry_prevents_further_rpc(self):
        self.authority['expires_at']=0
        with self.assertRaises(ServiceError):self.a.call('thread/resume',self.resume())
        self.base.assert_not_called();self.assertFalse((self.folder/'evidence-resume-intent-v11.json').exists())

    def test_seventh_disabled_mcp_is_rejected_after_filtered_six_names(self):
        fact={'method':'mcpServerStatus/list','thread_id':r.THREAD,'has_next_page':False,
            'servers':[{'name':n}for n in r.REGISTERED_SERVERS],'unknown_server_count':0}
        self.assertEqual(len(r.loaded_six_mcp([fact])),6)
        fact['unknown_server_count']=1
        with self.assertRaises(ServiceError):r.loaded_six_mcp([fact])
        fact['unknown_server_count']=0;fact['servers'].append({'name':r.REGISTERED_SERVERS[0]})
        with self.assertRaises(ServiceError):r.loaded_six_mcp([fact])

    def test_late_preview14_change_blocks_and_retains_idle_and_capabilities(self):
        t={'intent':{'grant':{}}};s=SimpleNamespace(task=lambda _:t)
        a=SimpleNamespace(proc=SimpleNamespace(returncode=0,poll=lambda:0),close=lambda:None,
            audit=[],capability_observations=[{'method':'experimentalFeature/list'}])
        self.closed_adapter(a);rec={'status':'PASS_CURRENT_EVIDENCE_ONLY','current_idle':{'status':'idle'}}
        config={'identity':[],'_private_content_hash':'private'}
        with mock.patch.object(r,'source_snapshot',return_value={}),mock.patch.object(r.c,'placeholder_snapshot',return_value={}),\
            mock.patch.object(r,'auth_identity',return_value=[]),mock.patch.object(r.p,'verify',return_value=config),\
            mock.patch.object(r,'mapping_check',side_effect=ServiceError('REAL_PREVIEW_VERSION_STALE')):
            r.finish(a,rec,self.folder,dict(self.authority,protected_pin={},source={},auth_identity=[],history_files={},helper_wall_seconds=120,renewal_allowed=False,auth_mode='ChatGPT'),s,t,{},{},config,[])
        saved=json.loads((self.folder/'capability-recovery-result-v11.json').read_text())
        self.assertEqual(saved['status'],'PARTIAL_BLOCKED');self.assertFalse(saved['readonly_preview14_unchanged'])
        self.assertEqual(saved['current_idle'],{'status':'idle'});self.assertEqual(saved['capability_facts'],a.capability_observations)
        self.assertEqual(saved['closure_errors'],[{'check':'readonly_preview14_unchanged','code':'REAL_PREVIEW_VERSION_STALE'}])

    def test_late_remote_and_mcp_flags_never_pass_closure(self):
        for remote,startup in [('enabled',False),('disabled',True)]:
            a=self.closed_adapter(SimpleNamespace(proc=SimpleNamespace(poll=lambda:0)))
            a.remote_control_status=remote;a.mcp_startup_seen=startup
            with self.subTest(remote=remote,startup=startup),self.assertRaises(ServiceError):r.final_capability_state(a,{})

    def test_closed_reader_queued_remote_notice_is_validated_retained_and_rejected(self):
        self.a.proc=SimpleNamespace(poll=lambda:0);self.a.failed='CODEX_STREAM_EOF'
        self.a.remote_control_status='disabled';self.a.mcp_startup_seen=False
        self.a.schemas={'ServerNotification':{'oneOf':[{'type':'object','required':['method','params'],
            'properties':{'method':{'enum':['remoteControl/status/changed']},'params':{'type':'object'}},'additionalProperties':False}]}}
        # notification_fact will reject enabled; the independently latched fact
        # is still persisted before that rejection, with no response or RPC.
        self.a.inbox.put({'method':'remoteControl/status/changed','params':{'status':'enabled'}})
        with self.assertRaises(ServiceError):r.final_capability_state(self.a,{})
        self.assertEqual(self.a.remote_control_status,'enabled')
        saved=json.loads((self.folder/'capability-current-v11-1.json').read_text())
        self.assertEqual(saved['status'],'enabled');self.base.assert_not_called()

    def test_probe_has_no_mutating_operations_and_rejects_partial_proofs(self):
        import ast
        script=r.probe_argv()[-1];tree=ast.parse(script)
        calls=[n.func.attr for n in ast.walk(tree)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)]
        self.assertFalse({'write','mkdir','unlink','remove','rename','symlink','truncate','fsync','connect'}&set(calls))
        self.assertNotIn('O_CREAT',script);self.assertNotIn('O_TRUNC',script);self.assertNotIn('O_APPEND',script)
        fact={'css_sha256':r.c.AFTER_SHA,'trial_bytes_read':143428,'protected_bytes_read':0,
            'denials':{n+'_'+m:13 for n in ['canonical','credential','parent_canary','tmp','sibling','symlink']for m in ['read','write']},
            'parent_open_errno':13,'parent_list_errno':13,'network_errno':1}
        def result(value):return {'exitCode':0,'stdout':json.dumps(value)}
        self.assertEqual(r.probe_fact(result(fact)),fact)
        fact['denials']['canonical_write']=0
        with self.assertRaises(ServiceError):r.probe_fact(result(fact))
        fact['denials']['canonical_write']=13;fact['css_sha256']=r.c.BEFORE_SHA
        with self.assertRaises(ServiceError):r.probe_fact(result(fact))

    def test_late_preservation_errors_retain_current_facts_and_block(self):
        proc=SimpleNamespace(returncode=0,poll=lambda:0)
        a=SimpleNamespace(proc=proc,close=lambda:None,audit=[{'method':'thread/resume'}],resumed=True,
            capability_observations=[{'method':'mcpServerStatus/list','servers':[]}])
        self.closed_adapter(a);record={'status':'PASS_CURRENT_EVIDENCE_ONLY','current_idle':{'thread_id':r.THREAD,'status':'idle'}}
        t={'fixture':'unchanged','intent':{'grant':{}}};s=SimpleNamespace(task=lambda _:t)
        with mock.patch.object(r,'source_snapshot',side_effect=ServiceError('source-late-error')),\
            mock.patch.object(r,'mapping_check',return_value=None),\
            mock.patch.object(r.c,'placeholder_snapshot',side_effect=OSError('do not retain body')),\
            mock.patch.object(r,'auth_identity',return_value=[]),\
            mock.patch.object(r.p,'verify',side_effect=ServiceError('config-late-error')):
            r.finish(a,record,self.folder,dict(self.authority,protected_pin={},source={},auth_identity=[],history_files={},helper_wall_seconds=120,renewal_allowed=False,auth_mode='ChatGPT'),s,t,{},{},{},[])
        saved=json.loads((self.folder/'capability-recovery-result-v11.json').read_text())
        self.assertEqual(saved['status'],'PARTIAL_BLOCKED');self.assertEqual(saved['current_idle'],record['current_idle'])
        self.assertEqual(saved['capability_facts'],a.capability_observations);self.assertEqual(len(saved['closure_errors']),3)
        self.assertNotIn('do not retain body',json.dumps(saved));self.assertEqual(saved['resume_calls_consumed'],1)

    def test_nonzero_helper_exit_never_passes(self):
        a=SimpleNamespace(proc=SimpleNamespace(returncode=1,poll=lambda:1),close=lambda:None,audit=[],capability_observations=[])
        self.closed_adapter(a);rec={'status':'PASS_CURRENT_EVIDENCE_ONLY'};t={'intent':{'grant':{}}};s=SimpleNamespace(task=lambda _:t)
        config={'identity':[],'_private_content_hash':'private'}
        with mock.patch.object(r,'source_snapshot',return_value={}),mock.patch.object(r,'mapping_check',return_value=None),mock.patch.object(r.c,'placeholder_snapshot',return_value={}),\
            mock.patch.object(r,'auth_identity',return_value=[]),mock.patch.object(r.p,'verify',return_value=config):
            r.finish(a,rec,self.folder,dict(self.authority,protected_pin={},source={},auth_identity=[],history_files={},helper_wall_seconds=120,renewal_allowed=False,auth_mode='ChatGPT'),s,t,{},{},config,[])
        self.assertEqual(rec['status'],'PARTIAL_BLOCKED');self.assertNotIn('private',json.dumps(rec))

if __name__=='__main__':unittest.main()
