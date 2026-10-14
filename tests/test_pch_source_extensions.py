"""Source-adapter checks use temporary Hub state, never live business roots."""
import copy
from pathlib import Path

import pytest
import yaml

from connection_fixtures import Fixture
from hub.connection_records import RecordError
from hub.connections import parse_owned_hub_state, parse_source


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
