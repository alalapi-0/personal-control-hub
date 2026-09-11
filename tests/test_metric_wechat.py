import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hub.metric_wechat import collect_wechat


class WechatMetricsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'data').mkdir()
        self.path = self.root / 'data/app.sqlite3'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('''
                CREATE TABLE articles(id INTEGER PRIMARY KEY, deleted_at TEXT, body TEXT);
                CREATE TABLE publish_jobs(id INTEGER PRIMARY KEY, article_id INTEGER, status TEXT, adapter_mode TEXT);
                CREATE TABLE wechat_drafts(id INTEGER PRIMARY KEY, article_id INTEGER, status TEXT, payload_json TEXT);
                CREATE TABLE publish_proofs(id INTEGER PRIMARY KEY, publish_job_id INTEGER, article_id INTEGER, confirmed_at TEXT, note TEXT);
                INSERT INTO articles VALUES(1,NULL,'PRIVATE'),(2,'2026-01-01','PRIVATE');
                INSERT INTO publish_jobs VALUES(1,1,'done','real'),(2,2,'done','real'),(3,1,'cancelled','mock');
                INSERT INTO wechat_drafts VALUES(1,1,'created','PRIVATE');
                INSERT INTO publish_proofs VALUES(1,2,2,'2026-01-01 00:00:00','PRIVATE');
            ''')

    def tearDown(self):
        self.temp.cleanup()

    def collect(self):
        return collect_wechat(self.root, 'wechat', '2026-09-09T00:00:00Z', {})

    def metrics(self, result):
        return {r['metric_id'].split('.', 1)[1]: r for r in result['metrics']}

    def test_current_scope_and_old_schema(self):
        result = self.collect()
        rows = self.metrics(result)
        for key, value in {'articles_total': 2, 'articles_active': 1, 'jobs_total': 3,
                           'queue_done': 1, 'queue_done_real': 1, 'queue_pending': 0,
                           'drafts_total': 1, 'drafts_real': None, 'proof_rows_total': 1,
                           'current_done_with_proof': 0, 'current_done_without_proof': 1}.items():
            self.assertEqual(rows[key]['value'], value, key)
        self.assertNotIn('PRIVATE', str(result))
        self.assertTrue(any(i['kind'] == 'unverified_publication' for i in result['issues']))
        self.assertTrue(all(r['business_at'] is None for r in result['metrics']))

    def test_registered_data_root_replaces_in_repository_state(self):
        repository = Path(tempfile.mkdtemp(dir=self.temp.name))
        (repository / 'data').mkdir()
        with closing(sqlite3.connect(repository / 'data/app.sqlite3')) as db, db:
            db.executescript('''
                CREATE TABLE articles(id INTEGER PRIMARY KEY, deleted_at TEXT);
                INSERT INTO articles VALUES(1,NULL),(2,NULL),(3,NULL);
            ''')
        stale = self.metrics(collect_wechat(repository, 'wechat', '2026-09-09T00:00:00Z', {}))
        self.assertEqual(stale['articles_total']['value'], 3)
        rows = self.metrics(collect_wechat(
            repository, 'wechat', '2026-09-09T00:00:00Z', {'data_root': str(self.root)}))
        self.assertEqual(rows['articles_total']['value'], 2)
        self.assertEqual(rows['articles_total']['source_ref'], str(self.path) + '#articles_total')

    def test_missing_registered_data_root_is_unknown_not_repository_state(self):
        repository = Path(tempfile.mkdtemp(dir=self.temp.name))
        (repository / 'data').mkdir()
        with closing(sqlite3.connect(repository / 'data/app.sqlite3')) as db, db:
            db.executescript('CREATE TABLE articles(id INTEGER PRIMARY KEY, deleted_at TEXT);'
                             'INSERT INTO articles VALUES(1,NULL);')
        result = collect_wechat(repository, 'wechat', '2026-09-09T00:00:00Z',
                                {'data_root': str(self.root / 'absent')})
        self.assertTrue(all(r['value'] is None for r in result['metrics']))
        self.assertTrue(any(i['code'] == 'wechat_database_unavailable' for i in result['issues']))

    def test_bad_status_preserves_independent_denominators(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE publish_jobs SET status='bad' WHERE id=1")
        rows = self.metrics(self.collect())
        self.assertIsNone(rows['queue_done']['value'])
        self.assertEqual(rows['articles_total']['value'], 2)
        self.assertEqual(rows['jobs_total']['value'], 3)
        self.assertEqual(rows['proof_rows_total']['value'], 1)

    def test_semantic_versions_ignore_content_but_track_proof_identity(self):
        before = self.collect()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE articles SET body='CHANGED'")
        self.assertEqual(before['source_version'], self.collect()['source_version'])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO publish_proofs VALUES(2,1,1,'2026-09-09 00:00:00','SECRET')")
        after = self.collect()
        self.assertNotEqual(before['source_version'], after['source_version'])
        self.assertEqual(self.metrics(after)['current_done_with_proof']['value'], 1)
        self.assertEqual(self.metrics(before)['articles_total']['source_version'], self.metrics(after)['articles_total']['source_version'])

    def test_missing_and_symlink_database_are_unknown(self):
        renamed = self.path.with_suffix('.original')
        self.path.rename(renamed)
        self.assertTrue(all(r['value'] is None for r in self.collect()['metrics']))
        self.path.symlink_to(renamed)
        self.assertTrue(all(r['value'] is None for r in self.collect()['metrics']))

    def test_read_only_transaction_and_projection(self):
        connect = sqlite3.connect
        statements = []
        def tracked(*args, **kwargs):
            self.assertIn('?mode=ro', args[0])
            db = connect(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db
        with patch('hub.metric_wechat.sqlite3.connect', tracked):
            self.collect()
        self.assertIn('BEGIN', statements)
        self.assertIn('ROLLBACK', statements)
        self.assertIn('PRAGMA query_only=ON', statements)
        self.assertFalse(any('SELECT *' in q or 'payload_json' in q or 'body' in q for q in statements))

    def test_historical_bad_status_and_mode_preserve_current_queue(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE publish_jobs SET status='bad', adapter_mode='bad' WHERE id=2")
            db.execute("UPDATE publish_jobs SET adapter_mode='bad' WHERE id=3")
        result = self.collect()
        rows = self.metrics(result)
        self.assertEqual(rows['queue_done']['value'], 1)
        self.assertEqual(rows['queue_done_real']['value'], 1)
        self.assertEqual(rows['queue_total']['value'], 1)
        self.assertEqual(rows['current_distinct_articles']['value'], 1)
        self.assertEqual(rows['orphan_jobs']['value'], 0)
        self.assertTrue(any('historical' in i['code'] for i in result['issues']))

    def test_proven_utc_business_time_and_invalid_time_preserve_counts(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('ALTER TABLE articles ADD COLUMN updated_at TEXT')
            db.execute('ALTER TABLE publish_jobs ADD COLUMN updated_at TEXT')
            db.execute("UPDATE articles SET updated_at='2026-09-08 12:34:56'")
            db.execute("UPDATE publish_jobs SET updated_at='2026-09-09 01:02:03'")
        before = self.metrics(self.collect())
        self.assertEqual(before['articles_total']['business_at'], '2026-09-08T12:34:56+00:00')
        self.assertEqual(before['queue_done']['business_at'], '2026-09-09T01:02:03+00:00')
        self.assertEqual(before['articles_total']['unit'], 'articles')
        self.assertEqual(before['queue_done']['unit'], 'jobs')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE publish_jobs SET updated_at='bad' WHERE id=2")
            db.execute("UPDATE articles SET updated_at='2026-02-30 01:02:03' WHERE id=1")
        after = self.metrics(self.collect())
        self.assertEqual(after['jobs_total']['value'], 3)
        self.assertIsNone(after['jobs_total']['business_at'])
        self.assertEqual(after['queue_done']['business_at'], before['queue_done']['business_at'])
        self.assertEqual(after['articles_total']['value'], 2)
        self.assertIsNone(after['articles_total']['business_at'])
        self.assertNotEqual(after['articles_total']['source_version'], before['articles_total']['source_version'])

    def test_duplicate_ids_rejected_and_orphan_denominator(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO publish_jobs VALUES(4,999,'pending','mock')")
        self.assertEqual(self.metrics(self.collect())['orphan_jobs']['value'], 1)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP TABLE wechat_drafts')
            db.execute('CREATE TABLE wechat_drafts(id INTEGER, article_id INTEGER, status TEXT)')
            db.execute("INSERT INTO wechat_drafts VALUES(1,1,'created'),(1,1,'created')")
        rows = self.metrics(self.collect())
        self.assertIsNone(rows['drafts_total']['value'])
        self.assertEqual(rows['jobs_total']['value'], 4)

    def test_row_limit_invalidates_only_affected_projection(self):
        with patch('hub.metric_wechat.MAX_ROWS', 2):
            rows = self.metrics(self.collect())
        self.assertIsNone(rows['jobs_total']['value'])
        self.assertEqual(rows['articles_total']['value'], 2)


if __name__ == '__main__':
    unittest.main()
