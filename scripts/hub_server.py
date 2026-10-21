#!/usr/bin/env python3
"""Start the single-user Hub UI and API on literal loopback."""
import argparse
import contextlib
import os
import signal
import subprocess
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT / "src"))

from hub.design_service import DesignService
from hub.design_store import DesignStore
from hub.local_service import HubHTTPServer
from hub.project_service import ProjectService
from hub.host_observer import HostObserver
from hub.preview_service import PreviewService
from hub.workbench_store import WorkbenchStore
from hub.task_store import TaskStore
from hub.task_service import TaskService
from hub.service_contract import ServiceError
from hub.rr_payload import RRFeedbackError
from hub.material_service import MaterialService
from hub.session_service import SessionService, load_official_metadata_binding
from hub.readonly_preview import ReadOnlyPreview, MANIFEST
from hub.owner_auth import OwnerAuth, SystemdCredentialProvider


def attach_ordinary_runtime(server, runtime):
    """Attach one explicit local executor; construction does not start it."""
    tasks = server.tasks
    if (tasks is None or runtime.store is not tasks.store
            or getattr(server, 'ordinary_runtime', None) is not None):
        raise ServiceError('ORDINARY_RUNTIME_BINDING_REJECTED')
    scope=TaskService._ordinary_scope_ids(getattr(runtime, 'grant_ids', None))
    projects=getattr(runtime, 'grant_projects', None)
    if scope is None or projects is None:
        raise ServiceError('ORDINARY_RUNTIME_BINDING_REJECTED')
    tasks.bind_ordinary_grants(scope,projects=projects)
    tasks.writer_check = runtime.writer_check
    tasks.writer_check_configured = True
    tasks.runtime_status = runtime.status
    tasks.ordinary_enabled = True
    server.ordinary_runtime = runtime
    close = server.server_close
    def close_owned():
        try:
            runtime.stop()
        finally:
            close()
    server.server_close = close_owned


def ordinary_provider_status():
    """Observe existing native login on explicit startup, never from a GET.

    Output is reduced to a boolean; account/session values are not retained.
    This is readiness, not evidence of a completed provider turn.
    """
    from hub.codex_adapter import child_environment
    ready = False
    try:
        result = subprocess.run(['codex', 'login', 'status'], env=child_environment(),
                                capture_output=True, timeout=10, check=False)
        ready = (result.returncode == 0 and
                 (result.stdout + result.stderr).strip() == b'Logged in using ChatGPT')
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {'available': ready, 'classification': 'real'}


