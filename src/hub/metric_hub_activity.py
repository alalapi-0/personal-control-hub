"""Read-only facts about saved Hub collection service activity, never project completion."""
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime

from hub.connection_records import METRIC_QUALITY_SEMANTICS, content_hash, timestamp
from hub.connection_refresh import RefreshLedgerError
from hub.metric_documents import Projection
from hub.metric_store import MetricStore

MAX_ROWS = 10000
MAX_BYTES = 2 * 1024 * 1024
SOURCE = 'data/connections/connection_refresh.sqlite3'
COUNTS = {'saved_requests': ('metric_requests', 'requests'),
          'saved_receipts': ('metric_receipts', 'receipts'),
          'covered_projects': ('metric_projects', 'projects'),
          'current_metric_records': ('metric_current', 'metrics'),
          'saved_metric_versions': ('metric_changes', 'metric_versions')}
DERIVED = {'complete_saved_requests': 'requests', 'incomplete_saved_requests': 'requests',
           'current_numeric_metrics': 'metrics', 'current_unknown_metrics': 'metrics',
           'current_missing_metrics': 'metrics', 'current_not_applicable_metrics': 'metrics',
           'current_invalid_metrics': 'metrics', 'malformed_current_metric_records': 'metrics',
           'service_observed_issues': 'issues'}


def _bounded(db, table, size):
    count, size = db.execute(f'SELECT count(*), coalesce(sum({size}),0) FROM {table}').fetchone()
    if count > MAX_ROWS or size > MAX_BYTES:
        raise ValueError('selected metadata budget')


def _object(raw):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('duplicate field')
            obj[key] = value
        return obj
    return json.loads(raw, object_pairs_hook=unique)


def _latest_time(rows, projection, group):
    stamps = [row[0] for row in rows]
    if not stamps:
        return None
    try:
        for stamp in stamps:
            timestamp(stamp, 'persisted observation')
        return max(stamps, key=lambda stamp: datetime.fromisoformat(stamp.replace('Z', '+00:00')))
    except (ValueError, TypeError, AttributeError):
        projection.problem(SOURCE + '#' + group + '_time_unknown')
        return None


