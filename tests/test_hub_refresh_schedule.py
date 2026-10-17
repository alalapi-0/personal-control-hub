"""Fixed-unit observations never issue controls or manufacture acceptance."""
import copy
import subprocess
import sys
from pathlib import Path
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from hub.refresh_schedule import refresh_schedule_status

TIMER = {'ActiveState': 'active', 'UnitFileState': 'enabled',
         'LastTriggerUSec': 'Wed 2026-10-14 12:00:00 JST',
         'NextElapseUSecRealtime': 'Thu 2026-10-15 12:00:00 JST', 'Persistent': 'yes'}
SERVICE = {'ActiveState': 'inactive', 'Result': 'success', 'ExecMainStatus': '0',
           'ExecMainStartTimestamp': 'Wed 2026-10-14 12:00:00 JST',
           'ExecMainExitTimestamp': 'Wed 2026-10-14 12:00:01 JST'}


def output(values):
    return '\n'.join(key + '=' + value for key, value in values.items()) + '\n'


def reply(values, code=0, stderr=''):
    return subprocess.CompletedProcess([], code, output(values), stderr)


class RefreshScheduleTests(unittest.TestCase):
    def observe(self, timer=None, service=None):
        with mock.patch('hub.refresh_schedule.sys.platform', 'linux'), mock.patch(
                'hub.refresh_schedule.subprocess.run', side_effect=[reply(timer or TIMER), reply(service or SERVICE)]) as run:
            result = refresh_schedule_status()
        return result, run

    def assert_unverified(self, result):
        self.assertEqual('unverified', result['actual_calendar_verification'])
        self.assertEqual('unverified', result['logout_recovery_verification'])
        self.assertIsNone(result['missed'])
        self.assertIsNone(result['catchup'])
        self.assertTrue(result['observed_at'].endswith('+00:00'))

    def test_active_timer_and_successful_service_have_only_allowed_fields(self):
        result, run = self.observe()
        self.assertTrue(result['available'])
        self.assertEqual('active', result['status'])
        self.assertIsNone(result['reason'])
        self.assertEqual(result['freshness_target']['interval_hours'],12)
        self.assertNotIn('incremental_check_seconds',result['freshness_target'])
        self.assertNotIn('source_to_page_seconds',result['freshness_target'])
        self.assertEqual(TIMER, result['timer'])
        expected = copy.deepcopy(SERVICE)
        expected['ExecMainStatus'] = 0
        self.assertEqual(expected, result['service'])
        self.assert_unverified(result)
        self.assertEqual(2, run.call_count)
        units = []
        for call in run.call_args_list:
            command = call.args[0]
            self.assertIs(type(command), list)
            self.assertEqual(['systemctl', '--user', 'show', '--no-pager'], command[:4])
            self.assertTrue(command[4].startswith('--property='))
            self.assertEqual(6, len(command))
            self.assertNotIn('ExecStart', command[4])
            units.append(command[-1])
            self.assertIs(False, call.kwargs['shell'])
            self.assertLessEqual(call.kwargs['timeout'], 3)
            self.assertTrue(call.kwargs['capture_output'])
        self.assertEqual(['personal-control-hub-refresh.timer', 'personal-control-hub-refresh.service'], units)
        with self.assertRaises(TypeError):
            refresh_schedule_status('another.service')

    def test_24_hour_expectation_retains_actual_failed_exit(self):
        failed={**SERVICE,'Result':'exit-code','ExecMainStatus':'2'}
        with mock.patch('hub.refresh_schedule.sys.platform','linux'),mock.patch(
                'hub.refresh_schedule.subprocess.run',side_effect=[reply(TIMER),reply(failed)]):
            result=refresh_schedule_status(interval_hours=24)
        self.assertEqual(result['freshness_target']['expected_interval_seconds'],86400)
        self.assertEqual(result['service']['ExecMainStatus'],2)
        self.assertEqual(result['service']['Result'],'exit-code')
        self.assertEqual(result['actual_calendar_verification'],'unverified')

    def test_paused_timer_retains_unknown_calendar_and_recovery(self):
        timer = {**TIMER, 'ActiveState': 'inactive', 'UnitFileState': 'disabled',
                 'LastTriggerUSec': '', 'NextElapseUSecRealtime': ''}
        result, _ = self.observe(timer=timer)
        self.assertTrue(result['available'])
        self.assertEqual('paused', result['status'])
        self.assert_unverified(result)

    def test_current_active_does_not_mean_enabled_or_calendar_accepted(self):
        result, _ = self.observe(timer={**TIMER, 'UnitFileState': 'disabled'})
        self.assertEqual('active', result['status'])
        self.assertEqual('disabled', result['timer']['UnitFileState'])
        self.assert_unverified(result)

    def test_second_query_failure_withholds_partial_timer_observation(self):
        with mock.patch('hub.refresh_schedule.sys.platform', 'linux'), mock.patch(
                'hub.refresh_schedule.subprocess.run', side_effect=[reply(TIMER), subprocess.TimeoutExpired('systemctl', 2)]):
            result = refresh_schedule_status()
        self.assertFalse(result['available'])
        self.assertEqual('query_timeout', result['reason'])
        self.assertIsNone(result['timer'])
        self.assertIsNone(result['service'])
        self.assert_unverified(result)

    def test_service_failure_is_an_observed_failure_not_a_missing_observation(self):
        result, _ = self.observe(service={**SERVICE, 'ActiveState': 'failed', 'Result': 'exit-code', 'ExecMainStatus': '2'})
        self.assertTrue(result['available'])
        self.assertEqual('failed', result['service']['ActiveState'])
        self.assertEqual('exit-code', result['service']['Result'])
        self.assertEqual(2, result['service']['ExecMainStatus'])
        self.assert_unverified(result)

    def test_timer_failed_is_distinct_from_paused(self):
        result, _ = self.observe(timer={**TIMER, 'ActiveState': 'failed'})
        self.assertTrue(result['available'])
        self.assertEqual('unavailable', result['status'])
        self.assertEqual('timer_not_ready', result['reason'])

    def test_nonlinux_never_queries_systemd(self):
        with mock.patch('hub.refresh_schedule.sys.platform', 'darwin'), mock.patch('hub.refresh_schedule.subprocess.run') as run:
            result = refresh_schedule_status()
        run.assert_not_called()
        self.assertFalse(result['available'])
        self.assertEqual('unsupported_platform', result['reason'])

    def test_missing_binary_timeout_and_os_failure_return_fixed_safe_reasons(self):
        for exception, reason in ((FileNotFoundError('private path'), 'systemctl_unavailable'),
                                  (subprocess.TimeoutExpired('secret command', 2, output='secret', stderr='secret'), 'query_timeout'),
                                  (PermissionError('private error'), 'query_failed')):
            with self.subTest(reason=reason), mock.patch('hub.refresh_schedule.sys.platform', 'linux'), mock.patch(
                    'hub.refresh_schedule.subprocess.run', side_effect=exception) as run:
                result = refresh_schedule_status()
                self.assertEqual(1, run.call_count)
                self.assertFalse(result['available'])
                self.assertEqual(reason, result['reason'])
                self.assertNotIn('secret', str(result))
                self.assertIsNone(result['timer'])
                self.assertIsNone(result['service'])
                self.assert_unverified(result)

    def test_user_bus_unavailable_and_uninstalled_are_not_silently_active(self):
        for code, stderr, reason in ((1, 'Failed to connect to bus: No medium found SECRET', 'user_bus_unavailable'),
                                     (4, 'Unit not found SECRET', 'unit_unavailable'),
                                     (1, 'Other private failure SECRET', 'query_failed')):
            with self.subTest(reason=reason), mock.patch('hub.refresh_schedule.sys.platform', 'linux'), mock.patch(
                    'hub.refresh_schedule.subprocess.run', return_value=reply({}, code, stderr)):
                result = refresh_schedule_status()
                self.assertFalse(result['available'])
                self.assertEqual(reason, result['reason'])
                self.assertNotIn('SECRET', str(result))
        result, _ = self.observe(timer={**TIMER, 'UnitFileState': '', 'Persistent': ''})
        self.assertFalse(result['available'])
        self.assertEqual('unit_unavailable', result['reason'])

    def test_unexpected_missing_duplicate_or_sensitive_output_is_not_exposed(self):
        malformed = [output(TIMER) + 'ExecStart=SECRET\n', output(TIMER) + 'ActiveState=active\n',
                     output({key: value for key, value in TIMER.items() if key != 'Persistent'}),
                     output({**TIMER, 'Persistent': 'SECRET'}), output({**TIMER, 'LastTriggerUSec': 'SECRET'}),
                     'SECRET\n', 'x' * 20000]
        for stdout in malformed:
            with self.subTest(stdout=stdout[:80]), mock.patch('hub.refresh_schedule.sys.platform', 'linux'), mock.patch(
                    'hub.refresh_schedule.subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout, 'SECRET')):
                result = refresh_schedule_status()
                self.assertFalse(result['available'])
                self.assertEqual('invalid_output', result['reason'])
                self.assertIsNone(result['timer'])
                self.assertNotIn('SECRET', str(result))
        for service in ({**SERVICE, 'ExecMainStatus': '-1'}, {**SERVICE, 'ExecMainStatus': '999'},
                        {**SERVICE, 'ActiveState': 'SECRET'}, {**SERVICE, 'Result': 'SECRET'}):
            result, _ = self.observe(service=service)
            self.assertFalse(result['available'])
            self.assertEqual('invalid_output', result['reason'])
            self.assertIsNone(result['timer'])
            self.assertNotIn('SECRET', str(result))
