"""Bounded immutable local capture receipts and verified PNG bytes; no capture I/O."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import zlib
import uuid

from .connection_records import RecordError, exact, fingerprint, identifier, require
from .preview_policy import read_preview_artifact, validate_artifact_ref
from .time_semantics import validate_time_point

MAX_BYTES = 10 * 1024 * 1024
MAX_RECORDS = 256
MAX_MANIFEST = 128 * 1024
_FIELDS = {"schema_version", "request_id", "request_sha256", "project_id", "page_id", "domain", "status", "reason", "method",
           "captured_at", "observed_at", "source_sha256", "build_sha256", "source_proof_sha256", "runtime_verified",
           "artifact_ref", "artifact_sha256", "media_type", "width", "height", "expires_at", "provenance"}
_IMAGE_FIELDS = {"artifact_ref", "artifact_sha256", "media_type", "width", "height"}


def _time(value, nullable=False):
    if value is None and nullable:
        return
    validate_time_point({"kind": "timestamp", "value": value, "timezone": None})


def _dimensions(width, height):
    require(type(width) is int and type(height) is int and 1 <= width <= 4096 and 1 <= height <= 4096
            and width * height <= 12_000_000, "capture image dimensions rejected")


def validate_capture_record(record):
    exact(record, _FIELDS, "capture record")
    require(record["schema_version"] == "1.0", "unsupported capture record schema")
    for key in ("request_id", "project_id", "page_id"):
        identifier(record[key], "capture " + key)
    fingerprint(record["request_sha256"], "capture request hash")
    require(record["domain"] == "implementation", "capture domain rejected")
    require(type(record["status"]) is str and record["status"] in {"success", "failed"}, "capture status rejected")
    require(type(record["method"]) is str and record["method"] in {"web", "existing_desktop"}, "capture method rejected")
    for key in ("source_sha256", "build_sha256", "source_proof_sha256"):
        if record[key] is not None:
            fingerprint(record[key], "capture " + key)
    _time(record["captured_at"], nullable=True)
    _time(record["observed_at"])
    _time(record["expires_at"])
    require(type(record["runtime_verified"]) is bool, "capture runtime binding must be boolean")
    provenance = record["provenance"]
    exact(provenance, {"kind", "report_sha256", "reported_at", "historical_candidate"}, "capture provenance")
    require(provenance["kind"] == ("web_capture" if record["method"] == "web" else "existing_desktop"),
            "capture provenance method mismatch")
    for key in ("report_sha256", "historical_candidate"):
        if provenance[key] is not None:
            fingerprint(provenance[key], "capture provenance " + key)
    _time(provenance["reported_at"], nullable=True)
    if record["status"] == "failed":
        require(type(record["reason"]) is str and bool(re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", record["reason"])),
                "capture failure reason must be a public code")
        require(all(record[key] is None for key in _IMAGE_FIELDS) and record["runtime_verified"] is False,
                "failed capture cannot claim image or runtime binding")
    else:
        require(record["reason"] is None, "successful capture cannot claim failure")
        _dimensions(record["width"], record["height"])
        fingerprint(record["artifact_sha256"], "capture artifact")
        validate_artifact_ref(record["artifact_ref"])
        expected = f'data/previews/images/{record["project_id"]}/{record["artifact_sha256"]}.png'
        require(record["artifact_ref"] == expected and record["media_type"] == "image/png", "capture image binding rejected")
        require(record["method"] != "web" or record["captured_at"] is not None, "web capture time required")
        require(not record["runtime_verified"] or record["method"] == "web" and record["source_sha256"] is not None,
                "capture runtime binding lacks web source")
    return deepcopy(record)


def png_info(data):
    """Validate PNG framing, CRCs, critical chunk order and actual scanline data."""
    require(type(data) is bytes and 0 < len(data) <= MAX_BYTES and data.startswith(b'\x89PNG\r\n\x1a\n'),
            "capture requires bounded PNG bytes")
    offset, header, palette, ended, idat_ended = 8, None, False, False, False
    compressed = bytearray()
    seen_idat = False
    while offset < len(data):
        require(offset + 12 <= len(data), "truncated PNG chunk")
        size = struct.unpack_from('>I', data, offset)[0]
        kind = data[offset + 4:offset + 8]
        require(bool(re.fullmatch(b'[A-Za-z]{4}', kind)) and not kind[2] & 32, "invalid PNG chunk type")
        end = offset + 12 + size
        require(end <= len(data), "truncated PNG chunk payload")
        payload = data[offset + 8:offset + 8 + size]
        crc = struct.unpack_from('>I', data, offset + 8 + size)[0]
        require(zlib.crc32(kind + payload) & 0xffffffff == crc, "PNG chunk checksum mismatch")
        require(header is not None or kind == b'IHDR', "PNG header must be first")
        if kind == b'IHDR':
            require(header is None and size == 13 and offset == 8, "invalid PNG header")
            header = struct.unpack('>IIBBBBB', payload)
            width, height, depth, color, compression, filtering, interlace = header
            _dimensions(width, height)
            require(color in {0, 2, 3, 4, 6} and depth in {0: {1, 2, 4, 8, 16}, 2: {8, 16},
                    3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}[color]
                    and compression == 0 and filtering == 0 and interlace in (0, 1), "unsupported PNG header")
        elif kind == b'PLTE':
            require(not palette and not seen_idat and color not in {0, 4} and 0 < size <= 768 and size % 3 == 0,
                    "invalid PNG palette")
            require(color != 3 or size // 3 <= 2 ** depth, "PNG palette exceeds bit depth")
            palette = True
        elif kind == b'IDAT':
            require(not idat_ended and (color != 3 or palette), "invalid PNG image chunk order")
            seen_idat = True
            compressed.extend(payload)
        elif kind == b'IEND':
            require(size == 0 and seen_idat and end == len(data), "invalid PNG end or trailing bytes")
            ended = True
        else:
            require(bool(kind[0] & 32), "unknown critical PNG chunk")
        if seen_idat and kind != b'IDAT':
            idat_ended = True
        offset = end
    require(ended and header is not None and bool(compressed), "incomplete PNG image")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    passes = [(0, 0, 1, 1)] if interlace == 0 else [
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
    rows = []
    for x, y, dx, dy in passes:
        columns, lines = max(0, (width - x + dx - 1) // dx), max(0, (height - y + dy - 1) // dy)
        if columns and lines:
            rows.extend([1 + (columns * channels * depth + 7) // 8] * lines)
    expected = sum(rows)
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, expected + 1)
        require(len(raw) == expected and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,
                "PNG image data length or stream rejected")
    except zlib.error:
        raise RecordError("invalid PNG compressed image") from None
    position = 0
    for length in rows:
        require(raw[position] <= 4, "invalid PNG scanline filter")
        position += length
    return {"media_type": "image/png", "width": width, "height": height}


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class CaptureStore:
    def __init__(self, root):
        require(isinstance(root, (str, Path)), "explicit capture root required")
        self.root = Path(root)
        require(self.root.is_absolute() and '..' not in self.root.parts, "absolute capture root required")

    @contextmanager
    def _directory(self, parts, *, create=False):
        opened, names = [], (*self.root.parts[1:], *parts)
        try:
            opened.append(os.open(self.root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            for index, name in enumerate(names):
                parent = opened[-1]
                # The selected Hub root must already exist; only its output descendants may be created.
                if create and index >= len(self.root.parts) - 1:
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=parent)
                    except FileExistsError:
                        pass
                opened.append(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent))
            def verify():
                for index, name in enumerate(names):
                    linked = os.stat(name, dir_fd=opened[index], follow_symlinks=False)
                    pinned = os.fstat(opened[index + 1])
                    require(stat.S_ISDIR(linked.st_mode) and (linked.st_dev, linked.st_ino) == (pinned.st_dev, pinned.st_ino),
                            "capture directory binding changed")
            verify()
            yield opened[-1], verify
            verify()
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)

    def _read(self, directory, name, limit):
        descriptor = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        try:
            before = os.fstat(descriptor)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
                    "unsafe capture file")
            result = bytearray()
            while len(result) <= limit:
                block = os.read(descriptor, min(65536, limit + 1 - len(result)))
                if not block:
                    break
                result.extend(block)
            after = os.fstat(descriptor)
            linked = os.stat(name, dir_fd=directory, follow_symlinks=False)
            require(len(result) <= limit and _identity(before) == _identity(after) == _identity(linked),
                    "capture file binding changed")
            return bytes(result)
        finally:
            os.close(descriptor)

    def _decode(self, raw, *, project_id, request_id):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                require(key not in result, "duplicate capture manifest key")
                result[key] = value
            return result
        try:
            record = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
        except (UnicodeError, ValueError, RecursionError):
            raise RecordError("invalid capture manifest") from None
        record = validate_capture_record(record)
        require(record['project_id'] == project_id and record['request_id'] == request_id, "capture manifest path mismatch")
        return record

    def records(self, project_id):
        identifier(project_id, "capture project")
        try:
            with self._directory(('data', 'previews', 'captures', project_id)) as (directory, verify):
                names = []
                with os.scandir(directory) as entries:
                    for entry in entries:
                        require(len(names) < MAX_RECORDS, "capture record limit exceeded")
                        require(entry.name.endswith('.json'), "unexpected capture manifest entry")
                        identifier(entry.name[:-5], "capture manifest identity")
                        names.append(entry.name)
                result = [self._decode(self._read(directory, name, MAX_MANIFEST), project_id=project_id,
                                       request_id=name[:-5]) for name in sorted(names)]
                verify()
                return result
        except FileNotFoundError:
            # A missing namespace is empty, but a missing listed manifest is corruption.
            try:
                with self._directory(('data', 'previews', 'captures', project_id)):
                    raise RecordError("capture manifest disappeared")
            except FileNotFoundError:
                return []
            except OSError:
                raise RecordError("capture records rejected") from None
        except OSError:
            raise RecordError("capture records rejected") from None

    def image(self, record):
        record = validate_capture_record(record)
        require(record['status'] == 'success', "failed capture has no image")
        data = read_preview_artifact(self.root, record['artifact_ref'], expected_sha256=record['artifact_sha256'])
        require(png_info(data) == {key: record[key] for key in ('media_type', 'width', 'height')},
                "capture PNG metadata mismatch")
        return data

    def _write(self, directory, name, data, verify):
        verify()
        temporary = '.capture-' + uuid.uuid4().hex + '.tmp'
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        published = False
        try:
            os.fchmod(descriptor, 0o600)
            position = 0
            while position < len(data):
                written = os.write(descriptor, data[position:])
                require(written > 0, "capture file write failed")
                position += written
            os.fsync(descriptor)
            info = os.fstat(descriptor)
            linked = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and _identity(info) == _identity(linked),
                    "capture temporary binding changed")
            verify()
            os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            published = True
        finally:
            try:
                # Only this invocation's exact temporary inode is disposable.
                linked = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
                pinned = os.fstat(descriptor)
                require((linked.st_dev, linked.st_ino) == (pinned.st_dev, pinned.st_ino),
                        "capture temporary binding changed")
                os.unlink(temporary, dir_fd=directory)
                if published:
                    final = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    require(stat.S_ISREG(final.st_mode) and final.st_nlink == 1
                            and (final.st_dev, final.st_ino) == (pinned.st_dev, pinned.st_ino),
                            "capture published binding changed")
                verify()
                os.fsync(directory)
            finally:
                os.close(descriptor)

    def commit(self, record, png=None):
        record = validate_capture_record(record)
        if record['status'] == 'success':
            info = png_info(png)
            require(info == {key: record[key] for key in ('media_type', 'width', 'height')}
                    and hashlib.sha256(png).hexdigest() == record['artifact_sha256'], "capture PNG binding mismatch")
        else:
            require(png is None, "failed capture cannot publish image bytes")
        try:
            existing = self.records(record['project_id'])
            matching = next((item for item in existing if item['request_id'] == record['request_id']), None)
            if matching is not None:
                require(matching == record, "immutable capture request conflict")
                require(png is None or self.image(matching) == png, "immutable capture image conflict")
                return deepcopy(record)
            require(len(existing) < MAX_RECORDS, "capture record limit exceeded")
            if png is not None:
                with self._directory(('data', 'previews', 'images', record['project_id']), create=True) as (directory, verify):
                    name = record['artifact_sha256'] + '.png'
                    try:
                        self._write(directory, name, png, verify)
                    except FileExistsError:
                        require(self._read(directory, name, MAX_BYTES) == png, "immutable capture image conflict")
            raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
            require(len(raw) <= MAX_MANIFEST, "capture manifest exceeds budget")
            with self._directory(('data', 'previews', 'captures', record['project_id']), create=True) as (directory, verify):
                name = record['request_id'] + '.json'
                try:
                    self._write(directory, name, raw, verify)
                except FileExistsError:
                    current = self._decode(self._read(directory, name, MAX_MANIFEST), project_id=record['project_id'],
                                           request_id=record['request_id'])
                    require(current == record, "immutable capture request conflict")
            return deepcopy(record)
        except OSError:
            raise RecordError("capture commit rejected") from None
