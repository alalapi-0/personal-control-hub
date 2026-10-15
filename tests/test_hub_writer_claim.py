import copy
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import test_hub_task_runtime as fixture


class WriterClaimTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TaskRuntimeTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.service.submit(self.f.payload)

    def queued(self,root,project='other-project'):
        source=self.f.store.task('fixture-task');payload=copy.deepcopy(source['payload']);intent=copy.deepcopy(source['intent'])
        payload.update(request_id='other-task',project_id=project)
        intent['grant'].update(root=str(root),project_id=project,task_id='other-task')
        self.f.store.admit(payload,intent)

    def test_active_cross_repository_effect_is_serialized(self):
        other=self.f.control/'other';other.mkdir();self.queued(other)
        self.assertEqual(self.f.store.claim('first-worker')['id'],'fixture-task')
        self.assertIsNone(self.f.store.claim('second-worker'))

    def test_reconcile_fences_same_root_under_different_project_id(self):
        self.queued(self.f.workspace)
        self.f.store.claim('first-worker',only_task_id='fixture-task')
        self.f.store.update('fixture-task','first-worker',status='requires_reconcile')
        self.assertIsNone(self.f.store.claim('second-worker',only_task_id='other-task'))

    def test_reconcile_in_separate_workspace_does_not_block_independent_work(self):
        other=self.f.control/'other';other.mkdir();self.queued(other)
        self.f.store.claim('first-worker',only_task_id='fixture-task')
        self.f.store.update('fixture-task','first-worker',status='requires_reconcile')
        self.assertEqual(self.f.store.claim('second-worker',only_task_id='other-task')['id'],'other-task')

    def test_same_repository_nested_roots_are_fenced(self):
        child=self.f.workspace/'nested';child.mkdir();self.queued(child)
        self.f.store.claim('first-worker',only_task_id='fixture-task')
        self.f.store.update('fixture-task','first-worker',status='requires_reconcile')
        self.assertIsNone(self.f.store.claim('second-worker',only_task_id='other-task'))

    def test_changed_root_alias_is_not_mistaken_for_absent_history(self):
        other=self.f.control/'other';other.mkdir();self.queued(other)
        self.f.store.claim('first-worker',only_task_id='fixture-task')
        self.f.store.update('fixture-task','first-worker',status='requires_reconcile')
        self.f.workspace.rename(self.f.control/'retired')
        self.f.workspace.symlink_to(other,target_is_directory=True)
        self.assertIsNone(self.f.store.claim('second-worker',only_task_id='other-task'))

    def test_shared_git_worktrees_are_fenced_until_reconciled(self):
        root=self.f.workspace
        subprocess.run(['git','init','-q',str(root)],check=True,capture_output=True)
        subprocess.run(['git','-C',str(root),'add','index.html'],check=True,capture_output=True)
        subprocess.run(['git','-C',str(root),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],check=True,capture_output=True)
        other=self.f.control/'worktree'
        subprocess.run(['git','-C',str(root),'worktree','add','--detach',str(other)],check=True,capture_output=True)
        self.queued(other);self.f.store.claim('first-worker',only_task_id='fixture-task')
        self.f.store.update('fixture-task','first-worker',status='requires_reconcile')
        self.assertIsNone(self.f.store.claim('second-worker',only_task_id='other-task'))
