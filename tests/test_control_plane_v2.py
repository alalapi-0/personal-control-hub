import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import governance_scope as scope
import agent_gate
import auto_advance_runner as runner
import round_consistency_check as consistency


def load_yaml(relative: str):
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def test_default_boot_packet_is_bounded():
    for task in scope.TASK_KEYS:
        assert len(scope.boot_packet(ROOT, task)) <= 8192


def scoped_fixture(tmp_path):
    state = load_yaml('STATE.yaml')
    (tmp_path / 'AGENTS.md').write_text('Fixture rules\n')
    (tmp_path / 'STATE.yaml').write_text(yaml.safe_dump(state, allow_unicode=True))
    return state


def test_scoped_projection_validates_whole_state_and_is_deterministic(tmp_path):
    state = scoped_fixture(tmp_path)
    first = scope.boot_packet(tmp_path, scope.WORKBENCH_TASK_ID)
    assert first == scope.boot_packet(tmp_path, scope.WORKBENCH_TASK_ID)
    payload = json.loads(first[len((tmp_path / 'AGENTS.md').read_bytes()):])
    assert set(payload) == {'metadata', 'boot_contract_version', 'linux_visual_workbench'}
    assert 'baseline' not in payload['linux_visual_workbench']
    # Even unrelated malformed/duplicated YAML must fail before projection.
    for suffix in ('\nunrelated: [', '\nunrelated: 1\nunrelated: 2\n', '\nunrelated:\n  a: 1\n  a: 2\n'):
        (tmp_path / 'STATE.yaml').write_text(yaml.safe_dump(state) + suffix)
        with pytest.raises(ValueError):
            scope.boot_packet(tmp_path, scope.WORKBENCH_TASK_ID)


@pytest.mark.parametrize('bad', [None, {}, {'task_id': 'wrong'}])
def test_scoped_missing_or_malformed_entry_rejected(tmp_path, bad):
    state = scoped_fixture(tmp_path)
    state['linux_visual_workbench'] = bad
    (tmp_path / 'STATE.yaml').write_text(yaml.safe_dump(state))
    with pytest.raises(ValueError):
        scope.boot_packet(tmp_path, scope.WORKBENCH_TASK_ID)


@pytest.mark.parametrize('paths', [[], ['/outside'], ['../outside'], ['a/../b'], ['.git/config'], ['a//b']])
def test_scoped_candidate_escape_rejected(tmp_path, paths):
    state = scoped_fixture(tmp_path)
    state['linux_visual_workbench']['candidate_paths'] = paths
    (tmp_path / 'STATE.yaml').write_text(yaml.safe_dump(state))
    with pytest.raises(ValueError):
        scope.boot_packet(tmp_path, scope.WORKBENCH_TASK_ID)


def test_scope_unknown_symlink_oversize_and_legacy_behavior(tmp_path):
    state = scoped_fixture(tmp_path)
    with pytest.raises(ValueError):
        scope.boot_packet(tmp_path, 'unknown')
    with pytest.raises(ValueError):
        scope.activate_scope('unknown')
    state['linux_visual_workbench']['candidate_paths'] = ['link/file']
    (tmp_path / 'link').symlink_to(ROOT, target_is_directory=True)
    (tmp_path / 'STATE.yaml').write_text(yaml.safe_dump(state))
    with pytest.raises(ValueError):
        scope.boot_packet(tmp_path, scope.WORKBENCH_TASK_ID)
    state['linux_visual_workbench']['candidate_paths'] = ['owned']
    state['linux_visual_workbench']['next_action'] = 'x' * 8193
    (tmp_path / 'STATE.yaml').write_text(yaml.safe_dump(state))
    with patch.dict('os.environ', {scope.ENV_NAME: scope.WORKBENCH_TASK_ID}), patch.object(agent_gate, 'ROOT', tmp_path):
        hard = []
        agent_gate._check_default_boot(hard)
        assert any('8192' in h for h in hard)
    with patch.dict('os.environ', {scope.ENV_NAME: ''}):
        assert scope.boot_packet(tmp_path) == (tmp_path / 'AGENTS.md').read_bytes() + (tmp_path / 'STATE.yaml').read_bytes()


