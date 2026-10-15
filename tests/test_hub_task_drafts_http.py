from __future__ import annotations
import copy
import sys
import threading
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.local_service import HubHTTPServer
from hub.owner_auth import OwnerAuth
from hub.preview_service import PreviewService
from hub.task_service import TaskService
from hub.task_store import TaskStore
from hub.workbench_store import WorkbenchStore
import test_hub_task_drafts as drafts_fixture
import test_hub_local_service as http_fixture

class TaskDraftHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture=drafts_fixture.TaskDraftTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.start()
    def start(self):
        source=self.fixture.helper
        self.http=http_fixture.LocalHTTPTests();self.http.projects=source.projects;self.http.designs=source.fixture.service
        self.http.owner_proof='fixture-only-public-test-vector-00000000000000000000'
        previews=PreviewService(source.projects,source.fixture.service,WorkbenchStore(source.root,fixture=True))
        tasks=TaskService(source.projects,previews,TaskStore(source.root))
        self.http.server=HubHTTPServer(source.projects,source.fixture.service,previews=previews,tasks=tasks,
            owner_auth=OwnerAuth(provider=lambda:self.http.owner_proof))
        self.http.thread=threading.Thread(target=self.http.server.serve_forever,kwargs={'poll_interval':.01})
        self.http.thread.start();self.http.cookie=self.http.csrf=None;self.addCleanup(self.http.stop)
    def test_owner_http_restart_exact_receipt_and_zero_queue_effect(self):
        command=self.fixture.command()
        self.assertEqual(self.http.call('POST','/api/task-drafts',command)[0],401)
        self.assertEqual(self.http.call(path='/api/task-drafts')[0],401)
        self.http.session()
        for headers in [{'Origin':'https://evil.example'},{'X-Hub-CSRF':'wrong'}]:
            self.assertEqual(self.http.call('POST','/api/task-drafts',command,headers=headers)[0],403)
        status,_,body=self.http.call('POST','/api/task-drafts',command)
        self.assertEqual(status,200);saved=body['data'];self.assertEqual(saved['receipt']['command'],command)
        self.assertFalse(saved['draft']['execution_allowed'])
        self.assertEqual(self.http.call('POST','/api/task-drafts',command)[2]['data']['replayed'],True)
        self.assertFalse(self.fixture.store.path.exists())
        self.http.stop();self.start();self.http.session()
        self.assertEqual(self.http.call(path='/api/task-drafts/'+command['request_id'])[2]['data']['draft'],saved['draft'])
        self.assertEqual(len(self.http.call(path='/api/task-drafts')[2]['data']['drafts']),1)
        self.assertEqual(self.http.call(path='/api/task-drafts?cwd=/tmp')[0],400)
        self.assertEqual(self.http.call('POST','/api/tasks',{})[0],400)
        self.assertFalse(self.fixture.store.path.exists())
    def test_wrong_project_stale_version_and_extra_commands_rejected(self):
        self.http.session();command=self.fixture.command()
        for field,value in [('project_id','wrong-project'),('artifact_sha256','0'*64),('registry_hash','0'*64)]:
            bad=copy.deepcopy(command);bad['binding'][field]=value
            self.assertGreaterEqual(self.http.call('POST','/api/task-drafts',bad)[0],400)
        for field in ['cwd','shell','grant_id']:
            bad={**command,field:'/tmp'}
            self.assertEqual(self.http.call('POST','/api/task-drafts',bad)[0],400)
        self.assertFalse(self.fixture.helper.store.path.exists());self.assertFalse(self.fixture.store.path.exists())
        self.http.server.owner_auth=OwnerAuth(provider=lambda:'rotated-fixture-token-000000000000000000000000000000000')
        self.assertEqual(self.http.call('POST','/api/task-drafts',command)[0],401)

if __name__=='__main__':unittest.main()
