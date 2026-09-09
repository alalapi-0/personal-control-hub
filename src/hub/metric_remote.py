"""Opt-in bounded GitHub metadata observations; never fetch or change Git refs.

Injected clients are callables accepting a GitHub REST endpoint and returning JSON.
Only an explicit owner/repository binding is accepted; Git remote URLs are unused.
"""
from __future__ import annotations

import json
import os
import re
import selectors
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from hub.metric_git import _run
from hub.metrics import content_hash, issue, metric

BINDING_VERSION = "github-readonly-v1"
MAX_PAGES = 3
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 15
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_CATEGORIES = ("total", "success", "failure", "pending", "cancelled", "other")
_GIT_IDS = ("remote_git.main.contains_head", "remote_git.main.ahead", "remote_git.main.behind", "remote_git.observed_timestamp")
_CI_IDS = tuple("github_ci.runs." + key for key in _CATEGORIES) + ("github_ci.complete", "github_ci.observed_timestamp")


class RemoteError(Exception):
    """Contains only an internal fixed code, never provider stderr or payload."""


def _api(endpoint):
    try:
        proc = subprocess.Popen(["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                env=dict(os.environ, GH_PROMPT_DISABLED="1"))
    except OSError:
        raise RemoteError("github_client_unavailable") from None
    chunks, size, deadline = [], 0, time.monotonic() + TIMEOUT_SECONDS
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteError("github_timeout")
                for key, _ in selector.select(min(remaining, .1)):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    size += len(data)
                    if size > MAX_OUTPUT_BYTES:
                        raise RemoteError("github_output_limit")
                    chunks.append(data)
            try:
                code = proc.wait(timeout=max(.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise RemoteError("github_timeout") from None
            if code:
                raise RemoteError("github_request_failed")
        try:
            return json.loads(b"".join(chunks))
        except (ValueError, UnicodeError):
            raise RemoteError("github_invalid_json") from None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()


def _sha(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise RemoteError("github_invalid_schema")
    return value


def _count(value):
    if type(value) is not int or value < 0:
        raise RemoteError("github_invalid_schema")
    return value


def _stamp(value):
    if not isinstance(value, str):
        raise RemoteError("github_invalid_schema")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.isoformat()
    except ValueError:
        raise RemoteError("github_invalid_schema") from None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _head(root):
    return _sha(_run(Path(root), "rev-parse", "--verify", "HEAD").decode("ascii").strip())


def collect_remote(root, pid, observed_at, spec, *, remote_git=False, github_ci=False, client=None):
    metrics, issues = [], []
    if not remote_git and not github_ci:
        return dict(metrics=[], issues=[], disposition="disabled", source_version=content_hash([BINDING_VERSION, "disabled"]))
    request = client or _api
    repo = spec.get("github_repository") if isinstance(spec, dict) else None
    groups = [("remote_git", _GIT_IDS)] if remote_git else []
    if github_ci:
        groups.append(("github_ci", _CI_IDS))
    head = None

    def emit(mid, value, evidence, *, business_at=None, reason=None):
        dimensions = {"provider": "github", "target_role": "local_head"}
        if mid.endswith(".ahead"):
            dimensions["direction"] = "local_head_ahead_of_remote_primary"
        if mid.endswith(".behind"):
            dimensions["direction"] = "local_head_behind_remote_primary"
        basis = ("Exact local HEAD versus GitHub actual default branch; ancestor comparison" if mid.startswith("remote_git") else
                 "Latest attempt per workflow run ID for exact local HEAD; all runs, not required checks")
        unit = "unix_seconds" if mid.endswith("timestamp") else "boolean" if mid.endswith(("contains_head", "complete")) else "commits" if mid.startswith("remote_git") else "runs"
        metrics.append(metric(pid, mid, value, unit,
            "github:" + (repo if isinstance(repo, str) and _REPO.fullmatch(repo) else "unbound") + ("@" + head if head else ""),
            content_hash([BINDING_VERSION, evidence, mid, value, reason]), observed_at,
            business_at=business_at, dimensions=dimensions, reason=reason, counting_basis=basis))

    def fail(group, ids, code):
        issues.append(issue(pid, code, "github:" + group, updated_at=observed_at,
                            recovery_condition="Restore explicit GitHub binding or read access and recollect", retry_entry="collect_remote"))
        for mid in ids:
            emit(mid, None, [head, code], reason=code)

    def finish():
        return dict(metrics=metrics, issues=issues, disposition="partial" if issues else "observed",
                    source_version=content_hash([m["source_version"] for m in metrics]))

    if not isinstance(repo, str) or not _REPO.fullmatch(repo) or repo.split("/")[-1] in (".", "..") or "--" in repo.split("/")[0]:
        for group, ids in groups:
            fail(group, ids, "github_binding_invalid")
        return finish()
    try:
        head = _head(root)
    except Exception:
        for group, ids in groups:
            fail(group, ids, "local_head_unavailable")
        return finish()

    for group, ids in groups:
        try:
            if group == "remote_git":
                metadata = request(f"repos/{repo}")
                branch = metadata["default_branch"]
                if not isinstance(branch, str) or not branch or len(branch) > 255 or any(ord(c) < 32 for c in branch):
                    raise RemoteError("github_invalid_schema")
                primary = _sha(request(f"repos/{repo}/branches/{quote(branch, safe='')}")["commit"]["sha"])
                compared = request(f"repos/{repo}/compare/{head}...{primary}")
                if _sha(compared["base_commit"]["sha"]) != head:
                    raise RemoteError("github_target_mismatch")
                merge_base = _sha(compared["merge_base_commit"]["sha"])
                remote_ahead, remote_behind = _count(compared["ahead_by"]), _count(compared["behind_by"])
                expected = "diverged" if remote_ahead and remote_behind else "ahead" if remote_ahead else "behind" if remote_behind else "identical"
                if compared["status"] != expected or (remote_behind == 0) != (merge_base == head):
                    raise RemoteError("github_invalid_schema")
                stamp = _now()
                evidence = [repo, head, branch, primary, merge_base, remote_ahead, remote_behind]
                for mid, value in zip(ids, (int(remote_behind == 0), remote_behind, remote_ahead, datetime.fromisoformat(stamp).timestamp())):
                    emit(mid, value, evidence, business_at=stamp if mid.endswith("observed_timestamp") else None)
            else:
                runs, total = {}, None
                for page in range(1, MAX_PAGES + 1):
                    payload = request(f"repos/{repo}/actions/runs?head_sha={head}&per_page=100&page={page}")
                    count = _count(payload["total_count"])
                    if total is not None and total != count:
                        raise RemoteError("github_ci_changed_during_read")
                    total = count
                    batch = payload["workflow_runs"]
                    if type(batch) is not list or len(batch) > 100:
                        raise RemoteError("github_invalid_schema")
                    for raw in batch:
                        selected = {k: raw[k] for k in ("id", "workflow_id", "run_attempt", "head_sha")}
                        for key in ("id", "workflow_id", "run_attempt"):
                            if _count(selected[key]) == 0:
                                raise RemoteError("github_invalid_schema")
                        if _sha(selected["head_sha"]) != head:
                            raise RemoteError("github_target_mismatch")
                        status, conclusion = raw.get("status"), raw.get("conclusion")
                        valid_status = (type(status) is str and status in {"queued", "in_progress", "completed", "waiting", "requested", "pending"}
                            and (conclusion is None or type(conclusion) is str and conclusion in {"success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required", "stale", "startup_failure"})
                            and (status == "completed") == (conclusion is not None))
                        selected["status"] = status if valid_status else None
                        selected["conclusion"] = conclusion if valid_status else None
                        selected["invalid_times"] = []
                        for key in ("created_at", "updated_at", "run_started_at"):
                            selected[key] = None
                            if raw.get(key) is not None:
                                try:
                                    selected[key] = _stamp(raw[key])
                                except RemoteError:
                                    selected["invalid_times"].append(key)
                            elif key != "run_started_at":
                                selected["invalid_times"].append(key)
                        old = runs.get(selected["id"])
                        if old is None or selected["run_attempt"] > old["run_attempt"]:
                            runs[selected["id"]] = selected
                        elif selected["run_attempt"] == old["run_attempt"] and selected != old:
                            raise RemoteError("github_ci_changed_during_read")
                    if len(runs) >= total or len(batch) < 100:
                        break
                complete = len(runs) == total
                if len(runs) > total:
                    raise RemoteError("github_invalid_schema")
                counts = dict.fromkeys(_CATEGORIES, 0)
                counts["total"] = len(runs)
                statuses_valid = all(r["status"] is not None for r in runs.values())
                invalid_times = sorted({key for r in runs.values() for key in r["invalid_times"]})
                for run in runs.values():
                    status, conclusion = run["status"], run["conclusion"]
                    if status is None:
                        continue
                    category = "pending" if status != "completed" else "success" if conclusion == "success" else "failure" if conclusion in {"failure", "timed_out", "startup_failure"} else "cancelled" if conclusion == "cancelled" else "other"
                    counts[category] += 1
                stamp = _now()
                ordered = sorted(runs.values(), key=lambda r: r["id"])
                identity = [repo, head, total, complete, [{k: r[k] for k in ("id", "workflow_id", "run_attempt", "head_sha")} for r in ordered]]
                outcomes = [identity, [{k: r[k] for k in ("id", "status", "conclusion")} for r in ordered]]
                business_at = (max((r["updated_at"] for r in runs.values()), key=lambda s: datetime.fromisoformat(s), default=None)
                               if "updated_at" not in invalid_times else None)
                for key in _CATEGORIES:
                    known = complete and (key == "total" or statuses_valid)
                    emit("github_ci.runs." + key, counts[key] if known else None,
                         identity if key == "total" else outcomes, business_at=business_at,
                         reason=None if known else "github_ci_incomplete" if not complete else "github_ci_status_unknown")
                emit("github_ci.complete", int(complete), identity)
                emit("github_ci.observed_timestamp", datetime.fromisoformat(stamp).timestamp(), identity, business_at=stamp)
                if not statuses_valid:
                    issues.append(issue(pid, "github_ci_status_unknown", "github:github_ci", updated_at=observed_at,
                                        affected_items=["github_ci.runs." + k for k in _CATEGORIES if k != "total"],
                                        recovery_condition="Obtain recognized run status and conclusion metadata", retry_entry="collect_remote"))
                if invalid_times:
                    issues.append(issue(pid, "github_ci_time_unknown", "github:github_ci", updated_at=observed_at,
                                        affected_items=invalid_times, recovery_condition="Obtain valid optional run timestamps", retry_entry="collect_remote"))
                if not complete:
                    issues.append(issue(pid, "github_ci_incomplete", "github:github_ci", updated_at=observed_at,
                                        recovery_condition="Obtain a complete bounded run listing", retry_entry="collect_remote"))
        except Exception as exc:
            safe_codes = {"github_client_unavailable", "github_timeout", "github_output_limit", "github_request_failed",
                          "github_invalid_json", "github_invalid_schema", "github_target_mismatch", "github_ci_changed_during_read"}
            code = str(exc) if isinstance(exc, RemoteError) and str(exc) in safe_codes else "github_timeout" if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else "github_observation_failed"
            fail(group, ids, code)
    try:
        stable = _head(root) == head
    except Exception:
        stable = False
    if not stable:
        metrics.clear()
        for group, ids in groups:
            fail(group, ids, "local_head_changed")
    return finish()
