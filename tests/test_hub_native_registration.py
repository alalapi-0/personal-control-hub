import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from hub import codex_adapter as adapter
from hub.native_config_registration import load_registration, NativeMetadataRegistration
from hub.service_contract import ServiceError


class NativeRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / 'native-fixture'
        self.binary.write_bytes(b'fixture-binary')
        self.registration = {'schema_version': '1.0', 'task_id': 'PCH-REFACTOR-20261014',
            'purpose': 'metadata_discovery', 'root': str(self.root), 'platform': 'linux',
            'config_sha256': 'a' * 64, 'cli_version': adapter.VERSION,
            'binary_realpath': str(self.binary),
            'binary_sha256': hashlib.sha256(self.binary.read_bytes()).hexdigest(),
            'mcp_servers': sorted(adapter.REGISTERED_SERVERS)}
        self.write()
        self.addCleanup(patch.stopall)
        patch('hub.native_config_registration.shutil.which', return_value=str(self.binary)).start()
        patch.object(adapter, 'config_fingerprint', return_value='a' * 64).start()

    def write(self, value=None):
        value = self.registration if value is None else value
        (self.root / 'STATE.yaml').write_text(json.dumps({'metadata': {'authority': 'canonical'}, 'pch_refactor': {
            'task_id': 'PCH-REFACTOR-20261014',
            'r51_implementation': {'native_metadata_registration': value}}}))

    def test_explicit_registration_and_semantic_identity(self):
        value = load_registration(self.root)
        self.assertEqual(value.config_sha256, 'a' * 64)
        self.assertEqual(value.root, self.root)
        self.assertEqual(len(value.semantic_sha256), 64)
        self.assertEqual(load_registration(self.root), value)

    def test_scope_shape_and_binary_drift_reject_before_launch(self):
        for mutation in ({'purpose': 'execution'}, {'root': '/wrong'}, {'platform': 'darwin'},
                         {'task_id': 'OTHER'}, {'mcp_servers': ['unknown']},
                         {'binary_sha256': 'b' * 64}, {'extra': True}):
            with self.subTest(mutation=mutation):
                self.write({**self.registration, **mutation})
                with patch.object(adapter.subprocess, 'Popen') as spawn:
                    with self.assertRaises(ServiceError):
                        load_registration(self.root)
                    spawn.assert_not_called()

    def test_config_drift_does_not_auto_enroll(self):
        before = (self.root / 'STATE.yaml').read_bytes()
        with patch.object(adapter, 'config_fingerprint', return_value='b' * 64):
            with self.assertRaises(ServiceError) as error:
                load_registration(self.root)
        self.assertEqual(error.exception.code, 'CODEX_CONFIG_DRIFT')
        self.assertEqual((self.root / 'STATE.yaml').read_bytes(), before)

    def test_state_alias_and_hardlink_rejected(self):
        state = self.root / 'STATE.yaml'
        saved = self.root / 'saved.yaml';state.rename(saved);state.symlink_to(saved)
        with self.assertRaises(ServiceError):load_registration(self.root)
        state.unlink();state.hardlink_to(saved)
        with self.assertRaises(ServiceError):load_registration(self.root)


