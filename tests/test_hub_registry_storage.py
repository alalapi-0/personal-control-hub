"""Preserve storage and authority checks while keeping removed roots inert."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from hub.services.project_registry_service import load_registry, validate_registry


class RegistryStorageTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()

    @staticmethod
    def project(registry, project_id):
        return next(p for p in registry['projects'] if p['id'] == project_id)

    def test_metadata_only_is_explicit_and_never_probes_local_roots(self):
        with patch.object(Path, 'exists', side_effect=AssertionError('metadata validation probed a path')):
            result = validate_registry(self.registry, check_paths=False)
        self.assertTrue(result['valid'], result['hard_blockers'])
        self.assertFalse(result['path_availability_checked'])
        self.assertEqual(result['project_count'], 26)

    def local_fixture(self, directory):
        registry = deepcopy(self.registry)
        for project in registry['projects']:
            if project['id'] == 'manga-localizer' or project.get('current_state_status') == 'removed_local':
                continue
            root = directory / project['id']
            root.mkdir()
            (root / 'AGENTS.md').write_text('Synthetic rules')
            (root / 'STATE.yaml').write_text('status: synthetic')
            project.update(root_path=str(root), watch_paths=['.'], rules_paths=[str(root / 'AGENTS.md')],
                           current_state_paths=[str(root / 'STATE.yaml')], supporting_authority_paths=[])
        return registry

    def test_runtime_still_rejects_missing_authority_and_unwatched_source(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = self.local_fixture(Path(directory))
            self.assertTrue(validate_registry(registry)['valid'])
            project = self.project(registry, 'light-novel')
            Path(project['current_state_paths'][0]).unlink()
            missing = validate_registry(registry)
            self.assertFalse(missing['valid'])
            self.assertTrue(missing['path_availability_checked'])
            self.assertTrue(any('light-novel' in e and 'authority' in e for e in missing['hard_blockers']))
            self.assertTrue(validate_registry(registry, check_paths=False)['valid'])
            project['watch_paths'] = ['unrelated']
            unwatched = validate_registry(registry, check_paths=False)
            self.assertFalse(unwatched['valid'])
            self.assertTrue(any('watch_paths' in e for e in unwatched['hard_blockers']))

    def test_removed_and_storage_excluded_roots_never_receive_probes(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = self.local_fixture(Path(directory))
            exists = Path.exists
            def bounded(path):
                self.assertTrue(path.is_relative_to(directory), 'excluded original root probed')
                return exists(path)
            with patch.object(Path, 'exists', bounded):
                self.assertTrue(validate_registry(registry)['valid'])
                for field, value in [('enabled', True), ('current_state_paths', ['never-read.yaml'])]:
                    broken = deepcopy(registry)
                    self.project(broken, 'manga-removed-local')[field] = value
                    self.assertFalse(validate_registry(broken)['valid'])
                broken = deepcopy(registry)
                self.project(broken, 'manga-removed-local')['local_presence'] = {}
                self.assertFalse(validate_registry(broken)['valid'])

    def test_storage_scope_and_effect_boundaries_cannot_be_lifted(self):
        mutations = [
            ('manga-localizer', 'storage_governance', 'inspection_allowed', True),
            ('manga-localizer', 'storage_governance', 'mutation_allowed', True),
            ('manga-localizer', 'storage_governance', 'scope', 'project_candidate'),
            ('personal-control-hub', 'storage_governance', 'migration_allowed', True),
            ('personal-control-hub', 'storage_governance', 'cleanup_allowed', True),
            ('storage_governance', 'storage_governance', 'cleanup_allowed', True),
        ]
        for project_id, field, key, value in mutations:
            with self.subTest(project=project_id, key=key):
                broken = deepcopy(self.registry)
                self.project(broken, project_id)[field][key] = value
                self.assertFalse(validate_registry(broken, check_paths=False)['valid'])
        broken = deepcopy(self.registry)
        broken['policy']['project_registry_is_effect_authority'] = True
        self.assertFalse(validate_registry(broken, check_paths=False)['valid'])

    def test_manga_business_route_is_exact_and_does_not_expand_storage_access(self):
        self.assertTrue(validate_registry(self.registry, check_paths=False)['valid'])
        self.assertEqual(['.agent/STATE.yaml'],
                         self.project(self.registry, 'manga-localizer')['current_state_paths'])
        for key, value in [('current_state_paths', ['.agent/STATE.md']),
                           ('current_state_paths', ['other.yaml']),
                           ('rules_paths', ['AGENTS.md']),
                           ('connection_authority', 'unrelated owner'),
                           ('access_profile', 'unbounded'),
                           ('connection_read_allowed', False)]:
            with self.subTest(key=key):
                broken = deepcopy(self.registry)
                self.project(broken, 'manga-localizer')[key] = value
                self.assertFalse(validate_registry(broken, check_paths=False)['valid'])
        legacy = deepcopy(self.registry)
        self.project(legacy, 'manga-localizer')['current_state_paths'] = []
        self.assertTrue(validate_registry(legacy, check_paths=False)['valid'])

    def test_live_projects_keep_required_authority_and_storage_contract(self):
        for field, value in [('rules_paths', None), ('current_state_status', ''),
                             ('storage_governance', None)]:
            with self.subTest(field=field):
                broken = deepcopy(self.registry)
                self.project(broken, 'light-novel')[field] = value
                self.assertFalse(validate_registry(broken, check_paths=False)['valid'])
        broken = deepcopy(self.registry)
        broken['storage_governance_contract']['sole_execution_state'] = 'unrelated-state.yaml'
        self.assertFalse(validate_registry(broken, check_paths=False)['valid'])


if __name__ == '__main__':
    unittest.main()
