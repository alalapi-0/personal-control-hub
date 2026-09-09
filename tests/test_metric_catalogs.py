import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hub.metric_catalogs import collect_anime, collect_pixel, STUDIO
NOW = '2026-09-09T01:00:00+00:00'

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value))
    def pixel(self):
        return collect_pixel(self.root, 'pixel', NOW, {'metadata_root': str(self.root)})
    def values(self, result, name):
        return [r['value'] for r in result['metrics'] if r['metric_id'].endswith('.' + name)]
    def asset(self, aid='a', **extra):
        return dict(asset_id=aid, data_origin='manual', lifecycle_status='pending_review', review_status='pending', updated_at=NOW, **extra)
    def test_pixel_counts_unknown_version_and_private_payload(self):
        self.write('metadata/assets/a.json', self.asset(prompt='PRIVATE ONE', notes='PRIVATE TWO'))
        self.write('metadata/reviews/r.json', dict(review_id='r', asset_id='gone', review_status='retry_needed', reviewed_at=NOW))
        first = self.pixel()
        self.assertEqual(self.values(first, 'assets_total'), [1])
        self.assertEqual(self.values(first, 'orphan_reviews'), [1])
        self.assertEqual(self.values(first, 'asset_version_max'), [None])
        self.assertEqual(self.values(first, 'backlog_oldest_age'), [None])
        self.assertNotIn('PRIVATE', json.dumps(first))
        self.write('metadata/assets/a.json', self.asset(prompt='DIFFERENT', notes='CHANGED'))
        self.assertEqual(first['source_version'], self.pixel()['source_version'])
    def test_duplicate_invalidates_identity_group(self):
        self.write('metadata/assets/a.json', self.asset())
        self.write('metadata/assets/b.json', self.asset())
        result = self.pixel()
        self.assertEqual(self.values(result, 'assets_total'), [None])
        self.assertTrue(all(v is None for v in self.values(result, 'lifecycle_status_count')))
    def test_enum_retains_denominator(self):
        row = self.asset(); row['lifecycle_status'] = 'never_seen'
        self.write('metadata/assets/a.json', row)
        result = self.pixel()
        self.assertEqual(self.values(result, 'assets_total'), [1])
        self.assertTrue(any(r['value'] == 1 and r['dimensions'].get('lifecycle_status') == 'unknown' for r in result['metrics']))
    def test_corrupt_file_not_silently_skipped(self):
        self.write('metadata/assets/a.json', self.asset())
        (self.root / 'metadata/assets/b.json').write_text('{broken')
        self.assertEqual(self.values(self.pixel(), 'assets_total'), [None])
    def test_symlink_collection_and_file_rejected(self):
        self.write('elsewhere/a.json', self.asset())
        (self.root / 'metadata').mkdir()
        (self.root / 'metadata/assets').symlink_to(self.root / 'elsewhere', target_is_directory=True)
        self.assertEqual(self.values(self.pixel(), 'assets_total'), [None])
        (self.root / 'metadata/assets').unlink()
        (self.root / 'metadata/assets').mkdir()
        (self.root / 'metadata/assets/a.json').symlink_to(self.root / 'elsewhere/a.json')
        self.assertEqual(self.values(self.pixel(), 'assets_total'), [None])
    def test_file_and_byte_bounds(self):
        self.write('metadata/assets/a.json', self.asset())
        with patch('hub.metric_catalogs.MAX_FILES', 0):
            self.assertEqual(self.values(self.pixel(), 'assets_total'), [None])
        with patch('hub.metric_catalogs.MAX_BYTES', 1):
            self.assertEqual(self.values(self.pixel(), 'assets_total'), [None])
    def setup_studio(self):
        self.write('schemas/studio_record.schema.json', {'properties': {'record_type': {'enum': list(STUDIO)}}})
        self.write('runtime_assets/metadata/studio/projects/p.json', dict(record_type='project', record_id='p', project_id='p', revision=1, created_at=NOW, updated_at=NOW, data={'project_id': 'p', 'archived': False}))
    def test_anime_known_absent_collections_empty_and_confirmed_not_pending_review(self):
        self.setup_studio()
        self.write('runtime_assets/metadata/real_image/runs/r.json', dict(run_id='r', project_id='p', status='confirmed', created_at=NOW, reconciliation_required=False, request={'prompt': 'PRIVATE'}))
        result = collect_anime(self.root, 'anime', NOW, {})
        tasks = [r for r in result['metrics'] if r['metric_id'] == 'anime.collection_total' and r['dimensions']['collection'] == 'tasks']
        self.assertEqual(tasks[0]['value'], 0)
        pending_review = [r for r in result['metrics'] if r['dimensions'].get('status') == 'pending_review']
        self.assertEqual(pending_review[0]['value'], 0)
        self.assertEqual(self.values(result, 'legacy_coverage'), [None])
        self.assertNotIn('PRIVATE', json.dumps(result))
    def test_schema_enums_and_same_status_new_revision(self):
        row = self.asset(); row.update(data_origin='real_api', lifecycle_status='generated_raw', review_status='under_review', asset_version=1)
        self.write('metadata/assets/a.json', row)
        first = self.pixel()
        group = lambda result: next(r for r in result['metrics'] if r['metric_id'] == 'pixel.lifecycle_status_count' and r['dimensions'].get('data_origin') == 'real_api' and r['dimensions'].get('lifecycle_status') == 'generated_raw')
        self.assertEqual(group(first)['value'], 1)
        self.assertEqual(group(first)['business_at'], NOW)
        row['asset_version'] = 2
        self.write('metadata/assets/a.json', row)
        self.assertNotEqual(group(first)['source_version'], group(self.pixel())['source_version'])
    def studio_asset(self, deleted=False, revision=1):
        self.write('runtime_assets/metadata/studio/assets/a.json', dict(record_type='asset', record_id='a', project_id='p', revision=revision, updated_at=NOW, data=dict(asset_id='a', project_id='p', deleted=deleted, primary=True, status='approved', data_origin='provider_generated', version=1)))
    def test_deleted_asset_excluded_and_bad_deleted_preserves_total(self):
        self.setup_studio()
        self.studio_asset(deleted=True)
        result = collect_anime(self.root, 'anime', NOW, {})
        self.assertEqual(self.values(result, 'active_assets'), [0])
        self.assertEqual(self.values(result, 'primary_assets'), [0])
        self.studio_asset(deleted='bad')
        result = collect_anime(self.root, 'anime', NOW, {})
        self.assertEqual(self.values(result, 'active_assets'), [None])
        totals = [r for r in result['metrics'] if r['metric_id'] == 'anime.collection_total' and r['dimensions'].get('collection') == 'assets']
        self.assertEqual(totals[0]['value'], 1)
    def test_anime_revision_and_project_run_status(self):
        self.setup_studio()
        self.studio_asset()
        self.write('runtime_assets/metadata/real_image/runs/r.json', dict(run_id='r', project_id='p', status='send_claimed', created_at=NOW, reconciliation_required=False))
        first = collect_anime(self.root, 'anime', NOW, {})
        group = lambda result: next(r for r in result['metrics'] if r['metric_id'] == 'anime.status_count' and r['dimensions'].get('collection') == 'assets' and r['dimensions'].get('status') == 'approved')
        self.studio_asset(revision=2)
        self.assertNotEqual(group(first)['source_version'], group(collect_anime(self.root, 'anime', NOW, {}))['source_version'])
        run = next(r for r in first['metrics'] if r['dimensions'].get('status') == 'send_claimed')
        self.assertEqual(run['value'], 1)
        self.assertEqual(run['dimensions']['source_project_id'], 'p')
        self.assertIsNone(run['business_at'])
    def test_anime_invalid_project_invalidates_dependents(self):
        self.setup_studio()
        self.write('runtime_assets/metadata/studio/projects/bad.json', dict(record_id='p', record_type='project', project_id='p', data={'project_id': 'p'}))
        result = collect_anime(self.root, 'anime', NOW, {})
        self.assertTrue(all(v is None for v in self.values(result, 'collection_total')))
if __name__ == '__main__':
    unittest.main()
