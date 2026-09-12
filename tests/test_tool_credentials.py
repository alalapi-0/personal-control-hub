"""Isolated credential transport tests; no real Keychain reads or writes."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("tool_credentials", Path(__file__).resolve().parents[1] / "scripts/tool_credentials.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class TransportTests(unittest.TestCase):
    entry = {"id": "fixture", "auth_kind": "macos_keychain", "service": "fixture-service", "account": "fixture-account", "runtime_env": "FIGMA_API_KEY"}

    def backend(self, value=b"fixture-credential", error=None):
        result = mock.Mock()
        result.get.side_effect = error
        result.get.return_value = value
        return result

    def invoke(self, argv, backend):
        out, err, execute = io.StringIO(), io.StringIO(), mock.Mock()
        with mock.patch.object(M, "load_entry", return_value=self.entry), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = M.main(argv, backend=backend, execute=execute)
        return code, out.getvalue(), err.getvalue(), execute

    def test_exact_argument_forwarding_and_environment_preservation(self):
        args = ["/fixture/program", "argument with spaces", "literal$(no-shell)", "line\nbreak", "--flag"]
        with mock.patch.dict(os.environ, {"FIGMA_API_KEY": "obsolete", "PRESERVED": "same"}, clear=True):
            code, out, err, execute = self.invoke(["run", "fixture", "--", *args], self.backend())
        self.assertEqual((code, out, err), (0, "", ""))
        execute.assert_called_once_with(args[0], args, {"FIGMA_API_KEY": "fixture-credential", "PRESERVED": "same"})

    def test_missing_locked_denied_empty_and_invalid_never_launch(self):
        cases = [self.backend(error=M.CredentialError("Keychain retrieval failed (OSStatus %d)" % status)) for status in (-25300, -25308, -25293)]
        cases += [self.backend(v) for v in (b"", b" \n", b"bad\x00value", b"\xff", None)]
        for backend in cases:
            with self.subTest(backend=backend), mock.patch.dict(os.environ, {"FIGMA_API_KEY": "inherited-fallback"}):
                code, out, err, execute = self.invoke(["run", "fixture", "--", "/fixture/program"], backend)
            self.assertEqual(code, 2); execute.assert_not_called()
            self.assertNotIn("inherited-fallback", out + err)
            self.assertNotIn("fixture-credential", out + err)

    def test_check_returns_only_nonsecret_status(self):
        code, out, err, execute = self.invoke(["check", "fixture"], self.backend())
        self.assertEqual(code, 0); execute.assert_not_called()
        self.assertEqual(json.loads(out), {"credential_id": "fixture", "available": True, "value_returned": False})
        self.assertEqual(err, "")

    def test_operating_system_error_is_redacted(self):
        code, out, err, execute = self.invoke(["run", "fixture", "--", "/fixture/program"], self.backend(error=OSError("fixture-credential")))
        self.assertEqual(code, 2); execute.assert_not_called()
        self.assertNotIn("fixture-credential", out + err)

    def test_bad_command_shape_does_not_read_keychain(self):
        for args in (["run", "fixture"], ["check", "fixture", "unexpected"]):
            backend = self.backend()
            code, _, _, execute = self.invoke(args, backend)
            self.assertEqual(code, 2); backend.get.assert_not_called(); execute.assert_not_called()

    def test_index_rejects_duplicates_provider_routes_and_core_env_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            p = Path(temporary) / "index.json"
            for entries in ([self.entry, self.entry], [dict(self.entry, auth_kind="managed_oauth")], [dict(self.entry, runtime_env="PATH")], []):
                p.write_text(json.dumps({"schema_version": "1.0", "credentials": entries}))
                with self.assertRaises(M.CredentialError):
                    M.load_entry("fixture", p)
            p.write_text(json.dumps({"schema_version": "1.0", "credentials": [self.entry]}))
            self.assertEqual(M.load_entry("fixture", p), self.entry)


if __name__ == "__main__":
    unittest.main()
