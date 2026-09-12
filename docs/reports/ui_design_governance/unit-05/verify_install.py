"""Verify only the registered Hub delivery overlay on its existing Git base.

Reader: Root and independent reviewers. Run from the Hub; all generated files and
fixture effects stay in one automatically removed temporary directory. The named
storage checks are read-only. No Git mutation, dependency install or project scan.
"""
from pathlib import Path
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[4]
REPORT = ROOT / "docs/reports/ui_design_governance/unit-05"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def main():
    baseline = json.loads((REPORT / "baseline.json").read_text())
    scope = json.loads((REPORT / "provenance.json").read_text())["delivery_paths"]
    tc4 = json.loads((ROOT / "docs/reports/ui_design_governance/unit-04/candidate-v1.json").read_text())
    results = []
    archive = subprocess.check_output(["git", "archive", "--format=tar", baseline["head"]], cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix="hub-tc5-install-") as directory:
        target = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for member in tar.getmembers():
                path = Path(member.name)
                assert not path.is_absolute() and ".." not in path.parts
                assert member.isfile() or member.isdir()
                assert not path.name.startswith(".env") or path.name == ".env.example"
            tar.extractall(target)

        def overlay(paths):
            for name in paths:
                rel = Path(name)
                assert not rel.is_absolute() and ".." not in rel.parts
                source = ROOT / rel
                assert source.is_file() and not source.is_symlink()
                destination = target / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)

        def check(label, arguments):
            run = subprocess.run([sys.executable, *arguments], cwd=target, env=ENV,
                                 text=True, capture_output=True, timeout=120)
            record = {"label": label, "argv": arguments, "exit_code": run.returncode,
                      "stdout": run.stdout, "stderr": run.stderr}
            if label == "TC4 real read-only API" and run.returncode == 0:
                payload = json.loads(run.stdout)
                payload.pop("projects")
                record["stdout"] = payload
            results.append(record)
            print(json.dumps({"label": label, "exit_code": run.returncode}), file=sys.stderr, flush=True)
            return run.returncode == 0

        overlay(scope)
        check("baseline bootstrap", ["scripts/bootstrap.py"])
        check("baseline repository", ["scripts/check_repo.py"])
        check("baseline registry", ["scripts/check_registry.py"])
        check("baseline round consistency", ["scripts/round_consistency_check.py"])
        check("baseline accepted source tests", ["-W", "error::ResourceWarning", "-m", "unittest", "discover", "-s", "tests", "-p", "test_hub*.py"])
        check("baseline management tests", ["-c", "import runpy; m=runpy.run_path('tests/test_control_plane_v2.py'); fs=[f for n,f in m.items() if n.startswith('test_') and callable(f)]; [f() for f in fs]; print(len(fs), 'management tests passed')"])
        for name, digest in tc4["semantic"]["files"].items():
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
        overlay(tc4["semantic"]["files"])
        check("combined TC1-TC4 tests", ["-W", "error::ResourceWarning", "-m", "unittest", "discover", "-s", "tests", "-p", "test_hub*.py"])
        check("TC4 real read-only API", ["docs/reports/ui_design_governance/unit-04/readback.py"])
    print(json.dumps({"schema_version": "1.0", "base_commit": baseline["head"],
                      "baseline_paths": len(scope), "tc4_candidate": tc4["sha256"],
                      "checks": results, "temporary_directory_removed": not target.exists(),
                      "git_mutations": 0, "external_writes": 0}, ensure_ascii=False, indent=2))
    return int(any(item["exit_code"] for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
