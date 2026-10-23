"""Actual closed handler preflight and disposable ordinary observations; no native calls."""
import copy
import unittest
from unittest import mock
import test_hub_graph_actions as action_fixture
import test_hub_ordinary_worker as ordinary_fixture
from hub.action_registry import descriptor, validate_descriptor, validate_port_compatibility, request_id, digest
from hub.task_service import TaskService
from hub.ordinary_tasks import validate_spec
from hub.service_contract import ServiceError


class GraphPortsTests(unittest.TestCase):
    def setUp(self):
        self.f=action_fixture.GraphActionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.actions=self.f.actions;self.binding=self.f.binding()

    def test_real_catalog_input_output_types_and_receipt_use_exact_registered_versions(self):
        catalog=self.actions.catalog('fixture-project')
        actions=next(e['actions']for e in catalog['entries']if e['actions'])
        for action in actions:
            self.assertEqual(validate_descriptor(action),action)
            self.assertFalse(action['tool']['native_execution'])
            for direction in ('inputs','outputs'):
                for port in action['ports'][direction]:
                    self.assertEqual(port['direction'],direction[:-1])
                    self.assertEqual(port['version']['binding_sha256'],action['binding']['binding_sha256'])
                    self.assertEqual(port['version']['artifact_sha256'],action['binding']['artifact_sha256'])
                    self.assertEqual(port['version']['object_revision'],action['binding']['object_revision'])
        read=next(a for a in actions if a['id']=='open_registered_artifact')
        self.assertEqual(read['tool']['input_types'],['registered_artifact']);self.assertEqual(read['tool']['output_types'],['bytes'])
        result=self.actions.read_with_receipt(self.binding,authorize=lambda:None)
        self.assertEqual(result.response.content_type,'image/png')
        self.assertEqual(result.receipt['tool'],read['tool']);self.assertEqual(result.receipt['ports'],read['ports'])
        self.assertEqual(result.receipt['frozen_input'],self.binding)
        self.assertEqual(result.receipt['declaration_sha256'],digest(read))
        self.assertFalse(result.receipt['native_execution']);self.assertFalse(result.receipt['durable_receipt'])

    def test_bad_modality_tool_direction_type_or_version_rejected_before_any_content_read(self):
        mutations=[
            lambda a:a['ports']['inputs'][0].update(type='image_understanding'),
            lambda a:a['ports']['inputs'][0].update(type='model_reference'),
            lambda a:a['tool'].update(handler='shell'),
            lambda a:a['tool'].update(capabilities=['arbitrary_shell']),
            lambda a:a['tool'].update(native_execution=True),
            lambda a:a['ports']['inputs'][0].update(direction='output'),
            lambda a:a['ports']['outputs'][0].update(type='unknown_modality'),
            lambda a:a['ports']['inputs'][0]['version'].update(object_revision='foreign'),
            lambda a:a['ports']['inputs'][0]['version'].update(artifact_sha256='0'*64),
            lambda a:a['ports']['outputs'][0].update(extra_permission=True),
        ]
        for ident in ('open_registered_artifact','verify_registered_artifact'):
            for mutate in mutations:
                with self.subTest(action=ident,mutation=mutations.index(mutate)):
                    catalog=self.actions.catalog('fixture-project');action=next(a for e in catalog['entries']for a in e['actions']if a['id']==ident)
                    mutate(action)
                    with mock.patch.object(self.actions,'catalog',return_value=catalog),mock.patch.object(self.f.f.service,'artifact',side_effect=AssertionError('must not read'))as content:
                        with self.assertRaises(ServiceError)as error:
                            if ident=='open_registered_artifact':self.actions.read(self.binding,authorize=lambda:None)
                            else:self.actions.verify(dict(action_id=ident,binding=self.binding,request_id=request_id(ident,self.binding)),authorize=lambda:None)
                        self.assertEqual(error.exception.code,'ACTION_PORT_REJECTED');content.assert_not_called()

    def test_capability_withdrawal_at_response_is_rejected_and_never_returned_as_a_run(self):
        calls=[]
        original=self.actions.catalog
        def catalog(project):
            value=original(project);calls.append(1)
            if len(calls)>1:
                for entry in value['entries']:
                    for action in entry['actions']:
                        if action['id']=='open_registered_artifact':action['tool']['available']=False
            return value
        with mock.patch.object(self.actions,'catalog',side_effect=catalog),mock.patch.object(self.f.f.service,'artifact',wraps=self.f.f.service.artifact)as read:
            with self.assertRaises(ServiceError)as error:self.actions.read_with_receipt(self.binding,authorize=lambda:None)
            self.assertEqual(error.exception.code,'ACTION_PORT_REJECTED');self.assertEqual(read.call_count,1)

    def test_native_unknown_tools_layout_and_candidate_run_cannot_authorize_a_read(self):
        native=descriptor('native_program',self.binding)
        self.assertFalse(native['tool']['available']);self.assertEqual(native['ports']['inputs'],[])
        with self.assertRaises(ServiceError)as error:validate_port_compatibility(native,self.binding)
        self.assertEqual(error.exception.code,'ACTION_HANDLER_UNAVAILABLE')
        catalog=self.actions.catalog('fixture-project')
        for entry in catalog['entries']:
            entry['object']['data'].update(layout={'x':1,'y':2},related_to='same-label',run={'identity':'candidate','id':'not-a-native-run'})
            entry['actions']=[]
        before=self.f.f.store.path.read_bytes()
        with mock.patch.object(self.actions,'catalog',return_value=catalog),mock.patch.object(self.f.f.service,'artifact',side_effect=AssertionError('no content'))as content:
            with self.assertRaises(ServiceError)as error:self.actions.read(self.binding,authorize=lambda:None)
            self.assertEqual(error.exception.code,'ACTION_BINDING_STALE');content.assert_not_called()
        self.assertEqual(self.f.f.store.path.read_bytes(),before)

    def test_repeated_real_reads_have_distinct_local_ids_and_frozen_versions_parameters_and_tools(self):
        ident='verify_registered_artifact';command=dict(action_id=ident,binding=copy.deepcopy(self.binding),request_id=request_id(ident,self.binding))
        before=self.f.f.store.path.read_bytes();first=self.actions.verify(command,authorize=lambda:None);second=self.actions.verify(command,authorize=lambda:None)
        self.assertNotEqual(first['run_id'],second['run_id']);self.assertTrue(first['run_id'].startswith('local-read-'))
        self.assertEqual(first['parameter_sha256'],digest(command));self.assertEqual(first['frozen_input'],self.binding)
        self.assertEqual(first['tool']['capabilities'],['registered_artifact_bounded_read','sha256_verification'])
        self.assertEqual(first['declaration_sha256'],digest(descriptor(ident,self.binding)))
        command['binding']['object_revision']='changed';self.assertEqual(first['frozen_input'],self.binding)
        self.assertEqual(self.f.f.store.path.read_bytes(),before)

    def test_changed_request_fields_or_exact_version_cannot_replay_as_supported_ports(self):
        ident='verify_registered_artifact';base=dict(action_id=ident,binding=self.binding,request_id=request_id(ident,self.binding))
        for extra in ('run_id','thread_id','permissions','ports','tool'):
            with self.subTest(extra=extra),mock.patch.object(self.f.f.service,'artifact',side_effect=AssertionError('no content')):
                with self.assertRaises(ServiceError)as error:self.actions.verify({**base,extra:'forged'},authorize=lambda:None)
                self.assertEqual(error.exception.code,'ACTION_COMMAND_INVALID')
        for key,value in (('object_revision','other'),('artifact_sha256','0'*64)):
            bad=copy.deepcopy(self.binding);bad[key]=value;bad['binding_sha256']=digest({k:v for k,v in bad.items()if k!='binding_sha256'})
            with self.assertRaises(ServiceError)as error:self.actions.verify({**base,'binding':bad},authorize=lambda:None)
            self.assertEqual(error.exception.code,'ACTION_REQUEST_CONFLICT')
            with self.assertRaises(ServiceError)as error:self.actions.verify({**base,'binding':bad,'request_id':request_id(ident,bad)},authorize=lambda:None)
            self.assertEqual(error.exception.code,'ACTION_BINDING_STALE')


