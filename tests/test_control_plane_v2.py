from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(relative: str):
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def test_default_boot_packet_is_bounded():
    total = sum((ROOT / name).stat().st_size for name in ("AGENTS.md", "STATE.yaml"))
    assert total <= 8192


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
    assert project["root_path"] == "~/Documents/StorageGovernance"
    assert project["authority_files"] == ["STORAGE_GOVERNANCE.md"]
    assert project["external_write_allowed"] is False
    assert project["norm_copy_forbidden"] is True
    assert "/Users/" not in str(project)


def test_storage_adapter_does_not_copy_or_override_norm():
    adapter = load_yaml("governance/adapters/storage_governance.yaml")
    assert adapter["authority"]["sole_norm"] == "~/Documents/StorageGovernance/STORAGE_GOVERNANCE.md"
    assert adapter["authority"]["content_copied_into_hub"] is False
    assert adapter["authority"]["hub_may_override_norm"] is False
    assert adapter["boundaries"]["external_project_writes_from_hub"] is False
