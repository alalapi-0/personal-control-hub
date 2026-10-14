import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import governance_scope as scope
import auto_advance_runner as runner
import round_consistency_check as consistency


def fixture(tmp_path):
    entry = {'task_id': scope.PCH_REFACTOR_TASK_ID, 'status': 'ACTIVE',
             'phase': 'P1', 'round': 'R1.1', 'plan_version': '1.0',
             'execution_document': 'plan.md', 'task_catalog': 'catalog.yaml',
             'candidate_paths': ['owned.py'], 'delivery': {'remote_main_verified': False},
             'next_action': 'Verify entry', 'active_task_id': 'leaf',
             'task_results': {'prior': {'verification': 'passed'}}}
    state = {'metadata': {'authority': 'canonical'}, 'pch_refactor': entry,
             'all_projects_governance': {'task_id': scope.TASK_ID, 'unit': 'old',
                 'status': 'ACTIVE', 'next_action': 'old next', 'candidate_paths': ['old.py'],
                 'execution_document': 'old.md', 'delivery': {}}}
    (tmp_path / 'AGENTS.md').write_text('Fixture rules\n')
    (tmp_path / 'STATE.yaml').write_text(yaml.safe_dump(state))
    for path in ('plan.md', 'catalog.yaml'):
        (tmp_path / path).write_text('fixture\n')
    return state


def test_refactor_uses_own_state_and_bounded_projection(tmp_path):
    state = fixture(tmp_path)
    packet = scope.boot_packet(tmp_path, scope.PCH_REFACTOR_TASK_ID)
    payload = json.loads(packet[len((tmp_path / 'AGENTS.md').read_bytes()):])
    assert set(payload) == {'metadata', 'boot_contract_version', 'pch_refactor'}
    assert 'task_results' not in payload['pch_refactor']
    assert payload['pch_refactor']['round'] == 'R1.1'
    assert len(packet) <= 8192
    with patch.dict('os.environ', {scope.ENV_NAME: scope.PCH_REFACTOR_TASK_ID}), \
            patch.object(runner, 'ROOT', tmp_path), patch.object(consistency, 'ROOT', tmp_path):
        before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
        assert runner._get_round_context()['current_phase'] == 'P1'
        assert runner._get_round_context()['task_unit'] == 'leaf'
        assert consistency.run_check()['current_round'] == 'R1.1'
        assert runner._candidate_paths() == ['STATE.yaml', 'owned.py']
        assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert scope.task_entry(state, scope.TASK_ID)['unit'] == 'old'


@pytest.mark.parametrize('field,value', [('round', None), ('phase', 1),
    ('plan_version', ''), ('task_catalog', {}), ('task_id', 'wrong')])
def test_refactor_rejects_malformed_entry(tmp_path, field, value):
    state = fixture(tmp_path)
    state['pch_refactor'][field] = value
    with pytest.raises(ValueError):
        scope.task_entry(state, scope.PCH_REFACTOR_TASK_ID)


@pytest.mark.parametrize('paths', [[], ['../outside'], ['.git/config'], ['/outside']])
def test_refactor_rejects_candidate_escape(tmp_path, paths):
    state = fixture(tmp_path)
    state['pch_refactor']['candidate_paths'] = paths
    (tmp_path / 'STATE.yaml').write_text(yaml.safe_dump(state))
    with pytest.raises(ValueError):
        scope.boot_packet(tmp_path, scope.PCH_REFACTOR_TASK_ID)


def test_refactor_rejects_duplicate_state_and_unknown_scope(tmp_path):
    fixture(tmp_path)
    with (tmp_path / 'STATE.yaml').open('a') as f:
        f.write('\nunrelated: 1\nunrelated: 2\n')
    with pytest.raises(ValueError):
        scope.boot_packet(tmp_path, scope.PCH_REFACTOR_TASK_ID)
    with pytest.raises(ValueError):
        scope.activate_scope('unknown-task')


def test_supported_but_unconfigured_task_fails_closed(tmp_path):
    state = fixture(tmp_path)
    state.pop('pch_refactor')
    (tmp_path / 'STATE.yaml').write_text(yaml.safe_dump(state))
    with pytest.raises(ValueError):
        scope.boot_packet(tmp_path, scope.PCH_REFACTOR_TASK_ID)
    assert scope.task_entry(state, scope.TASK_ID)['unit'] == 'old'
