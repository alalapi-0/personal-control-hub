import ast
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from hub.connection_records import validate_declaration
from hub.metric_import import read_metric_snapshot
from hub.metric_storage import collect_storage
from hub.metric_sources import read_structured
from hub.metrics import bounded_json, metric_key

NOW = '2026-09-09T00:00:00Z'
ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / 'governance/programs/storage_governance'


class StorageMetricTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.spec = {'evidence_root': str(self.root / 'evidence'), 'evidence_path': 'REPORT.yaml'}
        self.state = {'schema_version': 3, 'protocol_revision': 322,
                      'metadata': {'updated_at': '2026-09-05T22:11:33+08:00'},
                      'goal': {'objective': 'migrate scoped projects', 'phase': 'COMPLETED_SCOPED_ROOT_MIGRATION',
                               'status': 'COMPLETE_WITH_OWNER_REMOVAL',
                               'completion_basis': 'scoped_root_migration_complete_with_subplz_removed'},
                      'inventory_epoch': {'id': 'fixture_epoch', 'execution_candidate_count': 22},
                      'next_action': {'description': 'The frozen migration epoch is complete.'},
                      'control_plane_review': {'state': 'ACCEPTED'},
                      'closure': {'state': 'COMPLETE_WITH_OWNER_REMOVAL', 'all_scoped_projects_terminal': True,
                                  'final_report': str(self.root / 'evidence/REPORT.yaml')},
                      'project_accounting': {'cleaned_local_source_roots': 20,
                          'existing_external_projects_linked': 1, 'pending_projects': 0,
                          'active_projects': 0, 'remaining_execution_candidates': 0,
                          'removed_projects': ['/private/original'], 'released_bytes': None,
                          'external_added_bytes': None}}
        self.report = {'schema_version': 1, 'verified_at': '2026-09-04T09:53:36+08:00',
                       'external_copy_validation': 'PASS_before_each_source_cleanup',
                       'identity_guard': 'PASS', 'retained_local_sources': [],
                       'cleaned_local_sources': '20_exact_roots_replaced_with_symlinks',
                       'github_validation': '14 projects described in prose'}

    def collect(self):
        (self.root / 'STATE.yaml').write_text(json.dumps(self.state))
        (self.root / 'evidence').mkdir(exist_ok=True)
        (self.root / 'evidence/REPORT.yaml').write_text(json.dumps(self.report))
        result = collect_storage(self.root, 'fixture', NOW, self.spec)
        return result, {r['metric_id'].removeprefix('storage.'): r for r in result['metrics']}

    def test_true_fields_unknown_bytes_historical_bounds_and_budget(self):
        result, rows = self.collect()
        self.assertEqual(rows['cleaned_local_source_roots']['value'], 20)
        self.assertEqual(rows['removed_projects']['value'], 1)
        self.assertEqual(rows['pending_projects']['value'], 0)
        self.assertEqual(rows['historical_identity_guard_passed']['value'], 1)
        self.assertEqual(rows['historical_retained_local_sources']['value'], 0)
        for name in ('released_bytes', 'external_added_bytes', 'current_validation_failures'):
            self.assertIsNone(rows[name]['value'])
        self.assertEqual(rows['historical_identity_guard_passed']['business_at'], self.report['verified_at'])
        self.assertEqual(rows['cleaned_local_source_roots']['business_at'], self.state['metadata']['updated_at'])
        self.assertIsNone(rows['current_validation_failures']['business_at'])
        self.assertNotIn('/private/original', json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertNotIn('github_validation', json.dumps(result))
        self.assertLess(len(bounded_json({'metrics': [{k: r[k] for k in ('metric_id','value','unit','quality','business_at')} for r in result['metrics']]}).encode()), 8192)
        self.assertEqual(len({metric_key(r) for r in result['metrics']}), len(rows))

    def test_bad_field_isolated(self):
        self.state['project_accounting']['active_projects'] = True
        self.report['identity_guard'] = 'PASSED (natural language)'
        _, rows = self.collect()
        self.assertIsNone(rows['active_projects']['value'])
        self.assertIsNone(rows['historical_identity_guard_passed']['value'])
        self.assertEqual(rows['pending_projects']['value'], 0)
        self.assertEqual(rows['historical_external_copy_validation_passed']['value'], 1)

    def test_pointer_change_never_follows_unconfigured_path(self):
        self.state['closure']['final_report'] = '/private/other/REPORT.yaml'
        with patch('hub.metric_storage.read_structured', wraps=read_structured) as reader:
            _, rows = self.collect()
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(reader.call_args.args[1], 'STATE.yaml')
        self.assertIsNone(rows['historical_identity_guard_passed']['value'])
        self.assertEqual(rows['cleaned_local_source_roots']['value'], 20)

    def test_semantic_versions_and_identity_dedup(self):
        first, _ = self.collect()
        self.state['protocol_revision'] += 1
        self.state['goal'] = {'status': 'complete'}
        self.report['github_validation'] = 'OTHER PROSE'
        self.state['project_accounting']['removed_projects'] *= 2
        second, rows = self.collect()
        self.assertEqual(first['source_version'], second['source_version'])
        self.assertEqual(rows['removed_projects']['value'], 1)
        self.state['project_accounting']['removed_projects'].append('/private/other')
        third, _ = self.collect()
        self.assertNotEqual(second['source_version'], third['source_version'])

    def test_invalid_list_does_not_erase_independent_counts(self):
        self.state['project_accounting']['removed_projects'] = [{'path': '/private/original'}]
        self.report['retained_local_sources'] = 'unknown'
        _, rows = self.collect()
        self.assertIsNone(rows['removed_projects']['value'])
        self.assertIsNone(rows['historical_retained_local_sources']['value'])
        self.assertEqual(rows['cleaned_local_source_roots']['value'], 20)

    def test_invalid_evidence_path_is_never_read(self):
        self.spec['evidence_path'] = '../REPORT.yaml'
        self.state['closure']['final_report'] = str(self.root / 'evidence/../REPORT.yaml')
        with patch('hub.metric_storage.read_structured', wraps=read_structured) as reader:
            _, rows = self.collect()
        self.assertEqual(reader.call_count, 1)
        self.assertIsNone(rows['historical_identity_guard_passed']['value'])

    def test_unsupported_schema_and_missing_configuration(self):
        self.report['schema_version'] = 2
        _, rows = self.collect()
        self.assertIsNone(rows['historical_identity_guard_passed']['value'])
        self.assertEqual(rows['cleaned_local_source_roots']['value'], 20)
        self.spec = {}
        _, rows = self.collect()
        self.assertIsNone(rows['historical_external_copy_validation_passed']['value'])

    def test_project_entry_exports_one_bound_standard_snapshot(self):
        declaration = yaml.safe_load((PROGRAM / 'hub.connection.yaml').read_text())
        validate_declaration(declaration, 'storage_governance')
        script_path = PROGRAM / declaration['metric_export']['entry']
        script = script_path.read_text()
        constants = {
            node.targets[0].id: node.value.value
            for node in ast.parse(script).body
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant))
        }
        config = yaml.safe_load((ROOT / 'data/connections/metric_sources.yaml').read_text())
        storage_config = config['projects']['storage_governance']
        self.assertEqual(constants['EVIDENCE_ROOT'], storage_config['evidence_root'])
        self.assertEqual(constants['EVIDENCE_PATH'], storage_config['evidence_path'])
        registry = yaml.safe_load((ROOT / 'data/registry/external_projects.yaml').read_text())
        registered = next(item for item in registry['projects'] if item['id'] == 'storage_governance')
        self.assertIn('hub.connection.yaml', registered['watch_paths'])
        self.assertIn(declaration['metric_export']['entry'], registered['watch_paths'])

        hub = self.root / 'hub'
        project = self.root / 'project'
        evidence = self.root / 'export-evidence'
        (hub / 'data/registry').mkdir(parents=True)
        (hub / 'src').symlink_to(ROOT / 'src', target_is_directory=True)
        (project / 'scripts').mkdir(parents=True)
        evidence.mkdir()
        state = copy.deepcopy(self.state)
        state['closure']['final_report'] = str(evidence / 'REPORT.yaml')
        (project / 'STATE.yaml').write_text(yaml.safe_dump(state, sort_keys=False))
        (project / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration, sort_keys=False))
        (project / declaration['metric_export']['entry']).write_text(script)
        (evidence / 'REPORT.yaml').write_text(yaml.safe_dump(self.report, sort_keys=False))
        registry = {'projects': [{
            'id': 'storage_governance', 'name': 'StorageGovernance',
            'root_path': str(project), 'enabled': True, 'connection_read_allowed': True,
            'current_state_paths': ['STATE.yaml'],
        }]}
        (hub / 'data/registry/external_projects.yaml').write_text(yaml.safe_dump(registry, sort_keys=False))

        patched = script.replace(constants['EVIDENCE_ROOT'], str(evidence))
        patched = patched.replace(constants['EVIDENCE_PATH'], 'REPORT.yaml')
        environment = {
            'PATH': '', 'PYTHONNOUSERSITE': '1', 'PYTHONSAFEPATH': '1',
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1',
        }
        result = subprocess.run(
            [sys.executable, '-I', '-B', '-', '--hub-root', str(hub),
             '--project-root', str(project)],
            input=patched.encode(), cwd=project, env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        snapshot = read_metric_snapshot(project)
        self.assertEqual(snapshot['project_id'], 'storage_governance')
        self.assertEqual(snapshot['exporter']['id'], 'storage-governance-export')
        self.assertEqual(snapshot['management']['business']['current_work']['status'], 'complete')
        self.assertTrue(snapshot['management']['business']['current_work']['accepted'])
        self.assertEqual(snapshot['management']['business']['delivery']['status'], 'delivered')
        values = {row['metric_id']: row['value'] for row in snapshot['metrics']}
        self.assertEqual(values['storage.cleaned_local_source_roots'], 20)
        self.assertEqual(values['storage.historical_identity_guard_passed'], 1)
        self.assertIsNone(values['storage.current_validation_failures'])
        self.assertLessEqual((project / '.hub/status.json').stat().st_size, 8 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
