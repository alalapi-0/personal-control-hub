"""Fail-closed owner credential projection and local registration, synthetic data only."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import getpass
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import hub.owner_auth as owner_auth
from hub.owner_auth import OwnerAuth


def load_registration():
    spec = importlib.util.spec_from_file_location('synthetic_owner_registration',
        Path(__file__).resolve().parents[1] / 'scripts/hub_owner_credential.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'credentials'
        self.directory.mkdir(mode=0o700)
        self.file = self.directory / 'pch-owner-token'
        self.proof = 'synthetic-owner-proof-' + 'q' * 40
        self.write(self.proof.encode())
        self.environment = patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': str(self.directory),
                                                  'HUB_OWNER_TOKEN': 'environment-only-' + 'e' * 40})
        self.environment.start(); self.addCleanup(self.environment.stop)

    def write(self, raw):
        self.file.write_bytes(raw)
        self.file.chmod(0o600)

    def read(self):
        return owner_auth.SystemdCredentialProvider()()

    def test_private_projection_and_explicit_mode_do_not_fall_back_to_environment(self):
        self.assertEqual(self.read(), self.proof)
        explicit = OwnerAuth(provider=owner_auth.SystemdCredentialProvider())
        self.assertTrue(explicit.configured)
        self.assertEqual(explicit.verify(self.proof), explicit.version)
        self.file.unlink()
        self.assertIsNone(self.read())
        self.assertFalse(explicit.configured)
        self.assertTrue(OwnerAuth().configured)
        with patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': ''}):
            self.assertIsNone(self.read())

    def test_character_and_byte_boundaries_without_stripping(self):
        accepted = ['x' * 32, 'x' * 512, '界' * 512, '😀' * 512, ' ' + 'x' * 31]
        rejected = [b'', b'x' * 31, b'x' * 513, b'x' * 2049,
                    b'x' * 32 + b'\n', b'x' * 32 + b'\r', b'x' * 32 + b'\x00',
                    ('x' * 32 + '\u2028').encode(), b'\xff' * 40]
        for value in accepted:
            with self.subTest(accepted_chars=len(value)):
                self.write(value.encode()); self.assertEqual(self.read(), value)
        for raw in rejected:
            with self.subTest(rejected_bytes=len(raw)):
                self.write(raw); self.assertIsNone(self.read())

    def test_file_type_links_permissions_and_owner_fail_closed(self):
        self.file.chmod(0o640); self.assertIsNone(self.read())
        self.file.chmod(0o600)
        linked = self.directory / 'second-link'
        os.link(self.file, linked)
        self.assertIsNone(self.read())
        linked.unlink(); self.file.unlink()
        target = self.root / 'target'; target.write_text(self.proof); target.chmod(0o600)
        self.file.symlink_to(target); self.assertIsNone(self.read()); self.file.unlink()
        self.file.mkdir(); self.assertIsNone(self.read()); self.file.rmdir()
        os.mkfifo(self.file, 0o600)
        self.assertIsNone(self.read()); self.file.unlink()
        self.write(self.proof.encode())
        with patch.object(owner_auth.os, 'geteuid', return_value=os.geteuid() + 1):
            self.assertIsNone(self.read())

    def test_symlinked_credential_directory_and_ancestor_are_denied(self):
        alias = self.root / 'alias'; alias.symlink_to(self.directory, target_is_directory=True)
        with patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': str(alias)}):
            self.assertIsNone(self.read())
        ancestor = self.root / 'ancestor'; ancestor.symlink_to(self.root, target_is_directory=True)
        with patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': str(ancestor / 'credentials')}):
            self.assertIsNone(self.read())

    def test_file_replacement_and_permission_change_during_read_are_denied(self):
        real_read = os.read
        for mutation in ['replace', 'chmod']:
            with self.subTest(mutation=mutation):
                self.file.unlink(); self.write(self.proof.encode())
                changed = False
                def racing_read(fd, count):
                    nonlocal changed
                    result = real_read(fd, count)
                    if not changed and result:
                        changed = True
                        if mutation == 'replace':
                            alternate = self.directory / 'replacement'
                            alternate.write_text(self.proof); alternate.chmod(0o600)
                            alternate.replace(self.file)
                        else:
                            self.file.chmod(0o644)
                    return result
                with patch.object(owner_auth.os, 'read', side_effect=racing_read):
                    self.assertIsNone(self.read())
                self.assertTrue(changed)


    def test_directory_rebinding_and_directory_permission_change_during_read_are_denied(self):
        real_read = os.read
        for mutation in ['rebind', 'chmod']:
            with self.subTest(mutation=mutation):
                changed = False
                moved = self.root / 'moved-credentials'
                def racing_read(fd, count):
                    nonlocal changed
                    result = real_read(fd, count)
                    if not changed and result:
                        changed = True
                        if mutation == 'rebind':
                            self.directory.rename(moved)
                            self.directory.mkdir(mode=0o700)
                            self.write(self.proof.encode())
                        else:
                            self.directory.chmod(0o755)
                    return result
                with patch.object(owner_auth.os, 'read', side_effect=racing_read):
                    self.assertIsNone(self.read())
                self.assertTrue(changed)
                if mutation == 'rebind':
                    self.file.unlink(); self.directory.rmdir(); moved.rename(self.directory)
                else:
                    self.directory.chmod(0o700)


    def test_in_place_mutation_append_and_truncate_during_read_are_denied(self):
        real_read = os.read
        for mutation in ['rewrite', 'append', 'truncate']:
            with self.subTest(mutation=mutation):
                self.write(self.proof.encode())
                changed = False
                def racing_read(fd, count):
                    nonlocal changed
                    result = real_read(fd, count)
                    if not changed and result:
                        changed = True
                        if mutation == 'rewrite':
                            with self.file.open('r+b') as stream:
                                stream.write(b'Z')
                        elif mutation == 'append':
                            with self.file.open('ab') as stream:
                                stream.write(b'Z')
                        else:
                            with self.file.open('r+b') as stream:
                                stream.truncate(32)
                    return result
                with patch.object(owner_auth.os, 'read', side_effect=racing_read):
                    self.assertIsNone(self.read())
                self.assertTrue(changed)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.module = load_registration()
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / 'credstore.encrypted'
        self.proof = 'private-synthetic-value-' + 'p' * 40
        self.ciphertext = b'ENC:synthetic-ciphertext'

    def assert_safe_error(self, call):
        with self.assertRaises(self.module.OwnerCredentialError) as raised:
            call()
        error = raised.exception
        self.assertRegex(error.code, r'^[A-Z][A-Z0-9_]+$')
        self.assertNotIn(self.proof, str(error))
        return error

    def test_cli_invalid_arguments_never_echo_accidentally_supplied_proof(self):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output), \
             patch.object(self.module.subprocess, 'run', side_effect=AssertionError('provider effect')):
            with self.assertRaises(SystemExit) as raised:
                self.module.main(['register','--proof',self.proof])
        self.assertEqual(raised.exception.code,2)
        self.assertNotIn(self.proof,output.getvalue())
        self.assertIn('OWNER_CREDENTIAL_ARGUMENT_REJECTED',output.getvalue())

    def interactive(self, *, approved=True, inputs=None, provider=None):
        stack = ExitStack()
        stack.enter_context(patch.object(self.module.sys.stdin, 'isatty', return_value=True))
        stack.enter_context(patch.object(self.module.sys.stderr, 'isatty', return_value=True))
        stack.enter_context(patch('builtins.input', return_value=(
            'AUTHORIZE HOST CREDENTIAL SETUP' if approved else 'not approved')))
        stack.enter_context(patch.object(self.module.getpass, 'getpass', side_effect=(
            inputs if inputs is not None else [self.proof, self.proof])))
        stack.enter_context(patch.object(self.module, '_credential_directory', return_value=self.store))
        def roundtrip(action, raw):
            if action == 'encrypt':
                roundtrip.latest = raw
                return self.ciphertext
            if action == 'decrypt':
                return roundtrip.latest
            raise AssertionError('unexpected provider action')
        stack.provider = stack.enter_context(patch.object(self.module, '_run_creds',
            side_effect=provider if provider is not None else roundtrip))
        return stack

    def test_prepare_is_read_only_and_preserves_code_data_host_and_port(self):
        code = self.root / 'candidate code%$\\"'
        data = self.root / 'formal data%$\\"'
        python = self.root / 'Python Interpreter%$\\"'
        code.mkdir(); data.mkdir(); python.write_bytes(b'synthetic executable')
        (code / 'scripts').mkdir(); (code / 'src' / 'hub').mkdir(parents=True)
        candidate = Path(__file__).resolve().parents[1]
        (code / 'scripts' / 'hub_server.py').write_bytes((candidate / 'scripts' / 'hub_server.py').read_bytes())
        (code / 'src' / 'hub' / 'owner_auth.py').write_bytes((candidate / 'src' / 'hub' / 'owner_auth.py').read_bytes())
        with patch.object(self.module.subprocess, 'run', side_effect=AssertionError('provider effect')), \
             patch.object(self.module.getpass, 'getpass', side_effect=AssertionError('credential prompt')), \
             patch.object(Path, 'mkdir', side_effect=AssertionError('write effect')), \
             patch.object(Path, 'write_text', side_effect=AssertionError('write effect')):
            prepared = self.module.prepare(code_root=code, data_root=data, python=python,
                                           host='127.0.0.1', port=8766)
        self.assertFalse(prepared['ready_to_install'])
        self.assertEqual(set(prepared['code_sha256']), {'scripts/hub_server.py', 'src/hub/owner_auth.py'})
        for value in prepared['code_sha256'].values():
            self.assertRegex(value, r'^[0-9a-f]{64}$')
        template = prepared['drop_in']
        self.assertIn('LoadCredentialEncrypted=pch-owner-token:', template)
        self.assertIn('UnsetEnvironment=HUB_OWNER_TOKEN', template)
        self.assertIn('ExecStart=\n', template)
        self.assertIn('--owner-auth', template)
        self.assertIn('systemd-credential', template)
        self.assertIn('--root', template); self.assertIn('--host', template); self.assertIn('8766', template)
        self.assertIn('%%', template); self.assertIn('$$', template)
        self.assertIn('\\\\', template); self.assertIn('\\"', template)
        self.assertFalse(self.store.exists())

    def test_prepare_rejects_injected_newlines_and_invalid_port(self):
        good = dict(code_root=self.root, data_root=self.root, python=Path('/usr/bin/python3'))
        for key in ['code_root', 'data_root', 'python', 'host']:
            values = dict(good); values[key] = 'bad\nExecStart=other' if key == 'host' else Path('bad\nother')
            with self.subTest(key=key): self.assert_safe_error(lambda: self.module.prepare(**values))
        for port in [-1, 0, 65536, '8766\nExecStart=other']:
            with self.subTest(port=port):
                self.assert_safe_error(lambda: self.module.prepare(**good, port=port))

    def test_prepare_rejects_legacy_code_and_aliased_data_root(self):
        code = self.root / 'code'; code.mkdir()
        (code / 'scripts').mkdir(); (code / 'src' / 'hub').mkdir(parents=True)
        (code / 'scripts' / 'hub_server.py').write_text('legacy server without credential mode')
        (code / 'src' / 'hub' / 'owner_auth.py').write_text('legacy environment auth')
        self.assert_safe_error(lambda: self.module.prepare(code_root=code, data_root=self.root,
                                                           python=Path(sys.executable)))
        alias = self.root / 'data-alias'; alias.symlink_to(self.root, target_is_directory=True)
        # Any failure is acceptable here: the aliased data path must not yield an installable plan.
        with self.assertRaises((self.module.OwnerCredentialError, OSError, ValueError)):
            self.module.prepare(code_root=code, data_root=alias, python=Path(sys.executable))

    def test_non_tty_and_missing_approval_do_not_prompt_call_provider_or_write(self):
        for stream in ['stdin', 'stderr']:
            with self.subTest(stream=stream), self.interactive() as context:
                with patch.object(getattr(self.module.sys, stream), 'isatty', return_value=False), \
                     patch.object(self.module.getpass, 'getpass', side_effect=AssertionError('prompt')):
                    self.assert_safe_error(lambda: self.module.register(allow_host_key_initialization=True))
                context.provider.assert_not_called()
        with self.interactive() as context:
            self.assert_safe_error(lambda: self.module.register())
            context.provider.assert_not_called()
        with self.interactive(approved=False) as context:
            self.assert_safe_error(lambda: self.module.register(allow_host_key_initialization=True))
            context.provider.assert_not_called()
        self.assertFalse(self.store.exists())

    def test_qualification_occurs_before_owner_prompt_and_mismatch_stops_registration(self):
        def corrupt_roundtrip(action, raw):
            return b'ciphertext' if action == 'encrypt' else b'not-the-synthetic-proof'
        with self.interactive(provider=corrupt_roundtrip), \
             patch.object(self.module.getpass, 'getpass', side_effect=AssertionError('premature prompt')):
            self.assert_safe_error(lambda: self.module.register(allow_host_key_initialization=True))
        self.assertFalse(self.store.exists())

    def test_success_stores_only_encrypted_bytes_with_private_permissions(self):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output), self.interactive() as context:
            result = self.module.register(allow_host_key_initialization=True)
        self.assertNotIn(self.proof, output.getvalue() + repr(result))
        files = list(self.store.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, 'pch-owner-token')
        self.assertEqual(files[0].read_bytes(), self.ciphertext)
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        actions = [call.args[0] for call in context.provider.call_args_list]
        self.assertGreaterEqual(actions.count('encrypt'), 2)
        self.assertIn('decrypt', actions)
        self.assertNotEqual(context.provider.call_args_list[0].args[1], self.proof.encode())

    def test_mismatch_invalid_control_and_cancel_leave_no_final_file(self):
        for inputs in [[self.proof, self.proof + 'different'], ['short', 'short'],
                       ['x' * 513, 'x' * 513], ['x' * 32 + '\n'] * 2,
                       [EOFError()], [KeyboardInterrupt()]]:
            with self.subTest(case=repr(inputs)[:30]), self.interactive(inputs=inputs):
                self.assert_safe_error(lambda: self.module.register(allow_host_key_initialization=True))
            self.assertFalse((self.store / 'pch-owner-token').exists())
        def warning_prompt(*args, **kwargs):
            warnings.warn('echo unavailable', getpass.GetPassWarning)
            return self.proof
        with self.interactive(), patch.object(self.module.getpass, 'getpass', side_effect=warning_prompt):
            self.assert_safe_error(lambda: self.module.register(allow_host_key_initialization=True))
        self.assertFalse((self.store / 'pch-owner-token').exists())

    def test_existing_file_or_symlink_is_never_overwritten(self):
        self.store.mkdir(mode=0o700)
        target = self.store / 'pch-owner-token'
        target.write_bytes(b'existing-encrypted-content'); target.chmod(0o600)
        with self.interactive():
            self.assert_safe_error(lambda: self.module.register(allow_host_key_initialization=True))
        self.assertEqual(target.read_bytes(), b'existing-encrypted-content')
        target.unlink()
        other = self.root / 'protected'; other.write_bytes(b'protected')
        target.symlink_to(other)
        with self.interactive():
            self.assert_safe_error(lambda: self.module.register(allow_host_key_initialization=True))
        self.assertTrue(target.is_symlink()); self.assertEqual(other.read_bytes(), b'protected')

    def test_provider_failure_and_empty_ciphertext_have_no_final_file_or_secret_log(self):
        for failure in ['error', 'empty', 'oversize']:
            def provider(action, raw):
                if failure == 'error':
                    raise self.module.OwnerCredentialError('PROVIDER_UNAVAILABLE')
                return b'' if failure == 'empty' else b'x' * (1024 * 1024 + 1)
            output = io.StringIO()
            with self.subTest(failure=failure), redirect_stdout(output), redirect_stderr(output), \
                 self.interactive(provider=provider):
                self.assert_safe_error(lambda: self.module.register(allow_host_key_initialization=True))
            self.assertNotIn(self.proof, output.getvalue())
            self.assertFalse((self.store / 'pch-owner-token').exists())

    def test_unsafe_store_paths_fail_before_provider_or_owner_input(self):
        parent = self.root / 'parent'; parent.mkdir(mode=0o700)
        store = parent / 'store'
        alias = self.root / 'parent-alias'; alias.symlink_to(parent, target_is_directory=True)
        for case in ['parent_alias', 'store_alias', 'writable_parent', 'public_store']:
            with self.subTest(case=case):
                selected = store
                if case == 'parent_alias':
                    selected = alias / 'store'
                elif case == 'store_alias':
                    target = parent / 'target'; target.mkdir(mode=0o700)
                    store.symlink_to(target, target_is_directory=True)
                elif case == 'writable_parent':
                    parent.chmod(0o777)
                else:
                    store.mkdir(mode=0o755)
                with self.interactive() as context, \
                     patch.object(self.module, '_credential_directory', return_value=selected), \
                     patch.object(self.module.getpass, 'getpass', side_effect=AssertionError('owner prompt')), \
                     patch('builtins.input', side_effect=AssertionError('confirmation prompt')):
                    with self.assertRaises((self.module.OwnerCredentialError, OSError, ValueError)):
                        self.module.register(allow_host_key_initialization=True)
                    context.provider.assert_not_called()
                if store.is_symlink():
                    store.unlink(); target.rmdir()
                elif store.exists():
                    store.rmdir()
                parent.chmod(0o700)
                self.assertEqual(list(parent.iterdir()), [])

    def test_write_and_fsync_failures_remove_only_new_ciphertext(self):
        real_write, real_fsync = os.write, os.fsync
        for failure in ['partial_write', 'file_fsync', 'directory_fsync']:
            with self.subTest(failure=failure):
                wrote = False
                def failing_write(fd, raw):
                    nonlocal wrote
                    if failure == 'partial_write':
                        if wrote:
                            raise OSError('synthetic write failure')
                        wrote = True
                        return real_write(fd, raw[:3])
                    return real_write(fd, raw)
                def failing_fsync(fd):
                    directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                    if (failure == 'file_fsync' and not directory) or (failure == 'directory_fsync' and directory):
                        raise OSError('synthetic fsync failure')
                    return real_fsync(fd)
                with self.interactive(), patch.object(self.module.os, 'write', side_effect=failing_write), \
                     patch.object(self.module.os, 'fsync', side_effect=failing_fsync):
                    with self.assertRaises((self.module.OwnerCredentialError, OSError)):
                        self.module.register(allow_host_key_initialization=True)
                self.assertFalse((self.store / 'pch-owner-token').exists())
                self.assertEqual(list(self.store.iterdir()), [])
        protected = self.root / 'protected-ciphertext'; protected.write_bytes(b'preserved ciphertext')
        target = self.store / 'pch-owner-token'
        for existing_kind in ['regular', 'symlink']:
            with self.subTest(existing_kind=existing_kind):
                if existing_kind == 'regular':
                    target.write_bytes(b'pre-existing encrypted data'); target.chmod(0o600)
                else:
                    target.symlink_to(protected)
                with self.interactive() as context, \
                     patch.object(self.module.os, 'write', side_effect=AssertionError('must not write')), \
                     patch.object(self.module.os, 'fsync', side_effect=AssertionError('must not sync')):
                    with self.assertRaises((self.module.OwnerCredentialError, OSError)):
                        self.module.register(allow_host_key_initialization=True)
                    context.provider.assert_not_called()
                self.assertEqual(protected.read_bytes(), b'preserved ciphertext')
                if existing_kind == 'regular':
                    self.assertEqual(target.read_bytes(), b'pre-existing encrypted data')
                else:
                    self.assertTrue(target.is_symlink())
                target.unlink()

    def test_prepare_rejects_intermediate_alias_without_reading_aliased_code(self):
        code = self.root / 'aliased-code'; code.mkdir()
        outside = self.root / 'outside-code'; outside.mkdir()
        (outside / 'hub_server.py').write_bytes(b'private synthetic file must not be read')
        (code / 'scripts').symlink_to(outside, target_is_directory=True)
        with patch.object(self.module.os, 'read', side_effect=AssertionError('aliased source read')):
            with self.assertRaises((self.module.OwnerCredentialError, OSError, ValueError)):
                self.module.prepare(code_root=code, data_root=self.root, python=Path(sys.executable))
        self.assertEqual((outside / 'hub_server.py').read_bytes(), b'private synthetic file must not be read')

    def test_prepare_rejects_source_replacement_during_read(self):
        code = self.root / 'racing-code'; code.mkdir()
        (code / 'scripts').mkdir(); (code / 'src' / 'hub').mkdir(parents=True)
        candidate = Path(__file__).resolve().parents[1]
        source = code / 'scripts' / 'hub_server.py'
        source.write_bytes((candidate / 'scripts' / 'hub_server.py').read_bytes())
        (code / 'src' / 'hub' / 'owner_auth.py').write_bytes((candidate / 'src' / 'hub' / 'owner_auth.py').read_bytes())
        real_read = os.read
        changed = False
        def replacing_read(fd, count):
            nonlocal changed
            raw = real_read(fd, count)
            if raw and not changed:
                changed = True
                replacement = source.with_name('replacement.py')
                replacement.write_bytes(source.read_bytes())
                replacement.replace(source)
            return raw
        with patch.object(self.module.os, 'read', side_effect=replacing_read):
            with self.assertRaises((self.module.OwnerCredentialError, OSError, ValueError)):
                self.module.prepare(code_root=code, data_root=self.root, python=Path(sys.executable))
        self.assertTrue(changed)

    def test_provider_subprocess_has_fixed_noninteractive_arguments_and_memory_input(self):
        raw = self.proof.encode()
        with patch.object(self.module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'encrypted', b'')) as run:
            self.assertEqual(self.module._run_creds('encrypt', raw), b'encrypted')
        args, kwargs = run.call_args
        command = args[0]
        self.assertEqual(command[0], '/usr/bin/systemd-creds')
        for flag in ['--user', '--no-ask-password', '--with-key=host', '--name=pch-owner-token']:
            self.assertIn(flag, command)
        self.assertIn('encrypt', command)
        self.assertEqual(kwargs['input'], raw)
        self.assertNotIn(self.proof, repr(command))
        self.assertNotIn(self.proof, repr(kwargs.get('env', {})))
        self.assertFalse(kwargs.get('shell', False))
        self.assertNotIn('--with-key=null', command)
        with patch.object(self.module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', raw)):
            self.assert_safe_error(lambda: self.module._run_creds('encrypt', raw))
        for failure in [OSError(self.proof), subprocess.TimeoutExpired('synthetic', 15, stderr=raw)]:
            with patch.object(self.module.subprocess, 'run', side_effect=failure):
                self.assert_safe_error(lambda: self.module._run_creds('encrypt', raw))
        with patch.object(self.module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, raw, b'')) as run:
            self.assertEqual(self.module._run_creds('decrypt', b'ciphertext'), raw)
        self.assertIn('decrypt', run.call_args.args[0])
        self.assertNotIn('--with-key=host', run.call_args.args[0])
        with patch.object(self.module.subprocess, 'run') as run:
            self.assert_safe_error(lambda: self.module._run_creds('unsupported', raw))
            run.assert_not_called()



if __name__ == '__main__':
    unittest.main()
