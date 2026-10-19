"""Explicit bounded review DTOs over the existing PCH services; no refreshes."""
import datetime as dt
import hashlib
import re

from .rr_payload import canonical_json, RRFeedbackError, opaque

WORK_FIELDS = ('objective', 'phase', 'round', 'status', 'next_action', 'completed', 'accepted')
PROGRESS_FIELDS = ('completed', 'total', 'counting_basis')
DEFINITION_FIELDS = ('official_name', 'aliases', 'purpose', 'outputs')
STATUS = {'active', 'paused', 'blocked', 'complete', 'unknown'}


def public_text(value, limit=2000):
    """Do not export local paths, URLs, credential assignments or task bodies."""
    if type(value) is not str:
        return None
    text = ''.join(c for c in value[:limit] if c.isprintable() or c in '\n\t')
    text = re.sub(r'(?:https?://|file://|/home/|/tmp/|/data/|/run/|/Users/|[A-Za-z]:\\)[^\s，。；,;\)\]]*', '[位置已隐藏]', text)
    text = re.sub(r'(?i)\b(token|password|passwd|secret|api[_-]?key|cookie|authorization)\s*[:=]\s*[^\s,;]+', '[敏感字段已隐藏]', text)
    return text


def value(item):
    if item is None or type(item) is bool or type(item) is int:
        return item
    if type(item) is str:
        return public_text(item)
    if type(item) is list:
        return [value(x) for x in item[:20] if type(x) in (str, int, bool)]
    return None


def project_view(row):
    ident = opaque(row['project_id'])
    fresh = row.get('freshness', {})
    business = row.get('business', {})
    current = (row.get('declared', {}).get('connection_read_allowed') is True
               and fresh.get('state') == 'fresh' and not fresh.get('authority_drift')
               and business.get('state') == 'current')
    historical = row.get('last_success_snapshot', {})
    fields = business.get('fields') if current else historical.get('fields')
    fields = fields if type(fields) is dict else {}
    work, progress = fields.get('current_work') or {}, fields.get('progress') or {}
    definition = {}
    for name in DEFINITION_FIELDS:
        source = row.get('definition', {}).get('fields', {}).get(name, {})
        known = source.get('state') == 'known'
        definition[name] = {'state': 'known' if known else 'unavailable',
                            'value': value(source.get('value')) if known else None}
    state = fresh.get('state')
    return {'project_id': ident, 'name': public_text(row.get('name'), 160),
            'project_type': public_text(row.get('declared', {}).get('project_type'), 100),
            'status': business.get('normalized_status') if business.get('normalized_status') in STATUS else 'unknown',
            'freshness': {'state': state if state in {'fresh', 'stale', 'unknown', 'removed_local'} else 'unknown',
                          'authority_drift': fresh.get('authority_drift') is True,
                          'observed_at': public_text(fresh.get('observed_at'), 80)},
            'definition': definition,
            'work': {'state': 'current' if current else 'historical' if fields else 'unavailable',
                     'observed_at': public_text(fresh.get('observed_at') if current else historical.get('observed_at'), 80),
                     'fields': {k: value(work.get(k)) for k in WORK_FIELDS},
                     'progress': {k: value(progress.get(k)) for k in PROGRESS_FIELDS},
                     'blockers_available': type(fields.get('blockers')) is list,
                     'blockers': [{'reason': public_text(x.get('reason')),
                                   'waiting_for_user': x.get('waiting_for_user') is True,
                                   'recovery_condition': public_text(x.get('recovery_condition'))}
                                  for x in (fields.get('blockers') or [])[:20] if type(x) is dict]}}


class RRReadModel:
    def __init__(self, projects, task_store, project_ids, source):
        if projects.root != task_store.root:
            raise RRFeedbackError(503, 'RR_STORE_BINDING_REJECTED')
        if type(project_ids) is not list or not 1 <= len(project_ids) <= 100 or len(set(project_ids)) != len(project_ids):
            raise RRFeedbackError(503, 'RR_REVIEW_SCOPE_REQUIRED')
        self.projects, self.task_store = projects, task_store
        self.project_ids = tuple(opaque(x) for x in project_ids)
        self.source = dict(source)

    def envelope(self, content):
        digest = hashlib.sha256(canonical_json(content)).hexdigest()
        return {'schema': 1, 'source': self.source, 'revision': digest, 'body_sha256': digest,
                'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'data': content}

    def directory(self):
        listing = self.projects.list_projects({'limit': '100'})
        rows = [project_view(row) for row in listing['projects']
                if row['project_id'] in self.project_ids and row.get('freshness', {}).get('state') != 'removed_local'
                and row.get('declared', {}).get('connection_read_allowed') is True]
        return self.envelope({'projects': rows, 'total': len(rows), 'ledger_head': listing['head']})

    def project(self, ident):
        if ident not in self.project_ids:
            raise RRFeedbackError(404, 'RR_PROJECT_NOT_FOUND')
        row = self.projects.get_project(ident)
        if row.get('freshness', {}).get('state') == 'removed_local' or row.get('declared', {}).get('connection_read_allowed') is not True:
            raise RRFeedbackError(404, 'RR_PROJECT_NOT_FOUND')
        return self.envelope(project_view(row))

    def tasks(self, ident):
        self.project(ident)  # same scope and removed policy, without a source refresh
        rows = []
        for task in self.task_store.tasks():
            if task['project'] != ident:
                continue
            kind = task['payload'].get('kind')
            rows.append({'id': task['id'], 'sequence': task['sequence'], 'project_id': ident,
                         'status': task['status'], 'kind': kind if kind in {'ordinary_project', 'fixture'} else 'task',
                         'created': dt.datetime.fromtimestamp(task['created'], dt.timezone.utc).isoformat(),
                         'result_available': task['result'] is not None,
                         'result_version': self.task_store.result_version(task),
                         'human_acceptance': task['human_acceptance']})
            if len(rows) == 20:
                break
        return self.envelope({'project_id': ident, 'tasks': rows, 'limit': 20})
