import sqlite3
import tempfile
import unittest
from pathlib import Path
from hub.metric_manga import collect_manga

class MangaMetricsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.spec = dict(data_root=str(self.root), review_db='review.sqlite3', batch_id='batch', project_dbs={'source':'project.sqlite3'})
        self.sql('review.sqlite3', "CREATE TABLE batches(id,item_count,revision,format_version); INSERT INTO batches VALUES('batch',2,9,1); CREATE TABLE items(id,batch_id,source_project_id,verdict,artifact_revision); INSERT INTO items VALUES('a','batch','source','issues',2),('b','batch','source','approved',1); CREATE TABLE artifact_revisions(item_id,artifact_revision); INSERT INTO artifact_revisions VALUES('a',1),('a',2),('a',2);")
        self.sql('project.sqlite3', "CREATE TABLE projects(id); INSERT INTO projects VALUES('source'); CREATE TABLE jobs(project_id,status); INSERT INTO jobs VALUES('source','failed'); CREATE TABLE page_generations(project_id,state); INSERT INTO page_generations VALUES('source','active');")
    def tearDown(self): self.temp.cleanup()
    def sql(self, name, sql):
        c = sqlite3.connect(self.root/name)
        try:
            c.executescript(sql)
            c.commit()
        finally:
            c.close()
    def collect(self): return collect_manga(self.root,'manga','2026-09-09T00:00:00+00:00',self.spec)
    def test_current_not_history(self):
        before=(self.root/'review.sqlite3').read_bytes(); result=self.collect()
        self.assertEqual(next(x['value'] for x in result['metrics'] if x['metric_id']=='review_total'),2)
        self.assertEqual(before,(self.root/'review.sqlite3').read_bytes())
        self.assertIsNone(next(x['value'] for x in result['metrics'] if x['metric_id']=='blocker_oldest_age'))
        self.sql('review.sqlite3',"INSERT INTO artifact_revisions VALUES('a',3)")
        self.assertEqual(result['source_version'],self.collect()['source_version'])
    def test_optional_version_column(self):
        self.sql('review.sqlite3','ALTER TABLE items DROP COLUMN artifact_revision')
        r=self.collect()
        self.assertEqual(next(x['value'] for x in r['metrics'] if x['metric_id']=='review_total'),2)
        self.assertIsNone(next(x['value'] for x in r['metrics'] if x['metric_id']=='review_current_version_max'))
    def test_enum(self):
        self.sql('review.sqlite3',"UPDATE items SET verdict='surprise' WHERE id='a'"); r=self.collect()
        self.assertEqual(next(x['value'] for x in r['metrics'] if x['metric_id']=='review_count' and x['dimensions']['verdict']=='unknown'),1)
        self.assertEqual(r['disposition'],'partial')
    def test_review_identity(self):
        self.spec['batch_id']='wrong'; r=self.collect()
        self.assertFalse(any(x['metric_id']=='review_total' for x in r['metrics']))
        self.assertTrue(any(x['metric_id']=='job_count' for x in r['metrics']))
    def test_project_identity(self):
        self.sql('project.sqlite3',"UPDATE projects SET id='wrong'"); r=self.collect()
        self.assertTrue(any(x['metric_id']=='review_total' for x in r['metrics']))
        self.assertFalse(any(x['metric_id']=='job_count' for x in r['metrics']))
    def test_table_isolation(self):
        old=self.collect(); self.sql('project.sqlite3','DROP TABLE jobs'); r=self.collect()
        self.assertTrue(any(x['metric_id']=='generation_count' for x in r['metrics']))
        versions=lambda result:[x['source_version'] for x in result['metrics'] if x['metric_id'].startswith('review_')]
        self.assertEqual(versions(old),versions(r))

if __name__=='__main__': unittest.main()
