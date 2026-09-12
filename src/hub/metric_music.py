"""Registered music project/run metadata, without opening audio or running providers."""
from datetime import datetime
import os
from pathlib import Path
import re

from hub.connection_records import require, timestamp
from hub.connection_sources import _root_path_allowed
from hub.metric_documents import Projection
from hub.metric_sources import metadata_path, read_structured
from hub.metrics import issue

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
STATES = ("pending", "running", "succeeded", "failed", "cancelled")
MAX_PROJECTS, MAX_RUNS, MAX_BYTES = 500, 5000, 32 * 1024 * 1024


def _ids(value, paths=False):
    pattern = r"[A-Za-z0-9][A-Za-z0-9._/-]{0,300}" if paths else ID.pattern
    return (isinstance(value, list) and len(value) <= MAX_RUNS
            and all(isinstance(v, str) and re.fullmatch(pattern, v)
                    and ".." not in Path(v).parts for v in value)
            and len(value) == len(set(value)))


def collect_music(root, pid, observed_at, spec):
    base = Path(spec.get("data_root", root / "workspace/projects"))
    p = Projection(base, pid, observed_at, "music")
    consumed = files = 0

    def read(relative):
        nonlocal consumed, files
        try:
            path = metadata_path(base, relative)
            consumed += path.stat().st_size
            files += 1
            require(consumed <= MAX_BYTES and files <= MAX_RUNS + MAX_PROJECTS, "metadata budget")
            obj, _ = read_structured(base, relative)
            require(isinstance(obj, dict), "metadata object")
            return obj
        except (OSError, ValueError, TypeError):
            p.problem(str(base / relative))
            return {}

    def emit(name, value, unit, relative, **kwargs):
        p.emit(name, value, unit, str(base / relative), **kwargs)

    try:
        require(not base.is_symlink(), "symlink root")
        _root_path_allowed(base)
        folder = base.resolve(strict=True)
        _root_path_allowed(folder)
        names = []
        with os.scandir(folder) as entries:
            for index, entry in enumerate(entries):
                require(index < MAX_RUNS, "directory budget")
                require(not entry.is_symlink(), "symlink catalog entry")
                if entry.is_dir(follow_symlinks=False):
                    require(bool(ID.fullmatch(entry.name)), "project folder identity")
                    names.append(entry.name)
                    require(len(names) <= MAX_PROJECTS, "project budget")
        names.sort()
    except (OSError, ValueError, TypeError):
        names = None
        p.problem(str(base))
    complete = names is not None
    verified = []
    for project in names or []:
        ref = project + "/project.yaml"
        obj = read(ref)
        valid = obj.get("schema_version") == "1.0.0" and obj.get("project_id") == project
        complete = complete and valid
        p.dimensions = {"music_project_id": project, "scope": "registered_metadata"}
        if not valid:
            p.problem(str(base / ref) + "#identity")
        else:
            verified.append(project)
        at = obj.get("updated_at") if valid else None
        for name, key, unit in (("versions_registered", "versions", "versions"),
                                ("assets_registered", "assets", "assets"),
                                ("exports_registered", "exports", "exports"),
                                ("runs_registered", "runs", "runs")):
            values = obj.get(key)
            ok = valid and _ids(values, paths=key == "exports")
            emit(name, len(values) if ok else None, unit, ref + "#" + key,
                 semantic=sorted(values) if ok else None, business_at=at,
                 basis="Unique manifest references only; no generated-output, approval or real-provider inference.")
        registered = obj.get("runs")
        known = valid and _ids(registered)
        runs = []
        for run_id in registered if known else []:
            if files >= MAX_RUNS + MAX_PROJECTS:
                known = False
                p.problem(str(base) + "#file_budget")
                break
            run_ref = project + "/runs/" + run_id + ".json"
            run = read(run_ref)
            bound = (run.get("schema_version") == "1.0.0" and run.get("run_id") == run_id
                     and run.get("project_id") in (None, project))
            if not bound:
                known = False
                p.problem(str(base / run_ref) + "#identity")
                continue
            state = run.get("status")
            if state not in STATES:
                known = False
                p.problem(str(base / run_ref) + "#status")
            runs.append((run_id, run))
        for state in STATES:
            selected = sorted(rid for rid, r in runs if r.get("status") == state)
            emit("runs_" + state, len(selected) if known else None, "runs", ref + "#runs",
                 semantic=selected, business_at=at,
                 basis="Current native status of every unique registered run; includes offline demonstrations.")
        for rid, run in runs:
            ref_run = project + "/runs/" + rid + ".json"
            provider = run.get("provider_name")
            provider = provider if isinstance(provider, str) and ID.fullmatch(provider) else "unknown"
            dims = {"run_id": rid, "provider": provider}
            state = run.get("status")
            finished = run.get("completed_at")
            valid_time = True
            for field in ("started_at", "completed_at"):
                v = run.get(field)
                if field == "completed_at" and v is None:
                    continue
                try:
                    timestamp(v, "run time")
                except (ValueError, TypeError, AttributeError):
                    valid_time = False
            duration = None
            if valid_time and finished and run.get("started_at"):
                duration = (datetime.fromisoformat(finished.replace("Z", "+00:00")) -
                            datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))).total_seconds()
                if duration < 0:
                    duration = None
            emit("run_duration_seconds", duration, "seconds", ref_run + "#started_at,completed_at",
                 dims=dims, business_at=finished if valid_time else None,
                 basis="Recorded completion minus start; null if incomplete or timestamp ordering is invalid.")
            emit("run_succeeded", int(state == "succeeded") if state in STATES else None,
                 "runs", ref_run + "#status", dims=dims, semantic=[state, run.get("provider_version")],
                 business_at=finished if valid_time else None,
                 basis="Native run outcome for this provider; offline_vertical_slice is an offline demonstration.")
            if state == "failed":
                p.problems.append(issue(pid, "music_run_failed", str(base / ref_run), kind="business_blocker",
                    affected_items=1, started_at=finished if valid_time else None,
                    recovery_condition="Inspect this registered failed run in the project; collection never retries or generates."))
        emit("reviews_pending", None, "reviews", ref + "#review_binding",
             reason="The project manifest does not establish current candidate-bound review coverage.")
    p.dimensions = {"scope": "registered_metadata"}
    emit("projects_registered", len(verified) if complete else None, "projects", ".",
         semantic=verified, basis="Project folders with matching schema and project_id; missing or invalid catalog identities make the total unknown.")
    emit("catalog_complete", int(complete), "boolean", ".", semantic=verified,
         basis="Whether the bounded selected project catalog was read and all project identities validated.")
    return p.finish()
