"""Strict, detached graph records. No source reads, writes or action authority."""
from __future__ import annotations
import copy
import math
import re
from datetime import datetime
from .service_contract import ServiceError
from .workbench_store import digest

TYPES = frozenset({'Project', 'Goal', 'Phase', 'Round', 'Task', 'Artifact', 'FileRef', 'Workflow', 'Run', 'Service', 'Tool', 'Decision', 'Review'})
RELATIONS = frozenset({'managed_by', 'part_of', 'depends_on', 'blocks', 'consumes', 'produces', 'derived_from', 'references', 'related_to'})
EXPLANATIONS = frozenset({'purpose', 'outputs', 'current_work', 'next_action', 'actor'})
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}')
# Source IDs remain bounded as before; internal typed relation tuples contain
# two such IDs plus fixed separators. Source task IDs cannot contain ':'.
RELATION_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,1023}')
SHA = re.compile(r'[0-9a-f]{64}')


def need(ok):
    if not ok:
        raise ServiceError('GRAPH_RECORD_INVALID', status=400)


def exact(v, fields):
    need(type(v) is dict and set(v) == set(fields))


def text(v, *, nullable=False):
    need(nullable and v is None or type(v) is str and 0 < len(v) <= 4096 and not any(ord(c) < 32 for c in v))


def stable_id(project, kind, native):
    need(type(project) is str and ID.fullmatch(project) is not None)
    need(kind in TYPES | {'Relation'} and type(native) is str)
    need((RELATION_ID if kind == 'Relation' else ID).fullmatch(native) is not None)
    return project + '/' + kind + '/' + native


def source(value):
    exact(value, {'ref', 'version', 'sha256', 'fact_time', 'collected_at', 'origin', 'applicability', 'unknown_reason'})
    for k in ('ref', 'version', 'unknown_reason'): text(value[k], nullable=True)
    need(value['sha256'] is None or type(value['sha256']) is str and SHA.fullmatch(value['sha256']))
    need(value['origin'] in {'source_fact', 'registry', 'hub_management', 'hub_interpretation'})
    need(value['applicability'] in {'current', 'historical', 'unknown', 'unavailable'})
    for k in ('fact_time', 'collected_at'):
        if value[k] is not None:
            text(value[k])
            try: need(datetime.fromisoformat(value[k].replace('Z', '+00:00')).tzinfo is not None)
            except ValueError: need(False)
    if value['fact_time'] is None or value['version'] is None: text(value['unknown_reason'])


def validate_object(value):
    exact(value, {'id', 'project_id', 'type', 'native_id', 'revision', 'label', 'identity', 'source', 'data', 'explanations'})
    need(value['id'] == stable_id(value['project_id'], value['type'], value['native_id']))
    text(value['revision'], nullable=True); text(value['label'])
    need(value['identity'] in {'fact', 'candidate'})
    source(value['source']); need(type(value['data']) is dict)
    if value['type'] == 'Artifact':
        exact(value['data'].get('artifact'), {'input_version', 'code_version', 'sha256', 'purpose', 'host', 'file_ref'})
        for k, item in value['data']['artifact'].items():
            exact(item, {'value', 'unknown_reason'})
            if item['value'] is None: text(item['unknown_reason'])
            else:
                text(item['value']); need(item['unknown_reason'] is None)
                if k == 'sha256': need(SHA.fullmatch(item['value']) is not None)

    exact(value['explanations'], EXPLANATIONS)
    for item in value['explanations'].values():
        exact(item, {'state', 'value', 'source', 'unknown_reason'})
        need(item['state'] in {'known', 'empty', 'not_applicable', 'unknown'})
        need(item['source'] is None or type(item['source']) is dict)
        if item['state'] == 'unknown':
            need(item['value'] is None); text(item['unknown_reason'])
        else:
            need(item['source'] is not None and item['unknown_reason'] is None)
    return copy.deepcopy(value)


def validate_layout(value):
    exact(value, {'view_id', 'object_id', 'object_revision', 'x', 'y', 'width', 'height', 'collapsed', 'color', 'revision'})
    for k in ('view_id', 'object_id', 'color'): text(value[k])
    text(value['object_revision'], nullable=True)
    for k in ('x', 'y', 'width', 'height'):
        need(type(value[k]) in {int, float} and math.isfinite(value[k]))
    need(value['width'] > 0 and value['height'] > 0 and type(value['collapsed']) is bool and type(value['revision']) is int and value['revision'] >= 0)
    return copy.deepcopy(value)