def test_selected_runner_identity_and_unsupported_prepare_next():
    with patch.dict('os.environ', {scope.ENV_NAME: scope.WORKBENCH_TASK_ID}):
        state = scope.load_canonical_state(ROOT)['linux_visual_workbench']
        assert runner._get_round_context()['current_round'] == state['current_round']
        assert consistency.run_check()['current_round'] == state['current_round']
        with patch.object(runner, '_run_checks', side_effect=AssertionError('unsupported transition must stop')):
            assert runner.mode_prepare_next()['decision'] == 'stop'
        with patch.object(runner, '_run_script_command', return_value=(0, '')) as call:
            runner._git_status_porcelain()
            args = call.call_args.args[0]
            assert set(a.removeprefix(':(literal)') for a in args[args.index('--')+1:]) == set(runner._candidate_paths())
            assert 'README.md' not in args


def test_state_authority_is_unique_and_legacy_files_are_demoted():
    state = load_yaml("STATE.yaml")
    legacy_round = load_yaml("governance/round_state.yaml")
    legacy_status = load_yaml("data/state/current_status.yaml")
    assert state["metadata"]["authority"] == "canonical"
    assert legacy_round["metadata"]["authority"] == "non_canonical"
    assert legacy_status["metadata"]["authority"] == "non_canonical"
    assert legacy_round["metadata"]["canonical_current_state"] == "STATE.yaml"
    assert legacy_status["metadata"]["canonical_current_state"] == "STATE.yaml"


def test_storage_governance_registry_is_portable_and_pointer_only():
    registry = load_yaml("data/registry/external_projects.yaml")
    project = next(item for item in registry["projects"] if item["id"] == "storage_governance")
    assert project["root_path"] == "~/PycharmProjects/personal-control-hub/governance/programs/storage_governance"
    assert project["authority_files"] == ["STATE.yaml", "STORAGE_GOVERNANCE.md"]
    assert project["external_write_allowed"] is False
    assert project["competing_authority_copy_forbidden"] is True
    assert "/Users/" not in str(project)


def test_registry_identity_reflects_subplz_removal_and_mpv_registration():
    registry = load_yaml("data/registry/external_projects.yaml")
    project_by_id = {item["id"]: item for item in registry["projects"]}
    assert len(project_by_id) == len(registry["projects"]) == 24

    expected_git_projects = {
        "audio-clone",
        "ai-anime-short-factory",
        "ai-music-foundry",
        "light-novel",
        "resilient-personal-network",
        "novel-continuation-agent",
        "computer-study-plan",
        "universal-player",
        "story-faceless-utopia",
        "pixel-world-asset-forge",
        "zarathustra-adaptation",
        "wechat-article-scheduler",
        "cognitive-asset-library",
        "mpv-clip-workbench",
    }
    assert expected_git_projects <= project_by_id.keys()
    assert "subplz" not in project_by_id

    mpv = project_by_id["mpv-clip-workbench"]
    assert mpv["root_path"] == "/Volumes/AI_WORK_SSD/mpv-clip-workbench"
    assert mpv["external_write_allowed"] is False
    assert mpv["storage_governance"] == {
        "scope": "owner_named_existing_external_project",
        "root_migration": "already_external_no_copy",
        "authority_source": "sole_storage_execution_state",
    }

    manga = project_by_id["manga-localizer"]
    assert manga["enabled"] is False
    assert manga["scan_enabled"] is False
    assert manga["storage_governance"] == {
        "scope": "accepted_exclusion_record_only",
        "inventory_allowed": False,
        "inspection_allowed": False,
        "validation_allowed": False,
        "mutation_allowed": False,
    }

    hub = project_by_id["personal-control-hub"]
    assert hub["scan_enabled"] is False
    assert hub["storage_governance"]["migration_allowed"] is False
    assert hub["storage_governance"]["cleanup_allowed"] is False


def test_registry_registers_rules_and_current_state_for_every_frozen_record():
    registry = load_yaml("data/registry/external_projects.yaml")
    project_by_id = {item["id"]: item for item in registry["projects"]}

    for project in registry["projects"]:
        assert isinstance(project["rules_paths"], list)
        assert isinstance(project["current_state_paths"], list)
        assert isinstance(project["current_state_status"], str)
        assert project["current_state_status"]
        assert isinstance(project["supporting_authority_paths"], list)

    universal_player = project_by_id["universal-player"]
    assert universal_player["current_state_paths"] == [
        "/Users/alalapi/PycharmProjects/universal-player/.agent/STATE.md"
    ]
    assert ".agent/STATE.md" in universal_player["watch_paths"]

    manga = project_by_id["manga-localizer"]
    assert manga["rules_paths"] == []
    assert manga["current_state_paths"] == []
    assert manga["supporting_authority_paths"] == []

    contract = registry["storage_governance_contract"]
    assert contract["frozen_manifest"].endswith("/PROJECT_MANIFEST_v2.yaml")
    assert len(contract["frozen_manifest_sha256"]) == 64


