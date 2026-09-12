"""Bounded, local-only Git observations. No path names or remote URLs are emitted."""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from hub.metrics import issue

_OUTPUT_LIMIT = 2 * 1024 * 1024
_TIMEOUT = 10
_IDS = {
    "git.staged.files": "files", "git.unstaged.files": "files",
    "git.untracked.files": "files", "git.ignored.files": "files",
    "git.staged.lines_added": "lines", "git.staged.lines_deleted": "lines",
    "git.unstaged.lines_added": "lines", "git.unstaged.lines_deleted": "lines",
    "git.unpushed.commits": "commits", "git.main.ahead": "commits",
    "git.main.behind": "commits", "git.main.contains_head": "boolean",
    "git.main.patch_equivalent.commits": "commits", "git.main.unique.commits": "commits",
    "git.main.tree_equal": "boolean",
    "git.changed.overlapping_files": "files", "git.changed.conflicted_files": "files",
    "git.staged.binary_files": "files", "git.unstaged.binary_files": "files",
    "git.staged.known_text_lines_added": "lines", "git.staged.known_text_lines_deleted": "lines",
    "git.unstaged.known_text_lines_added": "lines", "git.unstaged.known_text_lines_deleted": "lines",
    "git.remote.observation_age_seconds": "seconds", "git.remote.observed_timestamp": "unix_seconds",
}


class GitObservationError(Exception):
    """A deliberately sanitized failure, never containing Git stderr."""