def validate_graph(value):
    exact(value, {'schema_version', 'project_id', 'source_version', 'projection_version', 'projected_at', 'objects', 'relations', 'layouts', 'rows', 'unknown_types', 'read_only', 'compatibility'})
    need(value['schema_version'] == '1.0' and value['read_only'] is True)
    need(value['compatibility'] == {'major': 1, 'minimum_reader': '1.0'})
    need(type(value['source_version']) is str and SHA.fullmatch(value['source_version']))
    need(type(value['objects']) is list and len(value['objects']) <= 10000)
    objects = {}; ids = set()
    for obj in value['objects']:
        validate_object(obj); need(obj['project_id'] == value['project_id'])
        key = (obj['id'], obj['revision']); need(key not in objects); objects[key] = obj; ids.add(obj['id'])
    need(type(value['relations']) is list and len(value['relations']) <= 20000)
    edges = {}; dependencies = {}
    for edge in value['relations']:
        exact(edge, {'id', 'project_id', 'native_id', 'revision', 'type', 'from', 'to', 'evidence', 'source', 'identity', 'run_id'})
        need(edge['project_id'] == value['project_id'] and edge['type'] in RELATIONS)
        need(edge['id'] == stable_id(value['project_id'], 'Relation', edge['native_id']))
        text(edge['revision'], nullable=True); source(edge['source'])
        need(edge['identity'] in {'fact', 'candidate'} and type(edge['evidence']) is list and 1 <= len(edge['evidence']) <= 128)
        for evidence in edge['evidence']: text(evidence)
        key = (edge['id'], edge['revision']); need(key not in edges); edges[key] = edge
        targets = []
        for k in ('from', 'to'):
            exact(edge[k], {'id', 'revision'}); target = (edge[k]['id'], edge[k]['revision']); need(target in objects); targets.append(objects[target])
        if edge['identity'] == 'fact': need(all(t['identity'] == 'fact' for t in targets))
        if edge['run_id'] is not None: need(any(o['id'] == edge['run_id'] and o['type'] == 'Run' and o['identity'] == 'fact' for o in objects.values()))
        if edge['type'] in {'depends_on', 'blocks'}:
            need(all(t['type'] == 'Task' for t in targets))
            a, b = ((edge['from']['id'], edge['from']['revision']), (edge['to']['id'], edge['to']['revision']))
            if edge['type'] == 'blocks': a, b = b, a
            dependencies.setdefault(a, []).append(b)
    def visit(node, active, done):
        need(node not in active)
        if node in done: return
        for child in dependencies.get(node, []): visit(child, active | {node}, done)
        done.add(node)
    done = set()
    for node in dependencies: visit(node, set(), done)
    need(type(value['layouts']) is list and len(value['layouts']) <= 10000)
    layout_ids = set()
    for layout in value['layouts']:
        validate_layout(layout); key = (layout['view_id'], layout['object_id'], layout['object_revision'])
        need(key not in layout_ids and key[1:] in objects); layout_ids.add(key)
    expected_rows = rows_for(value['objects'], value['projection_version'])
    need(value['rows'] == expected_rows)
    need(value['projection_version'] == digest({k: value[k] for k in ('project_id', 'source_version', 'objects', 'relations')}))
    need(type(value['unknown_types']) is dict and set(value['unknown_types']) == TYPES - {o['type'] for o in objects.values()})
    for reason in value['unknown_types'].values(): text(reason)
    return copy.deepcopy(value)


def rows_for(objects, version):
    return [{'object_id': o['id'], 'revision': o['revision'], 'type': o['type'], 'label': o['label'],
             'identity': o['identity'], 'source_version': o['source']['version'], 'projection_version': version}
            for o in objects]


def graph(project_id, source_version, objects, relations=(), layouts=(), *, projected_at=None):
    value = {'schema_version': '1.0', 'project_id': project_id, 'source_version': source_version,
             'objects': copy.deepcopy(objects), 'relations': copy.deepcopy(list(relations)),
             'layouts': copy.deepcopy(list(layouts)), 'projected_at': projected_at, 'read_only': True,
             'compatibility': {'major': 1, 'minimum_reader': '1.0'}}
    value['projection_version'] = digest({k: value[k] for k in ('project_id', 'source_version', 'objects', 'relations')})
    value['rows'] = rows_for(value['objects'], value['projection_version'])
    value['unknown_types'] = {t: 'No authorized current object with a stable source ID is published.' for t in sorted(TYPES - {o['type'] for o in objects})}
    return validate_graph(value)
