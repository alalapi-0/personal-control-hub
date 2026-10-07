#!/usr/bin/env python3
"""Start the single-user Hub UI and API on literal loopback."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--readonly-preview", choices=["computer-study-plan"],
                        help="Start the explicitly granted read-only development fixture")
    args = parser.parse_args(argv)
    server = None
    observer = None
    preview = None
    try:
        # Read/command authorities stay in their accepted Hub-owned locations.
        # An absent design store remains unavailable; starting is never a write.
        designs = DesignService(DesignStore(ROOT, "data/design_governance/design-store.json"))
        observer = HostObserver({'hub': ROOT, 'temp': ROOT.parents[1] / 'Temp'})
        projects = ProjectService(ROOT)
        previews = PreviewService(projects, designs, WorkbenchStore(ROOT))
        if (ROOT / MANIFEST).exists():
            preview = ReadOnlyPreview(ROOT, projects, designs)
            previews.readonly_preview = preview
        if args.readonly_preview:
            if preview is None: raise ServiceError('REAL_PREVIEW_REGISTRATION_INVALID')
            preview.start()
        tasks = TaskService(projects, previews, TaskStore(ROOT))
        server = HubHTTPServer(projects, designs, host=args.host, port=args.port,
                               host_observer=observer, previews=previews, tasks=tasks,
                               materials=MaterialService(projects, designs), session_view=SessionService())
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
