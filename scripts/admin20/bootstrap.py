"""Literal root bootstrap embedded by prepare.py after binding every payload.

Do not sudo this user-writable file. The generated command embeds its reviewed
source and all hashes, reads fixed input bytes once, then seals root-owned files.
"""
import hashlib
import io
import json
import os
import pathlib
import socket
import stat
import subprocess
import tarfile
import time

CONFIG = None


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe(path, directory=False):
    for p in (path, *path.parents):
        s = p.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise ValueError('UNTRUSTED_ROOT_PATH')
    if directory and not path.is_dir():
        raise ValueError('NOT_ROOT_DIRECTORY')


def userbytes(path, expected):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_size > 256 * 1024 * 1024:
            raise ValueError('INVALID_FIXED_INPUT')
        data = f.read()
    if digest(data) != expected:
        raise ValueError('FIXED_INPUT_HASH_CHANGED')
    return data


def unpack(blob, expected):
    payloads = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:') as t:
        for item in t:
            if item.name not in expected or item.name in payloads or not item.isfile():
                raise ValueError('INVALID_BUNDLE_MEMBER')
            if item.size > 1024 * 1024:
                raise ValueError('PAYLOAD_TOO_LARGE')
            data = t.extractfile(item).read()
            if digest(data) != expected[item.name]:
                raise ValueError('FIXED_PAYLOAD_HASH_CHANGED')
            payloads[item.name] = data
    if set(payloads) != set(expected):
        raise ValueError('INCOMPLETE_BUNDLE')
    return payloads


def main():
    if os.geteuid() != 0:
        raise ValueError('ROOT_AUTHENTICATION_REQUIRED')
    cfg = CONFIG
    if cfg is None:
        raise ValueError('MUST_USE_PREPARED_LITERAL_COMMAND')
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'HOME': '/root'}
    if pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip() != cfg['boot_id']:
        raise ValueError('REPREPARE_AFTER_REBOOT')
    safe(pathlib.Path('/etc/machine-id'))
    if pathlib.Path('/etc/machine-id').read_text().strip() != cfg['machine_id'] or socket.gethostname() != cfg['hostname']:
        raise ValueError('HOST_CHANGED')
    root = pathlib.Path(cfg['root']); run = pathlib.Path(cfg['socket_dir'])
    safe(root.parent, True); safe(run.parent, True)
    if root.exists() or root.is_symlink() or run.exists() or run.is_symlink():
        raise ValueError('EXISTING_WINDOW_PRESERVED_NO_RENEWAL')
    payloads = unpack(userbytes(cfg['bundle'], cfg['bundle_sha256']), cfg['payloads'])
    manifest = json.loads(payloads['manifest.json'])
    for name, expected in manifest['protected_code'].items():
        p = pathlib.Path(name); safe(p)
        if digest(p.read_bytes()) != expected:
            raise ValueError('SYSTEM_CODE_CHANGED')
    for name, target in manifest['protected_links'].items():
        p = pathlib.Path(name); s = p.lstat()
        if s.st_uid != 0 or not stat.S_ISLNK(s.st_mode) or str(p.resolve(strict=True)) != target:
            raise ValueError('SYSTEM_COMMAND_LINK_CHANGED')
    old = pathlib.Path(cfg['old_hooks']); safe(old)
    hooks = old.read_bytes(); sealed = json.loads(hooks)
    if set(sealed) != set(manifest['hook_policy']):
        raise ValueError('UNEXPECTED_SEALED_HOOK_SET')
    for name, value in sealed.items():
        p = pathlib.Path(name); safe(p); s = p.stat()
        if (digest(p.read_bytes()) != value['sha256'] or stat.S_IMODE(s.st_mode) != value['mode']
                or s.st_gid != value['gid'] or s.st_uid != value['uid'] or s.st_mode & 0o111):
            raise ValueError('SEALED_UFW_HOOK_PREIMAGE_CHANGED')
    inputs = {name: userbytes(value['path'], value['sha256']) for name, value in cfg['inputs'].items()}
    root.mkdir(mode=0o755); run.mkdir(mode=0o755)

    def put(path, data, mode):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), mode); f.write(data); f.flush(); os.fsync(f.fileno())

    for name, data in payloads.items():
        put(root / name, data, 0o444)
    put(root / 'hooks.json', hooks, 0o640)
    (root / 'inputs').mkdir(mode=0o700)
    for name, data in inputs.items():
        put(root / 'inputs' / name, data, 0o400)
    grant = {'window_id': cfg['window_id'], 'boot_id': cfg['boot_id'],
             'machine_id': cfg['machine_id'], 'payloads': cfg['payloads']}
    put(root / 'grant.json', (json.dumps(grant, sort_keys=True) + '\n').encode(), 0o444)
    # The backend rejects new effects at 72000s. The extra 900s runtime ceiling
    # allows an already admitted bounded transaction to finish safely.
    command = ['/usr/bin/systemd-run', '--quiet', '--unit=' + cfg['unit'],
               '--property=Type=exec', '--property=RuntimeMaxSec=72900',
               '--property=TimeoutStopSec=240', '--property=KillMode=control-group',
               '--property=UMask=0077', '--property=Restart=no',
               '/usr/bin/python3.14', '-I', '-B', str(root / 'server.py')]
    result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=20)
    if result.returncode:
        raise ValueError('BACKEND_START_FAILED_PRESERVE_FIXED_NAMESPACE')
    for _ in range(50):
        if (root / 'public.json').exists() and (run / 'cap.sock').exists():
            break
        time.sleep(0.1)
    else:
        raise ValueError('BACKEND_START_NOT_VERIFIED_NO_REPLAY')
    safe(root / 'public.json'); public = json.loads((root / 'public.json').read_text())
    if (public['expires_epoch'] - public['started_epoch'] != 72000
            or public['boot_id'] != cfg['boot_id'] or public['revoked'] or public['closed']):
        raise ValueError('WINDOW_METADATA_NOT_VALID')
    print('20-hour capability activated; password was used only by local sudo.')
    print('Status: /usr/bin/python3.14 -I ' + str(root / 'client.py') + ' status')
    print('Revoke: /usr/bin/python3.14 -I ' + str(root / 'client.py') + ' revoke')


if __name__ == '__main__':
    main()
