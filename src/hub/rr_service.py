"""Separate RR Unix gateway. No original PCH handler or Agent route is mounted."""
import hashlib
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import stat
import struct
import subprocess

from .rr_auth import GatewayDenied, canonical

MAX_BODY = 2129920


class RequestError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def build_manifest(repo_root, resources, page_paths=('/',)):
    root = Path(repo_root).resolve(strict=True)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL, timeout=10).decode().strip()
    commit = git('rev-parse', 'HEAD')
    if not re.fullmatch('[0-9a-f]{40,64}', commit):
        raise ValueError('source unavailable')
    frozen, build = {}, hashlib.sha256()
    for ident, item in sorted(resources.items()):
        if not re.fullmatch('[a-z0-9_-]{1,64}', ident):
            raise ValueError('resource id rejected')
        path = Path(item)
        if not path.is_absolute():
            path = root / path
        relative = path.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('resource symlink rejected')
        if relative.as_posix() != 'src/hub/web/icon.svg' or not path.is_file():
            raise ValueError('resource is not public allowlisted content')
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        frozen[ident] = {'id':ident, 'sha256':digest, 'mode':'readonly', 'preview_version':digest}
        build.update(ident.encode() + data)
    for name in ('rr_auth.py','rr_service.py','rr_feedback.py','rr_payload.py'):
        build.update((root/'src/hub'/name).read_bytes())
    if not page_paths or any(not isinstance(p, str) or not p.startswith('/') or '?' in p or '#' in p or '..' in p or '\\' in p for p in page_paths):
        raise ValueError('page path rejected')
    return {'schema':1, 'project_id':'personal-control-hub',
            'source':{'commit':commit,'build_id':build.hexdigest(),'dirty':bool(git('status','--porcelain','--untracked-files=normal'))},
            'page_paths':list(page_paths), 'resources':list(frozen.values()),
            'transport':{'websocket':False,'sse':False},
            'purpose':'PCH public review metadata; private project and task data are excluded.'}


def parse_multipart(content_type, body):
    """Bounded, deliberately narrow multipart form, with no MIME coercions."""
    match = re.fullmatch(r'multipart/form-data; boundary=(?:"([A-Za-z0-9_-]{1,70})"|([A-Za-z0-9_-]{1,70}))', content_type)
    if not match:
        raise RequestError(415, 'RR_MULTIPART_REQUIRED')
    boundary = (match[1] or match[2]).encode('ascii')
    marker = b'--' + boundary
    if not body.startswith(marker+b'\r\n') or not body.endswith(b'\r\n'+marker+b'--\r\n'):
        raise RequestError(400, 'RR_MULTIPART_INVALID')
    chunks = body[len(marker)+2:-len(marker)-6].split(b'\r\n'+marker+b'\r\n')
    if not 1 <= len(chunks) <= 3:
        raise RequestError(400, 'RR_FIELDS_INVALID')
    fields = {}
    for chunk in chunks:
        head, separator, data = chunk.partition(b'\r\n\r\n')
        if not separator or len(head)>2048:
            raise RequestError(400, 'RR_PART_INVALID')
        headers = {}
        for line in head.split(b'\r\n'):
            try:
                # Browser filenames may use UTF-8. Header/field names and MIME
                # remain fixed ASCII tokens through the allowlists below.
                key, val = line.decode('utf-8', errors='strict').split(':',1)
            except (ValueError,UnicodeError):
                raise RequestError(400, 'RR_PART_INVALID') from None
            key = key.lower()
            if key in headers or key not in ('content-disposition','content-type'):
                raise RequestError(400, 'RR_PART_HEADERS_INVALID')
            headers[key] = val.strip()
        disposition = re.fullmatch(r'form-data; name="(feedback|attachment\.[01])"(?:; filename="([^"\r\n]{1,200})")?', headers.get('content-disposition',''))
        if not disposition or disposition[1] in fields:
            raise RequestError(400, 'RR_FIELDS_INVALID')
        name, filename = disposition.groups()
        media = headers.get('content-type','')
        if name == 'feedback':
            if filename is not None or media not in ('application/json','application/json; charset=utf-8'):
                raise RequestError(415, 'RR_JSON_REQUIRED')
            if len(data)>16384:
                raise RequestError(413, 'RR_JSON_TOO_LARGE')
        else:
            if filename is None or '/' in filename or '\\' in filename or any(not c.isprintable() for c in filename):
                raise RequestError(400, 'RR_FILENAME_INVALID')
            if media not in ('image/png','image/jpeg','image/webp','text/plain','text/plain; charset=utf-8'):
                raise RequestError(415, 'RR_ATTACHMENT_TYPE_INVALID')
            if len(data)>1048576:
                raise RequestError(413, 'RR_ATTACHMENT_TOO_LARGE')
        fields[name] = (data, filename, media)
    if 'feedback' not in fields or ('attachment.1' in fields and 'attachment.0' not in fields):
        raise RequestError(400, 'RR_FIELDS_INVALID')
    from .rr_payload import parse_document
    document = parse_document(fields.pop('feedback')[0])
    if type(document) is not dict:
        raise RequestError(400, 'RR_DOCUMENT_INVALID')
    metadata = document.get('attachments',[])
    if not isinstance(metadata,list) or len(metadata) != len(fields):
        raise RequestError(400, 'RR_ATTACHMENTS_INVALID')
    attachments = []
    for i, meta in enumerate(metadata):
        data, filename, media = fields['attachment.'+str(i)]
        if not isinstance(meta,dict) or 'attachment_id' not in meta:
            raise RequestError(400, 'RR_ATTACHMENTS_INVALID')
        attachments.append({'attachment_id':meta['attachment_id'],'filename':filename,'content_type':'text/plain' if media == 'text/plain; charset=utf-8' else media,'data':data})
    return document, attachments