def assemble_server(root, *, host='127.0.0.1', port=8766, services=None, ordinary_runtime=None):
    """Construct the product without starting effects or initializing stores.

    Explicit dependencies let disposable instances use the same assembly while
    keeping source, host observations, identity and provider access isolated.
    This process-local injection point is never accepted from HTTP clients.
    """
    root = Path(root).absolute()
    if ('..' in root.parts or len(root.parents) < 2 or not root.is_dir()
            or any(path.is_symlink() for path in (root, *root.parents))):
        raise ServiceError('HUB_ROOT_REJECTED')
    services = dict(services or {})
    allowed = {'projects', 'designs', 'host_observer', 'previews', 'tasks',
               'materials', 'session_view', 'owner_auth', 'owner_session_store'}
    if set(services) - allowed:
        raise ValueError('Unknown Hub service dependency')
    def dependency(name, factory):
        return services[name] if name in services else factory()
    designs = dependency('designs', lambda: DesignService(DesignStore(root, 'data/design_governance/design-store.json')))
    observer = dependency('host_observer', lambda: HostObserver({'hub': root, 'temp': root.parents[1] / 'Temp'}))
    projects = dependency('projects', lambda: ProjectService(root))
    previews = dependency('previews', lambda: PreviewService(projects, designs, WorkbenchStore(root)))
    preview = None
    if (root / MANIFEST).exists() and 'previews' not in services:
        preview = ReadOnlyPreview(root, projects, designs)
        previews.readonly_preview = preview
    tasks = dependency('tasks', lambda: TaskService(projects, previews, TaskStore(root)))
    options = dict(host_observer=observer, previews=previews, tasks=tasks,
                   materials=dependency('materials', lambda: MaterialService(projects, designs)),
                   session_view=dependency('session_view', lambda: SessionService(hub_root=root)))
    if 'owner_auth' in services:
        options['owner_auth'] = services['owner_auth']
    from hub.owner_sessions import OwnerSessionStore
    options['owner_session_store'] = dependency('owner_session_store', lambda: OwnerSessionStore(root))
    server = HubHTTPServer(projects, designs, host=host, port=port, **options)
    if ordinary_runtime is not None:
        try:
            attach_ordinary_runtime(server, ordinary_runtime)
        except Exception:
            server.server_close()
            raise
    return server, observer, preview


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=CODE_ROOT,
                        help="Existing Hub data root; defaults to this checkout")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument('--owner-auth', choices=['environment', 'systemd-credential'],
                        default='environment', help='Explicit Owner proof provider; OS mode has no environment fallback')
    parser.add_argument('--rr-binding', type=Path,
                        help='Explicit protected RR installation binding; same-process Browser socket, no independent store')
    parser.add_argument('--rr-key-fd', type=int,
                        help='Inherited anonymous sealed credential FD number, never a key value')
    parser.add_argument('--official-metadata-binding', help='Explicit protected local metadata binding; no execution grant')
    parser.add_argument('--official-metadata-binding-sha256', help='Exact SHA256 of the protected metadata binding')
    parser.add_argument("--readonly-preview", choices=["computer-study-plan"],
                        help="Start the explicitly granted read-only development fixture")
    ordinary = parser.add_mutually_exclusive_group()
    ordinary.add_argument('--ordinary-readonly-grant', action='append', default=[],
                        help='Explicit existing synthetic ordinary grant ID; starts its owned read-only executor')
    ordinary.add_argument('--ordinary-real-readonly-grant', action='append', default=[],
                        help='Explicit existing real ordinary grant ID; starts up to two owned read-only tasks')
    args = parser.parse_args(argv)
    if args.rr_key_fd is not None and args.rr_binding is None:
        parser.error('RR credential FD requires the explicit binding')
    if args.ordinary_real_readonly_grant and args.readonly_preview:
        parser.error('Real ordinary execution cannot start a preview runtime')
    if (args.official_metadata_binding is None) != (args.official_metadata_binding_sha256 is None):
        parser.error('Official metadata binding requires both path and SHA256')
    server = None
    rr_key_fd = args.rr_key_fd
    rr_sigterm = None
    observer = None
    preview = None
    try:
        metadata_binding = (load_official_metadata_binding(args.official_metadata_binding,
            args.official_metadata_binding_sha256, server_root=args.root)
            if args.official_metadata_binding is not None else None)
        if args.rr_binding is not None:
            rr_sigterm = signal.getsignal(signal.SIGTERM)
            def stop_rr_process(signum, frame):
                raise KeyboardInterrupt()
            signal.signal(signal.SIGTERM, stop_rr_process)
        services = ({'owner_auth': OwnerAuth(provider=SystemdCredentialProvider())}
                    if args.owner_auth == 'systemd-credential' else None)
        if metadata_binding is not None:
            services = dict(services or {})
            services['session_view'] = SessionService(hub_root=args.root, official_metadata_profile=metadata_binding)
        server, observer, preview = assemble_server(args.root, host=args.host, port=args.port,
                                                    services=services)
        if args.rr_binding is not None:
            from hub.rr_runtime import attach_rr_gateway
            owned_fd, rr_key_fd = rr_key_fd, None
            attach_rr_gateway(server, code_root=CODE_ROOT, binding_path=args.rr_binding, key_fd=owned_fd)
        grant_ids = args.ordinary_real_readonly_grant or args.ordinary_readonly_grant
        if grant_ids:
            from hub.ordinary_runtime import OrdinaryRuntime
            from hub.ordinary_tasks import check_registration, validate_grant
            classification = 'real' if args.ordinary_real_readonly_grant else 'synthetic_fixture'
            if len(set(grant_ids)) != len(grant_ids):
                raise ServiceError('ORDINARY_RUNTIME_SCOPE_REJECTED')
            grants = [server.tasks.store.grant(ident) for ident in grant_ids]
            for grant in grants:
                if (type(grant) is not dict or grant.get('classification') != classification
                        or grant.get('kind') != 'ordinary_project' or grant.get('effect') != 'read_only'):
                    raise ServiceError('ORDINARY_RUNTIME_SCOPE_REJECTED')
                check_registration(grant, server.tasks.store.root)
                validate_grant(grant, live=True)
            runtime = OrdinaryRuntime(server.tasks.store, grants=grants,
                                      provider_status=ordinary_provider_status(),
                                      grant_classification=classification)
            attach_ordinary_runtime(server, runtime)
            runtime.start()
        if args.readonly_preview:
            if preview is None: raise ServiceError('REAL_PREVIEW_REGISTRATION_INVALID')
            preview.start()
        observer.start()
        print(f"Personal Control Hub: {server.origin}/", flush=True)
        print("Open this address in your browser. Ctrl-C stops the Hub.", flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        return 0
    except (ServiceError, RRFeedbackError, OSError):
        print("Hub local API could not start; check its local configuration.", file=sys.stderr)
        return 1
    finally:
        if rr_sigterm is not None:
            signal.signal(signal.SIGTERM, rr_sigterm)
        if rr_key_fd is not None:
            with contextlib.suppress(OSError): os.close(rr_key_fd)
        if observer is not None:
            observer.stop()
        if preview is not None:
            preview.stop()
        if server is not None:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
