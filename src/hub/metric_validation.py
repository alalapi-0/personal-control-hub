"""Collect existing validation reports without invoking their producers."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from hub.connection_records import RecordError, content_hash, require, timestamp
from hub.metric_git import GitObservationError, _run
from hub.metric_sources import read_json
from hub.metrics import issue, metric

STATUSES = ("PASS", "FAIL", "BLOCKED", "HUMAN_ONLY", "SKIPPED")
MAX_CHECKS = 10000


def _git_state(root):
    try:
        head = _run(root, "rev-parse", "--verify", "HEAD").decode().strip()
        clean = not _run(root, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
        return head, clean
    except (GitObservationError, UnicodeError):
        return None, None


def _records(data, format_name):
    if format_name == "feature_report":
        raw = data.get("feature_results")
        require(type(raw) is list and len(raw) <= MAX_CHECKS, "feature list missing or oversized")
        records = []
        for row in raw:
            require(type(row) is dict and type(row.get("id")) is str and
                    0 < len(row["id"]) <= 200, "invalid check identity")
            # Labels and details may contain user text, commands, or logs. Do not project them.
            records.append((content_hash(row["id"]), row.get("status")))
    elif format_name == "gate_arrays":
        records = []
        for key, status in (("passed", "PASS"), ("failed", "FAIL"),
                            ("blocked", "BLOCKED"), ("skipped", "SKIPPED")):
            values = data.get(key)
            require(type(values) is list and len(values) <= MAX_CHECKS, "gate array missing or oversized")
            for value in values:
                require(type(value) is str and 0 < len(value) <= 2000, "invalid gate identity")
                records.append((content_hash(value), status))
    else:
        raise RecordError("unsupported report format")
    require(len(records) <= MAX_CHECKS, "oversized check identities")
    return records


def collect_validation(root, project_id, observed_at, reports):
    rows, problems = [], []
    head, current_clean = _git_state(root) if any(r["format"] != "universal_player_guard_result_v1" for r in reports) else (None, None)
    for spec in reports:
        if spec["format"] == "universal_player_guard_result_v1":
            from hub.metric_guard_report import collect_guard_report
            group = collect_guard_report(root, project_id, observed_at, spec)
            rows.extend(group["metrics"])
            problems.extend(group["issues"])
            continue
        report_id, path = spec["id"], spec["path"]
        dimensions = {"report_id": report_id}
        source = "validation:" + report_id + ":" + path
        business_at, data, entries, records, reason = None, None, None, None, None
        try:
            report_root = Path(spec.get("root", root))
            require(report_root.resolve(strict=True) == report_root, "report root must be canonical")
            data, _ = read_json(report_root, path)
            require(type(data) is dict, "report must be an object")
        except (OSError, RecordError, ValueError):
            data = None
            reason = "Declared validation report unavailable or invalid."
            problems.append(issue(project_id, "validation_report_unavailable", source,
                                  recovery_condition="Restore the declared report; collection never runs checks."))
        if data is not None:
            try:
                candidate = data.get("generated_at", data.get("timestamp"))
                timestamp(candidate, "report business time")
                business_at = candidate
            except RecordError:
                problems.append(issue(project_id, "validation_time_unknown", source))
            try:
                entries = _records(data, spec["format"])
                require(len({key for key, _ in entries}) == len(entries), "duplicate check identities")
                records = entries
            except (RecordError, TypeError):
                reason = "Report check identities or structure are invalid."
                problems.append(issue(project_id, "validation_checks_invalid", source))
        data = data or {}
        commit = data.get("git_commit")
        commit = commit if type(commit) is str and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) else None
        env = data.get("environment", {})
        env = {k: env[k] for k in ("platform", "python")
               if type(env) is dict and type(env.get(k)) is str and len(env[k]) <= 300}
        report_tree = data.get("working_tree_status")
        report_clean = not report_tree if type(report_tree) is str else None
        provenance = [commit, env, report_clean]
        reference = source + "@code=" + (commit or "unknown") + ";environment=" + content_hash(env)

        def emit(mid, value, unit, basis, *, scope=None, selected=None, why=None):
            dims = dimensions | (scope or {})
            rows.append(metric(project_id, mid, value, unit, reference,
                content_hash([value, selected, provenance, business_at, why]), observed_at,
                dimensions=dims, business_at=business_at,
                reason=why if value is None else None, counting_basis=basis))

        known = records is not None
        emit("validation.entries.total", len(entries) if entries is not None else None, "entries",
             "Saved report entries before identity deduplication; entries are not independently identified checks",
             selected=sorted(key for key, _ in entries) if entries is not None else None, why=reason)
        emit("validation.entries.duplicate_identity_occurrences",
             len(entries) - len({key for key, _ in entries}) if entries is not None else None, "entries",
             "Repeated identity labels beyond their first occurrence; ambiguous identities invalidate unique check counts",
             selected=sorted(key for key, _ in entries) if entries is not None else None, why=reason)
        emit("validation.checks.total", len(records) if known else None, "checks",
             "Unique check identities in this saved report; not a current test execution",
             selected=sorted(key for key, _ in records) if known else None, why=reason)
        invalid_status = [(key, status) for key, status in records or [] if status not in STATUSES]
        for status in STATUSES:
            selected = sorted(key for key, value in records or [] if value == status)
            emit("validation.checks", len(selected) if known else None, "checks",
                 "Unique report checks with exactly the recorded status; human and blocked checks remain separate",
                 scope={"status": status}, selected=selected, why=reason)
        emit("validation.checks.invalid_status", len(invalid_status) if known else None, "checks",
             "Check identities whose saved status is outside the supported enum",
             selected=sorted(key for key, _ in invalid_status), why=reason)
        if invalid_status:
            problems.append(issue(project_id, "validation_status_invalid", source,
                                  affected_items=len(invalid_status)))
        for key, status in records or []:
            if status in ("FAIL", "BLOCKED", "HUMAN_ONLY"):
                problems.append(issue(project_id, "validation_" + status.lower(), source + "#check=" + key,
                    affected_items=1, updated_at=business_at,
                    kind="manual_decision" if status == "HUMAN_ONLY" else "business_blocker",
                    recovery_condition="Use the report producer or required reviewer; this collector does not retry it."))
        match = int(commit == head) if commit is not None and head is not None else None
        bound = None
        if match == 0 or report_clean is False or current_clean is False and match is not None:
            bound = 0
        elif match == 1 and report_clean is True and current_clean is True:
            bound = 1
        emit("validation.report.commit_matches_head", match, "boolean",
             "Recorded full Git commit equals current HEAD; this alone does not bind dirty working changes",
             selected=head, why="Report or current Git commit unknown.")
        emit("validation.report.bound_to_current_code", bound, "boolean",
             "Report names current HEAD and both report and current Git worktrees are clean; environment equality is not asserted",
             selected=[head, current_clean], why="A clean candidate binding is not recorded.")
        emit("validation.report.environment_recorded", int(len(env) == 2) if data else None, "boolean",
             "Saved report contains platform and Python version strings; not a current environment comparison",
             why="Report unavailable.")
        age = None
        if business_at:
            age = (datetime.fromisoformat(observed_at.replace("Z", "+00:00")) -
                   datetime.fromisoformat(business_at.replace("Z", "+00:00"))).total_seconds()
            if age < 0:
                age = None
        emit("validation.report.age", age, "seconds", "Collection time minus the report's timezone-qualified business time",
             why="Report time is unknown or later than collection time.")
        if bound != 1:
            problems.append(issue(project_id, "validation_candidate_binding_unverified", source,
                                  recovery_condition="Producer must record the tested candidate; saved success is historical evidence."))
    return {"metrics": rows, "issues": problems, "disposition": "partial" if problems else "resolved",
            "source_version": content_hash([r["source_version"] for r in rows])}


def collect_validation_runs(root, project_id, observed_at, spec):
    """A tool's real saved functional runs, using the shared report collector."""
    return collect_validation(root, project_id, observed_at, spec["validation_reports"])
