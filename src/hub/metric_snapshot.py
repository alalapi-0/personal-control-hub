"""Strict project-metric snapshot contract without project or ledger I/O."""
from __future__ import annotations

import json

from hub.connection_records import (
    RecordError,
    content_hash,
    exact,
    fingerprint,
    identifier,
    require,
    timestamp,
)
from hub.metrics import metric_catalog, metric_key, validate_metric


SNAPSHOT_SCHEMA_VERSION = "1.0"
SNAPSHOT_KIND = "project_metric_snapshot"
SNAPSHOT_RELATIVE_PATH = ".hub/status.json"
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_METRICS = 10_000
MAX_SNAPSHOT_ISSUES = 1_000
SNAPSHOT_DISPOSITIONS = {
    "resolved",
    "partial",
    "observed",
    "disabled",
    "no_git",
    "unavailable",
}
SNAPSHOT_FIELDS = {
    "schema_version",
    "kind",
    "project_id",
    "observed_at",
    "exporter",
    "disposition",
    "metrics",
    "metric_definitions",
    "issues",
    "source_versions",
    "snapshot_id",
}
EXPORTER_FIELDS = {"id", "version"}
ISSUE_FIELDS = {
    "issue_id",
    "project_id",
    "code",
    "source_ref",
    "kind",
    "affected_items",
    "started_at",
    "updated_at",
    "recovery_condition",
    "retry_entry",
}


def metric_snapshot_contract_schema():
    """Machine-readable index for the one project-to-Hub snapshot shape."""
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "kind": SNAPSHOT_KIND,
        "validator": "hub.metric_snapshot.validate_metric_snapshot",
        "builder": "hub.metric_snapshot.make_metric_snapshot",
        "writer": "hub.metric_export.export_metric_snapshot",
        "reader": "hub.metric_import.read_metric_snapshot",
        "importer": "hub.metric_import.import_metric_snapshot",
        "relative_path": SNAPSHOT_RELATIVE_PATH,
        "snapshot_fields": sorted(SNAPSHOT_FIELDS),
        "exporter_fields": sorted(EXPORTER_FIELDS),
        "issue_fields": sorted(ISSUE_FIELDS),
        "dispositions": sorted(SNAPSHOT_DISPOSITIONS),
        "max_bytes": MAX_SNAPSHOT_BYTES,
        "max_metrics": MAX_SNAPSHOT_METRICS,
        "max_issues": MAX_SNAPSHOT_ISSUES,
        "boundary": "exporter reads project sources; importer reads only .hub/status.json",
    }


def make_metric_snapshot(
    project_id,
    observed_at,
    *,
    exporter_id,
    exporter_version,
    disposition,
    metrics,
    issues,
    source_versions,
):
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "kind": SNAPSHOT_KIND,
        "project_id": project_id,
        "observed_at": observed_at,
        "exporter": {"id": exporter_id, "version": exporter_version},
        "disposition": disposition,
        "metrics": metrics,
        "metric_definitions": metric_catalog(metrics),
        "issues": issues,
        "source_versions": source_versions,
    }
    payload["snapshot_id"] = content_hash(payload)
    return validate_metric_snapshot(payload)


def validate_metric_snapshot(value):
    exact(value, SNAPSHOT_FIELDS, "metric snapshot")
    require(
        value["schema_version"] == SNAPSHOT_SCHEMA_VERSION
        and value["kind"] == SNAPSHOT_KIND,
        "unsupported metric snapshot",
    )
    identifier(value["project_id"], "snapshot project")
    timestamp(value["observed_at"], "snapshot observation")
    exact(value["exporter"], EXPORTER_FIELDS, "snapshot exporter")
    identifier(value["exporter"]["id"], "snapshot exporter")
    identifier(value["exporter"]["version"], "snapshot exporter version")
    require(
        type(value["disposition"]) is str
        and value["disposition"] in SNAPSHOT_DISPOSITIONS,
        "invalid snapshot disposition",
    )

    rows = value["metrics"]
    require(
        type(rows) is list and len(rows) <= MAX_SNAPSHOT_METRICS,
        "invalid snapshot metric collection",
    )
    keys = []
    for row in rows:
        validate_metric(row)
        require(
            row["project_id"] == value["project_id"]
            and row["observed_at"] == value["observed_at"],
            "snapshot metric binding mismatch",
        )
        keys.append(metric_key(row))
    require(len(keys) == len(set(keys)), "duplicate snapshot metric identities")
    require(
        value["metric_definitions"] == metric_catalog(rows),
        "snapshot metric catalog does not match facts",
    )

    versions = value["source_versions"]
    require(
        type(versions) is dict and len(versions) <= 32,
        "invalid snapshot source versions",
    )
    for group, version in versions.items():
        identifier(group, "snapshot source group")
        require(
            type(version) is str and 0 < len(version) <= 1000,
            "invalid snapshot source version",
        )

    issues = value["issues"]
    require(
        type(issues) is list and len(issues) <= MAX_SNAPSHOT_ISSUES,
        "invalid snapshot issues",
    )
    for item in issues:
        _validate_issue(item, value["project_id"])

    expected_id = content_hash(
        {key: item for key, item in value.items() if key != "snapshot_id"}
    )
    fingerprint(value["snapshot_id"], "metric snapshot identity")
    require(value["snapshot_id"] == expected_id, "metric snapshot identity mismatch")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise RecordError("metric snapshot is not canonical JSON") from exc
    require(len(encoded) <= MAX_SNAPSHOT_BYTES, "metric snapshot exceeds byte limit")
    return value


def _validate_issue(item, project_id):
    exact(item, ISSUE_FIELDS, "snapshot issue")
    fingerprint(item["issue_id"], "snapshot issue identity")
    require(item["project_id"] == project_id, "snapshot issue project mismatch")
    identifier(item["project_id"], "snapshot issue project")
    identifier(item["code"], "snapshot issue code")
    identifier(item["kind"], "snapshot issue kind")
    require(
        type(item["source_ref"]) is str and 0 < len(item["source_ref"]) <= 1000,
        "invalid snapshot issue source",
    )
    require(
        item["issue_id"]
        == content_hash([item["project_id"], item["code"], item["source_ref"]]),
        "snapshot issue identity mismatch",
    )
    affected = item["affected_items"]
    require(
        affected is None
        or type(affected) is int
        and affected >= 0
        or type(affected) is list
        and len(affected) <= 1000
        and all(type(entry) is str and 0 < len(entry) <= 200 for entry in affected),
        "invalid snapshot affected items",
    )
    for field in ("started_at", "updated_at"):
        if item[field] is not None:
            timestamp(item[field], "snapshot issue " + field)
    for field in ("recovery_condition", "retry_entry"):
        require(
            item[field] is None
            or type(item[field]) is str
            and 0 < len(item[field]) <= 1000,
            "invalid snapshot issue " + field,
        )
