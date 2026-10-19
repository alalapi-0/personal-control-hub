"""Bounded RR v1 documents and attachment containers; never filesystem selectors."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import struct
import uuid
import zlib
from urllib.parse import unquote

MAX_DOCUMENT = 16 * 1024
MAX_ATTACHMENT = 1024 * 1024
OPAQUE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')


class RRFeedbackError(Exception):
    def __init__(self, status: int, code: str, *, outcome='NOT_COMMITTED'):
        super().__init__(code)
        self.status, self.code, self.outcome = status, code, outcome


def _bad(code='RR_DOCUMENT_INVALID', status=400):
    raise RRFeedbackError(status, code)


def _text(value, *, maximum=16384, empty=True):
    if type(value) is not str or (not empty and not value) or len(value) > maximum:
        _bad()
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeError:
        _bad()
    if any((ord(c) < 32 and c not in '\n\r\t') or ord(c) == 127 for c in value):
        _bad()
    return value


def _tree(value, depth=0):
    if depth > 32:
        _bad()
    if type(value) is dict:
        for key, item in value.items():
            _text(key)
            _tree(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _tree(item, depth + 1)
    elif type(value) is str:
        _text(value, maximum=2 * MAX_ATTACHMENT)
    elif type(value) is int:
        if not -(2**63) <= value < 2**63:
            _bad()
    elif value is not None and type(value) is not bool:
        _bad()


def canonical_json(document):
    _tree(document)
    try:
        return json.dumps(document, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _bad()


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _bad('RR_DUPLICATE_JSON_KEY')
        value[key] = item
    return value


def parse_document(raw):
    if type(raw) is not bytes:
        _bad()
    if len(raw) > MAX_DOCUMENT:
        _bad('RR_DOCUMENT_TOO_LARGE', 413)
    try:
        document = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=_pairs,
                              parse_float=lambda _: _bad(), parse_constant=lambda _: _bad())
        if type(document) is not dict:
            _bad()
        canonical_json(document)
        return document
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _bad()


def valid_uuid(value):
    if type(value) is not str:
        _bad()
    try:
        if str(uuid.UUID(value)) != value:
            _bad()
    except ValueError:
        _bad()
    return value


def timestamp(value):
    _text(value, maximum=64, empty=False)
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if 'T' not in value or parsed.tzinfo is None or parsed.utcoffset() is None:
            _bad()
    except ValueError:
        _bad()
    return value


def opaque(value):
    if type(value) is not str or not OPAQUE.fullmatch(value):
        _bad()
    return value


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        _bad()


def _token(value):
    if type(value) is not str or not TOKEN.fullmatch(value):
        _bad()


def _sha(value):
    if type(value) is not str or not SHA.fullmatch(value):
        _bad()


def _page(value):
    _text(value, maximum=2048, empty=False)
    try:
        decoded = unquote(value, errors='strict')
    except UnicodeError:
        _bad()
    for path in (value, decoded):
        if (not path.startswith('/') or path.startswith('//') or any(c in path for c in '?#\\')
                or any(c.isspace() or not c.isprintable() for c in path)
                or any(part in ('.', '..') for part in path.split('/'))):
            _bad()
    # Nested encodings and malformed percent escapes have no useful registered-page role.
    if '%' in decoded or re.search(r'%(?![0-9A-Fa-f]{2})', value):
        _bad()


def _png(data):
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        return False
    offset = 8
    idat = bytearray()
    seen, ended, after_idat, palette = False, False, False, False
    rowbytes = height = 0
    while offset < len(data):
        if offset + 12 > len(data):
            return False
        length = int.from_bytes(data[offset:offset + 4], 'big')
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data) or not all(65 <= c <= 90 or 97 <= c <= 122 for c in kind):
            return False
        chunk = data[offset + 8:end - 4]
        if zlib.crc32(kind + chunk) & 0xffffffff != int.from_bytes(data[end - 4:end], 'big'):
            return False
        if not seen:
            if kind != b'IHDR' or length != 13:
                return False
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', chunk)
            allowed = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if (not 0 < width <= 4096 or not 0 < height <= 4096 or color not in allowed
                    or depth not in allowed[color] or compression or filtering or interlace):
                return False
            rowbytes = (width * {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color] * depth + 7) // 8
            if (rowbytes + 1) * height > 16 * 1024 * 1024:
                return False
            seen = True
        elif kind == b'IHDR':
            return False
        elif kind == b'PLTE':
            if idat or palette or length == 0 or length % 3 or length > 768:
                return False
            palette = True
        elif kind == b'IDAT':
            if after_idat or (color == 3 and not palette):
                return False
            idat.extend(chunk)
        elif kind == b'IEND':
            if length or end != len(data) or not idat:
                return False
            ended = True
        elif kind[0] & 32 == 0:
            return False
        if idat and kind not in (b'IDAT', b'IEND'):
            after_idat = True
        offset = end
    if not ended:
        return False
    size = (rowbytes + 1) * height
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(idat, size + 1)
        if len(raw) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            return False
        return all(raw[index] <= 4 for index in range(0, size, rowbytes + 1))
    except zlib.error:
        return False


def _jpeg(data):
    """Bounded JPEG marker/frame/scan framing, not a general image decoder."""
    if not data.startswith(b'\xff\xd8'):
        return False
    pos, frame, scan = 2, False, False
    while pos < len(data):
        if data[pos] != 255:
            return False
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos >= len(data):
            return False
        marker = data[pos]; pos += 1
        if marker == 0xd9:
            return frame and scan and pos == len(data)
        if marker not in {0xc0, 0xc1, 0xc2, 0xc4, 0xdb, 0xdd, 0xda, 0xfe, *range(0xe0, 0xf0)}:
            return False
        if pos + 2 > len(data):
            return False
        size = int.from_bytes(data[pos:pos + 2], 'big')
        if size < 2 or pos + size > len(data):
            return False
        payload = data[pos + 2:pos + size]
        if marker in (0xc0, 0xc1, 0xc2):
            if frame or len(payload) < 6 or payload[0] != 8:
                return False
            height, width, channels = struct.unpack('>HHB', payload[1:6])
            if not 0 < width <= 4096 or not 0 < height <= 4096 or channels not in (1, 3, 4) or len(payload) != 6 + 3 * channels:
                return False
            frame = True
        pos += size
        if marker == 0xda:
            if not frame or len(payload) < 4 or payload[0] not in (1, 2, 3, 4) or len(payload) != 4 + 2 * payload[0]:
                return False
            start = pos
            while pos < len(data):
                if data[pos] != 255:
                    pos += 1
                    continue
                if pos + 1 >= len(data):
                    return False
                if data[pos + 1] == 0 or 0xd0 <= data[pos + 1] <= 0xd7:
                    pos += 2
                    continue
                if data[pos + 1] == 255:
                    pos += 1
                    continue
                break
            if pos == start:
                return False
            scan = True
    return False


def _webp(data):
    if len(data) < 20 or data[:4] != b'RIFF' or data[8:12] != b'WEBP' or int.from_bytes(data[4:8], 'little') != len(data) - 8:
        return False
    pos, image, canvas = 12, None, None
    while pos < len(data):
        if pos + 8 > len(data):
            return False
        kind = data[pos:pos + 4]; size = int.from_bytes(data[pos + 4:pos + 8], 'little')
        end = pos + 8 + size
        if end + (size & 1) > len(data) or (size & 1 and data[end] != 0):
            return False
        body = data[pos + 8:end]
        if kind == b'VP8X':
            if pos != 12 or size != 10 or body[0] & 0xc3 or body[1:4] != b'\0\0\0':
                return False
            canvas = (1 + int.from_bytes(body[4:7], 'little'), 1 + int.from_bytes(body[7:10], 'little'))
        elif kind == b'VP8 ':
            if image is not None or size < 11 or body[0] & 1 or body[3:6] != b'\x9d\x01\x2a':
                return False
            image = (int.from_bytes(body[6:8], 'little') & 0x3fff, int.from_bytes(body[8:10], 'little') & 0x3fff)
        elif kind == b'VP8L':
            if image is not None or size < 6 or body[0] != 0x2f:
                return False
            bits = int.from_bytes(body[1:5], 'little')
            if bits >> 29:
                return False
            image = ((bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1)
        elif kind not in (b'ALPH', b'ICCP', b'EXIF', b'XMP '):
            return False
        pos = end + (size & 1)
    return bool(image and all(0 < n <= 4096 for n in image) and (canvas is None or canvas == image))


def _content(data, content_type):
    if type(content_type) is not str:
        _bad('RR_ATTACHMENT_TYPE_REJECTED', 415)
    if content_type == 'text/plain':
        try:
            _text(data.decode('utf-8', errors='strict'), maximum=MAX_ATTACHMENT)
            return
        except (UnicodeError, RRFeedbackError):
            _bad('RR_ATTACHMENT_TYPE_REJECTED', 415)
    checks = {'image/png': _png, 'image/jpeg': _jpeg, 'image/webp': _webp}
    if content_type not in checks or not checks[content_type](data):
        _bad('RR_ATTACHMENT_TYPE_REJECTED', 415)


def validate_submission(document, attachments, project_id):
    """Return canonical body and independently checked, in-document-order bytes."""
    context = document.get('context') if type(document) is dict else None
    required = ('schema', 'client_request_id', 'project_id', 'page_path', 'source', 'resource',
                'description', 'expected', 'actual', 'steps', 'submitted_at', 'attachments')
    _keys(document, required + (('context',) if type(document) is dict and 'context' in document else ()))
    if type(document) is dict and 'context' in document:
        _keys(context, ('data_revision', 'observed_at'))
        _sha(context['data_revision']); timestamp(context['observed_at'])
    body = canonical_json(document)
    if len(body) > MAX_DOCUMENT:
        _bad('RR_DOCUMENT_TOO_LARGE', 413)
    if type(document['schema']) is not int or document['schema'] != 1:
        _bad()
    valid_uuid(document['client_request_id'])
    opaque(document['project_id'])
    if document['project_id'] != project_id:
        _bad('RR_PROJECT_REJECTED', 404)
    _page(document['page_path'])
    source = document['source']; _keys(source, ('commit', 'build_id', 'dirty'))
    if type(source['commit']) is not str or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', source['commit']) or type(source['dirty']) is not bool:
        _bad()
    _token(source['build_id'])
    resource = document['resource']; _keys(resource, ('id', 'sha256', 'mode', 'preview_version'))
    opaque(resource['id']); _sha(resource['sha256']); _token(resource['mode']); _token(resource['preview_version'])
    for name in ('description', 'expected', 'actual'):
        _text(document[name])
    if type(document['steps']) is list:
        if len(document['steps']) > 128:
            _bad()
        for step in document['steps']:
            _text(step)
    else:
        _text(document['steps'])
    timestamp(document['submitted_at'])
    descriptors = document['attachments']
    if type(descriptors) is not list or type(attachments) is not list:
        _bad()
    if len(descriptors) > 2 or len(attachments) > 2:
        _bad('RR_ATTACHMENTS_TOO_LARGE', 413)
    if len(descriptors) != len(attachments):
        _bad('RR_ATTACHMENT_MISMATCH')
    seen, checked = set(), []
    for descriptor, item in zip(descriptors, attachments):
        _keys(descriptor, ('attachment_id', 'size', 'sha256'))
        ident = opaque(descriptor['attachment_id']); _sha(descriptor['sha256'])
        if ident in seen or type(descriptor['size']) is not int or descriptor['size'] < 0:
            _bad()
        seen.add(ident)
        _keys(item, ('attachment_id', 'filename', 'content_type', 'data'))
        if item['attachment_id'] != ident or type(item['data']) is not bytes:
            _bad('RR_ATTACHMENT_MISMATCH')
        data = item['data']
        if len(data) > MAX_ATTACHMENT or descriptor['size'] > MAX_ATTACHMENT:
            _bad('RR_ATTACHMENT_TOO_LARGE', 413)
        if len(data) != descriptor['size'] or hashlib.sha256(data).hexdigest() != descriptor['sha256']:
            _bad('RR_ATTACHMENT_MISMATCH')
        filename = _text(item['filename'], maximum=255, empty=False)
        if filename in ('.', '..') or any(c in filename for c in '/\\\r\n\t'):
            _bad()
        _content(data, item['content_type'])
        checked.append({**item, 'size': len(data), 'sha256': descriptor['sha256']})
    return {'document': json.loads(body), 'body': body, 'body_sha256': hashlib.sha256(body).hexdigest(),
            'attachments': checked}
