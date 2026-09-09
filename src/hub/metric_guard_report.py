"""Numeric saved guard runs, bound to the producer's explicit source-file set.

Only report metadata is projected. Neither collection nor report discovery runs
the guard, reads media, or promotes these checks to whole-product acceptance.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from hub.connection_records import RecordError, content_hash, require, timestamp
from hub.metric_documents import Projection
from hub.metric_sources import metadata_path, read_json, read_metadata
from hub.metrics import issue

FORMAT = "universal_player_guard_result_v1"
CODE_PATHS = (
    "Package.swift", "Config/ExternalVLCKitPackage.swift",
    "Config/ExternalVLCKitTreeManifest.json", "Config/Base.xcconfig",
    "Scripts/storage_governance.py", "Scripts/test_storage_governance.py",
    "Scripts/media_contract.py", "Scripts/external-build",
    "Scripts/test-external-build-guard.sh",
    "Apps/UniversalPlayerIOS/UniversalPlayerIOSApp.swift",
    "Apps/UniversalPlayerMac/UniversalPlayerMacApp.swift",
)
HEX = re.compile(r"[a-f0-9]{64}\Z")
MAX_DISCOVERY = 4096


def _signature(path):
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, "ordinary report/source required")
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def _latest(root, path, suite):
    """Discover only the producer's two known layouts; newest write wins.

    mtime selects a saved file; it never stands in for the run's business time.
    A newer corrupt report is not skipped in favor of an old success.
    """
    require(suite in {"core", "vlckit", "media", "raw"}, "guard suite")
    root = Path(root)
    require(root.resolve(strict=True) == root, "canonical report root required")
    if "*" not in path:
        selected = metadata_path(root, path)
        return path, _signature(selected)
    local = "results/" + suite + "-*.json"
    require(path in {local, "universal-player-guard-*/" + local}, "unsupported guard discovery pattern")
    scanned, folders = 0, [""]
    if path != local:
        folders = []
        with os.scandir(root) as entries:
            for entry in entries:
                scanned += 1
                require(scanned <= MAX_DISCOVERY, "guard discovery budget")
                if re.fullmatch(r"universal-player-guard-[a-f0-9]{32}", entry.name):
                    require(entry.is_dir(follow_symlinks=False), "guard fixture is not an ordinary directory")
                    folders.append(entry.name)
    candidates = []
    for folder in sorted(folders):
        relative = str(Path(folder) / "results")
        directory = metadata_path(root, relative)
        if not directory.exists():
            continue
        require(directory.is_dir(), "guard results directory")
        with os.scandir(directory) as entries:
            for entry in entries:
                scanned += 1
                require(scanned <= MAX_DISCOVERY, "guard discovery budget")
                if re.fullmatch(suite + r"-[a-f0-9]{32}\.json", entry.name):
                    report_path = str(Path(relative) / entry.name)
                    signature = _signature(metadata_path(root, report_path))
                    candidates.append((signature[3], report_path, signature))
                    require(len(candidates) <= 256, "guard report count budget")
    require(bool(candidates), "no saved guard report")
    _, selected, signature = max(candidates)
    return selected, signature


def _current_files(root):
    paths = {name: metadata_path(root, name) for name in CODE_PATHS}
    before = {name: _signature(path) for name, path in paths.items()}
    require(sum(s[2] for s in before.values()) <= 32 * 1024 * 1024, "source byte budget")
    hashes = {}
    for name in CODE_PATHS:
        require(before[name][2] <= 4 * 1024 * 1024, "source file byte budget")
        _, metadata = read_metadata(root, name)
        hashes[name] = metadata["sha256"]
    after = {name: _signature(metadata_path(root, name)) for name in paths}
    require(before == after, "source set changed during collection")
    return hashes


def collect_guard_report(root, pid, observed_at, spec):
    base = Path(spec.get("root", root))
    p = Projection(root, pid, observed_at, "validation.guard")
    suite = spec.get("suite", "core")
    p.dimensions = {"report_id": spec["id"], "suite": suite,
                    "scope": "saved_guard_run", "binding_scope": "selected_source_files"}
    relative, data, business_at = spec["path"], {}, None
    try:
        relative, selection = _latest(base, relative, suite)
        data, _ = read_json(base, relative)
        require(_latest(base, spec["path"], suite) == (relative, selection), "report selection changed")
        require(type(data) is dict and data.get("schema_version") == FORMAT
                and data.get("suite") == suite, "guard report identity")
    except (OSError, RecordError, ValueError, TypeError):
        data = {}
        p.problem(str(base / spec["path"]) + "#report_identity_or_selection")
    source = str(base / relative)
    try:
        timestamp(data.get("finished_at"), "guard finished time")
        business_at = data["finished_at"]
    except (RecordError, TypeError, AttributeError):
        p.problem(source + "#finished_at")
    summary = data.get("summary", {})
    summary = summary if type(summary) is dict else {}
    status = data.get("status")
    known_status = status in ("PASS", "FAIL")
    if data and not known_status:
        p.problem(source + "#status")
    entries = data.get("results")
    entry_count = len(entries) if type(entries) is list and len(entries) <= 10000 else None
    observed = summary.get("observed_cases")
    observed = observed if type(observed) is int and observed >= 0 and observed == entry_count else None
    complete = summary.get("complete")
    complete = int(complete) if type(complete) is bool and known_status and complete == (status == "PASS") else None
    failed = summary.get("failed_suites")
    failed = failed if type(failed) is int and known_status and failed == int(status == "FAIL") else None
    code = data.get("code", {})
    code = code if type(code) is dict else {}
    files = code.get("files")
    bound, current, code_valid = None, None, False
    try:
        require(type(files) is dict and set(files) == set(CODE_PATHS)
                and all(type(v) is str and HEX.fullmatch(v) for v in files.values()), "guard source set")
        aggregate = hashlib.sha256((json.dumps(files, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()
        require(code.get("sha256") == aggregate and type(code.get("stable")) is bool, "guard source fingerprint")
        code_valid = True
        if code["stable"]:
            current = _current_files(root)
            bound = int(current == files)
        else:
            bound = 0
    except (OSError, RecordError, ValueError, TypeError):
        p.problem(source + "#code_binding")
    env = data.get("environment", {})
    env = {k: v for k, v in env.items() if k in {"python", "system", "machine", "guard_sha256"}
           and type(v) is str and re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", v)} if type(env) is dict else {}
    if "guard_sha256" in env and not HEX.fullmatch(env["guard_sha256"]):
        env.pop("guard_sha256")
    provenance = [FORMAT, suite, code.get("sha256") if code_valid else None,
                  files if code_valid else None, code.get("stable") if code_valid else None, env]

    def emit(name, value, unit, basis, semantic=None):
        p.emit(name, value, unit, source, semantic=[provenance, semantic],
               business_at=business_at, basis=basis)

    emit("entries_observed", entry_count, "result_entries", "Saved completed result entries; not unique tests or assertions. A failure may precede any recorded entry.")
    emit("cases_reported", observed, "result_entries", "Producer summary only when its count equals the saved results array length; not whole-product progress.")
    emit("run_complete", complete, "boolean", "Saved summary completion agrees with PASS/FAIL; not current code acceptance.")
    emit("failed_suites", failed, "suites", "This one saved suite run failed (1) or passed (0); incomplete record counts are not a passed denominator.")
    emit("run_passed", int(status == "PASS") if known_status else None, "boolean", "Recorded run status, independent of current code; historical success is preserved when code changes.")
    emit("bound_to_selected_code", bound, "boolean", "All 11 producer-defined source hashes match stable current files and the aggregate verifies; no whole-HEAD or environment-equality claim.", current)
    emit("selected_code_files", len(files) if code_valid else None, "files", "Explicit source files in this verified report fingerprint, not completed project features.")
    current_passed = int(status == "PASS") if bound == 1 and known_status and complete is not None and failed is not None and observed is not None else None
    emit("current_selected_code_passed", current_passed, "boolean", "Saved run outcome applies to the 11 selected current files only; GUI, raw Xcode, private history and full product acceptance are outside scope.", current)
    emit("environment_recorded", int(set(env) == {"python", "system", "machine", "guard_sha256"}) if data else None,
         "boolean", "Report records producer environment fields; equality with the collecting environment is not asserted.")
    duration = age = None
    if business_at:
        finish = datetime.fromisoformat(business_at.replace("Z", "+00:00"))
        age = (datetime.fromisoformat(observed_at.replace("Z", "+00:00")) - finish).total_seconds()
        age = age if age >= 0 else None
        try:
            timestamp(data.get("started_at"), "guard start time")
            duration = (finish - datetime.fromisoformat(data["started_at"].replace("Z", "+00:00"))).total_seconds()
            duration = duration if duration >= 0 else None
        except (RecordError, TypeError, AttributeError):
            pass
    emit("duration", duration, "seconds", "Recorded finished_at minus started_at; null for missing or reversed times.")
    emit("age", age, "seconds", "Collection time minus recorded finished_at; filesystem mtime only selects the newest saved report.")
    if bound != 1:
        p.problems.append(issue(pid, "validation_candidate_binding_unverified", source,
            recovery_condition="Run the normal registered producer against the selected current files; collection never executes it."))
    if status == "FAIL":
        p.problems.append(issue(pid, "validation_guard_failed", source, kind="business_blocker", affected_items=1,
            updated_at=business_at, recovery_condition="Inspect this failed suite in the project; collection does not retry it."))
    return p.finish()
