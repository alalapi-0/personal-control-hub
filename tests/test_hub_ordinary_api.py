"""Real loopback ordinary requests cannot mint filesystem or native authority."""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pch_functional_fixture import FunctionalFixture,BEFORE
from hub.ordinary_tasks import validate_spec
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
        for key,value in [('cwd','/tmp/other'),('grant_id','invented'),('thread_id','unowned'),('profile','write')]:
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


if __name__=='__main__':unittest.main()
