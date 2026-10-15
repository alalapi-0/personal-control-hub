"""Pure allowlist boundaries and real temporary filesystem preview guards."""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hub.connection_records import RecordError
from hub.preview_policy import (
    authorize_capture_url, read_preview_artifact, validate_artifact_ref,
    validate_capture_policy,
)


def policy(**changes):
    result = {'schema_version': '1.0', 'project_id': 'project-a',
              'allowed_urls': ['http://127.0.0.1:9417/projects/a#/overview'],
              'allowed_read_paths': ['data/previews/project-a/view.png'],
              'max_redirects': 0, 'retention': 'local_only'}
    result.update(changes)
    return result


class PreviewPolicyTests(unittest.TestCase):
    def test_lexical_references_are_literal_and_perform_no_io(self):
        refs = ['data/previews/a.png', 'data/previews/project-a/overview-2.JPG',
                'data/previews/a/deep/shot.webp', 'data/previews/a/view.jpeg', 'data/previews/a.gif']
        with patch('hub.preview_policy.os.open', side_effect=AssertionError('unexpected file read')):
            for ref in refs:
                self.assertEqual(validate_artifact_ref(ref), ref)

    def test_unsafe_lexical_paths_are_rejected(self):
        refs = [None, {}, '', '/data/previews/a.png', '../data/previews/a.png',
                'data/previews/../a.png', 'data/previews/a/./view.png',
                'data/previews//view.png', 'data/previews/a\\view.png',
                'data/previews/%2e%2e/a.png', 'data/previews/shot%20a.png',
                'data/previews/a\x00.png', 'data/previews/a.svg',
                'data/other/a.png', 'data/previews/.git/view.png',
                'data/previews/credentials/view.png', 'data/previews/cookie.png',
                'data/previews/api-key.png', 'data/previews/access_token.png',
                'data/previews/session/view.png', 'data/previews/.env.png',
                'data/previews/' + 'x' * 129 + '.png']
        for ref in refs:
            with self.subTest(ref=ref), self.assertRaises(RecordError):
                validate_artifact_ref(ref)

    def test_policy_is_detached_and_permits_exact_explicit_loopback_ui(self):
        urls = ['http://127.0.0.1:9417/projects/a#/overview',
                'https://[::1]:9443/app?view=overview#!/pages/main']
        original = policy(allowed_urls=urls)
        checked = validate_capture_policy(original)
        checked['allowed_read_paths'].clear()
        self.assertEqual(original['allowed_read_paths'], ['data/previews/project-a/view.png'])
        for url in urls:
            self.assertEqual(authorize_capture_url(original, project_id='project-a', url=url), url)

    def test_empty_allowlist_wrong_project_and_arbitrary_urls_fail_closed(self):
        allowed = policy()
        denied = ['http://127.0.0.1:9417/', 'http://127.0.0.1:9417/api/tasks',
                  'http://127.0.0.1:9418/projects/a#/overview',
                  'http://127.0.0.1:9417/projects/b#/overview',
                  'http://localhost:9417/projects/a#/overview',
                  'https://example.org:9417/projects/a#/overview']
        for url in denied:
            with self.subTest(url=url), self.assertRaises(RecordError):
                authorize_capture_url(allowed, project_id='project-a', url=url)
        with self.assertRaises(RecordError):
            authorize_capture_url(allowed, project_id='project-b', url=allowed['allowed_urls'][0])
        with self.assertRaises(RecordError):
            authorize_capture_url(policy(allowed_urls=[]), project_id='project-a', url=allowed['allowed_urls'][0])

    def test_even_same_origin_redirects_are_rejected(self):
        allowed = policy(); url = allowed['allowed_urls'][0]
        for chain in ([url], (url,), ['http://127.0.0.1:9417/'], 'unknown', None):
            with self.subTest(chain=chain), self.assertRaises(RecordError):
                authorize_capture_url(allowed, project_id='project-a', url=url, redirect_chain=chain)

    def test_policy_rejects_unsafe_urls_and_sensitive_data(self):
        urls = ['http://localhost:9417/', 'http://127.1:9417/', 'http://2130706433:9417/',
                'http://127.0.0.2:9417/', 'http://127.0.0.1/', 'http://[::1]/',
                'http://127.0.0.1:0/', 'http://127.0.0.1:65536/',
                'http://127.0.0.1:09417/', 'http://owner:pass@127.0.0.1:9417/',
                'file:///tmp/view.png', 'https://example.org:443/',
                'http://127.0.0.1:9417/../secret', 'http://127.0.0.1:9417/%2e%2e/',
                'http://127.0.0.1:9417/credentials', 'http://127.0.0.1:9417/?token=abc',
                'http://127.0.0.1:9417/?session_id=abc',
                'http://127.0.0.1:9417/#/oauth/callback', 'http://127.0.0.1:9417/#/a%2fb',
                'http://127.0.0.1:9417/#/../a', 'http://127.0.0.1:9417/#//external.test', 'http://127.0.0.1:9417/#javascript:alert(1)',
                'http://127.0.0.1:9417/\n', 'http://127.0.0.1:9417/\\example.org',
                'http://127.0.0.1:9417/?password=private1234']
        for url in urls:
            with self.subTest(url=url), self.assertRaises(RecordError):
                validate_capture_policy(policy(allowed_urls=[url]))

    def test_policy_shape_bounds_and_inventory_do_not_grant_authority(self):
        malformed = [policy(schema_version='1.1'), policy(max_redirects=True), policy(max_redirects=1),
                     policy(retention='remote'), policy(project_id='../x'), policy(cookies={}),
                     policy(allowed_read_paths=['data/previews/a.png'] * 2),
                     policy(allowed_urls=['http://127.0.0.1:9417/'] * 2),
                     policy(allowed_urls='http://127.0.0.1:9417/'),
                     policy(allowed_urls=['http://127.0.0.1:9417/' + str(i) for i in range(1001)]),
                     {'schema_version': '1.0', 'project_id': 'project-a', 'domains': [], 'previews': []}]
        for item in malformed:
            with self.subTest(item=item), self.assertRaises(RecordError):
                validate_capture_policy(item)


class PreviewReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ref = 'data/previews/project-a/view.png'
        self.path = self.root / self.ref
        self.path.parent.mkdir(parents=True)
        self.data = b'local image bytes\x00\x01'
        self.path.write_bytes(self.data)
        self.digest = hashlib.sha256(self.data).hexdigest()

    def read(self, **kwargs):
        return read_preview_artifact(self.root, self.ref, expected_sha256=kwargs.pop('expected_sha256', self.digest), **kwargs)

    def test_exact_read_returns_bytes_and_leaves_tree_unchanged(self):
        before = self.path.stat()
        self.assertEqual(self.read(max_bytes=len(self.data)), self.data)
        after = self.path.stat()
        self.assertEqual((before.st_ino, before.st_size, before.st_mtime_ns),
                         (after.st_ino, after.st_size, after.st_mtime_ns))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_budget_hash_and_missing_rejections_hide_private_paths(self):
        for args in ({'expected_sha256': '0' * 64}, {'expected_sha256': 'invalid'},
                     {'max_bytes': len(self.data) - 1}, {'max_bytes': 0},
                     {'max_bytes': True}, {'max_bytes': 10485761}):
            with self.subTest(args=args), self.assertRaises(RecordError):
                self.read(**args)
        self.path.unlink()
        with self.assertRaises(RecordError) as raised:
            self.read()
        self.assertNotIn(str(self.root), str(raised.exception))
        self.assertNotIn(self.ref, str(raised.exception))

    def test_final_symlink_hardlink_fifo_and_directory_rejected(self):
        outside = self.root / 'outside.png'; outside.write_bytes(self.data)
        self.path.unlink(); self.path.symlink_to(outside)
        with self.assertRaises(RecordError): self.read()
        self.path.unlink(); self.path.write_bytes(self.data)
        hardlink = self.root / 'linked.png'; os.link(self.path, hardlink)
        with self.assertRaises(RecordError): self.read()
        hardlink.unlink(); self.path.unlink(); os.mkfifo(self.path)
        with self.assertRaises(RecordError): self.read()
        self.path.unlink(); self.path.mkdir()
        with self.assertRaises(RecordError): self.read()
        self.assertEqual(outside.read_bytes(), self.data)

    def test_intermediate_and_root_symlinks_rejected(self):
        original = self.path.parent
        moved = self.root / 'moved'; original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(RecordError): self.read()
        alias = self.root / 'root-alias'; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(RecordError):
            read_preview_artifact(alias, self.ref, expected_sha256=self.digest)

    def test_escape_relative_root_and_sensitive_refs_never_open(self):
        with patch('hub.preview_policy.os.open', side_effect=AssertionError('unexpected filesystem access')):
            for ref in ('../outside.png', '/tmp/outside.png', 'data/previews/%2e%2e/a.png',
                        'data/previews/access-token.png', 'data/previews/a\\b.png'):
                with self.subTest(ref=ref), self.assertRaises(RecordError):
                    read_preview_artifact(self.root, ref, expected_sha256=self.digest)
            with self.assertRaises(RecordError):
                read_preview_artifact(Path('relative'), self.ref, expected_sha256=self.digest)

    def test_file_mutation_during_read_rejected(self):
        original = os.fstat; calls = []
        inode = self.path.stat().st_ino
        def changed(fd):
            info = original(fd)
            if info.st_ino == inode:
                calls.append(True)
                if len(calls) == 2:
                    self.path.write_bytes(b'changed image bytes')
                    info = original(fd)
            return info
        with patch('hub.preview_policy.os.fstat', side_effect=changed), self.assertRaises(RecordError):
            self.read()
        self.assertEqual(len(calls), 2)

    def test_atomic_file_name_replacement_rejected_even_with_same_bytes(self):
        original = os.stat; replaced = []
        def changed(path, *args, **kwargs):
            if path == self.path.name and kwargs.get('dir_fd') is not None and not replaced:
                replaced.append(True)
                replacement = self.path.parent / 'next.png'; replacement.write_bytes(self.data)
                replacement.replace(self.path)
            return original(path, *args, **kwargs)
        with patch('hub.preview_policy.os.stat', side_effect=changed), self.assertRaises(RecordError):
            self.read()
        self.assertEqual(replaced, [True])

    def test_atomic_parent_replacement_rejected(self):
        original = os.stat; replaced = []
        def changed(path, *args, **kwargs):
            if path == 'previews' and kwargs.get('dir_fd') is not None and not replaced:
                replaced.append(True)
                directory = self.root / 'data/previews'
                directory.rename(self.root / 'detached-previews'); directory.mkdir()
            return original(path, *args, **kwargs)
        with patch('hub.preview_policy.os.stat', side_effect=changed), self.assertRaises(RecordError):
            self.read()
        self.assertEqual(replaced, [True])


if __name__ == '__main__':
    unittest.main()
