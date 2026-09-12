"""Whitelist-only projections of historical downloader and management-tool records."""
import math
import os
import re
import stat
from datetime import datetime, timezone

from hub.connection_records import content_hash
from hub.connections import parse_source
from hub.metric_documents import Projection
from hub.metric_sources import metadata_path, read_structured

LOG_BYTES = 512 * 1024
LOG_LINES = 2000
EVENTS = ('download_failed', 'download_interrupted', 'download_plan_context',
          'download_retryable_error', 'download_start', 'download_success',
          'invalid_plan', 'missing_plan', 'missing_ytdlp', 'reuse_existing_final',
          'stale_plan_used', 'plan_saved', 'probe_failed', 'probe_start', 'skip_existing_plan')


def _read(p, path):
    try:
        obj, _ = read_structured(p.root, path)
        if not isinstance(obj, dict):
            raise ValueError('object required')
        return obj
    except (OSError, ValueError, TypeError):
        p.problem(path)
        return None


def _entries(obj):
    if not isinstance(obj, dict) or any(not isinstance(k, str) or not k for k in obj):
        return None
    return sorted((content_hash(k), v) for k, v in obj.items())


def _epoch(value):
    if type(value) not in (int, float):
        return None
    try:
        if not math.isfinite(value) or value < 0:
            return None
        datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return value


def _times(p, name, rows, field, path):
    times = [(key, _epoch(v.get(field))) for key, v in rows] if rows is not None and all(isinstance(v, dict) for _, v in rows) else None
    valid = times is not None and all(t is not None for _, t in times)
    latest = max((t for _, t in times), default=None) if valid else None
    p.emit(name, latest, 'unix_seconds', path + '#' + field, times,
           business_at=datetime.fromtimestamp(latest, timezone.utc).isoformat() if latest is not None else None,
           reason='No valid saved business timestamp is established by this source.',
           basis='Latest saved timestamp in the selected records; observation time and current execution are not inferred.')


def _record_count(p, name, rows, fields, unit, path, basis):
    # Time validity is independent of the count. All selected times enter the
    # semantic version, even when a malformed timestamp prevents dating it.
    times = [(key, [(field, _epoch(value.get(field))) for field in fields])
             for key, value in rows] if rows is not None and all(isinstance(v, dict) for _, v in rows) else None
    selected_times = []
    if times is not None:
        for (_, value), (_, stamps) in zip(rows, times):
            chosen = next((stamp for field, stamp in stamps if field in value), None)
            selected_times.append(chosen)
    known = bool(selected_times) and all(stamp is not None for stamp in selected_times)
    business_at = datetime.fromtimestamp(max(selected_times), timezone.utc).isoformat() if known else None
    p.emit(name, len(rows) if rows is not None else None, unit, path,
           [[key for key, _ in rows] if rows is not None else None, times],
           business_at=business_at, basis=basis)


def _unknown_bindings(p):
    for name, reason in [('current_code_bound', 'Saved metadata does not bind these records to the current code version.'),
                         ('current_environment_bound', 'Saved metadata does not bind these records to the current environment.')]:
        p.emit(name, None, 'bindings', 'adapter:' + p.prefix + '.' + name, reason=reason,
               basis='Historical metadata only; no producer execution or current validation.')


def _log(p):
    path = 'state/run_log.jsonl'
    try:
        target = metadata_path(p.root, path)
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > LOG_BYTES:
                raise ValueError('log budget')
            raw = handle.read(LOG_BYTES + 1)
            after = os.fstat(handle.fileno())
        if len(raw) > LOG_BYTES or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError('log changed or budget exceeded')
        lines = raw.splitlines()
        if len(lines) > LOG_LINES:
            raise ValueError('line budget')
        complete = True
    except (OSError, ValueError, TypeError):
        p.problem(path)
        lines, complete = [], False
    p.emit('saved_log_coverage_complete', int(complete), 'indicators', path,
           basis='One means the complete current saved file fit the byte and line budgets; never all historical runs.')
    rows = []
    valid_events = complete
    for index, line in enumerate(lines):
        try:
            obj = parse_source(line, 'json')
            if not isinstance(obj, dict):
                raise ValueError('event object')
        except (ValueError, TypeError):
            obj = {}
        event = obj.get('event')
        if not isinstance(event, str) or not event:
            valid_events = False
        rows.append((str(index), {'event': event if event in EVENTS else 'unclassified', 'ts': obj.get('ts')}))
    _record_count(p, 'saved_event_lines', rows if complete else None, ('ts',), 'event_lines', path,
                  'Physical lines in the current saved log, including malformed lines; not unique jobs or runs.')
    for event in (*EVENTS, 'unclassified'):
        selected = [(key, row) for key, row in rows if row['event'] == event] if valid_events else None
        _record_count(p, 'saved_event_' + event, selected, ('ts',), 'event_lines', path + '#event',
                      'Matching saved event lines in this complete current file only; repetitions count separately. Unclassified means a valid event string outside the whitelist; its value is not projected.')
    _times(p, 'saved_log_latest_at', rows if complete else None, 'ts', path)


