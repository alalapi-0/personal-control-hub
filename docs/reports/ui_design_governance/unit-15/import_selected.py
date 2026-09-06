"""Idempotent append of the owner's exact C/P5 selection; no external reads."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'src'))
from hub.design_records import with_content_hash
from hub.design_store import DesignStore
from hub.design_service import DesignService

WORK = ROOT / 'docs/reports/ui_design_governance/unit-15'
TIME = '2026-09-06T03:37:40+00:00'
REFERENCE = 'codex-task://01a07442-c275-7270-bd2e-98b50f277bd0/owner-C-then-P5'
PAGES = ['design-compare', 'design-list', 'project-detail', 'projects', 'states']
SCOPE = {'family_id': None, 'members': [{'project_id': 'personal-control-hub', 'pages': PAGES}]}
SELECTED = 'a0c70f35f896142e8224c3fd8149b81c9bdfef8530845446943948da740fba81'


def build():
    exports = json.loads((ROOT / 'docs/reports/ui_design_governance/unit-14/exports.json').read_text())['exports']
    records = []
    for row in exports:
        if not row['key'].startswith('P5/') or row['key'].endswith('/full'):
            continue
        digest = hashlib.sha256((ROOT / row['path']).read_bytes()).hexdigest()
        if digest != row['png']['sha256']:
            raise RuntimeError('Accepted preview identity changed')
        records.append({'schema_version': '1.0', 'kind': 'artifact_ref',
            'id': 'hub-c-p5-' + row['key'][3:].replace('/', '-'), 'created_at': TIME,
            'classification': 'imported', 'location': {'kind': 'hub_relative', 'value': row['path']},
            'sha256': digest, 'provenance': {'method': 'Figma native export; synthetic sample content; TC14 ' + SELECTED,
                'source_refs': ['https://www.figma.com/design/AjYCtyxV5mmXNqWPtJQRQ4?node-id=' + row['id'].replace(':', '-'), 'node-content-sha256:' + row['content_sha256']]}, 'scope': SCOPE})
    if len(records) != 4:
        raise RuntimeError('Expected four selected C/P5 previews')
    baseline = with_content_hash({'schema_version': '1.0', 'kind': 'baseline', 'id': 'hub-before-graphical-ui',
        'created_at': TIME, 'classification': 'imported', 'project_id': 'personal-control-hub',
        'revision': 1, 'content_hash': '', 'source': {'kind': 'new_surface_spec',
        'reference': 'docs/design/personal_control_hub_completion.md; creation-time baseline: no graphical Hub',
        'commit': 'f21b4cfa63ad2e849546523d2d8ac5082f72dd83', 'dirty_fingerprint': None, 'observed_at': TIME},
        'scope': {'pages': PAGES, 'flows': ['projects-detail-refresh-design-compare-decide-export'],
            'viewport': {'width': 1440, 'height': 900, 'platform': 'desktop'}},
        'behaviors': [], 'data_contract_refs': ['docs/design/hub_local_service_protocol.md'],
        'unverified': ['创建前无图形界面；候选截图使用示例内容，不是实际项目状态。'], 'artifact_bindings': []})
    candidate = with_content_hash({'schema_version': '1.0', 'kind': 'candidate', 'id': 'hub-c-p5',
        'created_at': TIME, 'classification': 'imported', 'revision': 1, 'content_hash': '', 'scope': SCOPE,
        'baseline_bindings': [{'project_id': 'personal-control-hub', 'baseline_id': baseline['id'],
            'baseline_revision': 1, 'baseline_hash': baseline['content_hash'], 'pages': PAGES}],
        'visual': {'tokens': ['canvas #24172D', 'surface #35243E', 'text #FFF2EA', 'muted #CEB6D5',
            'border #927A9A', 'accent #FFBD95', 'tint #57384B', 'warning #F9DB86', 'success #99D4B3', 'onaccent #3B2132'],
            'components': ['阅读式项目条目', '桌面对照画布', '手机单画布', '44px 操作按钮'],
            'structure': ['C 柔和工作室', '桌面 1056px 阅读列', '手机 24px 边距'],
            'differences': ['C · 暮紫杏光', '深暮紫背景与暖杏色强调', '保留 C 排版；示例内容仅用于比较设计']},
        'figma_ref': {'file_key': 'AjYCtyxV5mmXNqWPtJQRQ4', 'node_id': '91:2723', 'version': 'TC14-' + SELECTED, 'offline': False},
        'artifact_bindings': [{'artifact_id': r['id'], 'sha256': r['sha256']} for r in records], 'evidence_refs': []})
    event = {'schema_version': '1.0', 'kind': 'decision_event', 'id': 'owner-hub-c-p5-selection',
        'request_id': 'tc15-owner-c-p5-select', 'created_at': TIME,
        'source': {'type': 'trusted_owner_reference', 'reference': REFERENCE, 'trusted_owner': True, 'fixture': False},
        'action': 'select', 'candidate': {k:candidate[k] for k in ('id', 'revision', 'content_hash')},
        'scope': SCOPE, 'feedback': '先选择 C 排版；本次明确选择「5 暮紫杏光」。此事件记录精确组合设计选择；Hub 实施依据当前 Goal 的既有授权。', 'supersedes': None}
    return records + [baseline, candidate], event


def main():
    store = DesignStore(ROOT, 'data/design_governance/design-store.json')
    facts, event = build()
    before = store.read()
    expected_ids = {x['id'] for x in facts}
    if any(x['id'] not in expected_ids for x in before['facts']) or any(x['id'] != event['id'] for x in before['events']):
        raise RuntimeError('Unexpected concurrent store data; preserve and inspect')
    receipts = []
    for fact in facts:
        _, receipt = store.append_fact(fact, expected_revision=store.read()['revision'], request_id='tc15-import-' + fact['id'])
        receipts.append(receipt)
    _, receipt = store.append_decision(event, expected_revision=store.read()['revision'], trusted_owner_reference=REFERENCE)
    receipts.append(receipt)
    after = store.read()
    command = {'request_id': 'tc15-selected-c-p5-export', 'expected_revision': after['revision'], 'candidate': event['candidate']}
    exported = DesignService(store).export(command)
    report = {'selection_authority': {'layout': 'C', 'palette': '5 暮紫杏光', 'reference': REFERENCE,
        'recorded_at': TIME, 'exact_message_timestamp': 'not available; recorded_at is import time', 'accepted_tc14': SELECTED},
        'facts': len(after['facts']), 'events': len(after['events']), 'store_revision': after['revision'],
        'candidate': event['candidate'], 'receipts': receipts, 'export_command': command, 'export': exported}
    (WORK / 'import.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'facts':len(after['facts']),'events':len(after['events']),'revision':after['revision'],'export_outcome':exported['outcome']}))

if __name__ == '__main__': main()
