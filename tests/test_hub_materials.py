from __future__ import annotations

import copy
import hashlib
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.design_records import with_content_hash
from hub.material_service import MaterialService, MAX_RESPONSE
from hub.service_contract import ServiceError
import test_hub_design_service as fixture_designs
import test_hub_local_service as fixture_http


class MaterialsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_designs.DesignServiceTests()
        self.fixture.setUp(); self.addCleanup(self.fixture.tearDown)
        self.registry = SimpleNamespace(projects={'fixture-project': {
            'id': 'fixture-project', 'name': 'Synthetic project', 'enabled': True,
            'root_path': '/fixture/metadata-only', 'access_profile': 'registered_project_read'}},
            authority={'registry_hash': 'a' * 64}, _check_registry=lambda: None)
        self.projects = SimpleNamespace(_resolver=lambda: self.registry)
        self.video_data = b'\0\0\0\x18ftypisom' + b'\0' * 12 + bytes(range(256)) * 4
        self.video_path = self.fixture.material / 'short.mp4'; self.video_path.write_bytes(self.video_data)
        self.text_path = self.fixture.material / 'report.md'; self.text_path.write_text('Synthetic report\n中文\n')
        self.add_artifacts([(self.video_path, 'fixture-video'), (self.text_path, 'fixture-report')])
        self.service = MaterialService(self.projects, self.fixture.service)
        self.initial = self.fixture.store.path.read_bytes()
        self.ids = {'fixture-video', 'fixture-report', self.fixture.html_artifact['id'], self.fixture.png_artifact['id']}
        self.service.prepare_fixture(self.ids)

    def add_artifacts(self, rows):
        evidence = list(self.fixture.candidate['evidence_refs'])
        for path, aid in rows:
            self.fixture.append(self.fixture.artifact(aid, path), 'append-' + aid); evidence.append(aid)
        candidate = copy.deepcopy(self.fixture.candidate)
        candidate['revision'] += 1; candidate['evidence_refs'] = evidence
        candidate = with_content_hash(candidate)
        self.fixture.append(candidate, 'append-candidate-' + str(candidate['revision']))
        self.fixture.candidate = candidate

    def row(self, aid='fixture-video'):
        return next(r for r in self.service.catalog(lambda: True)['materials'] if r['artifact_id'] == aid)

    def read(self, row=None, **kwargs):
        row = row or self.row()
        return self.service.read(row['id'], row['version'], authorize=lambda: True, **kwargs)

    def test_exact_registered_text_html_raster_and_immutable_history(self):
        for aid, data, mime in [(self.fixture.html_artifact['id'], self.fixture.html_data, 'text/plain; charset=utf-8'),
                                (self.fixture.png_artifact['id'], self.fixture.png_data, 'image/png'),
                                ('fixture-report', self.text_path.read_bytes(), 'text/plain; charset=utf-8')]:
            with self.subTest(aid=aid):
                result = self.read(self.row(aid)); self.assertEqual(result.data, data); self.assertEqual(result.content_type, mime)
        self.assertEqual(self.fixture.store.path.read_bytes(), self.initial)
        self.assertFalse(self.service.catalog(lambda: True)['content_cache'])
        self.assertTrue(all(r['execution_allowed'] is False for r in self.service.catalog(lambda: True)['materials']))

    def test_policy_and_version_rejection_before_open(self):
        row = self.row()
        with mock.patch.object(self.service, '_open', wraps=self.service._open) as probe:
            for changes in [{'enabled': False}, {'connection_read_allowed': False}, {'current_state_status': 'removed_local'},
                            {'root_kind': 'cloud'}, {'root_path': 'https://remote.invalid'},
                            {'local_presence': {'status': 'removed_local'}}, {'access_profile': 'no_current_goal_access'}]:
                before = copy.deepcopy(self.registry.projects['fixture-project'])
                self.registry.projects['fixture-project'].update(changes)
                with self.assertRaises(ServiceError): self.read(row)
                self.registry.projects['fixture-project'] = before
            for binding in [{'id': row['id'], 'version': '0'*64}, {'id': '../../STATE.yaml', 'version': row['version']},
                            {'id': 'material-'+'f'*32, 'version': row['version']}]:
                with self.assertRaises(ServiceError): self.read(binding)
            self.registry.authority['registry_hash'] = 'b'*64
            with self.assertRaises(ServiceError): self.read(row)
            probe.assert_not_called()

    def test_file_alias_link_fifo_missing_and_replacement(self):
        row = self.row(); original = self.video_path.read_bytes()
        alternate = self.video_path.with_name('alternate.mp4'); alternate.write_bytes(original)
        for replacement in ['symlink', 'hardlink', 'fifo', 'directory', 'missing', 'new-file']:
            with self.subTest(replacement=replacement):
                self.video_path.unlink()
                if replacement == 'symlink': self.video_path.symlink_to(alternate)
                elif replacement == 'hardlink': os.link(alternate, self.video_path)
                elif replacement == 'fifo': os.mkfifo(self.video_path)
                elif replacement == 'directory': self.video_path.mkdir()
                elif replacement == 'new-file': self.video_path.write_bytes(original)
                with self.assertRaises(ServiceError): self.read(row)
                if self.video_path.is_dir(): self.video_path.rmdir()
                elif self.video_path.exists() or self.video_path.is_symlink(): self.video_path.unlink()
                self.video_path.write_bytes(original)
        self.assertEqual(alternate.read_bytes(), original)

    def test_path_aliases_and_parent_swap(self):
        row = self.row()
        _, entries = self.service._facts(lambda: True)
        entry = next(e for e in entries if e['id'] == row['id'])
        for value in ['../short.mp4', '/tmp/short.mp4', 'docs//reports/ui_design_governance/short.mp4',
                      'docs/reports/./ui_design_governance/short.mp4', 'docs\\reports\\short.mp4', 'data/workbench/tasks.sqlite3']:
            malformed = copy.deepcopy(entry); malformed['artifact']['location']['value'] = value
            with self.subTest(value=value), self.assertRaises(ServiceError), self.service._open(malformed): pass
        directory = self.fixture.material; renamed = directory.with_name('renamed')
        directory.rename(renamed); directory.symlink_to(renamed, target_is_directory=True)
        try:
            with self.assertRaises(ServiceError): self.read(row)
        finally: directory.unlink(); renamed.rename(directory)

    def test_inflight_mutation_policy_revoke_and_session_loss_discard_bytes(self):
        row = self.row(); original_read = os.read
        for effect in ['file', 'policy', 'credential']:
            calls = []; allowed = [True]
            def intercepted(fd, size):
                data = original_read(fd, size); calls.append(len(data))
                if effect == 'file': self.video_path.write_bytes(self.video_data)
                elif effect == 'policy': self.registry.projects['fixture-project']['enabled'] = False
                else: allowed[0] = False
                return data
            with self.subTest(effect=effect), mock.patch('hub.material_service.os.read', side_effect=intercepted), self.assertRaises(ServiceError):
                self.service.read(row['id'], row['version'], range_header='bytes=0-15', authorize=lambda: allowed[0])
            self.assertEqual(calls, [16])
            self.registry.projects['fixture-project']['enabled'] = True
            self.service.prepare_fixture(self.ids)
            row = self.row()

    def test_single_range_exact_lengths_suffix_open_end_and_416(self):
        row = self.row(); size = len(self.video_data)
        for value, expected, status in [(None, self.video_data, 200), ('bytes=1-8', self.video_data[1:9], 206),
                ('bytes=1000-', self.video_data[1000:], 206), ('bytes=-9', self.video_data[-9:], 206),
                ('bytes=1000-99999', self.video_data[1000:], 206), ('bytes=99999-', b'', 416), ('bytes=-0', b'', 416)]:
            with self.subTest(value=value):
                r = self.read(row, range_header=value); self.assertEqual((r.status, r.data), (status, expected))
                self.assertEqual(r.headers['Accept-Ranges'], 'bytes')
                if status == 416: self.assertEqual(r.headers['Content-Range'], f'bytes */{size}')
                if status == 206: self.assertTrue(r.headers['Content-Range'].endswith('/'+str(size)))
        for value in ['bytes=0-2,4-8', 'bytes=', 'bytes=--4', 'items=0-1', 'bytes=0-999999999999999999999999', 'bytes= 0-1']:
            with self.subTest(value=value), mock.patch.object(self.service, '_open') as probe, self.assertRaises(ServiceError):
                self.read(row, range_header=value)
            probe.assert_not_called()

    def test_large_small_range_never_full_reads_and_capped_responses(self):
        large = self.fixture.material / 'large.mp4'
        large.write_bytes(self.video_data[:24] + b'x' * (MAX_RESPONSE * 3))
        self.add_artifacts([(large, 'fixture-large')]); self.service.prepare_fixture({'fixture-large'})
        row = self.row('fixture-large'); original_read = os.read; reads = []
        def counted(fd, count):
            data = original_read(fd, count); reads.append([count, len(data)]); return data
        with mock.patch('hub.material_service.os.read', side_effect=counted):
            r = self.read(row, range_header='bytes=2000000-2000031')
            self.assertEqual(r.data, b'x'*32); self.assertEqual(reads, [[32, 32]])
            for range_header in [None, 'bytes=0-', 'bytes=-99999999999999999999']:
                with self.assertRaises(ServiceError) as error: self.read(row, range_header=range_header)
                self.assertEqual(error.exception.status, 413)
        self.assertEqual(reads, [[32, 32]])

    def test_mime_hash_failed_pin_atomicity_and_real_gate(self):
        old = copy.deepcopy(self.service._pins)
        self.video_path.write_bytes(b'<script>wrong mime</script>')
        with self.assertRaises(ServiceError): self.service.prepare_fixture(self.ids)
        self.assertEqual(self.service._pins, old)
        self.fixture.store.fixture = False
        with mock.patch.object(self.service, '_open') as probe, self.assertRaises(ServiceError) as error:
            self.service.prepare_fixture(self.ids)
        self.assertEqual(error.exception.code, 'REAL_MATERIAL_GATE_CLOSED'); probe.assert_not_called()

    def test_registered_wrong_mime_and_invalid_utf8_are_not_inline(self):
        for name, body in [('false-video.mp4', b'<script>not MP4</script>'),
                           ('binary.txt', b'\xff\xfe'), ('null.txt', b'plain\0text')]:
            path = self.fixture.material / name; path.write_bytes(body)
            aid = 'fixture-' + name.replace('.', '-')
            self.add_artifacts([(path, aid)])
            with self.subTest(name=name), self.assertRaises(ServiceError) as error:
                self.service.prepare_fixture({aid})
            self.assertEqual(error.exception.status, 415)

    def test_descriptor_drift_and_revocation_never_resurrect_pins(self):
        old = self.row()
        self.add_artifacts([(self.text_path, 'fixture-new-report')])
        with self.assertRaises(ServiceError) as error: self.read(old)
        self.assertEqual(error.exception.code, 'MATERIAL_STALE')
        current = self.row()
        self.assertFalse(current['available'])
        with self.assertRaises(ServiceError): self.read(current)
        self.registry.projects['fixture-project']['root_path'] = '/fixture/changed-root'
        with mock.patch.object(self.service, '_open') as probe, self.assertRaises(ServiceError): self.read(current)
        probe.assert_not_called()

    def test_two_http_sessions_headers_revocation_and_extra_path_queries(self):
        h = fixture_http.LocalHTTPTests(); h.setUp(); self.addCleanup(h.doCleanups)
        h.server.materials = self.service
        h.session(); first = h.cookie
        h.session(); second = h.cookie
        row = self.row(); url = row['read_url']
        for cookie in [first, second]:
            status, headers, data = h.call(path=url, headers={'Cookie': cookie, 'Range': 'bytes=4-15'})
            self.assertEqual((status, data), (206, self.video_data[4:16]))
            self.assertEqual(headers['Content-Length'], '12'); self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(headers['Content-Type'], 'video/mp4'); self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn('sandbox', headers['Content-Security-Policy']); self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
            catalog = h.call(path='/api/materials', headers={'Cookie': cookie})[2]['data']
            self.assertEqual(catalog['store_revision'], self.fixture.revision)
        status, headers, data = h.call(path=url, headers={'Range':'bytes=999999-'})
        self.assertEqual((status, data, headers['Content-Length']), (416, b'', '0'))
        self.assertEqual(headers['Content-Range'], 'bytes */'+str(len(self.video_data)))
        h.server.sessions.revoke(first)
        with mock.patch.object(self.service, '_open', wraps=self.service._open) as probe:
            self.assertEqual(h.call(path=url, headers={'Cookie': first})[0], 401)
            self.assertEqual(h.call(path=url, authenticated=False)[0], 401)
            self.assertEqual(h.call(path=url+'&path=/etc/passwd')[0], 400)
            self.assertEqual(h.call(path=url, headers={'Sec-Fetch-Site':'cross-site'})[0], 403)
            probe.assert_not_called()
        self.assertEqual(self.fixture.store.path.read_bytes(), self.initial)


if __name__ == '__main__': unittest.main()
