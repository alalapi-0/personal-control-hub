"""Trusted backend enrollment remains separate from browser task submission."""
import copy
import re
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.service_contract import ServiceError
from hub.task_service import TaskService
from hub.workbench_store import digest


class RegisteredBusinessTests(unittest.TestCase):
    def test_browser_module_imports_have_http_asset_routes(self):
        from hub.local_service import WEB_ASSETS, WEB_ROOT
        registered={name for name,mime in WEB_ASSETS.values()if 'javascript'in mime}
        for name in registered:
            text=(WEB_ROOT/name).read_text()
            for relative in re.findall(r"(?:from\s*|import\s*)['\"]\./([^'\"]+)['\"]",text):
                with self.subTest(module=name,dependency=relative):self.assertIn(relative,registered)

    def setUp(self):
        self.payload = {'kind':'story_exchange','request_id':'task-story','project_id':'story-project',
                        'grant_id':'story-grant','grant_version':1,'mode':'new','parent_task_id':None,
                        'specification_sha256':'a'*64}
        self.grant = {'kind':'story_exchange','task_id':'task-story','project_id':'story-project','version':1,
                      'root':'/isolated-stage','mode':'new','parent_task_id':None,
                      'task_spec': {'requirement':'Three candidates','scope':'C01','acceptance':'Validate proposal results'},
                      'checks':['story_exchange'],'story_exchange': {'canonical_root':'/real-project',
                      'registry_hash':'b'*64,'specification_sha256':'a'*64,'registration_sha256':'c'*64,
                      'source_preconditions':{'workspace_revision':22}}}
        self.resolver = SimpleNamespace(projects={'story-project':{'root_path':'/real-project','enabled':True}},
                                        authority={'registry_hash':'b'*64}, _check_registry=Mock())
        self.projects = SimpleNamespace(_resolver=lambda:self.resolver)
        self.store = Mock(root=Path('/hub'))
        self.store.task.return_value = None
        self.store.grant.side_effect = lambda _:copy.deepcopy(self.grant)
        self.store.admit.return_value = ({'id':'task-story'}, False)
        self.annotations = Mock()
        self.writer = Mock(return_value={'scope':'exact-source-files'})
        self.service = TaskService(self.projects,self.annotations,self.store,writer_check=self.writer,test_gate=True)
        self.service.public = lambda task: {'id':task['id']}
        patches = [('hub.task_service.validate_grant', {'side_effect':lambda value, **_:copy.deepcopy(value)}),
                   ('hub.task_service.PreviewService._eligible', {'return_value':True}),
                   ('hub.native_business.NativeBusinessAdapter', {}),
                   ('hub.story_exchange.prompt', {'return_value':'Actual registered writing instruction'}),
                   ('hub.story_promotion.source_snapshot', {})]
        self.mocks = {}
        for name, kwargs in patches:
            context = patch(name, **kwargs); self.mocks[name]=context.start();self.addCleanup(context.stop)

    def submit(self, **kwargs):
        return self.service.submit_registered_business(self.payload, registration_sha256='c'*64, **kwargs)

    def test_backend_admits_only_frozen_source_identity(self):
        authorize=Mock()
        result=self.submit(authorize=authorize)
        self.assertFalse(result['replayed'])
        args, kwargs=self.store.admit.call_args
        self.assertEqual(args[0],self.payload)
        intent=args[1]
        self.assertEqual(intent['grant_hash'],digest(self.grant))
        self.assertEqual(intent['source_binding'],{'workspace_revision':22})
        self.assertEqual(intent['thread_source'],'hub_owned_app_server')
        self.assertIsNone(intent['previous_thread'])
        self.assertNotIn('annotation',intent)
        self.assertGreaterEqual(authorize.call_count,2)
        self.mocks['hub.native_business.NativeBusinessAdapter'].return_value.open.assert_not_called()

    def test_grant_payload_and_registry_mismatches_never_admit(self):
        cases=[('task_id','other-task'),('project_id','other-project'),('version',2)]
        for key,value in cases:
            original=self.grant[key];self.grant[key]=value
            with self.subTest(key=key),self.assertRaises(ServiceError):self.submit()
            self.grant[key]=original
        for key in ('canonical_root','registry_hash','specification_sha256','registration_sha256'):
            original=self.grant['story_exchange'][key];self.grant['story_exchange'][key]='mismatch'
            with self.subTest(key=key),self.assertRaises(ServiceError):self.submit()
            self.grant['story_exchange'][key]=original
        self.store.admit.assert_not_called()

    def test_registration_source_and_writer_rejections_remain_closed(self):
        for target in ('hub.native_business.NativeBusinessAdapter','hub.story_promotion.source_snapshot'):
            self.mocks[target].side_effect=ServiceError('TEST_REJECTED')
            with self.subTest(target=target),self.assertRaises(ServiceError):self.submit()
            self.mocks[target].side_effect=None
        self.writer.return_value=False
        with self.assertRaises(ServiceError):self.submit()
        self.store.admit.assert_not_called()

    def test_browser_payload_cannot_enter_backend_path(self):
        with self.assertRaises(ServiceError):self.service.submit(self.payload)
        legacy={'request_id':'task-story','project_id':'story-project','annotation_id':'annotation',
                'annotation_revision':1,'grant_id':'story-grant','grant_version':1,'mode':'new','parent_task_id':None}
        with self.assertRaises(ServiceError):self.service.submit(legacy)
        self.annotations.receipt.assert_not_called()
        self.store.admit.assert_not_called()

    def test_wrong_shape_resume_and_authorization_are_rejected(self):
        for patch_value in ({'root':'/untrusted'}, {'mode':'idle_continue'}, {'grant_version':True}, {'parent_task_id':'another'}):
            original=self.payload;self.payload={**original,**patch_value}
            with self.subTest(patch=patch_value),self.assertRaises(ServiceError):self.submit()
            self.payload=original
        def denied():raise ServiceError('AUTH_DENIED')
        with self.assertRaises(ServiceError):self.submit(authorize=denied)
        self.store.admit.assert_not_called()

    def test_exact_replay_does_not_dispatch_or_duplicate(self):
        self.store.task.return_value={'id':'task-story','payload':copy.deepcopy(self.payload)}
        self.assertTrue(self.submit()['replayed'])
        self.store.admit.assert_not_called()
        self.mocks['hub.native_business.NativeBusinessAdapter'].assert_not_called()
        self.payload['specification_sha256']='9'*64
        with self.assertRaises(ServiceError):self.submit()


if __name__=='__main__':unittest.main()
