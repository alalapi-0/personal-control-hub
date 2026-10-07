"""Version-bound registered media. No discovery, execution or content cache.

The current admission supports Root-preverified synthetic fixtures only. Starting
the ordinary service exposes metadata and a closed read gate, never real bytes.
Pins are disposable integrity evidence over DesignStore facts, not authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat

from .preview_service import PreviewService
from .service_contract import ServiceError
from .workbench_store import digest

MAX_RESPONSE = 1024 * 1024
MAX_PREVERIFY = 32 * 1024 * 1024
MAX_PINS = 64
KINDS = {'.txt': 'text', '.md': 'text', '.json': 'text', '.log': 'text',
         '.html': 'html_source', '.htm': 'html_source', '.mp4': 'video',
         '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.webp': 'image', '.gif': 'image'}
MIMES = {'text': 'text/plain; charset=utf-8', 'html_source': 'text/plain; charset=utf-8',
         'video': 'video/mp4'}


@dataclass(frozen=True)
class MaterialResponse:
    data: bytes
    content_type: str
    status: int
    headers: dict


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def byte_range(value, size):
    """Single inclusive range; unsupported and excessive requests never broaden."""
    if value is None:
        if size > MAX_RESPONSE: raise ServiceError('MATERIAL_RANGE_REQUIRED', status=413)
        return (0, size - 1, 200)
    if not isinstance(value, str) or len(value) > 64:
        raise ServiceError('INVALID_MATERIAL_RANGE')
    match = re.fullmatch(r'bytes=([0-9]{0,20})-([0-9]{0,20})', value)
    if not match or not any(match.groups()): raise ServiceError('INVALID_MATERIAL_RANGE')
    first, last = match.groups()
    if first:
        start = int(first); end = int(last) if last else size - 1
        if end < start or start >= size: return (0, -1, 416)
        end = min(end, size - 1)
    else:
        count = int(last)
        if not count or not size: return (0, -1, 416)
        start, end = max(0, size - count), size - 1
    if end - start + 1 > MAX_RESPONSE: raise ServiceError('MATERIAL_RANGE_TOO_LARGE', status=413)
    return (start, end, 206)


class MaterialService:
    def __init__(self, projects, designs):
        self.projects, self.designs = projects, designs
        self._pins = {}

    @staticmethod
    def _auth(authorize):
        if authorize() is not True: raise ServiceError('OWNER_AUTH_REQUIRED', status=401)

    def _facts(self, authorize):
        self._auth(authorize)
        resolver = self.projects._resolver()
        resolver._check_registry()
        state = self.designs._read()
        artifacts = {f['id']: f for f in state['facts'] if f['kind'] == 'artifact_ref'}
        entries = []
        for candidate in (f for f in state['facts'] if f['kind'] == 'candidate'):
            members = candidate['scope']['members']
            if len(members) != 1: continue
            pid = members[0]['project_id']; project = resolver.projects.get(pid)
            if project is None or not PreviewService._eligible(project): continue
            try: self.designs._assert_current_candidate(state, candidate)
            except ServiceError: continue
            bindings = {b['artifact_id']: b['sha256'] for b in candidate['artifact_bindings']}
            for aid in sorted(set(bindings) | set(candidate['evidence_refs'])):
                artifact = artifacts.get(aid)
                if (artifact is None or artifact['scope'] != candidate['scope']
                        or artifact['classification'] != candidate['classification']
                        or (aid in bindings and artifact['sha256'] != bindings[aid])): continue
                location = artifact['location']
                kind = 'review_reference' if location['kind'] == 'figma' else KINDS.get(Path(location['value']).suffix.lower())
                if kind is None: continue
                binding = {'project_id': pid, 'root_identity': digest(project['root_path']),
                           'registry_hash': resolver.authority['registry_hash'],
                           'project_policy_hash': digest(project), 'store_revision': state['revision'],
                           'store_hash': digest(state), 'candidate_id': candidate['id'],
                           'candidate_revision': candidate['revision'], 'candidate_hash': candidate['content_hash'],
                           'artifact_id': aid, 'artifact_hash': digest(artifact),
                           'artifact_sha256': artifact['sha256'], 'kind': kind,
                           'hub_root': str(self.designs.store.hub_root)}
                mid = 'material-' + digest([pid, candidate['id'], aid])[:32]
                version = digest(binding)
                entries.append({'id': mid, 'version': version, 'binding': binding,
                                'artifact': artifact, 'project_name': project['name']})
        resolver._check_registry()
        self._auth(authorize)
        if len({e['id'] for e in entries}) != len(entries): raise ServiceError('MATERIAL_AMBIGUOUS', status=503)
        return state, entries

    def catalog(self, authorize):
        state, entries = self._facts(authorize)
        return {'store_revision': state['revision'], 'classification': state['store_classification'],
                'content_cache': False, 'real_media_enabled': False,
                'materials': [{'id': e['id'], 'version': e['version'],
                    'project_id': e['binding']['project_id'], 'project_name': e['project_name'],
                    'candidate_id': e['binding']['candidate_id'],
                    'candidate_revision': e['binding']['candidate_revision'],
                    'artifact_id': e['binding']['artifact_id'], 'sha256': e['binding']['artifact_sha256'],
                    'kind': e['binding']['kind'],
                    'available': e['binding']['kind'] == 'review_reference' or
                        self._pins.get(e['id'], {}).get('version') == e['version'],
                    'review_reference': e['artifact']['location']['value'] if e['binding']['kind'] == 'review_reference' else None,
                    'read_url': '/api/materials/' + e['id'] + '?version=' + e['version'],
                    'execution_allowed': False} for e in entries]}

    def _select(self, mid, version, authorize):
        if not re.fullmatch(r'material-[0-9a-f]{32}', mid or '') or not re.fullmatch(r'[0-9a-f]{64}', version or ''):
            raise ServiceError('INVALID_MATERIAL_BINDING')
        _, entries = self._facts(authorize)
        matches = [e for e in entries if e['id'] == mid]
        if len(matches) != 1: raise ServiceError('MATERIAL_UNAVAILABLE', status=404)
        if matches[0]['version'] != version: raise ServiceError('MATERIAL_STALE', status=409)
        return matches[0]

    @contextmanager
    def _open(self, entry):
        # Walk every component including the absolute root, without following
        # aliases; nonblocking open makes FIFO/device rejection safe.
        value = entry['artifact']['location']['value']; relative = Path(value)
        allowed = (Path('data/design_governance'), Path('docs/reports/ui_design_governance'))
        if (entry['artifact']['location']['kind'] != 'hub_relative' or relative.is_absolute()
                or not relative.parts or relative.as_posix() != value or '\\' in value
                or any(p in {'.', '..'} for p in relative.parts)
                or not any(relative.is_relative_to(a) for a in allowed)):
            raise ServiceError('MATERIAL_UNAVAILABLE', status=404)
        root = self.designs.store.hub_root
        if root / relative in {self.designs.store.path, self.designs.store.lock_path}:
            raise ServiceError('MATERIAL_UNAVAILABLE', status=404)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        directory = descriptor = None; chain = []
        try:
            directory = os.open('/', flags | os.O_DIRECTORY)
            for part in (*root.parts[1:], *relative.parts[:-1]):
                child = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
                os.close(directory); directory = child
                info = os.fstat(directory); chain.append((info.st_dev, info.st_ino, info.st_mode))
            descriptor = os.open(relative.name, flags, dir_fd=directory)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_PREVERIFY:
                raise ServiceError('MATERIAL_UNAVAILABLE', status=404)
            yield descriptor, info, tuple(chain)
        except OSError:
            raise ServiceError('MATERIAL_UNAVAILABLE', status=404) from None
        finally:
            if descriptor is not None: os.close(descriptor)
            if directory is not None: os.close(directory)

    def prepare_fixture(self, artifact_ids):
        """Trusted Root/test setup only; there is intentionally no HTTP route."""
        if self.designs.store.fixture is not True:
            raise ServiceError('REAL_MATERIAL_GATE_CLOSED', status=403)
        if not isinstance(artifact_ids, set) or not 0 < len(artifact_ids) <= MAX_PINS:
            raise ServiceError('INVALID_MATERIAL_PREVERIFY')
        _, entries = self._facts(lambda: True)
        entries = [e for e in entries if e['binding']['artifact_id'] in artifact_ids and e['binding']['kind'] != 'review_reference']
        if {e['binding']['artifact_id'] for e in entries} != artifact_ids:
            raise ServiceError('MATERIAL_UNAVAILABLE', status=404)
        pins = {}; total = 0
        for e in entries:
            with self._open(e) as (fd, before, chain):
                total += before.st_size
                if total > MAX_PREVERIFY: raise ServiceError('MATERIAL_PREVERIFY_TOO_LARGE', status=413)
                data = bytearray()
                while len(data) < before.st_size:
                    chunk = os.read(fd, min(65536, before.st_size - len(data)))
                    if not chunk: break
                    data.extend(chunk)
                if _signature(before) != _signature(os.fstat(fd)) or len(data) != before.st_size:
                    raise ServiceError('MATERIAL_STALE', status=409)
                if hashlib.sha256(data).hexdigest() != e['binding']['artifact_sha256']:
                    raise ServiceError('MATERIAL_HASH_MISMATCH', status=409)
                kind = e['binding']['kind']; mime = MIMES.get(kind)
                if kind == 'image': mime = self.designs._raster_type(bytes(data[:16]), Path(e['artifact']['location']['value']).suffix.lower())
                if kind in {'text', 'html_source'}:
                    if before.st_size > MAX_RESPONSE: raise ServiceError('MATERIAL_TOO_LARGE', status=413)
                    try: data.decode('utf-8')
                    except UnicodeError: raise ServiceError('MATERIAL_MIME_REJECTED', status=415) from None
                    if b'\0' in data: raise ServiceError('MATERIAL_MIME_REJECTED', status=415)
                if kind == 'video' and not (len(data) >= 16 and data[4:8] == b'ftyp' and bytes(data[8:12]) in {b'isom', b'iso2', b'mp41', b'mp42', b'avc1'}):
                    raise ServiceError('MATERIAL_MIME_REJECTED', status=415)
                if mime is None or (kind == 'image' and before.st_size > MAX_RESPONSE):
                    raise ServiceError('MATERIAL_MIME_REJECTED', status=415)
                pin = {'version': e['version'], 'signature': _signature(before), 'chain': chain, 'mime': mime}
            with self._open(e) as (_, current, current_chain):
                if _signature(current) != pin['signature'] or current_chain != chain:
                    raise ServiceError('MATERIAL_STALE', status=409)
            self._select(e['id'], e['version'], lambda: True)
            pins[e['id']] = pin
        # A failed preparation never partially publishes new pins.
        self._pins = pins

    def read(self, mid, version, *, range_header=None, authorize):
        e = self._select(mid, version, authorize)
        pin = self._pins.get(mid)
        if pin is None or pin['version'] != version: raise ServiceError('MATERIAL_NOT_PREVERIFIED', status=503)
        if range_header is not None and e['binding']['kind'] != 'video':
            raise ServiceError('MATERIAL_RANGE_UNSUPPORTED')
        start, end, status = byte_range(range_header, pin['signature'][4])
        with self._open(e) as (fd, before, chain):
            if _signature(before) != pin['signature'] or chain != pin['chain']:
                raise ServiceError('MATERIAL_STALE', status=409)
            self._auth(authorize)
            data = bytearray(); remaining = max(0, end - start + 1)
            os.lseek(fd, start, os.SEEK_SET)
            while remaining:
                self._auth(authorize)
                chunk = os.read(fd, min(65536, remaining))
                if not chunk: raise ServiceError('MATERIAL_STALE', status=409)
                data.extend(chunk); remaining -= len(chunk)
            if _signature(os.fstat(fd)) != pin['signature']: raise ServiceError('MATERIAL_STALE', status=409)
        with self._open(e) as (_, current, current_chain):
            if _signature(current) != pin['signature'] or current_chain != pin['chain']:
                raise ServiceError('MATERIAL_STALE', status=409)
        self._select(mid, version, authorize)
        headers = {'ETag': '"' + e['binding']['artifact_sha256'] + '"',
                   'Content-Disposition': 'inline; filename="' + e['binding']['artifact_id'] + Path(e['artifact']['location']['value']).suffix.lower() + '"'}
        if e['binding']['kind'] == 'video': headers['Accept-Ranges'] = 'bytes'
        if status == 206: headers['Content-Range'] = f'bytes {start}-{end}/{before.st_size}'
        if status == 416: headers['Content-Range'] = f'bytes */{before.st_size}'
        return MaterialResponse(bytes(data), pin['mime'], status, headers)
