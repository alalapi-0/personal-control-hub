#!/usr/bin/env python3
"""Prepare an Owner service drop-in, or register an encrypted proof in a local TTY.

Preparation performs no credential, provider, service, or filesystem writes.
Registration is an explicit human action: host-key initialization may be needed.
Neither command installs a unit, restarts Hub, or enables task execution.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import warnings

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT / 'src'))
from hub.owner_auth import (OWNER_CREDENTIAL_NAME, credential_directory,
                            private_directory, valid_projected_proof, _file_identity)


class OwnerCredentialError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo accidental proof material supplied as an invalid argument.
        self.exit(2, '{"status":"unavailable","code":"OWNER_CREDENTIAL_ARGUMENT_REJECTED"}\n')


def _argument(value):
    value = str(value)
    if not value or any(not c.isprintable() for c in value):
        raise OwnerCredentialError('PREPARATION_ARGUMENT_REJECTED')
    # systemd command-line syntax, not shell quoting. Suppress both expansions.
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def prepare(*, code_root, data_root, python, host='127.0.0.1', port=8766):
    code_root, data_root, python = map(Path, (code_root, data_root, python))
    for path in (code_root, data_root, python):
        _argument(path)
        if not path.is_absolute() or '..' in path.parts:
            raise OwnerCredentialError('PREPARATION_PATH_REJECTED')
    for path in (code_root, data_root):
        with credential_directory(path):
            pass
    if not python.is_file() or host != '127.0.0.1' or type(port) is not int or not 1 <= port <= 65535:
        raise OwnerCredentialError('PREPARATION_ARGUMENT_REJECTED')
    files = {'scripts/hub_server.py': '--owner-auth',
             'src/hub/owner_auth.py': 'SystemdCredentialProvider'}
    hashes = {}
    for name, marker in files.items():
        path = code_root / name
        with credential_directory(path.parent) as (parent, check):
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=parent)
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 1024 * 1024:
                    raise OwnerCredentialError('OWNER_CODE_NOT_READY')
                raw = b''
                while len(raw) <= 1024 * 1024:
                    part = os.read(descriptor, 1024 * 1024 + 1 - len(raw))
                    if not part:
                        break
                    raw += part
                bound = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                check()
                if (len(raw) != before.st_size or _file_identity(before) != _file_identity(bound)
                        or _file_identity(before) != _file_identity(os.fstat(descriptor))):
                    raise OwnerCredentialError('OWNER_CODE_CHANGED')
            finally:
                os.close(descriptor)
        if marker.encode() not in raw or b'SystemdCredentialProvider' not in raw:
            raise OwnerCredentialError('OWNER_CODE_NOT_READY')
        hashes[name] = hashlib.sha256(raw).hexdigest()
    argv = [python, code_root / 'scripts/hub_server.py', '--root', data_root,
            '--host', host, '--port', port, '--owner-auth=systemd-credential']
    drop_in = ('[Service]\n'
               'LoadCredentialEncrypted=pch-owner-token:%h/.config/credstore.encrypted/pch-owner-token\n'
               'UnsetEnvironment=HUB_OWNER_TOKEN\n'
               'ExecStart=\nExecStart=' + ' '.join(map(_argument, argv)) + '\n')
    return {'status': 'prepared', 'ready_to_install': False, 'credential_name': OWNER_CREDENTIAL_NAME,
            'code_sha256': hashes, 'data_root': str(data_root), 'host': host, 'port': port,
            'drop_in': drop_in,
            'remaining': ['Review exact code and data/ledger migration with service write ownership.',
                          'Owner must qualify the OS provider and register the proof in a local TTY.',
                          'Approve the exact service change and rollback before installation or restart.']}


def _credential_directory():
    return Path.home() / '.config/credstore.encrypted'


def _run_creds(action, raw):
    if action not in {'encrypt', 'decrypt'}:
        raise OwnerCredentialError('PROVIDER_ACTION_REJECTED')
    argv = ['/usr/bin/systemd-creds', '--user', '--no-ask-password',
            '--name=' + OWNER_CREDENTIAL_NAME]
    if action == 'encrypt':
        argv.append('--with-key=host')
    argv.extend([action, '-', '-'])
    # Secrets are only stdin bytes; no inherited proof, session or provider env.
    runtime = '/run/user/' + str(os.geteuid())
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                   'HOME': str(Path.home()), 'XDG_RUNTIME_DIR': runtime,
                   'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + runtime + '/bus'}
    try:
        result = subprocess.run(argv, input=raw, capture_output=True, env=environment,
                                timeout=15, check=False)
        limit = 16384 if action == 'encrypt' else 2048
        if result.returncode != 0 or not isinstance(result.stdout, bytes) or not 0 < len(result.stdout) <= limit:
            raise OwnerCredentialError('OS_CREDENTIAL_PROVIDER_UNAVAILABLE')
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        # Provider stderr and exception strings may contain sensitive input.
        raise OwnerCredentialError('OS_CREDENTIAL_PROVIDER_UNAVAILABLE') from None


def _check_absent(directory):
    # Validate the existing store parent before any OS provider operation.
    with credential_directory(directory.parent) as (parent, check):
        metadata = os.fstat(parent)
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise OwnerCredentialError('ENCRYPTED_STORE_DIRECTORY_REJECTED')
        try:
            descriptor = os.open(directory.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        except FileNotFoundError:
            check()
            return
        try:
            if not private_directory(os.fstat(descriptor)):
                raise OwnerCredentialError('ENCRYPTED_STORE_DIRECTORY_REJECTED')
            try:
                os.stat(OWNER_CREDENTIAL_NAME, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                check()
                return
            raise OwnerCredentialError('ENCRYPTED_CREDENTIAL_ALREADY_EXISTS')
        finally:
            os.close(descriptor)


def register(*, allow_host_key_initialization=False):
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise OwnerCredentialError('LOCAL_TTY_REQUIRED')
    if not allow_host_key_initialization:
        raise OwnerCredentialError('HOST_PROVIDER_APPROVAL_REQUIRED')
    directory = _credential_directory()
    _check_absent(directory)
    try:
        confirmation = input('Host encryption may initialize an OS-wide key. Type AUTHORIZE HOST CREDENTIAL SETUP: ')
    except (EOFError, KeyboardInterrupt):
        raise OwnerCredentialError('LOCAL_TTY_INPUT_CANCELLED') from None
    if confirmation != 'AUTHORIZE HOST CREDENTIAL SETUP':
        raise OwnerCredentialError('HOST_PROVIDER_APPROVAL_REQUIRED')
    # Qualification is real only when the owner explicitly runs this TTY action.
    probe = secrets.token_urlsafe(40).encode('utf-8')
    if not hmac.compare_digest(_run_creds('decrypt', _run_creds('encrypt', probe)), probe):
        raise OwnerCredentialError('OS_CREDENTIAL_PROVIDER_UNAVAILABLE')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            proof = getpass.getpass('Owner proof (32–512 printable characters): ')
            repeated = getpass.getpass('Repeat Owner proof: ')
    except getpass.GetPassWarning:
        raise OwnerCredentialError('HIDDEN_TTY_INPUT_UNAVAILABLE') from None
    except (EOFError, KeyboardInterrupt):
        raise OwnerCredentialError('LOCAL_TTY_INPUT_CANCELLED') from None
    if not valid_projected_proof(proof) or not valid_projected_proof(repeated):
        raise OwnerCredentialError('OWNER_PROOF_FORMAT_REJECTED')
    raw = proof.encode('utf-8')
    if not hmac.compare_digest(raw, repeated.encode('utf-8')):
        raise OwnerCredentialError('OWNER_PROOF_MISMATCH')
    encrypted = _run_creds('encrypt', raw)
    if not hmac.compare_digest(_run_creds('decrypt', encrypted), raw):
        raise OwnerCredentialError('OS_CREDENTIAL_PROVIDER_UNAVAILABLE')
    with credential_directory(directory, create=True) as (parent, check):
        if not private_directory(os.fstat(parent)):
            raise OwnerCredentialError('ENCRYPTED_STORE_DIRECTORY_REJECTED')
        check()
        descriptor = os.open(OWNER_CREDENTIAL_NAME,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                            0o600, dir_fd=parent)
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(encrypted):
                size = os.write(descriptor, encrypted[offset:])
                if size <= 0:
                    raise OSError('Credential write failed')
                offset += size
            os.fsync(descriptor)
            check()
            bound = os.stat(OWNER_CREDENTIAL_NAME, dir_fd=parent, follow_symlinks=False)
            if not os.path.samestat(os.fstat(descriptor), bound):
                raise OwnerCredentialError('ENCRYPTED_STORE_CHANGED')
            os.fsync(parent)
        except BaseException:
            # Remove only our own, still-bound newly created ciphertext.
            try:
                bound = os.stat(OWNER_CREDENTIAL_NAME, dir_fd=parent, follow_symlinks=False)
                if os.path.samestat(os.fstat(descriptor), bound):
                    os.unlink(OWNER_CREDENTIAL_NAME, dir_fd=parent)
            except OSError:
                pass
            raise
        finally:
            os.close(descriptor)
    return {'status': 'encrypted_stored', 'credential_name': OWNER_CREDENTIAL_NAME,
            'path': str(directory / OWNER_CREDENTIAL_NAME), 'service_restarted': False}


def main(argv=None):
    parser = _ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    preparation = commands.add_parser('prepare')
    preparation.add_argument('--code-root', type=Path, default=CODE_ROOT)
    preparation.add_argument('--root', type=Path, required=True)
    preparation.add_argument('--python', type=Path, required=True)
    preparation.add_argument('--host', default='127.0.0.1')
    preparation.add_argument('--port', type=int, default=8766)
    registration = commands.add_parser('register')
    registration.add_argument('--allow-host-key-initialization', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare(code_root=args.code_root, data_root=args.root,
                             python=args.python, host=args.host, port=args.port)
        else:
            result = register(allow_host_key_initialization=args.allow_host_key_initialization)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OwnerCredentialError, OSError, ValueError, EOFError, KeyboardInterrupt) as error:
        print(json.dumps({'status': 'unavailable', 'code': error.code if isinstance(error, OwnerCredentialError)
                          else 'OWNER_CREDENTIAL_PREPARATION_FAILED'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
