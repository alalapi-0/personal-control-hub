"""Synthetic byte streams and process handles only; never a native/provider launch."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from unittest.mock import patch, Mock

from hub import official_execution as module
from hub.official_execution import ExecutionConfig, OfficialExecution
from hub.service_contract import ServiceError

THREAD='00000000-0000-7000-8000-000000000001'
SESSION='00000000-0000-7000-8000-000000000002'
TURN='00000000-0000-7000-8000-000000000003'
OTHER='00000000-0000-7000-8000-000000000004'


class Wire:
    def __init__(self):
        self.identity={'pid':12345,'start_ticks':123456,'pgid':12345}
        self.frames=[];self.sent=[];self.closes=0;self.close_status='CLOSED'
    def send(self,data,deadline):self.sent.append(json.loads(data))
    def receive(self,deadline):
        if not self.frames:return None
        frame=self.frames.pop(0)
        if isinstance(frame,BaseException):raise frame
        return frame if type(frame) is bytes or frame is None else json.dumps(frame).encode()
    def close(self):
        self.closes+=1
        return {'status':self.close_status,'identity':dict(self.identity)}


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='hub-execution-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.schemas=self.root/'schemas';self.schemas.mkdir()
        (self.schemas/'v1').mkdir();(self.schemas/'v2').mkdir()
        self.cwd=self.root/'work';self.cwd.mkdir();self.binary=self.root/'synthetic-binary';self.binary.write_text('not executed')
        schemas={name:{'type':'object'} for name in module.SCHEMA_PINS}
        schemas['InitializeResponse']['properties']={'userAgent':{'type':'string'}}
        schemas['ThreadResumeResponse']={'type':'object','properties':{key:{}for key in
            ['thread','cwd','sandbox','approvalPolicy','approvalsReviewer']},'required':['thread','cwd','sandbox','approvalPolicy','approvalsReviewer']}
        schemas['TurnStartResponse']={'type':'object','properties':{'turn':{}},'required':['turn']}
        schemas['TurnInterruptResponse']={'type':'object','properties':{}}
        for name,keys in [('RemoteControlStatusChangedNotification',['status','environmentId','installationId','serverName']),
                          ('ThreadStatusChangedNotification',['threadId','status']),('TurnStartedNotification',['threadId','turn']),
                          ('TurnCompletedNotification',['threadId','turn']),('ItemCompletedNotification',['threadId','turnId','item','completedAtMs'])]:
            schemas[name]={'type':'object','properties':{key:{} for key in keys}}
        def obj(properties,required=()):return {'type':'object','properties':properties,'required':list(required)}
        text={'type':'string'};integer={'type':'integer'}
        schemas['AccountUpdatedNotification']=obj({'authMode':{'enum':[None,'apikey','chatgpt','chatgptAuthTokens']},
                                                  'planType':{'enum':[None,'free','pro','unknown']}})
        schemas['AccountRateLimitsUpdatedNotification']=obj({'rateLimits':obj({
            'limitId':text,'planType':text,'credits':obj({'balance':text,'hasCredits':{'type':'boolean'},
                'unlimited':{'type':'boolean'}},('hasCredits','unlimited')),
            'primary':obj({'usedPercent':integer},('usedPercent',))})},('rateLimits',))
        usage=obj({k:integer for k in ('cachedInputTokens','inputTokens','outputTokens',
            'reasoningOutputTokens','totalTokens')},('cachedInputTokens','inputTokens','outputTokens','reasoningOutputTokens','totalTokens'))
        schemas['ThreadTokenUsageUpdatedNotification']=obj({'threadId':text,'turnId':text,
            'tokenUsage':obj({'last':usage,'total':usage},('last','total'))},('threadId','turnId','tokenUsage'))
        variants=[]
        for kind,extra in [('agentMessage',{'text':text}),('commandExecution',{'command':text}),
                           ('mcpToolCall',{'arguments':{}}),('collabAgentToolCall',{'prompt':text})]:
            variants.append(obj({'id':text,'type':{'enum':[kind]},**extra},('id','type',*extra)))
        schemas['ItemStartedNotification']=obj({'threadId':text,'turnId':text,'startedAtMs':integer,
            'item':{'oneOf':variants}},('threadId','turnId','startedAtMs','item'))
        schemas['AgentMessageDeltaNotification']=obj({'threadId':text,'turnId':text,'itemId':text,
            'delta':text},('threadId','turnId','itemId','delta'))
        schemas['McpServerStatusUpdatedNotification']=obj({'name':text,
            'status':{'enum':['starting','ready','failed','cancelled']},
            'threadId':{'type':['string','null']},'error':{'type':['string','null']},
            'failureReason':{'enum':[None,'reauthenticationRequired']}},('name','status'))
        self.pins={}
        for name,schema in schemas.items():
            path=self.schemas/('v1'if name.startswith('Initialize')else'v2')/(name+'.json')
            path.write_text(json.dumps(schema));self.pins[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        self.addCleanup(patch.stopall)
        patch.object(module,'SCHEMA_PINS',self.pins).start()
        patch.object(module,'_official_binary_hash',return_value=module.BINARY_SHA256).start()
        self.now=[0.0];self.wire=Wire();self.factory=Mock(return_value=self.wire)
        self.config=ExecutionConfig(THREAD,self.cwd,self.schemas,self.binary)
        self.client=OfficialExecution(self.config,enabled=True,wire_factory=self.factory,clock=lambda:self.now[0])
    def fresh(self):
        self.wire=Wire();self.factory=Mock(return_value=self.wire)
        self.client=OfficialExecution(self.config,enabled=True,wire_factory=self.factory,clock=lambda:self.now[0])

    def test_resume_core_mismatch_missing_and_session_variants(self):
        for change in ({'id':OTHER},{'sessionId':'not-a-native-session'},{'sessionId':False},{'turns':[{'id':TURN}]}):
            with self.subTest(change=change):
                self.fresh();self.open();result=self.resume_result();result['thread'].update(change)
                self.assert_unknown(lambda:self.resume(result))
        self.fresh();self.open();result=self.resume_result();result['thread']['sessionId']=None
        self.assertIsNone(self.resume(result)['session_id']);self.client.close()
        for policy in ({'type':'workspaceWrite'},{'type':'readOnly','networkAccess':True},
                       {'type':'readOnly','writableRoots':['/forged']}):
            self.fresh();self.open();result=self.resume_result();result['sandbox']=policy
            self.assert_unknown(lambda:self.resume(result))

    def test_unknown_requests_and_notifications_have_no_response(self):
        for method in ('item/tool/requestUserInput','item/fileChange/requestApproval','permission/request','goal/status/changed'):
            self.fresh();self.open();before=len(self.wire.sent)
            self.wire.frames.append({'id':99,'method':method,'params':{}})
            self.assert_unknown(self.client.resume)
            self.assertEqual(len(self.wire.sent),before+1)
            self.assertEqual(self.wire.sent[-1]['method'],'thread/resume')

    def test_each_effectful_method_is_single_use_after_success_or_unknown(self):
        self.open();self.resume();self.start();self.response(4,{})
        self.client.interrupt(TURN);before=len(self.wire.sent)
        with self.assertRaises(ServiceError):self.client.interrupt(TURN)
        self.assertEqual(len(self.wire.sent),before)
        self.assertEqual(sum(f['method']=='turn/interrupt' for f in self.wire.sent),1)
        self.fresh();self.open();self.resume();before=len(self.wire.sent)
        with self.assertRaises(ServiceError):self.client.start('PRIVATE',write_paths=['/forged'])
        self.assertEqual(len(self.wire.sent),before)

    def response(self,ident,result):self.wire.frames.append({'id':ident,'result':result})
    def open(self):self.response(1,{'userAgent':'synthetic'});self.client.open()
    def thread(self):return {'id':THREAD,'sessionId':SESSION,'cwd':str(self.cwd),'turns':[],'status':{'type':'idle'}}
    def resume_result(self):return {'thread':self.thread(),'cwd':str(self.cwd),'sandbox':{'type':'readOnly','networkAccess':False},'approvalPolicy':'never','approvalsReviewer':'user'}
    def resume(self,result=None):self.response(2,result or self.resume_result());return self.client.resume()
    def turn(self,ident=TURN,status='inProgress'):return {'id':ident,'items':[],'status':status}
    def notify(self,method,params):self.wire.frames.append({'method':method,'params':params})
    def start(self):self.response(3,{'turn':self.turn()});return self.client.start('PRIVATE PROMPT')
    def assert_unknown(self,fn):
        with self.assertRaises(ServiceError)as error:fn()
        self.assertEqual(error.exception.outcome,'UNKNOWN');self.assertEqual(self.wire.closes,1)
        self.assertEqual(self.client.closure['status'],'CLOSED')
        return error.exception

    def test_optin_and_default_readonly_sequence_independent_ids(self):
        with self.assertRaises(ServiceError):OfficialExecution(self.config)
        self.open();result=self.resume();self.assertEqual(result['session_id'],SESSION)
        self.assertNotEqual(result['session_id'],result['thread_id'])
        self.start();self.response(4,{});self.client.interrupt(TURN)
        self.notify('turn/started',{'threadId':THREAD,'turn':self.turn()})
        self.notify('turn/completed',{'threadId':THREAD,'turn':self.turn(status='interrupted')})
        result=self.client.wait();self.assertEqual(result['terminal'],'interrupted')
        self.assertEqual(result['closure']['status'],'CLOSED');self.assertEqual(self.wire.closes,1)
        self.assertEqual([f['method']for f in self.wire.sent],['initialize','initialized','thread/resume','turn/start','turn/interrupt'])
        self.assertEqual(self.wire.sent[0]['params']['capabilities'],{'experimentalApi':False,'explicitGatewayOauth':True})
        self.assertEqual(self.wire.sent[2]['params']['sandbox'],'read-only')
        self.assertEqual(self.wire.sent[3]['params']['sandboxPolicy'],{'type':'readOnly','networkAccess':False})
        self.assertFalse(result['exact_leaf_isolation']);self.assertFalse(result['native_execution_admitted'])
        self.assertNotIn('PRIVATE',json.dumps(result))
        result['process']['pid']=999;self.assertEqual(self.client.process_identity['pid'],12345)

    def test_nullable_session_is_not_thread_fallback(self):
        self.open();result=self.resume_result();result['thread'].pop('sessionId');result=self.resume(result)
        self.assertIsNone(result['session_id']);self.assertEqual(result['session_reason'],'NATIVE_SESSION_UNAVAILABLE');self.client.close()

    def test_resume_mismatched_binding_or_wider_echo_is_unknown(self):
        self.open();result=self.resume_result();result['thread']['cwd']='/forged'
        self.assert_unknown(lambda:self.resume(result));self.assertFalse(self.client.resumed)

    def test_missing_thread_and_unknown_session_or_active_status_refuse(self):
        self.open();result=self.resume_result();result['thread']['status']={'type':'active','activeFlags':[]}
        self.assert_unknown(lambda:self.resume(result))

    def test_capability_rejection_is_before_write_and_closes(self):
        self.open();before=len(self.wire.sent)
        with self.assertRaises(ServiceError):self.client.resume(read_paths=['README.md'])
        self.assertEqual(len(self.wire.sent),before);self.assertEqual(self.wire.closes,1)

    def test_wider_modes_and_leaf_claims_never_write(self):
        for kwargs in ({'sandbox':'workspaceWrite'},{'sandbox':'dangerFullAccess'},{'write_paths':[]},{'exact_input_leaves':True}):
            with self.subTest(kwargs=kwargs):
                client=OfficialExecution(self.config,enabled=True,wire_factory=self.factory)
                with self.assertRaises(ServiceError):client.resume(**kwargs)
        self.assertEqual(self.wire.sent,[]);self.factory.assert_not_called()

    def test_no_arbitrary_rpc_forwarding_or_start_before_resume(self):
        self.open();before=len(self.wire.sent)
        for method in ('thread/start','thread/read','command/exec','account/read','config/read','turn/steer'):
            with self.assertRaises(ServiceError):self.client._rpc(method,{})
        with self.assertRaises(ServiceError):self.client._rpc('thread/resume',{'threadId':OTHER})
        self.assertEqual(len(self.wire.sent),before)
        with self.assertRaises(ServiceError):self.client.start('PRIVATE')
        self.assertEqual(self.wire.closes,1)

    def test_schema_or_binary_drift_prevents_process(self):
        (self.schemas/'v1/InitializeParams.json').write_text('{}')
        with self.assertRaises(ServiceError):self.client.open()
        self.factory.assert_not_called()

    def test_approval_server_request_fails_without_any_permission_response(self):
        self.open();self.wire.frames.append({'id':99,'method':'item/commandExecution/requestApproval','params':{'private':'PRIVATE'}})
        self.assert_unknown(self.client.resume)
        self.assertEqual([f['method']for f in self.wire.sent],['initialize','initialized','thread/resume'])
        self.assertNotIn('PRIVATE',json.dumps(self.client.snapshot()))

    def test_malformed_duplicate_response_eof_timeout_have_no_replay(self):
        self.open();self.wire.frames.append(b'{"id":2,"id":2,"result":{}}')
        self.assert_unknown(self.client.resume);count=len(self.wire.sent)
        with self.assertRaises(ServiceError):self.client.resume()
        self.assertEqual(len(self.wire.sent),count)
        self.assertEqual(self.client.failure['outcome'],'UNKNOWN')

    def test_postsend_eof_or_sensitive_exception_is_unknown(self):
        self.open();self.wire.frames.append(EOFError('PRIVATE provider body'))
        error=self.assert_unknown(self.client.resume)
        self.assertNotIn('PRIVATE',str(error.as_dict()));self.assertNotIn('PRIVATE',json.dumps(self.client.snapshot()))

    def test_timeout_closes_and_keeps_charge_sent(self):
        self.open();self.assert_unknown(self.client.resume)
        self.assertEqual(self.client.sent.count('thread/resume'),1)

    def test_duplicate_reply_is_conflict_not_a_second_start(self):
        self.open();self.resume();self.response(2,self.resume_result())
        self.assert_unknown(lambda:self.client.start('PRIVATE'))
        self.assertEqual(self.client.sent.count('turn/start'),1)

    def test_notification_first_turn_id_must_match_response(self):
        self.open();self.resume();self.notify('turn/started',{'threadId':THREAD,'turn':self.turn()})
        self.response(3,{'turn':self.turn(OTHER)})
        self.assert_unknown(lambda:self.client.start('PRIVATE'))

    def test_cross_thread_or_turn_and_reordered_completion_fail(self):
        self.open();self.resume();self.notify('turn/started',{'threadId':OTHER,'turn':self.turn()})
        self.assert_unknown(lambda:self.client.start('PRIVATE'))

    def test_interrupt_never_guesses_or_repeats(self):
        self.open();self.resume();self.start();before=len(self.wire.sent)
        with self.assertRaises(ServiceError):self.client.interrupt(OTHER)
        self.assertEqual(len(self.wire.sent),before)

    def test_notification_flood_and_global_deadline_are_bounded(self):
        self.open()
        for _ in range(module.MAX_NOTIFICATIONS+1):
            self.notify('remoteControl/status/changed',{'status':'connected'})
        self.assert_unknown(self.client.resume)
        self.assertEqual(len(self.client.events),module.MAX_NOTIFICATIONS)

    def test_rpc_error_label_is_not_conclusive_refusal_and_raw_error_is_discarded(self):
        self.open();self.wire.frames.append({'id':2,'error':{'code':-32601,'message':'PRIVATE REFUSAL'}})
        with self.assertRaises(ServiceError)as error:self.client.resume()
        self.assertEqual(error.exception.code,'OFFICIAL_RPC_REFUSED')
        self.assertEqual(error.exception.outcome,'UNKNOWN');self.assertEqual(self.wire.closes,1)
        self.assertNotIn('PRIVATE',json.dumps(error.exception.as_dict()))
        before=copy.deepcopy(self.wire.sent)
        with self.assertRaises(ServiceError)as again:self.client.resume()
        self.assertEqual(again.exception.outcome,'UNKNOWN')
        self.assertEqual(self.wire.sent,before)
        self.assertEqual(self.wire.closes,1)

    def assert_rpc_error_unknown(self, method, code, close_status='CLOSED'):
        self.fresh();self.open()
        if method != 'thread/resume':self.resume()
        if method == 'turn/interrupt':self.start()
        self.wire.close_status=close_status
        ident=len(self.client.sent)+1
        self.wire.frames.append({'id':ident,'error':{'code':code,
            'message':'PRIVATE PROVIDER ERROR','data':{'prompt':'PRIVATE PROMPT',
                'credential':'PRIVATE SECRET','claimed_no_effect':True}}})
        call={'thread/resume':self.client.resume,
              'turn/start':lambda:self.client.start('PRIVATE PROMPT'),
              'turn/interrupt':lambda:self.client.interrupt(TURN)}[method]
        with self.assertRaises(ServiceError)as error:call()
        self.assertEqual(error.exception.code,'OFFICIAL_RPC_REFUSED')
        self.assertEqual(error.exception.outcome,'UNKNOWN')
        self.assertEqual(self.client.failure['outcome'],'UNKNOWN')
        self.assertEqual(self.client.closure['status'],close_status)
        self.assertEqual(self.wire.closes,1)
        self.assertEqual(sum(f['method']==method for f in self.wire.sent),1)
        sent=copy.deepcopy(self.wire.sent);failure=copy.deepcopy(self.client.failure)
        for retry in (call,lambda:self.client.start('PRIVATE RETRY')):
            with self.assertRaises(ServiceError)as again:retry()
            self.assertEqual(again.exception.outcome,'UNKNOWN')
            self.assertEqual(self.client.failure,failure)
            self.assertEqual(self.wire.sent,sent)
            self.assertEqual(self.wire.closes,1)
        self.assertNotIn('PRIVATE',json.dumps(self.client.snapshot()))
        self.assertNotIn('PRIVATE',json.dumps(error.exception.as_dict()))
        self.assertNotIn('claimed_no_effect',json.dumps(self.client.snapshot()))

    def test_resume_internal_rpc_error_cannot_prove_no_effect(self):
        self.assert_rpc_error_unknown('thread/resume',-32603)

    def test_start_internal_and_provider_rpc_errors_cannot_prove_no_effect(self):
        for code in (-32603,-32000):
            with self.subTest(code=code):self.assert_rpc_error_unknown('turn/start',code)

    def test_rpc_errors_preserve_uncertain_closure_and_unknown_on_retry(self):
        for method in ('thread/resume','turn/start','turn/interrupt'):
            with self.subTest(method=method):self.assert_rpc_error_unknown(method,-32603,'UNKNOWN')

    def test_interrupt_rpc_error_has_no_replay_or_raw_error_data(self):
        self.assert_rpc_error_unknown('turn/interrupt',-32000)

    def test_pre_send_capability_refusal_remains_distinct_from_sent_unknown(self):
        self.open();before=copy.deepcopy(self.wire.sent)
        with self.assertRaises(ServiceError)as error:self.client.resume(read_paths=['README.md'])
        self.assertEqual(error.exception.outcome,'NOT_COMMITTED')
        self.assertEqual(self.wire.sent,before)
        self.assertNotIn('thread/resume',self.client.sent)
        self.assertEqual(self.wire.closes,1)

    def test_caller_interrupt_and_uncertain_closure_are_preserved(self):
        self.open();self.wire.close_status='UNKNOWN';self.wire.frames.append(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):self.client.resume()
        self.assertEqual(self.client.closure['status'],'UNKNOWN');self.assertEqual(self.wire.closes,1)

    def test_process_identity_mismatch_refuses_and_closes_without_rpc(self):
        self.wire.identity['pgid']=98765
        with self.assertRaises(ServiceError):self.client.open()
        self.assertEqual(self.wire.sent,[]);self.assertEqual(self.wire.closes,1)

    def test_native_completion_and_item_order_are_auditable_without_bodies(self):
        self.open();self.resume();self.start()
        self.notify('turn/started',{'threadId':THREAD,'turn':self.turn()})
        self.notify('item/completed',{'threadId':THREAD,'turnId':TURN,'completedAtMs':1,
            'item':{'id':'synthetic-item','type':'agentMessage','text':'PRIVATE BODY'}})
        self.notify('turn/completed',{'threadId':THREAD,'turn':self.turn(status='completed')})
        result=self.client.wait();self.assertEqual(result['terminal'],'completed')
        self.assertEqual([e['sequence']for e in result['events']],[1,2,3])
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertEqual(result['closure']['status'],'CLOSED')

    def test_completion_before_any_native_start_identity_is_unknown(self):
        self.open();self.resume();self.notify('turn/completed',{'threadId':THREAD,'turn':self.turn(status='completed')})
        self.assert_unknown(lambda:self.client.start('PRIVATE'))

    def test_cross_turn_item_unknown_notification_and_extra_capability_refuse(self):
        self.open();self.resume();self.start()
        self.notify('item/completed',{'threadId':THREAD,'turnId':OTHER,'completedAtMs':1,'item':{'id':'item'}})
        self.assert_unknown(self.client.wait)

    def test_whole_deadline_not_extended_by_notifications(self):
        self.open();self.resume();self.start()
        original=self.wire.receive
        def receive(deadline):
            result=original(deadline);self.now[0]+=10;return result
        self.wire.receive=receive
        for _ in range(3):self.notify('thread/status/changed',{'threadId':THREAD,'status':{'type':'idle'}})
        self.assert_unknown(self.client.wait)
        self.assertLessEqual(len(self.client.events),1)

    def test_partial_send_and_close_exception_keep_unknown(self):
        self.open()
        def partial(data,deadline):self.wire.sent.append(json.loads(data));raise BrokenPipeError('PRIVATE')
        self.wire.send=partial
        self.wire.close=Mock(side_effect=OSError('PRIVATE'))
        with self.assertRaises(ServiceError)as error:self.client.resume()
        self.assertEqual(error.exception.outcome,'UNKNOWN')
        self.assertEqual(self.client.closure['status'],'UNKNOWN')
        before=len(self.wire.sent)
        with self.assertRaises(ServiceError)as again:self.client.resume()
        self.assertEqual(again.exception.outcome,'UNKNOWN');self.assertEqual(len(self.wire.sent),before)
        self.assertNotIn('PRIVATE',json.dumps(self.client.snapshot()))

    def test_oversized_frame_and_unknown_method_fail_closed(self):
        self.open();self.wire.frames.append(b'x'*(module.MAX_FRAME+1))
        self.assert_unknown(self.client.resume)

    def test_binary_mismatch_prevents_factory(self):
        with patch.object(module,'_official_binary_hash',return_value='b'*64):
            with self.assertRaises(ServiceError):self.client.open()
        self.factory.assert_not_called()


class PassiveNotificationTests(unittest.TestCase):
    def setUp(self):
        self.f=ExecutionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def ready(self):
        f=self.f;f.fresh();f.open();f.resume();f.start()
        f.notify('turn/started',{'threadId':THREAD,'turn':f.turn()})
        return f

    def item(self,kind='agentMessage',ident='item-one'):
        extra={'agentMessage':{'text':'PRIVATE ITEM BODY'},'commandExecution':{'command':'PRIVATE COMMAND'},
               'mcpToolCall':{'arguments':{'private':'PRIVATE ARGS'}},
               'collabAgentToolCall':{'prompt':'PRIVATE COLLAB'}}[kind]
        return {'threadId':THREAD,'turnId':TURN,'startedAtMs':1,'item':{'id':ident,'type':kind,**extra}}

    def delta(self,**changes):
        return {'threadId':THREAD,'turnId':TURN,'itemId':'item-one','delta':'PRIVATE DELTA',**changes}

    def finish(self):
        f=self.f;f.notify('turn/completed',{'threadId':THREAD,'turn':f.turn(status='completed')})
        return f.client.wait()

    def reject(self,method,params,prefix=()):
        f=self.ready()
        for m,p in prefix:f.notify(m,p)
        f.notify(method,params)
        # A valid terminal sentinel ensures silently accepting the bad event
        # cannot satisfy this test merely by reaching EOF/timeout afterwards.
        f.notify('turn/completed',{'threadId':THREAD,'turn':f.turn(status='completed')})
        error=f.assert_unknown(f.client.wait)
        self.assertNotEqual(error.code,'OFFICIAL_TIMEOUT')
        self.assertNotIn('PRIVATE',json.dumps(error.as_dict()))
        self.assertNotIn('PRIVATE',json.dumps(f.client.snapshot()))
        sent=copy.deepcopy(f.wire.sent)
        with self.assertRaises(ServiceError)as again:f.client.start('PRIVATE RETRY')
        self.assertEqual(again.exception.outcome,'UNKNOWN');self.assertEqual(f.wire.sent,sent)

    def test_exact_eleven_notifications_no_extra_rpc(self):
        self.assertEqual(set(module.NOTIFICATIONS),{'remoteControl/status/changed','thread/status/changed',
            'turn/started','turn/completed','item/completed','account/updated','account/rateLimits/updated',
            'thread/tokenUsage/updated','item/started','item/agentMessage/delta','mcpServer/startupStatus/updated'})
        self.assertEqual(set(module.METHODS),{'initialize','thread/resume','turn/start','turn/interrupt'})

    def test_baseline_account_notification_interleaved_with_rpc_is_passive(self):
        f=self.f
        f.notify('account/updated',{'authMode':None,'planType':None});f.open()
        f.notify('account/updated',{'authMode':None,'planType':None});f.resume()
        f.notify('account/rateLimits/updated',{'rateLimits':{'limitId':'PRIVATE QUOTA',
            'credits':{'balance':'PRIVATE BALANCE','hasCredits':True,'unlimited':False}}});f.start()
        result=self.finish()
        self.assertEqual([x['method']for x in f.wire.sent],['initialize','initialized','thread/resume','turn/start'])
        self.assertEqual(result['thread_id'],THREAD);self.assertEqual(result['session_id'],SESSION)
        self.assertFalse(result['native_execution_admitted']);self.assertFalse(result['exact_leaf_isolation'])
        self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertEqual([e['sequence']for e in result['events']],[1,2,3,4])
        for event in result['events']:
            self.assertEqual(set(event),{'sequence','method','sha256','size_bytes'})
            self.assertEqual(len(event['sha256']),64);self.assertGreater(event['size_bytes'],0)

    def test_auth_enum_vocabulary_never_selects_authentication(self):
        f=self.ready()
        for mode in ('apikey','chatgpt','chatgptAuthTokens',None):
            f.notify('account/updated',{'authMode':mode,'planType':'pro'})
        result=self.finish()
        for frame in f.wire.sent:self.assertNotIn('authMode',json.dumps(frame))
        self.assertNotIn('chatgptAuthTokens',json.dumps(result))
        self.assertNotIn('planType',json.dumps(result))

    def test_bound_token_item_delta_complete_lifecycle_discards_bodies(self):
        f=self.ready();usage={k:1 for k in ('cachedInputTokens','inputTokens','outputTokens','reasoningOutputTokens','totalTokens')}
        f.notify('thread/tokenUsage/updated',{'threadId':THREAD,'turnId':TURN,'tokenUsage':{'last':usage,'total':usage}})
        f.notify('item/started',self.item());f.notify('item/agentMessage/delta',self.delta())
        f.notify('item/agentMessage/delta',self.delta(delta='PRIVATE NEXT'))
        f.notify('item/completed',{'threadId':THREAD,'turnId':TURN,'completedAtMs':2,'item':self.item()['item']})
        result=self.finish();self.assertEqual(f.client.items,{'item-one':('agentMessage',True)})
        self.assertEqual(len(result['events']),7);self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertNotIn('"tokenUsage":',json.dumps(result));self.assertEqual(len(f.wire.sent),4)

    def test_cross_missing_and_out_of_order_bindings_fail_closed(self):
        start=self.item()
        for method,params,prefix in [
            ('item/started',{**start,'threadId':OTHER},()),('item/started',{**start,'turnId':OTHER},()),
            ('item/agentMessage/delta',self.delta(),()),
            ('item/agentMessage/delta',self.delta(threadId=OTHER),(('item/started',start),)),
            ('item/agentMessage/delta',self.delta(turnId=OTHER),(('item/started',start),)),
            ('item/agentMessage/delta',self.delta(itemId='other-item'),(('item/started',start),)),
            ('item/started',start,(('item/started',start),))]:
            with self.subTest(method=method,params=params):self.reject(method,params,prefix)

    def test_completion_only_and_explicit_stream_items_can_coexist(self):
        f=self.ready();legacy=self.item(ident='legacy-completed')['item']
        f.notify('item/completed',{'threadId':THREAD,'turnId':TURN,'completedAtMs':2,'item':legacy})
        f.notify('item/started',self.item());f.notify('item/agentMessage/delta',self.delta())
        f.notify('item/completed',{'threadId':THREAD,'turnId':TURN,'completedAtMs':3,'item':self.item()['item']})
        result=self.finish()
        self.assertEqual(f.client.items,{'legacy-completed':('agentMessage',True),'item-one':('agentMessage',True)})
        self.assertEqual(f.client.item_ids,{'legacy-completed','item-one'})
        self.assertEqual([event['sequence']for event in result['events']],[1,2,3,4,5,6])
        self.assertEqual(len(f.wire.sent),4);self.assertNotIn('PRIVATE',json.dumps(result))

    def test_completion_only_item_cannot_be_reopened_streamed_or_completed_twice(self):
        start=self.item();done={'threadId':THREAD,'turnId':TURN,'completedAtMs':2,'item':start['item']}
        for method,params in [('item/started',start),('item/agentMessage/delta',self.delta()),
            ('item/completed',done),('item/completed',{**done,'item':self.item('commandExecution')['item']})]:
            with self.subTest(method=method):self.reject(method,params,(('item/completed',done),))

    def test_stale_or_type_changed_item_cannot_receive_delta_or_complete_twice(self):
        start=self.item();done={'threadId':THREAD,'turnId':TURN,'completedAtMs':2,'item':start['item']}
        for method,params in [('item/agentMessage/delta',self.delta()),('item/completed',done)]:
            self.reject(method,params,(('item/started',start),('item/completed',done)))
        self.reject('item/completed',{**done,'item':self.item('commandExecution')['item']},(('item/started',start),))
        self.reject('item/agentMessage/delta',self.delta(),(('item/started',self.item('commandExecution')),))

    def test_tool_collaboration_items_are_data_and_never_trigger_response(self):
        for kind in ('commandExecution','mcpToolCall','collabAgentToolCall'):
            f=self.ready();start=self.item(kind);f.notify('item/started',start)
            f.notify('item/completed',{'threadId':THREAD,'turnId':TURN,'completedAtMs':2,'item':start['item']})
            result=self.finish();self.assertEqual(len(f.wire.sent),4)
            self.assertNotIn('PRIVATE',json.dumps(result))

    def test_extra_fields_rejected_at_top_and_nested_structure(self):
        usage={k:1 for k in ('cachedInputTokens','inputTokens','outputTokens','reasoningOutputTokens','totalTokens')}
        cases=[('account/updated',{'authMode':None,'planType':None,'unexpected':True}),
            ('account/rateLimits/updated',{'rateLimits':{'primary':{'usedPercent':1,'unexpected':True}}}),
            ('account/rateLimits/updated',{'rateLimits':{'credits':{'hasCredits':True,'unlimited':False,'unexpected':True}}}),
            ('thread/tokenUsage/updated',{'threadId':THREAD,'turnId':TURN,
                'tokenUsage':{'last':{**usage,'unexpected':True},'total':usage}}),
            ('item/started',{**self.item(),'item':{**self.item()['item'],'unexpected':True}}),
            ('item/agentMessage/delta',{**self.delta(),'unexpected':True})]
        for method,params in cases:
            prefix=(('item/started',self.item()),) if method=='item/agentMessage/delta' else ()
            with self.subTest(method=method):self.reject(method,params,prefix)

    def test_bound_notifications_before_native_resume_or_turn_are_rejected(self):
        for method,params in [('item/started',self.item()),('item/agentMessage/delta',self.delta())]:
            f=self.f;f.fresh();f.open();f.notify(method,params);f.assert_unknown(f.resume)
            self.assertNotIn('turn/start',[x['method']for x in f.wire.sent])
        f=self.f;f.fresh();f.open();f.resume();f.notify('item/started',self.item())
        # start is in flight, but no provider turn identity has been observed.
        f.assert_unknown(f.start)

    def test_id_bearing_notifications_and_unknown_methods_never_get_responses(self):
        for method in module.PASSIVE_NOTIFICATIONS:
            f=self.ready();before=len(f.wire.sent);f.wire.frames.append({'id':99,'method':method,'params':{}})
            f.notify('turn/completed',{'threadId':THREAD,'turn':f.turn(status='completed')})
            f.assert_unknown(f.client.wait);self.assertEqual(len(f.wire.sent),before)
        self.reject('account/login/updated',{})

    def test_extra_envelope_fields_are_rejected_before_processing(self):
        usage={k:1 for k in ('cachedInputTokens','inputTokens','outputTokens','reasoningOutputTokens','totalTokens')}
        cases=[('account/updated',{'authMode':None}),('account/rateLimits/updated',{'rateLimits':{}}),
            ('thread/tokenUsage/updated',{'threadId':THREAD,'turnId':TURN,'tokenUsage':{'last':usage,'total':usage}}),
            ('item/started',self.item()),('item/agentMessage/delta',self.delta())]
        for method,params in cases:
            f=self.ready()
            if method=='item/agentMessage/delta':f.notify('item/started',self.item())
            f.wire.frames.append({'method':method,'params':params,'unexpected':'PRIVATE EXTRA'})
            f.notify('turn/completed',{'threadId':THREAD,'turn':f.turn(status='completed')})
            before=len(f.wire.sent);error=f.assert_unknown(f.client.wait)
            self.assertEqual(error.code,'OFFICIAL_SERVER_REQUEST_REJECTED')
            self.assertNotIn('PRIVATE',json.dumps(error.as_dict()))
            self.assertEqual(len(f.wire.sent),before)

    def test_new_schema_pin_drift_blocks_native_factory(self):
        f=self.f
        for name in module.PASSIVE_NOTIFICATIONS.values():
            path=f.schemas/'v2'/(name+'.json');before=path.read_bytes();path.write_text('{}')
            f.fresh()
            with self.assertRaises(ServiceError):f.client.open()
            f.factory.assert_not_called();path.write_bytes(before)

    def test_new_notifications_share_count_size_deadline_bounds(self):
        f=self.ready()
        for _ in range(module.MAX_NOTIFICATIONS+1):f.notify('account/updated',{'authMode':None,'planType':None})
        f.assert_unknown(f.client.wait);self.assertEqual(len(f.client.events),module.MAX_NOTIFICATIONS)
        f=self.ready();f.wire.frames.append(json.dumps({'method':'account/updated','params':{'extra':'x'*module.MAX_FRAME}}).encode())
        f.assert_unknown(f.client.wait)
        f=self.ready();original=f.wire.receive
        def receive(deadline):
            result=original(deadline);f.now[0]+=60;return result
        f.wire.receive=receive;f.notify('account/updated',{'authMode':None})
        f.assert_unknown(f.client.wait)

    def test_malformed_types_required_fields_and_enums_reject(self):
        for method,params in [('account/updated',{'authMode':'made-up'}),('account/updated',{'planType':False}),
            ('account/rateLimits/updated',{'rateLimits':False}),('item/started',{'threadId':THREAD}),
            ('item/agentMessage/delta',{**self.delta(),'delta':42})]:
            with self.subTest(method=method):self.reject(method,params)


class StartupNotificationTests(unittest.TestCase):
    method='mcpServer/startupStatus/updated'

    def setUp(self):
        self.f=ExecutionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def message(self,status='starting',**changes):
        return {'name':'PRIVATE SYNTHETIC SERVER','status':status,**changes}

    def ready(self):
        f=self.f;f.fresh();f.open();f.resume();f.start();return f

    def terminal(self):
        f=self.f;f.notify('turn/completed',{'threadId':THREAD,'turn':f.turn(status='completed')})

    def reject(self,params,**envelope):
        f=self.ready();before=copy.deepcopy(f.wire.sent)
        f.wire.frames.append({'method':self.method,'params':params,**envelope});self.terminal()
        error=f.assert_unknown(f.client.wait)
        self.assertNotEqual(error.code,'OFFICIAL_TIMEOUT');self.assertEqual(f.wire.sent,before)
        self.assertNotIn('PRIVATE',json.dumps(error.as_dict()));self.assertNotIn('PRIVATE',json.dumps(f.client.snapshot()))
        with self.assertRaises(ServiceError)as again:f.client.resume()
        self.assertEqual(again.exception.outcome,'UNKNOWN');self.assertEqual(f.wire.sent,before)

    def test_all_four_states_nullable_binding_and_failure_reasons_are_passive(self):
        f=self.f
        for status in ('starting','ready','failed','cancelled'):
            f.notify(self.method,self.message(status,error='PRIVATE ERROR',failureReason=None))
        f.open()
        self.assertIsNone(f.client.native_thread_id);self.assertIsNone(f.client.turn_id)
        f.notify(self.method,self.message('failed',threadId=THREAD,failureReason='reauthenticationRequired'))
        f.resume();f.start()
        f.notify(self.method,self.message('cancelled',threadId=None,error=None))
        self.terminal();result=f.client.wait()
        self.assertEqual([frame['method']for frame in f.wire.sent],['initialize','initialized','thread/resume','turn/start'])
        self.assertEqual(result['terminal'],'completed');self.assertEqual(result['session_id'],SESSION)
        self.assertEqual(result['thread_id'],THREAD);self.assertFalse(result['native_execution_admitted'])
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('reauthenticationRequired',json.dumps(result))
        for event in result['events']:
            self.assertEqual(set(event),{'method','sequence','size_bytes','sha256'})
            self.assertEqual(len(event['sha256']),64)

    def test_scoped_startup_cannot_establish_native_identity_before_resume_reply(self):
        f=self.f;f.open();f.notify(self.method,self.message(threadId=THREAD))
        original=f.wire.receive;observed=[]
        def receive(deadline):
            observed.append((f.client.native_thread_id,f.client.resumed,f.client.turn_id))
            return original(deadline)
        f.wire.receive=receive;f.resume()
        self.assertEqual(observed,[(None,False,None),(None,False,None)])
        self.assertEqual(f.client.native_thread_id,THREAD);f.client.close()

    def test_foreign_malformed_and_premature_scoped_thread_rejected(self):
        for thread in (OTHER,'not-a-thread','',False,7,[],{}):
            with self.subTest(thread=thread):self.reject(self.message(threadId=thread))
        f=self.f;f.fresh();f.notify(self.method,self.message(threadId=THREAD))
        error=f.assert_unknown(f.open)
        self.assertEqual(error.code,'OFFICIAL_NOTIFICATION_BINDING_REJECTED')
        self.assertEqual([x['method']for x in f.wire.sent],['initialize'])

    def test_closed_fields_types_required_keys_and_enums(self):
        cases=[{},None,[],self.message(status='unknown'),self.message(status=None),
            self.message(name=None),self.message(name=3),self.message(error=False),
            self.message(failureReason='retry'),self.message(failureReason={}),
            self.message(unexpected=True)]
        for params in cases:
            with self.subTest(params=params):self.reject(params)

    def test_id_bearing_and_extra_envelope_requests_get_no_response(self):
        for extra in ({'id':99},{'id':None},{'unexpected':'PRIVATE EXTRA'}):
            with self.subTest(extra=extra):self.reject(self.message(),**extra)

    def test_startup_schema_pin_drift_refuses_before_factory(self):
        f=self.f;(f.schemas/'v2/McpServerStatusUpdatedNotification.json').write_text('{}')
        with self.assertRaises(ServiceError):f.client.open()
        f.factory.assert_not_called()

    def test_repeated_startup_shares_count_and_size_bounds(self):
        f=self.ready()
        for _ in range(module.MAX_NOTIFICATIONS+1):f.notify(self.method,self.message())
        f.assert_unknown(f.client.wait);self.assertEqual(len(f.client.events),module.MAX_NOTIFICATIONS)
        f=self.ready();f.notify(self.method,self.message(name='x'*(module.MAX_FRAME+1)))
        f.assert_unknown(f.client.wait)

    def test_repeated_startup_never_extends_pending_or_total_deadline(self):
        f=self.ready();original=f.wire.receive;seen=[]
        def receive(deadline):
            seen.append(deadline);value=original(deadline);f.now[0]+=2;return value
        f.wire.receive=receive
        for _ in range(12):f.notify(self.method,self.message('failed',failureReason='reauthenticationRequired'))
        f.assert_unknown(f.client.wait);self.assertEqual(len(set(seen)),1)
        self.assertLessEqual(len(seen),8);self.assertEqual(f.client.deadline,57)

    def test_exact_eleven_allowlist_and_no_new_outbound_method(self):
        self.assertEqual(len(module.NOTIFICATIONS),11)
        self.assertEqual(module.NOTIFICATIONS[self.method],'McpServerStatusUpdatedNotification')
        self.assertEqual(set(module.METHODS),{'initialize','thread/resume','turn/start','turn/interrupt'})


class ProcessClosureTests(unittest.TestCase):
    def wire(self):
        wire=module._ProcessWire.__new__(module._ProcessWire)
        wire._identity=(54321,87654,54321)
        wire.proc=Mock();wire.proc.poll.return_value=None
        wire.proc.stdin=Mock();wire.proc.stdout=Mock();wire.proc.stderr=Mock()
        return wire

    def test_only_exact_owned_group_is_signaled_and_reaped(self):
        import subprocess,signal
        wire=self.wire();wire.proc.wait.side_effect=[subprocess.TimeoutExpired('synthetic',.2),None]
        def kill(group,sig):
            self.assertEqual(group,54321)
            if sig==0:raise ProcessLookupError()
        with patch.object(module,'_ticks',return_value=87654),patch.object(module.os,'getpgid',return_value=54321),\
             patch.object(module.os,'killpg',side_effect=kill)as signal_group:
            result=wire.close()
        self.assertEqual(result['status'],'CLOSED')
        self.assertEqual(signal_group.call_args_list[0].args,(54321,signal.SIGTERM))
        self.assertTrue(wire.proc.stdout.close.called)

    def test_foreign_start_ticks_are_not_signaled_or_claimed_closed(self):
        import subprocess
        wire=self.wire();wire.proc.wait.side_effect=subprocess.TimeoutExpired('synthetic',.2)
        with patch.object(module,'_ticks',return_value=999),patch.object(module.os,'killpg')as kill:
            result=wire.close()
        kill.assert_not_called();self.assertEqual(result['status'],'UNKNOWN')

    def test_bounded_kill_after_term_timeout_rechecks_identity(self):
        import subprocess,signal
        wire=self.wire();wire.proc.wait.side_effect=[subprocess.TimeoutExpired('synthetic',.2),subprocess.TimeoutExpired('synthetic',1),None]
        def kill(group,sig):
            if sig==0:raise ProcessLookupError()
        with patch.object(module,'_ticks',return_value=87654)as ticks,patch.object(module.os,'getpgid',return_value=54321),\
             patch.object(module.os,'killpg',side_effect=kill)as sends:
            result=wire.close()
        self.assertEqual(result['status'],'CLOSED');self.assertEqual(ticks.call_count,2)
        self.assertEqual([c.args[1]for c in sends.call_args_list],[signal.SIGTERM,signal.SIGKILL,0])

    def test_unknown_identity_or_unclosed_group_retains_unknown(self):
        wire=self.wire();wire._identity=None
        with patch.object(module.os,'killpg')as kill:self.assertEqual(wire.close()['status'],'UNKNOWN')
        kill.assert_not_called()
        wire=self.wire();wire.proc.poll.return_value=0
        with patch.object(module,'_ticks',side_effect=FileNotFoundError()),patch.object(module.os,'killpg',return_value=None)as kill:
            self.assertEqual(wire.close()['status'],'UNKNOWN')
        self.assertTrue(all(c.args[1]==0 for c in kill.call_args_list))

    def test_captured_process_identity_is_not_mutable_through_projection(self):
        wire=self.wire();identity=wire.identity;identity['pgid']=1
        self.assertEqual(wire.identity,{'pid':54321,'start_ticks':87654,'pgid':54321})

    def test_constructor_records_identity_before_pipe_setup_without_real_spawn(self):
        proc=Mock();proc.pid=54321;proc.poll.return_value=0
        events=[]
        with patch.object(module.subprocess,'Popen',return_value=proc)as spawn, \
             patch.object(module,'_ticks',side_effect=lambda pid:events.append(('ticks',pid))or 87654), \
             patch.object(module.os,'getpgid',side_effect=lambda pid:events.append(('group',pid))or 54321), \
             patch.object(module.os,'set_blocking',side_effect=lambda *args:events.append(('pipe',None))), \
             patch.object(module.os,'killpg',side_effect=ProcessLookupError()):
            wire=module._ProcessWire(('/synthetic/binary','app-server'),Path('/synthetic/work'),{},lambda:0)
            self.assertEqual(wire.identity,{'pid':54321,'start_ticks':87654,'pgid':54321})
            self.assertEqual(events[:2],[('ticks',54321),('group',54321)])
            self.assertEqual(wire.close()['status'],'CLOSED')
        spawn.assert_called_once()
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        self.assertEqual(spawn.call_args.args[0],('/synthetic/binary','app-server'))

    def test_failed_start_ticks_preserve_known_pid_without_group_signals(self):
        proc=Mock();proc.pid=54321
        with patch.object(module.subprocess,'Popen',return_value=proc), \
             patch.object(module,'_ticks',side_effect=FileNotFoundError()), \
             patch.object(module.os,'killpg')as kill:
            wire=module._ProcessWire(('/synthetic/binary','app-server'),Path('/synthetic/work'),{},lambda:0)
            self.assertTrue(wire.startup_error)
            self.assertEqual(wire.identity,{'pid':54321,'start_ticks':None,'pgid':None})
            self.assertEqual(wire.close()['status'],'UNKNOWN')
        kill.assert_not_called()
