"""Behavior checks for project governance scope; subprocesses are instrumented."""
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
        candidate = Path(os.path.abspath(text))
        try:
            parts = candidate.relative_to(Path(os.environ['HUB_TEST_REPOSITORY'])).parts
        except ValueError:
            try:
                parts = candidate.relative_to(Path.home()).parts
            except ValueError:
                parts = ()
        if parts and parts[0] in {'.cursor', '.codex'}:
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
    def test_runtime_guard_allows_worktree_under_editor_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            repo = tmp / '.codex' / 'worktrees' / 'project'
            (repo / '.cursor').mkdir(parents=True)
            (repo / 'STATE.yaml').write_text('project: fixture')
            (repo / '.cursor' / 'settings.json').write_text('{}')
            (tmp / 'sitecustomize.py').write_text(GUARD)
            env = dict(os.environ, PYTHONPATH=str(tmp), HUB_ACCESS_TRACE=str(tmp / 'trace'),
                       HUB_TEST_REPOSITORY=str(repo.resolve()), PYTHONDONTWRITEBYTECODE='1')
            result = subprocess.run([sys.executable, '-c',
                "from pathlib import Path; assert Path('STATE.yaml').read_text() == 'project: fixture'; "
                "Path('.cursor/settings.json').read_text()"],
                cwd=repo, env=env, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('excluded host path accessed', result.stderr)
            self.assertEqual((tmp / 'trace').read_text().splitlines(),
                             ['open:.cursor/settings.json'])

    def test_runner_is_read_only_and_editor_runtime_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / 'sitecustomize.py').write_text(GUARD)
            trace = tmp / 'access.txt'
            env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(tmp), *sys.path]), HUB_ACCESS_TRACE=str(trace),
                       PYTHONDONTWRITEBYTECODE='1', HUB_TEST_REPOSITORY=str(ROOT))
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
                self.assertTrue(all(path.startswith(":(literal)") for path in paths))
                self.assertEqual(paths, expected)
            self.assertTrue(all(call[1] not in {"add", "commit", "push"} for call in git_calls))
            self.assertGreaterEqual(len(Path(str(trace) + ".installed").read_text().splitlines()), 14)
            self.assertEqual(before, {p: (ROOT / p).read_bytes() for p in before})

    def test_finalize_ignores_unrelated_staged_content(self):
        runner = module("auto_advance_runner")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, HUB_GOVERNANCE_TASK=TASK):
            tmp = Path(directory)
            (tmp / "STATE.yaml").write_text("all_projects_governance:\n  task_id: " + TASK + "\n  candidate_paths: [task.txt]\n")
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

    def test_editor_names_do_not_hide_project_files(self):
        gate = module("agent_gate")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, HUB_GOVERNANCE_TASK=TASK):
            tmp = Path(directory)
            (tmp / "docs").mkdir()
            files = [tmp / "docs" / name for name in ("cursor-project.md", "codex-project.md")]
            for path in files:
                path.write_text("ghp_" + "X" * 30)
            with patch.object(gate, "ROOT", tmp):
                self.assertEqual(set(gate._iter_scan_files()), set(files))
                self.assertEqual(len(gate._scan_for_secrets()), 2)

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

    def test_candidate_scope_uses_state_without_report_or_editor_filter(self):
        runner = module("auto_advance_runner")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, HUB_GOVERNANCE_TASK=TASK):
            tmp = Path(directory)
            state = tmp / "STATE.yaml"
            state.write_text("all_projects_governance:\n  task_id: " + TASK +
                             "\n  candidate_paths: [new.txt, docs/cursor-project.md]\n")
            with patch.object(runner, "ROOT", tmp):
                self.assertEqual(runner._candidate_paths(), ["STATE.yaml", "docs/cursor-project.md", "new.txt"])
                state.write_text("all_projects_governance:\n  task_id: " + TASK + "\n")
                with self.assertRaises(ValueError):
                    runner._candidate_paths()

    def test_candidate_scope_rejects_escape_git_internals_and_symlinks(self):
        runner = module("auto_advance_runner")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, HUB_GOVERNANCE_TASK=TASK):
            tmp = Path(directory)
            outside = tmp / "outside.txt"
            outside.write_text("never opened")
            (tmp / "alias.txt").symlink_to(outside)
            for paths in [[], ["../outside.txt"], ["/absolute"], [".git/config"], ["alias.txt"], ["a//b"]]:
                import yaml
                (tmp / "STATE.yaml").write_text(yaml.safe_dump({"all_projects_governance": {
                    "task_id": TASK, "candidate_paths": paths}}))
                original = Path.read_text
                def guarded(path, *args, **kwargs):
                    self.assertNotEqual(path, outside)
                    self.assertNotEqual(path, tmp / "alias.txt")
                    return original(path, *args, **kwargs)
                with patch.object(runner, "ROOT", tmp), patch.object(Path, "read_text", guarded):
                    with self.assertRaises(ValueError):
                        runner._candidate_paths()

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
                value["current_work"] = {"status": "ACTIVE", "next_action": "Unrelated task"}
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
        self.assertTrue(validate_registry(registry, check_paths=False)["valid"])
        for field, value in (("enabled", True), ("scan_enabled", True), ("external_write_allowed", True), ("watch_paths", ["STATE.yaml"])):
            broken = deepcopy(registry)
            next(p for p in broken["projects"] if p["id"] == "manga-removed-local")[field] = value
            self.assertFalse(validate_registry(broken, check_paths=False)["valid"])
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
        self.assertEqual(state['all_projects_governance']['task_id'], TASK)
        self.assertLessEqual(sum((ROOT / p).stat().st_size for p in ('AGENTS.md', 'STATE.yaml')), 8192)

if __name__ == '__main__':
    unittest.main()
