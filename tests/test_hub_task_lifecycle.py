from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.task_lifecycle import lifecycle


def task(**changes):
    return {'id': 'task-1', 'status': 'running', 'thread_id': None,
            'turn_id': None, 'provider_status': None, 'cancel': 0, **changes}


def event(sequence, key, **data):
    return {'sequence': sequence, 'key': key, 'received': 123, 'data': data}


def started(sequence=3, **changes):
    return event(sequence, 'event-' + 'a' * 64, method='turn/started',
                 digest='a' * 64, **{'thread_id': 'thread-1', 'turn_id': 'turn-1',
                                    'turn_status': 'inProgress', **changes})


class TaskLifecycleTests(unittest.TestCase):
    def test_queued_means_hub_received_only_including_requeued_task(self):
        value = lifecycle(task(status='queued', thread_id='thread-1', turn_id='turn-1',
                               provider_status='completed'), [started()])
        self.assertEqual((value['stage'], value['reason']), ('queued', 'hub_received'))
        self.assertFalse(value['provider_accepted'])
        self.assertFalse(value['provider_started'])

    def test_claim_before_send_is_preparing_not_provider_accepted(self):
        value = lifecycle(task(), [])
        self.assertEqual(value['stage'], 'preparing')
        self.assertFalse(value['provider_accepted'])

    def test_dispatch_intent_and_turn_intent_are_not_acceptance(self):
        rows = [event(1, 'dispatch-intent-worker', worker={'secret': 'hidden'})]
        self.assertEqual(lifecycle(task(), rows)['stage'], 'dispatching')
        rows.append(event(2, 'turn-intent-worker', thread_id='thread-1'))
        value = lifecycle(task(thread_id='thread-1'), rows)
        self.assertEqual(value['reason'], 'turn_prepared_acceptance_unconfirmed')
        self.assertFalse(value['provider_accepted'])

    def test_bound_response_is_accepted_before_started(self):
        value = lifecycle(task(thread_id='thread-1', turn_id='turn-1',
                               provider_status='inProgress'), [])
        self.assertEqual(value['stage'], 'accepted')
        self.assertTrue(value['provider_accepted'])
        self.assertFalse(value['provider_started'])

    def test_validated_started_must_match_exact_current_binding(self):
        current = task(thread_id='thread-1', turn_id='turn-1', provider_status='inProgress')
        self.assertEqual(lifecycle(current, [started()])['stage'], 'running')
        for changes in ({'thread_id': 'other'}, {'turn_id': 'old-turn'},
                        {'turn_status': None}, {'turn_status': 'completed'}):
            with self.subTest(changes=changes):
                self.assertEqual(lifecycle(current, [started(**changes)])['stage'], 'accepted')
        legacy = started()
        del legacy['data']['turn_status']
        self.assertEqual(lifecycle(current, [legacy])['stage'], 'accepted')

    def test_unknown_binding_and_foreign_or_ambiguous_events_do_not_start(self):
        current = task(thread_id='thread-1', turn_id='turn-1', provider_status='inProgress')
        foreign = {**started(), 'task_id': 'other-task'}
        for rows in ([foreign], [started(), started()],
                     [{**started(), 'key': 'untrusted'}]):
            self.assertFalse(lifecycle(current, rows)['provider_started'])
        self.assertFalse(lifecycle(task(), [started()])['provider_started'])

    def test_checks_complete_is_not_owner_acceptance(self):
        value = lifecycle(task(status='checks_complete'), [])
        self.assertEqual(value['stage'], 'await_owner_review')
        self.assertTrue(value['owner_review_required'])
        self.assertFalse(value['human_accepted'])

    def test_durable_waiting_validation_and_failure_states_take_precedence(self):
        for status in ('waiting_approval', 'waiting_input', 'validating', 'failed',
                       'cancelled', 'lost', 'requires_reconcile'):
            with self.subTest(status=status):
                current = task(status=status, thread_id='thread-1', turn_id='turn-1',
                               provider_status='inProgress')
                self.assertEqual(lifecycle(current, [started()])['stage'], status)

    def test_projection_is_restart_deterministic_and_does_not_leak_or_mutate(self):
        current = task(payload={'prompt': 'secret prompt'}, owner='private-worker',
                       result={'token': 'private-token', 'path': '/private/path'}, cancel=1)
        rows = [event(1, 'dispatch-intent-worker', worker={'pid': 987},
                      prompt='secret prompt', path='/private/path')]
        original = copy.deepcopy((current, rows))
        value = lifecycle(current, rows)
        self.assertEqual(value, lifecycle(json.loads(json.dumps(current)),
                                          json.loads(json.dumps(rows))))
        self.assertEqual((current, rows), original)
        self.assertTrue(value['cancel_requested'])
        encoded = json.dumps(value)
        for private in ('private-worker', 'secret prompt', '/private/path', 'private-token', '987'):
            self.assertNotIn(private, encoded)


if __name__ == '__main__':
    unittest.main()
