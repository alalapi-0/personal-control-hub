"""Source-adapter checks use temporary Hub state, never live business roots."""
import json
import os
from unittest import mock

import pytest
import yaml

from connection_fixtures import Fixture
from hub.connection_records import RecordError
from hub.connections import parse_owned_hub_state, parse_source
from hub.connection_records import empty_business
from hub.connection_sources import (MAX_SOURCE_BYTES, MAX_OWNED_HUB_STATE_BYTES,
                                    _safe_relative_read)


def test_owned_canonical_hub_can_read_historical_yaml_sharing():
    data = b'metadata: {authority: canonical}\nhistorical: &old {status: closed}\narchive: *old\npch_refactor: {status: ACTIVE}\n'
    assert parse_owned_hub_state(data)['pch_refactor']['status'] == 'ACTIVE'
    with pytest.raises(RecordError):
        parse_source(data, 'yaml')
    for invalid in (b'metadata: {authority: other}\n',
                    b'metadata: {authority: canonical}\na: 1\na: 2\n',
                    b'metadata: {authority: canonical}\na: !!python/object:unsafe {}\n'):
        with pytest.raises(RecordError):
            parse_owned_hub_state(invalid)


def test_hub_adapter_never_widens_external_root_or_source_role():
    f = Fixture()
    try:
        root, declaration = f.add('personal-control-hub')
        data = 'metadata: {authority: canonical}\nold: &old {}\narchive: *old\n' + yaml.safe_dump(f.business)
        (root / 'STATE.yaml').write_text(data)
        # This project's root is outside the resolver's own Hub root.
        assert f.resolver().refresh('personal-control-hub')['disposition'] == 'invalid'
    finally:
        f.close()


@pytest.fixture
def owned_hub():
    f = Fixture()
    external, declaration = f.add('personal-control-hub')
    f.projects[0]['root_path'] = str(f.hub)
    (f.hub / 'hub.connection.yaml').write_bytes((external / 'hub.connection.yaml').read_bytes())
    f.save_registry()
    try:
        yield f, declaration
    finally:
        f.close()


def write_state(f, *, size, path='STATE.yaml', source_format='yaml'):
    value = {'metadata': {'authority': 'canonical'}, **f.business}
    if source_format == 'json':
        data = json.dumps(value).encode()
        data += b' ' * (size - len(data))
    else:
        data = yaml.safe_dump(value).encode() + b'#'
        data += b'x' * (size - len(data))
    assert len(data) == size
    (f.hub / path).write_bytes(data)
    return data


def assert_no_partial_result(result):
    assert not result['success']
    assert result['business'] == empty_business()
    assert result['field_provenance'] == {}
    assert 'extensions' not in result


@pytest.mark.parametrize('size', [MAX_SOURCE_BYTES + 1, MAX_OWNED_HUB_STATE_BYTES])
def test_owned_state_fixed_budget_reads_real_file_twice(owned_hub, size):
    f, _ = owned_hub
    data = write_state(f, size=size)
    calls = []

    def read(root, relative, **kwargs):
        calls.append((relative, kwargs.get('max_bytes', MAX_SOURCE_BYTES)))
        return _safe_relative_read(root, relative, **kwargs)

    with mock.patch('hub.connection_sources._safe_relative_read', read):
        result = f.resolver().refresh('personal-control-hub')
    assert result['success']
    assert result['business'] == f.business
    assert result['sources'][0]['bytes'] == len(data)
    assert calls == [('hub.connection.yaml', MAX_SOURCE_BYTES),
                     ('STATE.yaml', MAX_OWNED_HUB_STATE_BYTES),
                     ('STATE.yaml', MAX_OWNED_HUB_STATE_BYTES),
                     ('hub.connection.yaml', MAX_SOURCE_BYTES)]


def test_owned_state_larger_than_fixed_budget_fails_closed(owned_hub):
    f, _ = owned_hub
    write_state(f, size=MAX_OWNED_HUB_STATE_BYTES + 1)
    assert_no_partial_result(f.resolver().refresh('personal-control-hub'))


