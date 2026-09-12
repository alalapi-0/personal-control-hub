import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from hub.metric_git import GitObservationError, _numstat, collect_git
from hub.metrics import validate_metric, metric_key


class GitMetricTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.root / "tracked").write_text("one\ntwo\n")
        self.git("add", "tracked")
        self.git("commit", "-m", "initial")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL).decode().strip()

    def collect(self, root=None):
        return collect_git(root or self.root, "example", datetime.now(timezone.utc).isoformat())

    def metrics(self):
        return {m["metric_id"]: m for m in self.collect()["metrics"]}

    def test_staged_and_unstaged_overlap_and_private_paths(self):
        (self.root / "tracked").write_text("one\ntwo\nthree\n")
        self.git("add", "tracked")
        (self.root / "tracked").write_text("one\ntwo\nthree\nfour\n")
        (self.root / "secret-path").write_text("data")
        (self.root / ".gitignore").write_text("ignored\ncache/\n")
        (self.root / "cache").mkdir()
        (self.root / "cache" / "a").write_text("a")
        (self.root / "cache" / "b").write_text("b")
        (self.root / "ignored").write_text("data")
        metrics = self.metrics()
        self.assertEqual(1, metrics["git.staged.files"]["value"])
        self.assertEqual(1, metrics["git.unstaged.files"]["value"])
        self.assertEqual(2, metrics["git.untracked.files"]["value"])
        self.assertEqual(3, metrics["git.ignored.files"]["value"])
        self.assertEqual(1, metrics["git.changed.overlapping_files"]["value"])
        self.assertEqual(1, metrics["git.staged.lines_added"]["value"])
        self.assertEqual(1, metrics["git.unstaged.lines_added"]["value"])
        self.assertNotIn("secret-path", str(metrics))

    def test_rename_with_tabs_and_newlines_is_one_record(self):
        self.git("mv", "tracked", "renamed\t\nname")
        metrics = self.metrics()
        self.assertEqual(1, metrics["git.staged.files"]["value"])
        self.assertEqual(0, metrics["git.untracked.files"]["value"])
        self.assertEqual(0, metrics["git.staged.lines_added"]["value"])
        self.assertEqual((3, 2, 0), _numstat(b"3\t2\t\0old\tname\0new\nname\0"))

    def test_binary_lines_unknown_with_known_text_separate(self):
        (self.root / "binary").write_bytes(b"a\0b")
        self.git("add", "binary")
        metric = self.metrics()["git.staged.lines_added"]
        self.assertIsNone(metric["value"])
        self.assertEqual("unknown", metric["quality"])
        self.assertEqual(1, self.metrics()["git.staged.binary_files"]["value"])

    def test_no_git_nested_and_missing_root(self):
        child = self.root / "child"
        child.mkdir()
        self.assertEqual("no_git", self.collect(child)["disposition"])
        with tempfile.TemporaryDirectory() as other:
            self.assertEqual("no_git", self.collect(Path(other))["disposition"])
        self.assertEqual("offline", self.collect(self.root / "missing")["disposition"])

    def configure_tracking(self):
        initial = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", initial)
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.git("config", "remote.origin.url", "https://example.invalid/private-token")
        self.git("config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
        self.git("config", "branch.main.remote", "origin")
        self.git("config", "branch.main.merge", "refs/heads/main")
        return initial

    def test_local_remote_comparison_and_fetch_provenance(self):
        initial = self.configure_tracking()
        (self.root / ".git" / "FETCH_HEAD").write_text(initial + "\t\tbranch 'main' of PRIVATE_URL\n")
        self.git("commit", "--allow-empty", "-m", "local")
        metrics = self.metrics()
        self.assertEqual(1, metrics["git.unpushed.commits"]["value"])
        self.assertEqual(1, metrics["git.main.ahead"]["value"])
        self.assertEqual(0, metrics["git.main.behind"]["value"])
        self.assertEqual(0, metrics["git.main.contains_head"]["value"])
        self.assertFalse(metrics["git.main.ahead"]["dimensions"]["remote_verified"])
        self.assertIsNotNone(metrics["git.remote.observation_age_seconds"]["value"])
        self.assertNotIn("PRIVATE_URL", str(metrics))
        self.assertNotIn("private-token", str(metrics))
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("reset", "--soft", initial)
        metrics = self.metrics()
        self.assertEqual(1, metrics["git.main.behind"]["value"])
        self.assertEqual(1, metrics["git.main.contains_head"]["value"])
        self.assertIsNone(metrics["git.remote.observation_age_seconds"]["value"])

    def test_cherry_picked_patch_is_equivalent_without_ancestry(self):
        initial = self.configure_tracking()
        (self.root / "tracked").write_text("one\ntwo\nfeature\n")
        self.git("add", "tracked")
        self.git("commit", "-m", "feature")
        feature = self.git("rev-parse", "HEAD")
        self.git("checkout", "-b", "primary-copy", initial)
        self.git("cherry-pick", feature)
        self.git("commit", "--amend", "-m", "equivalent feature with different identity")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("checkout", "main")
        metrics = self.metrics()
        self.assertEqual(0, metrics["git.main.contains_head"]["value"])
        self.assertEqual(1, metrics["git.main.patch_equivalent.commits"]["value"])
        self.assertEqual(0, metrics["git.main.unique.commits"]["value"])
        self.assertEqual(1, metrics["git.main.tree_equal"]["value"])
        for row in metrics.values():
            validate_metric(row)
        (self.root / "tracked").write_text("one\ntwo\nfeature\nunique\n")
        self.git("add", "tracked")
        self.git("commit", "-m", "unique followup")
        metrics = self.metrics()
        self.assertEqual(1, metrics["git.main.patch_equivalent.commits"]["value"])
        self.assertEqual(1, metrics["git.main.unique.commits"]["value"])
        self.assertEqual(0, metrics["git.main.tree_equal"]["value"])

    def test_patch_comparison_failure_preserves_ancestry_and_tree(self):
        self.configure_tracking()
        from hub import metric_git
        original = metric_git._run

        def fail_cherry(root, *args):
            if args[0] == "cherry":
                raise GitObservationError("git_timeout")
            return original(root, *args)

        with patch.object(metric_git, "_run", side_effect=fail_cherry):
            result = self.collect()
        metrics = {m["metric_id"]: m for m in result["metrics"]}
        self.assertEqual("partial", result["disposition"])
        self.assertIsNone(metrics["git.main.patch_equivalent.commits"]["value"])
        self.assertIsNone(metrics["git.main.unique.commits"]["value"])
        self.assertEqual(1, metrics["git.main.contains_head"]["value"])
        self.assertEqual(1, metrics["git.main.tree_equal"]["value"])

    def test_contract_and_independent_metric_identity(self):
        self.configure_tracking()
        before = self.metrics()
        for row in before.values():
            validate_metric(row)
        (self.root / "new_staged_file").write_text("new line\n")
        self.git("add", "new_staged_file")
        after = self.metrics()
        for row in after.values():
            validate_metric(row)
        for key in ("git.unstaged.files", "git.unstaged.lines_added", "git.main.ahead"):
            self.assertEqual(before[key]["source_version"], after[key]["source_version"])
            self.assertEqual(metric_key(before[key]), metric_key(after[key]))
        self.assertNotEqual(before["git.staged.files"]["source_version"], after["git.staged.files"]["source_version"])
        self.assertEqual(metric_key(before["git.staged.files"]), metric_key(after["git.staged.files"]))

    def test_fallback_and_independent_failure_preserve_status(self):
        self.configure_tracking()
        self.git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
        self.assertIn("fallback", self.metrics()["git.main.ahead"]["reason"])
        from hub import metric_git
        original = metric_git._run

        def fail_diff(root, *args):
            if args[0] == "diff":
                raise GitObservationError("git_timeout")
            return original(root, *args)

        with patch.object(metric_git, "_run", side_effect=fail_diff):
            result = self.collect()
        metrics = {m["metric_id"]: m for m in result["metrics"]}
        self.assertEqual("partial", result["disposition"])
        self.assertEqual(0, metrics["git.staged.files"]["value"])
        self.assertEqual(1, metrics["git.main.contains_head"]["value"])
        self.assertIsNone(metrics["git.staged.lines_added"]["value"])


if __name__ == "__main__":
    unittest.main()