class RRApplication:
    def __init__(self, store, auth, manifest, resources):
        self.store, self.auth = store, auth
        self.manifest = json.loads(canonical(manifest))
        if self.manifest['project_id'] != auth.project_id:
            raise ValueError('project binding mismatch')
        self.resources = {}
        for meta in self.manifest['resources']:
            path = Path(resources[meta['id']])
            # Frozen bytes only: no later request can select or reopen a path.
            if any(p.is_symlink() for p in (path,*path.parents)):
                raise ValueError('resource symlink rejected')
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != meta['sha256']:
                raise ValueError('resource changed')
            self.resources[meta['id']] = data

    def dispatch(self, method, path, headers, body, peer_uid):
        if '?' in path or '#' in path or '%' in path or '\\' in path or '..' in path:
            raise RequestError(404, 'RR_NOT_FOUND')
        if path == '/__rr-local/v1/challenge' and method == 'GET':
            if peer_uid != self.auth.gateway_uid or body:
                raise RequestError(404, 'RR_NOT_FOUND')
            return 200, {'challenge':self.auth.challenge}, 'application/json'
        try:
            device = self.auth.verify(peer_uid, method, path, body, headers.get('x-pch-rr-auth',''))
        except GatewayDenied:
            raise RequestError(404, 'RR_NOT_FOUND') from None
        if headers.get('origin') != self.auth.origin:
            raise RequestError(404, 'RR_NOT_FOUND')
        if method == 'GET' and body:
            raise RequestError(400, 'RR_BODY_REJECTED')
        if method == 'GET' and path == '/__rr/v1/manifest':
            return 200, self.manifest, 'application/json'
        resource = re.fullmatch(r'/__rr/v1/resources/([a-z0-9_-]{1,64})',path)
        if method == 'GET' and resource and resource[1] in self.resources:
            return 200,self.resources[resource[1]],'image/svg+xml'
        if method == 'POST' and path == '/__rr/v1/feedback':
            document, attachments = parse_multipart(headers.get('content-type',''),body)
            from .rr_payload import validate_submission
            validate_submission(document, attachments, self.auth.project_id)
            if document.get('source') != self.manifest['source'] or document.get('page_path') not in self.manifest['page_paths'] or document.get('resource') not in self.manifest['resources']:
                raise RequestError(409,'RR_SOURCE_MISMATCH')
            status, receipt = self.store.submit(device,document,attachments,guard=lambda:self.auth.check_binding(peer_uid,device))
            return status,receipt,'application/json'
        request = re.fullmatch(r'/__rr/v1/feedback/by-request/([0-9a-f-]{36})',path)
        if method == 'GET' and request:
            return 200,self.store.by_request(device,request[1]),'application/json'
        attachment = re.fullmatch(r'/__rr/v1/feedback/([A-Za-z0-9_-]{1,128})/attachments/([A-Za-z0-9_-]{1,128})',path)
        if method == 'GET' and attachment:
            item = self.store.attachment(device,attachment[1],attachment[2])
            return 200,item['data'],item['content_type']
        feedback = re.fullmatch(r'/__rr/v1/feedback/([A-Za-z0-9_-]{1,128})',path)
        if method == 'GET' and feedback:
            return 200,self.store.get(device,feedback[1]),'application/json'
        raise RequestError(404,'RR_NOT_FOUND')


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    def log_message(self,*args):
        pass
    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler normally embeds an invalid request line in HTML.
        data = b'{"code":"RR_REQUEST_REJECTED"}'
        self.request_version = 'HTTP/1.0'
        self.send_response_only(code)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Connection','close')
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True
    def _run(self):
        try:
            self.connection.settimeout(5)
            names = [k.lower() for k in self.headers.keys()]
            if len(names) != len(set(names)) or any(n in names for n in ('cookie','authorization','transfer-encoding','proxy-authorization','forwarded','x-forwarded-for')):
                raise RequestError(400,'RR_HEADERS_REJECTED')
            size = self.headers.get('Content-Length','0')
            if not re.fullmatch('[0-9]{1,10}',size):
                raise RequestError(400,'RR_LENGTH_INVALID')
            size = int(size)
            if size > MAX_BODY:
                raise RequestError(413,'RR_BODY_TOO_LARGE')
            body = self.rfile.read(size)
            if len(body) != size:
                raise RequestError(400,'RR_BODY_INCOMPLETE')
            _,uid,_ = struct.unpack('3i',self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            code,value,media = self.server.app.dispatch(self.command,self.path,{k.lower():v for k,v in self.headers.items()},body,uid)
        except Exception as error:
            # Only typed interface failures are returned; no exception detail.
            code = getattr(error,'status',503)
            if code not in (400,404,409,413,415,503):
                code = 503
            value = {'code':getattr(error,'code','RR_UNAVAILABLE')}
            media = 'application/json'
        data = canonical(value) if media == 'application/json' else value
        self.send_response(code)
        self.send_header('Content-Type',media)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'none'; sandbox")
        self.end_headers()
        self.wfile.write(data)
    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _run


class UnixRRServer(socketserver.UnixStreamServer):
    allow_reuse_address = False
    def __init__(self,socket_path,app,*,fixture=False,socket_gid=None):
        path = Path(socket_path)
        parent = path.parent
        info = parent.lstat()
        if parent.is_symlink() or any(p.is_symlink() for p in parent.parents) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError('socket directory rejected')
        if fixture:
            if not app.auth.fixture or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('fixture socket directory rejected')
        elif (app.auth.fixture or socket_gid is None or info.st_gid != socket_gid or stat.S_IMODE(info.st_mode) != 0o750 or socket_gid == os.getegid()):
            raise ValueError('protected gateway group required')
        # Never unlink an existing socket or overwrite a user path.
        if path.exists() or path.is_symlink():
            raise ValueError('socket already exists')
        self.app, self._owned_path = app, path
        super().__init__(str(path),_Handler)
        self._socket_identity = (path.stat().st_dev,path.stat().st_ino)
        try:
            if not fixture:
                os.chown(path,-1,socket_gid)
            os.chmod(path,0o600 if fixture else 0o660)
        except BaseException:
            self.server_close()
            raise
    def handle_error(self, request, client_address):
        # Never emit a request/body/credential traceback from the gateway.
        pass
    def server_close(self):
        super().server_close()
        try:
            info = self._owned_path.lstat()
            if stat.S_ISSOCK(info.st_mode) and (info.st_dev,info.st_ino) == self._socket_identity:
                self._owned_path.unlink()
        except (FileNotFoundError,AttributeError):
            pass
