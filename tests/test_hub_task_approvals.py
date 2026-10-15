from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.task_approvals import approval_policy


class TaskApprovalPolicyTests(unittest.TestCase):
    def setUp(self):
        self.grant = {'root': '/owned/fixture', 'expires_at': 200,
                      'approved_commands': [{'command': 'python check.py', 'cwd': '/owned/fixture'}]}
        self.request = {'id': 17, 'method': 'item/commandExecution/requestApproval', 'params': {
            'threadId': 'thread', 'turnId': 'turn', 'itemId': 'item', 'startedAtMs': 100000,
            'command': 'python check.py', 'cwd': '/owned/fixture',
            'availableDecisions': ['accept', 'decline', 'cancel']}}
        self.kwargs = dict(thread_id='thread', turn_id='turn', item_id='item', request_id=17,
                           grant_root='/owned/fixture', now=100)

    def policy(self):
        return approval_policy(self.grant, self.request, **self.kwargs)

    def denied(self):
        self.assertEqual(self.policy()['decisions'], ['decline'])

    def test_exact_command_and_readonly_inputs(self):
        before = copy.deepcopy((self.grant, self.request, self.kwargs))
        result = self.policy()
        self.assertEqual(result['decisions'], ['decline', 'accept'])
        self.assertEqual(len(result['operation_sha256']), 64)
        self.assertEqual(result, self.policy())
        self.assertEqual(before, (self.grant, self.request, self.kwargs))

    def test_nonexact_command_cwd_and_shell_additions(self):
        for command in ('python check.py ', 'python check.py; touch x', 'python check.py && pwd',
                        '$(python check.py)', 'python\ncheck.py', None, ['python check.py']):
            with self.subTest(command=command):
                self.request['params']['command'] = command
                self.denied()
        self.request['params']['command'] = 'python check.py'
        for cwd in ('/other', '/owned/fixture/', '/owned/fixture/../fixture', None):
            self.request['params']['cwd'] = cwd
            self.denied()

    def test_extra_authority_even_empty_rejected(self):
        for key in ('additionalPermissions', 'networkApprovalContext', 'proposedExecpolicyAmendment',
                    'proposedNetworkPolicyAmendments', 'environmentId', 'futurePermission'):
            for value in ({}, [], False, 'remote'):
                with self.subTest(key=key, value=value):
                    params = copy.deepcopy(self.request['params'])
                    self.request['params'][key] = value
                    self.denied()
                    self.request['params'] = params

    def test_null_optional_fields_and_parsed_actions_do_not_grant_authority(self):
        self.request['params'].update(additionalPermissions=None, networkApprovalContext=None,
                                      proposedExecpolicyAmendment=None, proposedNetworkPolicyAmendments=None,
                                      environmentId=None, approvalId=None, reason=None,
                                      commandActions=[{'type': 'unknown', 'command': 'other display text'}])
        self.assertIn('accept', self.policy()['decisions'])
        self.request['params']['command'] = 'unregistered'
        self.request['params']['commandActions'] = [{'type': 'read', 'command': 'python check.py',
                                                    'name': 'safe', 'path': '/owned/fixture'}]
        self.denied()

    def test_ids_and_binding(self):
        original = self.policy()['operation_sha256']
        self.request['params']['approvalId'] = 'callback'
        self.assertNotEqual(original, self.policy()['operation_sha256'])
        for field in ('threadId', 'turnId', 'itemId'):
            old = self.request['params'][field]
            self.request['params'][field] = 'wrong'
            self.denied()
            self.request['params'][field] = old
        for ident in ('17', 18, True, None, {}):
            self.request['id'] = ident
            self.denied()

    def test_expiry_and_unknown_root(self):
        for expiry in (100, 99, float('inf'), float('nan'), True, None, '200', 10**1000):
            self.grant['expires_at'] = expiry
            self.denied()
        self.grant['expires_at'] = 200
        for root in (None, 'relative', '/other', '/owned/fixture/', '/owned/../fixture'):
            self.grant['root'] = root
            self.denied()

    def test_advertised_broad_options_do_not_expand_response(self):
        for decisions in (None, ['accept'], ['accept', 'decline', 'acceptForSession', 'cancel']):
            self.request['params']['availableDecisions'] = decisions
            self.assertEqual(self.policy()['decisions'], ['decline', 'accept'])
        del self.request['params']['availableDecisions']
        self.assertEqual(self.policy()['decisions'], ['decline', 'accept'])

    def test_unknown_decisions_denied(self):
        for decisions in ([], ['decline', 'cancel'],
                          ['accept', 'decline', {'acceptWithExecpolicyAmendment': {}}],
                          ['accept', 'decline', 'future'], ['accept', 'decline', 'accept'], True):
            self.request['params']['availableDecisions'] = decisions
            self.denied()

    def test_other_requests_stdin_and_unknown_shapes_denied(self):
        for method in ('item/fileChange/requestApproval', 'item/permissions/requestApproval',
                       'item/tool/requestUserInput', 'execCommandApproval', 'future'):
            self.request['method'] = method
            self.denied()
        self.request['method'] = 'item/commandExecution/requestApproval'
        self.request['params']['kind'] = 'writeStdin'
        self.denied()
        self.request['params']['kind'] = 'command'
        for key, value in (('startedAtMs', True), ('commandActions', [{}]), ('approvalId', [])):
            old = copy.deepcopy(self.request['params'])
            self.request['params'][key] = value
            self.denied()
            self.request['params'] = old
        self.request['extra'] = 'authority'
        self.denied()

    def test_registration_shape_strict(self):
        for commands in (None, [], [{'command': 'python check.py', 'cwd': '/other'}],
                         [{'command': 'python check.py', 'cwd': '/owned/fixture', 'network': True}],
                         [{'command': None, 'cwd': '/owned/fixture'}]):
            self.grant['approved_commands'] = commands
            self.denied()


if __name__ == '__main__':
    unittest.main()
