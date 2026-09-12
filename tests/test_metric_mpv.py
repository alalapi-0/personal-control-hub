import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hub.metric_mpv import collect_mpv

NOW = '2026-09-09T00:00:00Z'
SAVED = '2026-09-03T00:00:00Z'

class MPVProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.files = [f'.local-runtime/f0{i}/projects/working.mpvclip.json' for i in range(2, 6)]
        self.obj = dict(schema_version=1, application_version='0.0.0-bootstrap', project_id='working',
            created_at=SAVED, updated_at=SAVED,
            source=dict(source_id='src-working', duration_us=8000000, file_path='/private/never-open.mov'),
            segments=[dict(segment_id='seg-1', source_id='src-working', enabled=True, decision='pending',
                duration_us=2400000, updated_at=SAVED, actual_output_boundary=None,
                validation_results=[dict(checker=c, status='pass', message='SECRET BODY')
                                    for c in ['duration_category', 'bounds', 'source']])])
        for i, f in enumerate(self.files):
            obj = copy.deepcopy(self.obj)
            obj['segments'][0]['duration_us'] = [2400000, 9783333, 3600000, 3166666][i]
            if i == 0:
                obj['segments'][0]['validation_results'][0]['status'] = 'fail'
            self.write(f, obj)

    def write(self, f, obj):
        path = self.root / f
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj))

    def collect(self, files=None):
        return collect_mpv(self.root, 'mpv', NOW, {'project_files': self.files if files is None else files})

    def rows(self, result, name):
        return [r for r in result['metrics'] if r['metric_id'] == 'mpv.' + name]

    def test_distinct_historical_scopes_and_saved_checks(self):
        r = self.collect()
        rows = self.rows(r, 'segment_duration')
        self.assertEqual(len({x['dimensions']['segment_identity'] for x in rows}), 4)
        self.assertEqual([x['value'] for x in rows], [2400000, 9783333, 3600000, 3166666])
        self.assertEqual(sum(x['value'] for x in self.rows(r, 'saved_validation_pass')), 11)
        self.assertEqual(sum(x['value'] for x in self.rows(r, 'saved_validation_fail')), 1)
        self.assertTrue(all(x['value'] is None for x in self.rows(r, 'saved_output_boundary_duration')))
        failures = [i for i in r['issues'] if i['code'] == 'mpv_saved_validation_fail']
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['affected_items'], 1)
        self.assertIsNone(failures[0]['started_at'])
        self.assertNotIn('SECRET BODY', json.dumps(r))
        self.assertNotIn('/private/never-open.mov', json.dumps(r))
        self.assertTrue(all(x['dimensions']['scope'] == 'historical_work_instance' for x in rows))

    def test_invalid_field_and_time_preserve_independent_facts(self):
        obj = copy.deepcopy(self.obj)
        obj['updated_at'] = 'not-a-time'
        obj['source'] = 'malformed'
        obj['segments'][0]['duration_us'] = True
        self.write(self.files[0], obj)
        r = self.collect()
        self.assertIsNone(self.rows(r, 'source_duration')[0]['value'])
        self.assertIsNone(self.rows(r, 'segment_duration')[0]['value'])
        self.assertEqual(sum(x['value'] for x in self.rows(r, 'segment_enabled')), 4)
        self.assertEqual(self.rows(r, 'saved_validation_pass')[0]['value'], 3)
        self.assertEqual(self.rows(r, 'total_saved_instances')[0]['value'], 4)
        (self.root / self.files[1]).write_text('{')
        r = self.collect()
        self.assertEqual(self.rows(r, 'readable_instances')[0]['value'], 3)
        self.assertIsNone(self.rows(r, 'total_saved_instances')[0]['value'])
        self.assertEqual(len(self.rows(r, 'segment_enabled')), 3)

    def test_semantic_versions_ignore_unselected_media_and_messages(self):
        before = self.collect()
        f = self.files[1]
        obj = json.loads((self.root / f).read_text())
        obj['source']['file_path'] = 'https://private.invalid/new'
        obj['segments'][0]['validation_results'][0]['message'] = 'DIFFERENT BODY'
        self.write(f, obj)
        self.assertEqual(before, self.collect())
        obj['segments'][0]['duration_us'] += 1
        self.write(f, obj)
        after = self.collect()
        changed = [a['metric_id'] for a, b in zip(before['metrics'], after['metrics']) if a != b]
        self.assertEqual(changed, ['mpv.segment_duration'])

    def test_bounds_and_symlinks(self):
        self.assertEqual(self.collect(['../outside.json'])['disposition'], 'partial')
        self.assertEqual(len(self.collect(self.files * 26)['metrics']), 1)
        target = self.root / self.files[0]
        target.unlink()
        target.symlink_to(self.root / self.files[1])
        self.assertEqual(self.rows(self.collect(), 'readable_instances')[0]['value'], 3)
        with patch('hub.metric_mpv.MAX_BYTES', 1):
            self.assertEqual(self.rows(self.collect(), 'readable_instances')[0]['value'], 0)
        with patch('hub.metric_mpv.MAX_METADATA_BYTES', 1):
            self.assertEqual(self.rows(self.collect(), 'readable_instances')[0]['value'], 0)

if __name__ == '__main__':
    unittest.main()
