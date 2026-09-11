import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from hub.connection_records import validate_declaration
from hub.connection_sources import SourceResolver
from hub.metric_import import read_metric_snapshot
from hub.metric_hub_activity import collect_hub_activity
from hub.metric_store import MetricStore

NOW = '2026-09-09T00:00:00Z'
ROOT = Path(__file__).resolve().parents[1]


class HubActivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = MetricStore(self.root)
        with self.store._write() as db:
            db.execute('INSERT INTO metric_requests VALUES(?,?,?)', ('PRIVATE', 'identity', '["p","q"]'))
            db.execute('INSERT INTO metric_receipts VALUES(?,?,?)', ('PRIVATE', 'p', json.dumps({'disposition':'partial','observed_at':NOW})))
            db.execute('INSERT INTO metric_projects VALUES(?,?,?)', ('p', NOW, '{"issues":[{"body":"SECRET"}],"source_ref":"SECRET"}'))
            db.execute('INSERT INTO metric_changes VALUES(?,?,?,?,?,?)', (1, 'p', 'key', 'version', NOW, '{"quality":"good","value":3,"source_ref":"SECRET"}'))
            db.execute('INSERT INTO metric_current VALUES(?,?,?,?)', ('key', 'p', 1, NOW))

    def collect(self):
        result = collect_hub_activity(self.root, 'hub', NOW, {'root':'/SECRET','path':'/SECRET'})
        return result, {r['metric_id'].split('.',1)[1]:r for r in result['metrics']}

    def test_coverage_and_privacy(self):
        result, rows = self.collect()
        self.assertEqual(rows['complete_saved_requests']['value'], 0)
        self.assertEqual(rows['incomplete_saved_requests']['value'], 1)
        self.assertEqual(rows['current_numeric_metrics']['value'], 1)
        self.assertEqual(rows['service_observed_issues']['value'], 1)
        self.assertNotIn('SECRET',json.dumps(result))
        self.assertNotIn('PRIVATE',json.dumps(result))
        with self.store._write() as db:
            db.execute('INSERT INTO metric_receipts VALUES(?,?,?)', ('PRIVATE','q','{"disposition":"failed"}'))
        _, rows = self.collect()
        self.assertEqual(rows['complete_saved_requests']['value'],1)
        self.assertIsNone(rows['saved_requests']['business_at'])
        self.assertIsNone(rows['complete_saved_requests']['business_at'])
        self.assertEqual(rows['covered_projects']['business_at'],NOW)
        self.assertEqual(rows['current_numeric_metrics']['business_at'],NOW)
        self.assertEqual(rows['saved_metric_versions']['business_at'],NOW)

    def test_duplicate_missing_and_extra_coverage(self):
        for projects in ('["p","p"]','[""]','{}','not json','[]'):
            with self.store._write() as db:
                db.execute('UPDATE metric_requests SET projects=?',(projects,))
            result, rows = self.collect()
            self.assertIsNone(rows['complete_saved_requests']['value'])
            self.assertEqual(rows['saved_requests']['value'],1)
            self.assertEqual(result['disposition'],'partial')
        with self.store._write() as db:
            db.execute('UPDATE metric_requests SET projects=?',('["q"]',))
        self.assertEqual(self.collect()[1]['incomplete_saved_requests']['value'],1)

    def test_malformed_isolation(self):
        with self.store._write() as db:
            db.execute('UPDATE metric_projects SET result=?',('not json',))
        _, rows = self.collect()
        self.assertIsNone(rows['service_observed_issues']['value'])
        self.assertEqual(rows['current_numeric_metrics']['value'],1)
        with self.store._write() as db:
            db.execute('UPDATE metric_changes SET value=?',('{"quality":"good","value":true}',))
        # SQLite extracts JSON booleans as ints; their JSON type must be checked.
        self.assertEqual(self.collect()[1]['current_numeric_metrics']['value'],0)
        self.assertEqual(self.collect()[1]['malformed_current_metric_records']['value'],1)

    def test_read_only_and_versions(self):
        before = self.store.path.read_bytes()
        first, base = self.collect()
        self.assertEqual(before,self.store.path.read_bytes())
        with self.store._write() as db:
            db.execute('UPDATE metric_projects SET result=?',('{"issues":[{"body":"CHANGED"}]}',))
        second, _ = self.collect()
        self.assertEqual(first['source_version'],second['source_version'])
        with self.store._write() as db:
            db.execute('UPDATE metric_requests SET identity=?',('new',))
        third, changed = self.collect()
        self.assertNotEqual(second['source_version'],third['source_version'])
        self.assertEqual(base['current_numeric_metrics']['source_version'],changed['current_numeric_metrics']['source_version'])

    def test_bounds_and_missing_schema(self):
        with patch('hub.metric_hub_activity.MAX_BYTES', 1):
            result, rows = self.collect()
        self.assertEqual(rows['saved_requests']['value'],1)
        self.assertIsNone(rows['current_numeric_metrics']['value'])
        with patch('hub.metric_hub_activity.MAX_ROWS', 0):
            self.assertIsNone(self.collect()[1]['complete_saved_requests']['value'])
        with tempfile.TemporaryDirectory() as missing:
            result = collect_hub_activity(Path(missing),'hub',NOW,{})
            self.assertEqual(result['disposition'],'partial')
            self.assertFalse(list(Path(missing).rglob('*')))
        with self.store._write() as db:
            db.execute('DROP TABLE metric_receipts')
        self.assertEqual(self.collect()[0]['disposition'],'partial')

    def test_identity_replacement_and_time_isolation(self):
        _, before = self.collect()
        self.assertEqual(before['saved_receipts']['business_at'], NOW)
        with self.store._write() as db:
            db.execute("UPDATE metric_current SET key='replacement', observed_at='bad time'")
            db.execute("UPDATE metric_projects SET project_id='other'")
        result, after = self.collect()
        self.assertEqual(after['current_metric_records']['value'],1)
        self.assertIsNone(after['current_metric_records']['business_at'])
        self.assertIsNone(after['current_numeric_metrics']['business_at'])
        self.assertNotEqual(before['current_metric_records']['source_version'], after['current_metric_records']['source_version'])
        self.assertNotEqual(before['covered_projects']['source_version'], after['covered_projects']['source_version'])
        self.assertEqual(before['saved_metric_versions']['source_version'], after['saved_metric_versions']['source_version'])
        self.assertEqual(result['disposition'], 'partial')

    def test_bad_rows_do_not_erase_valid_classifications(self):
        rows = ['not json', '{"quality":"unknown","value":null}', '{"quality":"missing","value":null}',
                '{"quality":"not_applicable","value":null}', '{"quality":"invalid","value":null}',
                '{"quality":"unknown","quality":"unknown","value":null}', '{"quality":"good","value":true}']
        with self.store._write() as db:
            for seq, raw in enumerate(rows, 2):
                key = 'key' + str(seq)
                db.execute('INSERT INTO metric_changes VALUES(?,?,?,?,?,?)', (seq,'p',key,'version',NOW,raw))
                db.execute('INSERT INTO metric_current VALUES(?,?,?,?)', (key,'p',seq,NOW))
        result, rows = self.collect()
        self.assertEqual(rows['current_numeric_metrics']['value'],1)
        self.assertEqual(rows['current_unknown_metrics']['value'],1)
        self.assertEqual(rows['current_missing_metrics']['value'],1)
        self.assertEqual(rows['current_not_applicable_metrics']['value'],1)
        self.assertEqual(rows['current_invalid_metrics']['value'],1)
        self.assertEqual(rows['malformed_current_metric_records']['value'],3)
        self.assertEqual(sum(rows[k]['value'] for k in (
            'current_numeric_metrics','current_missing_metrics','current_unknown_metrics',
            'current_not_applicable_metrics','current_invalid_metrics',
            'malformed_current_metric_records')),rows['current_metric_records']['value'])
        self.assertEqual(result['disposition'],'partial')

    def test_project_entry_exports_bound_standard_snapshot(self):
        declaration = yaml.safe_load((ROOT / 'hub.connection.yaml').read_text())
        validate_declaration(declaration, 'personal-control-hub')
        entry = ROOT / declaration['metric_export']['entry']
        script = entry.read_text()
        config = yaml.safe_load((ROOT / 'data/connections/metric_sources.yaml').read_text())
        self.assertEqual(config['projects']['personal-control-hub']['adapter'], 'hub_activity')
        registry = yaml.safe_load((ROOT / 'data/registry/external_projects.yaml').read_text())
        registered = next(item for item in registry['projects'] if item['id'] == 'personal-control-hub')
        self.assertIn('hub.connection.yaml', registered['watch_paths'])
        self.assertIn(declaration['metric_export']['entry'], registered['watch_paths'])
        self.assertIn('.hub/status.json', (ROOT / '.gitignore').read_text().splitlines())

        (self.root / 'src').symlink_to(ROOT / 'src', target_is_directory=True)
        (self.root / 'scripts').mkdir()
        (self.root / 'data/registry').mkdir(parents=True)
        (self.root / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration, sort_keys=False))
        (self.root / declaration['metric_export']['entry']).write_text(script)
        state = {
            'schema_version': '1.1',
            'all_projects_governance': {
                'task_id': 'ALL-PROJECTS-CODEX-GOVERNANCE-V1',
                'status': 'ACTIVE',
                'next_action': 'Continue the next minimum project unit.',
                'v3': {
                    'stage_id': 'V3-08',
                    'unit_id': 'V3-08/personal-control-hub',
                    'acceptance': {
                        'accepted_count': 19,
                        'latest': {'evidence': 'The latest route criterion has independent evidence.'},
                    },
                    'review': {'verdict': 'PASS'},
                    'delivery': {'status': 'DELIVERED', 'main_commit': 'a' * 40},
                },
            },
        }
        (self.root / 'STATE.yaml').write_text(yaml.safe_dump(state, sort_keys=False))
        fixture_registry = {'projects': [{
            'id': 'personal-control-hub', 'name': 'personal-control-hub',
            'root_path': str(self.root), 'enabled': True, 'connection_read_allowed': True,
            'current_state_paths': ['STATE.yaml'],
        }]}
        (self.root / 'data/registry/external_projects.yaml').write_text(
            yaml.safe_dump(fixture_registry, sort_keys=False)
        )
        management = SourceResolver(self.root, clock=lambda: NOW).refresh('personal-control-hub')
        self.assertTrue(management['success'], management['errors'])
        environment = {
            'PATH': '', 'PYTHONNOUSERSITE': '1', 'PYTHONSAFEPATH': '1',
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1',
        }
        other = self.root / 'other'
        other.mkdir()
        rejected = subprocess.run(
            [sys.executable, '-I', '-B', '-', '--hub-root', str(self.root),
             '--project-root', str(other)],
            input=script.encode(), cwd=self.root, env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertFalse((other / '.hub').exists())
        result = subprocess.run(
            [sys.executable, '-I', '-B', '-', '--hub-root', str(self.root),
             '--project-root', str(self.root)],
            input=script.encode(), cwd=self.root, env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        snapshot = read_metric_snapshot(self.root)
        self.assertEqual(snapshot['project_id'], 'personal-control-hub')
        self.assertEqual(snapshot['exporter']['id'], 'personal-control-hub-export')
        self.assertIsNone(snapshot['management']['business']['current_work']['objective'])
        self.assertEqual(snapshot['management']['business']['current_work']['status'], 'active')
        self.assertIsNone(snapshot['management']['business']['current_work']['completed'])
        self.assertIsNone(snapshot['management']['business']['progress']['completed'])
        self.assertIsNone(snapshot['management']['business']['progress']['total'])
        self.assertIsNone(snapshot['management']['business']['verification']['status'])
        self.assertIsNone(snapshot['management']['business']['delivery']['status'])
        self.assertIsNone(snapshot['management']['business']['delivery']['commit'])
        values = {row['metric_id']: row['value'] for row in snapshot['metrics']}
        self.assertEqual(values['hub_activity.saved_requests'], 1)
        self.assertEqual(values['hub_activity.current_numeric_metrics'], 1)
        self.assertEqual(values['hub_activity.service_observed_issues'], 1)
        payload = json.dumps(snapshot)
        self.assertNotIn('PRIVATE', payload)
        self.assertNotIn('SECRET', payload)
