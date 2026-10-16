#!/usr/bin/env python3
"""Serial, non-destructive GitHub fetch and branch comparison for copied projects.

Does not stage, commit, merge, change remotes, push, delete refs, or print secrets.
Per-project observations are returned for the existing workstation inventory.
"""
import json
import os
from pathlib import Path
import subprocess
import urllib.parse
import yaml


def git(root, *args, timeout=120):
    return subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"})


def identity(value):
    value = value.strip()
    if value.startswith("git@github.com:"):
        path = value.split(":", 1)[1]
    else:
        parsed = urllib.parse.urlsplit(value)
        if parsed.hostname != "github.com" or parsed.scheme not in {"https", "ssh"}:
            return None
        path = parsed.path.lstrip("/")
    return "github.com/" + path.removesuffix(".git").rstrip("/")


def main():
    inventory = yaml.safe_load(Path("data/workstation/project_inventory.yaml").read_text())
    observations = []
    for project in inventory["projects"]:
        if project.get("codex_session", {}).get("status") == "NOT_INDEPENDENT_PROJECT":
            continue
        target = project.get("proposed_linux_path")
        if not target or not Path(target).is_dir():
            continue
        root = Path(target)
        result = {"id": project["id"], "cwd": target}
        if not (root / ".git").exists():
            result["status"] = "NO_GIT_REPOSITORY"
        else:
            actual = git(root, "rev-parse", "--show-toplevel")
            if actual.returncode or Path(os.fsdecode(actual.stdout).strip()).resolve() != root.resolve():
                raise RuntimeError("Unexpected Git worktree root")
            head = git(root, "rev-parse", "HEAD")
            branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
            status = git(root, "status", "--porcelain=v1", "-z")
            remote = git(root, "remote", "get-url", "origin")
            expected = project.get("remote_identity")
            remote_id = identity(os.fsdecode(remote.stdout)) if not remote.returncode else None
            result.update(head_before=os.fsdecode(head.stdout).strip(),
                          branch=os.fsdecode(branch.stdout).strip(),
                          dirty_entries=len([x for x in status.stdout.split(b"\0") if x]),
                          status_exit=status.returncode, remote_identity=remote_id)
            if remote_id is None or remote_id != expected:
                result["status"] = "REMOTE_MISSING_OR_IDENTITY_MISMATCH_PRESERVED"
            elif head.returncode or branch.returncode or status.returncode:
                result["status"] = "LOCAL_GIT_IDENTITY_CHECK_FAILED"
            else:
                check = git(root, "fsck", "--no-reflogs", "--no-dangling", timeout=180)
                result["fsck_exit"] = check.returncode
                if check.returncode:
                    result["status"] = "LOCAL_GIT_OBJECT_CHECK_FAILED_PRESERVED"
                else:
                    fetched = git(root, "fetch", "--no-recurse-submodules", "origin", timeout=180)
                    result["fetch_exit"] = fetched.returncode
                    if fetched.returncode:
                        result["status"] = "FETCH_FAILED_PRESERVED"
                    else:
                        ref = "refs/remotes/origin/" + result["branch"]
                        remote_head = git(root, "rev-parse", "--verify", ref)
                        counts = git(root, "rev-list", "--left-right", "--count", "HEAD..." + ref)
                        result["head_after"] = os.fsdecode(git(root, "rev-parse", "HEAD").stdout).strip()
                        if result["head_after"] != result["head_before"]:
                            raise RuntimeError("Local HEAD changed concurrently; preserve workspace")
                        if remote_head.returncode or counts.returncode:
                            result["status"] = "FETCHED_REMOTE_BRANCH_MISSING_OR_UNRELATED"
                        else:
                            ahead, behind = map(int, counts.stdout.split())
                            result.update(remote_head=os.fsdecode(remote_head.stdout).strip(), ahead=ahead, behind=behind,
                                          status="SAME_COMMIT_WORKTREE_DIRTY" if ahead == behind == 0 and result["dirty_entries"]
                                          else "SAME_COMMIT_CLEAN" if ahead == behind == 0
                                          else "BRANCH_COMPARISON_NEEDS_SCOPED_REPAIR")
        observations.append(result)
        print(json.dumps({"project": result["id"], "status": result["status"],
                          "ahead": result.get("ahead"), "behind": result.get("behind")}), flush=True)
    print(json.dumps({"observations": observations}), flush=True)


if __name__ == "__main__":
    main()
