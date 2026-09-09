"""Bounded metadata projection; never follows source URIs or executes generation."""
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from hub.connection_records import require, timestamp
from hub.metric_documents import Projection, count, ids
from hub.metric_sources import metadata_path, read_metadata
from hub.metrics import issue

MAX_FILES = 1000
MAX_BYTES = 32 * 1024 * 1024
STATUSES = ('queued', 'running', 'stop_requested', 'stopped', 'failed', 'launch_failed', 'awaiting_pilot_review', 'completed')
REASONS = ('finish_reason_hard_stop:length',)


def _id(v):
    return isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,179}', v) is not None


def _chapters(v):
    return isinstance(v, list) and all(type(n) is int and n > 0 for n in v) and len(v) == len(set(v))


class ContinuationProjection(Projection):
    """Keep external evidence rooted and bind every import fact to its source."""
    def __init__(self, *args):
        super().__init__(*args)
        self.source_bindings = {}

    def reference(self, path):
        return path if path.startswith('adapter:') else str(Path(self.root).absolute() / path)

    def problem(self, path):
        super().problem(self.reference(path))

    def emit(self, name, value, unit, path, semantic=None, dims=None, **kwargs):
        digest = self.source_bindings.get((dims or {}).get('import_id'))
        super().emit(name, value, unit, self.reference(path),
                     [digest, semantic] if digest is not None else semantic, dims, **kwargs)


class Reader:
    def __init__(self, p):
        self.p, self.files, self.bytes = p, 0, 0

    def read(self, path):
        self.files += 1
        require(self.files <= MAX_FILES, 'file budget')
        self.bytes += metadata_path(self.p.root, path).stat().st_size
        require(self.bytes <= MAX_BYTES, 'byte budget')
        raw, _ = read_metadata(self.p.root, path)
        value = json.loads(raw)
        require(isinstance(value, dict), 'object')
        return value


