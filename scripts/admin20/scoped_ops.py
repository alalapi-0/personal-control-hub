"""Frozen LAN/Creator operations. Imported only from a root-sealed window bundle."""
import base64
import hashlib
import os
import pathlib
import re
import shutil
import stat
import subprocess
import zipfile

EXTRA_OPS = (
    'lan.packages.state', 'lan.packages.install',
    'lan.proof.encrypt', 'lan.proof.decrypt', 'lan.proof.wrong-name',
    'lan.proof.expired', 'lan.proof.project',
    'lan.credential.seal', 'lan.credential.get', 'creator.runtime.state', 'creator.runtime.install',
)
KEY_NAMES = ('lan-share-ca-key', 'lan-share-server-key')
MARKER = b'LAN-FILE-SHARE-PROVIDER-PROOF-v1\n'
PROOF_NAME = 'lan-share-provider-proof'
PACKAGES = (('libtss2-fapi1t64', '4.1.3-6'), ('tpm2-tools', '5.7-1build1'))


def argument_gate(request):
    op = request['op']
    keys = {'op', 'name', 'approval_reference'} if op.startswith('lan.credential.') else {'op'}
    if op == 'lan.credential.seal':
        keys |= {'secret_b64'}
    if set(request) != keys:
        return 'EXTRA_OR_MISSING_ARGUMENT'
    if op.startswith('lan.credential.'):
        if request['name'] not in KEY_NAMES:
            return 'OUTSIDE_CREDENTIAL_NAME'
        if not isinstance(request['approval_reference'], str) or not re.fullmatch(r'[A-Za-z0-9_.:/@+-]{1,160}', request['approval_reference']):
            return 'INVALID_APPROVAL_REFERENCE'
    if op == 'lan.credential.seal':
        try:
            value = base64.b64decode(request['secret_b64'], validate=True)
            if not 1 <= len(value) <= 16384:
                return 'INVALID_CREDENTIAL_SIZE'
        except (ValueError, TypeError):
            return 'INVALID_CREDENTIAL_ENCODING'
    return None


