"""Read-only TC6 cutover, preservation and authority verification; no project probes."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
UNIT = Path(__file__).parent
sys.path.insert(0, str(ROOT / 'src'))
from hub.connection_manager_cli import DEFAULT_BUNDLES, load_bundles, result_validator, current_authority_status, load_relations
from hub.connection_refresh import RefreshLedger


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    baseline = json.loads((UNIT / 'baseline.json').read_text())
    cutover = json.loads((UNIT / 'cutover.json').read_text())
    program = ROOT / 'governance/programs/storage_governance'
    for row in cutover['old_source_links']:
        source, target = Path(row['path']), Path(row['target'])
        assert source.is_symlink() and source.resolve() == target
        assert target.parent == program and target.is_file() and not target.is_symlink()
        assert sha(target.read_bytes()) == row['target_sha256']
        assert source.read_bytes() == target.read_bytes()
    wp = baseline['workspace_hub_paragraph']
    raw = Path(wp['path']).read_bytes()
    pointer = cutover['workspace_pointer'].encode()
    assert raw.count(pointer) == 1 and wp['paragraph'].encode() not in raw
    assert sha(raw.replace(pointer, b'')) == wp['other_sections_sha256']
    assert wp['paragraph'] in (ROOT / 'docs/archive/workspace_hub_inventory_20260812.md').read_text()
    for path, expected in baseline['protected_global_hashes'].items():
        if path == 'AGENTS.md':
            original = baseline['dirty_router_preimage']
            expected_router = original.replace('再按适配器进入外部 router 与唯一执行 `STATE.yaml`', '再按适配器进入专用 router 与唯一执行 `STATE.yaml`')
            assert (ROOT / path).read_text() == expected_router
        else:
            assert sha((ROOT / path).read_bytes()) == expected, path
    assert sha((ROOT / 'docs/global_agent_governance_execution.md').read_bytes()) == baseline['other_global_document_sha256']
    assert sha(Path('/Users/alalapi/.config/storage-governance/guard.sh').read_bytes()) == baseline['guard_sha256']
    mapping = Path('/Volumes/AI_WORK_SSD/_governance/STORAGE_MAP.yaml')
    assert sha(mapping.read_bytes()) == baseline['storage_map_sha256']
    assert '/Users/alalapi/Documents/StorageGovernance' in mapping.read_text()
    # Historical bundles and accepted inventory/provenance are immutable.
    for path in ['data/design_governance/authority-bundle-v1.json', 'data/design_governance/manifest-v2.json', 'data/design_governance/source-plan-v1.json', 'data/design_governance/relation-proposals-v1.json']:
        previous = subprocess.check_output(['git', 'show', baseline['head'] + ':' + path], cwd=ROOT)
        assert (ROOT / path).read_bytes() == previous, path
    state = yaml.safe_load((program / 'STATE.yaml').read_text())
    hub = yaml.safe_load((ROOT / 'STATE.yaml').read_text())
    assert (ROOT / hub['storage_governance']['sole_execution_state']).resolve() == program / 'STATE.yaml'
    assert 'compact_execution_summary' not in hub['storage_governance']
    assert set(state['authorities'].values()) == {'closed'}
    assert state['authorization_scope']['allowed_effects'] == []
    assert state['authorization_scope']['remaining_epoch_capability_authority'] == 'closed'
    for key in ('active_projects', 'active_batches', 'active_writers'):
        assert state['execution_control'][key] == state['closure'][key] == 0
    assert state['closure']['pending_projects'] == 0 and state['closure']['unconsumed_effect_authority'] == 'none'
    assert state['current_project']['id'] is None and state['current_batch']['id'] is None
    assert state['current_batch']['effect_authority'] == state['current_batch']['source_deletion'] == 'closed'
    assert state['execution_control']['native_goal'] == 'not_asserted_by_file'
    assert state['project_accounting']['released_bytes'] is None
    assert state['project_accounting']['external_added_bytes'] is None
    registry_bytes = (ROOT / 'data/registry/external_projects.yaml').read_bytes()
    assert sha(registry_bytes) == state['parent_hub']['project_registry_sha256_current']
    bundles, validators = load_bundles(ROOT, list(DEFAULT_BUNDLES))
    assert current_authority_status(ROOT, bundles[-1])['state'] == 'matched'
    authority = validators[bundles[-1]['source_plan']['content_hash']].authority
    ledger = RefreshLedger(ROOT, result_validator=result_validator(validators), read_only=True)
    history = ledger.history(current_authority=authority)
    projection = ledger.rebuild(current_authority=authority)
    refresh = json.loads((UNIT / 'refresh.json').read_text())
    assert history['head'] == projection['head'] == refresh['head_after']
    assert len(projection['projects']) == 24
    assert history['events'][77]['event_hash'] == refresh['head_before']['hash']
    assert projection['projects']['manga-localizer']['latest_attempt']['disposition'] == 'BLOCKED_BY_AUTHORITY'
    for pid in ['storage_governance', 'personal-control-hub']:
        assert projection['projects'][pid]['freshness'] == 'fresh'
    relations = load_relations(ROOT)
    old = json.loads((ROOT / 'data/design_governance/relation-proposals-v1.json').read_text())
    assert old['relations'] == relations['collection']['relations']
    print(json.dumps({'cutover': 'PASS', 'one_editable_storage_state': True, 'workspace_and_global_preservation': 'PASS', 'historical_authorities': 'PASS', 'source_state_closed': True, 'project_count': 24, 'ledger_head': history['head'], 'relation_semantics_preserved': True, 'manga_probes': 0, 'external_business_reads': 0}))


if __name__ == '__main__':
    main()