@pytest.mark.parametrize('variant', ['external_root', 'other_id', 'other_path', 'other_format', 'json_route'])
def test_larger_budget_requires_all_owned_canonical_conditions(owned_hub, variant):
    f, declaration = owned_hub
    project_id = 'personal-control-hub'
    path, source_format = 'STATE.yaml', 'yaml'
    if variant == 'other_id':
        project_id = 'another-hub'
        f.projects[0]['id'] = project_id
        declaration['project_id'] = project_id
    elif variant == 'other_path':
        path = 'OTHER.yaml'
        f.projects[0]['current_state_paths'] = [path]
    elif variant == 'other_format':
        source_format = 'json'
    elif variant == 'json_route':
        path, source_format = 'STATE.json', 'json'
        f.projects[0]['current_state_paths'] = [path]
    declaration['source_refs'][0].update(path=path, format=source_format)
    (f.hub / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration))
    data = write_state(f, size=MAX_SOURCE_BYTES + 1, path=path, source_format=source_format)
    if variant == 'external_root':
        # Exact same registered ID, name, relative path and format are insufficient.
        external = f.base / 'personal-control-hub'
        (external / path).write_bytes(data)
        f.projects[0]['root_path'] = str(external)
    f.save_registry()
    reads = []

    def read(root, relative, **kwargs):
        reads.append((relative, kwargs.get('max_bytes', MAX_SOURCE_BYTES)))
        return _safe_relative_read(root, relative, **kwargs)

    with mock.patch('hub.connection_sources._safe_relative_read', read):
        result = f.resolver().refresh(project_id)
    assert_no_partial_result(result)
    expected = [('hub.connection.yaml', MAX_SOURCE_BYTES)]
    # A format-only spoof fails the existing format/suffix declaration gate,
    # before source I/O. A valid JSON route still gets the generic source cap.
    if variant != 'other_format':
        expected.append((path, MAX_SOURCE_BYTES))
    assert reads == expected


def test_owned_declaration_retains_generic_budget(owned_hub):
    f, _ = owned_hub
    write_state(f, size=MAX_SOURCE_BYTES + 1)
    path = f.hub / 'hub.connection.yaml'
    data = path.read_bytes() + b'\n#'
    path.write_bytes(data + b'x' * (MAX_SOURCE_BYTES + 1 - len(data)))
    result = f.resolver().refresh('personal-control-hub')
    assert_no_partial_result(result)
    assert result['sources'] == []


def test_owned_declaration_recheck_retains_generic_budget(owned_hub):
    f, _ = owned_hub
    write_state(f, size=MAX_SOURCE_BYTES + 1)
    source_reads = 0

    def read(root, relative, **kwargs):
        nonlocal source_reads
        value = _safe_relative_read(root, relative, **kwargs)
        if relative == 'STATE.yaml':
            source_reads += 1
            if source_reads == 2:
                path = root / 'hub.connection.yaml'
                data = path.read_bytes() + b'\n#'
                path.write_bytes(data + b'x' * (MAX_SOURCE_BYTES + 1 - len(data)))
        return value

    with mock.patch('hub.connection_sources._safe_relative_read', read):
        result = f.resolver().refresh('personal-control-hub')
    assert source_reads == 2
    assert_no_partial_result(result)


@pytest.mark.parametrize('change', ['mutation', 'replacement', 'symlink', 'hardlink', 'root_identity'])
def test_large_owned_state_version_and_identity_still_fail_closed(owned_hub, change):
    f, _ = owned_hub
    original = write_state(f, size=MAX_SOURCE_BYTES + 1)
    resolver = f.resolver()
    first = True

    def read(root, relative, **kwargs):
        nonlocal first
        value = _safe_relative_read(root, relative, **kwargs)
        if relative == 'STATE.yaml' and first:
            first = False
            source = root / relative
            if change == 'mutation':
                with source.open('r+b') as stream:
                    stream.seek(-1, os.SEEK_END)
                    stream.write(b'y')
            elif change == 'replacement':
                replacement = root / 'replacement.yaml'
                replacement.write_bytes(original)
                replacement.replace(source)
            elif change in ('symlink', 'hardlink'):
                retained = root / 'retained.yaml'
                source.rename(retained)
                if change == 'symlink':
                    source.symlink_to(retained)
                else:
                    os.link(retained, source)
            else:
                moved = f.base / 'former-hub'
                root.rename(moved)
                (root / 'data/registry').mkdir(parents=True)
                (root / 'data/registry/external_projects.yaml').write_bytes(resolver.registry_bytes)
                source.write_bytes(original)
                (root / 'hub.connection.yaml').write_bytes((moved / 'hub.connection.yaml').read_bytes())
        return value

    with mock.patch('hub.connection_sources._safe_relative_read', read):
        result = resolver.refresh('personal-control-hub')
    assert_no_partial_result(result)
    if change == 'root_identity':
        assert result['disposition'] == 'authority_drift'