def test_registry_historical_manifest_and_current_hub_epoch_are_distinct_authorities():
    registry = load_yaml("data/registry/external_projects.yaml")
    hub_state = load_yaml("STATE.yaml")
    storage_state = yaml.safe_load(
        Path("~/PycharmProjects/personal-control-hub/governance/programs/storage_governance/STATE.yaml").expanduser().read_text(encoding="utf-8")
    )
    registry_bytes = (ROOT / "data/registry/external_projects.yaml").read_bytes()
    assert hashlib.sha256(registry_bytes).hexdigest() == storage_state["parent_hub"][
        "project_registry_sha256_current"
    ]

    historical_path = Path(registry["storage_governance_contract"]["frozen_manifest"])
    historical_manifest = yaml.safe_load(historical_path.read_text(encoding="utf-8"))
    historical_sha = hashlib.sha256(historical_path.read_bytes()).hexdigest()
    assert historical_sha == registry["storage_governance_contract"]["frozen_manifest_sha256"]
    assert historical_manifest["manifest"]["epoch_id"] == "dev_projects_2026_09_02_r1"

    current_path = Path(hub_state["registry"]["frozen_manifest"])
    current_manifest = yaml.safe_load(current_path.read_text(encoding="utf-8"))
    current_sha = hashlib.sha256(current_path.read_bytes()).hexdigest()
    assert current_path != historical_path
    assert current_sha != historical_sha
    assert current_sha == hub_state["registry"]["frozen_manifest_sha256"]
    assert current_sha == storage_state["inventory_epoch"]["project_manifest_sha256"]
    assert current_manifest["manifest"]["epoch_id"] == storage_state["inventory_epoch"]["id"]

    historical_ids = {record["id"] for record in historical_manifest["records"]}
    current_epoch_ids = {record["id"] for record in current_manifest["records"]}
    current_registry_ids = {record["id"] for record in registry["projects"]}
    assert "subplz" in historical_ids and "mpv-clip-workbench" not in historical_ids
    assert {"subplz", "mpv-clip-workbench"} <= current_epoch_ids
    assert "subplz" not in current_registry_ids and "mpv-clip-workbench" in current_registry_ids

    post_path = Path(storage_state["closure"]["final_report"])
    post = yaml.safe_load(post_path.read_text(encoding="utf-8"))
    assert post["epoch"] == current_manifest["manifest"]["epoch_id"]
    assert set(post["removed_projects"]["subplz"].values()) == {"PASS"}
    assert post["mpv_clip_workbench"]["registry"] == "linked"

    # Hub holds a pointer; only the program state owns execution.
    assert "compact_execution_summary" not in hub_state["storage_governance"]
    execution = storage_state["execution_control"]
    assert execution["project_effects_allowed"] is False
    assert execution["control_plane_effects_active"] is False
    assert execution["storage_effects_active"] is False
    assert execution["active_projects"] == execution["active_batches"] == execution["active_writers"] == 0

def test_storage_adapter_does_not_copy_or_override_norm():
    adapter = load_yaml("governance/adapters/storage_governance.yaml")
    assert adapter["authority"]["activation_prompt"] == "prompts/storage_governance_goal_mode.md"
    assert adapter["authority"]["stable_parameters"] == "data/programs/storage_governance_goal.yaml"
    assert adapter["authority"]["project_registry"] == "data/registry/external_projects.yaml"
    assert adapter["authority"]["sole_execution_state"] == "~/PycharmProjects/personal-control-hub/governance/programs/storage_governance/STATE.yaml"
    assert adapter["authority"]["sole_norm"] == "~/PycharmProjects/personal-control-hub/governance/programs/storage_governance/STORAGE_GOVERNANCE.md"
    assert adapter["authority"]["competing_copy_allowed"] is False
    assert adapter["authority"]["authority_location"] == "hub_owned_co_located_program"
    assert adapter["authority"]["hub_may_override_norm"] is False
    assert adapter["boundaries"]["external_project_effects_from_hub_registry"] == "forbidden"


def test_storage_execution_route_reads_registry_before_sole_state():
    adapter = load_yaml("governance/adapters/storage_governance.yaml")
    assert adapter["routing"]["storage_execution_task"]["each_turn_read_order"] == [
        "data/programs/storage_governance_goal.yaml",
        "data/registry/external_projects.yaml",
        "governance/adapters/storage_governance.yaml",
        "~/PycharmProjects/personal-control-hub/governance/programs/storage_governance/AGENTS.md",
        "~/PycharmProjects/personal-control-hub/governance/programs/storage_governance/STATE.yaml",
        "current_project_AGENTS_and_current_state_if_present",
    ]


