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
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from hub.metric_git import _run
from hub.metrics import content_hash, issue, metric

BINDING_VERSION = "github-readonly-v2"
MAX_PAGES = 3
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 15
DEFAULT_CACHE_TTL_SECONDS = 60
DEFAULT_BACKOFF_SECONDS = 60
MAX_CACHE_TTL_SECONDS = 3600
MAX_BACKOFF_SECONDS = 3600
MAX_CACHE_ENTRIES = 256
_REPO = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_CATEGORIES = ("total", "success", "failure", "pending", "cancelled", "other")
_GIT_IDS = ("remote_git.main.contains_head", "remote_git.main.ahead", "remote_git.main.behind", "remote_git.observed_timestamp")
_CI_RESULT_IDS = tuple("github_ci.runs." + key for key in _CATEGORIES)
_CI_COVERAGE_IDS = (
    "github_ci.runs.reported_total",
    "github_ci.runs.observed",
    "github_ci.pages.observed",
    "github_ci.complete",
    "github_ci.observed_timestamp",
)
_CI_IDS = _CI_RESULT_IDS + _CI_COVERAGE_IDS
_SAFE_RESPONSE_HEADERS = {
    "cache-control", "etag", "link", "retry-after",
    "x-ratelimit-remaining", "x-ratelimit-reset",
}
_HTTP_STATUS = re.compile(rb"(?m)^HTTP/[^\s]+ ([0-9]{3})[^\r\n]*\r?$")
_MAX_AGE = re.compile(r"(?:^|,)\s*max-age=(\d+)(?:\s*(?:,|$))", re.I)


class RemoteError(Exception):
    """Contains only an internal fixed code, never provider stderr or payload."""


@dataclass(frozen=True)
class ApiResponse:
    status: int
    headers: dict
    payload: object


@dataclass
class _CacheEntry:
    payload: object
    etag: str | None
    expires_at: float


def valid_repository_binding(value):
    return (isinstance(value, str) and bool(_REPO.fullmatch(value))
            and value.split("/")[-1] not in (".", "..")
            and "--" not in value.split("/")[0])


def _parse_response(data):
    matches = list(_HTTP_STATUS.finditer(data))
    if not matches:
        raise RemoteError("github_invalid_response")
    match = matches[-1]
    newline = data.find(b"\n", match.end())
    if newline < 0:
        raise RemoteError("github_invalid_response")
    separator = data.find(b"\r\n\r\n", newline + 1)
    separator_size = 4
    if separator < 0:
        separator = data.find(b"\n\n", newline + 1)
        separator_size = 2
    if separator < 0:
        raise RemoteError("github_invalid_response")
    headers = {}
    for line in data[newline + 1:separator].replace(b"\r\n", b"\n").split(b"\n"):
        if not line:
            continue
        key, marker, value = line.partition(b":")
        if not marker:
            raise RemoteError("github_invalid_response")
        try:
            name = key.decode("ascii").strip().lower()
            text = value.decode("ascii").strip()
        except UnicodeError:
            raise RemoteError("github_invalid_response") from None
        if name in _SAFE_RESPONSE_HEADERS:
            if name in headers or len(text) > 1000 or any(ord(char) < 32 or ord(char) == 127 for char in text):
                raise RemoteError("github_invalid_response")
            headers[name] = text
    status = int(match.group(1))
    body = data[separator + separator_size:].strip()
    if status == 304:
        if body:
            raise RemoteError("github_invalid_response")
        payload = None
    else:
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            raise RemoteError("github_invalid_json") from None
    return ApiResponse(status, headers, payload)


