"""Registry commentary bound to exact current facts, with no source I/O.

These explanations are Hub-authored display aids, never replacement business
facts or statements automatically published by a connected project.
"""
from __future__ import annotations

import copy

from hub.connection_records import (exact, fingerprint, get_field, identifier,
                                    relative_path, require, string, validate_selector)

FIELDS = frozenset({'current_work.' + name for name in ('objective', 'phase', 'round', 'next_action')}
                   | {'progress.counting_basis'})


def _bounded(value, label, limit):
    string(value, label)
    require(len(value) <= limit, label + ': size exceeded')


def validate_field_explanations(value):
    exact(value, {'schema_version', 'fields'}, 'Hub field explanations')
    require(value['schema_version'] == '1.0', 'unsupported field explanations schema')
    fields = value['fields']
    require(type(fields) is dict and set(fields) <= FIELDS, 'unsupported explanation field')
    for item in fields.values():
        exact(item, {'text', 'binding', 'basis'}, 'field explanation')
        _bounded(item['text'], 'explanation text', 4000)
        binding = item['binding']
        exact(binding, {'source_ref', 'selector', 'sha256', 'value'}, 'explanation binding')
        identifier(binding['source_ref'], 'explanation source')
        validate_selector(binding['selector'])
        fingerprint(binding['sha256'], 'explanation source')
        _bounded(binding['value'], 'explanation bound value', 16000)
        basis = item['basis']
        require(type(basis) is list and 0 < len(basis) <= 8, 'bounded explanation basis required')
        for reference in basis:
            exact(reference, {'path', 'sha256', 'locator'}, 'explanation basis')
            _bounded(reference['path'], 'explanation basis path', 16000)
            relative_path(reference['path'])
            fingerprint(reference['sha256'], 'explanation basis')
            _bounded(reference['locator'], 'explanation basis locator', 512)
    return value


def field_explanations(declared, record, freshness, authority, read_allowed):
    """Project current validated evidence only; mismatches hide commentary.

    No historical record fallback and no source reads. Registry validation occurs
    on load; this helper never changes the registry, ledger or normalized facts.
    """
    result = {'schema_version': '1.0', 'project_id': declared['id'], 'fields': {}}
    if (not read_allowed or not record or record.get('project_id') != declared['id']
            or record.get('success') is not True or record.get('disposition') != 'resolved'
            or freshness.get('state') != 'fresh' or freshness.get('authority_drift', False)
            or record.get('authority') != authority
            or declared.get('local_presence', {}).get('status') == 'removed_local'
            or declared.get('current_state_status') == 'removed_local'
            or declared.get('access_profile') == 'no_current_goal_access'
            or declared.get('connection_read_allowed') is False):
        return result
    for key, item in declared.get('hub_field_explanations', {}).get('fields', {}).items():
        binding = item['binding']
        actual = get_field(record['business'], key)
        proof = record.get('field_provenance', {}).get(key)
        if (type(actual) is not str or not actual.strip() or actual != binding['value']
                or key in record.get('unknown_fields', {}) or type(proof) is not dict
                or any(proof.get(name) != binding[name] for name in ('source_ref', 'selector', 'sha256'))
                or not any(source.get('id') == binding['source_ref'] and source.get('sha256') == binding['sha256']
                           for source in record.get('sources', []))):
            continue
        result['fields'][key] = {'text': item['text'], 'binding': copy.deepcopy(binding),
            'declaration': {'kind': 'registry', 'field': 'hub_field_explanations.fields.' + key,
                            'registry_sha256': authority['registry_hash'], 'sha256': authority['registry_hash'],
                            'basis': copy.deepcopy(item['basis'])}}
    return result
