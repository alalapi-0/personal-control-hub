"""Ordinary-command chain for V3-10; no Agent, model, or MCP."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_v3_end_to_end",
    ROOT / "scripts/check_v3_end_to_end.py",
)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class V3EndToEndTests(unittest.TestCase):
    def test_isolated_chain_completes_without_agent(self):
        result = CHECKER.run_check()
        self.assertFalse(result["agent_required"])
        self.assertFalse(result["mcp_required"])
        self.assertFalse(result["model_required"])
        self.assertEqual(result["commands"]["start"]["status"], "imported")
        self.assertEqual(result["commands"]["sync_replay"]["status"], "reused")
        self.assertEqual(result["commands"]["sync_source_change"]["status"], "imported")
        self.assertEqual(result["commands"]["offline_peer"]["peer_status"], "imported")
        self.assertEqual(result["commands"]["offline_peer"]["offline_error"], "project_export_unavailable")
        self.assertTrue(result["commands"]["offline_peer"]["online_not_blocked"])
        self.assertEqual(result["commands"]["recover"]["status"], "imported")
        self.assertGreaterEqual(result["commands"]["query"]["total_remaining"], 1)
        self.assertEqual(result["commands"]["reviews_empty_store"]["history_status"], "empty")
        self.assertEqual(result["review_rework_fixture"]["current_revision"], 2)
        self.assertTrue(result["review_rework_fixture"]["history_rework_not_copied"])
        self.assertFalse(result["review_rework_fixture"]["replaces_real_business"])
        self.assertTrue(result["coverage"]["valid"])
        self.assertEqual(result["coverage"]["counts"]["rollout_project_units"], 22)
        self.assertEqual(
            result["handoff"]["v3_08_remaining"]["blocked"],
            ["novel-continuation-agent"],
        )
        self.assertIn("mcp_host_events", result["unverified_host_events"])
        self.assertTrue(result["real_business_not_replaced_by_fixture"])

    def test_plain_command_stays_isolated_and_bounded(self):
        completed = subprocess.run(
            [sys.executable, "-I", str(ROOT / "scripts/check_v3_end_to_end.py")],
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
            timeout=60,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["kind"], "v3_end_to_end_check")
        self.assertLessEqual(len(completed.stdout.encode()), 8192)


if __name__ == "__main__":
    unittest.main()
