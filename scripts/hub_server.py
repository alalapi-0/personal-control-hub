#!/usr/bin/env python3
"""Start the Hub application API locally; no project app or UI is launched."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hub.design_service import DesignService
from hub.design_store import DesignStore
from hub.local_service import HubHTTPServer
from hub.project_service import ProjectService
from hub.service_contract import ServiceError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    server = None
    try:
        # Read/command authorities stay in their accepted Hub-owned locations.
        # An absent design store remains unavailable; starting is never a write.
        designs = DesignService(DesignStore(ROOT, "data/design_governance/design-store.json"))
        server = HubHTTPServer(ProjectService(ROOT), designs, host=args.host, port=args.port)
        print(f"Hub local API: {server.origin}/api/health", flush=True)
        print("UI awaits the owner's Figma selection. Ctrl-C stops this API.", flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        return 0
    except (ServiceError, OSError):
        print("Hub local API could not start; check its local configuration.", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