def collect_continuation(root, pid, observed_at, spec):
    p = ContinuationProjection(spec.get('data_root', root), pid, observed_at, 'continuation')
    r = Reader(p)
    uid = spec.get('universe_id')
    if not _id(uid):
        p.emit('imports', None, 'imports', 'adapter:continuation.universe_id')
        return p.finish()
    p.dimensions = {'universe_id': uid}
    base = 'universes/' + uid
    upath = base + '/universe.json'
    try:
        require(not Path(p.root).is_symlink(), 'symlink root')
        u = r.read(upath)
        require(u.get('schema_version') == 'universe_record_v1' and u.get('universe_id') == uid, 'universe identity')
        require(ids(u.get('imports'), 'import_id') is not None and ids(u.get('source_refs'), 'ref_id') is not None, 'import references')
        require(len(u['imports']) <= MAX_FILES, 'import budget')
    except (OSError, ValueError, TypeError):
        p.emit('imports', None, 'imports', upath)
        return p.finish()
    p.emit('imports', len(u['imports']), 'imports', upath, sorted(x['import_id'] for x in u['imports']))
    bindings = {}
    refs = {s['ref_id']: s for s in u['source_refs']}
    for imp in sorted(u['imports'], key=lambda x: x['import_id']):
        iid = imp['import_id']; dims = {'import_id': iid}
        path = base + '/imports/' + iid + '/manifest.json'
        try:
            require(_id(iid) and imp.get('manifest_path') == path and refs.get(iid, {}).get('uri') == path, 'source reference')
            m = r.read(path)
            require(m.get('schema_version') == 'complete_novel_import_manifest_v1' and m.get('universe_id') == uid and m.get('import_id') == iid, 'manifest identity')
            entries = m.get('source_file_manifest')
            require(isinstance(entries, list) and len(entries) <= 100000, 'source manifest')
            require(all(isinstance(e, dict) and type(e.get('chapter_number')) is int and e['chapter_number'] > 0 and isinstance(e.get('raw_sha256'), str) and re.fullmatch('[a-f0-9]{64}', e['raw_sha256']) and count(e.get('byte_count')) is not None and isinstance(e.get('source_uri'), str) for e in entries), 'source entries')
            require(sorted(e['chapter_number'] for e in entries) == list(range(1, len(entries)+1)), 'source numbering')
            digest = hashlib.sha256(''.join(e['raw_sha256']+'  '+e['source_uri']+'\n' for e in entries).encode()).hexdigest()
            require(m.get('source_manifest_hash') == digest and all(x.get('source_manifest_hash') == digest for x in (imp, refs[iid])), 'source hash binding')
            if any('source_hash' in x for x in (m, imp, refs[iid])):
                require(isinstance(m.get('source_hash'), str) and re.fullmatch('[a-f0-9]{64}', m['source_hash']) and all(x.get('source_hash') == m['source_hash'] for x in (imp, refs[iid])), 'import source binding')
        except (OSError, ValueError, TypeError):
            p.emit('source_chapters', None, 'chapters', path, dims=dims)
            continue
        p.source_bindings[iid] = digest
        source_count = len(entries) if count(m.get('source_chapter_count')) == len(entries) else None
        p.emit('source_chapters', source_count, 'chapters', path, digest, dims, basis='Declared source file metadata and recomputed manifest binding; raw source contents are not verified.')
        units = m.get('chapter_units')
        valid_units = ids(units, 'chapter_id') is not None and all(type(x.get('order')) is int and 1 <= x['order'] <= len(entries) and isinstance(x.get('source_hash'), str) and re.fullmatch('[a-f0-9]{64}', x['source_hash']) for x in units)
        orders = sorted(x['order'] for x in units) if valid_units else []
        valid_units = valid_units and len(set(orders)) == len(orders)
        selected = m.get('selected_chapter_range')
        range_ok = isinstance(selected, list) and len(selected) == 2 and all(type(n) is int for n in selected) and 1 <= selected[0] <= selected[1] <= len(entries) and orders == list(range(selected[0], selected[1]+1))
        p.emit('imported_chapters', len(units) if valid_units and range_ok and count(m.get('chapter_count')) == len(units) else None, 'chapters', path, orders, dims, business_at=m.get('updated_at'))
        for field, total, name in [('character_count', 'total_characters', 'characters'), ('paragraph_count', 'total_paragraphs', 'paragraphs')]:
            vals = [count(x.get(field)) for x in units] if valid_units else []
            value = sum(vals) if valid_units and all(v is not None for v in vals) else None
            if value != count(m.get(total)): value = None
            p.emit(name, value, name, path, orders, dims)
        bindings[iid] = (path, digest, set(orders) if valid_units and range_ok else None)
    jbase = base + '/runs/real_api_reverse/jobs'
    try:
        folder = metadata_path(p.root, jbase)
        names = []
        with os.scandir(folder) as scan:
            for entry in scan:
                require(len(names) < MAX_FILES and not entry.is_symlink() and entry.is_dir(follow_symlinks=False) and _id(entry.name), 'job directory')
                names.append(entry.name)
        names.sort()
    except (OSError, ValueError, TypeError):
        p.emit('jobs', None, 'jobs', jbase)
        return p.finish()
    p.emit('jobs', len(names), 'jobs', jbase, names)
    unions = {iid: set() for iid in bindings}; union_ok = {iid: True for iid in bindings}
    for jid in names:
        path = jbase + '/' + jid + '/job.json'
        try:
            j = r.read(path); req = j.get('request')
            require(j.get('schema_version') == 'real_api_reverse_job_v1' and j.get('job_id') == jid and j.get('universe_id') == uid and isinstance(req, dict), 'job identity')
            iid = req.get('import_id'); binding = bindings.get(iid)
            require(binding is not None and req.get('universe_id') == uid and (req.get('manifest_path'), req.get('source_manifest_hash')) == binding[:2], 'request binding')
        except (OSError, ValueError, TypeError):
            p.emit('job_requested_chapters', None, 'chapters', path, dims={'job_id':jid})
            union_ok = dict.fromkeys(union_ok, False)
            continue
        dims = {'import_id':iid, 'job_id':jid}
        requested = req.get('requested_chapters'); committed = j.get('committed_chapters')
        requested_ok = _chapters(requested) and bool(requested) and requested == j.get('requested_chapters') and binding[2] is not None and set(requested) <= binding[2]
        commit_ok = requested_ok and _chapters(committed) and set(committed) <= set(requested)
        p.emit('job_requested_chapters', len(requested) if requested_ok else None, 'chapters', path, sorted(requested) if requested_ok else None, dims)
        p.emit('job_committed_chapters', len(committed) if commit_ok else None, 'chapters', path, sorted(committed) if commit_ok else None, dims)
        if commit_ok: unions[iid].update(committed)
        else: union_ok[iid] = False
        status = j.get('status')
        p.emit('job_status', 1 if status in STATUSES else None, 'jobs', path, dims={**dims,'status':status if status in STATUSES else 'unknown'})
        aids = j.get('attempt_ids'); current = j.get('current_attempt_id')
        try:
            require(isinstance(aids, list) and all(_id(a) for a in aids) and len(aids) == len(set(aids)) and len(aids) <= MAX_FILES and current in aids, 'attempt registration')
            p.emit('registered_attempts', len(aids), 'attempts', path, sorted(aids), dims)
            for aid in sorted(aids):
                apath = jbase + '/' + jid + '/attempts/' + aid + '/attempt.json'
                a = r.read(apath)
                require(a.get('schema_version') == 'real_api_reverse_attempt_v1' and a.get('attempt_id') == aid and a.get('job_id') == jid, 'attempt identity')
                ad = {**dims, 'attempt_id':aid}; state = a.get('status')
                p.emit('attempt_status', 1 if state in STATUSES else None, 'attempts', apath, dims={**ad,'status':state if state in STATUSES else 'unknown'})
                if aid == current:
                    reason = a.get('error')
                    code = reason if reason in REASONS else 'unknown'
                    blocked = state in ('stopped','failed','launch_failed','awaiting_pilot_review')
                    p.emit('current_attempt_blocker', int(blocked) if state in STATUSES else None, 'attempts', apath, dims={**ad,'reason_code':code})
                    if blocked:
                        age = None
                        try:
                            finished = a.get('finished_at'); timestamp(finished, 'finished_at'); timestamp(observed_at, 'observed_at')
                            age = (datetime.fromisoformat(observed_at.replace('Z','+00:00'))-datetime.fromisoformat(finished.replace('Z','+00:00'))).total_seconds()/86400
                            require(age >= 0, 'future stop')
                        except (ValueError, TypeError, AttributeError): age = None
                        p.emit('blocker_age_days', age, 'days', apath, dims=ad, business_at=finished if age is not None else None, basis='Elapsed days since the currently registered blocked attempt finished_at; not continuous backlog age.')
                        p.problems.append(issue(pid, 'continuation_current_attempt_blocked:' + code,
                            p.reference(apath), kind='business_blocker',
                            affected_items=len(set(requested)-set(committed)) if commit_ok else None,
                            started_at=finished if age is not None else None,
                            updated_at=finished if age is not None else None,
                            recovery_condition='Review the current stopped or gated attempt and its registered request; resolve the native blocker before explicitly authorizing continuation.'))
        except (OSError, ValueError, TypeError):
            p.emit('attempt_status', None, 'attempts', path, dims=dims)
    for iid, chapters in sorted(unions.items()):
        p.emit('committed_chapters_unique', len(chapters) if union_ok[iid] else None, 'chapters', jbase, sorted(chapters), {'import_id':iid}, basis='Union of committed chapter numbers across jobs bound to the same import and source manifest hash; overlapping jobs count once.')
    p.emit('generation_quality', None, 'reviews', jbase, reason='Job metadata does not establish generated content quality or approval.')
    return p.finish()
