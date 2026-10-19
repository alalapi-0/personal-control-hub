"""Same-process current identities; hashes are verified once, metadata per use."""
import copy
import hashlib
from pathlib import Path
import stat

from .rr_payload import RRFeedbackError


def identity(info, *, immutable=False):
    stable = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
    return stable + ((info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                     if immutable else ())


class CurrentGuard:
    def __init__(self, server, code_root, binding_path):
        self.server, self.projects, self.store = server, server.projects, server.tasks.store
        self.root, self.code_root = Path(self.store.root), Path(code_root).absolute()
        self.ledger, self.registration = self.store.path, self.store.registration_path
        self.binding_path = Path(binding_path).absolute()
        self.files, self.directories = {}, {}
        self.ready, self.failed = False, False
        for path in (self.root, self.code_root, self.ledger.parent, self.binding_path.parent):
            self.pin_directories(path)
        info = self.ledger.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            self.deny()
        self.ledger_identity = identity(info)

    def deny(self):
        self.failed = True
        raise RRFeedbackError(503, 'RR_CURRENT_BINDING_CHANGED')

    def pin_directories(self, path):
        for current in (Path(path), *Path(path).parents):
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode):
                self.deny()
            value = identity(info)
            if current in self.directories and self.directories[current] != value:
                self.deny()
            self.directories[current] = value

    def read(self, path, maximum, *, private=False):
        """Called by the initial existing content verification, never per request."""
        from .rr_cli import _regular
        path = Path(path)
        self.pin_directories(path.parent)
        before = identity(path.lstat(), immutable=True)
        raw = _regular(path, maximum, private=private)
        value = (identity(path.lstat(), immutable=True), hashlib.sha256(raw).hexdigest())
        if before != value[0] or (path in self.files and self.files[path] != value):
            self.deny()
        self.files[path] = value
        self.pin_directories(path.parent)
        return raw

    def freeze(self, binding):
        self.profile = copy.deepcopy(self.store.storage_guard.profile)
        if (binding['data_root'] != str(self.root) or binding['code_root'] != str(self.code_root)
                or list(self.ledger_identity[:2]) != binding['ledger_identity']
                or self.registration not in self.files
                or self.files[self.registration][1] != binding['storage_registration_sha256']
                or self.binding_path not in self.files
                or self.code_root/'rr-runtime-manifest.json' not in self.files
                or self.code_root/'rr-installation.json' not in self.files):
            self.deny()
        self.ready = True
        self.check()

    def check(self):
        try:
            if (self.failed or not self.ready or self.server.projects is not self.projects
                    or self.server.tasks.store is not self.store
                    or self.projects.root != self.root or self.store.root != self.root
                    or self.store.path != self.ledger or self.store.registration_path != self.registration
                    or self.store.storage_guard.profile != self.profile):
                self.deny()
            expected = [(self.root, self.profile['device'], self.profile['inode'])]
            expected += [(Path(p), dev, ino) for p, dev, ino in self.profile['parents']]
            for path, dev, ino in expected:
                info = path.lstat()
                if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (dev, ino):
                    self.deny()
            for path, wanted in self.directories.items():
                if identity(path.lstat()) != wanted:
                    self.deny()
            info = self.ledger.lstat()
            if info.st_nlink != 1 or identity(info) != self.ledger_identity:
                self.deny()
            for path, (wanted, _digest) in self.files.items():
                if identity(path.lstat(), immutable=True) != wanted:
                    self.deny()
        except (OSError, AttributeError, KeyError, TypeError, ValueError):
            self.deny()