def installed():
    result = {}
    for name, _ in PACKAGES:
        p = subprocess.run(['/usr/bin/dpkg-query', '-W', '-f=${Version}|${db:Status-Status}', name],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
        result[name] = p.stdout.strip() if p.returncode == 0 else 'absent'
    return result


def call(argv, env, input_bytes=None):
    p = subprocess.run(argv, input=input_bytes, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=180, env=env)
    # Never include stdout/stderr in responses: this also covers credential operations.
    return p.returncode


def unit_prefix(root, state, suffix, writable, user=False):
    return [
        '/usr/bin/systemd-run', '--quiet', '--wait', '--collect', '--pipe',
        '--unit=codex-admin20-' + state['window_id'].split('-')[-1][:10] + '-' + suffix,
        '--property=Type=exec', '--property=RuntimeMaxSec=120',
        '--property=ProtectSystem=strict', '--property=ProtectHome=yes',
        '--property=PrivateNetwork=yes', '--property=PrivateTmp=yes',
        '--property=TemporaryFileSystem=/run/systemd:rw',
        '--property=ReadWritePaths=' + str(writable),
        '--property=DevicePolicy=closed', '--property=DeviceAllow=/dev/tpmrm0 rw',
        '--property=NoNewPrivileges=yes', '--property=UMask=0077',
    ] + (['--property=User=alalapi', '--property=Group=alalapi'] if user else [])


def operate(api, state, manifest, request):
    op = request['op']; root = api.ROOT
    if op == 'lan.packages.state':
        return {'status': 'OK', 'packages': installed(), 'effects': 0}
    if op == 'creator.runtime.state':
        target = pathlib.Path(manifest['creator']['runtime_root'])
        return {'status': 'OK', 'runtime_root': str(target), 'present': target.exists(), 'effects': 0}
    if min(state['expires_epoch'] - api.time.time(), state['expires_boottime'] - api.boottime()) < 600:
        raise ValueError('INSUFFICIENT_WINDOW_FOR_BOUNDED_TRANSACTION')
    if op == 'lan.packages.install':
        if state.get('packages_attempted'):
            raise ValueError('PACKAGE_OPERATION_ALREADY_ATTEMPTED')
        before = installed()
        if all(before[n] == v + '|installed' for n, v in PACKAGES):
            return {'status': 'ALREADY_OWNED', 'packages': before, 'effects': 0}
        if any(before[n] != 'absent' and before[n] != '|not-installed' for n, _ in PACKAGES):
            raise ValueError('PACKAGE_PREIMAGE_CHANGED')
        audit = subprocess.run(['/usr/bin/dpkg', '--audit'], capture_output=True, timeout=15, env=api.ENV)
        if audit.returncode or audit.stdout.strip() or audit.stderr.strip():
            raise ValueError('PREEXISTING_DPKG_AUDIT_FAILURE')
        paths = []
        for entry in manifest['packages']:
            p = root / 'inputs' / entry['file']
            if api.digest(api.trusted(p)) != entry['sha256']:
                raise ValueError('FIXED_PACKAGE_BYTES_CHANGED')
            paths.append(str(p))
        state['packages_attempted'] = True; api.save(state); api.begin_transaction()
        rc = call(['/usr/bin/dpkg', '--install', *paths], api.ENV)
        after = installed(); state['packages_result'] = {'exit': rc, 'packages': after}; api.save(state)
        return {'status': 'OK' if rc == 0 and all(after[n] == v + '|installed' for n, v in PACKAGES) else 'FAILED',
                'exit': rc, 'packages': after, 'only_fixed_two_packages': True}
    if op == 'creator.runtime.install':
        cfg = manifest['creator']; target = pathlib.Path(cfg['runtime_root'])
        if target.exists() or target.is_symlink():
            raise ValueError('EXISTING_RUNTIME_TARGET_PRESERVED')
        if target.parent != pathlib.Path('/opt'):
            raise ValueError('RUNTIME_RESOURCE_CHANGED')
        for parent in (target.parent, *target.parent.parents):
            s = parent.lstat()
            if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
                raise ValueError('UNTRUSTED_RUNTIME_PARENT')
        api.trusted(pathlib.Path('/usr/bin/python3.14'))
        archive = root / 'inputs' / cfg['archive']
        if api.digest(api.trusted(archive)) != cfg['sha256']:
            raise ValueError('OFFICIAL_RUNTIME_BYTES_CHANGED')
        api.begin_transaction(); target.mkdir(mode=0o755)
        try:
            with zipfile.ZipFile(archive) as z:
                names = set()
                for entry in z.infolist():
                    p = pathlib.PurePosixPath(entry.filename)
                    if p.is_absolute() or '..' in p.parts or not p.parts or entry.filename in names:
                        raise ValueError('UNSAFE_RUNTIME_ARCHIVE_MEMBER')
                    names.add(entry.filename)
                    mode = entry.external_attr >> 16
                    if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                        raise ValueError('SPECIAL_RUNTIME_ARCHIVE_MEMBER')
                    dest = target.joinpath(*p.parts)
                    if entry.is_dir():
                        dest.mkdir(parents=True, exist_ok=True, mode=0o755); continue
                    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                    with z.open(entry) as source, dest.open('xb') as output:
                        shutil.copyfileobj(source, output)
                    os.chmod(dest, 0o755 if mode & 0o111 else 0o644)
                    os.chown(dest, 0, 0)
            sandbox = target / 'chrome-sandbox'
            if api.digest(api.trusted(sandbox)) != cfg['sandbox_sha256']:
                raise ValueError('SANDBOX_BYTES_CHANGED')
            os.chmod(sandbox, 0o4755)
            state['creator_runtime_installed'] = str(target); api.save(state)
            return {'status': 'OK', 'runtime_root': str(target), 'electron': str(target / 'electron'),
                    'sandbox': 'root-owned4755_under_root-owned_runtime', 'app_source_changed': False}
        except Exception:
            # Only the directory freshly created by this operation is owned for cleanup.
            shutil.rmtree(target); raise
    proof = root / 'provider-proof'
    if not proof.exists():
        proof.mkdir(mode=0o700)
    if op.startswith('lan.proof.'):
        done = state.setdefault('proof_attempts', [])
        if op in done:
            raise ValueError('PROOF_OPERATION_ALREADY_ATTEMPTED')
        sealed = proof / 'marker.cred'
        if op != 'lan.proof.encrypt' and not sealed.is_file():
            raise ValueError('PROOF_ENCRYPTION_REQUIRED')
        if op not in ('lan.proof.encrypt', 'lan.proof.decrypt') and not state.get('proof_results', {}).get('lan.proof.decrypt', {}).get('expected_outcome'):
            raise ValueError('CORRECT_DECRYPTION_REQUIRED_FIRST')
        done.append(op); api.save(state); api.begin_transaction()
        suffix = op.rsplit('.', 1)[-1]; prefix = unit_prefix(root, state, suffix, proof)
        plain = proof / 'result'
        if op == 'lan.proof.encrypt':
            argv = prefix + ['/usr/bin/systemd-creds', '--no-ask-password', '--with-key=tpm2',
                             '--tpm2-device=/dev/tpmrm0', '--name=' + PROOF_NAME,
                             '--not-after=@' + str(int(state['expires_epoch'])), 'encrypt', '-', str(sealed)]
            rc = call(argv, api.ENV, MARKER); expected = rc == 0 and sealed.is_file()
        elif op == 'lan.proof.project':
            program = "import os,pathlib,hashlib; p=pathlib.Path(os.environ['CREDENTIALS_DIRECTORY'])/'" + PROOF_NAME + "'; assert os.getuid()==1000 and hashlib.sha256(p.read_bytes()).hexdigest()=='" + hashlib.sha256(MARKER).hexdigest() + "'"
            argv = unit_prefix(root, state, suffix, proof, user=True) + [
                '--property=LoadCredentialEncrypted=' + PROOF_NAME + ':' + str(sealed),
                '/usr/bin/python3.14', '-I', '-c', program]
            rc = call(argv, api.ENV); expected = rc == 0
        else:
            name = 'lan-share-wrong-name' if op == 'lan.proof.wrong-name' else PROOF_NAME
            argv = prefix + ['/usr/bin/systemd-creds', '--no-ask-password', '--refuse-null', '--name=' + name]
            if op == 'lan.proof.expired':
                argv += ['--timestamp=@' + str(int(state['expires_epoch']) + 60)]
            argv += ['decrypt', str(sealed), str(plain)]
            rc = call(argv, api.ENV)
            expected = rc != 0 if op != 'lan.proof.decrypt' else rc == 0 and plain.read_bytes() == MARKER
            plain.unlink(missing_ok=True)
        state.setdefault('proof_results', {})[op] = {'exit': rc, 'expected_outcome': expected}; api.save(state)
        return {'status': 'OK' if expected else 'FAILED', 'exit': rc,
                'expected_outcome': expected, 'synthetic_nonsecret_only': True}
    if op.startswith('lan.credential.'):
        # Called by the local application SDK in memory; CLI rejects this operation.
        if not all(state.get('proof_results', {}).get(x, {}).get('expected_outcome') for x in EXTRA_OPS if x.startswith('lan.proof.')):
            raise ValueError('PROVIDER_QUALIFICATION_REQUIRED')
        store = root / 'lan-credentials'; store.mkdir(mode=0o700, exist_ok=True)
        target = store / (request['name'] + '.cred')
        if op == 'lan.credential.get':
            api.trusted(target)
            program = "import os,pathlib,sys; assert os.getuid()==1000; sys.stdout.buffer.write((pathlib.Path(os.environ['CREDENTIALS_DIRECTORY'])/'" + request['name'] + "').read_bytes())"
            api.begin_transaction()
            argv = unit_prefix(root, state, 'get', store, user=True) + [
                '--property=LoadCredentialEncrypted=' + request['name'] + ':' + str(target),
                '/usr/bin/python3.14', '-I', '-c', program]
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                    env=api.ENV, timeout=180)
            if result.returncode or not 1 <= len(result.stdout) <= 16384:
                raise ValueError('CREDENTIAL_PROJECTION_FAILED_NO_SECRET_OUTPUT')
            # In-memory application SDK only. CLI blocks both credential operations.
            return {'status': 'OK', 'secret_b64': base64.b64encode(result.stdout).decode(),
                    'name': request['name']}
        if target.exists() or target.is_symlink():
            raise ValueError('EXISTING_CREDENTIAL_PRESERVED')
        secret = base64.b64decode(request['secret_b64'], validate=True)
        api.begin_transaction()
        argv = unit_prefix(root, state, 'seal', store) + [
            '/usr/bin/systemd-creds', '--no-ask-password', '--with-key=tpm2', '--tpm2-device=/dev/tpmrm0',
            '--name=' + request['name'], 'encrypt', '-', str(target)]
        rc = call(argv, api.ENV, secret)
        if rc or not target.is_file():
            target.unlink(missing_ok=True); raise ValueError('CREDENTIAL_SEAL_FAILED_NO_SECRET_OUTPUT')
        os.chmod(target, 0o600)
        return {'status': 'OK', 'encrypted_store': str(target), 'secret_returned': False}
    raise ValueError('OUTSIDE_SCOPE')
