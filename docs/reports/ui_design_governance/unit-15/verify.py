"""Read-only canonical persistence, transport and protected-preimage verification."""
from pathlib import Path
import hashlib
import http.cookiejar
import json
import signal
import subprocess
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[4]
UNIT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from hub.design_store import DesignStore
from hub.design_service import DesignService
from hub.project_service import ProjectService


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = json.loads((UNIT / "baseline.json").read_text())
    changes = [name for name, value in baseline["protected"].items()
               if not (ROOT / name).is_file() or digest(ROOT / name) != value]
    assert not changes, changes
    imported = json.loads((UNIT / "import.json").read_text())
    store_path = ROOT / "data/design_governance/design-store.json"
    before = digest(store_path)
    snapshot = DesignService(DesignStore(ROOT, str(store_path))).snapshot()
    assert snapshot["store_revision"] == imported["store_revision"] == 8
    assert len(snapshot["facts"]) == 6 and len(snapshot["history"]) == 1
    assert snapshot["history"][0]["event"]["id"] == "owner-hub-c-p5-selection"
    projects = ProjectService(ROOT).list_projects(design_snapshot=snapshot)
    assert projects["total"] == 24
    process = subprocess.Popen([sys.executable, "scripts/hub_server.py", "--port", "0"],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline().strip()
        assert line.startswith("Personal Control Hub: http://127.0.0.1:"), line
        origin = line.split(": ", 1)[1].rstrip("/")
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def get(path):
            with opener.open(origin + path, timeout=10) as response:
                return response.read(), response.headers
        get("/api/session")  # Runtime cookie/CSRF response is never logged or persisted.
        remote = json.loads(get("/api/designs")[0])["data"]
        assert remote == snapshot
        remote_projects = json.loads(get("/api/projects")[0])["data"]
        assert remote_projects == projects
        page, headers = get("/")
        assert b"/assets/hub.js" in page
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        command = imported["export_command"]
        ref = command["candidate"]
        query = urllib.parse.urlencode({"candidate_id": ref["id"], "candidate_revision": ref["revision"],
                                        "candidate_hash": ref["content_hash"], "store_revision": command["expected_revision"]})
        payload, export_headers = get("/api/exports/" + command["request_id"] + "?" + query)
        assert hashlib.sha256(payload).hexdigest() == imported["export"]["sha256"]
        assert "sandbox" in export_headers["Content-Security-Policy"]
    finally:
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=10)
    assert process.returncode == 0
    assert digest(store_path) == before
    result = {"status": "PASS", "protected_files": len(baseline["protected"]), "protected_changed": [],
              "canonical_store_sha256": before, "canonical_store_revision": 8, "facts": 6, "owner_events": 1,
              "cli_service_http_agree": True, "project_count": projects["total"], "ledger_head": projects["head"],
              "restart_process_readback": True, "graceful_stop_exit": process.returncode,
              "exact_export_download_sha256": imported["export"]["sha256"], "canonical_mutations": 0,
              "source_reads": "accepted Hub-local projections only"}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
