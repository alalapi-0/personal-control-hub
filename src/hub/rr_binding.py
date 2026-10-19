"""Owner-protected installation metadata, separate code and registered data."""
import hashlib
import json
import os
from pathlib import Path
import re

from .rr_payload import RRFeedbackError, canonical_json, opaque
from .task_store import TaskStore

ORIGIN = 'https://rr-personal-control-hub.invalid:9419'
SOCKET = Path('/run/pch-rr/browser.sock')
KEY_PROVIDER = Path('/run/rr-pch-key/pch.key.sock')
BINDING_KEYS = {'schema','project_id','program_id','code_root','data_root','browser_enabled','agent_uid',
                'gateway_uid','gateway_gid','origin','devices','review_project_ids','ledger_identity',
                'storage_registration_sha256','key_provider_socket'}


def denied():
    return RRFeedbackError(503, 'RR_INSTALLATION_NOT_READY')


def private_json(path, maximum=131072):
    from .rr_cli import _regular, _directories
    path = Path(path)
    _directories(path.parent)
    try:
        raw = _regular(path, maximum, private=True)
        value = json.loads(raw)
        if canonical_json(value) != raw:
            raise denied()
        return value
    except (ValueError, TypeError, UnicodeError):
        raise denied() from None


def verify_program(code_root, program_id):
    from .rr_cli import _regular, _directories
    code_root = Path(code_root).absolute()
    manifest = private_json(code_root / 'rr-runtime-manifest.json')
    if set(manifest) != {'schema','program_id','files','source'} or manifest['schema'] != 1:
        raise denied()
    files = manifest['files']
    if type(files) is not dict or not 1 <= len(files) <= 512:
        raise denied()
    actual_id = hashlib.sha256(canonical_json(files)).hexdigest()
    if program_id != actual_id or manifest['program_id'] != actual_id:
        raise denied()
    for name, item in files.items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] not in {'src','scripts','project.yaml'}:
            raise denied()
        target = code_root / path
        _directories(target.parent)
        raw = _regular(target, 2*1024*1024)
        if item != {'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}:
            raise denied()
    source = manifest['source']
    if (type(source) is not dict or set(source) != {'commit','dirty'}
            or not re.fullmatch('[0-9a-f]{40,64}', source.get('commit','')) or type(source.get('dirty')) is not bool):
        raise denied()
    return source


def load_binding(path, *, code_root, store=None):
    from .rr_cli import _directories
    code_root = Path(code_root).absolute()
    install = private_json(code_root / 'rr-installation.json')
    if (set(install) != {'schema','project_id','program_id','code_root','data_root','binding_path'}
            or install['schema'] != 1 or install['project_id'] != 'personal-control-hub'
            or install['code_root'] != str(code_root) or install['binding_path'] != str(Path(path).absolute())):
        raise denied()
    binding = private_json(path)
    if (type(binding) is not dict or set(binding) != BINDING_KEYS or binding['schema'] != 2
            or binding['project_id'] != 'personal-control-hub'
            or any(binding[k] != install[k] for k in ('project_id','program_id','code_root','data_root'))
            or type(binding['agent_uid']) is not int or binding['agent_uid'] != os.getuid()
            or type(binding['browser_enabled']) is not bool or binding['origin'] != ORIGIN
            or type(binding['review_project_ids']) is not list or not 1 <= len(binding['review_project_ids']) <= 100
            or len(set(binding['review_project_ids'])) != len(binding['review_project_ids'])
            or binding['key_provider_socket'] != str(KEY_PROVIDER)):
        raise denied()
    for ident in binding['review_project_ids']:
        opaque(ident)
    # Agent access remains available when the Browser is disabled. Live Browser
    # configuration is checked separately before receiving any credential FD.
    root = Path(binding['data_root'])
    if not root.is_absolute() or '..' in root.parts or Path(path).absolute() != root/'data/rr-adapter.json':
        raise denied()
    _directories(root)
    if root.stat().st_uid != os.getuid():
        raise denied()
    store = store if store is not None else TaskStore(root)
    if store.root != root:
        raise denied()
    for target in (store.path, store.registration_path):
        info = target.lstat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or target.is_symlink() or info.st_nlink != 1:
            raise denied()
    if ([store.path.stat().st_dev,store.path.stat().st_ino] != binding['ledger_identity']
            or hashlib.sha256(store.registration_path.read_bytes()).hexdigest() != binding['storage_registration_sha256']):
        raise denied()
    store.check_storage(readonly=True)
    source = verify_program(code_root, binding['program_id'])
    return binding, store, source


def browser_binding(binding):
    from .rr_cli import gateway_group
    if (not binding['browser_enabled'] or any(type(binding[k]) is not int or binding[k] < 0 for k in ('gateway_uid','gateway_gid'))
            or binding['gateway_uid'] in {0,binding['agent_uid']} or type(binding['devices']) is not dict or not binding['devices']
            or any(not re.fullmatch('[0-9a-f]{64}',k) or v != binding['project_id'] for k,v in binding['devices'].items())):
        raise denied()
    gateway_group(binding)
    return binding
