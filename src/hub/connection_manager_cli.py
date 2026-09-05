"""CLI for versioned source plans, durable refreshes and relation proposals."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .connection_records import RecordError, content_hash, require
from .connection_refresh import RefreshLedger, RefreshLedgerError, refresh
from .connection_relations import project_relations, validate_relations
from .connection_sources import (
    ACCEPTED_INVENTORY_CANDIDATE, ACCEPTED_INVENTORY_SHA256, INVENTORY_PATH,
    SourceResolver, _safe_relative_read,
)
from .connections import load_registry_at

DEFAULT_BUNDLE = "data/design_governance/authority-bundle-v1.json"
DEFAULT_BUNDLES = [
    DEFAULT_BUNDLE,
    "data/design_governance/authority-bundle-v2.json",
]
DEFAULT_RELATIONS = "data/design_governance/relation-proposals-v2.json"
ADAPTER_PATH = "data/design_governance/connection_adapters.json"
BUNDLE_FIELDS = {"schema_version", "kind", "manifest", "adapters", "source_plan", "content_hash"}


def load_json(root: Path, path: str) -> tuple[dict, str]:
    """Read one exact Hub JSON file; command paths never expand external roots."""
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            path = candidate.relative_to(root).as_posix()
        except ValueError as exc:
            raise RecordError("JSON input must be inside the Hub root") from exc
    raw = _safe_relative_read(root, path, suffixes={".json"})
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise RecordError("invalid Hub JSON input") from exc
    require(isinstance(value, dict), "Hub JSON input must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def validate_bundle(bundle: Any) -> SourceResolver:
    require(isinstance(bundle, dict) and set(bundle) == BUNDLE_FIELDS, "invalid authority bundle fields")
    require(bundle["schema_version"] == "1.0" and bundle["kind"] == "connection_authority_bundle",
            "unsupported authority bundle schema")
    require(bundle["content_hash"] == content_hash({k: v for k, v in bundle.items() if k != "content_hash"}),
            "authority bundle hash mismatch")
    return SourceResolver.from_frozen(bundle["manifest"], bundle["adapters"], bundle["source_plan"])


def load_bundles(root: Path, paths: list[str]) -> tuple[list[dict], dict[str, SourceResolver]]:
    bundles, validators = [], {}
    for path in paths:
        bundle, _ = load_json(root, path)
        validator = validate_bundle(bundle)
        identity = validator.authority["source_plan_hash"]
        require(identity not in validators, "duplicate frozen source authority")
        validators[identity] = validator
        bundles.append(bundle)
    return bundles, validators


def result_validator(validators: dict[str, SourceResolver]):
    def validate(result: dict) -> dict:
        require(isinstance(result, dict) and isinstance(result.get("authority"), dict), "result authority missing")
        identity = result["authority"].get("source_plan_hash")
        require(isinstance(identity, str) and identity in validators, "historical authority bundle is required")
        return validators[identity].validate_result(result)
    return validate


def load_relations(root: Path, path: str = DEFAULT_RELATIONS) -> dict:
    registry, registry_hash = load_registry_at(root)
    inventory, inventory_hash = load_json(root, INVENTORY_PATH)
    require(inventory_hash == ACCEPTED_INVENTORY_SHA256, "accepted relation inventory has drifted")
    records, _ = load_json(root, path)
    ids = {p["id"] for p in registry["projects"]}
    validate_relations(records, project_ids=ids, registry_hash=registry_hash, inventory=inventory,
                       inventory_ref={"path": INVENTORY_PATH, "sha256": ACCEPTED_INVENTORY_SHA256,
                                      "accepted_candidate_hash": ACCEPTED_INVENTORY_CANDIDATE})
    return {"schema_version": "1.0", "collection": records, "projects": project_relations(records, ids)}


def current_authority_status(root: Path, bundle: dict) -> dict:
    """Only Hub metadata is consulted; historical facts remain independently readable."""
    expected = bundle["source_plan"]["authority"]
    try:
        _, registry_hash = load_registry_at(root)
        adapters, _ = load_json(root, ADAPTER_PATH)
        _, inventory_hash = load_json(root, expected["accepted_inventory_path"])
        _, discovery_hash = load_json(root, expected["discovery_path"])
        changed = [name for name, actual in {
            "registry_hash": registry_hash, "adapter_hash": content_hash(adapters),
            "accepted_inventory_hash": inventory_hash, "discovery_hash": discovery_hash,
        }.items() if actual != expected[name]]
        return {"state": "drifted" if changed else "matched", "changed_fields": changed}
    except (OSError, RecordError, ValueError):
        return {"state": "unavailable", "changed_fields": [], "reason": "Current Hub authority could not be verified."}


def run(args: argparse.Namespace, root: Path) -> tuple[dict, int]:
    if args.command == "relations":
        return load_relations(root, args.relations), 0
    bundles, validators = load_bundles(root, args.bundle or DEFAULT_BUNDLES)
    validate = result_validator(validators)
    active = bundles[-1]
    authority = validators[active["source_plan"]["content_hash"]].authority
    if args.command == "validate":
        statuses = [current_authority_status(root, bundle) for bundle in bundles]
        relations = load_relations(root, args.relations)
        return {"schema_version": "1.0", "bundles": len(bundles),
                "projects": len(active["manifest"]["entries"]), "current_authorities": statuses,
                "relation_proposals": len(relations["collection"]["relations"])}, (
                    0 if statuses[-1]["state"] == "matched" else 2)
    if args.command == "refresh":
        require(current_authority_status(root, active)["state"] == "matched", "current authority drift prevents refresh")
        adapters, _ = load_json(root, ADAPTER_PATH)
        resolver = SourceResolver(root, active["manifest"], adapters, active["source_plan"])
        ids = args.project or [entry["project_id"] for entry in active["manifest"]["entries"]]
        require(len(ids) == len(set(ids)) and set(ids) <= set(resolver.entries), "invalid requested project set")
        expected = None
        require((args.expected_sequence is None) == (args.expected_hash is None), "both expected head fields are required")
        if args.expected_sequence is not None:
            expected = {"sequence": args.expected_sequence, "hash": args.expected_hash}
        ledger = RefreshLedger(root, args.ledger, result_validator=validate)
        result = refresh(ledger, resolver, args.request_id, ids, expected_head=expected)
        failures = [project["latest_attempt"] for project in result["projection"]["projects"].values()
                    if project["latest_attempt"]["request_id"] == args.request_id and not project["latest_attempt"]["success"]]
        return result, 2 if result["resolver_errors"] or failures else 0
    ledger = RefreshLedger(root, args.ledger, result_validator=validate, read_only=True)
    if args.command == "history":
        result = ledger.history(args.request_id, current_authority=authority)
        for item in result["results"]:
            validate(item["result"])
    else:
        result = ledger.rebuild(current_authority=authority)
    result["current_authority"] = current_authority_status(root, active)
    if args.command == "rebuild" and result["current_authority"]["state"] != "matched":
        for project in result["projects"].values():
            project["authority_drift"] = True
            if project["freshness"] == "fresh":
                project["freshness"] = "stale"
                project["stale_reason"] = "current Hub source authority could not be verified"
    return result, 0


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", action="append", help="Exact Hub authority bundle; repeat for historical versions")
    parser.add_argument("--ledger", help="Hub-local refresh database path")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("validate")
    check.add_argument("--relations", default=DEFAULT_RELATIONS)
    relation = sub.add_parser("relations")
    relation.add_argument("--relations", default=DEFAULT_RELATIONS)
    attempt = sub.add_parser("refresh")
    attempt.add_argument("--request-id", required=True)
    attempt.add_argument("--project", action="append")
    attempt.add_argument("--expected-sequence", type=int)
    attempt.add_argument("--expected-hash")
    history = sub.add_parser("history")
    history.add_argument("--request-id")
    sub.add_parser("rebuild")
    args = parser.parse_args(argv)
    try:
        result, code = run(args, root or Path(__file__).resolve().parents[2])
    except (RecordError, RefreshLedgerError, OSError, ValueError) as exc:
        result, code = {"error": getattr(exc, "code", "INPUT_INVALID"), "message": str(exc)}, 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return code
