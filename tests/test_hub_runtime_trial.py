import copy
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.runtime_trial import RuntimeTrialAdapter
from hub.task_service import root_identity
from hub.workbench_store import digest
from hub.service_contract import ServiceError


class RuntimeTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.hub=Path(self.temp.name)/'hub';self.root=Path(self.temp.name)/'fixture'
        self.hub.mkdir();self.root.mkdir()
        self.grant={'root':str(self.root),'isolated_root':True,'checks':['fixture_sample']}
        self.registration={'purpose':'disposable_fixture','task_id':'PCH-REFACTOR-20261014','trial_id':'trial-one',
            'root':str(self.root),'root_identity':root_identity(self.root),'grant_sha256':digest(self.grant),
            'metadata_registration_sha256':'a'*64,'expires_at':time.time()+600}
        self.config=SimpleNamespace(semantic_sha256='a'*64,config_sha256='b'*64)
        mock=patch('hub.runtime_trial.load_registration',return_value=self.config);mock.start();self.addCleanup(mock.stop)
        self.save()

    def save(self):
        (self.hub/'STATE.yaml').write_text(json.dumps({'metadata':{'authority':'canonical'},
            'pch_refactor':{'r53_implementation':{'runtime_trial':self.registration}}}))

    def adapter(self):
        return RuntimeTrialAdapter(self.root,hub_root=self.hub,grant=self.grant,trial_id='trial-one')

    def test_explicit_registration_reuses_config_identity_without_relaxing_general_pin(self):
        from hub.codex_adapter import AppServerAdapter, REGISTERED_CONFIG_SHA256
        self.assertEqual(self.adapter()._configuration_hash(),'b'*64)
        self.assertEqual(AppServerAdapter(self.root)._configuration_hash(),REGISTERED_CONFIG_SHA256)

    def test_wrong_scope_expiry_grant_and_config_identity_are_rejected(self):
        original=copy.deepcopy(self.registration)
        for key,value in [('purpose','real_project'),('expires_at',time.time()-1),('grant_sha256','c'*64),
                          ('metadata_registration_sha256','c'*64),('root_identity','c'*64),('trial_id','other')]:
            with self.subTest(key=key):
                self.registration={**original,key:value};self.save()
                with self.assertRaises(ServiceError):self.adapter()

    def test_uncertain_turn_is_not_replayed_and_stop_requires_exact_binding(self):
        adapter=self.adapter()
        with patch('hub.runtime_trial.AppServerAdapter.thread',return_value='native-thread'):
            self.assertEqual(adapter.thread(),'native-thread')
        with self.assertRaises(ServiceError):adapter.thread(previous='native-thread')
        with patch('hub.runtime_trial.AppServerAdapter.start',side_effect=ServiceError('CODEX_STREAM_EOF',outcome='UNKNOWN'))as call:
            with self.assertRaises(ServiceError):adapter.start('native-thread','input',None)
            with self.assertRaises(ServiceError):adapter.start('native-thread','input',None)
            self.assertEqual(call.call_count,1)
        with self.assertRaises(ServiceError):adapter.interrupt('native-thread','dispatching')
        adapter.trial_turn='native-turn'
        with self.assertRaises(ServiceError):adapter.interrupt('someone-else','native-turn')
        with patch('hub.runtime_trial.AppServerAdapter.interrupt',return_value={})as call:
            self.assertEqual(adapter.interrupt('native-thread','native-turn'),{})
            call.assert_called_once_with('native-thread','native-turn')