def _api(endpoint, request_headers=None):
    if (not isinstance(endpoint, str) or not endpoint.startswith("repos/")
            or len(endpoint) > 1000 or any(ord(char) < 33 or ord(char) > 126 for char in endpoint)):
        raise RemoteError("github_invalid_endpoint")
    request_headers = request_headers or {}
    if set(request_headers) - {"If-None-Match"}:
        raise RemoteError("github_invalid_header")
    argv = ["gh", "api", "--hostname", "github.com", "--method", "GET", "--include"]
    etag = request_headers.get("If-None-Match")
    if etag is not None:
        if (not isinstance(etag, str) or not etag or len(etag) > 512
                or any(ord(char) < 32 or ord(char) == 127 for char in etag)):
            raise RemoteError("github_invalid_header")
        argv.extend(["--header", "If-None-Match: " + etag])
    argv.append(endpoint)
    try:
        proc = subprocess.Popen(argv,
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
        data = b"".join(chunks)
        response = _parse_response(data)
        if code and response.status < 400:
            raise RemoteError("github_request_failed")
        return response
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()


class GitHubSharedClient:
    """Bounded process-local request cache with conditional ETag revalidation."""

    def __init__(self, transport=None, *, monotonic=time.monotonic, wallclock=time.time,
                 default_ttl=DEFAULT_CACHE_TTL_SECONDS):
        if (type(default_ttl) not in (int, float) or default_ttl < 0
                or default_ttl > MAX_CACHE_TTL_SECONDS):
            raise ValueError("invalid GitHub cache lifetime")
        self.transport = transport or _api
        self.monotonic = monotonic
        self.wallclock = wallclock
        self.default_ttl = float(default_ttl)
        self.cache = {}
        self.blocked_until = 0.0

    @staticmethod
    def _ttl(headers, default):
        match = _MAX_AGE.search(headers.get("cache-control", ""))
        return min(float(match.group(1)), MAX_CACHE_TTL_SECONDS) if match else default

    def _backoff(self, headers, now):
        delay = float(DEFAULT_BACKOFF_SECONDS)
        retry = headers.get("retry-after")
        reset = headers.get("x-ratelimit-reset")
        if retry is not None and retry.isdigit():
            delay = float(retry)
        elif reset is not None and reset.isdigit():
            delay = max(float(DEFAULT_BACKOFF_SECONDS), float(reset) - self.wallclock())
        self.blocked_until = max(self.blocked_until, now + min(delay, MAX_BACKOFF_SECONDS))

    def __call__(self, endpoint):
        if (not isinstance(endpoint, str) or not endpoint.startswith("repos/")
                or len(endpoint) > 1000 or any(ord(char) < 33 or ord(char) > 126 for char in endpoint)):
            raise RemoteError("github_invalid_endpoint")
        now = self.monotonic()
        entry = self.cache.get(endpoint)
        if entry is not None and entry.expires_at > now:
            return deepcopy(entry.payload)
        if now < self.blocked_until:
            raise RemoteError("github_rate_limited")
        request_headers = {"If-None-Match": entry.etag} if entry is not None and entry.etag else {}
        response = self.transport(endpoint, request_headers)
        now = self.monotonic()
        if (not isinstance(response, ApiResponse) or type(response.status) is not int
                or not 100 <= response.status <= 599 or type(response.headers) is not dict
                or any(type(key) is not str or type(value) is not str
                       for key, value in response.headers.items())):
            raise RemoteError("github_invalid_response")
        headers = response.headers
        remaining = headers.get("x-ratelimit-remaining")
        rate_limited = (response.status == 429
                        or response.status == 403
                        and (remaining == "0" or "retry-after" in headers))
        if rate_limited:
            self._backoff(headers, now)
            raise RemoteError("github_rate_limited")
        if response.status in (401, 403):
            raise RemoteError("github_permission_denied")
        if response.status == 304:
            if entry is None:
                raise RemoteError("github_cache_miss")
            entry.expires_at = now + self._ttl(headers, self.default_ttl)
            return deepcopy(entry.payload)
        if not 200 <= response.status < 300:
            raise RemoteError("github_request_failed")
        etag = headers.get("etag")
        if etag is not None and (not etag or len(etag) > 512
                                 or any(ord(char) < 32 or ord(char) == 127 for char in etag)):
            raise RemoteError("github_invalid_response")
        if remaining == "0":
            self._backoff(headers, now)
        if len(self.cache) >= MAX_CACHE_ENTRIES and endpoint not in self.cache:
            oldest = min(self.cache, key=lambda key: self.cache[key].expires_at)
            del self.cache[oldest]
        self.cache[endpoint] = _CacheEntry(deepcopy(response.payload), etag,
                                           now + self._ttl(headers, self.default_ttl))
        return deepcopy(response.payload)


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
    request = client or GitHubSharedClient()
    repo = spec.get("github_repository") if isinstance(spec, dict) else None
    groups = [("remote_git", _GIT_IDS)] if remote_git else []
    if github_ci:
        groups.append(("github_ci", _CI_IDS))
    head = None

    def emit(mid, value, evidence, *, business_at=None, reason=None):
        dimensions = {"provider": "github", "target_role": "local_head"}
        if mid.startswith("github_ci"):
            dimensions.update(candidate_binding="exact_commit", check_scope="all_workflow_runs",
                              required_checks_claimed=False)
        if mid.endswith(".ahead"):
            dimensions["direction"] = "local_head_ahead_of_remote_primary"
        if mid.endswith(".behind"):
            dimensions["direction"] = "local_head_behind_remote_primary"
        if mid.startswith("remote_git"):
            basis = "Exact local HEAD versus GitHub actual default branch; ancestor comparison"
            unit = "unix_seconds" if mid.endswith("timestamp") else "boolean" if mid.endswith("contains_head") else "commits"
        elif mid == "github_ci.runs.reported_total":
            basis, unit = "GitHub-reported workflow run total for exact local HEAD; not required-check coverage", "runs"
        elif mid == "github_ci.runs.observed":
            basis, unit = "Unique workflow run IDs parsed for exact local HEAD within the bounded page window", "runs"
        elif mid == "github_ci.pages.observed":
            basis, unit = "Fully validated GitHub Actions listing pages within the bounded page window", "pages"
        elif mid == "github_ci.complete":
            basis, unit = "Observed unique runs equal the stable provider-reported total within the bounded page window", "boolean"
        elif mid.endswith("observed_timestamp"):
            basis, unit = "Hub time of the bounded GitHub observation attempt for exact local HEAD", "unix_seconds"
        else:
            basis, unit = "Latest attempt per workflow run ID for exact local HEAD; all runs, not required checks", "runs"
        bound = repo if valid_repository_binding(repo) else "unbound"
        metrics.append(metric(
            pid, mid, value, unit, "github:" + bound + ("@" + head if head else ""),
            content_hash([BINDING_VERSION, evidence, mid, value, reason]), observed_at,
            business_at=business_at, dimensions=dimensions, reason=reason, counting_basis=basis,
        ))

    def safe_code(exc):
        allowed = {
            "github_cache_miss", "github_client_unavailable", "github_invalid_endpoint",
            "github_invalid_header", "github_invalid_json", "github_invalid_response",
            "github_invalid_schema", "github_observation_failed", "github_output_limit",
            "github_permission_denied", "github_rate_limited", "github_request_failed",
            "github_target_mismatch", "github_timeout", "github_ci_changed_during_read",
        }
        if isinstance(exc, RemoteError) and str(exc) in allowed:
            return str(exc)
        if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
            return "github_timeout"
        if isinstance(exc, (KeyError, TypeError, ValueError)):
            return "github_invalid_schema"
        return "github_observation_failed"

    def add_issue(group, code, affected=None):
        recovery = ("Wait for the provider limit window to reset, then recollect"
                    if code == "github_rate_limited"
                    else "Restore explicit GitHub binding or read access and recollect")
        issues.append(issue(
            pid, code, "github:" + group, updated_at=observed_at,
            affected_items=affected, recovery_condition=recovery, retry_entry="collect_remote",
        ))

    def fail(group, ids, code):
        add_issue(group, code, list(ids))
        for mid in ids:
            emit(mid, None, [head, code], reason=code)

    def finish():
        return dict(
            metrics=metrics, issues=issues, disposition="partial" if issues else "observed",
            source_version=content_hash([m["source_version"] for m in metrics]),
        )

    if not valid_repository_binding(repo):
        for group, ids in groups:
            fail(group, ids, "github_binding_invalid")
        return finish()
    try:
        head = _head(root)
    except Exception:
        for group, ids in groups:
            fail(group, ids, "local_head_unavailable")
        return finish()

    if remote_git:
        try:
            metadata = request(f"repos/{repo}")
            if not isinstance(metadata, dict):
                raise RemoteError("github_invalid_schema")
            branch = metadata["default_branch"]
            if not isinstance(branch, str) or not branch or len(branch) > 255 or any(ord(char) < 32 for char in branch):
                raise RemoteError("github_invalid_schema")
            branch_data = request(f"repos/{repo}/branches/{quote(branch, safe='')}")
            if not isinstance(branch_data, dict):
                raise RemoteError("github_invalid_schema")
            primary = _sha(branch_data["commit"]["sha"])
            compared = request(f"repos/{repo}/compare/{head}...{primary}")
            if not isinstance(compared, dict) or _sha(compared["base_commit"]["sha"]) != head:
                raise RemoteError("github_target_mismatch")
            merge_base = _sha(compared["merge_base_commit"]["sha"])
            remote_ahead, remote_behind = _count(compared["ahead_by"]), _count(compared["behind_by"])
            expected = "diverged" if remote_ahead and remote_behind else "ahead" if remote_ahead else "behind" if remote_behind else "identical"
            if compared["status"] != expected or (remote_behind == 0) != (merge_base == head):
                raise RemoteError("github_invalid_schema")
            stamp = _now()
            evidence = [repo, head, branch, primary, merge_base, remote_ahead, remote_behind]
            values = (int(remote_behind == 0), remote_behind, remote_ahead, datetime.fromisoformat(stamp).timestamp())
            for mid, value in zip(_GIT_IDS, values):
                emit(mid, value, evidence, business_at=stamp if mid.endswith("observed_timestamp") else None)
        except Exception as exc:
            fail("remote_git", _GIT_IDS, safe_code(exc))

    if github_ci:
        runs, total, pages = {}, None, 0
        collection_error = None
        try:
            for page in range(1, MAX_PAGES + 1):
                payload = request(f"repos/{repo}/actions/runs?head_sha={head}&per_page=100&page={page}")
                if not isinstance(payload, dict):
                    raise RemoteError("github_invalid_schema")
                count = _count(payload["total_count"])
                if total is not None and total != count:
                    total = None
                    raise RemoteError("github_ci_changed_during_read")
                batch = payload["workflow_runs"]
                if type(batch) is not list or len(batch) > 100:
                    raise RemoteError("github_invalid_schema")
                page_runs = deepcopy(runs)
                for raw in batch:
                    if not isinstance(raw, dict):
                        raise RemoteError("github_invalid_schema")
                    selected = {key: raw[key] for key in ("id", "workflow_id", "run_attempt", "head_sha")}
                    for key in ("id", "workflow_id", "run_attempt"):
                        if _count(selected[key]) == 0:
                            raise RemoteError("github_invalid_schema")
                    if _sha(selected["head_sha"]) != head:
                        raise RemoteError("github_target_mismatch")
                    status, conclusion = raw.get("status"), raw.get("conclusion")
                    valid_status = (
                        type(status) is str
                        and status in {"queued", "in_progress", "completed", "waiting", "requested", "pending"}
                        and (conclusion is None or type(conclusion) is str and conclusion in {
                            "success", "failure", "neutral", "cancelled", "skipped", "timed_out",
                            "action_required", "stale", "startup_failure",
                        })
                        and (status == "completed") == (conclusion is not None)
                    )
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
                    old = page_runs.get(selected["id"])
                    if old is None or selected["run_attempt"] > old["run_attempt"]:
                        page_runs[selected["id"]] = selected
                    elif selected["run_attempt"] == old["run_attempt"] and selected != old:
                        raise RemoteError("github_ci_changed_during_read")
                if len(page_runs) > count:
                    raise RemoteError("github_invalid_schema")
                runs, total, pages = page_runs, count, page
                if len(runs) >= total or len(batch) < 100:
                    break
        except Exception as exc:
            collection_error = safe_code(exc)

        complete = collection_error is None and total is not None and len(runs) == total
        counts = dict.fromkeys(_CATEGORIES, 0)
        counts["total"] = len(runs)
        statuses_valid = all(run["status"] is not None for run in runs.values())
        invalid_times = sorted({key for run in runs.values() for key in run["invalid_times"]})
        for run in runs.values():
            status, conclusion = run["status"], run["conclusion"]
            if status is None:
                continue
            category = (
                "pending" if status != "completed"
                else "success" if conclusion == "success"
                else "failure" if conclusion in {"failure", "timed_out", "startup_failure"}
                else "cancelled" if conclusion == "cancelled"
                else "other"
            )
            counts[category] += 1
        stamp = _now()
        ordered = sorted(runs.values(), key=lambda run: run["id"])
        identity = [
            repo, head, total, pages, len(runs), complete,
            [{key: run[key] for key in ("id", "workflow_id", "run_attempt", "head_sha")} for run in ordered],
        ]
        outcomes = [identity, [{key: run[key] for key in ("id", "status", "conclusion")} for run in ordered]]
        business_at = (
            max((run["updated_at"] for run in runs.values()), key=lambda value: datetime.fromisoformat(value), default=None)
            if "updated_at" not in invalid_times else None
        )
        incomplete_reason = collection_error or (None if complete else "github_ci_incomplete")
        for key in _CATEGORIES:
            known = complete and (key == "total" or statuses_valid)
            reason = None if known else incomplete_reason or "github_ci_status_unknown"
            emit("github_ci.runs." + key, counts[key] if known else None,
                 identity if key == "total" else outcomes, business_at=business_at, reason=reason)
        emit("github_ci.runs.reported_total", total, identity,
             reason=None if total is not None else incomplete_reason or "github_ci_reported_total_unknown")
        emit("github_ci.runs.observed", len(runs), identity)
        emit("github_ci.pages.observed", pages, identity)
        emit("github_ci.complete", int(complete), identity)
        emit("github_ci.observed_timestamp", datetime.fromisoformat(stamp).timestamp(), identity, business_at=stamp)
        if collection_error:
            add_issue("github_ci", collection_error,
                      list(_CI_RESULT_IDS) + ["github_ci.runs.reported_total"])
        if not statuses_valid:
            add_issue("github_ci", "github_ci_status_unknown",
                      ["github_ci.runs." + key for key in _CATEGORIES if key != "total"])
        if invalid_times:
            issues.append(issue(
                pid, "github_ci_time_unknown", "github:github_ci", updated_at=observed_at,
                affected_items=invalid_times, recovery_condition="Obtain valid optional run timestamps",
                retry_entry="collect_remote",
            ))
        if not complete and not collection_error:
            add_issue("github_ci", "github_ci_incomplete", list(_CI_RESULT_IDS))

    try:
        stable = _head(root) == head
    except Exception:
        stable = False
    if not stable:
        metrics.clear()
        issues.clear()
        for group, ids in groups:
            fail(group, ids, "local_head_changed")
    return finish()
