"""Refactor identity acceptance uses isolated roots, never business projects."""
import copy
from unittest.mock import patch

import pytest
import yaml

from connection_fixtures import Fixture
from hub.connection_records import RecordError


def test_stable_identity_survives_checkout_rename_without_duplicate_registration():
    f = Fixture()
    try:
        root, declaration = f.add()
        renamed = root.with_name('renamed-checkout')
        root.rename(renamed)
        f.projects[0]['root_path'] = str(renamed)
        f.save_registry()
        result = f.resolver().refresh('a')
        assert result['success'] and result['project_id'] == 'a'
        assert len(f.resolver().projects) == 1
        assert result['business']['progress']['total'] == f.business['progress']['total']
    finally:
        f.close()


def test_duplicate_checkout_record_cannot_duplicate_stable_project_id():
    f = Fixture()
    try:
        f.add()
        clone = copy.deepcopy(f.projects[0])
        clone['root_path'] = str(f.base / 'second-checkout')
        f.projects.append(clone)
        f.save_registry()
        with pytest.raises(RecordError):
            f.resolver()
    finally:
        f.close()


def test_unregistered_candidate_is_not_discovered_or_read():
    f = Fixture()
    try:
        f.add()
        candidate = f.base / 'candidate'
        candidate.mkdir()
        (candidate / 'STATE.yaml').write_text('not registered\n')
        resolver = f.resolver()
        with patch('os.open', side_effect=AssertionError('candidate opened')):
            with pytest.raises(KeyError):
                resolver.refresh('candidate')
        assert set(resolver.projects) == {'a'}
    finally:
        f.close()


def test_source_role_cannot_widen_current_state_read_permission():
    f = Fixture()
    try:
        root, declaration = f.add()
        declaration['source_refs'][0]['role'] = 'exporter'
        (root / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration))
        result = f.resolver().refresh('a')
        assert not result['success'] and result['disposition'] == 'invalid'
        assert result['sources'] == []
    finally:
        f.close()


def test_exporter_metadata_is_never_executed_and_source_is_unchanged():
    f = Fixture()
    try:
        root, declaration = f.add()
        declaration['metric_export'] = {'entry': 'export.py', 'snapshot': '.hub/status.json'}
        (root / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration))
        before = (root / 'STATE.yaml').read_bytes()
        with patch('subprocess.run', side_effect=AssertionError('exporter executed')), \
                patch('subprocess.Popen', side_effect=AssertionError('exporter spawned')):
            result = f.resolver().refresh('a')
        assert result['success']
        assert before == (root / 'STATE.yaml').read_bytes()
        assert not (root / '.hub/status.json').exists()
    finally:
        f.close()
