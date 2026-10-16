"""Real synthetic source/ledger checks for bounded incremental collection."""
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
import unittest
from unittest import mock
import yaml
from connection_fixtures import Fixture
from hub.connection_cli import main
from hub.connection_records import validate_result
from hub.connection_refresh import RefreshLedger, HeadConflictError
from hub.connection_sync import incremental_request, scheduled_request, sync_mutex
from hub.service_contract import ServiceError

class IncrementalSyncTests(unittest.TestCase):
    def setUp(self):
        self.fixture=Fixture();self.addCleanup(self.fixture.close)
        self.a,_=self.fixture.add('a');self.b,_=self.fixture.add('b')
        (self.fixture.hub/'data/connections').mkdir()
        (self.fixture.hub/'data/connections/sync_scope.yaml').write_text(yaml.safe_dump({
            'schema_version':'1.0','scope_id':'test-scope','baseline_total':2,
            'project_ids':['a','b'],'excluded_project_ids':{}}))
        self.resolver=self.fixture.resolver()
        self.ledger=RefreshLedger(self.fixture.hub,result_validator=validate_result)
    def run_incremental(self,ident):
        return incremental_request(self.fixture.hub,self.resolver,self.ledger,ident)
    def change(self,root,value):
        path=root/'STATE.yaml';data=yaml.safe_load(path.read_text());data['current_work']['objective']=value
        path.write_text(yaml.safe_dump(data))
    def test_no_change_touch_and_one_changed_source(self):
        first,code=self.run_incremental('first');self.assertEqual(code,0);self.assertEqual(first['appended_count'],2)
        before=self.ledger.head
        os.utime(self.a/'STATE.yaml',None)
        result,code=self.run_incremental('unchanged')
        self.assertEqual(result['status'],'UNCHANGED');self.assertEqual(before,self.ledger.head)
        self.change(self.a,'New source fact')
        result,code=self.run_incremental('changed');self.assertEqual(code,0)
        self.assertEqual(result['appended_count'],1);self.assertEqual(result['unchanged_count'],1)
        self.assertEqual(self.ledger.history('changed')['requests'][0]['project_ids'],['a'])
        repeat,code=self.run_incremental('changed');self.assertEqual(repeat['appended_count'],0)
    def test_bad_source_isolated_failed_state_recovered_only_after_change(self):
        self.run_incremental('first')
        (self.a/'STATE.yaml').write_text('broken: [')
        self.change(self.b,'B remains usable')
        result,code=self.run_incremental('bad');self.assertEqual(code,2)
        self.assertEqual(result['appended_count'],2)
        projection=self.ledger.rebuild(current_authority=self.resolver.authority)
        self.assertEqual(projection['projects']['a']['freshness'],'stale')
        self.assertEqual(projection['projects']['b']['freshness'],'fresh')
        before=self.ledger.head
        result,code=self.run_incremental('same-bad');self.assertEqual(code,2)
        self.assertEqual(result['status'],'UNCHANGED');self.assertEqual(before,self.ledger.head)
        self.change(self.b,'B update despite bad A')
        result,code=self.run_incremental('changed-b');self.assertEqual(result['appended_count'],1)
    def test_cas_competing_commit_is_explicit(self):
        original=self.resolver.refresh;mutated=[False]
        def resolve(ident):
            result=original(ident)
            if not mutated[0]:
                mutated[0]=True
                self.ledger.begin_request('competing',['b'],self.resolver.authority)
            return result
        with mock.patch.object(self.resolver,'refresh',side_effect=resolve):
            with self.assertRaises(HeadConflictError):self.run_incremental('loser')
        self.assertEqual(len(self.ledger.history()['results']),0)
    def test_expired_evidence_refreshes_without_business_change(self):
        self.run_incremental('first')
        original=self.resolver.clock()
        self.resolver.clock=lambda:(datetime.fromisoformat(original)+timedelta(hours=2)).isoformat()
        result,code=self.run_incremental('expired');self.assertEqual(result['appended_count'],2)
    def test_batch_mutex_and_daily_full_then_incremental(self):
        with sync_mutex(self.ledger):
            with self.assertRaises(ServiceError) as caught:
                with sync_mutex(self.ledger):pass
            self.assertEqual(caught.exception.code,'SYNC_BATCH_BUSY')
        instant=datetime(2026,10,16,0,0,tzinfo=timezone.utc)
        full,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=instant)
        self.assertIn('.daily.',full['request_id']);self.assertEqual(full['appended_count'],2)
        repeat,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=instant+timedelta(minutes=1))
        self.assertEqual(repeat['status'],'UNCHANGED')
    def test_cli_one_shot_and_subset_rejected(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            code=main(['--root',str(self.fixture.hub),'sync','--incremental'])
        self.assertEqual(code,0);self.assertEqual(json.loads(output.getvalue())['appended_count'],2)
        with contextlib.redirect_stdout(io.StringIO()):
            code=main(['--root',str(self.fixture.hub),'sync','--scheduled','--project-id','a'])
        self.assertNotEqual(code,0)

    def test_excluded_cloud_removed_and_unregistered_are_never_scanned(self):
        self.fixture.projects[0]['project_type']='cloud'
        self.fixture.projects[1]['current_state_status']='removed_local'
        self.fixture.save_registry();self.resolver=self.fixture.resolver()
        with mock.patch.object(self.resolver,'refresh',side_effect=AssertionError('excluded root scan')):
            result,code=self.run_incremental('excluded')
        self.assertEqual(result['checked_count'],0)
        self.assertEqual(result['status'],'NO_ELIGIBLE_PROJECTS')

if __name__=='__main__':unittest.main()
