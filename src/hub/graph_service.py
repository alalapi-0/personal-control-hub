"""Read-only graph/list projection of the existing authorized ProjectService DTO.

No resolver, source collector, native task reader or parallel data store.
"""
from __future__ import annotations
import copy
import re
from .graph_records import TYPES, EXPLANATIONS, stable_id, graph, need
from .workbench_store import digest


_HTTP_URL = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
_ABSOLUTE_PATH = re.compile(r"(?<![\w./])/[^\s<>\"'`()\[\]{};,!?]+")
_PRIVATE_FIELDS = frozenset({'path', 'root', 'cwd', 'location', 'messages', 'prompt', 'body', 'root_binding', 'entrypoints'})
_PRIVACY_REASON = 'Source text contains a private absolute filesystem reference.'


def _private_path(value):
    # Classify rooted Linux path syntax, not a list of private mount prefixes.
    # Web URLs are a separate syntax; preserve their ordinary documentation paths.
    public = _HTTP_URL.sub(lambda match: ' ' * len(match.group()), value)
    return value == '/' or _ABSOLUTE_PATH.search(public) is not None


def safe(value):
    """Discard private path text without altering graph object identity fields."""
    if type(value) is str:
        return None if _private_path(value) else value
    if type(value) is list:
        return [safe(v) for v in value]
    if type(value) is dict:
        result = {k: safe(v) for k, v in value.items()
                  if k not in _PRIVATE_FIELDS and not _private_path(k)}
        # A redacted fact cell is unknown, never a known null/partial value.
        if type(value.get('state')) is str and value['state'] in {'known', 'empty', 'not_applicable'} and 'value' in value and result.get('value') != value['value']:
            result.update(state='unknown', value=None, source=None, unknown_reason=_PRIVACY_REASON)
        elif any(type(v) is str and _private_path(v) for k, v in value.items() if k in result):
            result['unknown_reason'] = _PRIVACY_REASON
        return result
    return copy.deepcopy(value)


def explanations(value=None):
    result = {}
    for k in sorted(EXPLANATIONS):
        item = (value or {}).get(k)
        if not item or item.get('state') == 'unknown' or item.get('source') is None:
            reason = (item or {}).get('unknown_reason')
            result[k] = {'state': 'unknown', 'value': None, 'source': None,
                         'unknown_reason': (_PRIVACY_REASON if isinstance(reason, str) and _private_path(reason) else reason)
                         or 'No source explanation is published for this object.'}
        else:
            v = safe(item.get('value'))
            # Declared path fields are omitted from otherwise usable provenance.
            # Any private text in remaining source metadata invalidates this cell.
            visible_source = {key: val for key, val in item['source'].items() if key not in _PRIVATE_FIELDS}
            src = safe(visible_source)
            if v != item.get('value') or src != visible_source or not src:
                result[k] = {'state': 'unknown', 'value': None, 'source': None, 'unknown_reason': _PRIVACY_REASON}
            else:
                result[k] = {'state': item['state'], 'value': v, 'source': src, 'unknown_reason': None}
    return result


