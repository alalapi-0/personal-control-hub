"""Pure PCH refresh-domain projection into graph/1.0.

project_pch_domain(history, dto) accepts a COMPLETE RefreshLedger.history()
snapshot already verified by its owner, plus an authorized ProjectService PCH
DTO at exactly that head. It checks snapshot/DTO anchors, not provenance of the
caller. Serialized inputs are not read permission or independent ledger proof.
No I/O, refresh, clock, event creation or normal execution is performed here.

Malformed, filtered or inconsistent inputs raise PCH_DOMAIN_SNAPSHOT_INVALID
(400); a denied DTO raises PCH_DOMAIN_READ_DENIED (403). Inputs stay detached.
Content revisions omit collection/mtime/head churn; observation anchors retain
the exact result hash/reference and ledger head. Only published page inventory
creates page Artifact records; those records never assert artifact availability.
The latest authority-matched Run is the current attempt outcome, even when the
source failed; FileRef/Artifact currentness still requires fresh source content.
"""
from __future__ import annotations

import copy
import re
from .connection_records import authority_matches, validate_result
from .connection_refresh import SCHEMA_VERSION, GENESIS_HASH
from .graph_records import graph, stable_id, SHA
from .graph_service import project_graph, explanations, safe, _impact_projection
from .page_inventory import validate_page_inventory
from .service_contract import ServiceError
from .workbench_store import canonical, digest

PROJECT = 'personal-control-hub'


def _need(ok):
    if not ok:
        raise ServiceError('PCH_DOMAIN_SNAPSHOT_INVALID', status=400)


def _snapshot(history):
    _need(type(history) is dict and set(history) == {
        'schema_version', 'head', 'requests', 'observation_requests', 'events', 'results', 'observation_results'})
    _need(history['schema_version'] == SCHEMA_VERSION)
    for key, limit in (('requests', 1024), ('observation_requests', 1024),
                       ('events', 8192), ('results', 4096), ('observation_results', 4096)):
        _need(type(history[key]) is list and len(history[key]) <= limit)
    _need(len(canonical(history)) <= 16 * 1024 * 1024)
    head = history['head']
    _need(type(head) is dict and set(head) == {'sequence', 'hash'}
          and type(head['sequence']) is int and head['sequence'] >= 0
          and type(head['hash']) is str and SHA.fullmatch(head['hash']))
    requests = {}
    for r in history['requests'] + history['observation_requests']:
        _need(type(r) is dict and r['request_id'] not in requests)
        _need(type(r['project_ids']) is list and len(set(r['project_ids'])) == len(r['project_ids'])
              and r['authority_hash'] == digest(r['authority']) and r['status'] in {'OPEN', 'FINISHED'})
        requests[r['request_id']] = r
    events = {}; previous = GENESIS_HASH
    for seq, event in enumerate(history['events'], 1):
        _need(type(event) is dict and set(event) == {'sequence', 'event_id', 'event_type', 'request_id',
            'project_id', 'created_at', 'payload', 'previous_hash', 'event_hash'})
        _need(event['sequence'] == seq and event['previous_hash'] == previous and event['request_id'] in requests)
        core = {k: event[k] for k in ('sequence', 'event_id', 'event_type', 'request_id', 'project_id', 'created_at', 'previous_hash')}
        core.update(schema_version=SCHEMA_VERSION, payload_hash=digest(event['payload']))
        _need(digest(core) == event['event_hash'])
        _need(event['event_type'] in {'REQUEST_BEGUN', 'PROJECT_RESULT', 'REQUEST_FINISHED'})
        previous = event['event_hash']; events[seq] = event
    _need(head == {'sequence': len(events), 'hash': previous})
    results = {}; seen = set()
    for row in history['results'] + history['observation_results']:
        _need(type(row) is dict and set(row) == {'request_id', 'project_id', 'result_ref', 'result_hash',
            'success', 'event_sequence', 'result', 'attempt_metadata', 'observation_metadata'})
        key = (row['request_id'], row['project_id']); _need(key not in seen); seen.add(key)
        result = validate_result(row['result']); request = requests[row['request_id']]
        _need(result['project_id'] == row['project_id'] and row['project_id'] in request['project_ids'])
        _need(type(row['success']) is bool and row['success'] == result['success'] and digest(result) == row['result_hash'])
        _need(row['result_ref'] == 'refresh-result:' + row['request_id'] + ':' + row['project_id'] + ':' + row['result_hash'])
        event = events[row['event_sequence']]; payload = event['payload']
        _need(event['event_type'] == 'PROJECT_RESULT' and event['request_id'] == row['request_id']
              and event['project_id'] == row['project_id'])
        _need(all(payload[k] == row[k] for k in ('request_id', 'project_id', 'result_ref', 'result_hash', 'success'))
              and payload['observed_at'] == result['observed_at'])
        _need(authority_matches(result['authority'], request['authority'], [row['project_id']]))
        begun = events[request['begin_sequence']]
        _need(row['attempt_metadata'] == begun['payload'].get('attempt_metadata')
              and row['observation_metadata'] == payload.get('observation_metadata'))
        is_observation = bool(row['attempt_metadata'] and row['attempt_metadata'].get('role') == 'observation')
        _need(is_observation == (row in history['observation_results']))
        results[row['event_sequence']] = row
    _need(set(results) == {seq for seq, e in events.items() if e['event_type'] == 'PROJECT_RESULT'})
    for r in requests.values():
        begun = events[r['begin_sequence']]
        _need(begun['event_type'] == 'REQUEST_BEGUN' and begun['request_id'] == r['request_id'])
        _need(all(begun['payload'][k] == r[k] for k in ('request_id', 'project_ids', 'authority', 'authority_hash', 'started_at')))
        metadata = begun['payload'].get('attempt_metadata')
        _need(bool(metadata and metadata.get('role') == 'observation') == (r in history['observation_requests']))
        completed = sorted(pid for rid, pid in seen if rid == r['request_id'])
        _need(r['completed_project_ids'] == completed and r['remaining_project_ids'] == sorted(set(r['project_ids']) - set(completed)))
        if r['status'] == 'FINISHED':
            ended = events[r['finish_sequence']]
            _need(not r['remaining_project_ids'] and ended['event_type'] == 'REQUEST_FINISHED'
                  and ended['request_id'] == r['request_id'] and ended['payload']['finished_at'] == r['finished_at'])
        else:
            _need(r['finish_sequence'] is None and r['finished_at'] is None)
    return requests


