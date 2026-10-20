import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hub.systemd_refresh import SERVICE, TIMER, main, render_units


class DailyUnitsTests(unittest.TestCase):
    def test_default_calendar_and_direct_local_exec(self):
        units = render_units(Path('/workspace/hub'), Path('/runtime/version'), Path('/runtime/env/bin/python'))
        self.assertIn('OnCalendar=*-*-* 09:00:00 Asia/Tokyo', units[TIMER])
        self.assertIn('Persistent=true', units[TIMER])
        self.assertIn('Type=oneshot', units[SERVICE])
        self.assertIn('WorkingDirectory=/workspace/hub\n', units[SERVICE])
        self.assertIn('"/runtime/env/bin/python" "/runtime/version/scripts/hub_sync.py" --root "/workspace/hub" sync --scheduled', units[SERVICE])
        self.assertNotIn('personal-control-hub.service', ''.join(units.values()))
        self.assertNotIn('Restart=', units[SERVICE])

    def test_arguments_do_not_become_systemd_expansion_or_shell(self):
        text = render_units(Path('/space $x/%i/hub'), Path('/code path'), Path('/bin/python'))[SERVICE]
        self.assertIn('"/space $$x/%%i/hub"', text)
        self.assertIn('WorkingDirectory=/space $x/%%i/hub\n', text)
        for time, zone in [('25:00', 'Asia/Tokyo'), ('09:00\nExecStart=bad', 'Asia/Tokyo'), ('09:00', '../UTC')]:
            with self.subTest(time=time, zone=zone), self.assertRaises((ValueError, KeyError)):
                render_units(Path('/hub'), Path('/code'), Path('/bin/python'), time=time, timezone=zone)
        with self.assertRaises(ValueError):
            render_units(Path('/hub\nother'), Path('/code'), Path('/bin/python'))

    def test_install_refuses_existing_unit_and_does_not_control_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            args = ['--root', '/hub', '--code-root', '/code', '--python', '/bin/python', '--unit-dir', directory]
            self.assertEqual(main(args), 0)
            previous = {name: (target / name).read_bytes() for name in (SERVICE, TIMER)}
            with self.assertRaises(SystemExit):
                main(args)
            self.assertEqual(previous, {name: (target / name).read_bytes() for name in previous})




