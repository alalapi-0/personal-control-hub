"""Novel metadata facts; never opens source text, segment bodies or review bodies."""
from pathlib import Path
import re

from hub.connection_records import content_hash, timestamp
from hub.metric_sources import read_json
from hub.metrics import issue, metric

CHAPTERS = "workspace/manifests/chapter_manifest.json"
EXPORT = "output_cn/final_export_manifest.json"
STAGE = "workspace/stage_state_production.json"


def collect_novel(root: Path, project_id: str, observed_at: str) -> dict:
    rows, problems = [], []

    def problem(code, ref):
        problems.append(issue(project_id, code, ref,
            recovery_condition="Repair or refresh the authoritative metadata, then recollect."))

    def read(path):
        try:
            data, _ = read_json(root, path)
            if not isinstance(data, dict):
                raise ValueError("object required")
            return data
        except (OSError, ValueError, TypeError):
            problem("novel_source_unavailable", path)
            return {}

    def business(data, key, path):
        value = data.get(key)
        if value is None:
            return None
        try:
            timestamp(value, key)
            return value
        except (ValueError, TypeError, AttributeError):
            problem("novel_invalid_business_time", path + "#" + key)
            return None

    def emit(name, value, unit, path, selector, basis, at=None, dims=None,
             reason=None, semantic=None):
        if value is None:
            reason = reason or "Required authoritative metadata field is missing or invalid."
            problem("novel_unknown_" + name, path + "#" + selector)
        rows.append(metric(project_id, "novel." + name, value, unit,
            path + "#" + selector, content_hash([selector, value, semantic, reason]),
            observed_at, business_at=at, dimensions=dims, reason=reason,
            counting_basis=basis))

    def integer(value):
        return value if type(value) is int and value >= 0 else None

    chapters = read(CHAPTERS)
    at = business(chapters, "generated_at", CHAPTERS)
    supported = chapters.get("schema_version") == 1
    emit("chapters_expected", integer(chapters.get("total_expected_chapters")) if supported else None,
         "chapters", CHAPTERS, "total_expected_chapters", "Declared whole-book expected chapters.", at)
    entries = chapters.get("chapters")
    valid = supported and isinstance(entries, dict) and all(
        isinstance(v, dict) and re.fullmatch(r"ch-\d+", k) and v.get("chapter_id") == k
        for k, v in entries.items())
    emit("chapters_indexed", len(entries) if valid else None, "chapters", CHAPTERS,
         "chapters.keys", "Unique canonical chapter_id keys; runs are not added together.", at,
         semantic=sorted(entries) if valid else None)
    for state in ("complete", "partial", "missing"):
        # Unknown vocabulary invalidates this breakdown, not the chapter denominator.
        states = {k: v.get("draft_status") for k, v in entries.items()} if valid else {}
        statuses_valid = valid and all(v in {"complete", "partial", "missing"} for v in states.values())
        ids = sorted(k for k, v in states.items() if v == state)
        emit("chapters_draft_" + state, len(ids) if statuses_valid else None, "chapters",
             CHAPTERS, "chapters.*.draft_status=" + state,
             "Unique chapters whose canonical draft_status equals " + state + "; not formal approval.",
             at, semantic=ids)

    exported = read(EXPORT)
    at = business(exported, "generated_at", EXPORT)
    supported = exported.get("schema") == "consistency_final_export_v2"
    for name in ("chapters_discovered", "chapters_exported", "chapters_incomplete", "chapters_missing"):
        raw = exported.get(name)
        value = integer(raw) if name in {"chapters_discovered", "chapters_exported"} else (
            len(raw) if isinstance(raw, list) else None)
        emit("export_" + name, value if supported else None, "chapters", EXPORT, name,
             "Export manifest " + name + "; export existence does not prove formal approval.", at)

    stage = read(STAGE)
    run_id = stage.get("run_id")
    safe_run = isinstance(run_id, str) and bool(re.fullmatch(r"run_[A-Za-z0-9_-]{1,180}", run_id))
    path = "workspace/runs/" + run_id + "/run_progress.json" if safe_run else STAGE
    progress = read(path) if safe_run else {}
    at = business(progress, "updated_at", path)
    dims = {"scope": "default_production_batch"}
    if safe_run:
        dims["run_id"] = run_id
    identity = safe_run and progress.get("schema_version") == 1 and progress.get("run_id") == run_id
    values = {k: integer(progress.get(k)) if identity else None for k in
              ("total_segments", "completed_segments", "pending_segments")}
    conflict = all(v is not None for v in values.values()) and (
        values["completed_segments"] + values["pending_segments"] != values["total_segments"])
    if conflict:
        problem("novel_batch_count_conflict", path)
    for key, value in values.items():
        emit("batch_" + key, None if conflict else value, "segments", path, key,
             "Segments in the single default production run; never whole-book coverage.", at, dims,
             reason="Batch counts contradict total_segments." if conflict else None)
    status = progress.get("status")
    known = identity and status in {"pending", "in_progress", "completed", "failed", "aborted", "blocked"}
    emit("batch_failed", int(status == "failed") if known else None, "runs", path, "status=failed",
         "One default production run whose progress status equals failed.", at, dims)
    if known and stage.get("status") != status:
        problem("novel_stage_progress_conflict", STAGE + "#status")
    emit("review_pending", None, "segments", "workspace/review_state.json", "projects",
         "Real projects only; formal review requires current content identity and test/history exclusion.",
         reason="No verified body-free projection of current formal review identities is available.")
    return {"metrics": rows, "issues": problems,
            "disposition": "partial" if problems else "resolved",
            "source_version": content_hash([(r["metric_id"], r["source_version"]) for r in rows])}
