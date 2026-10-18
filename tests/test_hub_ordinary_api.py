"""Real loopback ordinary requests cannot mint filesystem or native authority."""
import copy
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pch_functional_fixture import FunctionalFixture,BEFORE,ControlledAdapter
from hub.ordinary_tasks import validate_spec
from hub.ordinary_runtime import OrdinaryRuntime
from hub.task_service import TaskService
from hub.task_store import MAX_TASKS
from hub.workbench_store import digest
from hub.service_contract import ServiceError


class OrdinaryAPITests(unittest.TestCase):
    def setUp(self):
        self.f=FunctionalFixture();self.addCleanup(self.f.close)
        self.f.ordinary_sample();self.f.handshake()
        self.project='fixture-project-a'

    def context(self):
        status,_,body=self.f.call(path='/api/ordinary-context?project_id='+self.project)
        self.assertEqual(status,200,body);return body['data']

    def spec(self,ident='ordinary-http'):
        return {'kind':'ordinary_project','request_id':ident,'project_id':self.project,
                'objective':'Read the registered synthetic inputs','scope':'Summarize as text without changing files',
                'effect':'read_only','input_version':self.context()['input_version'],'mode':'new','parent_task_id':None}

    def post(self,spec):return self.f.call('POST','/api/tasks',spec)

    def test_real_http_text_without_annotation_replay_and_worker_result(self):
        spec=self.spec();status,_,body=self.post(spec);self.assertEqual(status,200,body)
        task=body['data']['task'];self.assertEqual(task['kind'],'ordinary_project')
        self.assertIsNone(task['annotation_id']);self.assertEqual(task['task_spec'],spec)
        status,_,replay=self.post(spec);self.assertEqual(status,200,replay);self.assertTrue(replay['data']['replayed'])
        self.assertEqual(len(self.f.task_store.tasks()),1)
        self.assertTrue(self.f.run_worker(self.project,task_id=spec['request_id']))
        status,_,actual=self.f.call(path='/api/tasks/'+spec['request_id']);self.assertEqual(status,200,actual)
        task=actual['data']['task'];self.assertEqual(task['status'],'checks_complete')
        self.assertIn('Synthetic read-only response',task['result']['response'])
        self.assertNotIn('provider_reply',task['result']);self.assertEqual(task['human_acceptance'],'pending')
        self.assertEqual(self.f.effects,[])
        self.assertTrue(all((root/'index.html').read_text()==BEFORE for root in self.f.roots.values()))

    def test_client_authority_and_stale_inputs_rejected_without_admission(self):
        for key,value in [('cwd','/tmp/other'),('grant_id','invented'),('thread_id','unowned'),('profile','write'),('workspace','/tmp/foreign'),
                          ('grant_ids',['invented']),('ordinary_grant_ids',['invented'])]:
            with self.subTest(key=key):
                status,_,_=self.post({**self.spec(),key:value});self.assertEqual(status,400)
        for patch,expected in [({'effect':'write'},403),({'input_version':'0'*64},409),
                               ({'mode':'idle_continue','parent_task_id':'foreign'},409)]:
            status,_,_=self.post({**self.spec(),**patch});self.assertEqual(status,expected)
        self.assertEqual(self.f.task_store.tasks(),[])
        for value in ([],{},42):
            with self.assertRaises(ServiceError):validate_spec({**self.spec(),'mode':value})

    def test_draft_owner_target_scope_and_post_auth_guard(self):
        spec=self.spec();context=self.context()
        command={'request_id':'save-ordinary','expected_revision':0,'project_id':self.project,'parent_task_id':None,'value':spec}
        status,_,body=self.f.call('POST','/api/ordinary-drafts',command);self.assertEqual(status,200,body)
        self.assertEqual(self.context()['draft']['value'],spec)
        self.assertEqual(self.f.task_store.tasks(),[])
        self.assertNotEqual(self.f.tasks.ordinary_context(self.project,owner_context='different-owner')['draft_scope'],context['draft_scope'])
        self.assertIsNone(self.f.tasks.ordinary_context(self.project,owner_context='different-owner')['draft']['value'])
        self.assertEqual(self.f.call('POST','/api/tasks',spec,{'X-Hub-CSRF':'wrong'})[0],403)

    def test_registry_exclusion_precedes_input_probe_and_capability_gate(self):
        spec=self.spec()
        resolver=self.f.projects._resolver();resolver.projects[self.project]['connection_read_allowed']=False
        with mock.patch.object(self.f.projects,'_management_context',return_value=(set(),[])),\
             mock.patch('hub.ordinary_tasks._read_input',side_effect=AssertionError('excluded root read')):
            self.assertEqual(self.post(spec)[0],403)
        self.f.tasks.ordinary_enabled=False
        self.assertEqual(self.post(spec)[0],503);self.assertEqual(self.f.task_store.tasks(),[])

    def test_empty_default_and_changed_request_do_not_invent_acceptance(self):
        spec=self.spec();self.assertEqual(self.post(spec)[0],200)
        status,_,body=self.post({**spec,'objective':'A different request under the old identity'})
        self.assertEqual(status,409,body);self.assertEqual(body['error']['code'],'TASK_REQUEST_CONFLICT')
        task=self.f.task_store.task(spec['request_id']);self.assertEqual(task['status'],'queued');self.assertIsNone(task['result'])

    def selected(self, *grants):
        self.f.tasks.ordinary_grant_ids = tuple(grant['grant_id'] for grant in grants)

    def history_grant(self, ident, **changes):
        grant = {**copy.deepcopy(self.f.ordinary_grants[self.project]), 'grant_id': ident, **changes}
        self.f.task_store.register_grant(grant)
        return grant

    def assert_denied_without_admission(self, spec):
        context = self.context()
        self.assertFalse(context['ready'], context)
        status, _, body = self.post(spec)
        self.assertIn(status, (400, 403, 409, 503), body)
        self.assertEqual(self.f.task_store.tasks(), [])
        self.assertEqual(self.f.ordinary_adapters, [])
        self.assertEqual(self.f.effects, [])

    def test_expired_persisted_history_is_excluded_before_standalone_ambiguity(self):
        historical_spec = self.spec('existing-history')
        self.assertEqual(self.post(historical_spec)[0], 200)
        historical = self.f.task_store.task(historical_spec['request_id'])
        events = self.f.task_store.events(historical_spec['request_id'])
        spec = self.spec('expired-history')
        current = copy.deepcopy(self.f.ordinary_grants[self.project])
        old = self.history_grant('historical-expired', expires_at=time.time()-3600)
        status, _, body = self.post(spec)
        self.assertEqual(status, 200, body)
        task = self.f.task_store.task(spec['request_id'])
        self.assertEqual(task['intent']['grant'], current)
        self.assertEqual(task['intent']['grant_hash'], digest(current))
        self.assertEqual(self.f.task_store.grant(old['grant_id']), old)
        self.assertEqual(self.f.task_store.task(historical_spec['request_id']), historical)
        self.assertEqual(self.f.task_store.events(historical_spec['request_id']), events)

    def test_exact_selected_current_ignores_active_expired_and_cross_project_history(self):
        spec = self.spec('selected-current')
        current = copy.deepcopy(self.f.ordinary_grants[self.project])
        active = self.history_grant('unselected-active')
        old = self.history_grant('unselected-expired', expires_at=time.time()-3600)
        self.selected(current)
        self.assertTrue(self.context()['ready'])
        status, _, body = self.post(spec)
        self.assertEqual(status, 200, body)
        task = self.f.task_store.task(spec['request_id'])
        self.assertEqual(task['intent']['grant'], current)
        self.assertEqual(task['intent']['grant_hash'], digest(current))
        self.assertEqual(self.f.task_store.grant(active['grant_id']), active)
        self.assertEqual(self.f.task_store.grant(old['grant_id']), old)
        self.project = 'fixture-project-b'
        self.assertFalse(self.context()['ready'])
        status, _, _ = self.post({**spec, 'request_id':'unselected-other-project', 'project_id':self.project,
            'input_version':self.f.ordinary_grants[self.project]['input_version']})
        self.assertIn(status, (403, 409))
        self.assertEqual(len(self.f.task_store.tasks()), 1)

    def test_two_valid_selected_grants_remain_ambiguous_but_expired_selected_history_does_not(self):
        spec = self.spec('two-selected')
        current = self.f.ordinary_grants[self.project]
        second = self.history_grant('second-selected')
        self.selected(current, second)
        context = self.context()
        self.assertFalse(context['ready'])
        self.assertEqual(context['reason_code'], 'ORDINARY_PROJECT_GRANT_AMBIGUOUS')
        self.assertEqual(self.post(spec)[0], 409)
        self.assertEqual(self.f.task_store.tasks(), [])
        expired = {**second, 'version':2, 'expires_at':time.time()-3600}
        self.f.task_store.register_grant(expired)
        self.assertTrue(self.context()['ready'])
        self.assertEqual(self.post(spec)[0], 200)
        self.assertEqual(self.f.task_store.task(spec['request_id'])['intent']['grant'], current)
        self.assertEqual(self.f.task_store.grant(second['grant_id']), expired)

    def test_missing_deleted_expired_revoked_and_malformed_selected_never_fall_back(self):
        spec = self.spec('invalid-selected')
        current = self.f.ordinary_grants[self.project]
        self.history_grant('unrelated-valid')
        # All variants use persisted records; an unrelated valid registration remains available.
        for label, changes in [('expired', {'expires_at':time.time()-1}),
                               ('revoked', {'effect':'write'}),
                               ('malformed', {'source':'unverified'}),
                               ('bad-time-type', {'expires_at':True}),
                               ('too-far-future', {'expires_at':time.time()+172800}),
                               ('malformed-even-expired', {'expires_at':time.time()-1, 'source':'unverified'})]:
            with self.subTest(label=label):
                invalid = self.history_grant('selected-'+label, **changes)
                self.selected(invalid)
                self.assert_denied_without_admission(spec)
        self.f.tasks.ordinary_grant_ids = ('never-registered',)
        self.assert_denied_without_admission(spec)
        self.selected(current)
        with self.f.task_store._connection(True) as connection:
            connection.execute('DELETE FROM grants WHERE id=?', (current['grant_id'],))
        self.assert_denied_without_admission(spec)

    def test_live_selected_input_root_and_registry_drift_reject_without_fallback(self):
        spec = self.spec('selected-live-drift')
        current = self.f.ordinary_grants[self.project]
        self.selected(current)
        self.history_grant('unselected-live-fallback')
        for field, value in [('root', str(self.f.roots['fixture-project-b'])),
                             ('root_identity', '0'*64), ('registry_hash', '0'*64)]:
            with self.subTest(field=field):
                invalid = self.history_grant('selected-'+field, **{field:value})
                self.selected(invalid)
                self.assert_denied_without_admission(spec)
        self.selected(current)
        self.f.ordinary_input(self.project)
        self.assert_denied_without_admission(spec)

    def test_expired_malformed_selected_record_cannot_hide_beside_valid_selected_grant(self):
        spec = self.spec('malformed-expired-selected')
        current = self.f.ordinary_grants[self.project]
        for label, changes in [('expired-metadata', {'expires_at':time.time()-3600, 'input_files':[]}),
                               ('future-metadata', {'expires_at':time.time()+172800}),
                               ('boolean-time', {'expires_at':True})]:
            with self.subTest(label=label):
                malformed = self.history_grant('selected-invalid-'+label, **changes)
                self.selected(current, malformed)
                context = self.context()
                self.assertFalse(context['ready'], context)
                self.assertNotEqual(context['reason_code'], 'ORDINARY_PROJECT_GRANT_AMBIGUOUS')
                self.assert_denied_without_admission(spec)

    def test_actual_selected_registry_and_root_identity_drift_are_live_checks(self):
        spec = self.spec('selected-live-bindings')
        self.selected(self.f.ordinary_grants[self.project])
        self.history_grant('unselected-bindings-fallback')
        registry = self.f.root/'data/registry/external_projects.yaml'
        original = registry.read_bytes()
        try:
            registry.write_bytes(original+b'\n# synthetic registry drift\n')
            self.assert_denied_without_admission(spec)
        finally:
            registry.write_bytes(original)
        root = self.f.roots[self.project]
        moved = root.with_name(root.name+'-original')
        root.rename(moved)
        root.mkdir()
        try:
            for name in ('AGENTS.md', 'README.md'):
                (root/name).write_bytes((moved/name).read_bytes())
            self.assert_denied_without_admission(spec)
        finally:
            for child in root.iterdir():
                child.unlink()
            root.rmdir()
            moved.rename(root)

    def test_runtime_replaced_selected_registration_never_falls_back(self):
        spec = self.spec('replaced-selected-runtime')
        current = copy.deepcopy(self.f.ordinary_grants[self.project])
        runtime = OrdinaryRuntime(self.f.task_store, grants=[current],
            provider_status={'available':True,'classification':'synthetic_fixture'}, poll_interval=.01,
            adapter_factory=lambda root:ControlledAdapter(self.f, root, ordinary=True))
        self.f.ordinary_runtime = runtime
        self.f.assembly.attach_ordinary_runtime(self.f.server, runtime)
        runtime.start()
        self.history_grant('active-after-replacement')
        self.f.task_store.register_grant({**current, 'version':current['version']+1})
        self.assert_denied_without_admission(spec)

    def attach_selected_runtime(self, grants):
        runtime = OrdinaryRuntime(self.f.task_store, grants=grants,
            provider_status={'available':True,'classification':'synthetic_fixture'}, poll_interval=.01,
            adapter_factory=lambda root:ControlledAdapter(self.f, root, ordinary=True))
        self.f.ordinary_runtime = runtime
        self.f.assembly.attach_ordinary_runtime(self.f.server, runtime)
        runtime.start()
        return runtime

    def assert_partial_selected_deletion_denied(self, index):
        spec = self.spec('partial-selected-deletion')
        current = copy.deepcopy(self.f.ordinary_grants[self.project])
        second = self.history_grant('second-selected-for-deletion')
        unrelated = self.history_grant('unrelated-valid-during-deletion')
        selected = [current, second]
        runtime = self.attach_selected_runtime(selected)
        self.assertEqual(self.context()['reason_code'], 'ORDINARY_PROJECT_GRANT_AMBIGUOUS')
        self.assertEqual(self.post(spec)[0], 409)
        with self.f.task_store._connection(True) as connection:
            connection.execute('DELETE FROM grants WHERE id=?', (selected[index]['grant_id'],))
        self.assertIsNone(self.f.task_store.grant(selected[index]['grant_id']))
        self.assertEqual(self.f.task_store.grant(selected[1-index]['grant_id']), selected[1-index])
        self.assertTrue(runtime.status()['worker_runtime']['available'])
        self.assert_denied_without_admission(spec)
        self.assertEqual(self.f.task_store.grant(unrelated['grant_id']), unrelated)

    def test_runtime_missing_first_selected_same_project_cannot_authorize_survivor(self):
        self.assert_partial_selected_deletion_denied(0)

    def test_runtime_missing_second_selected_same_project_cannot_authorize_survivor(self):
        self.assert_partial_selected_deletion_denied(1)

    def test_runtime_selected_project_rebinding_cannot_collapse_original_ambiguity(self):
        spec = self.spec('selected-project-rebinding')
        current = copy.deepcopy(self.f.ordinary_grants[self.project])
        second = self.history_grant('second-selected-for-rebinding')
        unrelated = self.history_grant('unrelated-valid-during-rebinding')
        runtime = self.attach_selected_runtime([current, second])
        self.assertEqual(self.context()['reason_code'], 'ORDINARY_PROJECT_GRANT_AMBIGUOUS')
        other = self.f.ordinary_grants['fixture-project-b']
        rebound = {**copy.deepcopy(other), 'grant_id':second['grant_id'], 'version':second['version']+1}
        self.f.task_store.register_grant(rebound)
        self.assertTrue(runtime.status()['worker_runtime']['available'])
        self.assert_denied_without_admission(spec)
        self.assertEqual(self.f.task_store.grant(current['grant_id']), current)
        self.assertEqual(self.f.task_store.grant(unrelated['grant_id']), unrelated)

    def test_runtime_selected_kind_replacement_cannot_collapse_original_ambiguity(self):
        spec = self.spec('selected-kind-replacement')
        current = copy.deepcopy(self.f.ordinary_grants[self.project])
        second = self.history_grant('second-selected-for-kind-replacement')
        unrelated = self.history_grant('unrelated-valid-during-kind-replacement')
        self.attach_selected_runtime([current, second])
        self.assertEqual(self.context()['reason_code'], 'ORDINARY_PROJECT_GRANT_AMBIGUOUS')
        self.f.task_store.register_grant({**second, 'version':second['version']+1, 'kind':'css_trial'})
        self.assert_denied_without_admission(spec)
        self.assertEqual(self.f.task_store.grant(current['grant_id']), current)
        self.assertEqual(self.f.task_store.grant(unrelated['grant_id']), unrelated)

    def test_missing_selected_other_project_does_not_block_valid_selected_project(self):
        spec = self.spec('selected-project-independent')
        current = copy.deepcopy(self.f.ordinary_grants[self.project])
        other = copy.deepcopy(self.f.ordinary_grants['fixture-project-b'])
        unrelated = self.history_grant('unrelated-independent-history')
        self.attach_selected_runtime([current, other])
        with self.f.task_store._connection(True) as connection:
            connection.execute('DELETE FROM grants WHERE id=?', (other['grant_id'],))
        self.assertTrue(self.context()['ready'])
        status, _, body = self.post(spec)
        self.assertEqual(status, 200, body)
        deadline = time.monotonic()+4
        while time.monotonic()<deadline:
            if self.f.task_store.task(spec['request_id'])['status']=='checks_complete':
                break
            time.sleep(.01)
        task = self.f.task_store.task(spec['request_id'])
        self.assertEqual(task['status'], 'checks_complete')
        self.assertEqual(task['intent']['grant'], current)
        self.assertEqual(len(self.f.ordinary_adapters), 1)
        self.assertEqual(self.f.ordinary_adapters[0].root, self.f.roots[self.project])
        self.assertEqual(self.f.task_store.grant(unrelated['grant_id']), unrelated)
        self.assertEqual(self.f.effects, [])
        self.project = 'fixture-project-b'
        self.assertFalse(self.context()['ready'])

    def test_late_attachment_keeps_original_runtime_project_bindings(self):
        spec=self.spec('late-attach-project-drift')
        current=copy.deepcopy(self.f.ordinary_grants[self.project])
        second=self.history_grant('second-late-attach')
        runtime=OrdinaryRuntime(self.f.task_store,grants=[current,second],
            provider_status={'available':True,'classification':'synthetic_fixture'},poll_interval=.01,
            adapter_factory=lambda root:ControlledAdapter(self.f,root,ordinary=True))
        self.f.ordinary_runtime=runtime
        runtime.start()
        moved={**copy.deepcopy(self.f.ordinary_grants['fixture-project-b']),
               'grant_id':current['grant_id'],'version':current['version']+1}
        self.f.task_store.register_grant(moved)
        self.f.assembly.attach_ordinary_runtime(self.f.server,runtime)
        self.assert_denied_without_admission(spec)
        self.assertEqual(self.f.task_store.grant(second['grant_id']),second)

    def test_nonfinite_expiry_cannot_validate_as_historical_metadata(self):
        from hub.ordinary_tasks import validate_grant
        current=self.f.ordinary_grants[self.project]
        for value in (float('nan'),float('inf'),float('-inf')):
            with self.subTest(value=value),self.assertRaises(ServiceError)as raised:
                validate_grant({**current,'expires_at':value})
            self.assertEqual(raised.exception.code,'INVALID_ORDINARY_GRANT')

    def test_selected_history_deleted_after_preflight_rolls_back_transaction_admission(self):
        spec = self.spec('partial-scope-transaction')
        current = copy.deepcopy(self.f.ordinary_grants[self.project])
        expired = self.history_grant('selected-expired-before-transaction', expires_at=time.time()-3600)
        unrelated = self.history_grant('unselected-transaction-fallback')
        self.selected(current, expired)
        self.assertTrue(self.context()['ready'])
        original = self.f.task_store.admit
        reached = []
        def delete_then_admit(payload, intent, *, authorize=None):
            reached.append(True)
            with self.f.task_store._connection(True) as connection:
                connection.execute('DELETE FROM grants WHERE id=?', (expired['grant_id'],))
            return original(payload, intent, authorize=authorize)
        with mock.patch.object(self.f.task_store, 'admit', side_effect=delete_then_admit):
            status, _, body = self.post(spec)
        self.assertIn(status, (403, 409), body)
        self.assertEqual(reached, [True])
        self.assertIsNone(self.f.task_store.grant(expired['grant_id']))
        self.assert_denied_without_admission(spec)
        self.assertEqual(self.f.task_store.grant(current['grant_id']), current)
        self.assertEqual(self.f.task_store.grant(unrelated['grant_id']), unrelated)

    def test_selected_scope_changes_at_transaction_guard_roll_back_admission(self):
        spec = self.spec('scope-drift-transaction')
        current = self.f.ordinary_grants[self.project]
        alternate = self.history_grant('alternate-selected')
        self.selected(current)
        original = self.f.task_store.admit
        guard_calls = []
        def admit(payload, intent, *, authorize=None):
            def drift_then_check():
                guard_calls.append(True)
                self.selected(alternate)
                authorize()
            return original(payload, intent, authorize=drift_then_check)
        with mock.patch.object(self.f.task_store, 'admit', side_effect=admit):
            status, _, body = self.post(spec)
        self.assertEqual(status, 409, body)
        self.assertTrue(guard_calls)
        self.assertEqual(self.f.task_store.tasks(), [])
        self.assertEqual(self.f.task_store.grant(current['grant_id']), current)

    def test_scope_validation_preserves_standalone_default_and_rejects_explicit_invalid_scope(self):
        self.assertIsNone(self.f.tasks.ordinary_grant_ids)
        for value in ((), [], ['one'], 'one', ('duplicate','duplicate'), ('bad/id',), (False,),
                      tuple('grant-'+str(i) for i in range(MAX_TASKS+1))):
            with self.subTest(value=value):
                with self.assertRaises(ServiceError) as raised:
                    TaskService(self.f.projects, self.f.previews, self.f.task_store, ordinary_grant_ids=value)
                self.assertEqual(raised.exception.code, 'ORDINARY_GRANT_SCOPE_REJECTED')
        self.selected(self.f.ordinary_grants[self.project])
        before = self.f.tasks.ordinary_grant_ids
        with self.assertRaises(ServiceError):
            self.f.tasks.ordinary_grant_ids = ()
        self.assertEqual(self.f.tasks.ordinary_grant_ids, before)

    def test_actual_attachment_prepared_start_retired_uses_exact_scope_and_same_store(self):
        spec = self.spec('actual-selected-runtime')
        current = copy.deepcopy(self.f.ordinary_grants[self.project])
        old = self.history_grant('old-expired-runtime', expires_at=time.time()-3600)
        active = self.history_grant('unselected-active-runtime')
        before = self.f.task_store.grants()
        runtime = OrdinaryRuntime(self.f.task_store, grants=[current],
            provider_status={'available':True,'classification':'synthetic_fixture'}, poll_interval=.01,
            adapter_factory=lambda root:ControlledAdapter(self.f, root, ordinary=True))
        self.f.ordinary_runtime = runtime
        self.f.assembly.attach_ordinary_runtime(self.f.server, runtime)
        self.assertIs(runtime.store, self.f.task_store)
        self.assertIs(self.f.tasks.store, self.f.task_store)
        self.assertEqual(self.f.tasks.ordinary_grant_ids, (current['grant_id'],))
        self.assertFalse(self.context()['ready'])
        self.assertFalse(runtime.status()['worker_runtime']['available'])
        self.assertEqual(self.f.ordinary_adapters, [])
        self.assertEqual(self.f.task_store.grants(), before)
        runtime.start()
        self.assertTrue(self.context()['ready'])
        self.assertEqual(self.f.ordinary_adapters, [])  # GET did not launch a provider.
        status, _, body = self.post(spec)
        self.assertEqual(status, 200, body)
        deadline = time.monotonic()+4
        while time.monotonic()<deadline:
            if self.f.task_store.task(spec['request_id'])['status']=='checks_complete':
                break
            time.sleep(.01)
        task = self.f.task_store.task(spec['request_id'])
        self.assertEqual(task['status'], 'checks_complete')
        self.assertEqual(task['intent']['grant'], current)
        self.assertEqual(task['intent']['grant_hash'], digest(current))
        self.assertEqual(len(self.f.ordinary_adapters), 1)
        runtime.stop(timeout=3)
        self.assertFalse(self.context()['ready'])
        self.assertEqual(len(self.f.ordinary_adapters), 1)
        self.assertTrue(all(adapter.closed for adapter in self.f.ordinary_adapters))
        with self.assertRaises(ServiceError):
            runtime.start()
        self.assertEqual(self.f.task_store.grants(), before)
        self.assertEqual(self.f.task_store.grant(old['grant_id']), old)
        self.assertEqual(self.f.task_store.grant(active['grant_id']), active)
        self.assertEqual(self.f.effects, [])


if __name__=='__main__':unittest.main()
