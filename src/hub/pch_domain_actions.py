"""Self-only PCH reads and explicit refresh; no independent execution authority."""
from __future__ import annotations
import copy
import re
from pathlib import Path
from functools import lru_cache
import yaml
from .service_contract import ServiceError
from .workbench_store import digest
from .graph_service import safe

PROJECT = 'personal-control-hub'
PREFIX = '/api/projects/personal-control-hub/domain'
DECLARATION = {'schema_version': '1.0', 'project_id': PROJECT, 'route': 'route:personal-control-hub',
    'operations': {'catalog': 'read_only', 'request_result': 'read_only', 'refresh': 'explicit_owner_self_only',
                   'artifacts': 'existing_registered_read_receipt', 'tasks': 'existing_explicit_task_form'}}

@lru_cache(maxsize=1)
def declaration():
    # Installed source configuration, never a path provided by an HTTP client.
    value = yaml.safe_load((Path(__file__).resolve().parents[2] / 'data/connections/pch_domain_actions.yaml').read_bytes())
    if value != DECLARATION:
        raise ServiceError('PCH_DOMAIN_CONFIGURATION_INVALID', status=503)
    return copy.deepcopy(value)


def snapshot(projects, *, design_snapshot=None, dto=None):
    """One verified history + authorized same-head DTO, without source collection."""
    from .pch_domain_projection import project_pch_domain
    dto = dto if dto is not None else projects.get_project(PROJECT, design_snapshot=design_snapshot)
    if (dto['declared'].get('connection_read_allowed') is not True
            or dto['declared'].get('access_profile') == 'no_current_goal_access'
            or dto['freshness'].get('local_presence') == 'removed_local'):
        raise ServiceError('PCH_DOMAIN_READ_DENIED', status=403)
    ledger = projects._read_ledger()
    history = ledger.history() if ledger is not None else {
        'schema_version': '2.0', 'head': {'sequence': 0, 'hash': '0' * 64},
        'requests': [], 'observation_requests': [], 'events': [], 'results': [], 'observation_results': []}
    head = dto['provenance']['ledger_head']
    # Unread DTOs publish null rather than a synthetic committed ledger head.
    if ledger is None and head is None:
        from .graph_service import project_graph
        history['head'] = None
        return dto, history, project_graph(dto)
    if head != history['head']:
        raise ServiceError('PCH_DOMAIN_HEAD_CHANGED', status=409, retryable=True)
    try:
        projected = project_pch_domain(history, dto)
    except ServiceError as exc:
        if exc.status == 403:
            raise
        raise ServiceError('PCH_DOMAIN_SNAPSHOT_CHANGED', status=409, retryable=True) from None
    if projects.ledger_head() != head:
        raise ServiceError('PCH_DOMAIN_HEAD_CHANGED', status=409, retryable=True)
    return dto, history, projected