def project_graph(dto):
    """All I/O and eligibility remain with ProjectService.get_project."""
    project = dto['project_id']; declared = dto.get('declared', {}); fresh = dto.get('freshness', {})
    readable = (declared.get('connection_read_allowed') is True and declared.get('access_profile') != 'no_current_goal_access'
                and declared.get('current_state_status') != 'removed_local' and fresh.get('local_presence') != 'removed_local')
    current = readable and fresh.get('state') == 'fresh' and not fresh.get('authority_drift')
    provenance = dto.get('provenance', {}); binding = provenance.get('project_binding') or {}
    # Selected-project binding and actual source hashes only; global ledger head,
    # registry audit hash and projection clock are not source content versions.
    record = dto.get('source', {}).get('source_record') if current else None
    sources = [{'id': s['id'], 'sha256': s['sha256']} for s in (record or {}).get('sources', [])]
    version = digest({'project': project, 'binding': binding.get('registration_sha256'),
                      'schema': provenance.get('schema_sha256'), 'adapter': provenance.get('adapter_version'), 'sources': sources,
                      'freshness': fresh.get('state'), 'read_allowed': readable})
    origin_source = {'ref': 'registry:' + project, 'version': binding.get('registration_sha256'),
        'sha256': binding.get('registration_sha256'), 'fact_time': None, 'collected_at': None,
        'origin': 'registry', 'applicability': 'current', 'unknown_reason': 'Registry identity has no source fact timestamp.'}
    objects = []; relations = []
    def obj(kind, native, label, revision, data, source, *, identity='fact', exp=None):
        value = {'id': stable_id(project, kind, native), 'project_id': project, 'type': kind,
                 'native_id': native, 'revision': revision, 'label': safe(label) or 'Private label unavailable',
                 'identity': identity, 'source': copy.deepcopy(source), 'data': safe(data), 'explanations': explanations(exp)}
        objects.append(value); return value
    semantics = dto.get('semantics', {}) if current else {}
    separated = {k: safe(semantics.get(k)) for k in ('collection_health', 'fact_time', 'completeness', 'business_state', 'progress', 'availability', 'blockers', 'interpretations')}
    if not current:
        separated = {'business_state': {'state': 'unknown', 'reason': 'READ_DISABLED' if not readable else 'NO_CURRENT_SOURCE'},
                     'collection_health': {'read_allowed': readable, 'applicability': fresh.get('state', 'unknown')},
                     'availability': {'state': 'unknown'}, 'project_completion_inferred': False}
    root = obj('Project', project, dto.get('name') or project, binding.get('registration_sha256'), separated,
               origin_source, exp=semantics.get('explanations'))
    if not current:
        return graph(project, version, objects, projected_at=fresh.get('projected_at'))
    fact_source = {'ref': 'source:' + project, 'version': version, 'sha256': digest(sources) if sources else None,
        'fact_time': fresh.get('fact_updated_at'), 'collected_at': fresh.get('observed_at'),
        'origin': 'source_fact', 'applicability': 'current',
        'unknown_reason': None if fresh.get('fact_updated_at') else 'Source has no verified fact timestamp.'}
    def edge(native, kind, a, b, revision, source, evidence, *, identity='fact'):
        relations.append({'id': stable_id(project, 'Relation', native), 'project_id': project, 'native_id': native,
            'revision': revision, 'type': kind, 'from': {'id': a['id'], 'revision': a['revision']},
            'to': {'id': b['id'], 'revision': b['revision']}, 'evidence': evidence, 'source': copy.deepcopy(source),
            'identity': identity, 'run_id': None})
    task_fact = (record or {}).get('extensions', {}).get('fact', {}).get('task_plan')
    if task_fact and task_fact.get('value') is not None:
        from .business_tasks import validate_task_plan
        plan = validate_task_plan(task_fact['value']); need(plan['project_id'] == project)
        nodes = {project: root}; kind_map = {'stage': 'Phase', 'round': 'Round', 'leaf': 'Task'}
        for n in plan['nodes']:
            if n['kind'] == 'project': continue
            nodes[n['id']] = obj(kind_map[n['kind']], n['id'], plan['labels'][n['id']], plan['plan_version'],
                {'business_state': n['status'], 'progress': {'unit': plan['unit'], 'counting_basis': plan['counting_basis'], 'scope': 'source_declared_task', 'project_completion_inferred': False}}, fact_source)
        for n in plan['nodes']:
            if n['kind'] != 'project':
                edge(n['id'] + ':parent', 'part_of', nodes[n['id']], nodes[n['parent']], plan['plan_version'], fact_source,
                     ['task_plan.nodes.parent'])
                for dependency in n['dependencies']:
                    if nodes[n['id']]['type'] == nodes[dependency]['type'] == 'Task':
                        edge(n['id'] + ':depends:' + dependency, 'depends_on', nodes[n['id']], nodes[dependency],
                             plan['plan_version'], fact_source, ['task_plan.nodes.dependencies'])
    # Existing design facts retain their candidate/fixture identity. No raw event
    # messages, file locations, full paths or inferred native runs are projected.
    design = dto.get('design', {})
    if design.get('available'):
        for item in design.get('references', []):
            kind = {'artifact_ref': 'Artifact', 'review': 'Review', 'decision_event': 'Decision'}.get(item.get('kind'))
            if kind is None: continue
            ident = item.get('id')
            if not isinstance(ident, str): continue
            revision = str(item['revision']) if item.get('revision') is not None else item.get('sha256')
            src = {**fact_source, 'ref': 'design:' + ident, 'version': revision, 'sha256': item.get('content_hash') or item.get('sha256'),
                   'fact_time': item.get('created_at'), 'collected_at': None, 'origin': 'hub_management',
                   'unknown_reason': None if revision and item.get('created_at') else 'Design record has no declared revision or fact timestamp.'}
            data = {'engineering_result': 'unverified', 'availability': {'state': 'unknown', 'reason': 'Reference does not prove file availability.'}}
            if kind == 'Artifact':
                data['artifact'] = {k: {'value': None, 'unknown_reason': 'Artifact reference does not publish ' + k + '.'}
                                    for k in ('input_version', 'code_version', 'sha256', 'purpose', 'host', 'file_ref')}
                if item.get('sha256') is not None:
                    data['artifact']['sha256'] = {'value': item['sha256'], 'unknown_reason': None}
            obj(kind, ident, ident, revision, data, src, identity='candidate')
    return graph(project, version, objects, relations, projected_at=fresh.get('projected_at'))


class GraphService:
    def __init__(self, projects):
        self.projects = projects

    def get_project(self, project_id, *, design_snapshot=None):
        dto = self.projects.get_project(project_id, design_snapshot=design_snapshot)
        need(dto['project_id'] == project_id)
        return project_graph(dto)
