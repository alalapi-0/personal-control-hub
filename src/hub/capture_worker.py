"""Explicit finite preview work; ordinary projection never calls this worker.

Operational manifests contain capture observations, not source or plan authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urldefrag

from .capture_store import CaptureStore, validate_capture_record, png_info
from .connection_records import RecordError, exact, fingerprint, identifier, require, timestamp
from .page_inventory import validate_page_inventory
from .preview_policy import (authorize_capture_url, validate_capture_policy,
                             read_preview_artifact)
from .service_contract import ArtifactResponse, ServiceError

REQUEST_FIELDS = {'schema_version', 'request_id', 'project_id', 'page_id', 'url', 'policy',
                  'source_sha256', 'build_sha256', 'source_proof_sha256', 'version_url',
                  'viewport', 'timeout_ms', 'ready_selector', 'refresh_reason', 'expires_after_seconds'}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _time(value):
    timestamp(value, 'capture time')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def validate_capture_request(value):
    exact(value, REQUEST_FIELDS, 'capture request')
    require(value['schema_version'] == '1.0', 'unsupported capture request')
    for key in ['request_id', 'project_id', 'page_id']:
        identifier(value[key], 'capture identifier')
    for key in ['source_sha256', 'source_proof_sha256']:
        fingerprint(value[key], 'capture binding')
    if value['build_sha256'] is not None:
        fingerprint(value['build_sha256'], 'capture build')
    policy = validate_capture_policy(value['policy'])
    authorize_capture_url(policy, project_id=value['project_id'], url=value['url'])
    if value['version_url'] is not None:
        authorize_capture_url(policy, project_id=value['project_id'], url=value['version_url'])
        require(urldefrag(value['version_url'])[1] == '', 'version URL cannot have fragment')
    exact(value['viewport'], {'width', 'height'}, 'capture viewport')
    for key, low, high in [('width', 320, 1920), ('height', 240, 1200)]:
        require(type(value['viewport'][key]) is int and low <= value['viewport'][key] <= high, 'capture viewport out of bounds')
    require(type(value['timeout_ms']) is int and 1 <= value['timeout_ms'] <= 30000, 'capture timeout out of bounds')
    require(value['ready_selector'] is None or type(value['ready_selector']) is str
            and 0 < len(value['ready_selector']) <= 500, 'invalid capture ready selector')
    require(value['refresh_reason'] in {'version', 'expired', 'manual'}, 'invalid capture reason')
    require(type(value['expires_after_seconds']) is int and 60 <= value['expires_after_seconds'] <= 604800,
            'invalid capture expiry')
    return copy.deepcopy(value)


def _read_allowed(project):
    return (project.get('declared', {}).get('connection_read_allowed') is True
            and project.get('freshness', {}).get('local_presence') != 'removed_local'
            and project.get('freshness', {}).get('state') != 'removed_local')


def _current(project):
    record = project.get('operational', {}).get('latest_attempt') or {}
    fresh = project.get('freshness', {})
    return (_read_allowed(project) and record.get('schema_version') == '2.0'
            and record.get('project_id') == project.get('project_id') and record.get('success') is True
            and fresh.get('state') == 'fresh' and fresh.get('authority_drift') is not True)


def _field(project, name):
    if not _current(project):
        return None
    record = project['operational']['latest_attempt']
    field = (record.get('extensions') or {}).get('fact', {}).get(name)
    if not isinstance(field, dict) or field.get('unknown_reason') is not None:
        return None
    proof = field.get('proof') or {}
    if (proof.get('observed_at') != record['observed_at'] or not any(
            s.get('id') == proof.get('source_ref') and s.get('sha256') == proof.get('sha256')
            for s in record.get('sources', []))):
        return None
    return field


def _inventory(project):
    field = _field(project, 'page_inventory')
    view = project.get('business', {}).get('page_inventory', {})
    if (field is None or field.get('value') is None or view.get('available') is not True
            or view.get('value') != field['value'] or view.get('proof') != field['proof']):
        return None
    return validate_page_inventory(field['value'], project_id=project['project_id'])


def _page(project, page_id):
    value = _inventory(project)
    if value:
        return next((p for d in value['domains'] if d['domain'] == 'implementation'
                     for p in d['pages'] if p['id'] == page_id), None)
    return None


def _assert_binding(project, request):
    page = _page(project, request['page_id'])
    field = _field(project, 'page_inventory')
    require(page is not None and project['project_id'] == request['project_id'], 'current capture page unavailable')
    require(page['source_sha256'] == request['source_sha256']
            and page['build_sha256'] == request['build_sha256']
            and field['proof']['sha256'] == request['source_proof_sha256'], 'current capture version changed')


class CaptureWorker:
    def __init__(self, projects, *, store=None, capture=None, clock=None):
        self.projects = projects
        self.store = store or CaptureStore(projects.root)
        self.capture = capture
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run(self, value, *, executable_path=None):
        request = validate_capture_request(value)
        _assert_binding(self.projects.get_project(request['project_id']), request)
        records = self.store.records(request['project_id'])
        request_hash = _digest(request)
        old = next((r for r in records if r['request_id'] == request['request_id']), None)
        if old is not None:
            require(old['request_sha256'] == request_hash, 'capture request ID conflict')
            return {'record': old, 'reused': True, 'capture_performed': False, 'reason': 'EXACT_RECEIPT'}
        now = self.clock()
        require(now.tzinfo is not None, 'capture clock must be aware')
        same = [r for r in records if r['status'] == 'success' and r['method'] == 'web'
                and r['page_id'] == request['page_id'] and r['source_sha256'] == request['source_sha256']
                and r['build_sha256'] == request['build_sha256'] and r['runtime_verified'] is True]
        same.sort(key=lambda r: (r['observed_at'], r['request_id']), reverse=True)
        latest_page = next((r for r in sorted(records, key=lambda r: _time(r['observed_at']), reverse=True) if r['page_id'] == request['page_id']), None)
        if (same and latest_page and latest_page['status'] == 'success' and request['refresh_reason'] != 'manual'
                and _time(same[0]['expires_at']) > now):
            self.store.image(same[0])
            return {'record': same[0], 'reused': True, 'capture_performed': False, 'reason': 'UNCHANGED_UNEXPIRED'}
        record = {'schema_version': '1.0', 'request_id': request['request_id'], 'request_sha256': request_hash,
                  'project_id': request['project_id'], 'page_id': request['page_id'], 'domain': 'implementation',
                  'status': 'failed', 'reason': 'CAPTURE_FAILED', 'method': 'web', 'captured_at': None,
                  'observed_at': now.isoformat(), 'source_sha256': request['source_sha256'],
                  'build_sha256': request['build_sha256'], 'source_proof_sha256': request['source_proof_sha256'],
                  'runtime_verified': False, 'artifact_ref': None, 'artifact_sha256': None, 'media_type': None,
                  'width': None, 'height': None, 'expires_at': (now + timedelta(seconds=request['expires_after_seconds'])).isoformat(),
                  'provenance': {'kind': 'web_capture', 'report_sha256': None, 'reported_at': None, 'historical_candidate': None}}
        png = None
        from .capture_browser import capture_web, CaptureBrowserError
        try:
            result = (self.capture or capture_web)(request, executable_path=executable_path)
            _assert_binding(self.projects.get_project(request['project_id']), request)
            png = result['png']; info = png_info(png)
            require(result.get('blocked_count') == 0 and info['width'] == request['viewport']['width']
                    and info['height'] == request['viewport']['height'], 'capture image viewport or resources mismatch')
            captured = self.clock(); require(captured.tzinfo is not None, 'capture clock must be aware')
            sha = hashlib.sha256(png).hexdigest()
            record.update(status='success', reason=None, captured_at=captured.isoformat(), observed_at=captured.isoformat(),
                          expires_at=(captured + timedelta(seconds=request['expires_after_seconds'])).isoformat(),
                          runtime_verified=result['runtime_verified'] is True, artifact_ref=f'data/previews/images/{request["project_id"]}/{sha}.png',
                          artifact_sha256=sha, **info)
        except CaptureBrowserError as error:
            record['reason'] = error.code
        except RecordError:
            record['reason'] = 'CAPTURE_VERSION_OR_IMAGE_REJECTED'
        except ServiceError:
            record['reason'] = 'CAPTURE_SOURCE_UNAVAILABLE'
        if record['status'] != 'success':
            png = None
        saved = self.store.commit(record, png)
        return {'record': saved, 'reused': False, 'capture_performed': True, 'reason': saved['reason']}

    def import_existing(self, value, policy):
        record = validate_capture_record(value); policy = validate_capture_policy(policy)
        require(record['method'] == 'existing_desktop' and record['status'] == 'success'
                and record['runtime_verified'] is False and record['provenance']['report_sha256'] is not None
                and record['provenance']['historical_candidate'] is not None, 'existing desktop evidence required')
        require(record['project_id'] == policy['project_id'] and record['artifact_ref'] in policy['allowed_read_paths'],
                'existing preview read not permitted')
        require(_read_allowed(self.projects.get_project(record['project_id'])), 'existing preview project unavailable')
        data = read_preview_artifact(self.projects.root, record['artifact_ref'], expected_sha256=record['artifact_sha256'])
        return self.store.commit(record, data)


def capture_catalog(project, store, *, now=None):
    now = now or datetime.now(timezone.utc)
    pid = project['project_id']
    base = {'project_id': pid, 'classification': 'unavailable', 'reason': 'PAGE_INVENTORY_UNKNOWN',
            'previews': [], 'latest_attempt': None, 'capture_performed': False, 'execution_allowed': False}
    if not _read_allowed(project):
        return {**base, 'reason': 'PROJECT_READ_UNAVAILABLE'}
    rows = sorted(store.records(pid), key=lambda r: (_time(r['observed_at']), r['request_id']), reverse=True)
    base['latest_attempt'] = copy.deepcopy(rows[0]) if rows else None
    field = _field(project, 'has_ui')
    if field and field.get('value') is False:
        return {**base, 'classification': 'not_applicable', 'reason': 'SOURCE_HAS_UI_FALSE', 'latest_attempt': None}
    value = _inventory(project)
    if value:
        domain = next(d for d in value['domains'] if d['domain'] == 'implementation')
        if domain['completeness'] == 'complete' and domain['known_page_count'] == 0:
            return {**base, 'classification': 'not_implemented', 'reason': 'DECLARED_IMPLEMENTATION_EMPTY'}
    latest_by_page = {}
    for row in rows:
        latest_by_page.setdefault(row['page_id'], row)
    seen = set()
    for row in rows:
        if row['status'] != 'success' or row['page_id'] in seen:
            continue
        seen.add(row['page_id']); selected = copy.deepcopy(row); page = _page(project, row['page_id'])
        if (row['method'] == 'existing_desktop' and row['provenance']['historical_candidate'] is not None
                or latest_by_page[row['page_id']]['status'] == 'failed'
                or page is not None and (row['source_sha256'] is not None and page['source_sha256'] is not None
                    and row['source_sha256'] != page['source_sha256']
                    or row['build_sha256'] is not None and page['build_sha256'] is not None
                    and row['build_sha256'] != page['build_sha256'])):
            state = 'historical'
        elif (page is None or row['source_sha256'] is None or page['source_sha256'] is None
                or row['build_sha256'] != page['build_sha256'] or not row['runtime_verified']):
            state = 'unknown_version'
        elif _time(row['expires_at']) <= now:
            state = 'expired'
        else:
            state = 'current'
        selected.update(version_state=state, image_url=f'/api/projects/{pid}/captures/{row["request_id"]}/image')
        base['previews'].append(selected)
        if len(base['previews']) == 3:
            break
    if base['previews']:
        base.update(classification='available', reason=None)
    elif value:
        base['reason'] = 'NO_CAPTURE_AVAILABLE'
    return base


def read_capture_image(projects, project_id, request_id):
    store = CaptureStore(projects.root)
    before = capture_catalog(projects.get_project(project_id), store)
    selected = next((r for r in before['previews'] if r['request_id'] == request_id), None)
    if selected is None:
        raise ServiceError('CAPTURE_IMAGE_UNAVAILABLE', status=404)
    try:
        record = {k: v for k, v in selected.items() if k not in {'version_state', 'image_url'}}
        data = store.image(record)
    except RecordError:
        raise ServiceError('CAPTURE_IMAGE_REJECTED', status=409) from None
    after = capture_catalog(projects.get_project(project_id), store)
    if before != after:
        raise ServiceError('CAPTURE_BINDING_CHANGED', status=409)
    return ArtifactResponse(data, 'image/png', request_id+'.png', 'inline', selected['artifact_sha256'])
