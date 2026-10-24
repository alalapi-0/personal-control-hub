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
from hub.refresh_schedule import latest_due_slot
from hub.connection_sync import scope_plan, recovery_summary
from hub.connection_records import RecordError
from hub.graph_records import validate_graph
from hub.pch_domain_projection import PROJECT
from test_hub_sync_status import PchDomainFixture

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
        self.assertIn('.snapshot.12h.',full['request_id']);self.assertEqual(full['appended_count'],2)
        repeat,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=instant+timedelta(minutes=1))
        self.assertEqual(repeat['status'],'FINISHED');self.assertEqual(repeat['appended_count'],0)
    def test_two_same_day_snapshots_read_new_versions_and_slot_replay_does_not_read(self):
        morning=datetime(2026,10,16,0,0,tzinfo=timezone.utc)
        first,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=morning)
        self.assertEqual(code,0)
        self.change(self.a,'Evening source update')
        with mock.patch.object(self.resolver,'refresh',side_effect=AssertionError('completed slot reread')):
            replay,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=morning+timedelta(hours=11))
        self.assertEqual(replay['appended_count'],0)
        evening,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=morning+timedelta(hours=12))
        self.assertEqual(code,0);self.assertEqual(evening['appended_count'],2)
        self.assertNotEqual(first['request_id'],evening['request_id'])
        self.assertEqual(len(self.ledger.history()['requests']),2)

    def test_latest_due_before_anchor_missed_slots_and_24_hour_override(self):
        instant=datetime(2026,10,20,23,59,tzinfo=timezone.utc) # Tokyo 08:59
        self.assertEqual(latest_due_slot(instant).isoformat(),'2026-10-20T21:00:00+09:00')
        self.assertEqual(latest_due_slot(instant,24).isoformat(),'2026-10-20T09:00:00+09:00')
        scope=self.fixture.hub/'data/connections/sync_scope.yaml'
        data=yaml.safe_load(scope.read_text());data['schedule']={'interval_hours':24};scope.write_text(yaml.safe_dump(data))
        payload,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=instant)
        self.assertEqual(code,0);self.assertEqual(payload['interval_hours'],24)
        self.assertIn('.snapshot.24h.2026-10-20T09-00',payload['request_id'])
        self.assertEqual(len(self.ledger.history()['requests']),1) # no replay of missed history
        with mock.patch.object(self.resolver,'refresh',side_effect=AssertionError('same daily slot reread')):
            replay,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=instant+timedelta(seconds=30))
        self.assertEqual(replay['appended_count'],0)
        next_slot,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=instant+timedelta(minutes=1))
        self.assertEqual(code,0);self.assertEqual(next_slot['appended_count'],2)
        self.assertNotEqual(payload['request_id'],next_slot['request_id'])

    def test_scheduled_open_resumes_missing_only_failure_is_durable_then_recovers(self):
        morning=datetime(2026,10,16,0,0,tzinfo=timezone.utc)
        self.resolver.clock=lambda:morning.isoformat()
        original=self.resolver.refresh
        def interrupt(ident):
            if ident=='b':raise KeyboardInterrupt()
            return original(ident)
        with mock.patch.object(self.resolver,'refresh',side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=morning)
        with mock.patch.object(self.resolver,'refresh',wraps=original) as read:
            good,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=morning)
        read.assert_called_once_with('b');self.assertEqual(code,0)
        old_bytes=(self.a/'STATE.yaml').read_bytes();(self.a/'STATE.yaml').write_text('broken: [')
        evening=morning+timedelta(hours=12)
        self.resolver.clock=lambda:evening.isoformat()
        failed,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=evening)
        self.assertEqual(code,2);self.assertEqual(failed['status'],'FINISHED')
        summary=recovery_summary(self.ledger.history(current_authority=self.resolver.authority),{'a','b'})
        self.assertEqual(summary['last_success']['request_id'],good['request_id'])
        self.assertEqual(summary['last_failure']['request_id'],failed['request_id'])
        (self.a/'STATE.yaml').write_bytes(old_bytes)
        with mock.patch.object(self.resolver,'refresh',side_effect=AssertionError('durable failure reread')):
            repeated,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=evening)
        self.assertEqual(code,2);self.assertEqual(repeated['appended_count'],0)
        # Time alone cannot retry the same failed version. Supply actual bounded
        # source-change evidence, with the original failed result as its anchor.
        from hub.connection_sync import project_recovery, _source_version
        from hub.connection_records import content_hash
        recovery_time=morning+timedelta(days=1)
        self.resolver.clock=lambda:recovery_time.isoformat()
        previous=project_recovery(self.ledger.history(),'a',recovery_time)
        from hub.connection_sync import record_recovery_observation
        record_recovery_observation(self.ledger,self.resolver,'explicit-recovery-proof','a')
        proof=self.ledger.history()['observation_results'][-1]
        observed=proof['result']
        event={'kind':'source_changed','failure_ref':previous['failure_ref'],
               'source_version':_source_version(observed),
               'binding_hash':self.resolver.authority['project_bindings']['a'],
               'evidence_hash':proof['result_hash'],'proof_ref':proof['result_ref']}
        recovered,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,
            instant=recovery_time,recovery_events={'a':event})
        self.assertEqual(code,0);self.assertEqual(recovered['appended_count'],2)

    def test_manual_and_scheduled_cli_share_mutex_and_do_not_launch_other_runtimes(self):
        with sync_mutex(self.ledger), mock.patch('hub.connection_sources.SourceResolver.refresh',side_effect=AssertionError('busy read')):
            for args in (['--request-id','manual'],['--daily'],['--scheduled'],['--incremental']):
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    code=main(['--root',str(self.fixture.hub),'sync',*args])
                self.assertEqual(code,2);self.assertEqual(json.loads(output.getvalue())['error'],'SYNC_BATCH_BUSY')
        with mock.patch('subprocess.Popen',side_effect=AssertionError('background process')):
            first,code=scheduled_request(self.fixture.hub,self.resolver,self.ledger,instant=datetime(2026,10,16,0,tzinfo=timezone.utc))
        self.assertEqual(code,0)
        self.change(self.a,'Manual update')
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            code=main(['--root',str(self.fixture.hub),'sync','--request-id','manual-recovery'])
        self.assertEqual(code,0);self.assertEqual(json.loads(output.getvalue())['appended_count'],2)

    def test_schedule_schema_is_strict_and_default_is_12(self):
        self.assertEqual(scope_plan(self.fixture.hub,self.resolver)['schedule']['interval_hours'],12)
        scope=self.fixture.hub/'data/connections/sync_scope.yaml';data=yaml.safe_load(scope.read_text())
        for bad in (None,{},True,{'interval_hours':True},{'interval_hours':6},{'interval_hours':12,'extra':1}):
            data['schedule']=bad;scope.write_text(yaml.safe_dump(data))
            with self.subTest(schedule=bad),self.assertRaises(RecordError):scope_plan(self.fixture.hub,self.resolver)

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

