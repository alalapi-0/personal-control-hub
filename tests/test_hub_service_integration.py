from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import test_hub_sources as source_fixtures
import test_hub_local_service as transport_fixtures
from hub.connection_records import content_hash
from hub.connection_refresh import GENESIS_HASH
from hub.connection_relations import relation_hash
from hub.connection_sources import SourceResolver, freeze_source_plan
from hub.connections import freeze_manifest
from hub.design_cli import _artifact, _baseline, _candidate, FIXTURE_TIME
from hub.design_service import DesignService
from hub.design_store import DesignStore
from hub.local_service import HubHTTPServer
from hub.project_service import ProjectService


class LocalServiceIntegrationTests(unittest.TestCase):
    """Real libraries and HTTP lifecycle, entirely inside a temporary Hub."""
    call = transport_fixtures.LocalHTTPTests.call
    session = transport_fixtures.LocalHTTPTests.session
    stop = transport_fixtures.LocalHTTPTests.stop

    def setUp(self):
        self.source_fixture = source_fixtures.SourceResolverTests()
        self.source_fixture.setUp()
        self.addCleanup(self.source_fixture.tearDown)
        f = self.source_fixture
        self.root = f.root
        # One synthetic design project is also a registered source project. Its
        # external-root stand-in remains under this temporary directory only.
        registry = copy.deepcopy(f.registry)
        next(p for p in registry["projects"] if p["id"] == "declared-00")["id"] = "fixture-project"
        adapters = copy.deepcopy(f.adapters)
        adapters["projects"]["fixture-project"] = adapters["projects"].pop("declared-00")
        (self.root / "data/registry/external_projects.yaml").write_text(yaml.safe_dump(registry, sort_keys=False))
        data = self.root / "data/design_governance"
        (data / "connection_adapters.json").write_text(json.dumps(adapters))
        manifest = freeze_manifest(self.root, revision=2)
        plan = freeze_source_plan(manifest, registry, manifest["registry_ref"]["sha256"],
                                  adapters, f.discovery, f.discovery_hash,
                                  created_at="2026-09-05T05:00:00+00:00")
        bundle = {"schema_version": "1.0", "kind": "connection_authority_bundle",
                  "manifest": manifest, "adapters": adapters, "source_plan": plan}
        bundle["content_hash"] = content_hash(bundle)
        (data / "authority-bundle-v1.json").write_text(json.dumps(bundle))
        relations = {"schema_version": "1.0", "kind": "connection_relation_proposals",
                     "id": "fixture-http-relations", "revision": 1, "created_at": FIXTURE_TIME,
                     "registry_ref": {key: manifest["registry_ref"][key] for key in ("path", "sha256")},
                     "inventory_ref": {"path": "docs/reports/ui_design_governance/unit-02/ui-source-inventory.json",
                                       "sha256": "5e7132ce05a5dc2a569826444ea15f67e9893c725c9ec24c884872bd93917f7c",
                                       "accepted_candidate_hash": "f0d2f820f5b3bed541cee64b617f4e23fe6b0342d1b2db1541368df31c573512"},
                     "relations": []}
        relations["content_hash"] = relation_hash(relations)
        (data / "relation-proposals-v1.json").write_text(json.dumps(relations))
        self.material = self.root / "docs/reports/ui_design_governance/unit-04/integration"
        self.material.mkdir(parents=True)
        artifacts = []
        for name in ("baseline", "candidate"):
            path = self.material / (name + ".txt")
            payload = ("Synthetic " + name + " material\n").encode()
            path.write_bytes(payload)
            artifacts.append(_artifact("fixture-" + name + "-material", str(path.relative_to(self.root)),
                                       hashlib.sha256(payload).hexdigest()))
        self.baseline = _baseline(1, artifacts[0])
        self.candidate = _candidate(1, self.baseline, artifacts[1])
        self.store = DesignStore(self.root, self.material / "fixture-store.json", fixture=True)
        self.store.initialize()
        for i, fact in enumerate([*artifacts, self.baseline, self.candidate]):
            self.store.append_fact(fact, expected_revision=self.store.read()["revision"],
                                   request_id=f"fixture-fact-{i}")
        self.start()
        self.addCleanup(self.stop)

    def start(self):
        self.projects = ProjectService(self.root)
        self.designs = DesignService(DesignStore(self.root, self.store.path, fixture=True))
        self.server = HubHTTPServer(self.projects, self.designs)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.cookie, self.csrf = None, None

    def command(self, request_id="fixture-choice"):
        return {"request_id": request_id, "event_id": request_id, "created_at": FIXTURE_TIME,
                "expected_revision": self.store.read()["revision"], "action": "select",
                "candidate": {key: self.candidate[key] for key in ("id", "revision", "content_hash")},
                "scope": self.candidate["scope"], "feedback": "Synthetic owner action", "supersedes": None}

    def test_committed_response_failure_restart_retry_history_original_and_export(self):
        self.session()
        command = self.command()
        original_decide = self.designs.decide
        def committed_but_bad_response(*args, **kwargs):
            result = original_decide(*args, **kwargs)
            return {**result, "invalid_response": object()}
        with mock.patch.object(self.designs, "decide", side_effect=committed_but_bad_response):
            status, _, failure = self.call("POST", "/api/designs/decisions", command)
        self.assertEqual(status, 500)
        self.assertEqual(failure["error"]["outcome"], "UNKNOWN")
        committed = self.store.read()
        self.assertEqual(len(committed["events"]), 1)
        store_digest = hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        old_cookie = self.cookie
        self.stop()
        self.start()
        status, _, _ = self.call(headers={"Cookie": old_cookie})
        self.assertEqual(status, 401)
        self.session()
        status, _, retried = self.call("POST", "/api/designs/decisions", command)
        self.assertEqual(status, 200)
        self.assertEqual(retried["data"]["outcome"], "COMMITTED")
        self.assertEqual(hashlib.sha256(self.store.path.read_bytes()).hexdigest(), store_digest)
        status, _, history = self.call(path="/api/designs")
        self.assertEqual(status, 200)
        self.assertEqual(history["data"]["history"][0]["event"]["source"]["type"], "synthetic_fixture")
        self.assertEqual(self.store.projection()["real_selection_count"], 0)

        for artifact_id in ("fixture-baseline-material", "fixture-candidate-material"):
            status, headers, payload = self.call(path=f"/api/artifacts/{artifact_id}?candidate_id=fixture-candidate&candidate_revision=1")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/octet-stream")
            self.assertTrue(payload.startswith(b"Synthetic"))

        export = {"request_id": "fixture-export", "expected_revision": committed["revision"],
                  "candidate": command["candidate"]}
        status, _, result = self.call("POST", "/api/designs/exports", export)
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["outcome"], "COMMITTED")
        candidate_hash = self.candidate["content_hash"]
        url = (f"/api/exports/fixture-export?candidate_id=fixture-candidate&candidate_revision=1"
               f"&candidate_hash={candidate_hash}&store_revision={committed['revision']}")
        status, headers, data = self.call(path=url)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/zip")
        self.assertEqual(hashlib.sha256(data).hexdigest(), result["data"]["sha256"])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertFalse(manifest["authority"]["implementation_authority"])
        self.assertFalse(manifest["authority"]["real_selection"])
        self.assertEqual(manifest["candidate_identity"]["content_hash"], candidate_hash)

        baseline2 = _baseline(2)
        self.store.append_fact(baseline2, expected_revision=committed["revision"], request_id="fixture-new-baseline")
        status, _, after = self.call(path="/api/designs")
        self.assertEqual(status, 200)
        self.assertTrue(after["data"]["history"][0]["stale"])
        status, _, _ = self.call(path="/api/artifacts/fixture-baseline-material?candidate_id=fixture-candidate&candidate_revision=1")
        self.assertEqual(status, 200)
        status, _, old_export = self.call(path=url)
        self.assertEqual(status, 200)
        self.assertEqual(old_export, data)
        new_action = self.command("fixture-stale-choice")
        new_action["supersedes"] = command["event_id"]
        status, _, rejected = self.call("POST", "/api/designs/decisions", new_action)
        self.assertEqual((status, rejected["error"]["code"]), (409, "BASELINE_STALE"))

    def test_project_gets_are_offline_parity_and_refresh_retry_is_durable(self):
        self.session()
        status, _, before = self.call()
        self.assertEqual(status, 200)
        self.assertEqual(before["data"]["total"], 24)
        self.assertFalse((self.root / "data/design_governance/connection_refresh.sqlite3").exists())
        command = {"request_id": "fixture-refresh", "project_ids": ["fixture-project", "manga-localizer"],
                   "expected_head": {"sequence": 0, "hash": GENESIS_HASH}}
        status, _, result = self.call("POST", "/api/refresh", command)
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["request"]["status"], "FINISHED")
        ledger = self.root / "data/design_governance/connection_refresh.sqlite3"
        ledger_digest = hashlib.sha256(ledger.read_bytes()).hexdigest()
        with mock.patch.object(SourceResolver, "refresh", side_effect=AssertionError("GET/retry must remain offline")):
            status, _, listing = self.call()
            self.assertEqual(status, 200)
            row = next(item for item in listing["data"]["projects"] if item["project_id"] == "fixture-project")
            status, _, detail = self.call(path="/api/projects/fixture-project")
            self.assertEqual(status, 200)
            self.assertEqual(row, detail["data"])
            self.assertEqual(row["business"]["normalized_status"], "active")
            self.assertTrue(any(f["id"] == "fixture-candidate" for f in row["design"]["references"]))
            status, _, retried = self.call("POST", "/api/refresh", command)
            self.assertEqual(status, 200)
            self.assertEqual(retried["data"]["appended_project_ids"], [])
        self.assertEqual(hashlib.sha256(ledger.read_bytes()).hexdigest(), ledger_digest)
        blocked = next(item for item in listing["data"]["projects"] if item["project_id"] == "manga-localizer")
        self.assertEqual(blocked["business"]["normalized_status"], "unknown")
        self.assertEqual(blocked["operational"]["latest_attempt"]["disposition"], "BLOCKED_BY_AUTHORITY")


if __name__ == "__main__":
    unittest.main()
