"""Behavior checks for the Codex-only bootstrap; subprocesses are instrumented."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TASK = 'ALL-PROJECTS-CODEX-GOVERNANCE-V1'
GUARD = r'''
import builtins, io, os, subprocess, json
from pathlib import Path
trace = os.environ['HUB_ACCESS_TRACE']
_original_open = builtins.open
with _original_open(trace + '.installed', 'a') as installed:
    installed.write(str(os.getpid()) + '\n')

def guarded(original):
    def call(path, *args, **kwargs):
        try:
            text = os.fsdecode(path)
        except TypeError:
            text = ''
        if any('cursor' in p.lower() for p in Path(text).parts):
            with _original_open(trace, 'a') as out:
                out.write(original.__name__ + ':' + text + '\n')
            raise RuntimeError('excluded host path accessed')
        return original(path, *args, **kwargs)
    return call
_original_run = subprocess.run
def traced_run(args, *rest, **kwargs):
    if isinstance(args, list) and args and args[0] == 'git':
        with _original_open(trace + '.git', 'a') as out:
            out.write(json.dumps(args) + '\n')
    return _original_run(args, *rest, **kwargs)
subprocess.run = traced_run
for mod, names in ((builtins, ['open']), (io, ['open']), (os, ['stat', 'lstat', 'scandir', 'listdir'])):
    for name in names:
        setattr(mod, name, guarded(getattr(mod, name)))
'''


def module(name):
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class BootstrapTests(unittest.TestCase):
    def test_runner_and_children_never_access_excluded_host(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / 'sitecustomize.py').write_text(GUARD)
            trace = tmp / 'access.txt'
            env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(tmp), *sys.path]), HUB_ACCESS_TRACE=str(trace),
                       PYTHONDONTWRITEBYTECODE='1')
            watched = ['data/logs/auto_advance_log.jsonl', 'data/logs/environment_check_log.jsonl',
                       'data/runtime/toolchain_status.yaml', 'data/codex_queue/next_round_prompt.md']
            before = {p: (ROOT / p).read_bytes() for p in watched if (ROOT / p).is_file()}
            for script, args in [('auto_advance_runner.py', ['--mode', 'check']),
                                 ('auto_advance_runner.py', ['--mode', 'prepare-next']),
                                 ('auto_advance_runner.py', ['--mode', 'finalize-round']),
                                 ('check_repo.py', []), ('round_consistency_check.py', ['--json'])]:
                result = subprocess.run([sys.executable, 'scripts/' + script, *args, '--task-id', TASK],
                                        cwd=ROOT, env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(trace.exists(), trace.read_text() if trace.exists() else '')
            git_calls = [json.loads(line) for line in Path(str(trace) + ".git").read_text().splitlines()]
            path_queries = [call for call in git_calls if call[1] in {"status", "diff", "ls-files"}]
            self.assertTrue(any(call[1:4] == ["diff", "--cached", "--name-only"] for call in path_queries))
            runner = module("auto_advance_runner")
            with patch.dict(os.environ, HUB_GOVERNANCE_TASK=TASK):
                expected = [":(literal)" + path for path in runner._candidate_paths()]
            for call in path_queries:
                self.assertIn("--", call)
                paths = call[call.index("--") + 1:]
                self.assertTrue(paths)
                self.assertTrue(all(path.startswith(":(literal)") and "cursor" not in path.lower() for path in paths))
                self.assertEqual(paths, expected)
            self.assertTrue(all(call[1] not in {"add", "commit", "push"} for call in git_calls))
            self.assertGreaterEqual(len(Path(str(trace) + ".installed").read_text().splitlines()), 14)
            self.assertEqual(before, {p: (ROOT / p).read_bytes() for p in before})

    def test_finalize_ignores_unrelated_staged_content(self):
        runner = module("auto_advance_runner")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, HUB_GOVERNANCE_TASK=TASK):
            tmp = Path(directory)
            evidence = tmp / "docs/reports/all-projects-governance/bootstrap"
            evidence.mkdir(parents=True)
            (evidence / "candidate.json").write_text(json.dumps({"files": {"task.txt": "fixture"}}))
            (tmp / "task.txt").write_text("owned fixture")
            unrelated = tmp / "unrelated.txt"
            unrelated.write_text(runner.SENSITIVE_CONTENT_MARKERS[0])
            for args in (["git", "init", "-q"], ["git", "add", "--", "task.txt", "unrelated.txt"]):
                subprocess.run(args, cwd=tmp, check=True, capture_output=True)
            original_read = Path.read_text
            def owned_read(path, *args, **kwargs):
                self.assertNotEqual(path, unrelated)
                return original_read(path, *args, **kwargs)
            with patch.object(runner, "ROOT", tmp), patch.object(Path, "read_text", owned_read), \
                 patch.object(runner, "_get_round_context", return_value={}), \
                 patch.object(runner, "_run_checks", return_value={"hard_blockers": [], "soft_warnings": []}):
                result = runner.mode_finalize_round()
            self.assertEqual(result["hard_blockers"], [])
            self.assertEqual(result["decision"], "continue")

    def test_excluded_subtree_is_pruned_before_stat_or_descent(self):
        gate = module("agent_gate")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, HUB_GOVERNANCE_TASK=TASK):
            tmp = Path(directory)
            forbidden = tmp / "docs" / "cursor-synthetic-boundary"
            forbidden.mkdir(parents=True)
            (forbidden / "payload.md").write_text("synthetic fixture, never read")
            (tmp / "docs" / "allowed.md").write_text("allowed")
            original_stat = Path.stat
            original_scan = os.scandir
            def no_stat(path, *args, **kwargs):
                self.assertNotIn("cursor-synthetic-boundary", str(path))
                return original_stat(path, *args, **kwargs)
            def no_scan(path):
                self.assertNotIn("cursor-synthetic-boundary", str(path))
                return original_scan(path)
            with patch.object(gate, "ROOT", tmp), patch.object(Path, "stat", no_stat), patch.object(os, "scandir", no_scan):
                self.assertEqual(gate._iter_scan_files(), [tmp / "docs" / "allowed.md"])

    def test_state_is_scanned_for_tokens_by_both_entrypoints(self):
        gate = module("agent_gate")
        checker = module("check_repo")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, HUB_GOVERNANCE_TASK=TASK):
            tmp = Path(directory)
            fake = "ghp_" + "X" * 30
            (tmp / "STATE.yaml").write_text("unexpected: " + fake)
            with patch.object(gate, "ROOT", tmp):
                hits = gate._scan_for_secrets()
            self.assertTrue(any("STATE.yaml" in hit for hit in hits))
            self.assertNotIn(fake, str(hits))
            with patch.object(checker, "ROOT", tmp):
                from contextlib import redirect_stdout
                from io import StringIO
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(checker.main(["--task-id", TASK]), 1)
            self.assertIn("STATE.yaml: contains suspected token", output.getvalue())
            self.assertNotIn(fake, output.getvalue())

    def test_unknown_scope_fails_closed(self):
        result = subprocess.run([sys.executable, 'scripts/auto_advance_runner.py', '--task-id', 'invalid'],
                                cwd=ROOT, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_subprocess_failure_cannot_become_pass(self):
        runner = module('auto_advance_runner')
        with patch.object(runner, '_run_script', return_value=(7, '{"hard_blockers": []}')):
            result = runner._run_checks()
        self.assertFalse(result['checks_passed'])
        self.assertFalse(result['authority_granted'])
        self.assertGreaterEqual(len(result['hard_blockers']), 3)

    def test_scope_preserves_non_host_secret_and_state_gates(self):
        gate = module('agent_gate')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, HUB_GOVERNANCE_TASK=TASK):
            tmp = Path(directory)
            (tmp / 'docs').mkdir()
            (tmp / 'docs' / 'bad.md').write_text('ghp_' + 'X' * 30)
            with patch.object(gate, 'ROOT', tmp):
                self.assertTrue(gate._scan_for_secrets())
                result = gate.run_gate()
                self.assertEqual(result['decision'], 'stop')
                self.assertTrue(any('STATE.yaml' in h for h in result['hard_blockers']))

    def test_finalize_never_writes_git_even_on_pass(self):
        runner = module('auto_advance_runner')
        calls = []
        def command(args):
            calls.append(args)
            return (0, '?? task.txt' if args[:2] == ['git', 'status'] else '')
        with patch.object(runner, '_run_checks', return_value={'hard_blockers': [], 'soft_warnings': []}), \
             patch.object(runner, '_run_script', return_value=(0, 'PASS')), \
             patch.object(runner, '_run_script_command', side_effect=command):
            self.assertIsNone(runner.mode_finalize_round()['git_commit'])
        self.assertTrue(all(c[1] not in {'add', 'commit', 'push', 'checkout', 'reset'} for c in calls))

    def test_selected_task_cannot_fall_back_to_legacy_state(self):
        checker = module("round_consistency_check")
        original = checker._load_yaml
        def absent_task(relative, blockers):
            value = original(relative, blockers)
            if relative == "STATE.yaml":
                value.pop("all_projects_governance", None)
            return value
        with patch.dict(os.environ, HUB_GOVERNANCE_TASK=TASK), patch.object(checker, "_load_yaml", side_effect=absent_task):
            result = checker.run_check()
        self.assertTrue(result["hard_blockers"])

    def test_secret_scanner_distinguishes_flags_from_tokens(self):
        checker = module("check_repo")
        self.assertFalse(checker._scan_secrets_in_text("example", "--task-id"))
        self.assertTrue(checker._scan_secrets_in_text("example", "sk-" + "X" * 30))

    def test_registry_counts_and_removed_local_guards(self):
        sys.path.insert(0, str(ROOT / "src"))
        from hub.services.project_registry_service import load_registry, validate_registry
        from copy import deepcopy
        registry = load_registry()
        removed = [p for p in registry["projects"] if p.get("current_state_status") == "removed_local"]
        self.assertEqual(len(registry["projects"]), 26)
        self.assertEqual({p["id"] for p in removed}, {"manga-removed-local", "game-removed-local"})
        self.assertTrue(validate_registry(registry)["valid"])
        for field, value in (("enabled", True), ("scan_enabled", True), ("external_write_allowed", True), ("watch_paths", ["STATE.yaml"])):
            broken = deepcopy(registry)
            next(p for p in broken["projects"] if p["id"] == "manga-removed-local")[field] = value
            self.assertFalse(validate_registry(broken)["valid"])
        coverage = json.loads((ROOT / "docs/reports/all-projects-governance/bootstrap/coverage.json").read_text())
        native = json.loads((ROOT / "docs/reports/all-projects-governance/bootstrap/native-projects.json").read_text())["projects"]
        covered = [a for p in coverage["projects"] for a in p["native_aliases"]] + [p["discovery_id"] for p in coverage["additional_dispositions"]]
        self.assertEqual(len(covered), len(set(covered)))
        self.assertEqual(set(covered), {p["projectId"] for p in native})
        self.assertEqual(sum(p["disposition"] == "owner_excluded" for p in coverage["additional_dispositions"]), 17)
        self.assertTrue(all("state_source" in p and "git" in p for p in coverage["additional_dispositions"]))

    def test_canonical_state_and_boot_size(self):
        import yaml
        state = yaml.safe_load((ROOT / 'STATE.yaml').read_text())
        self.assertEqual(state['metadata']['authority'], 'canonical')
        self.assertNotIn('current_work', state)
        self.assertLessEqual(sum((ROOT / p).stat().st_size for p in ('AGENTS.md', 'STATE.yaml')), 8192)

if __name__ == '__main__':
    unittest.main()
