"""Append the reviewed source/candidate facts; never creates an owner decision."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[4]
WORK = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from hub.design_records import with_content_hash, validate_fact
from hub.design_store import DesignStore
from hub.design_service import DesignService

SCOPE = {'family_id': None, 'members': [{'project_id': 'computer-study-plan', 'pages': ['home']}]}
BASE = 'csp-home-isolated-baseline'
CANDIDATE = 'csp-home-quiet-workbench'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build():
    meta = json.loads((WORK / 'figma.json').read_text())
    baseline_input = json.loads((WORK / 'baseline.json').read_text())
    time = meta['prepared_at']
    records = []
    for side, file_prefix, scene_indices in [('original', 'original-overview', [0, 1]), ('quiet', 'candidate', [2, 3])]:
        for view, index in zip(['desktop', 'mobile'], scene_indices):
            path = WORK / (file_prefix + '-' + view + '.png')
            scene = meta['scenes'][index]
            records.append({'schema_version': '1.0', 'kind': 'artifact_ref',
                'id': f'csp-home-{side}-overview-{view}', 'created_at': time, 'classification': 'imported',
                'location': {'kind': 'hub_relative', 'value': str(path.relative_to(ROOT))}, 'sha256': digest(path),
                'provenance': {'method': ('Real UI source isolated browser crop' if side == 'original' else 'Figma native PNG preview') + '; identical synthetic data, not production learning progress',
                    'source_refs': ['docs/reports/ui_design_governance/unit-17/observation.md',
                        meta['file_url'] + '?node-id=' + scene['id'].replace(':', '-'),
                        'editable-node-sha256:' + scene['content_sha256']]}, 'scope': SCOPE})
    source_fingerprint = hashlib.sha256(json.dumps(baseline_input['source']['files'], sort_keys=True).encode()).hexdigest()
    baseline = with_content_hash({'schema_version': '1.0', 'kind': 'baseline', 'id': BASE,
        'created_at': time, 'classification': 'imported', 'project_id': 'computer-study-plan', 'revision': 1, 'content_hash': '',
        'source': {'kind': 'imported', 'reference': 'docs/reports/ui_design_governance/unit-17/observation.md',
            'commit': None, 'dirty_fingerprint': source_fingerprint, 'observed_at': time},
        'scope': {'pages': ['home'], 'flows': ['home-current-task-to-learn'], 'viewport': {'width': 1440, 'height': 937, 'platform': 'desktop'}},
        'behaviors': [{'action_id': 'home-enter-current-task', 'entry': '#homeTaskCard / .home-primary-action',
            'preconditions': ['三个合成任务均未完成，当前任务为认识终端。'], 'input': '点击或键盘 Enter',
            'output': '打开当前任务及合成阅读资料。', 'transition': 'Home → Learn；品牌入口返回 Home。',
            'storage_effects': '可能保存隔离端口界面偏好；零业务 POST。', 'external_effects': '仅隔离服务器合成 GET。',
            'recovery': '资料失败显示错误并可重试；空记录提交被校验阻止。',
            'test_refs': ['docs/reports/ui_design_governance/unit-17/baseline-browser.json', 'docs/reports/ui_design_governance/unit-17/baseline-states.json']}],
        'data_contract_refs': ['docs/reports/ui_design_governance/unit-17/observation.md'],
        'unverified': ['真实界面代码在合成数据下观察；未读取生产学习进度，未验证真实保存、完成、存档或终端执行。'],
        'artifact_bindings': [{'artifact_id': r['id'], 'sha256': r['sha256']} for r in records[:2]]})
    candidate = with_content_hash({'schema_version': '1.0', 'kind': 'candidate', 'id': CANDIDATE,
        'created_at': time, 'classification': 'imported', 'revision': 1, 'content_hash': '', 'scope': SCOPE,
        'baseline_bindings': [{'project_id': 'computer-study-plan', 'baseline_id': BASE, 'baseline_revision': 1,
            'baseline_hash': baseline['content_hash'], 'pages': ['home']}],
        'visual': {'tokens': ['Canvas #151416', 'Surface #211E23', 'Text #F7F0E8', 'Muted #B8ADB6', 'Accent #D8BBA6', 'Border #49414C'],
            'components': ['当前任务主卡', '44px 记录与路线入口', '手机五导航'],
            'structure': ['桌面任务卡与辅助信号并列', '手机任务优先，五导航保留', '任务名称成为视觉中心'],
            'differences': ['安静任务台', '收敛夸张透视，使用正面卡片与柔和阴影', '低饱和杏色强调，记录和模块信息分组', '仅学习计划首页；内容与业务入口保持，示例进度不是真实学习状态', '静态设计不代表已实施；悬停、长按拖动与回位仍为实施保留要求']},
        'figma_ref': {'file_key': meta['file_key'], 'node_id': '13:13', 'version': meta['version'], 'offline': False},
        'artifact_bindings': [{'artifact_id': r['id'], 'sha256': r['sha256']} for r in records[2:]], 'evidence_refs': []})
    return records + [baseline, candidate]


def main():
    records = build()
    for record in records:
        validate_fact(record)
    store = DesignStore(ROOT, 'data/design_governance/design-store.json')
    before = store.read()
    preimage = json.loads((WORK / 'baseline.json').read_text())
    report_path = WORK / 'import.json'
    if not report_path.exists() and digest(store.path) != preimage['design_store_preimage_sha256']:
        raise RuntimeError('Store changed before first import; inspect without overwriting')
    repair_path = WORK / 'name-resolution-repair.json'
    allowed_repair = {}
    if repair_path.exists():
        repair = json.loads(repair_path.read_text())
        for item in repair['changed']:
            if item['path'] != 'src/hub/web/hub.js' or item['before'] != preimage['protected'][item['path']]:
                raise RuntimeError('Unexpected repair scope')
            allowed_repair[item['path']] = item['after']
    protected_errors = [p for p, h in preimage['protected'].items()
                        if digest(ROOT / p) != allowed_repair.get(p, h)]
    if protected_errors:
        raise RuntimeError('Protected file changed: ' + ', '.join(protected_errors))
    receipts = []
    for record in records:
        _, receipt = store.append_fact(record, expected_revision=store.read()['revision'], request_id='tc17-import-' + record['id'])
        receipts.append(receipt)
    after = store.read()
    assert after['facts'][:len(before['facts'])] == before['facts']
    assert after['events'] == before['events'] and len(after['events']) == 1
    service = DesignService(store)
    service.snapshot()
    report = {'status': 'comparison-ready; actual owner selection pending', 'candidate': {k:records[-1][k] for k in ['id', 'revision', 'content_hash']},
        'store_revision': after['revision'], 'facts': len(after['facts']), 'events': len(after['events']),
        'receipts': receipts, 'preserved_protected': len(preimage['protected']),
        'store_sha256': digest(store.path), 'comparison_url': 'http://127.0.0.1:63078/#designs/computer-study-plan/' + CANDIDATE}
    if report_path.exists():
        previous = json.loads(report_path.read_text())
        assert previous['candidate'] == report['candidate']
        previous['replay'] = {'store_unchanged': before == after, 'receipts': receipts}
        report = previous
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k:report[k] for k in ['status', 'store_revision', 'facts', 'events', 'candidate']}))


if __name__ == '__main__':
    main()
