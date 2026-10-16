from __future__ import annotations

import copy
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.service_contract import ServiceError
from hub.task_service import REQUEST_FIELDS, TaskService, validate_grant


class TaskGrantCommandTests(unittest.TestCase):
    def setUp(self):
        self.grant = {'grant_id': 'fixture-grant', 'version': 1, 'source': 'verified_owner_decision',
                      'task_id': 'fixture-task', 'project_id': 'fixture-project', 'root': '/fixture',
                      'root_identity': 'a' * 64, 'fingerprint': [], 'mode': 'new', 'parent_task_id': None,
                      'annotation_hash': 'b' * 64, 'checks': ['fixture_sample'], 'expires_at': time.time() + 600,
                      'isolated_root': True, 'writable_scope': ['.'], 'prohibited': [], 'auth': 'ChatGPT',
                      'max_turn_seconds': 20, 'image': 'fixture.png', 'image_sha256': 'c' * 64}
        self.command = {'command': 'printf fixture', 'cwd': '/fixture'}

    def test_legacy_grant_unchanged_and_optional_exact_commands(self):
        self.assertEqual(validate_grant(self.grant), self.grant)
        original = copy.deepcopy(self.grant)
        self.grant['approved_commands'] = [self.command]
        validated = validate_grant(self.grant)
        self.assertEqual(validated, self.grant)
        validated['approved_commands'][0]['command'] = 'changed'
        self.assertEqual(self.grant['approved_commands'][0]['command'], 'printf fixture')
        self.assertEqual({k: v for k, v in self.grant.items() if k != 'approved_commands'}, original)

    def test_bad_command_shapes_duplicates_types_and_scope_rejected(self):
        cases = [None, {}, [], 'printf fixture', [self.command, self.command], [None], ['printf fixture'],
                 [{'command': 'printf fixture'}], [{'cwd': '/fixture'}],
                 [{**self.command, 'allow_session': True}]]
        cases += [[{'command': value, 'cwd': '/fixture'}] for value in ['', '  ', '\x00', True, [], 'x' * 4097]]
        cases += [[{'command': 'printf fixture', 'cwd': value}] for value in ['/other', '/fixture/..', '/fixture/', None, []]]
        cases += [[{'command': 'command-' + str(n), 'cwd': '/fixture'} for n in range(9)]]
        for commands in cases:
            with self.subTest(commands=commands):
                grant = {**self.grant, 'approved_commands': commands}
                with self.assertRaises(ServiceError):
                    validate_grant(grant)

    def test_css_cannot_add_optional_commands_or_expand_other_permissions(self):
        grant = {**self.grant, 'checks': ['csp_css_trial'], 'css_trial': {}, 'approved_commands': [self.command]}
        with self.assertRaises(ServiceError):
            validate_grant(grant)
        for patch in [{'checks': ['arbitrary']}, {'writable_scope': ['..']}, {'isolated_root': False}, {'auth': 'api_key'}]:
            with self.subTest(patch=patch), self.assertRaises(ServiceError):
                validate_grant({**self.grant, 'approved_commands': [self.command], **patch})

    def test_http_submission_still_cannot_supply_permissions(self):
        for key in ('approved_commands', 'writable_scope', 'root', 'authority', 'permissions'):
            self.assertNotIn(key, REQUEST_FIELDS)

    def test_owner_control_accept_delegated_to_store_policy_with_original_cas(self):
        store = mock.Mock()
        store.task.return_value = {'id': 'task-one', 'intent': {'grant': copy.deepcopy(self.grant)}}
        store.respond_control.return_value = False
        service = TaskService(None, None, store, test_gate=True)
        service.get = mock.Mock(return_value={'task': {'id': 'task-one'}})
        authorize = mock.Mock()
        for decision in ['accept', 'decline']:
            command = {'control_id': 'control-one', 'version': 1, 'decision': decision}
            response = service.respond_control('task-one', command, owner_context='owner-fixture', authorize=authorize)
            self.assertFalse(response['replayed'])
            store.respond_control.assert_called_with('task-one', 'control-one', 1, decision,
                                                      owner_context='owner-fixture', authorize=authorize)
        store.respond_control.side_effect = ServiceError('TASK_CONTROL_VERSION_STALE', status=409)
        with self.assertRaises(ServiceError):
            service.respond_control('task-one', {'control_id': 'control-one', 'version': 2, 'decision': 'accept'})

    def test_session_allow_and_malformed_controls_rejected_before_store(self):
        store = mock.Mock()
        service = TaskService(None, None, store, test_gate=True)
        for decision in ['acceptForSession', 'acceptWithExecpolicyAmendment', 'acceptWithNetworkPolicyAmendment', '', [], True]:
            with self.subTest(decision=decision), self.assertRaises(ServiceError):
                service.respond_control('task-one', {'control_id': 'control-one', 'version': 1, 'decision': decision})
        with self.assertRaises(ServiceError):
            service.respond_control('task-one', {'control_id': 'control-one', 'version': 1, 'decision': 'accept', 'permissions': {}})
        store.task.assert_not_called()
        store.respond_control.assert_not_called()


if __name__ == '__main__':
    unittest.main()
