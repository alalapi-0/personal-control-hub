"""Explicit, uncertain owner planning estimates, separate from actual evidence."""
from __future__ import annotations

from copy import deepcopy
import re

from .connection_records import exact, identifier, require
from .time_semantics import validate_calendar_date

_FIELDS = {'schema_version', 'kind', 'as_of', 'next_review', 'completion_window',
           'confidence', 'basis', 'conditions', 'next_milestone', 'acceptance_windows'}


def _text(value, limit, label):
    require(type(value) is str and bool(value.strip()) and len(value) <= limit,
            'invalid rolling estimate ' + label)


def _date(value):
    require(value is not None, 'rolling estimate date required')
    return validate_calendar_date(value)


def _window(value):
    if value is None:
        return
    exact(value, {'earliest', 'latest'}, 'rolling estimate window')
    _date(value['earliest']); _date(value['latest'])
    require(value['earliest'] <= value['latest'], 'rolling estimate window reversed')


def validate_rolling_plan(value):
    """Validate exact forecast-only fields and return a detached, unnormalized copy."""
    if value is None:
        return None
    exact(value, _FIELDS, 'rolling estimate')
    require(value['schema_version'] == '1.0' and value['kind'] == 'rolling_estimate',
            'unsupported rolling estimate')
    _date(value['as_of']); _date(value['next_review'])
    require(value['next_review'] >= value['as_of'], 'rolling estimate review precedes estimate')
    _window(value['completion_window'])
    require(value['confidence'] in ('unknown', 'low', 'medium', 'high'), 'invalid rolling estimate confidence')
    basis = value['basis']
    require(type(basis) is list and len(basis) <= 20, 'invalid rolling estimate basis')
    for item in basis:
        exact(item, {'kind', 'reference', 'sha256', 'summary'}, 'rolling estimate basis')
        require(item['kind'] in ('source', 'assumption'), 'invalid rolling estimate basis kind')
        _text(item['reference'], 200, 'reference'); _text(item['summary'], 500, 'summary')
        digest = item['sha256']
        require(digest is None or (type(digest) is str and re.fullmatch('[0-9a-fA-F]{64}', digest)),
                'invalid rolling estimate source hash')
        require(item['kind'] != 'source' or digest is not None, 'rolling estimate source hash required')
    conditions = value['conditions']
    require(type(conditions) is list and len(conditions) <= 20, 'invalid rolling estimate conditions')
    for condition in conditions:
        _text(condition, 500, 'condition')
    milestone = value['next_milestone']
    ids = set()
    if milestone is not None:
        exact(milestone, {'id', 'label', 'window', 'condition'}, 'rolling estimate milestone')
        identifier(milestone['id'], 'milestone'); ids.add(milestone['id'])
        _text(milestone['label'], 200, 'milestone label'); _window(milestone['window'])
        if milestone['condition'] is not None:
            _text(milestone['condition'], 500, 'milestone condition')
    acceptance = value['acceptance_windows']
    require(type(acceptance) is list and len(acceptance) <= 20, 'invalid rolling estimate acceptance windows')
    graph = {}
    for item in acceptance:
        exact(item, {'id', 'kind', 'label', 'window', 'depends_on', 'scope'}, 'rolling estimate acceptance')
        identifier(item['id'], 'acceptance')
        require(item['id'] not in ids, 'duplicate rolling estimate milestone/acceptance id')
        ids.add(item['id'])
        require(item['kind'] in ('automatic', 'human'), 'invalid rolling estimate acceptance kind')
        _text(item['label'], 200, 'acceptance label'); _text(item['scope'], 500, 'acceptance scope')
        _window(item['window'])
        dependencies = item['depends_on']
        require(type(dependencies) is list and len(dependencies) <= 20, 'invalid rolling estimate dependencies')
        for dependency in dependencies:
            identifier(dependency, 'dependency')
        require(len(dependencies) == len(set(dependencies)), 'duplicate rolling estimate dependency')
        graph[item['id']] = dependencies
    for node, dependencies in graph.items():
        require(node not in dependencies and all(dependency in ids for dependency in dependencies),
                'invalid rolling estimate dependency reference')
    done = set()
    def visit(node, active):
        require(node not in active, 'rolling estimate dependency cycle')
        if node in done:
            return
        for dependency in graph.get(node, []):
            visit(dependency, active | {node})
        done.add(node)
    for node in graph:
        visit(node, set())
    return deepcopy(value)
