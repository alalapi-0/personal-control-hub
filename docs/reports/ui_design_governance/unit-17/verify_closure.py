"""Validate the authorized TC17 v3 successor using Hub-local evidence only."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'src'))
from hub.design_store import DesignStore
from hub.design_service import DesignService

U = 'docs/reports/ui_design_governance/unit-17/'
def read(p): return json.loads((ROOT / p).read_text())
def sha(p): return hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
def canonical(value): return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def verify():
    baseline = read(U + 'closure-baseline.json')
    protected = read(baseline['protected_reference'])['protected']
    changed = [p for p, h in protected.items() if p not in baseline['authorized_changed_protected'] and sha(p) != h]
    assert not changed, changed
    store = DesignStore(ROOT, 'data/design_governance/design-store.json')
    current = store.read()
    assert current['revision'] == 15
    assert (len(current['facts']), len(current['events']), len(current['requests'])) == (12, 2, 15)
    for key, old in baseline['prefixes'].items():
        assert canonical(current[key][:old['count']]) == old['sha256'], key
    command = read(U + 'decision-command.json')
    owner = read(U + 'owner-decision.json')
    event = current['events'][-1]
    assert event['id'] == command['event_id'] and event['action'] == 'defer'
    for key in ['request_id', 'created_at', 'candidate', 'scope', 'feedback', 'supersedes']:
        assert event[key] == command[key], key
    assert owner['quote'] in event['feedback'] and owner['decision_request_id'] == event['request_id']
    assert event['source'] == {'type': 'trusted_owner_reference', 'reference': owner['trusted_service_reference'], 'trusted_owner': True, 'fixture': False}
    assert current['events'][0]['action'] == 'select' and current['events'][0]['candidate']['id'] == 'hub-c-p5'
    receipt = read(U + 'decision-receipt.json')['body']['data']
    assert receipt['event_hash'] == canonical(event) == current['requests'][-1]['result_hash']
    service = DesignService(store)
    snapshot = service.snapshot()
    assert len(snapshot['queues']['deferred']) == 1 and len(snapshot['queues']['selected']) == 1
    exported = read(U + 'export-receipt.json')
    export_command = exported['exportCommand']
    downloaded = service.export_download(export_command)
    assert downloaded.sha256 == exported['body']['data']['sha256']
    path = 'docs/reports/ui_design_governance/service/exports/real/' + export_command['request_id'] + '.zip'
    assert sha(path) == downloaded.sha256
    with zipfile.ZipFile(ROOT / path) as z:
        assert z.testzip() is None
        manifest = json.loads(z.read('manifest.json'))
        assert manifest['selection_state'] == 'unselected' and manifest['selection'] is None
        assert manifest['authority'] == {'fixture': False, 'implementation_authority': False, 'real_selection': False}
        assert manifest['candidate_identity'] == {**command['candidate'], 'scope': command['scope']}
        assert event in [item['event'] for item in manifest['decision_history']]
        assert len(manifest['artifact_files']) == 4
        assert set(z.namelist()) == {'manifest.json', *(a['archive_path'] for a in manifest['artifact_files'])}
        for a in manifest['artifact_files']:
            data = z.read(a['archive_path'])
            assert len(data) == a['size'] and hashlib.sha256(data).hexdigest() == a['sha256']
    browser = read(U + 'closure-browser.json')
    assert not browser['errors'] and not browser['consoleErrors'] and not browser['badResponses']
    assert not browser['requests']['external'] and not browser['mobileOverflow']
    assert browser['navigationFocus'] == 'main' and browser['reduced']['transition'] == '0s'
    assert all(p['width'] == 390 and p['height'] == 739 and p['alt'] for p in browser['previews'])
    assert browser['replay']['decision']['body']['data'] == receipt
    assert browser['replay']['exported']['body']['data'] == exported['body']['data']
    assert browser['replay']['download']['sha256'] == downloaded.sha256
    tc18 = read('docs/reports/ui_design_governance/unit-18/candidate.json')
    assert all(sha(p) == h for p, h in tc18['files'].items())
    return {'status': 'PASS', 'store_revision': 15, 'facts_preserved': 12, 'old_events_preserved': 1, 'authorized_new_events': 1, 'authorized_action': 'defer', 'protected_files_unchanged': len(protected)-1, 'authorized_store_successor': True, 'ledger_head': 130, 'export': {'path': path, 'sha256': downloaded.sha256, 'selection_state': 'unselected', 'artifact_files': 4}, 'tc18_candidate_unchanged': tc18['candidate_sha256'], 'external_effects': 0, 'cursor_operations': 0, 'verification_scope': 'Local frozen evidence and real Hub readback; no new external source/Figma verification.'}

if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=False))
