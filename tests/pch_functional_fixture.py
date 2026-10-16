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
import stat
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
from hub.connection_sync import scope_plan, sync_request
from hub.systemd_refresh import render_units
from hub.connection_records import FIELDS, validate_result
from hub.metric_management import REVIEW_AXES, DIMENSIONS, validate_management_config
from hub.design_cli import _artifact, _baseline, _candidate
from hub.design_records import with_content_hash
from hub.design_service import DesignService
from hub.design_store import DesignStore
from hub.host_observer import HostObserver
from hub.material_service import MaterialService
from hub.capture_store import CaptureStore
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
    # Ordinary branch enforces image=None and never performs a workspace effect.
    read_only = True

    def __init__(self, fixture, root, *, ordinary=False):
        self.fixture, self.root = fixture, Path(root)
        self.ordinary = ordinary
        if self.root not in fixture.roots.values():
            raise AssertionError('foreign adapter root')
        self.scenario = fixture.scenarios.get(self.root.name, 'normal')
        self.denials, self.events = [], []
        self.ident = self.root.name
        self.fixture.probes.append(('worker-root', self.root))
        self.release = threading.Event() if ordinary else fixture.release
        self.closed = False
        if ordinary:
            with fixture.ordinary_lock: fixture.ordinary_adapters.append(self)
        self.requested = False

    def open(self): return self
    def thread(self, previous=None):
        return previous or 'thread-' + self.ident + ('-' + secrets.token_hex(6) if self.ordinary else '')
    def verify_parent_binding(self, thread, turn):
        if self.fixture.ordinary_turns.get(thread) != (self.root, turn, 'completed'):
            raise ServiceError('CODEX_PARENT_BINDING_REJECTED', status=409)
    def start(self, thread, text, image):
        if image is not None:
            self.fixture.effects.append(self.root)
            (self.root / 'index.html').write_text(AFTER)
        turn = 'turn-' + self.ident + ('-' + secrets.token_hex(6) if self.ordinary else '')
        self.bound_thread, self.bound_turn = thread, turn
        if self.ordinary:
            self.fixture.ordinary_turns[thread] = (self.root, turn, 'inProgress')
        def event(method, **extra):
            return {'method': method, 'params': {'threadId': thread, 'turnId': turn, **extra}}
        self.events = [event('turn/started', turn={'id': turn, 'status': 'inProgress'}),
                       event('turn/started', turn={'id': turn, 'status': 'inProgress'}),
                       event('turn/completed', turn={'id': turn, 'status': 'completed'})]
        if image is None:
            self.events.insert(-1, event('item/completed', item={'id': 'message-' + self.ident,
                'type': 'agentMessage', 'text': self.fixture.stress_replies.get(self.ident,
                    'Synthetic read-only response for ' + self.ident + '.') if self.ordinary else
                    'Synthetic read-only response for ' + self.ident + '.'}))
        if self.ordinary and self.scenario == 'failed_turn':
            self.events[-1]['params']['turn']['status'] = 'failed'
        if self.scenario == 'out_of_order': self.events.reverse()
        return turn
    def poll(self, timeout):
        if self.ordinary and self.scenario in {'input', 'secret_input', 'approval', 'timeout_input'} and not self.requested:
            self.requested = True
            method = 'item/commandExecution/requestApproval' if self.scenario == 'approval' else 'item/tool/requestUserInput'
            params = {'threadId': self.bound_thread, 'turnId': self.bound_turn, 'itemId': 'synthetic-control-item'}
            if self.scenario == 'approval':
                params.update(startedAtMs=1, command='/bin/echo synthetic', cwd=str(self.root))
            else:
                params.update(isBlocking=True, questions=[{'id': 'focus', 'header': '检查关注点',
                    'question': '请补充本次只读检查的关注点', 'isSecret': self.scenario == 'secret_input'}])
            request = {'id': 'synthetic-control', 'method': method, 'params': params}
            response = self.request_handler(request)
            self.fixture.control_observations.append({'thread_id': self.bound_thread, 'turn_id': self.bound_turn,
                'decision': response.get('decision'), 'status': response.get('status'),
                'question_ids': list(response.get('answers', {})), 'request_digest': digest(request)})
            if response.get('decision') != 'answer':
                self.denials.append({'method': method, 'digest': digest(request)})
        if self.scenario == 'delay':
            if not self.release.wait(min(timeout, .05)): return None
        if self.scenario in {'disconnect', 'lost_receipt'}:
            raise ServiceError('FIXTURE_CONNECTION_LOST', outcome='UNKNOWN')
        event = self.events.pop(0) if self.events else None
        if self.ordinary and event and event['method'] == 'turn/completed':
            params = event['params']
            self.fixture.ordinary_turns[params['threadId']] = (self.root, params['turnId'], 'completed')
        return event
    def check(self, argv):
        assert tuple(argv) == FIXED_CHECKS['fixture_sample']
        assert (self.root / 'index.html').read_text() == AFTER
        return {'exitCode': 1 if self.scenario == 'failure' else 0, 'stdout': 'synthetic check', 'stderr': ''}
    def interrupt(self, thread, turn):
        self.fixture.interrupts.append((thread, turn))
        self.release.set()
    def close(self): self.closed = True


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
        self.source_faults = {}
        self.excluded_ids = set()
        self.ordinary_turns = {}
        self.ordinary_lock = threading.RLock()
        self.ordinary_adapters = []
        self.ordinary_runtime = None
        self.stress_replies = {}
        self.stress_metadata = None
        self.control_observations = []
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
                if project_id in fixture.excluded_ids:
                    raise AssertionError('excluded source refresh must not probe its root')
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
        self.assembly = assembly
        self._start_server()

    def _start_server(self):
        self.server, observer, preview = self.assembly.assemble_server(self.root, host='127.0.0.1', port=0, services={
            'projects': self.projects, 'designs': self.designs, 'previews': self.previews, 'tasks': self.tasks,
            'session_view': self.sessions, 'host_observer': self.host, 'materials': self.materials,
            'owner_auth': OwnerAuth(provider=lambda: self.owner)}, ordinary_runtime=self.ordinary_runtime)
        assert observer is self.host and preview is None
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, name='functional-fixture-http')
        self.thread.start()

    def preview_sample(self):
        """Opt-in saved synthetic media; never a real capture or task artifact."""
        if hasattr(self, 'preview_paths'):
            return {'preview_ready': True, 'materials': len(self.preview_paths),
                    'classification': self.classification}
        material = self.root / 'docs/reports/ui_design_governance/functional-fixture'
        self.preview_paths, self.saved_capture_paths = {}, {}
        for project, root in self.roots.items():
            self.preview_paths[project + '-image'] = material / (project + '.png')
            evidence = []
            for suffix, body in [('report.md', 'Synthetic saved report for ' + project + '\n'),
                                 ('source.html', '<script>window.syntheticExecuted=true</script>\nSynthetic HTML source ' + project)]:
                path = material / (project + '-' + suffix)
                path.write_text(body)
                aid = project + '-' + suffix.replace('.', '-') + '-' + 'long-name-' * 7 + 'end'
                artifact = _artifact(aid, path.relative_to(self.root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
                artifact['scope']['members'][0]['project_id'] = project
                self.design_store.append_fact(artifact, expected_revision=self.design_store.read()['revision'], request_id=aid)
                self.preview_paths[aid] = path
                evidence.append(aid)
            candidate = copy.deepcopy(next(f for f in self.design_store.read()['facts']
                if f['kind'] == 'candidate' and f['id'] == project + '-candidate'))
            candidate['revision'] += 1
            candidate['evidence_refs'] = evidence
            candidate = with_content_hash(candidate)
            self.design_store.append_fact(candidate, expected_revision=self.design_store.read()['revision'],
                                          request_id=project + '-preview-candidate')
            image = (root / 'fixture.png').read_bytes()
            sha = hashlib.sha256(image).hexdigest()
            # The sample has no actual capture time or runtime proof. Both API
            # and UI must keep this imported synthetic screenshot historical.
            record = {'schema_version': '1.0', 'request_id': project + '-saved',
                'request_sha256': digest([project, 'synthetic-saved-preview']),
                'project_id': project, 'page_id': 'synthetic-saved-page', 'domain': 'implementation',
                'status': 'success', 'reason': None, 'method': 'existing_desktop',
                'captured_at': None, 'observed_at': '2026-10-16T00:00:00Z',
                'source_sha256': None, 'build_sha256': None, 'source_proof_sha256': None,
                'runtime_verified': False, 'artifact_ref': 'data/previews/images/' + project + '/' + sha + '.png',
                'artifact_sha256': sha, 'media_type': 'image/png', 'width': 32, 'height': 32,
                'expires_at': '2099-10-16T00:00:00Z',
                'provenance': {'kind': 'existing_desktop', 'report_sha256': digest(['synthetic', project]),
                    'reported_at': '2026-10-15T00:00:00Z', 'historical_candidate': digest(['synthetic-candidate', project])}}
            CaptureStore(self.root).commit(record, image)
            self.saved_capture_paths[project] = self.root / record['artifact_ref']
        self.materials.prepare_fixture(set(self.preview_paths))
        return {'preview_ready': True, 'materials': len(self.preview_paths), 'classification': self.classification}

    def drift_preview_materials(self):
        if not hasattr(self, 'preview_paths') or getattr(self, 'preview_drifted', False):
            raise ValueError('Synthetic preview drift requires one prepared sample')
        project = 'fixture-project-a'
        path = self.preview_paths[project + '-image']
        artifact = _artifact('synthetic-preview-revision', path.relative_to(self.root).as_posix(),
                             hashlib.sha256(path.read_bytes()).hexdigest())
        artifact['scope']['members'][0]['project_id'] = project
        self.design_store.append_fact(artifact, expected_revision=self.design_store.read()['revision'],
                                      request_id=artifact['id'])
        self.materials.prepare_fixture(set(self.preview_paths))
        self.preview_drifted = True
        return {'preview_drifted': True, 'classification': self.classification}

    def invalidate_preview(self, project_id, kind):
        if project_id not in self.roots or not hasattr(self, 'preview_paths'):
            raise ValueError('Only prepared synthetic project previews may change')
        if kind == 'material_image': path = self.preview_paths[project_id + '-image']
        elif kind == 'saved_capture': path = self.saved_capture_paths[project_id]
        else: raise ValueError('Unknown synthetic preview kind')
        if path.is_symlink() or not path.is_file(): raise ValueError('Synthetic preview already changed')
        path.unlink()
        return {'preview_invalidated': True, 'project_id': project_id, 'kind': kind}

    def management_sample(self):
        """Explicit O3 setup; default O0/O1 data remains unchanged."""
        a, b = 'fixture-project-a', 'fixture-project-b'
        proposal = {'id': 'synthetic-a-b-related', 'project_ids': [a, b], 'kind': 'pipeline',
                    'status': 'proposed', 'shared_tasks': ['Synthetic shared review'],
                    'differences': ['Separate synthetic ownership'], 'not_shared': ['Source authority'],
                    'inventory_index': 0, 'evidence_refs': ['fixture://synthetic-o3-evidence']}
        projection = {'projects': [{'project_id': project, 'status': 'proposed', 'reason': None,
                                   'relations': [proposal], 'program_link_authority': False,
                                   'design_selection_authority': False} for project in (a, b)]}
        path = self.root / 'data/connections/fixture-relations.json'
        raw = json.dumps(projection, sort_keys=True).encode()
        if path.exists():
            if path.is_symlink() or path.read_bytes() != raw: raise RuntimeError('Synthetic relation file changed')
        else:
            with path.open('xb') as stream: stream.write(raw)
        self.projects.relations_path = path
        state_path = self.roots[b] / 'STATE.yaml'
        state = yaml.safe_load(state_path.read_text())
        state['current_work'].update(status='blocked', completed=False, objective='Synthetic blocked source dependency')
        state_path.write_text(yaml.safe_dump(state, sort_keys=False))
        synced = self.sync(b)
        if synced['exit_code'] != 0: raise RuntimeError('Synthetic blocked source could not be collected')
        return {'management_ready': True, 'project_ids': [a, b], 'proposal_id': proposal['id'],
                'proposal_source_sha256': hashlib.sha256(raw).hexdigest(), 'blocked_project': b,
                'proposals': self.projects.get_management(a).get('proposals', [])}

    def empty_directory(self):
        """Explicit empty-registry fixture; retained sources/ledger are not erased."""
        if (getattr(self, 'ordinary_grants', None) is not None or self.ordinary_runtime is not None
                or self.task_store.tasks() or self.workers or self.ordinary_adapters
                or self.task_store.grants() != sorted(self.grants.values(), key=lambda grant: grant['grant_id'])):
            raise ValueError('Empty directory must precede task admission and additional grants')
        base = Path(self.source.temporary.name).absolute()
        if self.source.base != base or self.root != self.source.hub:
            raise ValueError('Empty directory requires this fixture owned root')
        owned = [self.root, *self.directory_roots.values()]
        for root in owned:
            if not root.is_absolute() or not root.is_relative_to(base) or root.resolve(strict=True) != root:
                raise ValueError('Empty directory cannot inspect foreign or redirected roots')
        registry = self.root / 'data/registry/external_projects.yaml'
        scope_path = self.root / 'data/connections/sync_scope.yaml'
        if any(path.resolve(strict=True) != path or not path.is_relative_to(self.root)
               for path in (registry, scope_path)):
            raise ValueError('Empty directory requires original fixture control files')
        for row in self.source.projects:
            if self.directory_roots.get(row['id']) != Path(row['root_path']):
                raise ValueError('Empty directory registry root is not owned')
        scope_bytes = scope_path.read_bytes()
        scope = yaml.safe_load(scope_bytes)
        head = self.projects.ledger_head()
        self.source.projects.clear()
        self.source.save_registry()
        synced = self.sync()
        result = synced['result']
        current_head = self.projects.ledger_head()
        directory = self.projects.list_projects()
        if (synced['exit_code'] != 2 or result['status'] != 'NO_ELIGIBLE_PROJECTS'
                or result['appended_count'] != 0 or current_head != head
                or scope_path.read_bytes() != scope_bytes or directory['total'] != 0 or directory['projects']):
            raise RuntimeError('Synthetic empty directory did not preserve its ledger and scope')
        return {'empty_directory_ready': True, 'classification': self.classification, 'total': 0,
            'sync': {'exit_code': synced['exit_code'], 'status': result['status'], 'appended_count': 0},
            'ledger_head': current_head, 'ledger_unchanged': True, 'scope_project_ids': scope['project_ids']}

    def stress_sample(self, active_blocked=False):
        if type(active_blocked) is not bool:
            raise ValueError('active_blocked must be an explicit boolean')
        """Opt-in synthetic data; collect sources before registering ordinary grants."""
        if (getattr(self, 'ordinary_grants', None) is not None or self.ordinary_runtime is not None
                or self.task_store.tasks() or self.workers or self.ordinary_adapters):
            raise ValueError('Stress sample must precede all task admission and ordinary grants')
        if self.stress_metadata is not None:
            if self.stress_metadata.get('active_blocked', False) != active_blocked:
                raise ValueError('Stress sample variant is already frozen')
            return copy.deepcopy(self.stress_metadata)
        a, b = 'fixture-project-a', 'fixture-project-b'
        known = lambda value: {'value': value, 'state': 'known', 'reason': None}
        unknown = lambda reason: {'value': None, 'state': 'unknown', 'reason': reason}
        projects = []
        for project in (a, b):
            suffix = project[-1].upper()
            name = ('合成长名称只读项目' + suffix + ' · ') * 6 + 'LONG_DISPLAY_' + suffix * 128
            for row in self.source.projects:
                if row['id'] == project:
                    row.update(name=name, description=None)
            state_path = self.roots[project] / 'STATE.yaml'
            state = yaml.safe_load(state_path.read_text())
            local_blockers = copy.deepcopy(state['blockers'])
            for field in FIELDS:
                parts = field.split('.')
                target = state
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = None
            state['current_work']['objective'] = 'Synthetic stress objective ' + suffix + ': ' + '只读范围与未知事实分别保留。' * 12
            if active_blocked and project == a:
                state['current_work'].update(status='active', completed=False)
                state['blockers'] = local_blockers
            state['definition'] = {
                'official_name': known(name),
                'purpose': unknown('Synthetic source intentionally does not publish its long-term purpose.'),
                'outputs': unknown('Synthetic source has no verified output inventory.'),
                'roles': known(['Producer', 'Library', 'Synthetic read-only reviewer'] if project == a else
                               ['Consumer', 'Working copy', 'Synthetic read-only reviewer']),
                'aliases': known([{'name': '合成压力项目' + suffix, 'confirmed': True}]),
                'primary_project_id': unknown('Synthetic primary root is not declared.') if project == a else known(a),
                'working_copies': known([b] if project == a else []),
                'project_type': unknown('Synthetic source does not declare a project category.')}
            state_path.write_text(yaml.safe_dump(state, sort_keys=False, allow_unicode=True))
            declaration_path = self.roots[project] / 'hub.connection.yaml'
            declaration = yaml.safe_load(declaration_path.read_text())
            declaration['unknown_fields'] = {field: 'Synthetic stress source intentionally does not publish this fact.' for field in FIELDS}
            declaration['extensions'] = {'schema_version': '1.4',
                'fact_mapping': {'project_definition': {'source_ref': 'state', 'selector': {'path': ['definition']}}},
                'unknown_fields': {'project_definition': 'Synthetic definition is unavailable.'}}
            declaration_path.write_text(yaml.safe_dump(declaration, sort_keys=False))
            marker = 'SYNTHETIC_READONLY_STRESS_' + suffix
            unbroken = 'LONG_UNBROKEN_REPLY_' + suffix + '_'
            reply = marker + '\n' + ''.join(
                f'{index:02d} 合成只读压力答复 {suffix} ' + 'synthetic-readable-scope-' * 12 + '\n'
                for index in range(48)) + unbroken + suffix * 2048 + '\n' + marker + '_END'
            reply_bytes = len(reply.encode('utf-8'))
            if not 12 * 1024 <= reply_bytes <= 24 * 1024:
                raise AssertionError('Synthetic stress response must remain bounded')
            self.stress_replies[project] = reply
            projects.append({'project_id': project, 'name': name, 'reply_marker': marker,
                'reply_unbroken_marker': unbroken, 'reply_bytes': reply_bytes,
                'reply_sha256': hashlib.sha256(reply.encode()).hexdigest(),
                'unknown_business_fields': len(FIELDS) - (4 if active_blocked and project == a else 1)})
        self.source.save_registry()
        proposals = [{'id': 'synthetic-stress-' + kind, 'project_ids': [a, b], 'kind': kind,
            'status': 'proposed', 'shared_tasks': ['Synthetic bounded review; no shared execution authority.'],
            'differences': ['Separate source identity and read-only reply ownership.'],
            'not_shared': ['Execution grants, files and owner acceptance.'], 'inventory_index': index,
            'evidence_refs': ['fixture://synthetic-stress-' + kind]}
            for index, kind in enumerate(('pipeline', 'shared_review_pattern', 'shared_visual_language'))]
        projection = {'projects': [{'project_id': project, 'status': 'proposed', 'reason': None,
            'relations': proposals, 'program_link_authority': False, 'design_selection_authority': False}
            for project in (a, b)]}
        path = self.root / 'data/connections/fixture-stress-relations.json'
        with path.open('x') as stream:
            json.dump(projection, stream, sort_keys=True)
        self.projects.relations_path = path
        synced = self.sync()
        if synced['exit_code'] != 0:
            raise RuntimeError('Synthetic stress sources could not be collected')
        self.stress_metadata = {'stress_ready': True, 'classification': self.classification,
            'projects': projects, 'proposal_ids': [proposal['id'] for proposal in proposals], 'active_blocked': active_blocked}
        return copy.deepcopy(self.stress_metadata)

    def ordinary_sample(self, max_turn_seconds=120):
        """Explicit O4 trusted grants; no ordinary authority in default fixtures."""
        from hub.ordinary_tasks import make_grant
        if type(max_turn_seconds) is not int or max_turn_seconds not in {10, 120}:
            raise ValueError('Only bounded synthetic turn durations 10 or 120 are allowed')
        if self.ordinary_runtime is not None or self.task_store.tasks():
            raise ValueError('Ordinary grants must precede runtime and task admission')
        self.ordinary_grants = {}
        registry = self.root / 'data/registry/external_projects.yaml'
        registry_hash = hashlib.sha256(registry.read_bytes()).hexdigest()
        for project, root in self.roots.items():
            readme = root / 'README.md'
            if not readme.exists(): readme.write_text('Synthetic read-only project ' + project + '\n')
            grant = make_grant(root, project, registry_hash, project + '-ordinary-grant',
                               input_paths=['AGENTS.md', 'README.md'], classification='synthetic_fixture',
                               version=1, max_turn_seconds=max_turn_seconds)
            self.task_store.register_grant(grant)
            self.ordinary_grants[project] = grant
        self.tasks.ordinary_enabled = True
        return {'ordinary_ready': True, 'project_ids': list(self.ordinary_grants),
                'max_turn_seconds': max_turn_seconds, 'classification': self.classification}

    def ordinary_input(self, project_id):
        if project_id not in getattr(self, 'ordinary_grants', {}):
            raise ValueError('Only explicitly granted synthetic projects may change')
        path = self.roots[project_id] / 'README.md'
        path.write_text(path.read_text() + 'Synthetic input version changed.\n')
        return {'ordinary_input_changed': True, 'project_id': project_id}

    def release_ordinary(self, task_id=None):
        """Release an exact synthetic task's latch, never another task's wait."""
        with self.ordinary_lock:
            if task_id is None:
                adapters = list(self.ordinary_adapters)  # Explicit legacy all-release.
            else:
                task = self.task_store.task(task_id)
                if (not task or task['project'] not in self.roots
                        or task['payload'].get('kind') != 'ordinary_project'
                        or not task['thread_id'] or not task['turn_id']):
                    raise ValueError('Only exact bound synthetic ordinary tasks may release')
                adapters = [adapter for adapter in self.ordinary_adapters
                            if adapter.root == self.roots[task['project']]
                            and getattr(adapter, 'bound_thread', None) == task['thread_id']
                            and getattr(adapter, 'bound_turn', None) == task['turn_id']]
                if len(adapters) != 1:
                    raise ValueError('Exact synthetic adapter binding unavailable')
            for adapter in adapters: adapter.release.set()
        return {'ordinary_released': True, 'task_id': task_id, 'released_count': len(adapters)}

    def ordinary_observations(self):
        with self.ordinary_lock:
            return {'ordinary_control_observations': list(self.control_observations),
                    'interrupts': list(self.interrupts), 'effects': len(self.effects),
                    'ordinary_adapters': [{'project_id': adapter.ident,
                        'thread_id': getattr(adapter, 'bound_thread', None),
                        'turn_id': getattr(adapter, 'bound_turn', None),
                        'released': adapter.release.is_set(), 'closed': adapter.closed}
                        for adapter in self.ordinary_adapters]}

    def start_ordinary_runtime(self, request_timeout=10):
        from hub.ordinary_runtime import OrdinaryRuntime
        if not getattr(self, 'ordinary_grants', None) or self.ordinary_runtime is not None:
            raise ValueError('Explicit synthetic grants required; runtime cannot be replaced')
        self.ordinary_runtime = OrdinaryRuntime(self.task_store, grants=list(self.ordinary_grants.values()),
            provider_status={'available': True, 'classification': 'synthetic_fixture'},
            adapter_factory=lambda root: ControlledAdapter(self, root, ordinary=True),
            request_timeout=request_timeout)
        self.assembly.attach_ordinary_runtime(self.server, self.ordinary_runtime)
        self.ordinary_runtime.start()
        return {'ordinary_runtime_started': True, 'classification': 'synthetic_fixture'}

    def ordinary_regrant(self, project_id):
        from hub.ordinary_tasks import make_grant
        if project_id not in getattr(self, 'ordinary_grants', {}):
            raise ValueError('Only explicitly granted synthetic projects may rebind')
        previous = self.ordinary_grants[project_id]
        registry = self.root / 'data/registry/external_projects.yaml'
        grant = make_grant(self.roots[project_id], project_id,
                           hashlib.sha256(registry.read_bytes()).hexdigest(), previous['grant_id'],
                           input_paths=['AGENTS.md', 'README.md'], classification='synthetic_fixture',
                           version=previous['version'] + 1, max_turn_seconds=120)
        self.task_store.register_grant(grant)
        self.ordinary_grants[project_id] = grant
        return {'ordinary_regranted': True, 'project_id': project_id}

    def restart_server(self):
        if any(thread.is_alive() for thread in self.workers):
            raise RuntimeError('Cannot restart fixture with active owned worker')
        if self.ordinary_runtime is not None and any(task['status'] in {'running', 'validating', 'waiting_input', 'waiting_approval'}
                                                    for task in self.task_store.tasks()):
            raise RuntimeError('Cannot restart fixture with active owned ordinary worker')
        previous_origin, previous_instance = self.origin, self.server.backend_instance
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)
        if self.thread.is_alive(): raise RuntimeError('Old fixture HTTP remains active')
        self.cookie = self.csrf = None
        self.ordinary_runtime = None  # A new executor requires another explicit start.
        self._start_server()
        return {'restarted': True, 'origin': self.origin, 'backend_instance': self.server.backend_instance,
                'previous_origin': previous_origin, 'previous_backend_instance': previous_instance}

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

    def run_worker(self, project='fixture-project-a', *, background=False, task_id=None):
        if project not in self.roots: raise ValueError('Foreign synthetic project')
        selected = task_id or self.payloads[project]['request_id']
        if task_id is not None:
            task = self.task_store.task(task_id)
            if not task or task['project'] != project or task['payload'].get('kind') != 'ordinary_project':
                raise ValueError('Only exact synthetic ordinary task IDs may be selected')
        worker = TaskWorker(self.task_store, adapter_factory=lambda root: ControlledAdapter(self, root, ordinary=task_id is not None),
                            writer_check=self.writer_check, only_task_id=selected, request_timeout=1)
        if background:
            thread = threading.Thread(target=worker.run_one, name='functional-fixture-worker')
            self.workers.append(thread); thread.start(); return thread
        return worker.run_one()

    def _owned_state(self, project_id):
        if project_id not in self.roots:
            raise ValueError('Only owned synthetic task projects A/B may change')
        root = self.roots[project_id]
        base = Path(self.source.temporary.name).absolute()
        if (self.source.base != base or not root.is_relative_to(base) or root.resolve(strict=True) != root):
            raise ValueError('Synthetic source root is redirected or foreign')
        return root / 'STATE.yaml'

    def source_fault(self, project_id, kind):
        if kind not in {'missing', 'invalid', 'permission'} or project_id in self.source_faults:
            raise ValueError('Invalid or already active synthetic source fault')
        path = self._owned_state(project_id)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Synthetic source must be an original single-link file')
        original = path.read_bytes()
        mode = stat.S_IMODE(info.st_mode)
        if kind == 'permission' and os.geteuid() == 0:
            raise RuntimeError('Real permission-denial fixture requires an unprivileged process')
        if kind == 'missing':
            path.unlink()
        elif kind == 'invalid':
            path.write_bytes(b'progress: [unterminated synthetic YAML\n')
        else:
            path.chmod(0)
        self.source_faults[project_id] = {'bytes': original, 'mode': mode, 'kind': kind,
            'identity': (info.st_dev, info.st_ino)}
        return {'source_fault': kind, 'project_id': project_id, 'collected': False}

    def source_restore(self, project_id):
        path = self._owned_state(project_id)
        previous = self.source_faults.get(project_id)
        if previous is None:
            raise ValueError('No active owned synthetic source fault')
        try:
            current = path.lstat()
        except FileNotFoundError:
            current = None
        if previous['kind'] == 'missing':
            if current is not None:
                raise ValueError('Synthetic missing source was replaced before restore')
        elif (current is None or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
              or (current.st_dev, current.st_ino) != previous['identity']
              or stat.S_IMODE(current.st_mode) != (0 if previous['kind'] == 'permission' else previous['mode'])):
            raise ValueError('Synthetic fault source identity or mode changed')
        elif previous['kind'] == 'invalid' and path.read_bytes() != b'progress: [unterminated synthetic YAML\n':
            raise ValueError('Synthetic invalid source changed before restore')
        temporary = path.with_name('.source-restore-' + secrets.token_hex(8))
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, previous['mode'])
            with os.fdopen(fd, 'wb') as stream:
                stream.write(previous['bytes']);stream.flush();os.fchmod(stream.fileno(), previous['mode']);os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():temporary.unlink()
        del self.source_faults[project_id]
        return {'source_restored': True, 'project_id': project_id, 'collected': False}

    def excluded_sample(self):
        if (self.directory_size < 6 or self.excluded_ids or getattr(self, 'ordinary_grants', None) is not None
                or self.ordinary_runtime is not None or self.task_store.tasks()):
            raise ValueError('Excluded sample requires four extras before task authority')
        extras = [row for row in self.source.projects if row['id'] not in self.roots][:4]
        if len(extras) != 4:
            raise ValueError('Four original synthetic extra projects required')
        categories = [('removed', {'local_presence': {'status': 'removed_local'}}, 'removed_local'),
            ('cloud', {'project_type': 'cloud'}, 'cloud'),
            ('read-disabled', {'connection_read_allowed': False}, 'connection_read_disabled'),
            ('no_current_goal_access', {'access_profile': 'no_current_goal_access'}, 'connection_read_disabled')]
        scope_path = self.root / 'data/connections/sync_scope.yaml'
        scope_bytes = scope_path.read_bytes()
        rows = []
        for row, (category, changes, reason) in zip(extras, categories):
            root = self.directory_roots[row['id']]
            base = Path(self.source.temporary.name).absolute()
            if (Path(row['root_path']) != root or not root.is_relative_to(base) or root.resolve(strict=True) != root):
                raise ValueError('Excluded sample refuses foreign or redirected roots')
            row.update(changes)
            rows.append({'project_id': row['id'], 'category': category, 'reason': reason})
        self.source.save_registry()
        self.excluded_ids = {row['project_id'] for row in rows}
        marker = len(self.probes)
        synced = self.sync()
        plan = scope_plan(self.root, self.projects._resolver())
        expected = {row['project_id']: row['reason'] for row in rows}
        actual = {row['project_id']: row['reason'] for row in plan['excluded']}
        excluded_probes = [probe for probe in self.probes[marker:] if probe[0] == 'source-refresh'
            and probe[1] in {self.directory_roots[ident] for ident in self.excluded_ids}]
        if (scope_path.read_bytes() != scope_bytes or actual != expected
                or self.excluded_ids & set(plan['eligible_project_ids']) or excluded_probes):
            raise RuntimeError('Synthetic excluded roots were not kept outside collection')
        return {'excluded_ready': True, 'classification': self.classification, 'excluded': rows,
            'scope_total': plan['scope_total'], 'eligible_project_ids': plan['eligible_project_ids'],
            'sync': {'exit_code': synced['exit_code'], **{key: synced['result'][key] for key in ('status', 'appended_count')}}, 'excluded_probe_count': len(excluded_probes)}

    def update_source(self, project_id, *, completed, objective):
        if project_id not in self.roots:
            raise ValueError('Only synthetic task projects A/B may change')
        if (completed is not None and (type(completed) is not int or completed < 0)
                or type(objective) is not str or not objective.strip()):
            raise ValueError('Invalid synthetic source values')
        path = self.roots[project_id] / 'STATE.yaml'
        old = path.read_bytes()
        data = yaml.safe_load(old)
        if completed is not None:
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
        if self.ordinary_runtime is not None: self.ordinary_runtime.stop(timeout=3)
        self.release.set()
        self.release_ordinary()  # Retire only this fixture's manual mock workers.
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
            elif action == 'run':
                ran = fixture.run_worker(command.get('project_id', 'fixture-project-a'),
                                         task_id=command.get('task_id'), background=command.get('background', False))
                result = {'ran': True if isinstance(ran, threading.Thread) else ran,
                          'background': isinstance(ran, threading.Thread)}
            elif action == 'health': result = {'status': fixture.call(path='/api/health')[0]}
            elif action == 'management-sample': result = fixture.management_sample()
            elif action == 'preview-sample': result = fixture.preview_sample()
            elif action == 'preview-drift': result = fixture.drift_preview_materials()
            elif action == 'preview-invalidate': result = fixture.invalidate_preview(command.get('project_id'), command.get('kind'))
            elif action == 'empty-directory': result = fixture.empty_directory()
            elif action == 'stress-sample': result = fixture.stress_sample(command.get('active_blocked', False))
            elif action == 'ordinary-sample': result = fixture.ordinary_sample(command.get('max_turn_seconds', 120))
            elif action == 'ordinary-runtime-start': result = fixture.start_ordinary_runtime(command.get('request_timeout', 10))
            elif action == 'ordinary-observations': result = fixture.ordinary_observations()
            elif action == 'ordinary-input': result = fixture.ordinary_input(command.get('project_id'))
            elif action == 'ordinary-regrant': result = fixture.ordinary_regrant(command.get('project_id'))
            elif action == 'ordinary-scenario':
                project = command.get('project_id')
                if project not in fixture.roots or command.get('scenario') not in {'normal', 'delay', 'disconnect', 'input', 'secret_input', 'approval', 'timeout_input', 'failed_turn'}:
                    raise ValueError('Invalid synthetic ordinary scenario')
                fixture.scenarios[fixture.roots[project].name] = command['scenario']
                result = {'ordinary_scenario': command['scenario']}
            elif action == 'ordinary-release':
                result = fixture.release_ordinary(command.get('task_id'))
                for thread in fixture.workers: thread.join(3)
                if any(thread.is_alive() for thread in fixture.workers): raise RuntimeError('Fixture worker did not finish')
            elif action == 'ordinary-revoke':
                project = command.get('project_id')
                grant = copy.deepcopy(fixture.ordinary_grants[project])
                grant.update(version=grant['version'] + 1, expires_at=time.time() - 1)
                fixture.task_store.register_grant(grant)
                result = {'ordinary_revoked': True, 'project_id': project}
            elif action == 'restart': result = fixture.restart_server()
            elif action == 'source-fault': result = fixture.source_fault(command.get('project_id'), command.get('kind'))
            elif action == 'source-restore': result = fixture.source_restore(command.get('project_id'))
            elif action == 'excluded-sample': result = fixture.excluded_sample()
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
