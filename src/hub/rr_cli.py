"""Explicit local RR entrypoint. No provisioning, owner login or task execution."""
from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import pwd

import yaml

from .service_contract import ServiceError
from .task_store import TaskStore
from .rr_feedback import RRFeedbackError, RRFeedbackStore
from .rr_payload import canonical_json, parse_document

PROJECT = 'personal-control-hub'
SOCKET = Path('/run/pch-rr/browser.sock')
CONFIG_KEYS = {'schema', 'enabled', 'project_id', 'agent_uid', 'gateway_uid',
               'gateway_gid', 'origin', 'devices'}


def unavailable():
    return RRFeedbackError(503, 'RR_LOCAL_NOT_READY')


def _regular(path, maximum, *, private=False):
    """No-follow bounded read; never include a path or content in an error."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or info.st_size > maximum
                    or stat.S_IMODE(info.st_mode) & (0o077 if private else 0o022)):
                raise unavailable()
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(maximum + 1)
            if len(raw) > maximum:
                raise unavailable()
            return raw
        finally:
            os.close(fd)
    except OSError:
        raise unavailable() from None


def _directories(path):
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current /= part
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o002:
                raise unavailable()
            if info.st_mode & 0o020:
                # A private per-owner primary group is common on Linux. It is
                # safe only while every actual member is this Owner.
                group = grp.getgrgid(info.st_gid)
                names = set(group.gr_mem) | {p.pw_name for p in pwd.getpwall() if p.pw_gid == info.st_gid}
                if info.st_uid != os.getuid() or names != {pwd.getpwuid(os.getuid()).pw_name}:
                    raise unavailable()
    except (OSError, KeyError):
        raise unavailable() from None


def local_context(root):
    """Called with the installation root, never with a browser/CLI path argument."""
    root = Path(root).absolute()
    _directories(root)
    info = root.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise unavailable()
    try:
        for directory in ('scripts', 'src', 'src/hub', 'data'):
            _directories(root / directory)
        for name in ('scripts/pch_rr.py', 'src/hub/rr_cli.py'):
            _regular(root / name, 65536)
        identity = yaml.safe_load(_regular(root / 'project.yaml', 16384))
        if identity['project']['id'] != PROJECT:
            raise unavailable()
        config = parse_document(_regular(root / 'data/rr-adapter.json', 16384, private=True))
        if (type(config) is not dict or set(config) != CONFIG_KEYS
                or type(config['schema']) is not int or config['schema'] != 1
                or config['enabled'] is not True or config['project_id'] != PROJECT
                or type(config['agent_uid']) is not int or config['agent_uid'] != os.getuid()
                or any(type(config[k]) is not int or config[k] < 0
                       for k in ('gateway_uid', 'gateway_gid'))
                or config['gateway_uid'] in {0, config['agent_uid']}
                or type(config['origin']) is not str or type(config['devices']) is not dict
                or not config['devices']):
            raise unavailable()
        _directories(root / 'data/workbench')
        for directory in (root / 'data', root / 'data/workbench'):
            data_info = directory.lstat()
            if data_info.st_uid != os.getuid() or stat.S_IMODE(data_info.st_mode) != 0o700:
                raise unavailable()
        # Database content is not copied. TaskStore verifies its existing registration.
        for name in ('tasks.sqlite3', 'storage-registration.json'):
            file_info = (root / 'data/workbench' / name).lstat()
            if (not stat.S_ISREG(file_info.st_mode) or file_info.st_nlink != 1
                    or file_info.st_uid != os.getuid()
                    or stat.S_IMODE(file_info.st_mode) != 0o600):
                raise unavailable()
        store = TaskStore(root)
        store.check_storage(readonly=True)
        return config, RRFeedbackStore(store, project_id=PROJECT)
    except (KeyError, TypeError, ValueError, yaml.YAMLError, OSError):
        raise unavailable() from None


def gateway_group(config):
    """A provisioned group may admit only the dedicated gateway and Owner."""
    try:
        group = grp.getgrgid(config['gateway_gid'])
        gateway = pwd.getpwuid(config['gateway_uid'])
        owner = pwd.getpwuid(config['agent_uid'])
        allowed = {gateway.pw_name, owner.pw_name}
        members = set(group.gr_mem) | {p.pw_name for p in pwd.getpwall() if p.pw_gid == group.gr_gid}
        if gateway.pw_name not in members or not members <= allowed or group.gr_gid == os.getegid():
            raise unavailable()
        return group.gr_gid
    except (KeyError, OSError):
        raise unavailable() from None


def _credential_key():
    # The environment contains only the provider directory name, never the key.
    directory = os.environ.get('CREDENTIALS_DIRECTORY')
    if not directory:
        raise unavailable()
    path = Path(directory)
    if not path.is_absolute() or '..' in path.parts:
        raise unavailable()
    _directories(path)
    raw = _regular(path / 'pch-rr-gateway-key', 64, private=True)
    if not 32 <= len(raw) <= 64:
        raise unavailable()
    return raw


def _exclusive(path, raw):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _materialize(path, raw):
    try:
        _exclusive(path, raw)
    except FileExistsError:
        if _regular(path, len(raw), private=True) != raw:
            raise unavailable() from None


def agent_action(root, store, command, *, feedback_id=None, cursor=None, proof=None,
                 summary=None, references=None):
    """The authenticated local entry never accepts an agent identity override."""
    agent = 'uid-' + str(os.getuid())
    if command == 'pending':
        return store.pending(limit=20, cursor=cursor)
    if command == 'read':
        value = store.read_for_agent(feedback_id, agent)
        # All actual bytes were verified in the store. Materialize them privately,
        # with opaque names, so an Agent can inspect content before acknowledging.
        parent = Path(root) / 'data/rr-agent-reads'
        parent.mkdir(mode=0o700, exist_ok=True)
        _directories(parent)
        parent_info = parent.lstat()
        if parent_info.st_uid != os.getuid() or stat.S_IMODE(parent_info.st_mode) != 0o700:
            raise unavailable()
        from .rr_payload import valid_uuid
        valid_uuid(value['read_receipt']['read_id'])
        target = parent / value['read_receipt']['read_id']
        target.mkdir(mode=0o700, exist_ok=True)
        _directories(target)
        if target.lstat().st_uid != os.getuid() or stat.S_IMODE(target.lstat().st_mode) != 0o700:
            raise unavailable()
        manifest = []
        for index, attachment in enumerate(value['attachments']):
            raw = attachment['data']
            output = target / ('attachment-' + str(index))
            _materialize(output, raw)
            manifest.append({k: attachment[k] for k in ('attachment_id', 'size', 'sha256', 'content_type')})
            manifest[-1]['local_file'] = str(output)
            if len(raw) != attachment['size'] or hashlib.sha256(raw).hexdigest() != attachment['sha256']:
                raise unavailable()
        _materialize(target / 'proof.json', canonical_json(value['read_receipt']))
        _materialize(target / 'document.json', canonical_json(value['document']))
        return {'receipt': value['receipt'], 'document': value['document'],
                'read_receipt': value['read_receipt'], 'attachments': manifest,
                'proof_file': str(target / 'proof.json')}
    if command == 'ack':
        return store.ack(feedback_id, agent, proof)
    if command == 'result':
        return store.result(feedback_id, agent, proof, summary, references)
    raise unavailable()


def main(argv=None, *, installation_root=None):
    # installation_root is an in-process test seam, never a parsed CLI option.
    root = Path(installation_root) if installation_root is not None else Path(__file__).absolute().parents[2]
    parser = argparse.ArgumentParser(description='Explicit protected local PCH RR adapter')
    actions = parser.add_subparsers(dest='command', required=True)
    actions.add_parser('serve')
    pending = actions.add_parser('pending'); pending.add_argument('--cursor')
    read = actions.add_parser('read'); read.add_argument('feedback_id')
    for action in ('ack', 'result'):
        sub = actions.add_parser(action); sub.add_argument('feedback_id'); sub.add_argument('--proof', required=True)
        if action == 'result':
            sub.add_argument('--handling', required=True, help='Owner-only JSON file: summary and references')
    args = parser.parse_args(argv)
    try:
        config, store = local_context(root)
        if args.command == 'serve':
            from .rr_auth import GatewayAuth
            from .rr_service import build_manifest, RRApplication, UnixRRServer
            socket_gid = gateway_group(config)
            resources = {'pch-icon': root / 'src/hub/web/icon.svg'}
            manifest = build_manifest(root, resources, page_paths=('/',))
            auth = GatewayAuth(_credential_key(), config['devices'], PROJECT, config['origin'],
                               config['gateway_uid'], config['agent_uid'])
            app = RRApplication(store, auth, manifest, resources)
            with UnixRRServer(SOCKET, app, socket_gid=socket_gid) as server:
                server.serve_forever()
            return 0
        proof = parse_document(_regular(Path(args.proof), 16384, private=True)) if hasattr(args, 'proof') else None
        handling = parse_document(_regular(Path(args.handling), 16384, private=True)) if hasattr(args, 'handling') else None
        if handling is not None and (type(handling) is not dict or set(handling) != {'summary', 'references'}):
            raise unavailable()
        value = agent_action(root, store, args.command, feedback_id=getattr(args, 'feedback_id', None),
                             cursor=getattr(args, 'cursor', None), proof=proof,
                             summary=handling['summary'] if handling else None,
                             references=handling['references'] if handling else None)
        sys.stdout.buffer.write(canonical_json(value) + b'\n')
        return 0
    except (RRFeedbackError, ServiceError) as error:
        # No diagnostic payload or content reaches stderr/logs.
        print(json.dumps({'status': 'unavailable', 'code': error.code}), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, KeyError):
        print('{"status":"unavailable","code":"RR_LOCAL_NOT_READY"}', file=sys.stderr)
        return 1