def collect_downloader(root, pid, observed_at, spec=None):
    p = Projection(root, pid, observed_at, 'downloader')
    p.dimensions = {'scope': 'historical_saved_tool_records'}
    path = 'state/failed_jobs.json'
    rows = _entries(_read(p, path))
    _record_count(p, 'saved_latest_failure_records', rows, ('last_failed_at',), 'records', path,
                  'Unique hashed saved cache identities; last failure is overwritten and success does not clear it. Not unresolved backlog.')
    valid = rows is not None and all(isinstance(v, dict) and type(v.get('has_partial')) is bool for _, v in rows)
    selected = [(k, v) for k, v in rows if v['has_partial']] if valid else None
    _record_count(p, 'saved_failure_partial_records', selected, ('last_failed_at',), 'records', path + '#has_partial',
                  'Saved latest failure records explicitly marked has_partial; not current filesystem verification.')
    _times(p, 'saved_failure_latest_at', rows, 'last_failed_at', path)
    path = 'state/plan_cache.json'
    rows = _entries(_read(p, path))
    plan_times = ('last_status_at', 'last_verified_at', 'cached_at')
    _record_count(p, 'saved_plan_records', rows, plan_times, 'records', path,
                  'Unique hashed identities in the saved plan cache; timestamp uses first present status, verification, then cache field.')
    valid = rows is not None and all(isinstance(v, dict) and v.get('status') in ('usable', 'suspected_expired') for _, v in rows)
    for status in ('usable', 'suspected_expired'):
        selected = [(k, v) for k, v in rows if v['status'] == status] if valid else None
        _record_count(p, 'saved_plan_' + status, selected, plan_times, 'records', path + '#status',
                      'Saved cache status only; usable does not establish current downloadability. Timestamp uses first present status, verification, then cache field.')
    for field in ('cached_at', 'last_verified_at', 'last_status_at'):
        _times(p, 'saved_plan_latest_' + field, rows, field, path)
    _log(p)
    _unknown_bindings(p)
    return p.finish()


def _checks(projects, group, field=None):
    rows = _entries(projects)
    if rows is None:
        return None
    result = []
    for project, obj in rows:
        entries = _entries(obj.get(group)) if isinstance(obj, dict) else None
        if entries is None:
            return None
        for key, value in entries:
            result.append((content_hash([project, key]), value.get(field) if field and isinstance(value, dict) else None if field else value))
    return sorted(result)


def _booleans(p, name, rows, path):
    p.emit(name + '_checks', len(rows) if rows is not None else None, 'checks', path,
           [k for k, _ in rows] if rows is not None else None,
           basis='Unique hashed project and check-path identities in this management tool report only; not projects business progress.')
    valid = rows is not None and all(type(value) is bool for _, value in rows)
    for state, label in ((True, 'present'), (False, 'absent')):
        selected = [key for key, value in rows if value is state] if valid else None
        p.emit(name + '_' + label, len(selected) if selected is not None else None, 'checks', path, selected,
               basis='Explicit saved boolean checks in this report and field only; do not aggregate duplicate version checks across reports.')


def collect_workspace_checks(root, pid, observed_at, spec=None):
    p = Projection(root, pid, observed_at, 'workspace_checks')
    p.dimensions = {'scope': 'historical_management_tool_checks'}
    for filename, report in [('project_protocol_check.json', 'protocol'), ('protocol_version_comparison.json', 'comparison')]:
        path = 'reports/' + filename
        obj = _read(p, path)
        target = obj.get('global_protocol_version') if obj is not None else None
        # Keep only a bounded version token; never emit arbitrary report text.
        safe_target = target if isinstance(target, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', target) else None
        p.dimensions = {'scope': 'historical_management_tool_checks', 'target_protocol_version': safe_target or 'unknown'}
        if safe_target is None:
            p.problem(path + '#global_protocol_version')
        projects = obj.get('projects') if obj is not None else None
        entries = _entries(projects)
        p.emit(report + '_projects_checked', len(entries) if entries is not None else None, 'project_checks', path + '#projects',
               [k for k, _ in entries] if entries is not None else None,
               basis='Distinct project identities represented in this saved management-tool report; not downstream business completion.')
        _booleans(p, report + '_version_file', _checks(projects, 'versions', 'exists'), path + '#projects.versions.exists')
        if report == 'protocol':
            _booleans(p, 'protocol_presence', _checks(projects, 'presence'), path + '#projects.presence')
            issues = _checks(projects, 'local_override_issues')
            valid = issues is not None and all(isinstance(value, list) for _, value in issues)
            counts = [(key, len(value)) for key, value in issues] if valid else None
            p.emit('protocol_override_issue_items', sum(n for _, n in counts) if valid else None, 'issue_items', path + '#projects.local_override_issues', counts,
                   basis='Saved issue-list lengths only; freeform issue text is never projected.')
        else:
            for field in ('real_exists', 'candidate_exists'):
                _booleans(p, 'comparison_template_' + field, _checks(projects, 'template_coverage', field), path + '#projects.template_coverage.' + field)
        stale = _checks(projects, 'versions', 'stale')
        judgable = [(key, value) for key, value in stale if type(value) is bool] if stale is not None else None
        p.emit(report + '_versions_judgable', len(judgable) if judgable is not None else None, 'checks', path + '#projects.versions.stale', judgable,
               basis='Version checks with an explicit boolean stale field; absent or invalid stale fields are not judgable.')
        complete = stale is not None and len(judgable) == len(stale)
        selected = [key for key, value in judgable if value] if complete else None
        p.emit(report + '_stale_versions', len(selected) if selected is not None else None, 'checks', path + '#projects.versions.stale', selected,
               reason='Complete boolean stale coverage is not established; missing is unknown, not false.',
               basis='Stale checks across the complete saved version-check set; a total is available only when every stale field is boolean.')
        p.emit(report + '_business_timestamp', None, 'unix_seconds', path + '#generated_at',
               reason='No generated business timestamp is established; filesystem mtime is observation metadata only.')
    p.dimensions.pop('target_protocol_version', None)
    _unknown_bindings(p)
    return p.finish()
