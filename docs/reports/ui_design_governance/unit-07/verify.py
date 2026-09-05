"""Read-only verification of TC7 production evidence; never refresh external sources."""
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
UNIT = Path(__file__).parent
sys.path.insert(0, str(ROOT / 'src'))
from hub.connection_manager_cli import DEFAULT_BUNDLES, load_bundles, result_validator, current_authority_status
from hub.connection_refresh import RefreshLedger
from hub.design_store import DesignStore


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = json.loads((UNIT / 'baseline.json').read_text())
    evidence = json.loads((UNIT / 'runtime-evidence.json').read_text())
    for path, expected in {**baseline['protected_hashes'], **baseline['authority_hashes']}.items():
        assert digest(ROOT / path) == expected, path
    # Executable source is the accepted TC6 Git content; data changed only through accepted APIs.
    changed = subprocess.check_output(['git', 'diff', baseline['head'], '--name-only', '--', 'src', 'tests', 'scripts'], cwd=ROOT, text=True).splitlines()
    assert set(changed) <= set(baseline['protected_hashes']), changed
    bundles, validators = load_bundles(ROOT, list(DEFAULT_BUNDLES))
    assert current_authority_status(ROOT, bundles[-1])['state'] == 'matched'
    authority = validators[bundles[-1]['source_plan']['content_hash']].authority
    assert authority == evidence['active_authority']
    ledger_path = ROOT / 'data/design_governance/connection_refresh.sqlite3'
    before_digest = digest(ledger_path)
    ledger = RefreshLedger(ROOT, result_validator=result_validator(validators), read_only=True)
    history = ledger.history(current_authority=authority)
    projection = ledger.rebuild(current_authority=authority)
    assert history['head'] == projection['head'] == evidence['head_after']
    assert history['events'][81]['event_hash'] == evidence['head_before']['hash']
    rows = [x for x in history['results'] if x['request_id'] == evidence['request_id']]
    assert rows == evidence['refresh_results']
    assert len(rows) == len(projection['projects']) == len(set(evidence['all_24_ids'])) == 24
    assert {x['project_id'] for x in rows} == set(evidence['all_24_ids'])
    counts = dict(Counter(x['result']['disposition'] for x in rows))
    assert counts == evidence['source_dispositions'] == {'SOURCE_RESOLVED': 20, 'EXPLICIT_NO_CURRENT_SOURCE_VERIFIED': 3, 'BLOCKED_BY_AUTHORITY': 1}
    manga = next(x['result'] for x in rows if x['project_id'] == 'manga-localizer')
    assert manga['disposition'] == 'BLOCKED_BY_AUTHORITY' and not manga['sources'] and not manga['evidence']
    assert evidence['manga_path_calls'] == evidence['external_writable_opens'] == evidence['business_process_launches'] == 0
    assert evidence['replay_added_rows'] == evidence['replay_source_reads'] == 0
    assert digest(ledger_path) == before_digest
    store = DesignStore(ROOT, 'data/design_governance/design-store.json')
    data = store.read()
    assert data['store_classification'] == 'real' and data['revision'] == 1
    assert data['facts'] == data['events'] == []
    assert len(data['requests']) == 1 and data['requests'][0]['operation'] == 'initialize'
    assert store.read() == data and digest(store.path) == evidence['design_store']['sha256']
    state = yaml.safe_load((ROOT / 'STATE.yaml').read_text())
    assert state['task_health']['first_250k_rebaseline'] == 'completed'
    assert sum((ROOT / p).stat().st_size for p in ['AGENTS.md', 'STATE.yaml']) <= 8192
    dependencies = json.loads((UNIT / 'dependencies.json').read_text())
    assert dependencies['frozen_total'] == 24
    assert dependencies['authority_blocked'] == ['manga-localizer']
    print(json.dumps({'status': 'PASS', 'projects': 24, 'dispositions': counts, 'head': history['head'], 'design_store': 'real empty revision1', 'protected_and_source_hashes': 'PASS', 'final_acceptance': False}))


if __name__ == '__main__':
    main()