def test_storage_goal_package_is_single_source_and_not_current_state():
    # An active Goal checks the path without re-reading or reactivating the prompt.
    assert (ROOT / "prompts/storage_governance_goal_mode.md").is_file()
    params = load_yaml("data/programs/storage_governance_goal.yaml")
    state = load_yaml("STATE.yaml")

    assert params["metadata"]["authority"] == "stable_parameters_not_current_state"
    assert params["control_planes"]["personal_control_hub"]["migration_excluded"] is True
    assert params["control_planes"]["personal_control_hub"]["cleanup_excluded"] is True
    assert params["project_inventory"]["project_minimum_allocated_bytes"] == 0
    assert params["explicit_exclusions"]["manga_localizer"]["disposition"] == "accepted_exclusion_record_only"
    assert params["explicit_exclusions"]["manga_localizer"]["inventory"] == "forbidden"
    assert params["explicit_exclusions"]["manga_localizer"]["validation"] == "forbidden"
    assert params["explicit_exclusions"]["manga_localizer"]["mutation"] == "forbidden"
    assert params["execution"]["concurrency"] == {
        "active_projects": 1,
        "active_batches": 1,
        "writers": 1,
        "next_actions": 1,
    }
    for forbidden_key in ("current_batch", "progress", "attempt_count", "next_action", "effect_authority_state"):
        assert forbidden_key not in params

    goal_package = state["storage_governance"]["goal_package"]
    assert goal_package["prompt"] == "prompts/storage_governance_goal_mode.md"
    assert goal_package["parameters"] == "data/programs/storage_governance_goal.yaml"
    assert goal_package["project_registry"] == "data/registry/external_projects.yaml"
    assert set(goal_package) == {"prompt", "parameters", "project_registry"}
    assert state["storage_governance"]["sole_execution_state"] == "governance/programs/storage_governance/STATE.yaml"


def test_release_first_parameters_remove_routine_history_proof_and_backup_gates():
    params = load_yaml("data/programs/storage_governance_goal.yaml")
    assert params["execution"]["priorities"][:2] == [
        "release_internal_storage_first", "usable_external_project_operation_second"
    ]
    assert params["owner_risk_tolerance"]["authorized_generated_history_loss"] is True
    gates = params["per_project_gates"]
    assert "intentional_permanent_loss_is_accepted" in gates["disposable_generated_data_cleanup"]
    assert "no_archive_hashgraph_xattr_parity_second_restore_copy_or_immediate_rebuild_proof_required" in gates["disposable_generated_data_cleanup"]
    assert "one_current_primary_entry_start_or_core_smoke_as_applicable" in gates["project_level_validation"]
    assert "private_or_non_reproducible_payloads_without_content_aware_migration_proof" not in params["project_inventory"]["exclusions"]
    assert params["completion"]["zero_conditions"] == [
        "pending_projects_or_items", "active_projects", "active_batches",
        "active_writers", "unconsumed_effect_authority"
    ]


def test_current_owner_resume_contract_shape_and_hub_pointer_agree():
    hub = load_yaml("STATE.yaml")
    state = yaml.safe_load(
        Path("~/PycharmProjects/personal-control-hub/governance/programs/storage_governance/STATE.yaml").expanduser().read_text(encoding="utf-8")
    )
    binding = state["task_contract"]
    path = Path(binding["path"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"]
    contract = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert (ROOT / hub["storage_governance"]["sole_execution_state"]).resolve() == (ROOT / "governance/programs/storage_governance/STATE.yaml").resolve()
    assert contract["contract"] == binding["version"]
    assert contract["authority_source"] == state["authorization_scope"]["source"]
    assert contract["objective"] == (
        "migrate_all_registered_projects_except_manga_localizer_and_personal_control_hub_to_ai_work_ssd"
    )
    assert contract["additional_project"] == "mpv-clip-workbench"
    assert "access_to_manga_localizer_tree_or_associated_paths" in contract["forbidden"]
    assert "migration_or_cleanup_of_personal_control_hub_or_storage_governance" in contract["forbidden"]
    assert binding["status"] == state["execution_control"]["state"] == "COMPLETE_WITH_OWNER_REMOVAL"
    assert state["execution_control"]["project_effects_allowed"] is False
