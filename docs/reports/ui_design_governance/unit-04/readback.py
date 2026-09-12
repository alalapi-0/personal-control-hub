"""Reproduce TC4 read-only real-Hub API evidence; stdout only, no fact writes."""
import hashlib
import http.client
import json
import sys
import threading
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
from hub import connection_manager_cli, connection_sources
from hub.design_service import DesignService
from hub.design_store import DesignStore
from hub.local_service import HubHTTPServer
from hub.project_service import ProjectService


def main():
    ledger = ROOT / "data/design_governance/connection_refresh.sqlite3"
    before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    blocked_reads = []
    read = connection_sources._safe_relative_read

    def hub_only(root, *args, **kwargs):
        if Path(root).absolute() != ROOT:
            blocked_reads.append("external-root-attempt")
            raise AssertionError("TC4 query attempted a non-Hub source read")
        return read(root, *args, **kwargs)

    designs = DesignService(DesignStore(ROOT, "data/design_governance/design-store.json"))
    server = HubHTTPServer(ProjectService(ROOT), designs)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    cookie = None

    def get(path):
        conn = http.client.HTTPConnection(*server.server_address, timeout=15)
        try:
            conn.request("GET", path, headers={"Cookie": cookie} if cookie else {})
            response = conn.getresponse()
            data = json.loads(response.read())
            assert response.status == 200 and data["ok"], (response.status, data)
            return data["data"], response.getheader("Set-Cookie")
        finally:
            conn.close()

    try:
        with mock.patch.object(connection_sources.SourceResolver, "refresh", side_effect=AssertionError("GET refreshed")), \
             mock.patch.object(connection_sources, "_safe_relative_read", side_effect=hub_only), \
             mock.patch.object(connection_manager_cli, "_safe_relative_read", side_effect=hub_only):
            # Deliberately never include session values or headers in evidence.
            _, raw_cookie = get("/api/session")
            cookie = raw_cookie.split(";", 1)[0]
            start = time.monotonic()
            listing, _ = get("/api/projects")
            list_ms = round((time.monotonic() - start) * 1000, 2)
            for row in listing["projects"]:
                detail, _ = get("/api/projects/" + row["project_id"])
                assert detail == row, row["project_id"]
            design, _ = get("/api/designs")
            empty, _ = get("/api/projects?q=tc4-no-such-project")
            assert empty["head"] == listing["head"] and empty["total"] == 0
            assert listing["total"] == 24 and not blocked_reads
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    after = hashlib.sha256(ledger.read_bytes()).hexdigest()
    assert before == after and not thread.is_alive()
    manga = next(row for row in listing["projects"] if row["project_id"] == "manga-localizer")
    assert manga["operational"]["latest_attempt"]["disposition"] == "BLOCKED_BY_AUTHORITY"
    print(json.dumps({
        "schema_version": "1.0", "kind": "TC4_real_read_only_api_evidence",
        "project_count": listing["total"], "list_detail_equal": True,
        "head": listing["head"], "list_latency_ms": list_ms,
        "ledger_sha256_before": before, "ledger_sha256_after": after,
        "design_available": design["available"], "design_revision": design["store_revision"],
        "external_source_reads": 0, "manga_probes": 0, "mutating_http_calls": 0,
        "new_real_decisions": 0, "background_server_remaining": False,
        "ui_implemented": False, "final_connections_accepted": False,
        "projects": listing["projects"],
    }, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
