"""Read-only graph/list projection of the existing authorized ProjectService DTO.

No resolver, source collector, native task reader or parallel data store.
"""
from __future__ import annotations
import copy
from collections import deque
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


def _public_field_selector(value):
    """Only an exact, bounded logical key/index selector is public provenance.

    This exception belongs to selector.path, never a general filesystem path key.
    Validation grants no source access and does not resolve any supplied value.
    """
    from .connection_records import validate_selector
    if type(value) is not dict or set(value) != {'path'}: return False
    try: validate_selector(value)
    except (ValueError, TypeError): return False
    return all(type(part) is int or (part not in {'.', '..'} and '/' not in part and '\\' not in part
               and not any(ord(c) < 32 or ord(c) == 127 for c in part)) for part in value['path'])


def safe(value):
    """Discard private path text without altering graph object identity fields."""
    if type(value) is str:
        return None if _private_path(value) else value
    if type(value) is list:
        return [safe(v) for v in value]
    if type(value) is dict:
        result = {k: (copy.deepcopy(v) if k == 'selector' and _public_field_selector(v) else safe(v)) for k, v in value.items()
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
        return _impact_graph(project, version, objects, projected_at=fresh.get('projected_at'))
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
                # Only explicit same-scope registered candidate bindings. A
                # filename, evidence presence or layout never creates an action.
                data['action_candidates'] = [
                    {k: candidate[k] for k in ('id', 'revision', 'content_hash', 'classification')}
                    for candidate in design.get('references', [])
                    if candidate.get('kind') == 'candidate' and candidate.get('scope') == item.get('scope')
                    and candidate.get('classification') == item.get('classification')
                    and item.get('delivery', {}).get('kind') == 'registered_artifact'
                    and item.get('sha256') is not None
                    and any(binding.get('artifact_id') == ident and binding.get('sha256') == item['sha256']
                            for binding in candidate.get('artifact_bindings', []))]
            obj(kind, ident, ident, revision, data, src, identity='candidate')
    _management_relations(dto, root, objects, relations)
    return _impact_graph(project, version, objects, relations, projected_at=fresh.get('projected_at'))


def _impact_graph(project, version, objects, relations=(), *, projected_at=None):
    # Validate and detach existing records first. Only the final graph hash is
    # published; derived data never includes that hash or the projection clock.
    base = graph(project, version, objects, relations, projected_at=projected_at)
    impacts = _impact_projection(base['objects'], base['relations'])
    for item in base['objects']:
        item['data']['impact'] = impacts[(item['id'], item['revision'])]
    return graph(project, version, base['objects'], base['relations'], projected_at=projected_at)


def _impact_projection(objects, relations):
    """One bounded IO algorithm; the client renders these published paths only.

    These are consequences of declarations in this snapshot, not observation of
    changed files, real consumers outside it, or permission to replay anything.
    """
    key = lambda v: (v['id'], v['revision'])
    ref = lambda v: dict(id=v['id'], revision=v['revision'])
    by_key = {key(v): v for v in objects}
    outgoing = {}; incident = set(); fact_incident = set(); omitted = set()
    def versioned(v):
        source = v['source']
        return (v['revision'] is not None and source['version'] is not None and source['sha256'] is not None
                and source['applicability'] in {'current', 'historical'})
    for edge in relations:
        if edge['type'] not in {'produces', 'consumes', 'derived_from'}: continue
        a, b = key(edge['from']), key(edge['to'])
        involved = (by_key[a], edge, by_key[b])
        candidate = any(v['identity'] == 'candidate' for v in involved)
        if not all(versioned(v) for v in involved) or not candidate and any(v['source']['origin'] != 'source_fact' for v in involved):
            omitted.update((a,b)); continue
        if edge['type'] in {'consumes','derived_from'}: a,b = b,a
        incident.update((a,b))
        if not candidate: fact_incident.update((a,b))
        step = dict(relation=ref(edge), type=edge['type'], **{'from': ref(by_key[a]), 'to': ref(by_key[b])},
                    evidence=copy.deepcopy(edge['evidence']), source=copy.deepcopy(edge['source']))
        outgoing.setdefault(a, []).append((b,candidate,step))
    result = {}; work_left = 100000; output_left = 4096
    for subject in objects:
        origin = key(subject); paths = {'fact': [], 'candidate': []}; limited = False; has_omitted = origin in omitted
        pending = deque([(origin, subject['identity'] == 'candidate', [])]); seen = {(origin,False),(origin,True)}
        steps = 0
        while pending:
            node, candidate, path = pending.popleft()
            has_omitted = has_omitted or node in omitted
            links = outgoing.get(node, ())
            if links and (len(path) >= 32 or steps >= 2048 or work_left <= 0 or output_left <= 0 or len(paths['fact']) + len(paths['candidate']) >= 128):
                limited = True; continue
            for target, edge_candidate, step in links:
                if steps >= 2048 or work_left <= 0 or output_left <= 0 or len(paths['fact']) + len(paths['candidate']) >= 128:
                    limited = True; break
                steps += 1; work_left -= 1
                next_candidate = candidate or edge_candidate
                state = (target,next_candidate)
                if state in seen: continue
                seen.add(state); route = path + [step]
                paths['candidate' if next_candidate else 'fact'].append(dict(target=ref(by_key[target]), path=route))
                output_left -= 1
                pending.append((target,next_candidate,route))
        gaps = []
        if origin not in incident: gaps.append('NO_EXPLICIT_VERSIONED_IO')
        if origin not in fact_incident: gaps.append('NO_PUBLISHED_SOURCE_FACT_IO')
        if has_omitted: gaps.append('UNQUALIFIED_IO_OMITTED')
        if limited: gaps.append('TRAVERSAL_LIMIT')
        result[origin] = dict(schema_version='graph-impact/1',subject=ref(subject),
            state='partial' if limited or has_omitted else 'available' if origin in incident else 'unknown',
            fact_paths=paths['fact'],candidate_paths=paths['candidate'],gaps=gaps,traversal_complete=not limited and not has_omitted,
            scope='published_exact_version_io_only',rerun=dict(available=False,reason='NO_REPLAY_AUTHORITY_OR_EFFECT_RECONCILIATION'))
    return result


def _management_relations(dto, root, objects, relations):
    """Project only this authorized DTO's overlay, never fetch other projects.

    Accepted management is still an overlay, not an observed source/run fact.
    Cross-project Project nodes are registry references with unknown business data.
    """
    from .management_store import validate_overlay
    state = dto.get('management') or {}
    overlay = state.get('overlay')
    if not state.get('available') or overlay is None: return
    try: validate_overlay(overlay, dto['project_id'])
    except (ValueError, TypeError, KeyError): need(False)
    project = dto['project_id']; configured = overlay.get('relations') or {}
    items = copy.deepcopy((configured.get('typed') or {}).get('items', []))
    local_version = digest(configured)
    # The original three project relations retain their management meaning.
    for kind, targets in [('managed_by', [configured['belongs_to']] if configured.get('belongs_to') else []),
                          ('related_to', configured.get('related_to', []))]:
        for target in targets:
            items.append({'id': 'legacy-' + digest([project, kind, target]), 'revision': local_version,
                'type': kind, 'from': {'project_id': project, 'type': 'Project', 'native_id': project, 'revision': root['revision']},
                'to': {'project_id': target, 'type': 'Project', 'native_id': target, 'revision': None},
                'state': 'accepted', 'origin': 'explicit', 'created_at': None, 'run': None,
                'evidence': [{'ref': 'management:legacy:' + kind, 'sha256': local_version}]})
    gaps = [{'id': 'legacy-' + digest([project, 'depends_on', target]), 'reason': 'Project dependency is management only; no real Task endpoints are published.'}
            for target in configured.get('depends_on', [])]
    options = {v['id']: v for v in state.get('project_options', [])}
    lookup = {(o['project_id'], o['type'], o['native_id'], o['revision']): o for o in objects}

    def endpoint(ref):
        key = tuple(ref[k] for k in ('project_id', 'type', 'native_id', 'revision'))
        if key in lookup: return lookup[key]
        target = ref['project_id']
        if target == project or target not in options or ref['type'] != 'Project' or ref['native_id'] != target or ref['revision'] is not None:
            return None
        value = {'id': stable_id(project, 'Project', 'registered:' + target), 'project_id': project,
                 'type': 'Project', 'native_id': 'registered:' + target, 'revision': None,
                 'label': safe(options[target].get('name')) or target, 'identity': 'fact',
                 'source': {'ref': 'registry:' + target, 'version': None, 'sha256': None, 'fact_time': None,
                            'collected_at': None, 'origin': 'registry', 'applicability': 'unknown',
                            'unknown_reason': 'Authorized registered reference only; target business and version are not read.'},
                 'data': {'reference_project_id': target, 'scope': 'registered_reference_only', 'business_state': {'state': 'unknown'}},
                 'explanations': explanations()}
        lookup[key] = value; return value

    for item in items:
        evidence = [proof['ref'] for proof in item['evidence']]
        if any(_private_path(proof) for proof in evidence):
            gaps.append({'id': item['id'], 'reason': _PRIVACY_REASON}); continue
        a, b = endpoint(item['from']), endpoint(item['to'])
        run = endpoint(item['run']) if item['run'] is not None else None
        if a is None or b is None or item['run'] is not None and (run is None or run['type'] != 'Run' or run['identity'] != 'fact'):
            gaps.append({'id': item['id'], 'reason': 'Exact authorized endpoint/version or real published run is unavailable.'}); continue
        if item['state'] == 'accepted' and item['type'] in {'depends_on', 'blocks'}:
            # Source revisions may conflict with a previously accepted overlay.
            # Preserve the overlay/audit, but never publish a cyclic task DAG.
            links = {}
            for existing in relations:
                if existing['type'] not in {'depends_on', 'blocks'} or not (existing['identity'] == 'fact' or existing.get('management', {}).get('state') == 'accepted'): continue
                x, y = ((existing[k]['id'], existing[k]['revision']) for k in ('from', 'to'))
                if existing['type'] == 'blocks': x, y = y, x
                links.setdefault(x, []).append(y)
            x, y = ((value['id'], value['revision']) for value in (a, b))
            if item['type'] == 'blocks': x, y = y, x
            pending = [y]; visited = set()
            while pending:
                node = pending.pop()
                if node in visited: continue
                visited.add(node); pending.extend(links.get(node, []))
            if x in visited:
                gaps.append({'id': item['id'], 'reason': 'Accepted overlay conflicts with the published source task DAG; dependency not projected.'}); continue
        for value in (a, b):
            if value not in objects: objects.append(value)
        native = 'management:' + item['id']; version = digest(item)
        relations.append({'id': stable_id(project, 'Relation', native), 'project_id': project, 'native_id': native,
            'revision': str(item['revision']), 'type': item['type'],
            'from': {'id': a['id'], 'revision': a['revision']}, 'to': {'id': b['id'], 'revision': b['revision']},
            'identity': 'candidate', 'management': {'state': item['state'], 'origin': item['origin'], 'item_sha256': version},
            'source': {'ref': 'management:' + item['id'], 'version': version, 'sha256': version,
                       'fact_time': item['created_at'], 'collected_at': None, 'origin': 'hub_management', 'applicability': 'current',
                       'unknown_reason': 'Management confirmation does not establish source facts or execution authority.'},
            'evidence': evidence, 'run_id': run['id'] if run else None})
    root['data']['relation_gaps'] = gaps


class GraphService:
    def __init__(self, projects):
        self.projects = projects

    def get_project(self, project_id, *, design_snapshot=None):
        dto = self.projects.get_project(project_id, design_snapshot=design_snapshot)
        need(dto['project_id'] == project_id)
        return project_graph(dto)
