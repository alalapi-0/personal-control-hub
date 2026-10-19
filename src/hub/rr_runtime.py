"""One PCH process, existing services/store, one owned Browser socket lifecycle."""
import array
import contextlib
import fcntl
import json
import os
from pathlib import Path
import socket
import stat
import struct
import threading

from .rr_auth import GatewayAuth
from .rr_binding import load_binding, browser_binding, denied, SOCKET, KEY_PROVIDER
from .rr_payload import canonical_json
from .rr_feedback import RRFeedbackStore
from .rr_read_model import RRReadModel
from .rr_service import RRApplication, UnixRRServer, build_manifest, public_resources


def consume_key_fd(fd):
    """Consume and close one sealed anonymous memfd; never accept a disk file."""
    try:
        info = os.fstat(fd)
        target = os.readlink('/proc/self/fd/' + str(fd))
        required = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 0 or not target.startswith('/memfd:')
                or not 32 <= info.st_size <= 64 or fcntl.fcntl(fd, fcntl.F_GET_SEALS) & required != required):
            raise denied()
        os.lseek(fd,0,os.SEEK_SET)
        raw = os.read(fd,65)
        if len(raw) != info.st_size:
            raise denied()
        return raw
    except (OSError, ValueError):
        raise denied() from None
    finally:
        with contextlib.suppress(OSError):os.close(fd)


def request_key_fd(binding):
    """Fixed local OS-provider contract: SO_PEERCRED plus exactly one SCM_RIGHTS FD."""
    path = KEY_PROVIDER
    parent = path.parent.lstat(); info = path.lstat()
    if (any(p.is_symlink() for p in (path,*path.parents)) or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != binding['gateway_uid'] or parent.st_gid != binding['gateway_gid']
            or stat.S_IMODE(parent.st_mode) != 0o750 or not stat.S_ISSOCK(info.st_mode)
            or info.st_uid != binding['gateway_uid'] or info.st_gid != binding['gateway_gid']
            or stat.S_IMODE(info.st_mode) != 0o660):
        raise denied()
    fds=[]
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as channel:
            channel.settimeout(3); channel.connect(str(path))
            _,uid,_=struct.unpack('3i',channel.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid != binding['gateway_uid']:
                raise denied()
            channel.sendall(canonical_json({'schema':1,'project_id':binding['project_id'],'recipient_uid':binding['agent_uid']})+b'\n')
            data,ancillary,flags,_=channel.recvmsg(1024,socket.CMSG_SPACE(8*array.array('i').itemsize),socket.MSG_CMSG_CLOEXEC)
            for level,kind,body in ancillary:
                if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                    received=array.array('i');received.frombytes(body[:len(body)-len(body)%received.itemsize]);fds.extend(received)
            expected={'schema':1,'project_id':binding['project_id'],'key_fd':True}
            if flags & (socket.MSG_TRUNC|socket.MSG_CTRUNC) or data != canonical_json(expected)+b'\n' or len(fds)!=1:
                raise denied()
            return fds.pop()
    except (OSError,ValueError):
        raise denied() from None
    finally:
        for fd in fds:os.close(fd)


class RRRuntime:
    def __init__(self, gateway):
        self.gateway=gateway
        self.thread=threading.Thread(target=gateway.serve_forever,kwargs={'poll_interval':0.1},name='pch-rr-browser',daemon=False)
        self.started=False;self.closed=False

    def start(self):
        if self.started or self.closed:raise denied()
        self.started=True
        try:self.thread.start()
        except BaseException:
            self.started=self.thread.ident is not None
            raise

    def close(self):
        if self.closed:return
        try:
            if self.started:
                self.gateway.shutdown();self.thread.join(5)
                if self.thread.is_alive():raise denied()
        finally:
            self.gateway.server_close();self.closed=True


def attach_rr_gateway(server, *, code_root, binding_path, key_fd=None):
    """Trusted local assembly API; browser input can never call this function.

    key_fd, if supplied, is consumed. Otherwise request an anonymous FD from the
    fixed provider. No key value occurs in argv, env, ordinary files or logs.
    """
    gateway=None;runtime=None
    try:
        if getattr(server,'rr_runtime',None) is not None:raise denied()
        binding,store,source=load_binding(binding_path,code_root=code_root,store=server.tasks.store)
        if server.projects.root != store.root:raise denied()
        if not binding['browser_enabled']:
            return None
        browser_binding(binding)
        fd = key_fd if key_fd is not None else request_key_fd(binding)
        key_fd=None
        key=consume_key_fd(fd)
        ids=binding['review_project_ids']
        pages=['/__rr/review/']+['/__rr/review/projects/'+ident for ident in ids]
        resources=public_resources(code_root);manifest=build_manifest(code_root,resources,pages,source=source)
        auth=GatewayAuth(key,binding['devices'],binding['project_id'],binding['origin'],binding['gateway_uid'],binding['agent_uid'])
        model=RRReadModel(server.projects,store,ids,manifest['source'])
        app=RRApplication(RRFeedbackStore(store),auth,manifest,resources,read_model=model)
        gateway=UnixRRServer(SOCKET,app,socket_gid=binding['gateway_gid'])
        runtime=RRRuntime(gateway);old_close=server.server_close
        def close_owned():
            try:runtime.close()
            finally:old_close()
        server.server_close=close_owned;server.rr_runtime=runtime
        runtime.start()
        return runtime
    except BaseException as error:
        if runtime is not None:runtime.close()
        elif gateway is not None:gateway.server_close()
        if isinstance(error,(KeyboardInterrupt,SystemExit)):raise
        raise denied() from None
    finally:
        if key_fd is not None:os.close(key_fd)
