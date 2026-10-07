"""Server-registered storage identity; no client paths or silent fallback."""
from __future__ import annotations

import copy
import json
import os
import stat
import subprocess
from pathlib import Path

from .service_contract import ServiceError

PROFILE_VERSION = 2
MIN_FREE_BYTES = 64 * 1024 * 1024
PROFILE_FIELDS = {'version', 'root', 'device', 'inode', 'parents', 'mount'}
MOUNT_FIELDS = {'target', 'source', 'fstype', 'uuid'}


def blocked(reason, *, capacity=False):
    return ServiceError('TASK_STORAGE_BLOCKED', status=507 if capacity else 503,
                        details={'reason_code': reason})


def components(root, *, with_parents=False):
    if not root.is_absolute() or '..' in root.parts:
        raise blocked('PATH_REJECTED')
    current = Path(root.anchor)
    parents=[]
    try:
        for part in root.parts[1:]:
            parent=current.stat();parents.append([str(current),parent.st_dev,parent.st_ino])
            current /= part
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise blocked('PATH_REJECTED')
        return (root.stat(),parents) if with_parents else root.stat()
    except OSError:
        raise blocked('ROOT_UNAVAILABLE') from None


def mount_identity(root):
    try:
        result = subprocess.run(['findmnt', '--json', '--target', str(root),
                                 '--output', 'TARGET,SOURCE,FSTYPE,UUID'],
                                capture_output=True, text=True, timeout=1, check=False)
        if result.returncode or len(result.stdout) > 16384:
            raise ValueError()
        rows = json.loads(result.stdout)['filesystems']
        if len(rows) != 1 or set(rows[0]) != MOUNT_FIELDS:
            raise ValueError()
        row = rows[0]
        if any(type(row[key]) is not str or not row[key] for key in ('target', 'source', 'fstype')):
            raise ValueError()
        if row['uuid'] is not None and (type(row['uuid']) is not str or not row['uuid']):
            raise ValueError()
        target = Path(row['target'])
        if not target.is_absolute() or '..' in target.parts or not root.is_relative_to(target):
            raise ValueError()
        return row
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        raise blocked('MOUNT_UNAVAILABLE') from None


class StorageGuard:
    def __init__(self, root, profile, *, mount=mount_identity, space=os.statvfs):
        self.root, self.mount, self.space = Path(root), mount, space
        self.profile = copy.deepcopy(profile)

    @classmethod
    def register_internal(cls, root, *, mount=mount_identity, space=os.statvfs):
        """Trusted startup registration for a new ledger, never a replacement pin."""
        root = Path(root)
        info,parents = components(root,with_parents=True)
        profile = {'version': PROFILE_VERSION, 'root': str(root), 'device': info.st_dev,
                   'inode': info.st_ino, 'parents':parents, 'mount': mount(root)}
        guard = cls(root, profile, mount=mount, space=space)
        guard.check()
        return guard

    def check_identity(self):
        profile = self.profile
        if (type(profile) is not dict or set(profile) != PROFILE_FIELDS
                or type(profile['version']) is not int or profile['version'] != PROFILE_VERSION
                or profile['root'] != str(self.root)
                or any(type(profile[key]) is not int or profile[key] < 0 for key in ('device', 'inode'))
                or type(profile['parents']) is not list
                or type(profile['mount']) is not dict or set(profile['mount']) != MOUNT_FIELDS):
            raise blocked('PROFILE_REJECTED')
        info,parents = components(self.root,with_parents=True)
        if (info.st_dev, info.st_ino) != (profile['device'], profile['inode']):
            raise blocked('ROOT_IDENTITY_CHANGED')
        if parents!=profile['parents']:
            raise blocked('PARENT_IDENTITY_CHANGED')
        if self.mount(self.root) != profile['mount']:
            raise blocked('MOUNT_IDENTITY_CHANGED')
        return {'state': 'ready', 'profile_version': PROFILE_VERSION, 'reason_code': None}

    def check_space(self):
        try:
            space = self.space(self.root)
            available = space.f_bavail * space.f_frsize
            if type(available) is not int or available < 0:
                raise ValueError()
        except (OSError, AttributeError, ValueError, TypeError):
            raise blocked('SPACE_UNAVAILABLE') from None
        if available < MIN_FREE_BYTES:
            raise blocked('LOW_SPACE', capacity=True)
        return {'state': 'ready', 'profile_version': PROFILE_VERSION, 'reason_code': None}

    def check(self):
        self.check_identity()
        return self.check_space()
