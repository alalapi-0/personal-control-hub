"""Real-classification assembly with synthetic disks and controlled providers only.

These checks exercise the explicit production-mode contract, not real business,
Owner, or provider acceptance. No Codex process or external source is opened.
"""
import copy
import hashlib
import time
import unittest
from unittest import mock

import test_hub_ordinary_worker as fixtures
import test_hub_server_entry as entry_fixtures
from pch_functional_fixture import FunctionalFixture, ControlledAdapter
from hub.ordinary_runtime import OrdinaryRuntime
from hub.ordinary_tasks import make_grant, prompt
from hub.service_contract import ServiceError
from hub.task_store import TaskStore
from hub.workbench_store import digest


class RealOrdinaryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.OrdinaryWorkerTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = self.f.store
        self.grant = make_grant(self.f.root, self.f.grant['project_id'], self.f.registry_hash,
                               self.f.grant['grant_id'], input_paths=['README.md'], classification='real',version=2)
        self.store.register_grant(self.grant)
        class Adapter(self.f.Adapter):
            created, interrupts, closed = [], [], []
            hold = False
            def start(self, thread, text, image):
                if image is not None: raise AssertionError('ordinary text must not send an image')
                self.events = [self.event('turn/started', turn={'id':'ordinary-turn','status':'inProgress'}),
                    self.event('item/completed', item={'id':'reply','type':'agentMessage','text':'Controlled component answer'}),
                    self.event('turn/completed', turn={'id':'ordinary-turn','status':'completed'})]
                return 'ordinary-turn'
            def poll(self, timeout):
                if self.hold and self.events[0]['method'] != 'turn/started':
                    time.sleep(.005)
                    return None
                return self.events.pop(0)
            def interrupt(self, thread, turn):
                self.hold = False
                super().interrupt(thread, turn)
            def close(self): self.closed.append(self)
        self.Adapter = Adapter

    def runtime(self, **changes):
        options = dict(grants=[self.grant], provider_status={'available':True,'classification':'real'},
                       adapter_factory=self.Adapter, grant_classification='real', poll_interval=.01)
        options.update(changes)
        runtime = OrdinaryRuntime(self.store, **options)
        self.addCleanup(runtime.stop)
        return runtime

    def admit(self, ident, grant=None):
        grant = self.grant if grant is None else grant
        spec = {**self.f.spec, 'request_id':ident, 'input_version':grant['input_version'],
                'project_id':grant['project_id']}
        intent = {**self.f.intent, 'grant':grant, 'grant_hash':digest(grant), 'task_spec':spec,
                  'prompt_sha256':hashlib.sha256(prompt(spec,input_files=grant['input_files']).encode()).hexdigest()}
        self.store.admit(spec, intent)

    def wait(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.01)
        self.fail('controlled runtime did not reach the expected state')

    def test_real_queue_is_serial_and_reopened_result_retains_classification(self):
        runtime = self.runtime()
        self.Adapter.hold = True
        self.admit('first'); self.admit('second')
        runtime.start()
        self.wait(lambda:self.store.task('first')['turn_id'] is not None)
        self.assertEqual(runtime.status()['worker_runtime']['classification'], 'real')
        time.sleep(.08)
        self.assertEqual(len(self.Adapter.created), 1)
        self.assertEqual(self.store.task('second')['status'], 'queued')
        # TaskStore's existing fixture-only parallel exception must stay closed.
        self.assertIsNone(self.store.claim('foreign-worker', only_task_id='second'))
        self.Adapter.hold = False
        self.Adapter.created[0].hold = False
        self.wait(lambda:self.store.task('second')['status'] == 'checks_complete')
        runtime.stop()
        reopened = TaskStore(self.store.root)
        for ident in ('first','second'):
            task = reopened.task(ident)
            self.assertEqual(task['status'], 'checks_complete')
            self.assertEqual(task['result']['classification'], 'real')
            self.assertEqual(task['result']['provider_reply'], 'Controlled component answer')
            self.assertEqual(task['human_acceptance'], 'pending')
        self.assertEqual(len(self.Adapter.closed), 2)
        self.assertEqual(self.f.source.read_text(), 'Synthetic exact input\n')

    def test_stop_cancels_owned_only_and_runtime_cannot_restart(self):
        runtime = self.runtime()
        self.Adapter.hold = True
        self.admit('owned'); self.admit('waiting')
        runtime.start()
        self.wait(lambda:self.store.task('owned')['turn_id'] is not None)
        runtime.stop(timeout=2)
        self.assertTrue(self.store.task('owned')['cancel'])
        self.assertFalse(self.store.task('waiting')['cancel'])
        self.assertEqual(self.store.task('waiting')['status'], 'queued')
        self.assertEqual(self.Adapter.interrupts, [('ordinary-thread','ordinary-turn')])
        self.assertEqual(len(self.Adapter.closed), 1)
        with self.assertRaises(ServiceError) as error: runtime.start()
        self.assertEqual(error.exception.code, 'ORDINARY_RUNTIME_RESTART_REJECTED')

    def test_foreign_active_claim_is_neither_adopted_nor_cancelled(self):
        self.admit('foreign')
        self.store.claim('existing-owner', only_task_id='foreign')
        runtime = self.runtime(); runtime.start(); time.sleep(.05); runtime.stop()
        task = self.store.task('foreign')
        self.assertEqual(task['owner'], 'existing-owner')
        self.assertEqual(task['status'], 'running')
        self.assertFalse(task['cancel'])
        self.assertEqual(self.Adapter.created, [])

    def test_registration_input_and_revocation_changes_fail_closed_before_adapter(self):
        for change in ('registration','input','revocation','expired'):
            with self.subTest(change=change):
                other = RealOrdinaryRuntimeTests(); other.setUp()
                try:
                    runtime = other.runtime(grants=[{**other.grant,'expires_at':time.time()-1}]) if change=='expired' else other.runtime()
                    if change == 'registration': other.f.registry_path.write_text('projects: []\n')
                    elif change == 'input': other.f.source.write_text('changed')
                    elif change == 'revocation': other.store.register_grant({**other.grant,'version':3})
                    else:
                        expired = runtime._grants[0]
                        # Use an expired exact historical registration; do not create a fresh grant.
                        with mock.patch.object(other.store, 'grant', return_value=expired):
                            with self.assertRaises(ServiceError) as error: runtime.start()
                        self.assertEqual(error.exception.code,'GRANT_EXPIRED')
                        self.assertEqual(other.Adapter.created, [])
                        continue
                    with self.assertRaises(ServiceError): runtime.start()
                    self.assertFalse(runtime.writer_check(other.grant))
                    self.assertEqual(other.Adapter.created, [])
                finally: other.doCleanups()

    def test_active_grant_revocation_never_publishes_success(self):
        self.Adapter.hold = True
        self.admit('active')
        runtime = self.runtime(); runtime.start()
        self.wait(lambda:self.store.task('active')['turn_id'] is not None)
        self.store.register_grant({**self.grant,'version':3})
        self.wait(lambda:self.store.task('active')['status'] == 'requires_reconcile')
        self.assertFalse(runtime.writer_check(self.grant))
        self.assertNotEqual(self.store.task('active')['provider_status'], 'completed')

    def test_default_mode_rejects_real_grant_and_explicit_mode_rejects_mixed_or_wrong_provider(self):
        with self.assertRaises(ServiceError):
            OrdinaryRuntime(self.store, grants=[self.grant], provider_status={'available':True,'classification':'real'})
        synthetic = {**self.grant,'grant_id':'synthetic-other','classification':'synthetic_fixture'}
        for changes in (dict(grants=[self.grant,synthetic]), dict(grants=[synthetic]),
                        dict(provider_status={'available':True,'classification':'synthetic_fixture'}),
                        dict(provider_status={'available':True,'classification':'unknown'}),
                        dict(grant_classification='unknown')):
            with self.subTest(changes=changes), self.assertRaises(ServiceError): self.runtime(**changes)
        self.assertEqual(self.Adapter.created, [])

    def test_real_http_admission_persists_queue_and_reopens_completed_result(self):
        f = FunctionalFixture(); self.addCleanup(f.close)
        f.ordinary_sample(); f.handshake()
        f.scenarios['fixture-project-a']='delay'
        grants = [{**g,'classification':'real','version':2} for g in f.ordinary_grants.values()]
        for grant in grants: f.task_store.register_grant(grant)
        runtime = OrdinaryRuntime(f.task_store, grants=grants, grant_classification='real',
            provider_status={'available':True,'classification':'real'}, poll_interval=.01,
            adapter_factory=lambda root:ControlledAdapter(f,root,ordinary=True))
        f.ordinary_runtime = runtime
        f.assembly.attach_ordinary_runtime(f.server, runtime)
        runtime.start()
        project = 'fixture-project-a'
        status, _, context = f.call(path='/api/ordinary-context?project_id='+project)
        self.assertEqual(status, 200, context)
        self.assertTrue(context['data']['ready'])
        self.assertEqual(context['data']['capabilities']['ordinary_execution']['classification'], 'real')
        spec = dict(kind='ordinary_project',request_id='real-contract-http',project_id=project,
            objective='Summarize synthetic registered text',scope='Only the registered inputs',effect='read_only',
            input_version=context['data']['input_version'],mode='new',parent_task_id=None)
        status, _, admitted = f.call('POST','/api/tasks',spec)
        self.assertEqual(status, 200, admitted)
        self.assertEqual(f.task_store.task(spec['request_id'])['payload'], spec)
        self.wait(lambda:f.task_store.task(spec['request_id'])['turn_id'] is not None)
        second = {**spec,'request_id':'real-contract-http-b','project_id':'fixture-project-b',
                  'input_version':next(g['input_version'] for g in grants if g['project_id']=='fixture-project-b')}
        self.assertEqual(f.call('POST','/api/tasks',second)[0],200)
        time.sleep(.08)
        self.assertEqual(f.task_store.task(second['request_id'])['status'],'queued')
        self.assertEqual(len(f.ordinary_adapters),1)
        self.assertIsNone(f.task_store.claim('foreign-worker',only_task_id=second['request_id']))
        f.release_ordinary(spec['request_id'])
        self.wait(lambda:f.task_store.task(second['request_id'])['status']=='checks_complete')
        self.wait(lambda:f.task_store.task(spec['request_id'])['status']=='checks_complete')
        status, _, reopened = f.call(path='/api/tasks/'+spec['request_id'])
        self.assertEqual(status, 200, reopened)
        task = reopened['data']['task']
        self.assertEqual(task['result']['classification'], 'real')
        self.assertIn('Synthetic read-only response',task['result']['response'])
        self.assertEqual(task['human_acceptance'],'pending')
        self.assertEqual(f.effects, [])
        self.assertTrue(f.call('POST','/api/tasks',spec)[2]['data']['replayed'])


class RealOrdinaryEntryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.OrdinaryWorkerTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.entry = entry_fixtures.ServerEntryTests(); self.entry.setUp(); self.addCleanup(self.entry.doCleanups)
        self.grant = make_grant(self.f.root,self.f.grant['project_id'],self.f.registry_hash,
                               self.f.grant['grant_id'],input_paths=['README.md'],classification='real',version=2)
        self.f.store.register_grant(self.grant)

    def run_entry(self, flag='--ordinary-real-readonly-grant', ident=None):
        return self.entry.run_entry(['--root',str(self.f.store.root),flag,ident or self.grant['grant_id']])

    def test_explicit_real_flag_binds_exact_existing_grant_and_real_mode(self):
        runtimes=[]
        class Runtime:
            def __init__(self,store,**kwargs):
                self.store=store;self.kwargs=kwargs;self.started=False;self.stopped=False;runtimes.append(self)
            def writer_check(self,grant): return False
            def status(self): return {}
            def start(self): self.started=True
            def stop(self): self.stopped=True
        with mock.patch('hub.ordinary_runtime.OrdinaryRuntime',Runtime), \
             mock.patch.object(self.entry.entry,'ordinary_provider_status',return_value={'available':True,'classification':'real'}) as probe, \
             mock.patch.object(TaskStore,'register_storage',side_effect=AssertionError('implicit storage registration')), \
             mock.patch.object(TaskStore,'register_grant',side_effect=AssertionError('implicit authority creation')):
            code,state=self.run_entry()
        self.assertEqual(code,0);self.assertTrue(state['served']);self.assertEqual(probe.call_count,1)
        self.assertEqual(len(runtimes),1)
        runtime=runtimes[0]
        self.assertEqual(runtime.kwargs['grants'],[self.grant])
        self.assertEqual(runtime.kwargs['grant_classification'],'real')
        self.assertTrue(runtime.started and runtime.stopped)
        self.assertEqual(runtime.store.root,self.f.store.root)

    def test_invalid_registration_classes_expiry_and_input_fail_before_provider(self):
        for change in ('missing','synthetic','expired','registration','input'):
            with self.subTest(change=change):
                original=self.f.registry_path.read_bytes();source=self.f.source.read_bytes()
                grant=copy.deepcopy(self.grant)
                if change=='synthetic':grant['classification']='synthetic_fixture'
                if change=='expired':grant['expires_at']=time.time()-1
                if change=='registration':self.f.registry_path.write_text('projects: []\n')
                if change=='input':self.f.source.write_text('changed')
                try:
                    with mock.patch.object(TaskStore,'grant',return_value=None if change=='missing' else grant), \
                         mock.patch.object(self.entry.entry,'ordinary_provider_status',side_effect=AssertionError('provider probed before grant guards')), \
                         mock.patch('hub.ordinary_runtime.OrdinaryRuntime',side_effect=AssertionError('runtime constructed before guard')), \
                         mock.patch.object(TaskStore,'register_storage',side_effect=AssertionError('implicit storage registration')), \
                         mock.patch.object(TaskStore,'register_grant',side_effect=AssertionError('implicit authority creation')):
                        code,state=self.run_entry()
                    self.assertEqual(code,1);self.assertNotIn('served',state)
                finally:
                    self.f.registry_path.write_bytes(original);self.f.source.write_bytes(source)

    def test_real_and_fixture_flags_or_preview_reject_before_assembly(self):
        for extra in (['--ordinary-readonly-grant','fixture'],['--readonly-preview','computer-study-plan']):
            with self.subTest(extra=extra), \
                 mock.patch.object(self.entry.entry,'assemble_server',side_effect=AssertionError('assembly before incompatible CLI guards')):
                with self.assertRaises(SystemExit):
                    self.entry.run_entry(['--root',str(self.f.store.root),'--ordinary-real-readonly-grant',self.grant['grant_id'],*extra])

    def test_duplicate_or_mixed_registered_grants_fail_before_provider(self):
        for second, grant in ((self.grant['grant_id'],self.grant),
                              ('other-synthetic',{**self.grant,'grant_id':'other-synthetic','classification':'synthetic_fixture'})):
            with self.subTest(second=second), \
                 mock.patch.object(TaskStore,'grant',side_effect=lambda ident:self.grant if ident==self.grant['grant_id'] else grant), \
                 mock.patch.object(self.entry.entry,'ordinary_provider_status',side_effect=AssertionError('unexpected probe')):
                code,state=self.entry.run_entry(['--root',str(self.f.store.root),'--ordinary-real-readonly-grant',
                    self.grant['grant_id'],'--ordinary-real-readonly-grant',second])
            self.assertEqual(code,1);self.assertNotIn('served',state)

    def test_fixture_flag_rejects_real_registration_before_provider(self):
        with mock.patch.object(self.entry.entry,'ordinary_provider_status',side_effect=AssertionError('unexpected probe')):
            code,state=self.run_entry('--ordinary-readonly-grant')
        self.assertEqual(code,1);self.assertNotIn('served',state)


if __name__ == '__main__': unittest.main()