def collect_hub_activity(root, pid, observed_at, spec=None):
    # spec deliberately cannot redirect the ledger or trigger another collector.
    p = Projection(root, pid, observed_at, 'hub_activity')
    p.dimensions = {'scope': 'saved_collection_service_activity'}
    values, semantics, times = {}, {}, {}
    try:
        store = MetricStore(root, read_only=True)
        with closing(store._connect()) as db:
            db.execute('BEGIN')
            for name, (table, _) in COUNTS.items():
                values[name] = db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
            identity_columns = {
                'saved_requests': ('id,identity', None),
                'saved_receipts': ('request_id,project_id', None),
                'covered_projects': ('project_id', 'observed_at'),
                'current_metric_records': ('key,project_id,seq', 'observed_at'),
                'saved_metric_versions': ('seq,project_id,key,version', 'observed_at'),
            }
            for name, (columns, time_column) in identity_columns.items():
                table = COUNTS[name][0]
                try:
                    bounded_columns = columns + (',' + time_column if time_column else '')
                    size = '+'.join('coalesce(length(CAST(' + c + ' AS BLOB)),0)' for c in bounded_columns.split(','))
                    _bounded(db, table, size)
                    members = list(db.execute('SELECT ' + columns + ' FROM ' + table + ' ORDER BY ' + columns))
                    semantics[name] = content_hash([list(row) for row in members])
                    if time_column:
                        times[name] = _latest_time(db.execute('SELECT ' + time_column + ' FROM ' + table), p, name)
                except (ValueError, TypeError, sqlite3.Error):
                    p.problem(SOURCE + '#' + name + '_identity_budget')
                    semantics[name] = 'identity_coverage_unavailable'
            try:
                _bounded(db, 'metric_receipts', 'length(CAST(result AS BLOB))')
                times['saved_receipts'] = _latest_time(db.execute(
                    "SELECT json_extract(CASE WHEN json_valid(result) THEN result ELSE '{}' END,'$.observed_at') FROM metric_receipts"), p, 'receipt_time')
            except (ValueError, TypeError, sqlite3.Error):
                p.problem(SOURCE + '#receipt_time_budget')
            try:
                _bounded(db, 'metric_requests', 'length(CAST(id AS BLOB))+length(CAST(identity AS BLOB))+length(CAST(projects AS BLOB))')
                _bounded(db, 'metric_receipts', 'length(CAST(request_id AS BLOB))+length(CAST(project_id AS BLOB))')
                receipts = {}
                for row in db.execute('SELECT request_id,project_id FROM metric_receipts'):
                    receipts.setdefault(row[0], []).append(row[1])
                complete = incomplete = 0
                selected = []
                for request, identity, raw in db.execute('SELECT id,identity,projects FROM metric_requests ORDER BY id'):
                    expected = _object(raw)
                    if (not isinstance(expected, list) or not expected or
                        any(type(x) is not str or not x or len(x) > 192 for x in expected) or
                        len(set(expected)) != len(expected)):
                        raise ValueError('invalid expected coverage')
                    actual = receipts.get(request, [])
                    done = len(actual) == len(set(actual)) and set(actual) == set(expected)
                    complete += done
                    incomplete += not done
                    selected.append([content_hash([request, identity]), sorted(expected), sorted(actual)])
                values.update(complete_saved_requests=complete, incomplete_saved_requests=incomplete)
                for name in ('complete_saved_requests', 'incomplete_saved_requests'):
                    semantics[name] = content_hash(selected)
            except (ValueError, TypeError, sqlite3.Error):
                p.problem(SOURCE + '#request_coverage')
            try:
                join = 'metric_current c LEFT JOIN metric_changes h ON c.seq=h.seq'
                _bounded(db, join, 'length(CAST(h.value AS BLOB))')
                quality = {name: [] for name in METRIC_QUALITY_SEMANTICS}
                quality['malformed'] = []
                for key, project, seq, valid, q, value, value_type, keys in db.execute(
                    "SELECT c.key,c.project_id,c.seq,json_valid(h.value), json_extract(CASE WHEN json_valid(h.value) THEN h.value ELSE '{}' END,'$.quality'), "
                    "json_extract(CASE WHEN json_valid(h.value) THEN h.value ELSE '{}' END,'$.value'), "
                    "json_type(CASE WHEN json_valid(h.value) THEN h.value ELSE '{}' END,'$.value'), "
                    "(SELECT count(*) FROM json_each(CASE WHEN json_valid(h.value) THEN h.value ELSE '{}' END) WHERE key IN ('quality','value')) FROM " + join):
                    valid_number = value_type in ('integer', 'real') and type(value) in (int, float) and math.isfinite(value)
                    valid_fields = (valid and keys == 2 and q in METRIC_QUALITY_SEMANTICS and
                                    ((q == 'good' and valid_number) or (q != 'good' and value is None and value_type == 'null')))
                    quality[q if valid_fields else 'malformed'].append([key, project, seq])
                for name, category in [('current_numeric_metrics', 'good'), ('current_unknown_metrics', 'unknown'),
                                       ('current_missing_metrics', 'missing'),
                                       ('current_not_applicable_metrics', 'not_applicable'),
                                       ('current_invalid_metrics', 'invalid'), ('malformed_current_metric_records', 'malformed')]:
                    values[name] = len(quality[category])
                    semantics[name] = content_hash(sorted(quality[category]))
                    times[name] = times.get('current_metric_records')
                if quality['malformed']:
                    p.problem(SOURCE + '#malformed_current_metric_records')
            except (ValueError, TypeError, AttributeError, OverflowError, sqlite3.Error):
                p.problem(SOURCE + '#current_quality')
            try:
                # Only project summary issue array length is projected; never its bodies.
                _bounded(db, 'metric_projects', 'length(CAST(result AS BLOB))')
                total, members = 0, []
                for project, kind, count, keys in db.execute(
                    "SELECT project_id,json_type(CASE WHEN json_valid(result) THEN result ELSE '{}' END,'$.issues'), "
                    "json_array_length(CASE WHEN json_valid(result) THEN result ELSE '{}' END,'$.issues'), "
                    "(SELECT count(*) FROM json_each(CASE WHEN json_valid(result) THEN result ELSE '{}' END) WHERE key='issues') FROM metric_projects"):
                    if kind != 'array' or keys != 1:
                        raise ValueError('invalid issues metadata')
                    total += count
                    members.append([project, count])
                values['service_observed_issues'] = total
                semantics['service_observed_issues'] = content_hash(sorted(members))
                times['service_observed_issues'] = times.get('covered_projects')
            except (ValueError, TypeError, AttributeError, sqlite3.Error):
                p.problem(SOURCE + '#service_issues')
    except (OSError, ValueError, TypeError, sqlite3.Error, RefreshLedgerError):
        p.problem(SOURCE + '#ledger_unavailable')
    for name, (_, unit) in COUNTS.items():
        p.emit(name, values.get(name), unit, SOURCE, semantic=semantics.get(name), business_at=times.get(name), basis='Exact saved metric-table row count in one read-only snapshot; no active-run, business-completion or test-pass inference.')
    for name, unit in DERIVED.items():
        basis = ('Saved requests whose unique expected project set exactly equals saved receipt coverage; dispositions may include partial or failed collection. Incomplete means saved coverage only, never currently executing. Request creation time is not persisted and remains unknown.'
                 if name.endswith('saved_requests') else
                 'Validated saved records in one bounded snapshot: numeric, missing, unknown, not-applicable, invalid and malformed classifications partition all current metric rows. Malformed rows are counted separately, never normal unknowns. Issues describe service observations, not underlying project business facts. Business time is latest persisted dependency observation, not creation or execution time.')
        p.emit(name, values.get(name), unit, SOURCE, semantic=semantics.get(name), business_at=times.get(name), basis=basis)
    return p.finish()