def _project(history, dto):
    requests = _snapshot(history)
    _need(type(dto) is dict and dto['project_id'] == PROJECT)
    declared, freshness = dto['declared'], dto['freshness']
    if (declared.get('connection_read_allowed') is not True or declared.get('access_profile') == 'no_current_goal_access'
            or declared.get('current_state_status') == 'removed_local' or freshness.get('local_presence') == 'removed_local'):
        raise ServiceError('PCH_DOMAIN_READ_DENIED', status=403)
    _need(dto['provenance']['ledger_head'] == history['head'])
    rows = sorted((r for r in history['results'] if r['project_id'] == PROJECT), key=lambda r: r['event_sequence'])
    latest = rows[-1] if rows else None
    successful = [r for r in rows if r['success']]
    last_success = successful[-1] if successful else None
    _need(dto['source']['source_record'] == (latest['result'] if latest else None))
    _need(dto['provenance']['latest_result_hash'] == (latest['result_hash'] if latest else None))
    _need(dto['provenance']['latest_authority'] == (latest['result']['authority'] if latest else None))
    _need(dto['operational']['last_success'] == (last_success['result'] if last_success else None))
    if latest:
        _need(dto['provenance']['project_binding']['project_id'] == PROJECT)
        _need(dto['provenance']['project_binding']['registration_sha256'] == latest['result']['authority']['registration_bindings'][PROJECT])
    _need(len(canonical(dto)) <= 16 * 1024 * 1024)
    base = project_graph(dto); objects = {(o['id'], o['revision']): o for o in base['objects']}
    relations = {(e['id'], e['revision']): e for e in base['relations']}; semantic = []
    current = freshness['state'] == 'fresh' and not freshness['authority_drift']
    # A committed latest request outcome is a current attempt fact even when
    # its source failed. This never makes source content current or available.
    current_attempt = freshness['authority_drift'] is False

    def observation(row):
        return {'head': copy.deepcopy(history['head']), 'result_ref': row['result_ref'], 'result_sha256': row['result_hash']}

    def source(row, revision, sha, ref, *, attempt=False):
        return {'ref': ref, 'version': revision, 'sha256': sha, 'fact_time': None,
                'collected_at': row['result']['observed_at'], 'origin': 'source_fact',
                'applicability': 'current' if row is latest and (current_attempt if attempt else current) else 'historical',
                'unknown_reason': 'Observation time is not a source fact timestamp.'}

    def obj(kind, identity, label, revision, data, src):
        native = 'pch-' + digest(identity)
        value = {'id': stable_id(PROJECT, kind, native), 'project_id': PROJECT, 'type': kind,
            'native_id': native, 'revision': revision, 'label': safe(label) or '来源标签不可公开',
            'identity': 'fact', 'source': src, 'data': data, 'explanations': explanations()}
        objects[(value['id'], revision)] = value
        return value

    def edge(kind, left, right, row, src, evidence):
        native = 'pch-' + digest([kind, left['id'], right['id']])
        revision = digest([kind, left['revision'], right['revision']])
        value = {'id': stable_id(PROJECT, 'Relation', native), 'project_id': PROJECT,
            'native_id': native, 'revision': revision, 'type': kind,
            'from': {'id': left['id'], 'revision': left['revision']}, 'to': {'id': right['id'], 'revision': right['revision']},
            'evidence': [row['result_ref'], 'ledger-head:' + str(history['head']['sequence']) + ':' + history['head']['hash'], *evidence],
            'source': copy.deepcopy(src), 'identity': 'fact', 'run_id': None}
        relations[(value['id'], revision)] = value

    for request in history['requests']:
        if PROJECT not in request['project_ids']: continue
        row = next((r for r in rows if r['request_id'] == request['request_id']), None)
        if row is None: continue  # No committed PCH observation yet; do not invent a result.
        result = row['result']; errors = [e['code'] if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', e['code'])
                                         else 'unclassified_error' for e in result['errors']]
        versions = {'declaration_sha256': result['declaration']['sha256'] if result['declaration'] else None,
            'schema_sha256': result['authority']['schema_hash'], 'adapter_version': result['authority']['adapter_version'],
            'binding_sha256': result['binding_hash'],
            'rules_sha256': digest({k: {a: v[a] for a in ('source_ref', 'selector', 'value_map')}
                                    for k, v in result['field_provenance'].items()})}
        revision = digest({'request_id': request['request_id'], 'status': request['status'],
            'success': row['success'], 'error_codes': errors, 'inputs': versions,
            'sources': [(s['id'], s['sha256']) for s in result['sources']]})
        run = obj('Run', ['refresh-request', request['request_id']], '来源刷新请求', revision,
            {'kind': 'refresh_request', 'request_id': request['request_id'], 'status': request['status'],
             'success': row['success'], 'error_codes': errors, 'version_inputs': versions,
             'observation': observation(row), 'native_run_id': None,
             'native_run_unknown_reason': 'Refresh request identity is not a native execution identity.',
             'execution_authority': False, 'actor': {'value': None, 'unknown_reason': 'Refresh actor is not published.'}},
            source(row, revision, row['result_hash'], 'refresh-request:' + request['request_id'], attempt=True))
        semantic.append([run['id'], revision])
        if not row['success']: continue
        files = {}
        for item in result['sources']:
            inputs = {**versions, 'source_sha256': item['sha256']}; revision = digest(inputs)
            src = source(row, revision, item['sha256'], 'source-ref:' + item['id'])
            file = obj('FileRef', ['source-ref', item['id']], '登记来源 ' + item['id'], revision,
                {'source_ref': item['id'], 'format': item['format'], 'role': item['role'],
                 'version_inputs': inputs, 'observation': observation(row), 'execution_authority': False}, src)
            files[item['id']] = file; semantic.append([file['id'], revision])
            edge('consumes', run, file, row, src, ['verified_refresh_source_sha256'])
        cell = (result.get('extensions') or {}).get('fact', {}).get('page_inventory')
        if not cell or cell['value'] is None: continue
        inventory = validate_page_inventory(cell['value'], project_id=PROJECT)
        proof = cell['proof']; file = files.get(proof['source_ref'])
        _need(file is not None and file['data']['version_inputs']['source_sha256'] == proof['sha256'])
        for domain in inventory['domains']:
            for page in domain['pages']:
                revision = digest({'domain': domain['domain'], 'page': page, 'version_inputs': versions})
                src = source(row, revision, page['source_sha256'], 'page-inventory:' + page['id'])
                artifact = {k: {'value': None, 'unknown_reason': 'Page metadata does not publish ' + k + '.'}
                            for k in ('input_version', 'code_version', 'sha256', 'purpose', 'host', 'file_ref')}
                if page['source_sha256']:
                    artifact['input_version'] = {'value': page['source_sha256'], 'unknown_reason': None}
                if page['build_sha256']:
                    artifact['code_version'] = {'value': page['build_sha256'], 'unknown_reason': None}
                value = obj('Artifact', ['published-page', domain['domain'], page['id']], page['label'], revision,
                    {'kind': 'declared_page', 'domain': domain['domain'], 'page_id': page['id'], 'artifact': artifact,
                     'availability': {'state': 'unknown', 'reason': 'Page publication is not an artifact byte read.'},
                     'version_inputs': versions, 'observation': observation(row), 'execution_authority': False}, src)
                semantic.append([value['id'], revision])
                edge('references', file, value, row, file['source'], ['page_inventory', 'source-sha256:' + proof['sha256']])
    version = digest({'base': base['source_version'], 'domain_revisions': semantic})
    projected = graph(PROJECT, version, list(objects.values()), list(relations.values()), base['layouts'], projected_at=base['projected_at'])
    # The base graph's impacts predate the domain records. Recompute every
    # object against the validated final graph before hashing derived data.
    impacts = _impact_projection(projected['objects'], projected['relations'])
    for item in projected['objects']:
        item['data']['impact'] = impacts[(item['id'], item['revision'])]
    projected = graph(PROJECT, version, projected['objects'], projected['relations'],
                      projected['layouts'], projected_at=projected['projected_at'])
    if 'Artifact' in projected['unknown_types']:
        projected['unknown_types']['Artifact'] = 'No explicitly evidenced source page or artifact is published in this snapshot.'
    return projected


def project_pch_domain(history, dto):
    """Return detached graph/1.0; see module input, privacy and error contract."""
    try:
        return _project(history, dto)
    except ServiceError as exc:
        if exc.code in {'PCH_DOMAIN_SNAPSHOT_INVALID', 'PCH_DOMAIN_READ_DENIED'}: raise
        raise ServiceError('PCH_DOMAIN_SNAPSHOT_INVALID', status=400) from None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        raise ServiceError('PCH_DOMAIN_SNAPSHOT_INVALID', status=400) from None
