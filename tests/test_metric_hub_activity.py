import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hub.metric_hub_activity import collect_hub_activity
from hub.metric_store import MetricStore

NOW = '2026-09-09T00:00:00Z'


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