class ServiceProtocolTests(unittest.TestCase):
    def test_pinned_renderer_binds_preflight_and_execution_without_exit_allowlist(self):
        program = 'a' * 64
        text = render_units(Path('/data/hub'), Path('/frozen $x/%i/code'), Path('/env/python'),
                            program_id=program)[SERVICE]
        self.assertIn('ExecStartPre=', text)
        self.assertIn('service-precheck-v1 --program-id ' + program, text)
        self.assertIn('service-scheduled-v1 --program-id ' + program, text)
        self.assertEqual(2, text.count('--runtime-manifest "/frozen $$x/%%i/code/rr-runtime-manifest.json"'))
        self.assertNotIn('SuccessExitStatus', text)
        with self.assertRaises(ValueError):
            render_units(Path('/data'), Path('/code'), Path('/python'), program_id=program,
                         runtime_manifest=Path('/other/rr-runtime-manifest.json'))

    def test_actual_frozen_preflight_and_service_batches(self):
        import hashlib, json, os, shutil, sqlite3, subprocess
        from connection_fixtures import Fixture
        import yaml
        fixture = Fixture(); self.addCleanup(fixture.close)
        a, _ = fixture.add('a'); b, _ = fixture.add('b')
        scope = fixture.hub / 'data/connections/sync_scope.yaml'
        scope.parent.mkdir(parents=True)
        scope.write_text(yaml.safe_dump({'schema_version':'1.0','scope_id':'service-fixture',
            'baseline_total':2,'project_ids':['a','b'],'excluded_project_ids':{}}))
        repository = Path(__file__).resolve().parents[1]
        frozen = fixture.base / 'frozen'
        files = {}
        for source in sorted((repository / 'src/hub').rglob('*.py')) + [repository / 'scripts/hub_sync.py']:
            relative = str(source.relative_to(repository)); raw = source.read_bytes()
            target = frozen / relative; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw); target.chmod(0o600)
            files[relative] = {'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        canonical = lambda value: json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
        program = hashlib.sha256(canonical(files)).hexdigest()
        manifest = frozen / 'rr-runtime-manifest.json'
        manifest.write_bytes(canonical({'schema':1,'program_id':program,'files':files,
                                       'source':{'commit':'a'*40,'dirty':False}})); manifest.chmod(0o600)
        def invoke(command, *arguments, data_root=None):
            result = subprocess.run([sys.executable,'-B',str(frozen/'scripts/hub_sync.py'),
                '--root',str(data_root or fixture.hub),command,*arguments],cwd=fixture.base,
                capture_output=True,text=True,timeout=20)
            self.assertEqual('',result.stderr,result.stderr)
            return result.returncode,json.loads(result.stdout)
        binding = ['--program-id',program,'--runtime-manifest',str(manifest)]
        self.assertEqual(0,invoke('service-precheck-v1',*binding)[0])
        (b/'STATE.yaml').write_text('broken: [yaml')
        code, normal = invoke('sync','--scheduled')
        self.assertEqual(2,code); self.assertEqual('FINISHED',normal['status'])
        code, service = invoke('service-scheduled-v1',*binding)
        self.assertEqual(0,code,service)
        self.assertEqual({'invalid':1},service['failure_code_counts'])
        self.assertEqual(2,service['service_execution']['data_exit_code'])
        self.assertEqual('completed_degraded',service['service_execution']['classification'])
        self.assertEqual(2,service['service_execution']['scope_total'])
        self.assertEqual(1,service['service_execution']['quality_counts']['complete'])
        self.assertEqual(1,service['service_execution']['quality_counts']['failed'])
        self.assertEqual({'invalid':1},service['service_execution']['current_failure_codes'])
        self.assertEqual(0,service['appended_count'])
        db = fixture.hub/'data/connections/connection_refresh.sqlite3'
        from contextlib import closing
        with closing(sqlite3.connect(db)) as connection:
            counts = connection.execute('SELECT count(*),sum(success) FROM refresh_results').fetchone()
        self.assertEqual((2,1),counts)
        # A self-consistent but incomplete manifest must not omit its entry code.
        saved_manifest = manifest.read_bytes()
        omitted = dict(files); omitted.pop('src/hub/connection_cli.py')
        omitted_id = hashlib.sha256(canonical(omitted)).hexdigest()
        manifest.write_bytes(canonical({'schema':1,'program_id':omitted_id,'files':omitted,
                                       'source':{'commit':'a'*40,'dirty':False}}))
        self.assertNotEqual(0, invoke('service-scheduled-v1','--program-id',omitted_id)[0])
        manifest.write_bytes(saved_manifest)
        self.assertNotEqual(0, invoke('service-precheck-v1','--program-id','f'*64)[0])
        # Preflight, configuration and integrity faults remain operational failures.
        manifest.chmod(0o644)
        self.assertNotEqual(0, invoke('service-scheduled-v1', *binding)[0])
        manifest.chmod(0o600)
        scope_bytes = scope.read_bytes(); scope.unlink()
        code, error = invoke('service-scheduled-v1', *binding)
        self.assertNotEqual(0, code); self.assertEqual('IO_UNAVAILABLE', error['error'])
        scope.write_bytes(scope_bytes)
        # A separately valid empty eligibility set is an idempotent no-work result.
        empty = Fixture(); self.addCleanup(empty.close)
        empty.add('a'); empty.add('b')
        for project in empty.projects: project['enabled'] = False
        empty.save_registry()
        empty_scope = empty.hub/'data/connections/sync_scope.yaml'; empty_scope.parent.mkdir(parents=True)
        empty_scope.write_bytes(scope_bytes)
        code, no_work = invoke('service-scheduled-v1', *binding, data_root=empty.hub)
        self.assertEqual(0, code, no_work)
        self.assertEqual('no_work', no_work['service_execution']['classification'])
        code, repeated = invoke('service-scheduled-v1', *binding, data_root=empty.hub)
        self.assertEqual(0, code); self.assertEqual(no_work['head'], repeated['head'])
        self.assertEqual(0, repeated['appended_count'])
        # Tampering fails before any source/ledger access, including fresh writes.
        before = db.read_bytes(); entry = frozen/'scripts/hub_sync.py'
        entry.write_text(entry.read_text()+'\n# tampered\n')
        code, error = invoke('service-scheduled-v1',*binding)
        self.assertNotEqual(0,code); self.assertFalse(error['valid'])
        self.assertEqual(before,db.read_bytes())
        # Seal an explicit fixture fault implementation: an exception before B's
        # result leaves a genuinely incomplete existing-ledger request, not a
        # mocked FINISHED dictionary. Service mode must not mask it.
        source_file = frozen/'src/hub/connection_sources.py'
        source_file.write_text(source_file.read_text()+"\n_fixture_refresh = SourceResolver.refresh\ndef _fixture_infrastructure_failure(self, ident):\n    if ident == 'b': raise OSError('synthetic producer failure')\n    return _fixture_refresh(self, ident)\nSourceResolver.refresh = _fixture_infrastructure_failure\n")
        for name in ('src/hub/connection_sources.py','scripts/hub_sync.py'):
            raw = (frozen/name).read_bytes(); files[name] = {'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        fault_program = hashlib.sha256(canonical(files)).hexdigest()
        manifest.write_bytes(canonical({'schema':1,'program_id':fault_program,'files':files,
                                       'source':{'commit':'b'*40,'dirty':True}}))
        incomplete = Fixture(); self.addCleanup(incomplete.close)
        incomplete.add('a'); incomplete.add('b')
        incomplete_scope = incomplete.hub/'data/connections/sync_scope.yaml'; incomplete_scope.parent.mkdir(parents=True)
        incomplete_scope.write_bytes(scope_bytes)
        code, error = invoke('service-scheduled-v1','--program-id',fault_program, data_root=incomplete.hub)
        self.assertNotEqual(0, code); self.assertEqual('REQUEST_INCOMPLETE', error['error'])
        from hub.connection_refresh import RefreshLedger
        from hub.connection_records import validate_result
        actual = RefreshLedger(incomplete.hub,result_validator=validate_result).history()
        self.assertEqual('OPEN',actual['requests'][0]['status'])
        self.assertEqual(['b'],actual['requests'][0]['remaining_project_ids'])
        self.assertEqual(['a'],[row['project_id'] for row in actual['results']])
        # Actual corrupted event bytes fail before a completed/degraded claim.
        with closing(sqlite3.connect(incomplete.hub/'data/connections/connection_refresh.sqlite3')) as connection:
            connection.execute("UPDATE refresh_events SET payload_json='{}' WHERE sequence=1")
            connection.commit()
        code, error = invoke('service-scheduled-v1','--program-id',fault_program, data_root=incomplete.hub)
        self.assertNotEqual(0, code); self.assertEqual('LEDGER_CORRUPT', error['error'])


if __name__ == '__main__':
    unittest.main()
