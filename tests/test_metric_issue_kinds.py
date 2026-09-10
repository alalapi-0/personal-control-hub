from pathlib import Path
import tempfile
import unittest

from hub.metric_documents import Projection
from hub.metric_store import MetricStore
from hub.metrics import metric_catalog


class IssueKindsTests(unittest.TestCase):
    def test_unknown_value_is_a_data_gap_and_missing_file_is_a_read_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            p = Projection(root, 'sample', '2026-09-09T00:00:00Z', 'sample')
            p.emit('output_boundary', None, 'microseconds', 'metadata.json#boundary')
            p.read('missing.json')
            self.assertEqual([i['kind'] for i in p.problems], ['data_gap', 'read_failure'])
            self.assertIsNone(p.rows[0]['value'])
            self.assertEqual(p.rows[0]['quality'], 'unknown')
            store = MetricStore(root)
            store.begin('issue-kind-check', ['sample'], 'fixture')
            store.save('issue-kind-check', dict(project_id='sample', observed_at=p.observed,
                disposition='partial', metrics=p.rows, metric_definitions=metric_catalog(p.rows),
                issues=p.problems))
            coverage = store.coverage({'projects': [{'id': 'sample', 'enabled': True}]})['coverage']
            self.assertEqual(coverage['read_errors'], 1)
            self.assertEqual(coverage['data_gaps'], 1)
            self.assertEqual(coverage['issues'], 2)


if __name__ == '__main__':
    unittest.main()
