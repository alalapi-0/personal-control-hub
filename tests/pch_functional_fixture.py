"""Complete isolated Hub; synthetic owner/provider/host, real HTTP and static assets.

Run ``python tests/pch_functional_fixture.py --serve``. JSON stdin commands:
health, handshake (runs real owner handshake in memory), run (optional project_id),
source (A/B only; no collection), sync (optional only_project_id), timer-start,
timer-status, timer-stop, stop. Timer operations are explicit fixture-only effects. Only nonsensitive summaries go to stdout; no proof/cookie is exported.
For browser owner authentication supply --owner-fd N and write a temporary
synthetic proof through that inherited pipe; the fixture never prints it.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import secrets
import select
import struct
import subprocess
from datetime import datetime, timezone
import sys
import threading
import time
from types import SimpleNamespace
import zlib
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from connection_fixtures import Fixture
from hub.codex_adapter import DISCOVERY_SCHEMA_PINS
from hub.connection_sources import SourceResolver
from hub.connection_refresh import RefreshLedger, refresh
from hub.connection_sync import sync_request
from hub.systemd_refresh import render_units
from hub.connection_records import validate_result
from hub.metric_management import REVIEW_AXES, DIMENSIONS, validate_management_config
from hub.design_cli import _artifact, _baseline, _candidate
from hub.design_records import with_content_hash
from hub.design_service import DesignService
from hub.design_store import DesignStore
from hub.host_observer import HostObserver
from hub.material_service import MaterialService
from hub.owner_auth import OwnerAuth
from hub.preview_service import PreviewService
from hub.project_service import ProjectService
from hub.service_contract import ServiceError
from hub.session_service import SessionService
from hub.task_service import TaskService, fingerprint, root_identity, FIXED_CHECKS
from hub.task_store import TaskStore
from hub.task_worker import TaskWorker
from hub.workbench_store import WorkbenchStore, digest

BEFORE = '<!doctype html><title>Fixture</title><h1>before</h1>\n'
AFTER = '<!doctype html><title>Fixture</title><h1>after</h1>\n'


def png(color):
    def chunk(kind, raw):
        return struct.pack('!I', len(raw)) + kind + raw + struct.pack('!I', zlib.crc32(kind + raw))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 32, 32, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + bytes(color) * 32) * 32)) + chunk(b'IEND', b''))


class ControlledAdapter:
    """Deterministic mock. Faults never establish real provider acceptance."""
    transport = 'mock'

    def __init__(self, fixture, root):
        self.fixture, self.root = fixture, Path(root)
        if self.root not in fixture.roots.values():
            raise AssertionError('foreign adapter root')
        self.scenario = fixture.scenarios.get(self.root.name, 'normal')
        self.denials, self.events = [], []
        self.ident = self.root.name
        self.fixture.probes.append(('worker-root', self.root))
        self.release = fixture.release

    def open(self): return self
    def thread(self, previous=None): return previous or 'thread-' + self.ident
    def start(self, thread, text, image):
        self.fixture.effects.append(self.root)
        (self.root / 'index.html').write_text(AFTER)
        turn = 'turn-' + self.ident
        def event(method, **extra):
            return {'method': method, 'params': {'threadId': thread, 'turnId': turn, **extra}}
        self.events = [event('turn/started', turn={'id': turn, 'status': 'inProgress'}),
                       event('turn/started', turn={'id': turn, 'status': 'inProgress'}),
                       event('turn/completed', turn={'id': turn, 'status': 'completed'})]
        if self.scenario == 'out_of_order': self.events.reverse()
        return turn
    def poll(self, timeout):
        if self.scenario == 'delay':
            if not self.release.wait(min(timeout, .05)): return None
        if self.scenario in {'disconnect', 'lost_receipt'}:
            raise ServiceError('FIXTURE_CONNECTION_LOST', outcome='UNKNOWN')
        return self.events.pop(0) if self.events else None
    def check(self, argv):
        assert tuple(argv) == FIXED_CHECKS['fixture_sample']
        assert (self.root / 'index.html').read_text() == AFTER
        return {'exitCode': 1 if self.scenario == 'failure' else 0, 'stdout': 'synthetic check', 'stderr': ''}
    def interrupt(self, thread, turn):
        self.fixture.interrupts.append((thread, turn))
        self.release.set()
    def close(self): pass


class FunctionalFixture:
    classification = 'synthetic_fixture'

    def __init__(self, *, owner=None, directory_size=2):
        if type(directory_size) is not int or not 2 <= directory_size <= 103:
            raise ValueError('directory_size must be between 2 and 103')
        self.directory_size = directory_size
        self.source = Fixture()
        self.root = self.source.hub
        self.owner = owner or secrets.token_urlsafe(40)
        self.probes, self.effects, self.interrupts = [], [], []
        self.scenarios, self.workers = {}, []
        self.runtime = {key: {'available': True, 'classification': 'synthetic_fixture'}
                        for key in ('worker_runtime', 'provider')}
        self.release = threading.Event()
        self.roots = {}
        self.cookie = self.csrf = None
        self.server = self.thread = None
        self.closed = False
        self.timer_units = {}
        self.timer_created = {}
        self.timer_prefix = None
        self.timer_since = None
        self.timer_loaded = False
        self.command_run = subprocess.run
        self.batch_child = None
        try:
            self._assemble()
        except BaseException:
            self.close()
            raise

    def _assemble(self):
        self.commands, self.payloads, self.grants = {}, {}, {}
        for suffix in ('a', 'b'):
            project = 'fixture-project-' + suffix
            self.source.business['current_work']['objective'] = 'Synthetic objective ' + suffix
            root, _ = self.source.add(project)
            self.roots[project] = root
            (root / 'index.html').write_text(BEFORE)
            (root / 'AGENTS.md').write_text('Synthetic disposable workspace\n')
            (root / 'fixture.png').write_bytes(png((40, 120, 200) if suffix == 'a' else (200, 100, 40)))
        # A and B retain full task bindings. Extra directory identities have only
        # synthetic registry/state sources, so paging never grants task authority.
        self.directory_roots = dict(self.roots)
        for index in range(2, self.directory_size):
            project = f'fixture-directory-{index:03d}'
            self.directory_roots[project] = self.source.add(project)[0]
        self.source.projects[0].update(name='Synthetic Project A', description='Synthetic reusable asset production')
        self.source.projects[1].update(name='Synthetic Project B', description=None)
        self.source.save_registry()
        for suffix in ('a', 'b'):
            root = self.roots['fixture-project-' + suffix]
            state = yaml.safe_load((root / 'STATE.yaml').read_text())
            state['progress'].update(completed=0, total=7 if suffix == 'a' else None)
            if suffix == 'a':
                known = lambda value: {'value': value, 'state': 'known', 'reason': None}
                state['definition'] = {'purpose': known('Synthetic reusable asset production'),
                                       'outputs': known(['Editable synthetic asset']),
                                       'aliases': known([{'name': '合成甲', 'confirmed': True}]),
                                       'roles': known(['Producer', 'Library'])}
                declaration = yaml.safe_load((root / 'hub.connection.yaml').read_text())
                declaration['extensions'] = {'schema_version': '1.4',
                    'fact_mapping': {'project_definition': {'source_ref': 'state', 'selector': {'path': ['definition']}}},
                    'unknown_fields': {'project_definition': 'No synthetic definition published'}}
                (root / 'hub.connection.yaml').write_text(yaml.safe_dump(declaration))
            (root / 'STATE.yaml').write_text(yaml.safe_dump(state))
        fixture = self
        class IsolatedResolver(SourceResolver):
            def refresh(self, project_id):
                if project_id not in fixture.directory_roots:
                    raise AssertionError('foreign source refresh')
                assert Path(self.projects[project_id]['root_path']) == fixture.directory_roots[project_id]
                fixture.probes.append(('source-refresh', fixture.directory_roots[project_id]))
                return super().refresh(project_id)
        class IsolatedProjects(ProjectService):
            def _resolver(self):
                fixture.probes.append(('registry', fixture.root / 'data/registry/external_projects.yaml'))
                return IsolatedResolver(fixture.root)
        self.projects = IsolatedProjects(self.root)
        connections = self.root / 'data/connections'
        connections.mkdir(parents=True, exist_ok=True)
        scope = {'schema_version': '1.0', 'scope_id': 'synthetic-functional-scope',
                 'baseline_total': self.directory_size, 'project_ids': list(self.directory_roots), 'excluded_project_ids': {}}
        (connections / 'sync_scope.yaml').write_text(yaml.safe_dump(scope, sort_keys=False))
        management = {'schema_version': '1.3', 'kind': 'hub_management_config', 'write_back_allowed': False,
                      'freshness': {'max_age_seconds': 86400, 'labels': {'fresh': 'Fresh', 'stale': 'Stale', 'unknown': 'Unknown'}},
                      'axes': {key: {'display_name': key, 'unit': 'count'} for key in REVIEW_AXES},
                      'dimensions': {key: key for key in DIMENSIONS},
                      'watch': [{'id': 'synthetic-freshness', 'display_name': 'Synthetic freshness', 'metric_id': 'coverage.freshness',
                                 'threshold': None, 'threshold_reason': 'Synthetic fixture', 'recovery': 'Refresh fixture sources'}],
                      'projects': {}, 'revision': 0, 'history': []}
        validate_management_config(management)
        (connections / 'management_config.yaml').write_text(yaml.safe_dump(management, sort_keys=False))
        self.refresh_ledger = RefreshLedger(self.root, result_validator=validate_result)
        refresh(self.refresh_ledger, self.projects._resolver(), 'fixture-initial-source', list(self.directory_roots))
        material = self.root / 'docs/reports/ui_design_governance/functional-fixture'
        material.mkdir(parents=True)
        self.design_store = DesignStore(self.root, material / 'fixture-store.json', fixture=True,
                                       fixture_project_ids=set(self.roots))
        self.design_store.initialize()
        for project, root in self.roots.items():
            path = material / (project + '.png')
            path.write_bytes((root / 'fixture.png').read_bytes())
            artifact = _artifact(project + '-image', str(path.relative_to(self.root)), hashlib.sha256(path.read_bytes()).hexdigest())
            artifact['scope']['members'][0]['project_id'] = project
            baseline = _baseline(1, artifact)
            baseline.update(id=project + '-baseline', project_id=project)
            baseline = with_content_hash(baseline)
            candidate = _candidate(1, baseline, artifact)
            candidate['id'] = project + '-candidate'
            candidate['scope']['members'][0]['project_id'] = project
            candidate['baseline_bindings'][0]['project_id'] = project
            candidate = with_content_hash(candidate)
            for fact in (artifact, baseline, candidate):
                self.design_store.append_fact(fact, expected_revision=self.design_store.read()['revision'], request_id=fact['id'])
        self.designs = DesignService(self.design_store)
        self.workbench = WorkbenchStore(self.root, fixture=True)
        self.previews = PreviewService(self.projects, self.designs, self.workbench)
        self.task_store = TaskStore(self.root)
        self.task_store.register_storage()
        self.tasks = TaskService(self.projects, self.previews, self.task_store, test_gate=True,
                                 writer_check=self.writer_check, runtime_status=lambda: self.runtime)
        for item in self.previews.catalog()['previews']:
            binding = item['binding']; project = binding['project_id']; root = self.roots[project]
            command = {'request_id': project + '-annotation', 'expected_revision': self.workbench.read()['revision'],
                       'binding': binding, 'kind': 'whole', 'region': None,
                       'requested_change': 'Synthetic isolated spacing change', 'preserve_scope': 'Preserve fixture scope',
                       'view': {'viewport_width': 390, 'viewport_height': 844, 'dpr': 1, 'scroll_x': 0, 'scroll_y': 0,
                                'zoom': 1, 'image_width': 32, 'image_height': 32, 'natural_width': 32, 'natural_height': 32,
                                'image_left': 0, 'image_top': 0, 'fit': 'image-content-box'}}
            saved = self.previews.save(command, backend_instance='b' * 32)
            grant = {'grant_id': project + '-grant', 'version': 1, 'source': 'verified_owner_decision',
                     'task_id': project + '-task', 'project_id': project, 'root': str(root), 'root_identity': root_identity(root),
                     'fingerprint': fingerprint(root), 'annotation_hash': digest(command), 'checks': ['fixture_sample'],
                     'expires_at': time.time() + 3600, 'isolated_root': True, 'writable_scope': ['.'], 'prohibited': [],
                     'auth': 'ChatGPT', 'max_turn_seconds': 20, 'image': 'fixture.png',
                     'image_sha256': hashlib.sha256((root / 'fixture.png').read_bytes()).hexdigest(),
                     'mode': 'new', 'parent_task_id': None}
            self.task_store.register_grant(grant)
            self.commands[project], self.grants[project] = command, grant
            self.payloads[project] = {'request_id': grant['task_id'], 'project_id': project,
                                      'annotation_id': command['request_id'], 'annotation_revision': saved['receipt']['revision'],
                                      'grant_id': grant['grant_id'], 'grant_version': 1, 'mode': 'new', 'parent_task_id': None}
        fixture = self
        class Discovery:
            transport = 'mock'
            def __init__(self, root):
                fixture.probes.append(('session-launch', Path(root)))
                self.next_id = 0
                self.schema_hashes = copy.deepcopy(DISCOVERY_SCHEMA_PINS)
                self.schemas = {'ThreadListResponse': {'definitions': {'Thread': {'properties': dict.fromkeys(('id', 'source', 'cwd', 'modelProvider', 'status', 'createdAt', 'updatedAt', 'turns'))}}}}
            def open(self):
                self.next_id += 1
                return self
            def call(self, method, params):
                self.next_id += 1
                assert method == 'thread/list' and params['cwd'] == str(fixture.root)
                fixture.probes.append(('session-list', params['cwd']))
                return {'data': [], 'nextCursor': None, 'backwardsCursor': None}
            def close(self): pass
        self.sessions = SessionService(hub_root=self.root, adapter_factory=Discovery, identity=lambda: 'synthetic-environment', wall=lambda: 1000)
        self.host = self._host()
        assembly_path = Path(__file__).resolve().parents[1] / 'scripts/hub_server.py'
        spec = importlib.util.spec_from_file_location('pch_functional_product_assembly', assembly_path)
        assembly = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(assembly)
        self.materials = MaterialService(self.projects, self.designs)
        self.server, observer, preview = assembly.assemble_server(self.root, host='127.0.0.1', port=0, services={
            'projects': self.projects, 'designs': self.designs, 'previews': self.previews, 'tasks': self.tasks,
            'session_view': self.sessions, 'host_observer': self.host, 'materials': self.materials,
            'owner_auth': OwnerAuth(provider=lambda: self.owner)})
        assert observer is self.host and preview is None
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, name='functional-fixture-http')
        self.thread.start()

    def _host(self):
        proc = self.root / 'synthetic-proc'; proc.mkdir()
        hwmon = self.root / 'synthetic-hwmon'; hwmon.mkdir()
        (proc / 'stat').write_text('cpu 100 0 0 800 0 0 0 0\ncpu0 0\n')
        (proc / 'meminfo').write_text('MemTotal: 1024 kB\nMemAvailable: 512 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n')
        (proc / 'diskstats').write_text('')
        def run(argv, **kwargs):
            assert argv[0] in {'findmnt', 'nvidia-smi', 'systemctl'}
            self.probes.append(('host-command', argv[0]))
            stdout = json.dumps({'filesystems': [{'source': '/dev/synthetic', 'target': str(self.root), 'fstype': 'ext4'}]}) if argv[0] == 'findmnt' else ''
            if argv[0] == 'systemctl': stdout = 'ActiveState=inactive\nSubState=dead\nMainPID=0\n'
            return SimpleNamespace(returncode=0, stdout=stdout)
        def statvfs(path):
            assert Path(path) == self.root
            self.probes.append(('host-filesystem', Path(path)))
            return SimpleNamespace(f_blocks=100, f_bavail=80, f_frsize=1024)
        host = HostObserver({'hub': self.root}, proc=proc, hwmon=hwmon, run=run, statvfs=statvfs, wall=lambda: 1000)
        host.sample()
        return host

    def writer_check(self, grant):
        return grant.get('project_id') in self.roots and grant.get('root') == str(self.roots[grant['project_id']])

    @property
    def origin(self): return self.server.origin

    def call(self, method='GET', path='/api/projects', body=None, headers=None):
        sent = {'Cookie': self.cookie} if self.cookie else {}
        if method == 'POST': sent.update({'Origin': self.origin, 'X-Hub-CSRF': self.csrf or 'missing', 'Content-Type': 'application/json'})
        sent.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, json.dumps(body).encode() if body is not None else None, sent)
            response = connection.getresponse(); raw = response.read(); response_headers = dict(response.getheaders())
            data = json.loads(raw) if response_headers.get('Content-Type', '').startswith('application/json') else raw
            return response.status, response_headers, data
        finally: connection.close()

    def handshake(self, *, owner=True):
        status, headers, data = self.call(path='/api/session')
        assert status == 200
        self.cookie, self.csrf = headers['Set-Cookie'].split(';', 1)[0], data['data']['csrf_token']
        if owner:
            status, headers, data = self.call('POST', '/api/owner/login', {'token': self.owner})
            assert status == 200
            self.cookie, self.csrf = headers['Set-Cookie'].split(';', 1)[0], data['data']['csrf_token']
        return {'classification': self.classification, 'owner_authenticated': owner}

    def run_worker(self, project='fixture-project-a', *, background=False):
        worker = TaskWorker(self.task_store, adapter_factory=lambda root: ControlledAdapter(self, root),
                            writer_check=self.writer_check, only_task_id=self.payloads[project]['request_id'], request_timeout=1)
        if background:
            thread = threading.Thread(target=worker.run_one, name='functional-fixture-worker')
            self.workers.append(thread); thread.start(); return thread
        return worker.run_one()

    def update_source(self, project_id, *, completed, objective):
        if project_id not in self.roots:
            raise ValueError('Only synthetic task projects A/B may change')
        if type(completed) is not int or completed < 0 or type(objective) is not str or not objective.strip():
            raise ValueError('Invalid synthetic source values')
        path = self.roots[project_id] / 'STATE.yaml'
        old = path.read_bytes()
        data = yaml.safe_load(old)
        data['progress']['completed'] = completed
        data['current_work']['objective'] = objective
        updated_at = datetime.now(timezone.utc).isoformat()
        data['updated_at'] = updated_at
        raw = yaml.safe_dump(data, sort_keys=False).encode()
        temporary = path.with_name('.STATE-fixture-' + secrets.token_hex(8))
        try:
            with temporary.open('xb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists(): temporary.unlink()
        return {'project_id': project_id, 'old_sha256': hashlib.sha256(old).hexdigest(),
                'new_sha256': hashlib.sha256(raw).hexdigest(), 'updated_at': updated_at,
                'source': {'completed': data['progress']['completed'], 'objective': data['current_work']['objective']},
                'collected': False}

    def sync(self, only_project_id=None):
        if only_project_id is not None and only_project_id not in self.roots:
            raise ValueError('Only synthetic task projects A/B may be selected')
        resolver = self.projects._resolver()
        ledger = RefreshLedger(self.root, result_validator=validate_result)
        result, code = sync_request(self.root, resolver, ledger, 'fixture-control-' + secrets.token_hex(8),
                                   project_ids=[only_project_id] if only_project_id else None)
        return {'exit_code': code, 'result': result}

    def _control(self, argv, *, check=True):
        result = self.command_run(argv, capture_output=True, text=True, timeout=15, check=False)
        if check and result.returncode:
            raise RuntimeError('Fixture unit command failed: ' + argv[0] + ' exit=' + str(result.returncode))
        return result

    def hold_batch(self):
        if self.batch_child is not None: raise RuntimeError('Fixture batch already held')
        program = ('import fcntl,os,sys; '
                   'fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600); '
                   'fcntl.flock(fd,fcntl.LOCK_EX); print("held",flush=True); '
                   'sys.stdin.read(); os.close(fd)')
        self.batch_child = subprocess.Popen([sys.executable, '-c', program,
            str(self.refresh_ledger.path.with_name('.sync.lock'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready, _, _ = select.select([self.batch_child.stdout], [], [], 3)
        if not ready or self.batch_child.stdout.readline().strip() != 'held':
            self.release_batch(); raise RuntimeError('Fixture batch handshake failed')
        raw = Path('/proc', str(self.batch_child.pid), 'stat').read_text()
        return {'batch_held': True, 'pid': self.batch_child.pid,
                'start_ticks': int(raw.rsplit(') ', 1)[1].split()[19])}

    def release_batch(self):
        if self.batch_child is not None:
            self.batch_child.stdin.close()
            self.batch_child.wait(timeout=3)
            self.batch_child.stdout.close(); self.batch_child.stderr.close()
            if self.batch_child.returncode != 0: raise RuntimeError('Fixture batch child failed')
            self.batch_child = None
        return {'batch_released': True}

    def timer_start(self, *, unit_dir=None):
        if self.timer_prefix is not None: raise RuntimeError('Fixture timer already configured')
        # No default production identity and no credential/environment copying.
        prefix = 'pch-fixture-' + secrets.token_hex(12)
        directory = Path(unit_dir) if unit_dir is not None else Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'systemd/user'
        if not directory.is_absolute() or any(path.is_symlink() for path in (directory, *directory.parents)):
            raise ValueError('Unsafe fixture unit directory')
        directory.mkdir(parents=True, exist_ok=True)
        if directory.stat().st_uid != os.getuid(): raise ValueError('Foreign fixture unit directory')
        units = render_units(self.root, Path(__file__).resolve().parents[1], Path(sys.executable),
                             incremental=True, unit_prefix=prefix)
        if any((directory / name).exists() or (directory / name).is_symlink() for name in units):
            raise FileExistsError('Fixture unit collision')
        self.timer_prefix, self.timer_units = prefix, units
        self.timer_since = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        try:
            for name, content in units.items():
                path = directory / name
                with path.open('x', encoding='utf-8') as stream:
                    os.fchmod(stream.fileno(), 0o600); stream.write(content); stream.flush(); os.fsync(stream.fileno())
                self.timer_created[name] = path
            self.timer_loaded = True
            self._control(['systemctl', '--user', 'daemon-reload'])
            # Start the timer only. The service is invoked exclusively by systemd.
            self._control(['systemctl', '--user', 'start', prefix + '.timer'])
        except BaseException:
            self.timer_stop()
            raise
        return {'classification': self.classification, 'unit_prefix': prefix,
                'timer': prefix + '.timer', 'service': prefix + '.service',
                'interval_seconds': 60, 'daily': '09:00 Asia/Tokyo', 'started': True}

    def timer_status(self):
        if self.timer_prefix is None: return {'configured': False, 'invocations': []}
        prefix = self.timer_prefix
        properties = 'Id,ActiveState,SubState,InvocationID,MainPID,ExecMainPID,ExecMainStartTimestamp,ExecMainStartTimestampMonotonic,ExecMainCode,ExecMainStatus,LastTriggerUSec,NextElapseUSecRealtime'
        show = self._control(['systemctl', '--user', 'show', prefix + '.timer', prefix + '.service', '--property=' + properties], check=False)
        units = []
        for block in show.stdout.strip().split('\n\n'):
            fields = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
            if fields.get('Id') in self.timer_units: units.append(fields)
        journal = self._control(['journalctl', '--user', '--unit=' + prefix + '.service', '--since=' + self.timer_since,
                                 '--output=json', '--no-pager', '-n', '3000'], check=False)
        groups = {}
        journal_reason = None
        if journal.returncode:
            journal_reason = 'JOURNAL_UNAVAILABLE_EXIT_' + str(journal.returncode)
        elif len(journal.stdout) > 1024 * 1024:
            journal_reason = 'JOURNAL_OUTPUT_LIMIT'
        else:
            for line in journal.stdout.splitlines():
                try: row = json.loads(line)
                except (ValueError, TypeError): continue
                if row.get('_SYSTEMD_USER_UNIT') != prefix + '.service': continue
                invocation = row.get('_SYSTEMD_INVOCATION_ID')
                if not invocation: continue
                group = groups.setdefault(invocation, {'invocation_id': invocation, 'pid': row.get('_PID'),
                    'first_journal_realtime_us': row.get('__REALTIME_TIMESTAMP'), 'first_journal_monotonic_us': row.get('__MONOTONIC_TIMESTAMP'),
                    'start_ticks': None, 'messages': []})
                if row.get('_TRANSPORT') == 'stdout' and type(row.get('MESSAGE')) is str:
                    group['messages'].append(row['MESSAGE'])
        invocations = []
        for group in groups.values():
            try: payload = json.loads('\n'.join(group.pop('messages')))
            except (ValueError, TypeError): payload = {}
            for key in ('request_id', 'mode', 'scheduled_at', 'status', 'appended_count', 'unchanged_count', 'head', 'failure_code_counts'):
                if key in payload: group[key] = payload[key]
            group['result_readable'] = bool(payload)
            invocations.append(group)
        if not invocations and journal_reason is None: journal_reason = 'NO_READABLE_INVOCATIONS'
        return {'configured': True, 'unit_prefix': prefix, 'systemctl_exit': show.returncode,
                'units': units, 'journal_exit': journal.returncode, 'journal_reason': journal_reason,
                'invocations': invocations, 'ledger_head': self.projects.ledger_head()}

    def timer_stop(self):
        if self.timer_prefix is None: return {'stopped': True, 'configured': False}
        prefix = self.timer_prefix
        # Preserve the temp data root if service shutdown or file ownership fails.
        if self.timer_loaded:
            self._control(['systemctl', '--user', 'stop', prefix + '.timer', prefix + '.service'])
        for name, path in list(self.timer_created.items()):
            if path.is_symlink() or path.read_text() != self.timer_units[name]:
                raise RuntimeError('Fixture unit changed; preserved for reconciliation')
            path.unlink()
            del self.timer_created[name]
        self._control(['systemctl', '--user', 'daemon-reload'])
        self.timer_prefix, self.timer_units, self.timer_loaded = None, {}, False
        return {'stopped': True, 'unit_prefix': prefix, 'removed_owned_units': True}

    def close(self):
        self.timer_stop()
        self.release_batch()
        self.release.set()
        for thread in self.workers: thread.join(3)
        if any(thread.is_alive() for thread in self.workers): raise RuntimeError('fixture worker remains active')
        if self.server:
            if self.thread and self.thread.is_alive(): self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(3)
            if self.thread.is_alive(): raise RuntimeError('fixture HTTP remains active')
        if hasattr(self, 'host'): self.host.stop()
        self.cookie = self.csrf = self.owner = None
        self.source.close()
        self.closed = True

    def __enter__(self): return self
    def __exit__(self, *exc): self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true', required=True)
    parser.add_argument('--owner-fd', type=int)
    parser.add_argument('--directory-size', type=int, default=2)
    args = parser.parse_args()
    owner = None
    if args.owner_fd is not None:
        with os.fdopen(args.owner_fd) as stream: owner = stream.readline().strip()
        if not 32 <= len(owner) <= 512: raise ValueError('invalid synthetic owner pipe')
    with FunctionalFixture(owner=owner, directory_size=args.directory_size) as fixture:
        print(json.dumps({'ready': True, 'classification': fixture.classification, 'origin': fixture.origin}), flush=True)
        for line in sys.stdin:
            command = json.loads(line); action = command.get('action')
            if action == 'stop': break
            if action == 'handshake': result = fixture.handshake()
            elif action == 'run': result = {'ran': fixture.run_worker(command.get('project_id', 'fixture-project-a'))}
            elif action == 'health': result = {'status': fixture.call(path='/api/health')[0]}
            elif action == 'source': result = fixture.update_source(command.get('project_id'), completed=command.get('completed'), objective=command.get('objective'))
            elif action == 'sync': result = fixture.sync(command.get('only_project_id'))
            elif action == 'hold-batch': result = fixture.hold_batch()
            elif action == 'release-batch': result = fixture.release_batch()
            elif action == 'timer-start': result = fixture.timer_start()
            elif action == 'timer-status': result = fixture.timer_status()
            elif action == 'timer-stop': result = fixture.timer_stop()
            elif action == 'runtime':
                for key in ('worker_runtime', 'provider'):
                    if key in command:
                        if type(command[key]) is not bool: raise ValueError('invalid fixture runtime state')
                        fixture.runtime[key]['available'] = command[key]
                result = {'runtime_updated': True}
            else: result = {'error': 'unknown control action'}
            print(json.dumps(result), flush=True)
    print(json.dumps({'stopped': True}), flush=True)

if __name__ == '__main__': main()
