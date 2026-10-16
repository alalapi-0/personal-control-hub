#!/usr/bin/env python3
"""Start the single-user Hub UI and API on literal loopback."""
import argparse
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
from hub.material_service import MaterialService
from hub.session_service import SessionService
from hub.readonly_preview import ReadOnlyPreview, MANIFEST


def assemble_server(root, *, host='127.0.0.1', port=8766, services=None):
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
               'materials', 'session_view', 'owner_auth'}
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
    server = HubHTTPServer(projects, designs, host=host, port=port, **options)
    return server, observer, preview


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=CODE_ROOT,
                        help="Existing Hub data root; defaults to this checkout")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--readonly-preview", choices=["computer-study-plan"],
                        help="Start the explicitly granted read-only development fixture")
    args = parser.parse_args(argv)
    server = None
    observer = None
    preview = None
    try:
        server, observer, preview = assemble_server(args.root, host=args.host, port=args.port)
        if args.readonly_preview:
            if preview is None: raise ServiceError('REAL_PREVIEW_REGISTRATION_INVALID')
            preview.start()
        observer.start()
        print(f"Personal Control Hub: {server.origin}/", flush=True)
        print("Open this address in your browser. Ctrl-C stops the Hub.", flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        return 0
    except (ServiceError, OSError):
        print("Hub local API could not start; check its local configuration.", file=sys.stderr)
        return 1
    finally:
        if observer is not None:
            observer.stop()
        if preview is not None:
            preview.stop()
        if server is not None:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
