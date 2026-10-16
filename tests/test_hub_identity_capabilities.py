"""Session-specific capability projection does not confer execution authority."""
import unittest

from pch_functional_fixture import FunctionalFixture


class IdentityCapabilitiesTests(unittest.TestCase):
    def test_guest_owner_and_runtime_revocation_are_distinct(self):
        with FunctionalFixture() as fixture:
            fixture.handshake(owner=False)
            guest = fixture.call(path='/api/identity')[2]['data']
            self.assertFalse(guest['execution_enabled'])
            self.assertTrue(guest['capabilities']['annotation_execution']['service_available'])
            self.assertEqual(guest['capabilities']['annotation_execution']['reason_code'], 'OWNER_REQUIRED')
            fixture.handshake()
            owner = fixture.call(path='/api/identity')[2]['data']
            self.assertTrue(owner['execution_enabled'])
            self.assertFalse(owner['capabilities']['ordinary_execution']['available'])
            self.assertEqual(owner['capabilities']['provider']['classification'], 'synthetic_fixture')
            fixture.tasks.runtime_status = lambda: {}
            revoked = fixture.call(path='/api/identity')[2]['data']
            self.assertTrue(revoked['owner_authenticated'])
            self.assertFalse(revoked['execution_enabled'])
            self.assertEqual(revoked['capabilities']['annotation_execution']['reason_code'], 'TASK_WORKER_UNCONFIGURED')

    def test_missing_task_service_is_not_empty_task_history(self):
        with FunctionalFixture() as fixture:
            fixture.handshake()
            fixture.server.tasks = None
            identity = fixture.call(path='/api/identity')[2]['data']
            self.assertFalse(identity['execution_enabled'])
            self.assertNotIn('task_history', identity['capabilities'])
            status, _, payload = fixture.call(path='/api/tasks')
            self.assertEqual(status, 503)
            self.assertEqual(payload['error']['code'], 'TASK_RUNTIME_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
