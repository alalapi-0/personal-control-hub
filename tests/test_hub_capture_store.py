"""PNG payload validation and immutable no-follow local capture publication."""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.capture_store import CaptureStore, png_info, validate_capture_record
from hub.connection_records import RecordError


def chunk(kind, payload=b''):
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)


def png(width=1, height=1, raw=None, interlace=0, depth=8, color=6):
    if raw is None:
        raw = (b'\x00' + b'\x11\x22\x33\xff' * width) * height
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, depth, color, 0, 0, interlace))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND'))


def record(data=None, request_id='request-1', failed=False):
    data = png() if data is None else data
    sha = hashlib.sha256(data).hexdigest()
    return {'schema_version': '1.0', 'request_id': request_id, 'request_sha256': 'c' * 64,
            'project_id': 'business', 'page_id': 'home', 'domain': 'implementation',
            'status': 'failed' if failed else 'success', 'reason': 'CAPTURE_TIMEOUT' if failed else None,
            'method': 'web', 'captured_at': None if failed else '2026-10-15T12:00:00+09:00',
            'observed_at': '2026-10-15T03:00:01Z', 'source_sha256': 'a' * 64, 'build_sha256': None,
            'source_proof_sha256': 'b' * 64, 'runtime_verified': not failed,
            'artifact_ref': None if failed else 'data/previews/images/business/' + sha + '.png',
            'artifact_sha256': None if failed else sha, 'media_type': None if failed else 'image/png',
            'width': None if failed else 1, 'height': None if failed else 1,
            'expires_at': '2026-10-16T03:00:00Z',
            'provenance': {'kind': 'web_capture', 'report_sha256': None, 'reported_at': None, 'historical_candidate': None}}


class PngTests(unittest.TestCase):
    def test_actual_rgba_and_adam7_scanlines(self):
        self.assertEqual({'media_type': 'image/png', 'width': 1, 'height': 1}, png_info(png()))
        self.assertEqual(1, png_info(png(interlace=1))['width'])
        self.assertEqual(2, png_info(png(2, 2))['height'])

    def test_palette_png_valid_and_palette_required(self):
        header = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 1, 3, 0, 0, 0))
        signature = b'\x89PNG\r\n\x1a\n'
        body = chunk(b'IDAT', zlib.compress(b'\x00\x00')) + chunk(b'IEND')
        self.assertEqual(1, png_info(signature + header + chunk(b'PLTE', b'\xff\x00\x00') + body)['width'])
        with self.assertRaises(RecordError):
            png_info(signature + header + body)

    def test_signature_crc_truncation_and_trailing_data_rejected(self):
        good = png()
        altered = bytearray(good)
        altered[29] ^= 1
        for bad in (b'', b'JPEG', good[:-1], good + b'trailing', bytes(altered), bytearray(good)):
            with self.subTest(size=len(bad)), self.assertRaises(RecordError):
                png_info(bad)

    def test_actual_deflate_length_filter_and_stream_end_checked(self):
        for bad in (png(raw=b''), png(raw=b'\x05\x11\x22\x33\xff'), png(raw=b'\x00' * 6), png(raw=b'\x00' * 4)):
            with self.assertRaises(RecordError):
                png_info(bad)
        good = png()
        first_idat = good.index(b'IDAT') - 4
        for data in (b'not-zlib', zlib.compress(b'\x00\x11\x22\x33\xff') + b'extra',
                     zlib.compress(b'\x00\x11\x22\x33\xff')[:-2]):
            with self.assertRaises(RecordError):
                png_info(good[:first_idat] + chunk(b'IDAT', data) + chunk(b'IEND'))

    def test_chunk_order_critical_header_and_dimension_bounds(self):
        good = png()
        header, body = good[8:33], good[33:-12]
        signature = good[:8]
        for bad in (signature + body + header + chunk(b'IEND'), signature + header + header + body + chunk(b'IEND'),
                    signature + header + chunk(b'NOPE') + body + chunk(b'IEND'),
                    signature + header + body + chunk(b'tEXt', b'hello') + body + chunk(b'IEND'),
                    png(0), png(4097), png(4096, 4096, raw=b''), png(depth=3), png(color=1)):
            with self.assertRaises(RecordError):
                png_info(bad)

    def test_oversized_and_decompression_bomb_bounded(self):
        with self.assertRaises(RecordError):
            png_info(png() + b'0' * (10 * 1024 * 1024))
        with self.assertRaises(RecordError):
            png_info(png(raw=b'0' * 1_000_000))


class CaptureStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = CaptureStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def manifest(self, request_id='request-1'):
        return self.root / 'data/previews/captures/business' / (request_id + '.json')

    def test_constructor_and_missing_reads_never_create_namespace(self):
        self.assertEqual([], self.store.records('business'))
        self.assertEqual([], list(self.root.iterdir()))
        for bad in ('relative', self.root / '..' / 'other'):
            with self.assertRaises(RecordError):
                CaptureStore(bad)

    def test_commit_roundtrip_modes_detachment_and_exact_idempotence(self):
        value = record()
        before = copy.deepcopy(value)
        result = self.store.commit(value, png())
        inode = self.manifest().stat().st_ino
        self.assertEqual(before, result)
        result['provenance']['kind'] = 'changed'
        self.assertEqual(before, value)
        self.assertEqual([before], self.store.records('business'))
        self.assertEqual(png(), self.store.image(before))
        self.assertEqual(before, self.store.commit(value, png()))
        self.assertEqual(inode, self.manifest().stat().st_ino)
        for file in (self.manifest(), self.root / value['artifact_ref']):
            self.assertEqual(0o600, stat.S_IMODE(file.stat().st_mode))
            self.assertEqual(1, file.stat().st_nlink)
        for directory in (self.root / 'data', self.manifest().parent, (self.root / value['artifact_ref']).parent):
            self.assertEqual(0o700, stat.S_IMODE(directory.stat().st_mode))
        self.assertEqual([], list(self.root.rglob('.capture-*.tmp')))

    def test_same_id_different_request_or_record_conflicts_without_overwrite(self):
        original = record()
        self.store.commit(original, png())
        raw = self.manifest().read_bytes()
        for changes in ({'request_sha256': 'f' * 64}, {'page_id': 'other'}, {'runtime_verified': False}):
            with self.assertRaises(RecordError):
                self.store.commit({**original, **changes}, png())
            self.assertEqual(raw, self.manifest().read_bytes())

    def test_images_content_deduplicate_across_requests_without_link_aliases(self):
        self.store.commit(record(), png())
        self.store.commit(record(request_id='request-2'), png())
        self.assertEqual(2, len(self.store.records('business')))
        files = list((self.root / 'data/previews/images/business').iterdir())
        self.assertEqual(1, len(files))
        self.assertEqual(1, files[0].stat().st_nlink)
        self.assertEqual([], list(self.root.rglob('.capture-*.tmp')))

    def test_failure_receipt_has_no_image_and_preserves_prior_success(self):
        self.store.commit(record(), png())
        failed = record(request_id='request-2', failed=True)
        self.assertEqual(failed, self.store.commit(failed))
        self.assertEqual(png(), self.store.image(record()))
        with self.assertRaises(RecordError):
            self.store.image(failed)
        with self.assertRaises(RecordError):
            self.store.commit(record(request_id='request-3', failed=True), png())

    def test_success_requires_actual_png_and_matching_dimensions_hash(self):
        for data in (None, b'not-png', png(raw=b'\x00\xaa\xbb\xcc\xff')):
            with self.assertRaises(RecordError):
                self.store.commit(record(), data)
        with self.assertRaises(RecordError):
            self.store.commit({**record(), 'width': 2}, png())
        self.assertEqual([], list(self.root.iterdir()))

    def test_manifest_strict_fields_reason_types_and_method_provenance(self):
        original = record()
        for changes in ({'cookies': 'secret'}, {'request_sha256': None}, {'runtime_verified': 1}, {'width': True},
                        {'domain': 'design'}, {'artifact_ref': 'data/previews/images/other/a.png'},
                        {'captured_at': None}, {'observed_at': '2026-10-15'}, {'source_sha256': None}):
            with self.assertRaises(RecordError):
                validate_capture_record({**original, **changes})
        for reason in ('CAPTURE_TIMEOUT: http://private', 'timeout', '', 'A', 'A' * 81):
            with self.assertRaises(RecordError):
                validate_capture_record({**record(failed=True), 'reason': reason})
        bad = copy.deepcopy(original)
        bad['provenance']['kind'] = 'existing_desktop'
        with self.assertRaises(RecordError):
            validate_capture_record(bad)

    def test_desktop_report_time_never_becomes_capture_time_or_runtime(self):
        value = record()
        value.update(method='existing_desktop', captured_at=None, runtime_verified=False)
        value['provenance'].update(kind='existing_desktop', report_sha256='e' * 64,
                                   reported_at='2026-10-15T11:00:00+09:00', historical_candidate='f' * 64)
        self.assertEqual(value, self.store.commit(value, png()))
        self.assertIsNone(self.store.records('business')[0]['captured_at'])
        with self.assertRaises(RecordError):
            validate_capture_record({**value, 'runtime_verified': True})

    def test_root_ancestor_and_namespace_symlinks_are_refused(self):
        actual = self.root / 'actual'
        actual.mkdir()
        alias = self.root / 'alias'
        alias.symlink_to(actual, target_is_directory=True)
        for chosen in (alias, alias / 'child'):
            with self.assertRaises(RecordError):
                CaptureStore(chosen).records('business')
        (self.root / 'data').symlink_to(actual, target_is_directory=True)
        with self.assertRaises(RecordError):
            self.store.records('business')
        with self.assertRaises(RecordError):
            self.store.commit(record(), png())
        self.assertEqual([], list(actual.iterdir()))

    def test_manifest_symlink_hardlink_and_fifo_refused(self):
        self.store.commit(record(), png())
        path = self.manifest()
        outside = self.root / 'outside'
        path.rename(outside)
        path.symlink_to(outside)
        with self.assertRaises(RecordError):
            self.store.records('business')
        path.unlink()
        os.link(outside, path)
        with self.assertRaises(RecordError):
            self.store.records('business')
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(RecordError):
            self.store.records('business')

    def test_image_tampering_symlink_hardlink_and_png_metadata_refused(self):
        value = record()
        self.store.commit(value, png())
        path = self.root / value['artifact_ref']
        original = path.read_bytes()
        path.write_bytes(original + b'bad')
        with self.assertRaises(RecordError):
            self.store.image(value)
        path.write_bytes(original)
        with self.assertRaises(RecordError):
            self.store.image({**value, 'width': 2})
        outside = self.root / 'outside.png'
        path.rename(outside)
        path.symlink_to(outside)
        with self.assertRaises(RecordError):
            self.store.image(value)
        path.unlink()
        os.link(outside, path)
        with self.assertRaises(RecordError):
            self.store.image(value)

    def test_malformed_existing_records_never_silently_skip_or_overwrite(self):
        self.store.commit(record(), png())
        for raw in (b'{', b'[]', b'{"project_id":"business","project_id":"other"}', b'\xff', b' ' * 131073):
            self.manifest().write_bytes(raw)
            with self.assertRaises(RecordError):
                self.store.records('business')
            with self.assertRaises(RecordError):
                self.store.commit(record(request_id='request-2'), png())
            self.assertEqual(raw, self.manifest().read_bytes())

    def test_manifest_path_identity_and_orphan_diagnostic(self):
        self.store.commit(record(), png())
        value = record(request_id='wrong')
        self.manifest().write_text(json.dumps(value))
        with self.assertRaises(RecordError):
            self.store.records('business')
        self.manifest().write_text(json.dumps(record()))
        orphan = self.manifest().parent / '.capture-orphan.tmp'
        orphan.write_text('interrupted')
        with self.assertRaises(RecordError):
            self.store.records('business')

    def test_atomic_publish_failure_has_no_receipt_and_cleans_only_owned_temporary(self):
        original_link = os.link
        def fail_manifest(source, target, **kwargs):
            if target.endswith('.json'):
                raise OSError('simulated publication interruption')
            return original_link(source, target, **kwargs)
        with patch('hub.capture_store.os.link', side_effect=fail_manifest):
            with self.assertRaises(RecordError):
                self.store.commit(record(), png())
        self.assertEqual([], self.store.records('business'))
        self.assertFalse(self.manifest().exists())
        self.assertEqual([], list(self.root.rglob('.capture-*.tmp')))
        # An image without its receipt cannot become an accepted attempt.
        self.assertTrue((self.root / record()['artifact_ref']).exists())

    def test_file_and_parent_rebinding_during_manifest_read_are_rejected(self):
        self.store.commit(record(), png())
        original_read = os.read
        replaced = False
        def rebind_file(descriptor, count):
            nonlocal replaced
            raw = original_read(descriptor, count)
            if not replaced:
                replaced = True
                target = self.manifest()
                target.rename(target.with_suffix('.old'))
                target.write_bytes(raw)
            return raw
        with patch('hub.capture_store.os.read', side_effect=rebind_file):
            with self.assertRaises(RecordError):
                self.store.records('business')
        self.manifest().with_suffix('.old').unlink()
        replaced = False
        def rebind_parent(descriptor, count):
            nonlocal replaced
            raw = original_read(descriptor, count)
            if not replaced:
                replaced = True
                parent = self.manifest().parent
                parent.rename(parent.with_name('relocated'))
                parent.mkdir()
            return raw
        with patch('hub.capture_store.os.read', side_effect=rebind_parent):
            with self.assertRaises(RecordError):
                self.store.records('business')

    def test_record_limit_and_identifier_traversal(self):
        failed = record(failed=True)
        for index in range(256):
            self.store.commit({**failed, 'request_id': 'r-' + str(index)})
        self.assertEqual(256, len(self.store.records('business')))
        with self.assertRaises(RecordError):
            self.store.commit({**failed, 'request_id': 'r-overflow'})
        for project in ('../outside', '/tmp', '', None):
            with self.assertRaises(RecordError):
                self.store.records(project)


if __name__ == '__main__':
    unittest.main()
