"""Read-only observations of the two fixed user refresh units.

Systemd's observed timestamps are not proof of calendar or logout recovery.
This module never installs, starts, stops, or changes a unit.
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

_TIMER = 'personal-control-hub-refresh.timer'
_SERVICE = 'personal-control-hub-refresh.service'
_TIMER_FIELDS = ('ActiveState', 'UnitFileState', 'LastTriggerUSec',
                 'NextElapseUSecRealtime', 'Persistent')
_SERVICE_FIELDS = ('ActiveState', 'Result', 'ExecMainStatus',
                   'ExecMainStartTimestamp', 'ExecMainExitTimestamp')
_ACTIVE_STATES = {'active', 'reloading', 'inactive', 'failed', 'activating',
                  'deactivating', 'maintenance', 'refreshing'}
_FILE_STATES = {'enabled', 'enabled-runtime', 'linked', 'linked-runtime',
                'alias', 'masked', 'masked-runtime', 'static', 'disabled',
                'indirect', 'generated', 'transient'}
_TIMESTAMP = re.compile(r'(?:|n/a|0|(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) '
                        r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)? [A-Za-z0-9_+:/-]+)')


def schedule_policy(schedule=None) -> dict:
    """One strict cadence configuration shared by collectors, units and pages."""
    if schedule is None:
        schedule = {'interval_hours': 12}
    if (type(schedule) is not dict or set(schedule) != {'interval_hours'}
            or type(schedule['interval_hours']) is not int or schedule['interval_hours'] not in (12, 24)):
        raise ValueError('schedule requires interval_hours 12 or 24')
    hours = schedule['interval_hours']
    return {'interval_hours': hours, 'timezone': 'Asia/Tokyo', 'anchor_time': '09:00',
            'calendar': '09:00,21:00' if hours == 12 else '09:00',
            'expected_interval_seconds': hours * 3600}


def latest_due_slot(instant: datetime, interval_hours: int = 12) -> datetime:
    """Only the most recent due slot; missed slots never create a backlog."""
    schedule_policy({'interval_hours': interval_hours})
    if instant.tzinfo is None:
        raise ValueError('scheduled instant needs timezone')
    local = instant.astimezone(ZoneInfo('Asia/Tokyo'))
    anchor = local.replace(hour=9, minute=0, second=0, microsecond=0)
    if local < anchor:
        anchor -= timedelta(days=1)
    if interval_hours == 12 and local >= anchor + timedelta(hours=12):
        anchor += timedelta(hours=12)
    return anchor



def retry_readiness(failures: list[dict], instant: datetime) -> dict:
    """Pure projection. A deadline is eligibility, never permission to reread."""
    if instant.tzinfo is None:
        raise ValueError('retry instant needs timezone')
    if not failures:
        return {'state': 'ready', 'failure_count': 0, 'next_retry_at': None,
                'recovery_condition': 'No unresolved source failure.'}
    first, last = failures[0], failures[-1]
    began = datetime.fromisoformat(first['observed_at'].replace('Z', '+00:00'))
    observed = datetime.fromisoformat(last['observed_at'].replace('Z', '+00:00'))
    delay = (30, 120, 600)[min(len(failures) - 1, 2)]
    cap = began + timedelta(minutes=15)
    deadline = min(observed + timedelta(seconds=delay), cap)
    streak = 0
    for item in reversed(failures):
        if item['failure_signature'] != last['failure_signature']:
            break
        streak += 1
    state = ('event_wait' if instant >= cap or instant < observed else 'diagnosis_required'
             if streak >= 2 else 'retry_wait'
             if instant < deadline else 'retry_due')
    return {**last, 'state': state, 'failure_count': len(failures),
            'no_progress_count': streak, 'diagnosis_required': streak >= 2, 'backoff_seconds': delay, 'clock_regressed': instant < observed,
            'first_failure_at': first['observed_at'], 'cap_at': cap.isoformat(),
            'deadline_at': deadline.isoformat(),
            'next_retry_at': deadline.isoformat() if instant < cap else None,
            'transient_retries': sum(item.get('admission_kind') == 'T' for item in failures),
            'recovery_condition': ('Diagnosed repair with a new source or method version.'
                if streak >= 2 else 'Relevant changed source/binding evidence; at most one demonstrated transient retry.')}

def _elapsed_seconds(start,end):
    """Compare only complete timestamps in the same reported timezone."""
    try:
        left,left_zone=start.rsplit(' ',1);right,right_zone=end.rsplit(' ',1)
        if left_zone!=right_zone:return None
        seconds=(datetime.fromisoformat(right[4:])-datetime.fromisoformat(left[4:])).total_seconds()
        return seconds if seconds>=0 else None
    except (ValueError,TypeError,AttributeError):return None


def _parse(stdout: str, fields: tuple[str, ...]) -> dict:
    if type(stdout) is not str or len(stdout) > 16384:
        raise ValueError('invalid output')
    values = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition('=')
        if not separator or key not in fields or key in values or len(value) > 256:
            raise ValueError('invalid output')
        values[key] = value
    if set(values) != set(fields) or values['ActiveState'] not in _ACTIVE_STATES:
        raise ValueError('invalid output')
    timestamp_fields = ('LastTriggerUSec', 'NextElapseUSecRealtime') if fields == _TIMER_FIELDS else (
        'ExecMainStartTimestamp', 'ExecMainExitTimestamp')
    if any(not _TIMESTAMP.fullmatch(values[field]) for field in timestamp_fields):
        raise ValueError('invalid output')
    if fields == _TIMER_FIELDS:
        if values['UnitFileState'] == '' and values['Persistent'] == '':
            raise LookupError('unit unavailable')
        if values['UnitFileState'] not in _FILE_STATES or values['Persistent'] not in {'yes', 'no'}:
            raise ValueError('invalid output')
    else:
        if not re.fullmatch(r'[a-z][a-z-]{0,63}', values['Result']):
            raise ValueError('invalid output')
        if not re.fullmatch(r'[0-9]{1,3}', values['ExecMainStatus']) or int(values['ExecMainStatus']) > 255:
            raise ValueError('invalid output')
        values['ExecMainStatus'] = int(values['ExecMainStatus'])
    return values


def refresh_schedule_status(*, interval_hours: int = 12) -> dict:
    """Observe only the fixed timer/service; never accept a command or unit."""
    policy = schedule_policy({'interval_hours': interval_hours})
    result = {'available': False, 'reason': None,
              'observed_at': datetime.now(timezone.utc).isoformat(),
              'status': 'unavailable', 'timer': None, 'service': None,
              'actual_calendar_verification': 'unverified',
              'logout_recovery_verification': 'unverified',
              'freshness_target': {**policy, 'configuration_verification': 'unverified'},
              'last_run_seconds':None,'overloaded':None,
              'missed': None, 'catchup': None}
    if sys.platform != 'linux':
        result['reason'] = 'unsupported_platform'
        return result
    observations = []
    for unit, fields in ((_TIMER, _TIMER_FIELDS), (_SERVICE, _SERVICE_FIELDS)):
        try:
            completed = subprocess.run(
                ['systemctl', '--user', 'show', '--no-pager', '--property=' + ','.join(fields), unit],
                shell=False, timeout=2, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            result['reason'] = 'systemctl_unavailable'
            return result
        except subprocess.TimeoutExpired:
            result['reason'] = 'query_timeout'
            return result
        except (OSError, UnicodeError):
            result['reason'] = 'query_failed'
            return result
        if completed.returncode != 0:
            stderr = completed.stderr if type(completed.stderr) is str else ''
            if any(message in stderr.lower() for message in (
                    'failed to connect to bus', 'failed to connect to user scope bus', 'no medium found')):
                result['reason'] = 'user_bus_unavailable'
            else:
                result['reason'] = 'unit_unavailable' if completed.returncode == 4 else 'query_failed'
            return result
        try:
            observations.append(_parse(completed.stdout, fields))
        except LookupError:
            result['reason'] = 'unit_unavailable'
            return result
        except (ValueError, TypeError):
            result['reason'] = 'invalid_output'
            return result
    timer, service = observations
    elapsed=_elapsed_seconds(service['ExecMainStartTimestamp'],service['ExecMainExitTimestamp'])
    result.update(available=True, timer=timer, service=service,
                  last_run_seconds=elapsed,overloaded=elapsed>policy['expected_interval_seconds'] if elapsed is not None else None,
                  status='active' if timer['ActiveState'] == 'active' else
                         'paused' if timer['ActiveState'] == 'inactive' else 'unavailable')
    if result['status'] == 'unavailable':
        result['reason'] = 'timer_not_ready'
    return result