def _run(root: Path, *args: str) -> bytes:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    # Caller environment must not redirect this invocation to a different repository.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(key, None)
    try:
        proc = subprocess.Popen(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    except OSError as exc:
        raise GitObservationError("git_unavailable") from exc
    chunks, total, deadline = [], 0, time.monotonic() + _TIMEOUT
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GitObservationError("git_timeout")
                for key, _ in selector.select(min(remaining, .1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > _OUTPUT_LIMIT:
                        raise GitObservationError("git_output_limit")
                    chunks.append(chunk)
            try:
                code = proc.wait(timeout=max(.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise GitObservationError("git_timeout") from exc
            if code:
                raise GitObservationError("git_command_failed")
        return b"".join(chunks)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()


def _status_counts(data: bytes) -> dict:
    result = dict(staged=0, unstaged=0, untracked=0, ignored=0, overlapping=0, conflicted=0)
    records, index = data.split(b"\0"), 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise GitObservationError("invalid_porcelain")
        x, y = record[:1], record[1:2]
        if record[:2] == b"??":
            result["untracked"] += 1
        elif record[:2] == b"!!":
            result["ignored"] += 1
        else:
            staged, unstaged = x != b" ", y != b" "
            result["staged"] += staged
            result["unstaged"] += unstaged
            result["overlapping"] += staged and unstaged
            result["conflicted"] += b"U" in record[:2] or record[:2] in (b"AA", b"DD")
        if x in (b"R", b"C") or y in (b"R", b"C"):
            if index >= len(records) or not records[index]:
                raise GitObservationError("invalid_porcelain_rename")
            index += 1  # -z adds the original path after a rename/copy record.
    return result


def _numstat(data: bytes) -> tuple[int, int, int]:
    added = deleted = binary = 0
    records, index = data.split(b"\0"), 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        fields = record.split(b"\t", 2)
        if len(fields) != 3:
            raise GitObservationError("invalid_numstat")
        a, d, path = fields
        if a == b"-" or d == b"-":
            binary += 1
        else:
            try:
                added += int(a)
                deleted += int(d)
            except ValueError as exc:
                raise GitObservationError("invalid_numstat") from exc
        if not path:  # A rename is: counts TAB NUL old NUL new NUL.
            if index + 1 >= len(records) or not records[index] or not records[index + 1]:
                raise GitObservationError("invalid_numstat_rename")
            index += 2
    return added, deleted, binary


def collect_git(root: Path, project_id: str, observed_at: str) -> dict:
    root = Path(root).resolve()
    metrics, issues, digest = [], [], hashlib.sha256()

    def run(*args):
        value = _run(root, *args)
        digest.update(repr(args).encode() + b"\0" + value)
        return value

    def emit(metric_id, value, *, dimensions=None, reason=None, quality=None, basis="", source="git:local", business_at=None):
        metrics.append(dict(project_id=project_id, metric_id=metric_id, value=value,
                            unit=_IDS[metric_id], dimensions=dimensions or {}, observed_at=observed_at,
                            business_at=business_at, source_ref=source, source_version="",
                            quality=quality or ("unknown" if value is None else "good"), reason=reason,
                            counting_basis=basis))

    def fail(group, exc):
        issues.append(issue(project_id, str(exc), "git:" + group, updated_at=observed_at,
                            recovery_condition="Restore the local repository observation prerequisite",
                            retry_entry="collect_git"))

    def finish(disposition):
        present = {m["metric_id"] for m in metrics}
        for metric_id in _IDS:
            if metric_id not in present and not metric_id.startswith("git.remote."):
                emit(metric_id, None, reason="observation_unavailable", basis="Independent Git observation group unavailable")
        for role in ("upstream", "primary"):
            if not any(m["metric_id"].startswith("git.remote.") and m["dimensions"].get("ref_role") == role for m in metrics):
                emit_remote(role, None, None)
        version = "sha256:" + digest.hexdigest()
        for metric in metrics:
            semantic = {k: v for k, v in metric.items() if k not in ("source_version", "observed_at")}
            metric["source_version"] = "sha256:" + hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest()
        return dict(metrics=metrics, issues=issues, disposition=disposition, source_version=version)

    def emit_remote(role, stamp, age):
        for metric_id, value in (("git.remote.observation_age_seconds", age),
                                 ("git.remote.observed_timestamp", stamp.timestamp() if stamp else None)):
            emit(metric_id, value, dimensions={"ref_role": role, "remote_verified": False},
                 reason=None if value is not None else "matching_FETCH_HEAD_observation_unavailable",
                 basis="Matching local FETCH_HEAD commit and file mtime; does not verify remote",
                 source="git:FETCH_HEAD")

    if not root.is_dir():
        fail("repository", GitObservationError("root_unavailable"))
        return finish("offline")
    try:
        actual = Path(os.fsdecode(run("rev-parse", "--show-toplevel").rstrip(b"\n"))).resolve()
        if actual != root:
            fail("repository", GitObservationError("registry_root_is_not_repository_root"))
            return finish("no_git")
    except GitObservationError as exc:
        fail("repository", exc)
        return finish("partial" if (root / ".git").exists() else "no_git")

    try:
        run("rev-parse", "--verify", "HEAD")
    except GitObservationError:
        digest.update(b"HEAD_unavailable")  # An unborn repository still has valid status metrics.

    try:
        counts = _status_counts(run("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=traditional"))
        for name in ("staged", "unstaged", "untracked", "ignored"):
            emit(f"git.{name}.files", counts[name], dimensions={},
                 basis="Porcelain file records with all untracked and ignored files; staged and unstaged overlap; nested repositories are opaque records", source="git:status")
        for suffix, key in (("overlapping_files", "overlapping"), ("conflicted_files", "conflicted")):
            emit("git.changed." + suffix, counts[key], basis="Porcelain file records", source="git:status")
    except GitObservationError as exc:
        fail("status", exc)

    for group, flags in (("staged", ["--cached"]), ("unstaged", [])):
        try:
            added, deleted, binary = _numstat(run("diff", *flags, "--numstat", "-z", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all"))
            for suffix, value in (("added", added), ("deleted", deleted)):
                emit(f"git.{group}.lines_{suffix}", None if binary else value,
                     dimensions={},
                     reason="binary_line_count_unknown" if binary else None,
                     basis="Numstat line counts; any binary change makes total unknown", source=f"git:diff:{group}")
                emit(f"git.{group}.known_text_lines_{suffix}", value,
                     basis="Known text numstat lines excluding binary changes", source=f"git:diff:{group}")
            emit(f"git.{group}.binary_files", binary, basis="Numstat records with binary line counts", source=f"git:diff:{group}")
        except GitObservationError as exc:
            fail(group + "_lines", exc)

    def remote_provenance(ref, role):
        sha = run("rev-parse", "--verify", ref).strip()
        dims = {"remote_verified": False}
        matched_stamp = matched_age = None
        try:
            fetch_path = Path(os.fsdecode(run("rev-parse", "--git-path", "FETCH_HEAD").strip()))
            if not fetch_path.is_absolute():
                fetch_path = root / fetch_path
            with fetch_path.open("rb") as stream:
                content = stream.read(_OUTPUT_LIMIT + 1)
            if len(content) <= _OUTPUT_LIMIT and any(line.split(b"\t", 1)[0] == sha for line in content.splitlines()):
                stamp = datetime.fromtimestamp(fetch_path.stat().st_mtime, timezone.utc)
                observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                age = (observed - stamp).total_seconds()
                if age >= 0:
                    matched_stamp, matched_age = stamp, age
                    digest.update(content + str(fetch_path.stat().st_mtime_ns).encode())
        except (OSError, ValueError, TypeError, GitObservationError):
            pass
        emit_remote(role, matched_stamp, matched_age)
        return dims

    try:
        upstream = os.fsdecode(run("rev-parse", "--symbolic-full-name", "@{upstream}")).strip()
        if not upstream.startswith("refs/remotes/"):
            raise GitObservationError("upstream_is_not_remote_tracking")
        dims = remote_provenance(upstream, "upstream")
        value = int(run("rev-list", "--count", f"{upstream}..HEAD").strip())
        emit("git.unpushed.commits", value, dimensions=dims,
             reason="local_tracking_only_remote_not_verified", basis="HEAD commits absent from locally stored upstream tracking ref", source="git:upstream_tracking")
    except (GitObservationError, ValueError) as exc:
        fail("unpushed", exc if isinstance(exc, GitObservationError) else GitObservationError("invalid_commit_count"))

    primary = None
    try:
        fallback = None
        try:
            primary = os.fsdecode(run("symbolic-ref", "refs/remotes/origin/HEAD")).strip()
            if not primary.startswith("refs/remotes/origin/"):
                raise GitObservationError("invalid_origin_head")
        except GitObservationError:
            primary = None
            for name in ("main", "master"):
                try:
                    run("rev-parse", "--verify", f"refs/remotes/origin/{name}^{{commit}}")
                    primary, fallback = f"refs/remotes/origin/{name}", "origin_HEAD_missing_conventional_name_fallback"
                    break
                except GitObservationError:
                    pass
            if primary is None:
                raise GitObservationError("primary_remote_tracking_ref_unknown")
        dims = remote_provenance(primary, "primary")
        ahead, behind = map(int, run("rev-list", "--left-right", "--count", f"HEAD...{primary}").split())
        for suffix, value in (("ahead", ahead), ("behind", behind), ("contains_head", int(ahead == 0))):
            emit(f"git.main.{suffix}", value, dimensions=dims.copy(),
                 reason=fallback or "local_tracking_only_remote_not_verified",
                 basis="HEAD compared to local origin primary tracking ref; containment means no HEAD-only commits", source="git:origin_primary_tracking")
    except (GitObservationError, ValueError) as exc:
        fail("primary", exc if isinstance(exc, GitObservationError) else GitObservationError("invalid_commit_count"))
    if primary is not None:
        try:
            equivalent = unique = 0
            for line in run("cherry", primary, "HEAD").splitlines():
                fields = line.split()
                if len(fields) != 2 or fields[0] not in (b"+", b"-"):
                    raise GitObservationError("invalid_cherry_output")
                equivalent += fields[0] == b"-"
                unique += fields[0] == b"+"
            for suffix, value in (("patch_equivalent", equivalent), ("unique", unique)):
                emit(f"git.main.{suffix}.commits", value, dimensions={"remote_verified": False},
                     reason=fallback or "local_tracking_only_remote_not_verified",
                     basis="Git cherry minus/plus counts for HEAD non-merge commits absent from local primary history; patch equivalence is not ancestry or merge validation",
                     source="git:origin_primary_patch_comparison")
        except GitObservationError as exc:
            fail("primary_patch_comparison", exc)
        try:
            different_paths = run("diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv",
                                  "--ignore-submodules=none", "HEAD", primary, "--")
            emit("git.main.tree_equal", int(not different_paths), dimensions={"remote_verified": False},
                 reason=fallback or "local_tracking_only_remote_not_verified",
                 basis="No changed paths between committed HEAD and local primary trees; excludes working tree changes",
                 source="git:origin_primary_tree_comparison")
        except GitObservationError as exc:
            fail("primary_tree_comparison", exc)
    return finish("partial" if issues else "resolved")