class PchDomainIncrementalTests(unittest.TestCase):
    def setUp(self):
        self.f = PchDomainFixture(); self.addCleanup(self.f.close)

    def incremental(self, request):
        f = self.f
        return incremental_request(f.root, f.resolver, f.ledger, request)

    def test_touch_replay_and_content_changes_preserve_identity_not_old_revision(self):
        f = self.f; first, code = self.incremental('domain-first')
        self.assertEqual((code, first['appended_count']), (0, 1))
        before = f.project(); history = f.ledger.history()
        old_file = next(o for o in before['objects'] if o['type'] == 'FileRef')
        os.utime(f.source / 'STATE.yaml', None)
        touched, code = self.incremental('domain-touch')
        self.assertEqual((code, touched['status']), (0, 'UNCHANGED'))
        self.assertEqual(f.ledger.history(), history)
        after_touch = f.project(); self.assertEqual(after_touch['source_version'], before['source_version'])
        self.assertEqual(after_touch['projection_version'], before['projection_version'])
        with mock.patch.object(f.resolver, 'refresh', side_effect=AssertionError('replay collected source')):
            replay, code = self.incremental('domain-first')
        self.assertEqual((code, replay['appended_count']), (0, 0)); self.assertEqual(f.ledger.history(), history)
        state = yaml.safe_load((f.source / 'STATE.yaml').read_text())
        state['current_work']['objective'] = 'Changed synthetic PCH source content'
        (f.source / 'STATE.yaml').write_text(yaml.safe_dump(state))
        changed, code = self.incremental('domain-changed'); self.assertEqual((code, changed['appended_count']), (0, 1))
        projected = f.project(); validate_graph(projected)
        current = next(o for o in projected['objects'] if o['type'] == 'FileRef' and o['source']['applicability'] == 'current')
        self.assertEqual(current['id'], old_file['id']); self.assertNotEqual(current['revision'], old_file['revision'])
        self.assertNotEqual(projected['source_version'], before['source_version'])
        self.assertIn((old_file['id'], old_file['revision']), {(r['object_id'], r['revision']) for r in projected['rows']})
        self.assertEqual(projected['rows'], f.project()['rows'])

    def test_reobservation_has_new_run_but_same_content_and_rule_revision(self):
        f = self.f; f.collect(); before = f.project()
        file = next(o for o in before['objects'] if o['type'] == 'FileRef')
        os.utime(f.source / 'STATE.yaml', None); f.collect('domain-reobserved')
        after = f.project(); files = [o for o in after['objects'] if o['type'] == 'FileRef']
        self.assertEqual(len(files), 1); self.assertEqual(files[0]['id'], file['id'])
        self.assertEqual(files[0]['revision'], file['revision'])
        self.assertEqual(files[0]['data']['version_inputs'], file['data']['version_inputs'])
        self.assertNotEqual(files[0]['data']['observation'], file['data']['observation'])
        self.assertEqual(len([o for o in after['objects'] if o['type'] == 'Run']), 2)
        self.assertEqual(files[0]['data']['observation']['head'], f.ledger.head)

    def test_failed_source_keeps_error_run_and_historical_success_without_current_edges(self):
        f = self.f; f.publish_page(); self.incremental('domain-good')
        good = f.project(); source = (f.source / 'STATE.yaml')
        source.write_text('private_failure_body_not_for_graph: [')
        failed, code = self.incremental('domain-bad'); self.assertEqual((code, failed['appended_count']), (2, 1))
        dto = f.service.get_project(PROJECT); self.assertEqual(dto['last_success_snapshot']['state'], 'historical')
        result = f.project(); runs = [o for o in result['objects'] if o['type'] == 'Run']
        failure = next(o for o in runs if o['data']['request_id'] == 'domain-bad')
        self.assertFalse(failure['data']['success']); self.assertTrue(failure['data']['error_codes'])
        self.assertFalse(dto['freshness']['authority_drift'])
        self.assertEqual(failure['source']['applicability'], 'current')
        self.assertFalse(failure['data']['execution_authority'])
        self.assertIsNone(failure['data']['native_run_id'])
        earlier = next(o for o in runs if o['data']['request_id'] == 'domain-good')
        self.assertEqual(earlier['source']['applicability'], 'historical')
        self.assertEqual(len([o for o in runs if o['data']['success']]), 1)
        for obj in result['objects']:
            if obj['type'] in {'Artifact', 'FileRef'}: self.assertEqual(obj['source']['applicability'], 'historical')
        self.assertFalse(any(e['from']['id'] == failure['id'] for e in result['relations']))
        for edge in result['relations']:
            if edge['source']['origin'] == 'source_fact': self.assertEqual(edge['source']['applicability'], 'historical')
        self.assertNotIn('private_failure_body_not_for_graph', json.dumps(result))
        self.assertNotIn(str(f.fixture.base), json.dumps(result))
        self.assertTrue(any(o['id'] in {x['id'] for x in good['objects']} for o in result['objects'] if o['type'] == 'FileRef'))

    def test_latest_failed_attempt_does_not_become_current_under_authority_drift(self):
        from hub.connection_records import record_schema
        from hub.pch_domain_projection import project_pch_domain
        f = self.f; f.publish_page(); self.incremental('domain-good')
        (f.source / 'STATE.yaml').write_text('broken: [')
        self.incremental('domain-bad')
        history, matched = f.snapshot(); before = project_pch_domain(history, matched)
        schema = record_schema(); schema['shared_safety_revision'] = 'synthetic-domain-authority-drift'
        with mock.patch('hub.connection_sources.record_schema', return_value=schema):
            drifted = f.service.get_project(PROJECT)
        self.assertTrue(drifted['freshness']['authority_drift'])
        result = project_pch_domain(history, drifted); validate_graph(result)
        previous = {(o['id'], o['revision']) for o in before['objects'] if o['type'] in {'Run', 'FileRef', 'Artifact'}}
        observed = {(o['id'], o['revision']) for o in result['objects'] if o['type'] in {'Run', 'FileRef', 'Artifact'}}
        self.assertEqual(observed, previous)
        for obj in result['objects']:
            if obj['type'] in {'Run', 'FileRef', 'Artifact'}:
                self.assertEqual(obj['source']['applicability'], 'historical')
                self.assertFalse(obj['data']['execution_authority'])
        for edge in result['relations']:
            if edge['source']['origin'] == 'source_fact': self.assertEqual(edge['source']['applicability'], 'historical')
        self.assertEqual(f.ledger.history(), history)


if __name__=='__main__':unittest.main()