class PCHDomainActions:
    def __init__(self, projects, actions, *, checkout_observation=None):
        self.projects, self.actions = projects, actions
        self.config = declaration()
        # Only an internally supplied bounded hash observation; not installed proof.
        self.checkout = {'state': 'unknown', 'reason': 'NO_TRUSTED_CHECKOUT_OBSERVATION'}
        if checkout_observation is not None:
            v = checkout_observation
            if (type(v) is not dict or set(v) != {'commit', 'dirty_sha256', 'observed_at'}
                or not re.fullmatch('[a-f0-9]{40}', v.get('commit', ''))
                or not re.fullmatch('[a-f0-9]{64}', v.get('dirty_sha256', ''))
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', v.get('observed_at', ''))):
                raise ServiceError('PCH_CHECKOUT_OBSERVATION_INVALID')
            self.checkout = {'state': 'observed', **copy.deepcopy(v), 'installed_program_proof': False}

    @staticmethod
    def command(value):
        if (type(value) is not dict or set(value) != {'request_id', 'project_ids', 'expected_head'}
            or value.get('project_ids') != [PROJECT]
            or not isinstance(value.get('request_id'), str)
            or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value['request_id'])
            or type(value.get('expected_head')) is not dict
            or set(value['expected_head']) != {'sequence', 'hash'}
            or type(value['expected_head']['sequence']) is not int or value['expected_head']['sequence'] < 0
            or not isinstance(value['expected_head']['hash'], str)
            or not re.fullmatch('[a-f0-9]{64}', value['expected_head']['hash'])):
            raise ServiceError('PCH_REFRESH_SCOPE_INVALID')
        return copy.deepcopy(value)

    @staticmethod
    def requests(history):
        result = []
        for request in history['requests']:
            if PROJECT not in request['project_ids']:
                continue
            rows = [r for r in history['results'] if r['request_id'] == request['request_id'] and r['project_id'] == PROJECT]
            result.append({'request_id': request['request_id'], 'status': request['status'],
                'self_only': request['project_ids'] == [PROJECT], 'results': [
                    {'success': r['success'], 'result_ref': r['result_ref'], 'result_sha256': r['result_hash']} for r in rows]})
        return result

    def catalog(self):
        # Keep the existing read gate ahead of any design-store observation.
        dto, history, graph = snapshot(self.projects)
        # Both producers use trusted DesignService observations. Duplicate reads
        # are bounded; a relevant design/head change rejects the assembled view.
        designs = getattr(self.actions, 'designs', None)
        design_snapshot, design_unavailable = None, False
        if designs is not None:
            try:
                design_snapshot = designs.snapshot()
            except ServiceError:
                design_unavailable = True
        if design_snapshot is not None:
            dto, history, graph = snapshot(self.projects, design_snapshot=design_snapshot)
        # Existing closed registry is authoritative for all bytes and receipts.
        artifact_gap = None
        if design_unavailable:
            artifacts, artifact_gap = None, 'ACTION_CATALOG_UNAVAILABLE'
        else:
            try:
                artifacts = self.actions.catalog(PROJECT)
            except ServiceError as exc:
                if exc.code not in {'ACTION_PROJECT_DENIED', 'ACTION_CATALOG_UNAVAILABLE'}:
                    raise
                artifacts, artifact_gap = None, exc.code
        if artifacts is not None and any(artifacts[key] != graph[key]
                for key in ('source_version', 'projection_version')):
            raise ServiceError('PCH_DOMAIN_DESIGN_CHANGED', status=409, retryable=True)
        if self.projects.ledger_head() != history['head']:
            raise ServiceError('PCH_DOMAIN_HEAD_CHANGED', status=409, retryable=True)
        # Revocation or source authority changes during a read cannot publish the old DTO.
        latest = self.projects.get_project(PROJECT, design_snapshot=design_snapshot)
        if (latest['declared'].get('connection_read_allowed') is not True
                or latest['declared'].get('access_profile') == 'no_current_goal_access'
                or latest['freshness'].get('local_presence') == 'removed_local'):
            raise ServiceError('PCH_DOMAIN_READ_DENIED', status=403)
        if (latest['provenance'] != dto['provenance'] or latest['declared'] != dto['declared']
                or latest['freshness']['authority_drift'] != dto['freshness']['authority_drift']):
            raise ServiceError('PCH_DOMAIN_HEAD_CHANGED', status=409, retryable=True)
        record = dto['source']['source_record']
        fact = ((record or {}).get('extensions') or {}).get('fact', {})
        pages = copy.deepcopy(fact.get('page_inventory'))
        resources = {}
        for name in ('hub.js', 'actions.mjs', 'graph.mjs', 'hub.css'):
            import hashlib
            resources[name] = hashlib.sha256((Path(__file__).parent / 'web' / name).read_bytes()).hexdigest()
        return {'schema_version': '1.0', 'project_id': PROJECT, 'route': self.config['route'],
            'head': copy.deepcopy(history['head']), 'refresh_head': copy.deepcopy(history['head']) or {'sequence': 0, 'hash': '0' * 64}, 'detail': safe(dto), 'graph': graph,
            'pages': safe(pages), 'requests': self.requests(history), 'artifacts': artifacts, 'artifact_gap': artifact_gap,
            'versions': {'checkout': copy.deepcopy(self.checkout),
                'source': {'result_sha256': dto['provenance']['latest_result_hash'],
                    'head': copy.deepcopy(history['head']), 'sources': safe((record or {}).get('sources', [])),
                    'version_inputs': safe(next((o['data']['version_inputs'] for o in reversed(graph['objects']) if o['type'] == 'FileRef' and o['source']['applicability'] == 'current'), None)), 'schema_sha256': dto['provenance'].get('schema_sha256'),
                    'authority_drift': dto['freshness']['authority_drift']},
                'web_resources': {'state': 'observed', 'sha256': resources, 'installed_program_proof': False},
                'runtime_manifest': safe(dto.get('operational', {}).get('runtime_manifest')) or
                    {'state': 'unknown', 'reason': 'NO_FROZEN_RUNTIME_OBSERVATION'}},
            'operations': copy.deepcopy(self.config['operations']), 'read_only': True,
            'native_execution_available': False}

    def request_result(self, command):
        command = self.command(command)
        _, history, _ = snapshot(self.projects)
        requests = [r for r in history['requests'] if r['request_id'] == command['request_id']]
        if not requests:
            return {'schema_version': '1.0', 'project_id': PROJECT, 'request_id': command['request_id'],
                    'status': 'NOT_FOUND', 'head': history['head'], 'command_sha256': digest(command)}
        request = requests[0]
        begun = next(e for e in history['events'] if e['request_id'] == command['request_id'] and e['event_type'] == 'REQUEST_BEGUN')
        if request['project_ids'] != [PROJECT] or command['expected_head'] != {'sequence': begun['sequence'] - 1, 'hash': begun['previous_hash']}:
            raise ServiceError('PCH_REFRESH_REQUEST_CONFLICT', status=409)
        return {'schema_version': '1.0', 'project_id': PROJECT, 'head': history['head'],
                'command_sha256': digest(command), **next(r for r in self.requests(history) if r['request_id'] == command['request_id'])}

    def refresh(self, command, *, authorize):
        command = self.command(command)
        authorize()
        # Exact finished request replay is read-only, even if source freshness changed.
        receipt = self.request_result(command)
        if receipt['status'] == 'FINISHED':
            try:
                catalog = self.catalog()
                authorize()
                if receipt['head'] != catalog['head']:
                    raise ServiceError('PCH_DOMAIN_HEAD_CHANGED', status=409, retryable=True)
                return {'schema_version': '1.0', 'project_id': PROJECT, 'command': command,
                        'request_result': receipt, 'catalog': catalog}
            except ServiceError as exc:
                raise ServiceError(exc.code, status=exc.status, retryable=True, outcome='UNKNOWN') from None
        # Current project eligibility before any source/content collection.
        self.projects.assert_action_project(PROJECT)
        authorize()
        self.projects.refresh(command)
        try:
            authorize()
            receipt, catalog = self.request_result(command), self.catalog()
            authorize()
            if receipt['head'] != catalog['head']:
                raise ServiceError('PCH_DOMAIN_HEAD_CHANGED', status=409, retryable=True)
            return {'schema_version': '1.0', 'project_id': PROJECT, 'command': command,
                    'request_result': receipt, 'catalog': catalog}
        except ServiceError as exc:
            raise ServiceError(exc.code, status=exc.status, retryable=True, outcome='UNKNOWN') from None
