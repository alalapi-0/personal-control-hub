"""V3-11 closure checks; no project-root or credential I/O."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_v3_closure",
    ROOT / "scripts/check_v3_closure.py",
)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class V3ClosureTests(unittest.TestCase):
    def test_metadata_closure_keeps_recovery_and_dead_paths_clear(self):
        result = CHECKER.run_check()
        self.assertEqual(result["kind"], "v3_closure_check")
        self.assertFalse(result["agent_required"])
        self.assertFalse(result["probed_protected_roots"])
        self.assertFalse(result["remote_verified"])
        self.assertFalse(result["goal_complete"])
        self.assertEqual(result["dead_paths"]["unexpectedly_present"], [])
        self.assertFalse(result["dead_paths"]["duplicate_current_state"])
        self.assertEqual(result["coverage"]["counts"]["rollout_project_units"], 22)
        self.assertEqual(result["coverage"]["counts"]["no_git"], 7)
        self.assertEqual(
            result["handoff"]["v3_08_remaining"]["blocked"],
            ["novel-continuation-agent"],
        )
        self.assertEqual(
            set(result["handoff"]["v3_08_remaining"]["skipped_condition_unchanged"]),
            {"computer-study-plan"},
        )
        self.assertIn("youtube-hq-downloader", result["protected_local_io"])
        self.assertEqual(len(result["unresolved"]), 11)

    def test_remote_lookup_is_used_without_visiting_project_roots(self):
        seen = []

        def fake_remote(repository):
            seen.append(repository)
            if repository == "alalapi-0/personal-control-hub":
                return CHECKER._git("rev-parse", "HEAD")
            return "a" * 40

        result = CHECKER.run_check(verify_remote=True, remote_lookup=fake_remote)
        self.assertTrue(result["remote_verified"])
        self.assertIn("personal-control-hub", result["remotes"])
        self.assertIn("mpv-clip-workbench", result["remotes"])
        self.assertNotIn("youtube-hq-downloader", result["remotes"])
        self.assertTrue(seen)
        self.assertNotIn("alalapi-0/youtube-hq-downloader", seen)
        self.assertEqual(
            result["remotes"]["personal-control-hub-local"]["main"],
            result["remotes"]["personal-control-hub-local"]["head"],
        )
        self.assertFalse(result["goal_complete"])

    def test_plain_command_stays_bounded(self):
        completed = subprocess.run(
            [sys.executable, "-I", str(ROOT / "scripts/check_v3_closure.py")],
            cwd=ROOT,
            env={
                "PATH": "",
                "PYTHONNOUSERSITE": "1",
                "PYTHONSAFEPATH": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
            },
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["kind"], "v3_closure_check")
        self.assertFalse(payload["goal_complete"])
        self.assertLessEqual(len(completed.stdout.encode()), 8192)


if __name__ == "__main__":
    unittest.main()
