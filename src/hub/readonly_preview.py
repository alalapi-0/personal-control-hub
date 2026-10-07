"""Owner-selected real-code preview, never a project execution capability.

Only the named learning fixture is supported. The browser cannot choose a root,
URL, command, server or file. Every request rechecks the captured source version.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

from .preview_service import PreviewService
from .service_contract import ServiceError
from .workbench_store import digest

PROJECT = 'computer-study-plan'
HEAD = 'f5570473cf213f42b52b597e3e46e368dc53932e'
ROOT = Path('/home/alalapi/Projects/computer-study-plan')
CANDIDATE = 'csp-home-readonly-code'
FILES = frozenset({'progress.html', 'progress_ui.js', 'progress_ui.css',
    'progress_data.js', 'rounds_data.js', 'progress.json',
    'content/courses/linux-foundations/course.json', 'tests/ui/fixture_server.py',
    'rounds/round_00/week1/notes.md', 'rounds/round_00/final/command_cheatsheet.md',
    'rounds/round_01/final/command_cheatsheet.md', 'rounds/round_02/final/command_cheatsheet.md',
    'rounds/round_06/final/linux_automation_cheatsheet.md', 'plans/linux/README.md'})
ROUTES = frozenset('/' + f for f in FILES if f != 'tests/ui/fixture_server.py')
APIS = frozenset({'/api/health', '/api/events', '/api/feedback', '/api/saves', '/api/terminal'})
MANIFEST = 'docs/reports/linux-workbench/csp-preview-manifest-v2.json'
BASELINE = 'csp-readonly-code-baseline'
SNAPSHOT_REVISION = 2


class ReadOnlyPreview:
    candidate_id = CANDIDATE

    def __init__(self, hub_root, projects, designs):
        self.hub_root, self.projects, self.designs = Path(hub_root), projects, designs
        self.fixture = None
        self.requests = []
        self.manifest_path = self.hub_root / MANIFEST
        try:
            if self.manifest_path.is_symlink() or self.manifest_path.stat().st_size > 16384:
                raise ValueError()
            self.manifest = json.loads(self.manifest_path.read_bytes())
            if (set(self.manifest) != {'schema', 'project_id', 'head', 'entry', 'files', 'captures', 'observed_at'}
                    or self.manifest['schema'] != 1 or self.manifest['project_id'] != PROJECT
                    or self.manifest['head'] != HEAD or self.manifest['entry'] != 'progress.html'
                    or not isinstance(self.manifest['files'], dict) or set(self.manifest['files']) != FILES
                    or any(not isinstance(v, str) or len(v) != 64 or any(c not in '0123456789abcdef' for c in v)
                           for v in self.manifest['files'].values())
                    or not isinstance(self.manifest['captures'], dict)
                    or set(self.manifest['captures']) != {'desktop', 'mobile'}
                    or any(not isinstance(c, dict) or set(c) != {'path', 'sha256', 'width', 'height'}
                           or not isinstance(c['path'], str)
                           or not isinstance(c['sha256'], str) or len(c['sha256']) != 64
                           or any(v not in '0123456789abcdef' for v in c['sha256'])
                           or any(type(c[v]) is not int or c[v] <= 0 for v in ('width', 'height'))
                           for c in self.manifest['captures'].values())):
                raise ValueError()
            self.manifest_hash = digest(self.manifest)
        except (OSError, ValueError, TypeError):
            raise ServiceError('REAL_PREVIEW_REGISTRATION_INVALID', status=503) from None

    def _registration(self):
        # Startup pinning is insufficient: a new process must still match the
        # immutable baseline that owns these images and their annotations.
        state = self.designs._read()
        candidates = [f for f in state['facts'] if f['kind'] == 'candidate' and f['id'] == self.candidate_id]
        if not candidates:
            raise ServiceError('REAL_PREVIEW_VERSION_STALE', status=409)
        candidate = max(candidates, key=lambda f: f['revision'])
        self.designs._assert_current_candidate(state, candidate)
        bindings = candidate['baseline_bindings']
        if (candidate.get('purpose') != 'read_only_snapshot'
                or candidate['revision'] != SNAPSHOT_REVISION
                or candidate.get('decision_eligible') is not False
                or candidate.get('execution_allowed') is not False
                or len(bindings) != 1 or bindings[0]['project_id'] != PROJECT
                or bindings[0]['baseline_id'] != BASELINE
                or bindings[0]['baseline_revision'] != SNAPSHOT_REVISION):
            raise ServiceError('REAL_PREVIEW_VERSION_STALE', status=409)
        binding = bindings[0]
        baselines = [f for f in state['facts'] if f['kind'] == 'baseline'
            and (f['id'], f['revision'], f['content_hash']) ==
            (binding['baseline_id'], binding['baseline_revision'], binding['baseline_hash'])]
        if (len(baselines) != 1 or baselines[0]['source']['kind'] != 'repository'
                or baselines[0]['source']['commit'] != HEAD
                or baselines[0]['source']['reference'] != MANIFEST + '#sha256=' + self.manifest_hash):
            raise ServiceError('REAL_PREVIEW_VERSION_STALE', status=409)
        return candidate

    def check(self):
        try:
            self._registration()
            if self.manifest_path.is_symlink() or digest(json.loads(self.manifest_path.read_bytes())) != self.manifest_hash:
                raise ServiceError('REAL_PREVIEW_VERSION_STALE', status=409)
            state_path = self.hub_root / 'STATE.yaml'
            if state_path.is_symlink(): raise ValueError()
            state = yaml.safe_load(state_path.read_bytes())['linux_visual_workbench']
            grants = state['authorization'].get('readonly_preview_grants', [])
            if not any(g.get('project_id') == PROJECT and g.get('root') == str(ROOT)
                    and g.get('head') == HEAD and g.get('state') == 'active'
                    and g.get('server') == 'tests/ui/fixture_server.py:FixtureServer(task_actions=False)' for g in grants):
                raise ServiceError('REAL_PREVIEW_GRANT_REVOKED', status=403)
            resolver = self.projects._resolver(); resolver._check_registry()
            project = resolver.projects.get(PROJECT)
            if not project or not PreviewService._eligible(project) or project['root_path'] != str(ROOT):
                raise ServiceError('REAL_PREVIEW_PROJECT_REJECTED', status=403)
            if any(p.is_symlink() for p in [ROOT, *ROOT.parents]): raise ValueError()
            actual = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                capture_output=True, timeout=2, check=True).stdout.decode().strip()
            if actual != HEAD: raise ServiceError('REAL_PREVIEW_VERSION_STALE', status=409)
            for name, expected in self.manifest['files'].items():
                path = ROOT / name
                if any(p.is_symlink() for p in [path, *path.parents]) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ServiceError('REAL_PREVIEW_VERSION_STALE', status=409)
            resolver._check_registry()
        except ServiceError: raise
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
            raise ServiceError('REAL_PREVIEW_UNAVAILABLE', status=503) from None

    def start(self):
        if self.fixture is not None: raise ServiceError('REAL_PREVIEW_ALREADY_RUNNING')
        self.check()
        # Disable bytecode before executing the approved external module.
        import sys
        previous = sys.dont_write_bytecode; sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location('hub_owned_csp_fixture', ROOT / 'tests/ui/fixture_server.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        finally: sys.dont_write_bytecode = previous
        fixture = module.FixtureServer(task_actions=False)
        adapter = self

        class RestrictedHandler(module.FixtureHandler):
            def send_response(self, code, message=None):
                if len(adapter.requests) < 512:
                    adapter.requests.append({'method': self.command, 'path': urlsplit(self.path).path, 'status': code})
                super().send_response(code, message)

            def do_GET(self):
                try: adapter.check()
                except ServiceError as error:
                    self._json(error.status, {'ok': False, 'error': error.code}); return
                parts = urlsplit(self.path)
                if parts.scheme or parts.netloc or parts.path != unquote(parts.path) or parts.path not in ROUTES | APIS:
                    self._json(404, {'ok': False, 'error': 'unregistered_preview_resource'}); return
                super().do_GET()

            # HEAD must use the same resource/version fence, never SimpleHTTP's root listing.
            do_HEAD = do_GET

            def end_headers(self):
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'")
                self.send_header('X-Content-Type-Options', 'nosniff')
                super().end_headers()

        fixture.httpd.RequestHandlerClass = RestrictedHandler
        fixture.__enter__(); self.fixture = fixture
        try: self.check()
        except BaseException:
            self.stop(); raise

    @property
    def origin(self):
        return self.fixture.url if self.fixture is not None else None

    def describe(self):
        self.check()
        candidate = self._registration()
        available = self.fixture is not None and self.fixture.thread.is_alive()
        return {'available': available, 'project_id': PROJECT,
            'source': 'real_code_readonly_fixture', 'code_head': HEAD,
            'manifest_hash': self.manifest_hash, 'manifest_observed_at': self.manifest['observed_at'],
            'candidate_revision': candidate['revision'], 'candidate_hash': candidate['content_hash'],
            'origin': self.origin if available else None, 'frame_url': self.origin + '/progress.html' if available else None,
            'business_data': 'fixture', 'execution_allowed': False, 'source_mapping': None,
            'reason': None if available else 'REAL_PREVIEW_STOPPED'}

    def stop(self):
        if self.fixture is not None:
            fixture, self.fixture = self.fixture, None
            fixture.__exit__()
