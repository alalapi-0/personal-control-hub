"""Read-only TC18 evidence validation. Never opens any registered project root."""
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[4]
UNIT = 'docs/reports/ui_design_governance/unit-18/'

def local(path):
    p = Path(path)
    assert not p.is_absolute() and '..' not in p.parts, ('nonlocal reference', path)
    target = ROOT / p
    assert target.resolve().is_relative_to(ROOT), ('escaped reference', path)
    return target

def sha(path):
    return hashlib.sha256(local(path).read_bytes()).hexdigest()

def read(path):
    return json.loads(local(path).read_text())

def verify():
    d = read('data/design_governance/scope-resolution-v1.json')
    baseline = read(UNIT + 'baseline.json')
    assert d['id'] == 'hub-ui-scope-resolution-v1' and d['contract'] == 'TC18 v1'
    bindings = d['bindings']
    for name, binding in bindings.items():
        assert sha(binding['path']) == binding['sha256'], ('binding drift', name)
    registry = yaml.safe_load(local(bindings['registry']['path']).read_text())
    projects = {p['id']: p for p in registry['projects']}
    rows = d['projects']
    ids = [r['project_id'] for r in rows]
    assert len(ids) == len(set(ids)) == len(projects) == 24
    assert set(ids) == set(projects)
    inventory = {p['project_id']: p for p in read(bindings['discovery_inventory']['path'])['projects']}
    manifest = read(bindings['manifest']['path'])
    assert manifest['id'] == bindings['manifest']['id'] == 'hub-connections-v5'
    assert manifest['content_hash'] == bindings['manifest']['content_hash']
    assert manifest['registry_ref']['sha256'] == bindings['registry']['sha256']
    bundle = read(bindings['authority_bundle']['path'])
    assert bundle['manifest'] == manifest
    assert bundle['content_hash'] == bindings['authority_bundle']['content_hash']
    outcomes = read(bindings['tc16_outcomes']['path'])
    coverage = {p['project_id']: p for p in outcomes['coverage']}
    assert len(coverage) == 24 and outcomes['post_head']['sequence'] == 130
    assert Counter(p['outcome'] for p in coverage.values()) == {'CURRENT_AUTHORITY_REFRESH': 20, 'AUTHORIZED_EXCEPTION': 4}
    approval = read(bindings['tc16_acceptance']['path'])
    assert approval['status'] == 'APPROVE'
    assert approval['candidate_sha256'] == '78b4221f82d49dd48397fbb47f0e74d2111ddf15540c33f8d357f24e849ce3a0'
    scopes = Counter(r['ui_disposition'] for r in rows)
    assert scopes == {'in_scope': 13, 'no_ui': 8, 'protected': 3}
    assert d['counts'] == {v: scopes[v] for v in d['definitions']}
    source_keys = set()
    for r in rows:
        pid = r['project_id']
        assert r['discovery_proposal'] == inventory[pid]['proposed_ui_disposition']
        assert r['ui_disposition'] in d['definitions']
        assert r['meaning'] == d['definitions'][r['ui_disposition']]
        assert r['rationale'] and r['protection_and_recovery'] and r['runtime_scope']
        assert r['external_implementation_required'] is False
        assert r['runtime_verified'] is (pid in {'personal-control-hub', 'computer-study-plan'})
        assert r['management_outcome'] == {'evidence_binding': 'tc16_outcomes', **coverage[pid]}
        assert all(key in bindings for key in r['evidence_bindings'])
        if r['ui_disposition'] == 'protected':
            assert pid in {'storage_governance', 'manga-localizer', 'pycharm-agent-workspace'}
            assert not r['sources'] and r['basis_type'] in {'current_policy', 'owner_decision'}
            continue
        assert r['basis_type'] == 'current_exact_cited_source_identity_and_structure' and r['sources']
        allowed = {re.sub(r':\d+$', '', s) for s in inventory[pid]['source_refs']}
        if pid == 'personal-control-hub':
            allowed.add('src/hub/web/index.html')
            assert r['new_source_basis']
        for s in r['sources']:
            path = s['relative']
            assert path in allowed and '..' not in Path(path).parts and not Path(path).is_absolute()
            assert re.fullmatch('[0-9a-f]{64}', s['sha256']) and 0 < s['bytes'] <= 1_000_000
            assert datetime.fromisoformat(s['observed_at']).tzinfo and s['structural_evidence']
            key = (pid, path)
            assert key not in source_keys
            source_keys.add(key)
    by_id = {r['project_id']: r for r in rows}
    assert by_id['desktop-tool']['proposal_resolution'] and by_id['desktop-tool']['discovery_proposal'] == 'deferred'
    assert len(by_id['desktop-tool']['sources']) == 2
    manga = projects['manga-localizer']
    assert manga['access_profile'] == 'no_current_goal_access' and not manga['enabled']
    assert not manga['scan_enabled'] and not manga['profile_enabled'] and manga['watch_paths'] == []
    assert all(manga['storage_governance'][k] is False for k in ['inventory_allowed', 'inspection_allowed', 'validation_allowed', 'mutation_allowed'])
    decision = read(bindings['manga_owner_decision']['path'])
    assert decision['project_ids'] == ['manga-localizer'] and decision['decision'] == 'AUTHORIZED_EXCEPTION'
    assert manga['hub_connection_exception']['authority_ref'] == decision['authority_ref']
    storage = projects['storage_governance']
    assert storage['project_type'] == 'governance_program' and storage['access_profile'] == 'named_control_plane_files_only'
    assert not storage['storage_governance']['migration_allowed'] and not storage['storage_governance']['cleanup_allowed']
    workspace = projects['pycharm-agent-workspace']
    assert workspace['project_type'] == 'non_git_shared_development_tooling'
    assert workspace['access_profile'] == 'registered_project_read_with_protected_config'
    boundary = d['observation_boundary']
    assert boundary['unique_source_files'] == len(source_keys) == 25
    assert boundary['source_read_operations'] == 30 and boundary['external_source_read_operations'] == 29
    for key in ['external_directory_scans', 'external_writes', 'external_launches', 'business_data_files_read', 'credential_files_read', 'manga_path_operations', 'runtime_launches_this_unit']:
        assert boundary[key] == 0
    changed = [path for path, expected in baseline['protected'].items() if sha(path) != expected]
    assert not changed, ('protected drift', changed)
    return {'status': 'PASS', 'projects': 24, 'ui_counts': dict(scopes), 'source_files': len(source_keys), 'bindings': len(bindings), 'protected_files_unchanged': len(baseline['protected']), 'validation_external_reads': 0, 'validation_external_writes': 0, 'tc16_ledger_head': 130, 'tc17_preserved': True, 'acceptance': 'requires fresh Judge and Governor; does not select TC17 design'}

if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=False))
