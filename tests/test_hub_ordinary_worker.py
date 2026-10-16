"""Ordinary execution uses exact read inputs and synthetic provider events only."""
import hashlib
import json
from pathlib import Path
import tempfile
import sys
import subprocess
import yaml
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

from hub.codex_adapter import AppServerAdapter, EVIDENCE_PERMISSION_PROFILE, profile_arguments
from hub.ordinary_tasks import KIND, make_grant, prompt
from hub.service_contract import ServiceError
from hub.task_store import TaskStore
from hub.task_worker import TaskWorker, ORDINARY_RESPONSE_BYTES
from hub.workbench_store import digest, canonical
import hub.task_worker as worker_module


class OrdinaryWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'project'; self.root.mkdir()
        self.source = self.root / 'README.md'; self.source.write_text('Synthetic exact input\n')
        (self.root / 'unregistered.txt').write_text('Synthetic file outside input registration\n')
        self.store = TaskStore(base / 'hub'); self.store.root.mkdir()
        self.store.register_storage()
        self.registry_path=self.store.root/'data/registry/external_projects.yaml'
        self.registry_path.parent.mkdir(parents=True)
        self.registry={'projects':[{'id':'ordinary-fixture','name':'Synthetic ordinary project','enabled':True,
                                    'connection_read_allowed':True,'root_path':str(self.root),'current_state_paths':['README.md']}]}
        self.registry_path.write_text(yaml.safe_dump(self.registry))
        self.registry_hash=hashlib.sha256(self.registry_path.read_bytes()).hexdigest()
        scope=self.store.root/'data/connections/sync_scope.yaml';scope.parent.mkdir(parents=True)
        scope.write_text(yaml.safe_dump({'schema_version':'1.0','scope_id':'ordinary-synthetic-scope','baseline_total':1,
                                         'project_ids':['ordinary-fixture'],'excluded_project_ids':{}}))
        self.grant = make_grant(self.root, 'ordinary-fixture', self.registry_hash, 'ordinary-grant', input_paths=['README.md'])
        self.store.register_grant(self.grant)
        self.spec = {'kind': KIND, 'request_id': 'ordinary-task', 'project_id': 'ordinary-fixture',
                     'objective': 'Summarize the registered text', 'scope': 'Only the registered input',
                     'effect': 'read_only', 'input_version': self.grant['input_version'], 'mode': 'new', 'parent_task_id': None}
        self.intent = {'grant': self.grant, 'grant_hash': digest(self.grant), 'task_spec': self.spec,
                       'registry_hash': self.registry_hash, 'previous_thread': None, 'owner_context': 'synthetic-owner',
                       'prompt_sha256': hashlib.sha256(prompt(self.spec,input_files=self.grant['input_files']).encode()).hexdigest()}
        fixture = self
        class Adapter:
            transport = 'mock'
            text = 'Synthetic full response'
            scenario = 'normal'
            created = []
            interrupts = []
            read_only = True
            def __init__(self, root, *, read_only=True, readable_paths=None):
                self.root, self.read_only, self.readable_paths = Path(root), read_only, readable_paths
                self.denials = []; self.events = []; self.changed = False
                self.created.append(self)
            def open(self): return self
            def thread(self, previous=None): return previous or 'ordinary-thread'
            def event(self, method, **extra):
                return {'method': method, 'params': {'threadId': 'ordinary-thread', 'turnId': 'ordinary-turn', **extra}}
            def start(self, thread, text, image):
                fixture.assertIsNone(image)
                fixture.assertEqual(text, prompt(fixture.spec,input_files=fixture.grant['input_files']))
                self.events = [self.event('turn/started', turn={'id': 'ordinary-turn', 'status': 'inProgress'}),
                               self.event('item/agentMessage/delta', itemId='reply', delta='Synthetic partial'),
                               self.event('item/agentMessage/delta', itemId='reply', delta='Synthetic partial'),
                               self.event('item/completed', item={'id': 'reply', 'type': 'agentMessage', 'text': self.text}),
                               self.event('item/completed', item={'id': 'reply', 'type': 'agentMessage', 'text': self.text}),
                               self.event('turn/completed', turn={'id': 'ordinary-turn', 'status': 'completed'})]
                if self.scenario == 'input_changed': fixture.source.write_text('Changed synthetic source')
                if self.scenario == 'conflicting_completed': self.events[4]['params']['item']['text']='Conflicting final text'
                return 'ordinary-turn'
            def poll(self, timeout):
                if not self.changed:
                    self.changed = True
                    if self.scenario == 'revoked': fixture.store.register_grant({**fixture.grant, 'version': 2})
                    if self.scenario == 'cancelled': fixture.store.cancel('ordinary-task',worker_owner=fixture.store.task('ordinary-task')['owner'])
                    if self.scenario == 'registry_changed':
                        fixture.registry['projects'][0]['enabled']=False
                        fixture.registry_path.write_text(yaml.safe_dump(fixture.registry))
                return self.events.pop(0)
            def interrupt(self, thread, turn):
                self.interrupts.append((thread, turn))
                self.events = [self.event('turn/completed', turn={'id': turn, 'status': 'interrupted'})]
            def check(self, argv): raise AssertionError('ordinary worker must not execute fixture checks')
            def close(self): pass
        self.Adapter = Adapter

    def run_worker(self, *, native_default=False):
        self.store.admit(self.spec, self.intent)
        if native_default:
            with mock.patch.object(worker_module, 'AppServerAdapter', self.Adapter):
                worker = TaskWorker(self.store, adapter_factory=self.Adapter, writer_check=lambda _: True)
                worker.run_one()
        else:
            TaskWorker(self.store, adapter_factory=self.Adapter, writer_check=lambda _: True).run_one()
        return self.store.task('ordinary-task')

    def test_exact_native_factory_arguments_and_full_response_no_fixture_checks(self):
        before = self.source.read_bytes()
        task = self.run_worker(native_default=True)
        self.assertEqual(task['status'], 'checks_complete', task['result'])
        self.assertTrue(self.Adapter.created[0].read_only)
        self.assertEqual(self.Adapter.created[0].readable_paths, ['README.md'])
        self.assertEqual(task['result']['provider_reply'], self.Adapter.text)
        self.assertFalse(task['result']['response_truncated'])
        self.assertEqual(task['result']['checks'][0]['exit'], 0)
        self.assertEqual(task['human_acceptance'], 'pending')
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(task['result']['stream_observation']['count'], 2)

    def test_native_profile_rejection_retains_owned_thread_without_turn_or_retry(self):
        adapter=AppServerAdapter(self.root,read_only=True,readable_paths=['README.md'],schemas={})
        adapter.open=lambda:adapter;adapter.close=lambda:None
        adapter.call=mock.Mock(return_value={'thread':{'id':'owned-native-thread','cwd':str(self.root)},
                                            'activePermissionProfile':None})
        adapter.start=mock.Mock(side_effect=AssertionError('profile rejection must not send turn'))
        self.store.admit(self.spec,self.intent)
        TaskWorker(self.store,adapter_factory=lambda root:adapter,writer_check=lambda _:True).run_one()
        task=self.store.task('ordinary-task')
        self.assertEqual(task['status'],'requires_reconcile')
        self.assertEqual(task['thread_id'],'owned-native-thread');self.assertIsNone(task['turn_id'])
        self.assertEqual(task['result']['error_class'],'CODEX_PROFILE_REJECTED')
        self.assertEqual(task['result']['pre_turn_binding'],{'thread_id':'owned-native-thread',
            'operation':'thread/start','binding_verified':True,'turn_started':False})
        adapter.start.assert_not_called();self.assertEqual(adapter.call.call_count,1)
        self.assertIsNone(self.store.claim('another-worker'))

    def test_native_wrong_root_rejection_does_not_adopt_stale_identity(self):
        adapter=AppServerAdapter(self.root,read_only=True,readable_paths=['README.md'],schemas={})
        adapter.open=lambda:adapter;adapter.close=lambda:None
        adapter.thread_binding={'thread_id':'stale-owned','cwd':str(self.root),'operation':'thread/start'}
        adapter.call=mock.Mock(return_value={'thread':{'id':'foreign','cwd':str(self.root/'other')},
                                            'activePermissionProfile':None})
        adapter.start=mock.Mock(side_effect=AssertionError('foreign binding must not dispatch'))
        self.store.admit(self.spec,self.intent)
        TaskWorker(self.store,adapter_factory=lambda root:adapter,writer_check=lambda _:True).run_one()
        task=self.store.task('ordinary-task')
        self.assertEqual(task['status'],'failed');self.assertIsNone(task['thread_id']);self.assertIsNone(task['turn_id'])
        self.assertEqual(task['result']['error_class'],'CODEX_THREAD_BINDING_REJECTED')
        self.assertNotIn('pre_turn_binding',task['result']);adapter.start.assert_not_called()

    def test_native_exact_read_scope_rejection_keeps_thread_and_never_starts_turn(self):
        adapter=AppServerAdapter(self.root,read_only=True,readable_paths=['README.md'],schemas={})
        adapter.open=lambda:adapter;adapter.close=lambda:None
        calls=[]
        def rpc(method,params):
            calls.append(method)
            if method=='thread/start':return {'thread':{'id':'bound-before-scope-check','cwd':str(self.root)},
                'activePermissionProfile':{'id':EVIDENCE_PERMISSION_PROFILE}}
            if method=='config/read':return {'config':{'default_permissions':EVIDENCE_PERMISSION_PROFILE,
                'permissions':{EVIDENCE_PERMISSION_PROFILE:{'filesystem':{str(self.root):'read'},'network':{'enabled':False}}}}}
            raise AssertionError('no turn may follow unverified scope')
        adapter.call=rpc
        self.store.admit(self.spec,self.intent)
        TaskWorker(self.store,adapter_factory=lambda root:adapter,writer_check=lambda _:True).run_one()
        task=self.store.task('ordinary-task')
        self.assertEqual(task['status'],'requires_reconcile');self.assertEqual(task['thread_id'],'bound-before-scope-check')
        self.assertIsNone(task['turn_id']);self.assertEqual(task['result']['error_class'],'CODEX_EXACT_READ_SCOPE_UNVERIFIED')
        self.assertFalse(task['result']['pre_turn_binding']['turn_started'])
        self.assertEqual(calls,['thread/start','config/read']);self.assertIsNone(adapter.active_profile)

    def test_long_multibyte_and_json_escaped_response_is_bounded_and_explicit(self):
        for text in ('文' * 40000, 'answer\n' * 12000):
            with self.subTest(shape='multibyte' if text[0] == '文' else 'escaped'):
                # Each fixture row is one immutable task; use isolated setup per input.
                other = OrdinaryWorkerTests(); other.setUp()
                try:
                    other.Adapter.text = text
                    task = other.run_worker()
                    self.assertEqual(task['status'], 'checks_complete', task['result'])
                    result = task['result']; self.assertTrue(result['response_truncated'])
                    self.assertLessEqual(len(result['provider_reply'].encode()), ORDINARY_RESPONSE_BYTES)
                    self.assertLessEqual(len(canonical(result)), 131072)
                    self.assertTrue(text.startswith(result['provider_reply']))
                finally: other.doCleanups()

    def test_input_change_or_grant_revocation_never_publishes_success(self):
        for scenario in ('input_changed', 'revoked'):
            with self.subTest(scenario=scenario):
                other = OrdinaryWorkerTests(); other.setUp()
                try:
                    other.Adapter.scenario = scenario
                    task = other.run_worker()
                    self.assertEqual(task['status'], 'requires_reconcile')
                    self.assertIn(task['result']['error_class'], ('ORDINARY_INPUT_CHANGED', 'GRANT_VERSION_STALE'))
                    self.assertEqual(other.Adapter.interrupts, [('ordinary-thread', 'ordinary-turn')])
                finally: other.doCleanups()

    def test_queued_registry_denial_precedes_input_probes_and_dispatch(self):
        self.store.admit(self.spec,self.intent)
        self.registry['projects'][0]['enabled']=False
        self.registry_path.write_text(yaml.safe_dump(self.registry))
        with mock.patch('hub.ordinary_tasks.snapshot_inputs',side_effect=AssertionError('Denied input was probed')):
            TaskWorker(self.store,adapter_factory=self.Adapter,writer_check=lambda _:True).run_one()
        self.assertEqual(self.store.task('ordinary-task')['status'],'failed')
        self.assertEqual(self.Adapter.created,[])

    def test_running_registry_revocation_interrupts_without_success(self):
        self.Adapter.scenario='registry_changed'
        task=self.run_worker()
        self.assertEqual(task['status'],'requires_reconcile')
        self.assertEqual(self.Adapter.interrupts,[('ordinary-thread','ordinary-turn')])
        self.assertNotEqual(task['status'],'checks_complete')

    def test_cancel_interrupts_only_owned_turn(self):
        self.Adapter.scenario = 'cancelled'
        task = self.run_worker()
        self.assertEqual(task['status'], 'cancelled')
        self.assertEqual(self.Adapter.interrupts, [('ordinary-thread', 'ordinary-turn')])

    def test_conflicting_completed_item_is_not_silently_deduplicated(self):
        self.Adapter.scenario='conflicting_completed'
        task=self.run_worker()
        self.assertEqual(task['status'],'requires_reconcile')
        self.assertEqual(task['result']['error_class'],'ORDINARY_RESPONSE_CONFLICT')

    def test_missing_response_is_failed_without_making_up_content(self):
        self.Adapter.text=''
        task=self.run_worker()
        self.assertEqual(task['status'],'failed')
        self.assertEqual(task['result']['provider_reply'],'')
        self.assertEqual(task['result']['checks'][-1],{'id':'ordinary_response_present','exit':1})

    def test_wrong_adapter_profile_is_rejected_before_thread_dispatch(self):
        self.store.admit(self.spec,self.intent)
        adapter=self.Adapter(self.root);adapter.read_only=False
        TaskWorker(self.store,adapter_factory=lambda root:adapter,writer_check=lambda _:True).run_one()
        task=self.store.task('ordinary-task')
        self.assertEqual(task['status'],'failed')
        self.assertEqual(task['result']['error_class'],'ORDINARY_READ_ONLY_PROFILE_REQUIRED')
        self.assertIsNone(task['thread_id'])

    def test_parent_kind_cannot_cross_existing_fixture_or_story_channel(self):
        parent_payload={**self.spec,'kind':'fixture','request_id':'other-kind-parent'}
        self.store.admit(parent_payload,self.intent)
        parent=self.store.claim('synthetic-parent-owner')
        self.store.update(parent['id'],'synthetic-parent-owner',thread_id='ordinary-thread',turn_id='ordinary-turn',provider_status='completed',status='validating')
        self.store.update(parent['id'],'synthetic-parent-owner',status='checks_complete',result={'checks':[{'exit':0}]})
        self.spec.update(mode='idle_continue',parent_task_id=parent['id'])
        self.intent.update(parent_binding={'thread_id':'ordinary-thread','turn_id':'ordinary-turn'},previous_thread='ordinary-thread')
        self.intent['prompt_sha256']=hashlib.sha256(prompt(self.spec,input_files=self.grant['input_files']).encode()).hexdigest()
        task=self.run_worker()
        self.assertEqual(task['status'],'failed')
        self.assertEqual(task['result']['error_class'],'ORDINARY_PARENT_KIND_MISMATCH')
        self.assertEqual(self.Adapter.created,[])

    def test_cli_missing_store_never_initializes_or_starts_provider(self):
        target=Path(self.temp.name)/'missing-hub'
        command=[sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/hub_worker.py'),
                 '--ordinary-readonly','--once','--task-id','missing-task','--control-root',str(target)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=5,check=False)
        self.assertEqual(result.returncode,2)
        self.assertFalse(target.exists())
        self.assertNotIn('Traceback',result.stderr)

    def test_sibling_continuation_rejects_historical_turn_and_new_parent_can_continue(self):
        parent_spec={**self.spec,'request_id':'original-parent'}
        parent_intent={**self.intent,'task_spec':parent_spec,
                       'prompt_sha256':hashlib.sha256(prompt(parent_spec,input_files=self.grant['input_files']).encode()).hexdigest()}
        self.store.admit(parent_spec,parent_intent)
        self.store.claim('parent-owner',only_task_id='original-parent')
        self.store.update('original-parent','parent-owner',thread_id='ordinary-thread',turn_id='original-turn',
                          provider_status='completed',status='validating')
        self.store.update('original-parent','parent-owner',status='checks_complete',result={'checks':[{'exit':0}]})
        original=self.store.task('original-parent')
        fixture=self
        class NativeTimeline(self.Adapter):
            latest='original-turn'
            starts=[]
            verified=[]
            def verify_parent_binding(self,thread,turn):
                type(self).verified.append((thread,turn))
                if thread!='ordinary-thread' or turn!=type(self).latest:
                    raise ServiceError('CODEX_PARENT_BINDING_REJECTED',status=409)
            def start(self,thread,text,image):
                fixture.assertIsNone(image)
                turn='next-turn-'+str(len(type(self).starts)+1)
                type(self).starts.append(turn);type(self).latest=turn
                self.events=[{'method':'turn/started','params':{'threadId':thread,'turnId':turn,'turn':{'id':turn,'status':'inProgress'}}},
                             {'method':'item/completed','params':{'threadId':thread,'turnId':turn,'item':{'id':turn+'-reply','type':'agentMessage','text':'Synthetic continuation'}}},
                             {'method':'turn/completed','params':{'threadId':thread,'turnId':turn,'turn':{'id':turn,'status':'completed'}}}]
                return turn
        def admit_child(ident,parent):
            spec={**self.spec,'request_id':ident,'mode':'idle_continue','parent_task_id':parent['id']}
            intent={**self.intent,'task_spec':spec,'previous_thread':parent['thread_id'],
                    'parent_binding':{'thread_id':parent['thread_id'],'turn_id':parent['turn_id']},
                    'prompt_sha256':hashlib.sha256(prompt(spec,input_files=self.grant['input_files']).encode()).hexdigest()}
            self.store.admit(spec,intent)
        def run_child(ident):
            TaskWorker(self.store,adapter_factory=NativeTimeline,writer_check=lambda _:True,only_task_id=ident).run_one()
            return self.store.task(ident)
        admit_child('first-child',original);admit_child('stale-sibling',original)
        first=run_child('first-child')
        self.assertEqual(first['status'],'checks_complete',first['result'])
        self.assertEqual(first['turn_id'],'next-turn-1')
        # The new completed child, rather than its historical ancestor, is a
        # legitimate authority for another turn on this same native thread.
        admit_child('fresh-grandchild',first)
        fresh=run_child('fresh-grandchild')
        self.assertEqual(fresh['status'],'checks_complete',fresh['result'])
        self.assertEqual(fresh['turn_id'],'next-turn-2')
        stale=run_child('stale-sibling')
        self.assertEqual(stale['status'],'requires_reconcile')
        self.assertEqual(stale['result']['error_class'],'CODEX_PARENT_BINDING_REJECTED')
        self.assertIsNone(stale['turn_id'])
        self.assertEqual(NativeTimeline.starts,['next-turn-1','next-turn-2'])
        self.assertEqual(NativeTimeline.verified[-1],('ordinary-thread','original-turn'))

    def test_continuation_without_native_parent_verifier_fails_closed(self):
        self.store.admit(self.spec,self.intent);self.store.claim('parent-owner')
        self.store.update('ordinary-task','parent-owner',thread_id='ordinary-thread',turn_id='ordinary-turn',
                          provider_status='completed',status='validating')
        self.store.update('ordinary-task','parent-owner',status='checks_complete',result={'checks':[{'exit':0}]})
        child_spec={**self.spec,'request_id':'unverified-child','mode':'idle_continue','parent_task_id':'ordinary-task'}
        intent={**self.intent,'task_spec':child_spec,'previous_thread':'ordinary-thread',
                'parent_binding':{'thread_id':'ordinary-thread','turn_id':'ordinary-turn'},
                'prompt_sha256':hashlib.sha256(prompt(child_spec,input_files=self.grant['input_files']).encode()).hexdigest()}
        self.store.admit(child_spec,intent)
        TaskWorker(self.store,adapter_factory=self.Adapter,writer_check=lambda _:True,only_task_id='unverified-child').run_one()
        task=self.store.task('unverified-child')
        self.assertEqual(task['status'],'requires_reconcile')
        self.assertEqual(task['result']['error_class'],'ORDINARY_PARENT_VERIFICATION_UNAVAILABLE')
        self.assertIsNone(task['turn_id'])
        self.assertEqual(self.Adapter.created[0].events,[])

    def test_prompt_hash_fails_before_dispatch(self):
        self.intent['prompt_sha256'] = '0' * 64
        task = self.run_worker()
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['result']['error_class'], 'ORDINARY_PROMPT_VERSION_STALE')
        self.assertEqual(self.Adapter.created, [])


class ExactReadProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'allowed.txt').write_text('synthetic')
        (self.root / 'extra.txt').write_text('unregistered')

    def test_profile_denies_root_and_lists_only_registered_file(self):
        args = profile_arguments(self.root, read_only=True, readable_paths=['allowed.txt'])
        table = args[3]
        self.assertIn(json.dumps(str(self.root)) + '="deny"', table)
        self.assertIn(json.dumps(str(self.root / 'allowed.txt')) + '="read"', table)
        self.assertNotIn(str(self.root / 'extra.txt'), table)
        self.assertIn('network.enabled=false', args[-1])

    def test_reject_write_mode_escaping_alias_directory_and_duplicate(self):
        (self.root / 'alias').symlink_to(self.root / 'allowed.txt')
        for paths, readonly in ((['allowed.txt'], False), (['../outside'], True), (['alias'], True),
                                (['.'], True), (['allowed.txt', 'allowed.txt'], True), ([{}], True)):
            with self.subTest(paths=paths), self.assertRaises(ServiceError):
                profile_arguments(self.root, read_only=readonly, readable_paths=paths)

    def test_native_parent_latest_completed_turn_is_strict_and_payload_not_retained(self):
        adapter=AppServerAdapter(self.root,read_only=True,schemas={})
        thread={'id':'native-thread','cwd':str(self.root),'status':{'type':'idle'},
                'turns':[{'id':'old-turn','status':'completed'},
                         {'id':'expected-turn','status':'completed','items':[{'text':'synthetic-private-turn-body'}]}]}
        calls=[]
        def call(method,params):
            calls.append((method,params));return {'thread':thread}
        adapter.call=call
        self.assertIsNone(adapter.verify_parent_binding('native-thread','expected-turn'))
        self.assertEqual(calls,[('thread/read',{'threadId':'native-thread','includeTurns':True})])
        self.assertNotIn('synthetic-private-turn-body',repr(adapter.__dict__))
        mutations=[{'turns':[]}, {'turns':[{'id':'different-turn','status':'completed'}]},
                   {'turns':[{'id':'expected-turn','status':'inProgress'}]},
                   {'turns':[{'id':'expected-turn','status':'interrupted'}]},
                   {'status':{'type':'notLoaded'}}, {'status':{'type':'active'}},
                   {'cwd':str(self.root/'other')}, {'id':'other-thread'}]
        for changes in mutations:
            with self.subTest(changes=changes):
                adapter.call=lambda *args,changes=changes,**kwargs:{'thread':{**thread,**changes}}
                with self.assertRaises(ServiceError)as caught:
                    adapter.verify_parent_binding('native-thread','expected-turn')
                self.assertEqual(caught.exception.code,'CODEX_PARENT_BINDING_REJECTED')

    def test_effective_profile_mismatch_fails_without_root_read_fallback(self):
        adapter = AppServerAdapter(self.root, read_only=True, readable_paths=['allowed.txt'], schemas={})
        expected = {':root': 'deny', ':minimal': 'read', ':tmpdir': 'deny', ':slash_tmp': 'deny',
                    str(self.root): 'deny', str(self.root / 'allowed.txt'): 'read'}
        def response(table):
            return {'config': {'default_permissions': EVIDENCE_PERMISSION_PROFILE,
                              'permissions': {EVIDENCE_PERMISSION_PROFILE: {'filesystem': table, 'network': {'enabled': False}}}}}
        adapter.call = lambda *args, **kwargs: response(expected)
        adapter._profile_echo({'activePermissionProfile': {'id': EVIDENCE_PERMISSION_PROFILE}})
        adapter.call = lambda *args, **kwargs: response({**expected, str(self.root): 'read'})
        with self.assertRaises(ServiceError) as caught:
            adapter._profile_echo({'activePermissionProfile': {'id': EVIDENCE_PERMISSION_PROFILE}})
        self.assertEqual(caught.exception.code, 'CODEX_EXACT_READ_SCOPE_UNVERIFIED')
        self.assertIsNone(adapter.active_profile)


if __name__ == '__main__': unittest.main()
