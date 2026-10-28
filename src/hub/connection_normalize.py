"""Normalize declared source facts against the exact parsed source."""
from hub.connection_records import empty_business, put_field, require
from hub.connections import select_value


def normalize_source(parsed, declaration, source, metadata, observed_at, *, owned_hub_state=False):
    business = empty_business()
    provenance = {}
    unknown = dict(declaration["unknown_fields"])
    for field, rule in declaration["mapping"].items():
        raw = select_value(parsed, rule["selector"])
        if raw is None:
            continue
        value = raw
        if rule["value_map"] is not None:
            require(type(raw) is str and raw in rule["value_map"], "unmapped source enum")
            value = rule["value_map"][raw]
        put_field(business, field, value)
        provenance[field] = {"source_ref": source["id"], "sha256": metadata["sha256"],
                             "selector": rule["selector"], "value_map": rule["value_map"], "raw_value": raw}
        del unknown[field]
    normalized = {"business": business, "field_provenance": provenance, "unknown_fields": unknown}
    if "extensions" in declaration:
        from .connection_extensions import validate_extensions
        config = declaration["extensions"]
        envelope = {"schema_version": config["schema_version"],
                    "fact": {}, "managed": {}, "derived": {}, "proposed": {}}
        for field, rule in config["fact_mapping"].items():
            raw = select_value(parsed, rule["selector"])
            envelope["fact"][field] = {
                "value": raw, "unknown_reason": config["unknown_fields"][field] if raw is None else None,
                "proof": None if raw is None else {"source_ref": source["id"],
                    "selector": rule["selector"], "sha256": metadata["sha256"],
                    "observed_at": observed_at}}
            if field == "task_plan" and raw is not None and owned_hub_state:
                from .task_view_projection import validate_refactor_task_view
                validate_refactor_task_view(parsed["pch_refactor"], raw)
        normalized["extensions"] = validate_extensions(envelope, source_context={source["id"]: {
            "sha256": metadata["sha256"], "observed_at": observed_at,
            "format": source["format"], "data": parsed}})
    return normalized
