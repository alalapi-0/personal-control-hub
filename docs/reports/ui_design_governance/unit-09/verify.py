"""Read-only TC9 Manga authority/exception/API verification; never refresh project sources."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[4]
UNIT = Path(__file__).parent
sys.path.insert(0, str(ROOT / 'src'))
from hub.connection_manager_cli import DEFAULT_BUNDLES, load_bundles, result_validator, current_authority_status
from hub.connection_records import validate_collection
from hub.connection_refresh import RefreshLedger
from hub.connections import Connections


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = json.loads((UNIT / 'baseline.json').read_text())
    decision = json.loads((UNIT / 'owner-decision.json').read_text())
    expected_ids = {'desktop-downloads-scripts', 'desktop-magnet', 'pycharm-misc-project', 'manga-localizer'}
    assert decision['project_ids'] == ['manga-localizer']
    assert decision['quote'] == '在 Codex 中重新授权 Figma 插件写入权限。批准可见接入例外。' and decision['decision'] == 'AUTHORIZED_EXCEPTION'
    for path, expected in {**baseline['protected_hashes'], **baseline['historical_hashes']}.items():
        assert digest(ROOT / path) == expected, path
    def preimage(path):
        raw = subprocess.check_output(['git', 'show', baseline['head'] + ':' + path], cwd=ROOT)
        assert hashlib.sha256(raw).hexdigest() == baseline['owned_preimage_hashes'][path], path
        return raw
    registry_path = 'data/registry/external_projects.yaml'
    registry = yaml.safe_load((ROOT / registry_path).read_text())
    before_registry = yaml.safe_load(preimage(registry_path))
    stripped = copy.deepcopy(registry)
    exceptions = {}
    for project in stripped['projects']:
        if 'hub_connection_exception' in project:
            exceptions[project['id']] = project.pop('hub_connection_exception')
    assert set(exceptions) == expected_ids
    for project in stripped['projects']:
        if project['id'] != 'manga-localizer' and project['id'] in exceptions:
            project['hub_connection_exception'] = exceptions[project['id']]
    assert stripped == before_registry
    for declaration in [exceptions['manga-localizer']]:
        assert declaration['authority_ref'] == decision['authority_ref']
        assert declaration['reason'] == decision['reason']
        assert declaration['status'] == 'AUTHORIZED_EXCEPTION'
        assert declaration['decision_ref'] == str((UNIT / 'owner-decision.json').relative_to(ROOT))
    storage_path = 'governance/programs/storage_governance/STATE.yaml'
    storage = yaml.safe_load((ROOT / storage_path).read_text())
    before_storage = yaml.safe_load(preimage(storage_path))
    assert storage['parent_hub']['project_registry_sha256_current'] == digest(ROOT / registry_path)
    storage['parent_hub']['project_registry_sha256_current'] = before_storage['parent_hub']['project_registry_sha256_current']
    assert storage == before_storage
    data = ROOT / 'data/design_governance'
    manifest = json.loads((data / 'manifest-v5.json').read_text())
    adapters = json.loads((data / 'connection_adapters.json').read_text())
    projection = json.loads((data / 'connection-exceptions-v2.json').read_text())
    snapshots = [x for x in projection['records'] if x['record_type'] == 'project_snapshot']
    evidence = [x for x in projection['records'] if x['record_type'] == 'connection_evidence']
    assert len(snapshots) == len(evidence) == 4
    assert {x['project_id'] for x in snapshots} == {x['project_id'] for x in evidence} == expected_ids
    with mock.patch('hub.connections.bounded_read', side_effect=AssertionError('source read forbidden')), \
         mock.patch('hub.connections.resolve_named_source', side_effect=AssertionError('source probe forbidden')), \
         mock.patch('hub.connection_records.resolve_named_source', side_effect=AssertionError('source probe forbidden')):
        valid = validate_collection([manifest] + projection['records'], registry, digest(ROOT / registry_path), adapters)
        connection = Connections(ROOT, manifest, adapters)
        for snapshot in snapshots:
            regenerated = connection.refresh(snapshot['project_id'])
            for field in ['created_at', 'observed_at']:
                regenerated[field] = snapshot[field]
            assert regenerated == snapshot
            assert snapshot['normalized_status'] == 'unknown'
            assert snapshot['availability'] == ('blocked_by_authority' if snapshot['project_id'] == 'manga-localizer' else 'source_not_declared')
            assert not snapshot['sources'] and snapshot['last_success_at'] is None
    for item in evidence:
        assert item['status'] == 'AUTHORIZED_EXCEPTION' and item['ui_verification'] == 'UNVERIFIED'
        assert item['authority_ref'] == exceptions[item['project_id']]['authority_ref'] and item['exit_code'] == 2
    bundles, validators = load_bundles(ROOT, list(DEFAULT_BUNDLES))
    assert len(bundles) == 4 and current_authority_status(ROOT, bundles[-1])['state'] == 'matched'
    assert bundles[-1]['manifest'] == manifest
    assert bundles[-1]['source_plan'] == json.loads((data / 'source-plan-v4.json').read_text())
    old_relations = json.loads((data / 'relation-proposals-v4.json').read_text())
    relations = json.loads((data / 'relation-proposals-v4.json').read_text())
    assert relations['relations'] == old_relations['relations']
    old_manifest = json.loads((data / 'manifest-v4.json').read_text())
    normalized = copy.deepcopy(manifest)
    for key in ('id', 'revision', 'created_at', 'registry_ref', 'content_hash'):
        normalized[key] = old_manifest[key]
    for old_entry, entry in zip(old_manifest['entries'], normalized['entries']):
        for key in ('id', 'created_at'):
            entry['scope'][key] = old_entry['scope'][key]
    assert normalized == old_manifest
    previous_plan = json.loads((data / 'source-plan-v3.json').read_text())
    assert bundles[-1]['source_plan']['entries'] == previous_plan['entries']
    ledger = RefreshLedger(ROOT, result_validator=result_validator(validators), read_only=True)
    history = ledger.history()
    assert len(history['events']) == 108 and len(history['results']) == 98 and len(history['requests']) == 5
    # Reuse the accepted HTTP readback with its no-refresh and Hub-only read guards.
    spec = importlib.util.spec_from_file_location('tc4_readback', ROOT / 'docs/reports/ui_design_governance/unit-04/readback.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        module.main()
    api = json.loads(output.getvalue())
    assert api['project_count'] == 24 and api['list_detail_equal'] and api['design_available']
    for row in api['projects']:
        declared = row['declared']['hub_connection_exception']
        assert declared == exceptions.get(row['project_id'])
        if declared:
            assert row['business']['normalized_status'] == 'unknown'
            assert row['operational']['latest_attempt']['disposition'] == ('BLOCKED_BY_AUTHORITY' if row['project_id'] == 'manga-localizer' else 'EXPLICIT_NO_CURRENT_SOURCE_VERIFIED')
    deps = json.loads((ROOT / 'docs/reports/ui_design_governance/unit-07/dependencies.json').read_text())
    before_deps = json.loads(preimage('docs/reports/ui_design_governance/unit-07/dependencies.json'))
    assert deps['authority_blocked'] == [] and deps['manga']['state'] == 'owner_visible_exception_approved'
    assert deps['manga']['probes'] == 0 and deps['manga']['access'] == 'no_current_goal_access'
    assert deps['manga']['decision_ref'] == '../unit-09/owner-decision.json'
    deps['manga'] = before_deps['manga']; deps['authority_blocked'] = before_deps['authority_blocked']
    assert deps == before_deps
    state = yaml.safe_load((ROOT / 'STATE.yaml').read_text())
    assert state['current_work']['unit'] == 'HUB-09-MANGA-EXCEPTION'
    assert 'Figma' in state['current_work']['blocker'] and 'Manga' not in state['current_work']['blocker']
    assert sum((ROOT / p).stat().st_size for p in ['AGENTS.md', 'STATE.yaml']) <= 8192
    for path, expected in {**baseline['protected_hashes'], **baseline['historical_hashes']}.items():
        assert digest(ROOT / path) == expected, path
    print(json.dumps({'status': 'PASS', 'exceptions': sorted(expected_ids), 'records': valid,
                      'all_projects': 24, 'api_list_detail_equal': True, 'business_status': 'unknown',
                      'history_events': 108, 'history_results': 98, 'history_requests': 5,
                      'project_source_reads': 0, 'manga_probes': 0, 'ledger_mutations': 0,
                      'protected_and_historical_hashes': 'PASS', 'ui_verification': 'UNVERIFIED',
                      'final_goal_acceptance': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