class MetadataControlsTests(unittest.TestCase):
    def make(self):
        registration = NativeMetadataRegistration(Path('/hub'), 'a'*64, adapter.VERSION,
            '/native', 'b'*64, tuple(adapter.REGISTERED_SERVERS), 'c'*64)
        result = adapter.ReadonlyAppServerAdapter('/fixture', registration=registration, schemas={})
        result.remote_control_status = 'disabled'
        effective = {'default_permissions': result.permission_profile,
            'permissions': {result.permission_profile: {'filesystem': {
                ':root': 'deny', ':minimal': 'read', ':tmpdir': 'deny', ':slash_tmp': 'deny', '/fixture': 'read'},
                'network': {'enabled': False}}},
            'mcp_servers': {name: {'enabled': False} for name in adapter.REGISTERED_SERVERS},
            'web_search': 'disabled', 'unrelated_private_value': 'PRIVATE-DO-NOT-RETAIN'}
        responses = {'config/read': {'config': effective},
            'mcpServerStatus/list': {'data': [{'name': name, 'runtimeStatus': None,
                'tools': {}, 'resources': [], 'resourceTemplates': [], 'serverCapabilities': None}
                for name in adapter.REGISTERED_SERVERS], 'nextCursor': None},
            'experimentalFeature/list': {'data': [{'name': name, 'enabled': False} for name in adapter.DISABLED_FEATURES], 'nextCursor': None}}
        return result, responses

    def test_readonly_constructor_and_rpc_boundary(self):
        value = adapter.ReadonlyAppServerAdapter('/fixture', schemas={})
        self.assertTrue(value.read_only)
        self.assertEqual(value.permission_profile, adapter.EVIDENCE_PERMISSION_PROFILE)
        with self.assertRaises(ServiceError):adapter.ReadonlyAppServerAdapter('/fixture', read_only=False)
        for method in ('turn/start', 'thread/start', 'thread/resume', 'turn/interrupt', 'command/exec', 'config/read'):
            with self.subTest(method=method), self.assertRaises(ServiceError):value.call(method, {})
        with self.assertRaises(ServiceError):value._diagnostic('turn/start', {})

    def test_actual_controls_projection_contains_no_config_or_secret(self):
        value, responses = self.make()
        responses['config/read']['config']['permissions'][value.permission_profile]['filesystem']['glob_scan_max_depth'] = None
        with patch.object(value, '_diagnostic', side_effect=lambda method, params: responses[method]):
            value._verify_metadata_controls()
        self.assertEqual(value.metadata_controls['filesystem'], 'read-only')
        self.assertEqual(value.metadata_controls['model_turns'], 0)
        self.assertNotIn('PRIVATE', str(value.metadata_controls))
        self.assertEqual(value.metadata_controls['unavailable_mcp_runtime_rows'], 6)
        self.assertFalse(value.metadata_controls['loaded_thread_runtime_probed'])

    def test_registered_metadata_cannot_select_other_roots_or_history(self):
        value, _ = self.make()
        for method, params in [('thread/list', {'cwd': '/other'}),
                               ('thread/read', {'threadId': 'unseen', 'includeTurns': False}),
                               ('thread/read', {'threadId': 'seen', 'includeTurns': True}),
                               ('thread/read', {'threadId': [], 'includeTurns': False})]:
            with self.subTest(method=method, params=params), patch.object(adapter, 'config_fingerprint', return_value='a'*64):
                with self.assertRaises(ServiceError) as error:
                    value.call(method, params)
                self.assertEqual(error.exception.code, 'DISCOVERY_SCOPE_REJECTED')

    def test_effective_write_network_unknown_mcp_remote_and_feature_rejected(self):
        for failure in ('write', 'network', 'mcp', 'remote', 'feature', 'pagination', 'scan_option'):
            with self.subTest(failure=failure):
                value, responses = self.make();effective = responses['config/read']['config']
                if failure == 'write':effective['permissions'][value.permission_profile]['filesystem']['/fixture'] = 'write'
                if failure == 'network':effective['permissions'][value.permission_profile]['network']['enabled'] = True
                if failure == 'mcp':effective['mcp_servers']['unknown'] = {'enabled': False}
                if failure == 'remote':value.remote_control_status = 'running'
                if failure == 'feature':responses['experimentalFeature/list']['data'][0]['enabled'] = True
                if failure == 'pagination':responses['mcpServerStatus/list']['nextCursor'] = 'more'
                if failure == 'scan_option':effective['permissions'][value.permission_profile]['filesystem']['glob_scan_max_depth'] = 2
                with patch.object(value, '_diagnostic', side_effect=lambda method, params: responses[method]):
                    with self.assertRaises(ServiceError):value._verify_metadata_controls()


if __name__ == '__main__':
    unittest.main()
