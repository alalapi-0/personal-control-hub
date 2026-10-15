from __future__ import annotations

import copy
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.preview_service import PreviewService
from hub.service_contract import ServiceError
from hub.task_service import TaskService
from hub.task_store import TaskStore
from hub.workbench_store import WorkbenchStore
import test_hub_workbench as fixture


class TaskDraftTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixture.WorkbenchTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.store = TaskStore(self.helper.root)
        self.service = TaskService(self.helper.projects, self.helper.service, self.store)

    def command(self, **changes):
        return self.helper.command(task_spec={
            'requirements': 'Explain the selected fixture layout change',
            'scope': 'Only the declared fixture page; no execution authority',
            'acceptance': 'Owner can read the saved requirements after reopening'}, **changes)

    def save(self, command=None, **options):
        return self.service.create_draft(command or self.command(), backend_instance='b' * 32, **options)

    def test_persist_reopen_replay_and_legacy_annotation_compatibility(self):
        command = self.command()
        saved = self.save(command)
        self.assertEqual(saved['draft']['status'], 'draft')
        self.assertEqual(saved['draft']['task_spec'], command['task_spec'])
        self.assertEqual(saved['draft']['binding'], command['binding'])
        self.assertFalse(saved['draft']['execution_allowed'])
        self.assertFalse(self.store.path.exists())
        original = self.helper.store.path.read_bytes()
        self.assertTrue(self.save(command)['replayed'])
        self.assertEqual(original, self.helper.store.path.read_bytes())
        preview = PreviewService(self.helper.projects, self.helper.fixture.service,
                                 WorkbenchStore(self.helper.root, fixture=True))
        reopened = TaskService(self.helper.projects, preview, TaskStore(self.helper.root))
        self.assertEqual(reopened.draft(command['request_id'])['draft'], saved['draft'])
        self.assertEqual(len(reopened.drafts()['drafts']), 1)
        self.helper.save(self.helper.command(request_id='legacy-feedback', expected_revision=1))
        self.assertEqual(len(reopened.drafts()['drafts']), 1)
        with self.assertRaises(ServiceError): reopened.draft('legacy-feedback')
        self.assertEqual(self.helper.store.read()['revision'], 2)

    def test_invalid_scope_shape_and_nonempty_bounded_text_zero_effect(self):
        command = self.command()
        bad = []
        missing = copy.deepcopy(command); del missing['task_spec']; bad.append(missing)
        for key in ('cwd', 'shell', 'grant_id'):
            item = copy.deepcopy(command); item['task_spec'][key] = '/tmp'; bad.append(item)
            bad.append({**command, key: '/tmp'})
        for key in ('requirements', 'scope', 'acceptance'):
            for value in ('', '   ', 'x' * 2001, None, '\ud800'):
                item = copy.deepcopy(command); item['task_spec'][key] = value; bad.append(item)
        with mock.patch.object(self.helper.service, 'save', wraps=self.helper.service.save) as save:
            for item in bad:
                with self.subTest(item=item), self.assertRaises(ServiceError) as caught:
                    self.save(item)
                self.assertEqual(caught.exception.code, 'INVALID_TASK_DRAFT')
            save.assert_not_called()
        self.assertFalse(self.helper.store.path.exists())
        self.assertFalse(self.store.path.exists())

    def test_binding_and_image_registry_drift_are_rejected(self):
        command = self.command()
        for key, value in [('project_id', 'other-project'), ('candidate_revision', 99),
                           ('artifact_sha256', '0' * 64), ('registry_hash', '0' * 64),
                           ('pages', ['other-page'])]:
            item = copy.deepcopy(command); item['binding'][key] = value
            with self.subTest(key=key), self.assertRaises(ServiceError): self.save(item)
        self.assertFalse(self.helper.store.path.exists())
        self.save(command)
        original = self.helper.store.path.read_bytes()
        self.helper.registry.authority['registry_hash'] = 'c' * 64
        self.assertEqual(self.service.draft(command['request_id'])['draft']['material_state'], 'stale')
        with self.assertRaises(ServiceError): self.save(command)
        self.helper.registry.authority['registry_hash'] = 'a' * 64
        self.helper.fixture.png_path.write_bytes(b'changed image')
        with self.assertRaises(ServiceError):
            self.save(self.command(request_id='new-draft', expected_revision=1))
        self.assertEqual(original, self.helper.store.path.read_bytes())

    def test_revision_request_conflicts_and_authorization_preserve_history(self):
        command = self.command(); self.save(command)
        original = self.helper.store.path.read_bytes()
        changed = copy.deepcopy(command); changed['task_spec']['scope'] = 'Different scope'
        for item in (changed, self.command(request_id='conflicting-revision')):
            with self.assertRaises(ServiceError) as caught: self.save(item)
            self.assertEqual(caught.exception.status, 409)
        def denied(): raise ServiceError('OWNER_REQUIRED', status=403)
        with self.assertRaises(ServiceError):
            self.save(self.command(request_id='denied-draft', expected_revision=1), authorize=denied)
        self.assertEqual(original, self.helper.store.path.read_bytes())

    def test_no_task_store_or_agent_entry_and_no_new_existing_task_rows(self):
        with (mock.patch.object(self.store, 'grant', side_effect=AssertionError('grant lookup')),
              mock.patch.object(self.store, 'grants', side_effect=AssertionError('grant listing')),
              mock.patch.object(self.store, 'admit', side_effect=AssertionError('enqueue')),
              mock.patch('hub.codex_adapter.AppServerAdapter.open', side_effect=AssertionError('Agent startup')),
              mock.patch('hub.task_worker.TaskWorker.run_one', side_effect=AssertionError('worker startup'))):
            self.save()
            self.service.drafts()
            self.service.draft('fixture-annotation')
        self.assertFalse(self.store.path.exists())
        self.assertFalse(self.store.registration_path.exists())
        self.store.register_storage()
        before = self.store.path.read_bytes()
        self.save(self.command(request_id='second-draft', expected_revision=1))
        self.assertEqual(before, self.store.path.read_bytes())
        with sqlite3.connect(self.store.path) as connection:
            self.assertEqual(connection.execute('select count(*) from tasks').fetchone()[0], 0)
            self.assertEqual(connection.execute('select count(*) from grants').fetchone()[0], 0)
        payload = {'request_id':'never-task','project_id':'fixture-project',
                   'annotation_id':'second-draft','annotation_revision':2,
                   'grant_id':'never-grant','grant_version':1,'mode':'new','parent_task_id':None}
        with self.assertRaises(ServiceError) as caught: self.service.submit(payload)
        self.assertEqual(caught.exception.code, 'EXECUTION_DISABLED')


if __name__ == '__main__': unittest.main()
