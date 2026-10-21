"""Exact rebuild-schema package names; disposable files, no ledger or service."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from hub.rr_binding import verify_program
from hub.rr_payload import RRFeedbackError, canonical_json
import test_hub_rr_current as current_fixture


SCHEMAS = ('schemas/rebuild/task-package.schema.json',
           'schemas/rebuild/receipt.schema.json')
BODY = b'{"synthetic_schema":1}\n'


class PlanPackageTests(unittest.TestCase):
    write = staticmethod(current_fixture.CurrentTests.write)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix='owned-rr-package-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = {'commit': 'a' * 40, 'dirty': False}
        for name in SCHEMAS:
            self.artifact(name)

    def artifact(self, name, body=BODY):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(body)
        path.chmod(0o600)
        return path

    def manifest(self, names=SCHEMAS):
        files = {name: {'size': len(BODY), 'sha256': hashlib.sha256(BODY).hexdigest()}
                 for name in names}
        program = hashlib.sha256(canonical_json(files)).hexdigest()
        self.write(self.root / 'rr-runtime-manifest.json',
                   {'schema': 1, 'program_id': program, 'files': files, 'source': self.source})
        return program

    def reject(self, program):
        with self.assertRaises(RRFeedbackError):
            verify_program(self.root, program)

    def test_exact_two_schema_artifacts_and_each_individual_name_are_admitted(self):
        for names in (SCHEMAS, SCHEMAS[:1], SCHEMAS[1:]):
            with self.subTest(names=names):
                self.assertEqual(verify_program(self.root, self.manifest(names)), self.source)

    def test_legacy_src_scripts_and_project_manifest_still_works(self):
        names = ('src/hub/fixture.py', 'scripts/fixture.py', 'project.yaml')
        for name in names:
            self.artifact(name)
        self.assertEqual(verify_program(self.root, self.manifest(names)), self.source)

    def test_schema_tamper_and_missing_artifacts_are_rejected(self):
        for name in SCHEMAS:
            with self.subTest(name=name):
                program = self.manifest()
                path = self.root / name
                path.write_bytes(BODY.replace(b'1', b'2'))
                self.reject(program)
                path.unlink()
                self.reject(program)
                self.artifact(name)

    def test_schema_file_symlink_hardlink_and_shared_write_mode_are_rejected(self):
        for name in SCHEMAS:
            with self.subTest(name=name):
                program = self.manifest()
                path = self.root / name
                other = self.artifact('owned-other.json')
                path.unlink(); path.symlink_to(other)
                self.reject(program)
                path.unlink(); path.hardlink_to(other)
                self.reject(program)
                path.unlink(); other.unlink(); self.artifact(name)
                path.chmod(0o666)
                self.reject(program)
                path.chmod(0o600)

    def test_schema_parent_directory_symlink_is_rejected(self):
        program = self.manifest()
        original = self.root / 'schemas/rebuild'
        retained = self.root / 'owned-retained'
        original.rename(retained); original.symlink_to(retained, target_is_directory=True)
        self.reject(program)

    def test_unknown_schema_names_and_nested_siblings_are_rejected_even_if_present(self):
        for name in ('schemas/rebuild/unknown.schema.json', 'schemas/receipt.schema.json',
                     'schemas/rebuild/nested/task-package.schema.json',
                     'schemas/rebuild/task-package.schema.json.extra',
                     'schemas/rebuild/Receipt.schema.json', 'other/receipt.schema.json'):
            with self.subTest(name=name):
                self.artifact(name)
                self.reject(self.manifest((SCHEMAS[0], name)))

    def test_noncanonical_schema_spellings_and_traversal_are_rejected(self):
        for canonical in SCHEMAS:
            aliases = ('./' + canonical, canonical.replace('/', '//', 1),
                       canonical.replace('rebuild/', 'rebuild/./'),
                       canonical.replace('rebuild/', 'rebuild/../rebuild/'),
                       canonical + '/', canonical.replace('/', '\\'),
                       str(self.root / canonical), 'src/../' + canonical,
                       'schemas/rebuild/../../' + canonical)
            for name in aliases:
                with self.subTest(name=name):
                    # All normalizing aliases refer to existing matching bytes.
                    # A spy reader would not prove the no-link/ownership rules.
                    self.reject(self.manifest((name,)))

    def test_manifest_program_digest_and_metadata_integrity_remain_required(self):
        program = self.manifest()
        self.reject('0' * 64)
        manifest = self.root / 'rr-runtime-manifest.json'
        raw = manifest.read_bytes()
        manifest.chmod(0o666)
        self.reject(program)
        manifest.chmod(0o600)
        manifest.unlink()
        self.reject(program)
        retained = self.root / 'owned-manifest.json'
        retained.write_bytes(raw); retained.chmod(0o600)
        manifest.symlink_to(retained)
        self.reject(program)


if __name__ == '__main__':
    unittest.main()
