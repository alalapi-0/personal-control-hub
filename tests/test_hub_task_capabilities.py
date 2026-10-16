"""Readiness is observation, never task authority or a provider probe."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.task_service import TaskService
from hub.service_contract import ServiceError


class TaskCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.store=mock.Mock()
        self.store.storage_status.return_value={'state':'ready','reason_code':None}
        self.store.tasks.return_value=[]
        self.runtime={'worker_runtime':{'available':True},'provider':{'available':True}}
        self.annotations=mock.Mock()
        self.annotations.annotations.return_value={'records':[],'revision':0,'classification':'fixture'}
        self.service=TaskService(None,self.annotations,self.store,test_gate=True,
            writer_check=lambda _:True,runtime_status=lambda:self.runtime)

    def test_readonly_projection_and_shared_listing(self):
        caps=self.service.capabilities()
        self.assertTrue(caps['annotation_execution']['available'])
        self.assertTrue(caps['task_drafts']['available'])
        self.assertTrue(caps['task_history']['available'])
        self.assertFalse(caps['ordinary_execution']['available'])
        for item in caps.values():
            self.assertEqual(set(item),{'available','status','reason_code','classification'})
        self.assertTrue(self.service.list()['execution_enabled'])
        self.assertTrue(self.service.drafts()['execution_enabled'])
        self.store.grants.assert_not_called()
        self.store.grant.assert_not_called()
        self.store.admit.assert_not_called()
        self.assertEqual(set(name for name,_,_ in self.store.mock_calls),{'storage_status','tasks'})

    def test_gate_and_writer_are_separate_prerequisites(self):
        self.service.test_gate=False
        self.assertEqual(self.service.capabilities()['annotation_execution']['reason_code'],'EXECUTION_DISABLED')
        other=TaskService(None,self.annotations,self.store,test_gate=True,runtime_status=lambda:self.runtime)
        self.assertEqual(other.capabilities()['annotation_execution']['reason_code'],'TASK_WRITER_CHECK_UNCONFIGURED')

    def test_unconfigured_worker_provider_and_callback_failure(self):
        self.service.runtime_status=None
        self.assertEqual(self.service.capabilities()['annotation_execution']['reason_code'],'TASK_WORKER_UNCONFIGURED')
        for reported in ({'worker_runtime':{'available':True}},
                         {'worker_runtime':{'available':True},'provider':{'available':'yes'}}):
            self.service.runtime_status=lambda:reported
            caps=self.service.capabilities()
            self.assertEqual(caps['annotation_execution']['reason_code'],'TASK_PROVIDER_UNAVAILABLE')
        self.service.runtime_status=mock.Mock(side_effect=RuntimeError('private diagnostic'))
        self.assertFalse(self.service.capabilities()['annotation_execution']['available'])
        self.service.runtime_status=lambda:[]
        self.assertFalse(self.service.capabilities()['worker_runtime']['available'])

    def test_storage_corruption_and_low_space_preserve_draft_readiness(self):
        for reason in ('TASK_STORE_CORRUPT','TASK_SCHEMA_UNSUPPORTED','REGISTRATION_MISSING'):
            self.store.storage_status.return_value={'state':'blocked','reason_code':reason}
            caps=self.service.capabilities()
            self.assertFalse(caps['task_history']['available'])
            self.assertFalse(caps['annotation_execution']['available'])
            self.assertTrue(caps['task_drafts']['available'])
            self.assertFalse(self.service.list()['tasks_available'])
        self.store.storage_status.return_value={'state':'blocked','reason_code':'LOW_SPACE'}
        caps=self.service.capabilities()
        self.assertTrue(caps['task_history']['available'])
        self.assertFalse(caps['annotation_execution']['available'])

    def test_corruption_discovered_reading_tasks_updates_projection(self):
        self.store.tasks.side_effect=ServiceError('TASK_STORE_CORRUPT',status=503)
        result=self.service.list()
        self.assertFalse(result['execution_enabled'])
        self.assertFalse(result['capabilities']['task_history']['available'])
        self.assertEqual(result['capabilities']['annotation_execution']['reason_code'],'TASK_STORE_CORRUPT')

    def test_runtime_revocation_is_observed_on_each_read(self):
        self.assertTrue(self.service.list()['execution_enabled'])
        self.runtime['provider']['available']=False
        result=self.service.list()
        self.assertFalse(result['execution_enabled'])
        self.assertEqual(result['capabilities']['annotation_execution']['reason_code'],'TASK_PROVIDER_UNAVAILABLE')
        self.runtime['provider']['available']=True
        self.runtime['worker_runtime']['available']=False
        self.assertFalse(self.service.drafts()['execution_enabled'])

    def test_runtime_classification_is_preserved_and_validated(self):
        self.runtime['provider']['classification']='synthetic_fixture'
        self.runtime['worker_runtime']['classification']='real'
        caps=self.service.capabilities()
        self.assertEqual(caps['provider']['classification'],'synthetic_fixture')
        self.assertEqual(caps['annotation_execution']['classification'],'synthetic_fixture')
        self.runtime['provider']['classification']='untrusted-private-string'
        caps=self.service.capabilities()
        self.assertEqual(caps['provider']['classification'],'unknown')
        self.assertEqual(caps['annotation_execution']['classification'],'unknown')


if __name__=='__main__':unittest.main()
