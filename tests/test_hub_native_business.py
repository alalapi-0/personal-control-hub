"""Native business boundaries, with transport and identity discovery mocked."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.codex_adapter import AppServerAdapter, profile_arguments
from hub.native_business import NativeBusinessAdapter, PROFILE, TASK, exchange_profile
from hub.service_contract import ServiceError
from hub.workbench_store import digest


class NativeBusinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.hub = Path(self.temp.name); self.root = self.hub / 'stage'
        self.root.mkdir(); (self.hub/'STATE.yaml').write_text('registered fixture')
        self.identity = SimpleNamespace(semantic_sha256='a'*64, config_sha256='b'*64)
        self.grant = {'task_id': 'task-story', 'project_id': 'story-project', 'root_identity': 'c'*64,
                      'expires_at': 123456789, 'story_exchange': {'canonical_root': '/source-not-mounted',
                      'registry_hash': 'd'*64, 'specification_sha256': 'e'*64, 'request_sha256': 'f'*64}}
        self.body = {'purpose':'story_exchange','goal_task_id':TASK,'task_id':'task-story',
                     'project_id':'story-project','root':str(self.root),'root_identity':'c'*64,
                     'canonical_root':'/source-not-mounted','registry_hash':'d'*64,
                     'specification_sha256':'e'*64,'request_sha256':'f'*64,
                     'config_identity_sha256':'a'*64,'expires_at':123456789}
        self.grant['story_exchange']['registration_sha256'] = digest(self.body)
        self.wrapper = {'body': self.body, 'grant_sha256': digest(self.grant)}
        self.state = {'pch_refactor': {'r54_implementation': {'native_execution_registration': self.wrapper}}}
        for name, kwargs in [('hub.story_exchange.validate', {'side_effect': lambda grant, **_:copy.deepcopy(grant)}),
                             ('hub.native_business.load_registration', {'return_value':self.identity}),
                             ('hub.native_business.parse_owned_hub_state', {'side_effect':lambda _:self.state})]:
            context = patch(name, **kwargs); context.start(); self.addCleanup(context.stop)

    def adapter(self):
        return NativeBusinessAdapter(self.root, hub_root=self.hub, grant=self.grant)

    def test_registration_binds_all_scope_and_grant_values(self):
        self.assertIs(self.adapter()._registration(), self.identity)
        for field in self.body:
            original = self.body[field]
            self.body[field] = 'changed'
            with self.subTest(field=field), self.assertRaises(ServiceError): self.adapter()
            self.body[field] = original
        self.wrapper['grant_sha256'] = '0'*64
        with self.assertRaises(ServiceError): self.adapter()

    def test_registration_no_alias_or_unknown_wrapper_fields(self):
        self.wrapper['extra'] = True
        with self.assertRaises(ServiceError): self.adapter()
        self.wrapper.pop('extra')
        original = self.hub/'STATE.yaml'
        original.rename(self.hub/'real-state'); original.symlink_to(self.hub/'real-state')
        with self.assertRaises(ServiceError): self.adapter()

    def test_typed_profile_does_not_mount_canonical_source(self):
        args = exchange_profile(self.root)
        self.assertIn('default_permissions='+json.dumps(PROFILE), args)
        filesystem = next(value for value in args if '.filesystem=' in value)
        self.assertIn(json.dumps(str(self.root))+'="read"', filesystem)
        self.assertIn(json.dumps(str(self.root/'output'))+'="write"', filesystem)
        self.assertEqual(filesystem.count('="write"'), 1)
        self.assertNotIn('/source-not-mounted', filesystem)
        self.assertIn('permissions.'+PROFILE+'.network.enabled=false', args)
        self.assertIn('":root"="deny"', filesystem)

    def test_launch_replaces_fixture_profile_without_broadening(self):
        adapter = self.adapter()
        prefix = ('codex', 'app-server', '--stdio')
        with patch.object(AppServerAdapter, '_launch', return_value=(prefix+profile_arguments(self.root), ['disabled'])):
            args, servers = adapter._launch()
        self.assertEqual(args, prefix+exchange_profile(self.root))
        self.assertEqual(servers, ['disabled'])
        self.assertEqual(adapter._child_environment()['CODEX_INTERNAL_APP_SERVER_REMOTE_CONTROL_DISABLED'], '1')

    def test_effective_profile_must_match_exact_isolated_mounts(self):
        adapter = self.adapter()
        filesystem = {':root':'deny', ':minimal':'read', ':tmpdir':'deny', ':slash_tmp':'deny',
                      str(self.root):'read', str(self.root/'output'):'write'}
        effective = {'default_permissions':PROFILE, 'web_search':'disabled',
                     'permissions':{PROFILE:{'filesystem':filesystem, 'network':{'enabled':False}}}}
        with patch.object(AppServerAdapter, 'verify_capabilities', return_value={'loaded':True}), \
                patch.object(adapter, 'call', return_value={'config':effective}) as call:
            self.assertTrue(adapter.verify_capabilities('thread-1')['effective_filesystem_verified'])
            call.assert_called_with('config/read', {'cwd':str(self.root), 'includeLayers':False})
            filesystem['glob_scan_max_depth'] = None
            self.assertTrue(adapter.verify_capabilities('thread-1')['effective_filesystem_verified'])
            mutations = [lambda c:c.update(default_permissions='broad'),
                         lambda c:c.update(web_search='live'),
                         lambda c:c['permissions'][PROFILE].update(extends='other'),
                         lambda c:c['permissions'][PROFILE]['network'].update(enabled=True),
                         lambda c:c['permissions'][PROFILE]['filesystem'].update({'/source-not-mounted':'read'}),
                         lambda c:c['permissions'][PROFILE]['filesystem'].update({str(self.root):'write'}),
                         lambda c:c['permissions'][PROFILE]['filesystem'].update(glob_scan_max_depth=20)]
            for mutate in mutations:
                altered = copy.deepcopy(effective); mutate(altered)
                call.return_value = {'config':altered}
                with self.subTest(effective=altered), self.assertRaises(ServiceError):
                    adapter.verify_capabilities('thread-1')

    def test_zero_model_probe_requires_all_isolation_observations(self):
        adapter = self.adapter()
        adapter.business_grant['story_exchange']['input_files'] = [{'path':name} for name in (
            'AGENTS.md', 'input/export.json', 'input/context.json', 'input/result-template.json', 'input/result-schema.json')]
        proof = {'readable_inputs':5, 'outside':[[13,13],[1,30],[2,13]],
                 'output_write':True, 'network_errno':1}
        response = {'exitCode':0,'stdout':json.dumps(proof)}
        with patch.object(adapter, 'check', return_value=response) as check:
            result = adapter.isolation_proof()
            self.assertEqual(result['model_turns'],0)
            self.assertEqual(result['outside_bytes_read'],0)
            command = check.call_args.args[0]
            self.assertEqual(command[:3],('/usr/bin/python3','-B','-c'))
            self.assertIn('os.O_RDONLY,os.O_WRONLY', command[3])
            self.assertIn('os.O_EXCL', command[3])
            mutations = [lambda p:p.update(readable_inputs=4),
                         lambda p:p.update(output_write=False),
                         lambda p:p.update(network_errno=None),
                         lambda p:p.update(outside=[[13,13],[13,13]]),
                         lambda p:p['outside'][0].__setitem__(0,None),
                         lambda p:p['outside'][1].__setitem__(1,0)]
            for mutate in mutations:
                altered=copy.deepcopy(proof);mutate(altered)
                check.return_value={'exitCode':0,'stdout':json.dumps(altered)}
                with self.subTest(proof=altered),self.assertRaises(ServiceError):adapter.isolation_proof()
            for response in ({'exitCode':1,'stdout':json.dumps(proof)}, {'exitCode':0,'stdout':'not json'},
                             {'exitCode':0,'stdout':'{}'}):
                check.return_value=response
                with self.subTest(response=response),self.assertRaises(ServiceError):adapter.isolation_proof()

    def test_exactly_one_new_thread_and_one_turn(self):
        adapter = self.adapter()
        with patch.object(AppServerAdapter, 'thread', return_value='thread-1') as thread, \
                patch.object(AppServerAdapter, 'start', return_value='turn-1') as start, \
                patch.object(AppServerAdapter, 'interrupt', return_value={}) as interrupt:
            with self.assertRaises(ServiceError): adapter.thread(previous='old-thread')
            self.assertEqual(adapter.thread(), 'thread-1')
            with self.assertRaises(ServiceError): adapter.thread()
            with self.assertRaises(ServiceError): adapter.start('wrong', 'text')
            with self.assertRaises(ServiceError): adapter.start('thread-1', 'text', image='image')
            self.assertEqual(adapter.start('thread-1', 'text'), 'turn-1')
            with self.assertRaises(ServiceError): adapter.start('thread-1', 'text')
            with self.assertRaises(ServiceError): adapter.interrupt('thread-1', 'wrong')
            adapter.interrupt('thread-1', 'turn-1')
            self.assertEqual((thread.call_count, start.call_count, interrupt.call_count), (1,1,1))

    def test_unbound_or_uncertain_thread_cannot_start_or_interrupt(self):
        adapter = self.adapter()
        with patch.object(AppServerAdapter, 'start', return_value='unexpected') as start, \
                patch.object(AppServerAdapter, 'interrupt', return_value={}) as interrupt:
            with self.assertRaises(ServiceError): adapter.start(None, 'text')
            with self.assertRaises(ServiceError): adapter.interrupt(None, None)
            adapter.owned_thread = 'dispatching'
            with self.assertRaises(ServiceError): adapter.start('dispatching', 'text')
            start.assert_not_called()
            interrupt.assert_not_called()

    def recovery_fixture(self):
        authority = {'purpose':'story_exchange_interrupted_resume', 'task_id':self.grant['task_id'],
                     'grant_sha256':digest(self.grant), 'thread_id':'prior-thread',
                     'interrupted_turn_id':'interrupted-turn', 'reconcile_event_key':'owned-reconciled',
                     'reason':'input_overread', 'effort':'medium', 'additional_turn_limit':1}
        self.state['pch_refactor']['r54_implementation']['native_execution_recovery']=authority
        events = [{'key':'owned-reconciled','data':{'original_thread_id':'prior-thread',
                   'original_turn_id':'interrupted-turn',
                   'action':'same_owned_thread_new_turn_after_verified_interruption'}}]
        task = {'intent':{'grant':copy.deepcopy(self.grant)}, 'thread_id':'prior-thread','turn_id':None}
        native = {'id':'prior-thread','cwd':str(self.root),
                  'turns':[{'id':'interrupted-turn','status':'interrupted'}]}
        return authority, events, task, native

    def test_registered_recovery_resumes_exact_thread_only_once(self):
        from unittest.mock import Mock
        authority,events,task,native=self.recovery_fixture()
        store=Mock();store.task.return_value=task;store.events.side_effect=lambda _:copy.deepcopy(events)
        store.event.side_effect=lambda _task,key,data:events.append({'key':key,'data':data})
        adapter=self.adapter()
        self.assertEqual(adapter._turn_overrides(),{})
        with self.assertRaises(ServiceError):adapter.recovery_text('original task')
        with patch('hub.task_store.TaskStore',return_value=store), \
                patch.object(adapter,'call',side_effect=[{'thread':native},{'thread':native}]) as call, \
                patch.object(adapter,'_profile_echo') as echo:
            self.assertEqual(adapter.thread(previous='prior-thread'),'prior-thread')
            self.assertEqual(call.call_args_list[0].args,('thread/read',{'threadId':'prior-thread','includeTurns':True}))
            self.assertEqual(call.call_args_list[1].args[0],'thread/resume')
            self.assertEqual(call.call_args_list[1].args[1]['permissions'],PROFILE)
            echo.assert_called_once()
            with self.assertRaises(ServiceError):adapter.thread(previous='prior-thread')
        self.assertEqual(adapter._turn_overrides(),{'effort':'medium'})
        recovery=adapter.recovery_text('original task')
        self.assertTrue(recovery.startswith('original task'))
        self.assertIn('120秒上限中断',recovery)
        self.assertIn('output/result.json',recovery)
        self.assertIn('3项分母保持原合同',recovery)
        second=self.adapter()
        with patch('hub.task_store.TaskStore',return_value=store),patch.object(second,'call') as call:
            with self.assertRaises(ServiceError):second.thread(previous='prior-thread')
            call.assert_not_called()

    def test_recovery_rejects_changed_authority_and_missing_event(self):
        from unittest.mock import Mock
        authority,events,task,native=self.recovery_fixture()
        store=Mock();store.task.return_value=task;store.events.return_value=events
        with patch('hub.task_store.TaskStore',return_value=store):
            for field in authority:
                old=authority[field];authority[field]='changed'
                adapter=self.adapter()
                with self.subTest(field=field),patch.object(adapter,'call') as call,self.assertRaises(ServiceError):
                    adapter.thread(previous='prior-thread')
                call.assert_not_called();authority[field]=old
            store.events.return_value=[]
            with self.assertRaises(ServiceError):self.adapter().thread(previous='prior-thread')
        store.event.assert_not_called()

    def test_recovery_requires_matching_reconcile_and_real_interrupted_turn(self):
        from unittest.mock import Mock
        authority,events,task,native=self.recovery_fixture()
        store=Mock();store.task.return_value=task;store.events.return_value=events
        with patch('hub.task_store.TaskStore',return_value=store):
            for field in ('original_thread_id','original_turn_id','action'):
                old=events[0]['data'][field];events[0]['data'][field]='wrong'
                adapter=self.adapter()
                with self.subTest(event_field=field),patch.object(adapter,'call') as call,self.assertRaises(ServiceError):
                    adapter.thread(previous='prior-thread')
                call.assert_not_called();events[0]['data'][field]=old
            for change in (lambda t:t.update(id='other-thread'),lambda t:t.update(cwd='/wrong'),
                           lambda t:t.update(turns=[]),lambda t:t['turns'][-1].update(id='other-turn'),
                           lambda t:t['turns'][-1].update(status='inProgress'),
                           lambda t:t['turns'][-1].update(status='completed')):
                altered=copy.deepcopy(native);change(altered)
                adapter=self.adapter()
                with self.subTest(native=altered),patch.object(adapter,'call',return_value={'thread':altered}) as call,self.assertRaises(ServiceError):
                    adapter.thread(previous='prior-thread')
                self.assertEqual(call.call_count,1)

    def test_uncertain_turn_dispatch_cannot_be_replayed(self):
        adapter = self.adapter(); adapter.owned_thread='thread-1'
        with patch.object(AppServerAdapter, 'start', side_effect=TimeoutError('unknown send outcome')) as start:
            with self.assertRaises(TimeoutError): adapter.start('thread-1', 'text')
            with self.assertRaises(ServiceError): adapter.start('thread-1', 'text')
            self.assertEqual(start.call_count, 1)

    def test_uncertain_thread_allocation_cannot_be_replayed(self):
        adapter = self.adapter()
        with patch.object(AppServerAdapter, 'thread', side_effect=TimeoutError('unknown send outcome')) as thread:
            with self.assertRaises(TimeoutError): adapter.thread()
            with self.assertRaises(ServiceError): adapter.thread()
            self.assertEqual(thread.call_count, 1)


if __name__ == '__main__': unittest.main()
