"""Backend-only explicit writer release proofs; no scheduling or authority inference."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import time

from .service_contract import ServiceError
from .task_service import root_identity
from .workbench_store import digest


def _reject(code='WRITER_HANDOFF_REJECTED'):
    raise ServiceError(code, status=409)


def _paths(paths):
    if type(paths) is not list or not paths or len(paths) > 128:
        _reject()
    for value in paths:
        if type(value) is not str or not value or '\\' in value or any(c in value for c in '*?[]\x00'):
            _reject()
        path = PurePosixPath(value)
        if path.is_absolute() or any(p in {'', '.', '..'} for p in value.split('/')) or str(path) != value:
            _reject()
        if path.parts[0] == '.git':
            _reject()
    if len(set(paths)) != len(paths):
        _reject()
    return sorted(paths)


def _file(root, relative):
    parts = PurePosixPath(relative).parts
    directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        for part in parts[:-1]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            except FileNotFoundError:
                return {'state': 'absent'}
            except OSError:
                _reject('WRITER_PATH_ALIAS')
            os.close(directory)
            directory = child
        try:
            info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return {'state': 'absent'}
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 8 * 1024 * 1024:
            _reject('WRITER_PATH_ALIAS')
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read(8 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)
        if identity(info) != identity(opened) or identity(opened) != identity(after) or os.stat(parts[-1], dir_fd=directory, follow_symlinks=False) != after or len(raw) > 8 * 1024 * 1024:
            _reject('WRITER_PREIMAGE_CHANGED')
        return {'state': 'file', 'sha256': hashlib.sha256(raw).hexdigest(),
                'identity': [info.st_dev, info.st_ino]}
    finally:
        os.close(directory)


def _git_identity(root):
    # rev-parse only: no network, index refresh, hook, traversal or Git mutation.
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith('GIT_'):
            env.pop(key)
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS='0', LC_ALL='C')
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--path-format=absolute',
                             '--show-toplevel', '--git-dir', '--git-common-dir'],
                            env=env, capture_output=True, timeout=5, check=False)
    if result.returncode:
        # Only a genuinely non-repository root can be treated as non-Git.
        if result.returncode == 128 and b'not a git repository' in result.stderr:
            return None
        _reject('WRITER_GIT_IDENTITY_UNKNOWN')
    rows = result.stdout.decode('utf-8', errors='strict').splitlines()
    if len(rows) != 3:
        _reject('WRITER_GIT_IDENTITY_UNKNOWN')
    values = []
    for value in rows:
        path = Path(value).resolve(strict=True)
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode):
            _reject('WRITER_GIT_IDENTITY_UNKNOWN')
        values.append({'path': str(path), 'identity': [info.st_dev, info.st_ino]})
    return dict(zip(('worktree', 'git_dir', 'common'), values))


def snapshot_scope(root, owned_paths):
    """Read only exact owned file paths and necessary local Git identity metadata."""
    root = Path(root)
    identity = root_identity(root)
    paths = _paths(owned_paths)
    info = root.stat()
    result = {'canonical_root': str(root), 'root_identity': identity, 'root_entity': [info.st_dev, info.st_ino],
              'git': _git_identity(root), 'paths': {path: _file(root, path) for path in paths}}
    if root_identity(root) != identity:
        _reject('WRITER_ROOT_CHANGED')
    return result


def workspace_identity(root):
    """Exact granted root and Git entities only; never enumerate business files."""
    root=Path(root);identity=root_identity(root);info=root.stat()
    result={'canonical_root':str(root),'root_identity':identity,'root_entity':[info.st_dev,info.st_ino],
            'git':_git_identity(root),'paths':{}}
    if root_identity(root)!=identity:_reject('WRITER_ROOT_CHANGED')
    return result


def workspaces_overlap(left, right):
    """Whole-root grants overlap through physical roots or shared Git state."""
    a,b=Path(left['canonical_root']),Path(right['canonical_root'])
    ag,bg=left.get('git'),right.get('git')
    return (left['root_entity']==right['root_entity'] or a==b or a.is_relative_to(b) or b.is_relative_to(a)
            or bool(ag and bg and ag['common']['identity']==bg['common']['identity']))


class WriterHandoffCheck:
    """Shared admission/dispatch callback backed by fresh Root-native evidence.

    ``resolve_evidence`` belongs to the trusted local backend. The browser can
    supply neither release proof nor observation, and each call resolves them
    again instead of caching an idle badge as authority.
    """
    def __init__(self,resolve_evidence):
        if not callable(resolve_evidence):_reject()
        self.resolve_evidence=resolve_evidence

    def __call__(self,grant):
        evidence=self.resolve_evidence(grant)
        fields={'proof','registry_hash','observation','authority','from_owner','to_owner','task_id'}
        if type(evidence)is not dict or set(evidence)!=fields:_reject()
        verify_handoff(grant=grant,**evidence)
        return True


def _fresh(value, now, lifetime=120):
    return type(value) in (int, float) and math.isfinite(value) and now - lifetime <= value <= now


def verify_handoff(proof, *, grant, registry_hash, observation, authority,
                   from_owner, to_owner, task_id, now=None):
    """Verify a server-supplied release decision, never an untrusted HTTP payload.

    ``authority`` and ``observation`` MUST originate from the trusted Root backend;
    matching hashes bind their evidence, not authenticate a caller. Grant validation
    and the SQLite atomic claim remain the caller's responsibility.
    """
    now = time.time() if now is None else now
    fields = {'grant_hash', 'registry_hash', 'task_id', 'from_owner', 'to_owner',
              'observation_hash', 'authority_hash', 'scope', 'issued_at', 'expires_at'}
    if type(proof) is not dict or set(proof) != fields or type(grant) is not dict:
        _reject()
    if not all(type(value) is str and value for value in (from_owner, to_owner, task_id)) or from_owner == to_owner:
        _reject()
    if proof['grant_hash'] != digest(grant) or proof['registry_hash'] != registry_hash or not registry_hash:
        _reject('WRITER_GRANT_CHANGED')
    if any(proof[key] != value for key, value in [('from_owner', from_owner), ('to_owner', to_owner), ('task_id', task_id)]) or grant.get('task_id') != task_id:
        _reject()
    expiry = proof['expires_at']
    if not _fresh(proof['issued_at'], now) or type(expiry) not in (int, float) or not math.isfinite(expiry) or not now < expiry <= proof['issued_at'] + 120:
        _reject('WRITER_HANDOFF_EXPIRED')
    if type(grant.get('expires_at')) not in (int, float) or not math.isfinite(grant['expires_at']) or expiry > grant['expires_at']:
        _reject('WRITER_HANDOFF_EXPIRED')
    observed_fields = {'thread_id', 'cwd', 'source', 'provider', 'status', 'observed_at'}
    authority_fields = {'source', 'decision_id', 'release_source', 'released', 'from_owner', 'thread_id', 'observed_at'}
    if type(observation) is not dict or set(observation) != observed_fields or type(authority) is not dict or set(authority) != authority_fields:
        _reject()
    if proof['observation_hash'] != digest(observation) or proof['authority_hash'] != digest(authority):
        _reject('WRITER_PROOF_CHANGED')
    if not all(type(observation[key]) is str and observation[key] for key in ('thread_id', 'cwd', 'source', 'provider', 'status')) or not _fresh(observation['observed_at'], now) or not _fresh(authority['observed_at'], now):
        _reject()
    if authority['source'] != 'root_backend' or not isinstance(authority['decision_id'], str) or not authority['decision_id'] or authority['released'] is not True or authority['from_owner'] != from_owner or authority['thread_id'] != observation['thread_id']:
        _reject('WRITER_RELEASE_UNPROVEN')
    if authority['release_source'] not in {'owner_explicit_release', 'native_turn_terminal'}:
        _reject('WRITER_RELEASE_UNPROVEN')
    if observation['status'] not in {'completed', 'interrupted', 'cancelled', 'idle', 'notLoaded'}:
        _reject('WRITER_RELEASE_UNPROVEN')
    if authority['release_source'] == 'native_turn_terminal' and observation['status'] not in {'completed', 'interrupted', 'cancelled'}:
        _reject('WRITER_RELEASE_UNPROVEN')
    scope = proof['scope']
    if type(scope) is not dict or set(scope) != {'canonical_root', 'root_identity', 'root_entity', 'git', 'paths'} or type(scope['paths']) is not dict:
        _reject()
    if grant.get('root') != scope['canonical_root'] or grant.get('root_identity') != scope['root_identity'] or observation['cwd'] != scope['canonical_root']:
        _reject('WRITER_ROOT_CHANGED')
    current = snapshot_scope(scope['canonical_root'], list(scope['paths']))
    if current != scope:
        _reject('WRITER_PREIMAGE_CHANGED')
    return current


def scopes_conflict(left, right):
    """Compare backend snapshots with access=read/write and status; unknown fails closed.

    A shared Git common directory alone does not alias distinct worktree files.
    Writes in distinct repositories must still be serialized by the existing claim.
    """
    if left.get('access') == right.get('access') == 'read':
        return False
    if left.get('access') not in {'read', 'write'} or right.get('access') not in {'read', 'write'}:
        return True
    a, b = left.get('scope'), right.get('scope')
    if not isinstance(a, dict) or not isinstance(b, dict):
        return True
    ag, bg = a.get('git'), b.get('git')
    same_git = bool(ag and bg and ag['worktree']['identity'] == bg['worktree']['identity'])
    common_git = bool(ag and bg and ag['common']['identity'] == bg['common']['identity'])
    same_root = a.get('root_entity') == b.get('root_entity') or a.get('canonical_root') == b.get('canonical_root')
    roots = [Path(a['canonical_root']), Path(b['canonical_root'])]
    nested = roots[0].is_relative_to(roots[1]) or roots[1].is_relative_to(roots[0])
    if (same_root or same_git or common_git or nested) and ('requires_reconcile' in {left.get('status'), right.get('status')}):
        return True
    for ap, av in a['paths'].items():
        for bp, bv in b['paths'].items():
            if av.get('state') == bv.get('state') == 'file' and av.get('identity') == bv.get('identity'):
                return True
            absolute_a, absolute_b = roots[0] / ap, roots[1] / bp
            if absolute_a == absolute_b or absolute_a.is_relative_to(absolute_b) or absolute_b.is_relative_to(absolute_a):
                return True
            if same_root and (ap == bp or PurePosixPath(ap).is_relative_to(bp) or PurePosixPath(bp).is_relative_to(ap)):
                return True
    return False
