"""Versioned registered fixture bytes; never exercise the real-media gate."""
import copy
import hashlib
import json
import os
import unittest
from unittest import mock

import test_hub_materials as fixtures
from hub.design_records import with_content_hash, DesignRecordError
from hub.service_contract import ServiceError


class ArtifactVersionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.MaterialsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.service = self.f.service
        self.store = self.f.fixture.store

    def rows(self, aid=None):
        return [r for r in self.service.catalog(lambda: True)['materials']
                if aid is None or r['artifact_id'] == aid]

    def test_eight_synthetic_original_identities_provenance_bytes_are_immutable(self):
        originals = []
        for i in range(8):
            p = self.f.fixture.material / ('version-' + str(i)) / 'same-report.txt'
            p.parent.mkdir(); p.write_bytes(('Synthetic original ' + str(i)).encode())
            originals.append((p, 'fixture-original-' + str(i)))
        self.f.add_artifacts(originals)
        before = self.store.read()
        self.service.prepare_fixture({aid for _, aid in originals})
        for path, aid in originals:
            with self.subTest(aid=aid):
                row = self.rows(aid)[0]
                self.assertEqual(row['display_name'], 'same-report.txt')
                result = self.service.read(row['id'], row['version'], authorize=lambda: True)
                self.assertEqual(result.data, path.read_bytes())
                original = next(a for a in before['facts'] if a['id'] == aid)
                self.assertEqual(row['provenance'], {**original['provenance'], 'reason': None})
                self.assertEqual(row['sha256'], hashlib.sha256(result.data).hexdigest())
        self.assertEqual(len({r['id'] for r in self.rows() if r['artifact_id'].startswith('fixture-original-')}), 8)
        self.assertEqual(self.store.read(), before)

    def test_exact_pair_reads_multiple_revisions_and_never_selects_latest_implicitly(self):
        before = self.rows('fixture-report')[0]
        candidate = copy.deepcopy(self.f.fixture.candidate)
        candidate['revision'] += 1; candidate['visual']['differences'].append('Synthetic version explanation')
        candidate = with_content_hash(candidate)
        self.f.fixture.append(candidate, 'fixture-version-next')
        self.service.prepare_fixture(self.f.ids)
        rows = self.rows('fixture-report')
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['id'] for r in rows}, {before['id']})
        self.assertEqual(len({r['version'] for r in rows}), 2)
        self.assertEqual([r['historical'] for r in rows], [True, False])
        self.assertTrue(all(r['available'] for r in rows))
        for r in rows:
            self.assertIn((r['id'], r['version']), self.service._pins)
            self.assertEqual(self.service.read(r['id'], r['version'], authorize=lambda: True).data,
                             self.f.text_path.read_bytes())
        with mock.patch.object(self.service, '_open') as opened, self.assertRaises(ServiceError):
            self.service.read(before['id'], 'f' * 64, authorize=lambda: True)
        opened.assert_not_called()

    def test_unrelated_registration_changes_observation_not_content_or_readability(self):
        old = self.rows('fixture-report')[0]
        extra = self.f.fixture.material / 'unrelated.txt'; extra.write_text('Unrelated synthetic source')
        self.f.fixture.append(self.f.fixture.artifact('fixture-unrelated', extra), 'fixture-unrelated-append')
        current = self.rows('fixture-report')[0]
        self.assertEqual((old['id'], old['version']), (current['id'], current['version']))
        self.assertNotEqual(old['observation_version'], current['observation_version'])
        self.assertTrue(current['available'])
        self.assertEqual(self.service.read(old['id'], old['version'], authorize=lambda: True).data,
                         self.f.text_path.read_bytes())

    def test_store_drift_during_read_still_discards_bytes(self):
        row = self.rows('fixture-report')[0]; original_read = os.read; changed = []
        def mutate(fd, count):
            data = original_read(fd, count)
            if not changed:
                changed.append(True)
                p = self.f.fixture.material / 'other.txt'; p.write_text('Synthetic other')
                self.f.fixture.append(self.f.fixture.artifact('fixture-inflight', p), 'fixture-inflight')
            return data
        with mock.patch('hub.material_service.os.read', side_effect=mutate), self.assertRaises(ServiceError) as error:
            self.service.read(row['id'], row['version'], authorize=lambda: True)
        self.assertEqual(error.exception.code, 'MATERIAL_STALE')

    def test_same_name_different_sha_and_scope_preserve_separate_identities(self):
        first = self.f.fixture.material / 'one' / 'same.txt'
        second = self.f.fixture.material / 'two' / 'same.txt'
        for p, body in [(first, b'First'), (second, b'Second')]:
            p.parent.mkdir(); p.write_bytes(body)
        self.f.add_artifacts([(first, 'fixture-same-one'), (second, 'fixture-same-two')])
        self.service.prepare_fixture({'fixture-same-one', 'fixture-same-two'})
        rows = [r for r in self.rows() if r['artifact_id'] in {'fixture-same-one', 'fixture-same-two'}]
        self.assertEqual({r['display_name'] for r in rows}, {'same.txt'})
        self.assertEqual(len({(r['id'], r['version']) for r in rows}), 2)
        self.assertEqual(len({r['sha256'] for r in rows}), 2)
        state, _ = self.service._facts(lambda: True)
        bad = copy.deepcopy(state)
        next(a for a in bad['facts'] if a['id'] == 'fixture-same-one')['scope']['members'][0]['pages'] = ['foreign-page']
        # Actual DesignStore, not a patched service reader, rejects a bad archive.
        with self.assertRaises(DesignRecordError): self.store.validate(bad)

    def test_same_name_and_bytes_in_other_registered_scope_stay_distinct(self):
        first = self.rows('fixture-report')[0]
        baseline = copy.deepcopy(self.f.fixture.baseline)
        baseline['revision'] += 1; baseline['scope']['pages'] = ['comparison', 'review']
        baseline = with_content_hash(baseline)
        self.f.fixture.append(baseline, 'fixture-expanded-baseline')
        path = self.f.fixture.material / 'other-scope' / 'report.md'
        path.parent.mkdir(); path.write_bytes(self.f.text_path.read_bytes())
        artifact = self.f.fixture.artifact('fixture-other-scope', path)
        artifact['scope']['members'][0]['pages'] = ['comparison']
        self.f.fixture.append(artifact, 'fixture-other-scope-artifact')
        candidate = copy.deepcopy(self.f.fixture.candidate)
        candidate['id'] = 'fixture-other-scope-candidate'; candidate['revision'] = 1
        candidate['scope'] = copy.deepcopy(artifact['scope'])
        candidate['artifact_bindings'] = [{'artifact_id': artifact['id'], 'sha256': artifact['sha256']}]
        candidate['evidence_refs'] = []
        candidate['baseline_bindings'] = [{'project_id': 'fixture-project', 'baseline_id': baseline['id'],
            'baseline_revision': baseline['revision'], 'baseline_hash': baseline['content_hash'], 'pages': ['comparison']}]
        self.f.fixture.append(with_content_hash(candidate), 'fixture-other-scope-candidate')
        self.service.prepare_fixture({'fixture-report', artifact['id']})
        other = self.rows(artifact['id'])[0]
        self.assertEqual(first['display_name'], other['display_name'])
        self.assertEqual(first['sha256'], other['sha256'])
        self.assertNotEqual((first['id'], first['version']), (other['id'], other['version']))
        self.assertEqual(self.service.read(first['id'], first['version'], authorize=lambda: True).data,
                         self.service.read(other['id'], other['version'], authorize=lambda: True).data)
        self.assertTrue(self.rows('fixture-report')[0]['historical'])

    def test_history_overwrite_missing_alias_and_hardlink_never_reads_new_bytes(self):
        row = self.rows('fixture-report')[0]
        candidate = copy.deepcopy(self.f.fixture.candidate); candidate['revision'] += 1
        self.f.fixture.append(with_content_hash(candidate), 'fixture-historical')
        original = self.f.text_path.read_bytes()
        other = self.f.text_path.with_name('other-report.md'); other.write_bytes(b'New content')
        for mode in ['overwrite', 'missing', 'symlink', 'hardlink']:
            with self.subTest(mode=mode):
                if self.f.text_path.exists(): self.f.text_path.unlink()
                if mode == 'overwrite': self.f.text_path.write_bytes(b'New content')
                elif mode == 'symlink': self.f.text_path.symlink_to(other)
                elif mode == 'hardlink': os.link(other, self.f.text_path)
                with self.assertRaises(ServiceError):
                    self.service.read(row['id'], row['version'], authorize=lambda: True)
                if self.f.text_path.exists() or self.f.text_path.is_symlink(): self.f.text_path.unlink()
                self.f.text_path.write_bytes(original)

    def test_history_is_not_current_action_and_authority_drift_precedes_content(self):
        row = self.rows('fixture-report')[0]
        self.f.add_artifacts([(self.f.text_path, 'fixture-later-report')])
        for change in [{'enabled': False}, {'access_profile': 'no_current_goal_access'},
                       {'root_kind': 'cloud'}, {'current_state_status': 'removed_local'}]:
            saved = copy.deepcopy(self.f.registry.projects['fixture-project'])
            self.f.registry.projects['fixture-project'].update(change)
            with mock.patch.object(self.service, '_open') as opened, self.assertRaises(ServiceError):
                self.service.read(row['id'], row['version'], authorize=lambda: True)
            opened.assert_not_called()
            self.f.registry.projects['fixture-project'] = saved
        with mock.patch.object(self.f.fixture.service, '_assert_current_candidate', side_effect=ServiceError('UNEXPECTED_CORRUPTION')):
            with self.assertRaises(ServiceError) as error: self.service.catalog(lambda: True)
            self.assertEqual(error.exception.code, 'UNEXPECTED_CORRUPTION')
        self.store.fixture = False
        with mock.patch.object(self.service, '_open') as opened, self.assertRaises(ServiceError) as error:
            self.service.read(row['id'], row['version'], authorize=lambda: True)
        opened.assert_not_called(); self.assertEqual(error.exception.code, 'REAL_MATERIAL_GATE_CLOSED')

    def test_catalog_has_safe_names_provenance_unknown_purpose_and_reference_not_read(self):
        p = self.f.fixture.material / 'public.txt'; p.write_text('Synthetic')
        artifact = self.f.fixture.artifact('fixture-private-provenance', p)
        artifact['provenance'] = {'method': 'Read /data/private/original.txt', 'source_refs': ['/var/private/source', 'logical-source']}
        self.f.fixture.append(artifact, 'fixture-redaction')
        candidate = copy.deepcopy(self.f.fixture.candidate); candidate['revision'] += 1
        candidate['evidence_refs'].append(artifact['id'])
        self.f.fixture.append(with_content_hash(candidate), 'fixture-redacted-candidate')
        catalog = self.service.catalog(lambda: True); body = json.dumps(catalog)
        for secret in [str(self.f.fixture.root), '/data/private', '/var/private', 'location', 'hub_root']:
            self.assertNotIn(secret, body)
        r = next(r for r in catalog['materials'] if r['artifact_id'] == artifact['id'])
        self.assertIsNone(r['purpose']); self.assertEqual(r['purpose_reason'], 'PURPOSE_NOT_DECLARED')
        self.assertEqual(r['provenance']['source_refs'], ['logical-source'])
        self.assertEqual(r['provenance']['reason'], 'PROVENANCE_NOT_PUBLIC')
        figma = next(r for r in catalog['materials'] if r['kind'] == 'review_reference')
        self.assertFalse(figma['content_readable']); self.assertIsNone(figma['review_reference'])
        self.assertEqual(figma['availability_reason'], 'REVIEW_REFERENCE_UNAVAILABLE')


if __name__ == '__main__': unittest.main()
