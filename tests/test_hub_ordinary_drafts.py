"""Owner/project/parent drafts share the guarded task DB without model effects."""
import contextlib
import hashlib
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.task_store import TaskStore
from hub.service_contract import ServiceError
from hub.workbench_store import canonical,digest

class OrdinaryDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'hub';self.root.mkdir()
        self.store=TaskStore(self.root);self.store.register_storage()
        self.owner='owner-a'
    def command(self,ident='draft-one',project='a',parent=None,revision=0,value=None):
        return {'request_id':ident,'expected_revision':revision,'project_id':project,'parent_task_id':parent,
                'value':{'requirement':'Synthetic read-only summary','scope':'Fixture only'} if value is None else value}
    def save(self,command=None,owner=None,authorize=lambda:None):
        return self.store.save_ordinary_draft(command or self.command(),owner or self.owner,authorize)
    def tables(self):
        with contextlib.closing(sqlite3.connect(self.store.path)) as db:
            return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    def assert_code(self,code,action):
        with self.assertRaises(ServiceError) as caught:action()
        self.assertEqual(caught.exception.code,code)
        return caught.exception

    def test_get_and_constructor_never_create_feature_or_unregistered_database(self):
        before=self.store.path.read_bytes();tables=self.tables()
        self.assertEqual(self.store.ordinary_draft(self.owner,'a'),{'project_id':'a','parent_task_id':None,'revision':0,'value':None})
        self.assertEqual(before,self.store.path.read_bytes());self.assertEqual(tables,self.tables())
        other=self.root.parent/'absent';other.mkdir();empty=TaskStore(other)
        with self.assertRaises(ServiceError):empty.ordinary_draft(self.owner,'a')
        self.assertEqual(list(other.iterdir()),[])

    def test_save_restart_cas_replay_and_explicit_clear_no_task_or_grant(self):
        command=self.command();first=self.save(command)
        self.assertEqual(first['draft']['revision'],1);self.assertFalse(first['replayed'])
        self.assertEqual(self.store.tasks(),[]);self.assertEqual(self.store.grants(),[])
        self.assertEqual(TaskStore(self.root).ordinary_draft(self.owner,'a'),first['draft'])
        self.assert_code('ORDINARY_DRAFT_CONFLICT',lambda:self.save(self.command('stale')))
        changed=self.command();changed['value']['requirement']='Changed'
        self.assert_code('ORDINARY_DRAFT_REQUEST_CONFLICT',lambda:self.save(changed))
        clear=self.command('clear',revision=1);clear['value']=None
        self.assertIsNone(self.save(clear)['draft']['value'])
        before=self.store.path.read_bytes()
        replay=self.save(command);self.assertTrue(replay['replayed']);self.assertEqual(replay['draft'],first['draft'])
        self.assertEqual(before,self.store.path.read_bytes())
        self.assertEqual(self.store.ordinary_draft(self.owner,'a')['revision'],2)
        with contextlib.closing(sqlite3.connect(self.store.path)) as db:
            self.assertEqual(db.execute('SELECT version FROM meta').fetchall(),[(2,)])

    def test_owner_project_parent_keys_are_isolated(self):
        for owner in ('owner-a','owner-b'):
            for project in ('a','b'):
                for index,parent in enumerate((None,'parent-one','parent-two')):
                    ident=project+'-'+str(index)
                    value={'tag':owner+'/'+project+'/'+str(parent)}
                    self.save(self.command(ident,project,parent,value=value),owner=owner)
                    self.assertEqual(self.store.ordinary_draft(owner,project,parent)['value'],value)
        self.assertEqual(self.store.ordinary_draft('owner-c','a')['revision'],0)
        self.assert_code('ORDINARY_DRAFT_REQUEST_CONFLICT',lambda:self.save(self.command('a-0',project='b')))

    def test_authorization_denial_before_and_at_commit_rolls_back_optional_ddl(self):
        before=self.store.path.read_bytes();tables=self.tables()
        def denied():raise ServiceError('OWNER_AUTH_FAILED',status=401)
        self.assert_code('OWNER_AUTH_FAILED',lambda:self.save(authorize=denied))
        count=[0]
        def revoked():
            count[0]+=1
            if count[0]==2:denied()
        self.assert_code('OWNER_AUTH_FAILED',lambda:self.save(authorize=revoked))
        self.assertEqual(before,self.store.path.read_bytes());self.assertEqual(tables,self.tables())

    def test_unknown_commit_replays_same_request_after_restart(self):
        def fault(stage):
            if stage=='after_commit':raise OSError('synthetic receipt loss')
        self.store.fault=fault
        error=self.assert_code('TASK_COMMIT_UNCONFIRMED',self.save)
        self.assertEqual(error.outcome,'UNKNOWN')
        self.store=TaskStore(self.root)
        before=self.store.path.read_bytes();result=self.save()
        self.assertTrue(result['replayed']);self.assertEqual(result['draft']['revision'],1)
        self.assertEqual(before,self.store.path.read_bytes())

    def test_input_and_feature_capacity_are_bounded(self):
        large=self.command(value={'text':'x'*8193})
        self.assert_code('ORDINARY_DRAFT_CAPACITY',lambda:self.save(large))
        self.assertNotIn('ordinary_drafts',self.tables())
        with mock.patch('hub.task_store.MAX_ORDINARY_DRAFTS',1):
            self.save()
            self.assert_code('ORDINARY_DRAFT_CAPACITY',lambda:self.save(self.command('other',project='b')))
        with mock.patch('hub.task_store.MAX_ORDINARY_DRAFT_REQUESTS',1):
            self.assert_code('ORDINARY_DRAFT_CAPACITY',lambda:self.save(self.command('next',revision=1)))
        invalid=self.command();invalid['value']=[]
        self.assert_code('INVALID_ORDINARY_DRAFT',lambda:self.save(invalid))
        invalid=self.command();invalid['extra']='untrusted'
        self.assert_code('INVALID_ORDINARY_DRAFT',lambda:self.save(invalid))

    def copied(self):
        root=self.root.parent/'copy';root.mkdir();store=TaskStore(root);store.path.parent.mkdir(parents=True)
        shutil.copy2(self.store.path,store.path);shutil.copy2(self.store.registration_path,store.registration_path)
        return store
    def restore(self,store):
        return store.restore_storage(database_sha256=hashlib.sha256(store.path.read_bytes()).hexdigest(),
            registration_sha256=hashlib.sha256(store.registration_path.read_bytes()).hexdigest(),confirm_offline=True)

    def test_optional_feature_offline_restore_preserves_values_receipts_and_legacy_tables(self):
        saved=self.save();self.store.register_grant({'grant_id':'legacy','version':1})
        copied=self.copied();self.restore(copied)
        self.assertEqual(copied.ordinary_draft(self.owner,'a'),saved['draft'])
        self.assertTrue(copied.save_ordinary_draft(self.command(),self.owner,lambda:None)['replayed'])
        self.assertEqual(copied.grant('legacy'),{'grant_id':'legacy','version':1})

    def test_corrupt_feature_hash_or_unknown_ddl_is_rejected_without_restore(self):
        self.save();copied=self.copied()
        with contextlib.closing(sqlite3.connect(copied.path)) as db:
            db.execute("UPDATE ordinary_drafts SET hash=?",('0'*64,));db.commit()
        before=copied.path.read_bytes()
        with self.assertRaises(ServiceError):self.restore(copied)
        self.assertEqual(before,copied.path.read_bytes())
        with contextlib.closing(sqlite3.connect(copied.path)) as db:
            db.execute('CREATE TABLE unrecognized(value TEXT)');db.commit()
        with self.assertRaises(ServiceError):self.restore(copied)

    def test_restore_checks_request_identity_capacity_and_partial_feature_schema(self):
        self.save();copied=self.copied()
        with contextlib.closing(sqlite3.connect(copied.path)) as db:
            db.execute("UPDATE ordinary_draft_requests SET command_hash=?",('0'*64,));db.commit()
        before=copied.path.read_bytes()
        with self.assertRaises(ServiceError):self.restore(copied)
        self.assertEqual(before,copied.path.read_bytes())
        with contextlib.closing(sqlite3.connect(copied.path)) as db:
            db.execute('UPDATE ordinary_draft_requests SET command_hash=?',(digest(self.command()),));db.commit()
        with mock.patch('hub.task_store.MAX_ORDINARY_DRAFTS',0):
            with self.assertRaises(ServiceError):self.restore(copied)
        with contextlib.closing(sqlite3.connect(copied.path)) as db:
            db.execute('DROP TABLE ordinary_draft_requests');db.commit()
        before=copied.path.read_bytes()
        with self.assertRaises(ServiceError):self.restore(copied)
        self.assertEqual(before,copied.path.read_bytes())

    def test_ordinary_result_cap_uses_stored_kind_and_legacy_remains_bounded(self):
        payload={'request_id':'ordinary-task','project_id':'a','parent_task_id':None,'mode':'new','kind':'ordinary_project'}
        self.store.admit(payload,{'grant':{'root':str(self.root),'kind':'ordinary_project'}})
        self.store.claim('worker-a')
        result={'provider_reply':'x'*65536}
        self.store.update('ordinary-task','worker-a',result=result)
        self.assertEqual(self.store.task('ordinary-task')['result'],result)
        self.assert_code('TASK_RESULT_CAPACITY',lambda:self.store.update('ordinary-task','worker-a',result={'text':'x'*131073}))
        self.store.update('ordinary-task','worker-a',status='failed')
        payload.update(request_id='legacy-task',project_id='b',kind='fixture')
        self.store.admit(payload,{'grant':{'root':str(self.root),'kind':'fixture'}});self.store.claim('worker-b')
        self.assert_code('TASK_RESULT_CAPACITY',lambda:self.store.update('legacy-task','worker-b',result={'kind':'ordinary_project','text':'x'*16385}))
        self.store.update('legacy-task','worker-b',result={'text':'legacy result'})

if __name__=='__main__':unittest.main()