class TaskRunObservationTests(unittest.TestCase):
    def setUp(self):
        self.f=ordinary_fixture.OrdinaryWorkerTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.service=TaskService(None,None,self.f.store)

    def test_public_queued_and_observed_native_identity_freeze_spec_without_fabricated_run(self):
        self.f.store.admit(self.f.spec,self.f.intent);queued=self.service.public(self.f.store.task(self.f.spec['request_id']))
        unknown=queued['run_observation'];self.assertEqual(unknown['identity_state'],'unknown');self.assertIsNone(unknown['run_id'])
        self.assertIsNone(unknown['thread_id']);self.assertIsNone(unknown['turn_id'])
        task=self.f.run_worker();before=copy.deepcopy(task);public=self.service.public(task);observed=public['run_observation']
        self.assertEqual((observed['thread_id'],observed['turn_id']),('ordinary-thread','ordinary-turn'))
        self.assertEqual(observed['identity_state'],'observed_thread_turn');self.assertIsNone(observed['run_id']);self.assertNotEqual(observed['run_id'],task['id'])
        self.assertEqual(observed['input_version'],self.f.spec['input_version']);self.assertEqual(observed['parameter_sha256'],digest(self.f.spec))
        self.assertFalse(observed['native_execution_proven']);self.assertIsNone(observed['tool_capability']);self.assertIsNone(observed['session_id'])
        self.assertEqual(task,before);self.assertEqual(set(public['task_spec']),set(self.f.spec));self.assertFalse(self.service.ordinary_enabled)

    def test_ordinary_spec_cannot_self_assign_native_identity_tool_or_permission(self):
        for extra in ('run_id','thread_id','ports','tool','permissions'):
            with self.subTest(extra=extra):
                with self.assertRaises(ServiceError)as error:validate_spec({**self.f.spec,extra:'forged'})
                self.assertEqual(error.exception.code,'ORDINARY_SPEC_INVALID')
        self.assertEqual(self.f.store.tasks(),[])
