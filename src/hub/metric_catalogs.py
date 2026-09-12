"""Bounded metadata-only catalogs. Never initialize project storage or workflows."""
from pathlib import Path
import os
import re

from hub.connection_records import content_hash, require, timestamp
from hub.metric_sources import metadata_path, read_json
from hub.metrics import issue, metric

MAX_FILES = 5000
MAX_BYTES = 32 * 1024 * 1024
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}\Z")
STUDIO = {k: k + 's' for k in ('project', 'story', 'episode', 'scene', 'shot', 'task', 'asset', 'review', 'caption', 'job', 'preparation_package', 'execution_candidate')}
STUDIO['story'] = 'stories'

# Authoritative enums: pixel_asset/animation_asset, pixel_review/review_record,
# studio_asset/studio_caption/studio_review/studio_real_image_run schemas.
PIXEL_ORIGINS = ('mock', 'real_api', 'imported', 'manual', 'placeholder')
PIXEL_LIFECYCLE = ('generated_raw', 'pending_review', 'under_review', 'approved', 'rejected',
                   'retry_needed', 'trashed', 'permanently_deleted', 'fixed_asset', 'archived')
PIXEL_REVIEW = ('pending', 'under_review', 'approved', 'rejected', 'retry_needed')
ANIME_RUN_STATUS = ('prepared', 'quoted', 'confirmed', 'send_claimed', 'definitive_failure',
                    'reconciliation_required', 'pending_review', 'target_invalidated', 'cancelled_due_batch_failure')


def _legal_time(value):
    try:
        timestamp(value, 'business_time')
        return value
    except (ValueError, TypeError, AttributeError):
        return None


def _identity(records, key):
    """Only IDs, positive numeric revisions and valid dates enter fingerprints."""
    return sorted([[r[key]] + [r.get(k) if type(r.get(k)) is int and r[k] >= 1 else None
                   for k in ('version', 'asset_version', 'revision')]
                   + [_legal_time(r.get(k)) for k in ('created_at', 'updated_at', 'reviewed_at')]
                   for r in records], key=lambda row: row[0])


def _id(value):
    return isinstance(value, str) and bool(ID.fullmatch(value))


class Catalog:
    def __init__(self, base, pid, observed, prefix):
        self.base, self.pid, self.observed, self.prefix = Path(base), pid, observed, prefix
        self.metrics, self.issues = [], []
        self.files = self.bytes = 0

    def problem(self, code, ref):
        problem = issue(self.pid, self.prefix + '_' + code, str(self.base / ref),
            recovery_condition='Repair the selected authoritative metadata and recollect read-only.')
        if not any(i['issue_id'] == problem['issue_id'] for i in self.issues):
            self.issues.append(problem)

    def emit(self, name, value, ref, dims=None, semantic=None, at=None, unit='records', reason=None):
        reason = reason or ('Required metadata is missing or invalid.' if value is None else None)
        self.metrics.append(metric(self.pid, self.prefix + '.' + name, value, unit,
            str(self.base / ref), content_hash([name, dims, value, semantic, at, reason]), self.observed,
            dimensions=dims, business_at=at, reason=reason,
            counting_basis='Unique current metadata identities in selected collection; statuses are metadata facts, not workflow execution.'))

    def scan(self, relative, key, missing_empty=False, bind_filename=False):
        try:
            require(not self.base.is_symlink(), 'symlink root')
            folder = metadata_path(self.base, relative)
            if not folder.exists() and missing_empty:
                return []
            require(folder.is_dir(), 'collection missing')
            paths = []
            with os.scandir(folder) as entries:
                for entry in entries:
                    require(not entry.is_symlink(), 'symlink entry')
                    require(entry.is_file(follow_symlinks=False), 'unexpected collection directory')
                    self.files += 1
                    require(self.files <= MAX_FILES, 'file budget')
                    if entry.name.endswith('.json'):
                        paths.append(entry.name)
            records, seen = [], set()
            for name in sorted(paths):
                path = relative + '/' + name
                self.bytes += metadata_path(self.base, path).stat().st_size
                require(self.bytes <= MAX_BYTES, 'byte budget')
                value, _ = read_json(self.base, path)
                require(isinstance(value, dict) and _id(value.get(key)), 'invalid identity')
                require(not bind_filename or value[key] == Path(name).stem, 'filename identity mismatch')
                require(value[key] not in seen, 'duplicate identity')
                seen.add(value[key])
                records.append(value)
            return records
        except (OSError, ValueError, TypeError):
            self.problem('collection_invalid', relative)
            return None

    def time(self, records, field, ref):
        values = []
        for r in records:
            value = r.get(field)
            try:
                timestamp(value, field)
                values.append(value)
            except (ValueError, TypeError, AttributeError):
                self.problem('business_time_unknown', ref)
                return None
        # Compare actual instants, not ISO strings with differing offsets.
        from datetime import datetime
        return max(values, key=lambda v: datetime.fromisoformat(v.replace('Z', '+00:00'))) if values else None

    def groups(self, records, field, allowed, ref, dims, key, time_field="updated_at"):
        if records is None:
            self.emit(field + '_count', None, ref, dims)
            return
        for status in (*allowed, 'unknown'):
            selected = [r for r in records if
                (r.get(field) not in allowed if status == 'unknown' else r.get(field) == status)]
            self.emit(field + '_count', len(selected), ref, dict(dims, **{field: status}),
                      _identity(selected, key), self.time(selected, time_field, ref))
        if any(r.get(field) not in allowed for r in records):
            self.problem(field + '_unknown', ref)

    def finish(self):
        return dict(metrics=self.metrics, issues=self.issues,
            disposition='partial' if self.issues or any(m['value'] is None for m in self.metrics) else 'resolved',
            source_version=content_hash([m['source_version'] for m in self.metrics]))


def collect_pixel(root, project_id, observed_at, spec):
    c = Catalog(spec.get('metadata_root', root), project_id, observed_at, 'pixel')
    assets = c.scan('metadata/assets', 'asset_id')
    ref = 'metadata/assets'
    dims = {'scope': 'current_assets'}
    c.emit('assets_total', len(assets) if assets is not None else None, ref, dims,
           sorted(r['asset_id'] for r in assets) if assets is not None else None,
           c.time(assets, 'updated_at', ref) if assets is not None else None)
    origins = PIXEL_ORIGINS
    c.groups(assets, 'data_origin', origins, ref, dims, 'asset_id')
    for origin in (*origins, 'unknown'):
        selected = None if assets is None else [r for r in assets if
            (r.get('data_origin') not in origins if origin == 'unknown' else r.get('data_origin') == origin)]
        od = dict(dims, data_origin=origin)
        c.groups(selected, 'lifecycle_status', PIXEL_LIFECYCLE, ref, od, 'asset_id')
        c.groups(selected, 'review_status', PIXEL_REVIEW, ref, od, 'asset_id')
    versions = [r.get('asset_version') for r in assets] if assets is not None else []
    known = assets is not None and all(type(v) is int and v >= 1 for v in versions)
    c.emit('asset_version_max', max(versions) if known and versions else None, ref, dims, unit='revision')
    if not known:
        c.problem('asset_version_unknown', ref)
    reviews = c.scan('metadata/reviews', 'review_id')
    ref = 'metadata/reviews'
    c.emit('reviews_total', len(reviews) if reviews is not None else None, ref,
           semantic=sorted(r['review_id'] for r in reviews) if reviews is not None else None,
           at=c.time(reviews, 'reviewed_at', ref) if reviews is not None else None)
    # Legacy review_status is kept separate from the explicit decision field.
    c.groups(reviews, 'decision', ('approved', 'rejected', 'retry_needed'), ref, {}, 'review_id', time_field='reviewed_at')
    c.groups(reviews, 'review_status', PIXEL_REVIEW, ref, {'scope': 'legacy_review_status'}, 'review_id', time_field='reviewed_at')
    if reviews is not None and assets is not None:
        ids = {r['asset_id'] for r in assets}
        orphans = [r['review_id'] for r in reviews if r.get('asset_id') not in ids]
        c.emit('orphan_reviews', len(orphans), ref, semantic=sorted(orphans))
        if orphans:
            c.problem('orphan_review_reference', ref)
    else:
        c.emit('orphan_reviews', None, ref)
    c.emit('redo_scope_known', sum(isinstance(r.get('redo_scope'), str) and bool(r['redo_scope']) for r in reviews)
           if reviews is not None else None, ref)
    if reviews is not None and any(not r.get('redo_scope') for r in reviews):
        c.problem('redo_scope_unknown', ref)
    for collection, key in [('asset_requests', 'request_id'), ('generation_runs', 'run_id'), ('frame_units', 'frame_unit_id'),
                            ('animation_clips', 'clip_id'), ('spritesheet_manifests', 'manifest_id'), ('export_manifests', 'manifest_id')]:
        ref = 'metadata/' + collection
        records = c.scan(ref, key)
        c.emit(collection + '_total', len(records) if records is not None else None, ref,
               semantic=sorted(r[key] for r in records) if records is not None else None)
    c.emit('backlog_oldest_age', None, 'metadata/assets', unit='seconds',
           reason='Created or reviewed timestamps do not establish a continuous current backlog start.')
    return c.finish()


def collect_anime(root, project_id, observed_at, spec):
    c = Catalog(spec.get('metadata_root', root), project_id, observed_at, 'anime')
    prefix = '' if 'metadata_root' in spec else 'runtime_assets/metadata/'
    studio = prefix + 'studio'
    # An absent collection means empty only with this known schema and existing Studio root.
    schema_ok = False
    try:
        schema, _ = read_json(root, 'schemas/studio_record.schema.json')
        types = schema['properties']['record_type']['enum']
        schema_ok = all(t in types for t in STUDIO) and metadata_path(c.base, studio).is_dir()
    except (OSError, ValueError, TypeError, KeyError):
        c.problem('studio_schema_unknown', 'schemas/studio_record.schema.json')
    projects = c.scan(studio + '/projects', 'record_id', bind_filename=True)
    if projects is not None and not all(r.get('record_type') == 'project' and r.get('project_id') == r['record_id']
            and isinstance(r.get('data'), dict) and r['data'].get('project_id') == r['record_id'] for r in projects):
        projects = None
        c.problem('project_identity_invalid', studio + '/projects')
    project_ids = {r['record_id'] for r in projects} if projects is not None else set()
    for kind, collection in STUDIO.items():
        ref = studio + '/' + collection
        records = projects if kind == 'project' else c.scan(ref, 'record_id', missing_empty=schema_ok and projects is not None, bind_filename=True)
        valid = records is not None and projects is not None and all(
            r.get('record_type') == kind and r.get('project_id') in project_ids and
            isinstance(r.get('data'), dict) and r['data'].get(kind + '_id') == r['record_id'] and
            ('project_id' not in r['data'] or r['data']['project_id'] == r['project_id']) for r in records)
        if not valid:
            c.problem('record_identity_invalid', ref)
            c.emit('collection_total', None, ref, {'scope': 'current_studio', 'collection': collection})
            continue
        for source_pid in sorted(project_ids):
            selected = [r for r in records if r['project_id'] == source_pid]
            dims = {'scope': 'current_studio', 'collection': collection, 'source_project_id': source_pid}
            identity = _identity(selected, 'record_id')
            at = c.time(selected, 'updated_at', ref)
            c.emit('collection_total', len(selected), ref, dims, identity, at)
            data = [dict(r['data'], record_id=r['record_id'], revision=r.get('revision'),
                         created_at=r.get('created_at'), updated_at=r.get('updated_at')) for r in selected]
            if kind in ('project', 'story', 'episode', 'scene', 'shot'):
                valid_archive = all(type(r.get('archived')) is bool for r in data)
                c.emit('nonarchived_total', sum(not r['archived'] for r in data) if valid_archive else None, ref, dims, identity, at)
            revisions = [r.get('revision') for r in selected]
            known = all(type(v) is int and v >= 1 for v in revisions)
            c.emit('record_revision_max', max(revisions) if known and revisions else None, ref, dims, at=at, unit='revision')
            if kind == 'asset':
                # Total records above includes tombstones; active facts do not.
                valid_deleted = all(type(r.get('deleted')) is bool for r in data)
                data = [r for r in data if not r['deleted']] if valid_deleted else None
                dims = dict(dims, scope='active_studio_assets')
                c.emit('active_assets', len(data) if data is not None else None, ref, dims,
                       _identity(data, 'record_id') if data is not None else None,
                       c.time(data, 'updated_at', ref) if data is not None else None)
                if not valid_deleted:
                    c.problem('asset_deleted_unknown', ref)
            if kind in ('asset', 'caption'):
                statuses = ('pending_review', 'approved', 'rejected') + (('trashed',) if kind == 'asset' else ())
                c.groups(data, 'status', statuses, ref, dims, 'record_id')
                versions = [r.get('version') for r in data] if data is not None else []
                c.emit('content_version_max', max(versions) if versions and all(type(v) is int and v >= 1 for v in versions) else None,
                       ref, dims, semantic=_identity(data, 'record_id') if data is not None else None,
                       at=c.time(data, 'updated_at', ref) if data is not None else None, unit='revision')
            if kind == 'asset':
                c.groups(data, 'data_origin', ('mock', 'imported', 'manual', 'user_upload', 'provider_generated'), ref, dims, 'record_id')
                primary = [r for r in data if r.get('primary') is True] if data is not None else None
                c.emit('primary_assets', len(primary) if data is not None and all(type(r.get('primary')) is bool for r in data) else None,
                       ref, dims, _identity(primary, 'record_id') if primary is not None else None,
                       c.time(primary, 'updated_at', ref) if primary is not None else None)
            if kind == 'review':
                c.groups(data, 'decision', ('approve', 'reject', 'needs_correction'), ref, dims, 'record_id')
    ref = prefix + 'real_image/runs'
    runs = c.scan(ref, 'run_id')
    if runs is not None and (projects is None or any(r.get('project_id') not in project_ids for r in runs)):
        c.problem('run_project_identity_invalid', ref)
        runs = None
    for source_pid in sorted(project_ids) if project_ids else [None]:
        selected = [r for r in runs if r['project_id'] == source_pid] if runs is not None else None
        dims = {'scope': 'supervised_real_image'}
        if source_pid is not None:
            dims['source_project_id'] = source_pid
        c.emit('supervised_runs_total', len(selected) if selected is not None else None, ref,
               dims, _identity(selected, 'run_id') if selected is not None else None,
               c.time(selected, 'created_at', ref) if selected is not None else None)
        # created_at proves creation, not when the current run status began.
        c.groups(selected, 'status', ANIME_RUN_STATUS, ref, dims, 'run_id')
        c.emit('reconciliation_required', sum(r['reconciliation_required'] for r in selected)
               if selected is not None and all(type(r.get('reconciliation_required')) is bool for r in selected) else None,
               ref, dims, _identity(selected, 'run_id') if selected is not None else None)
    c.emit('legacy_coverage', None, prefix + 'generation_runs', {'scope': 'legacy_excluded'},
           reason='Historical generation_runs and image_tasks are outside current Studio scope; legacy files were not scanned.')
    c.emit('blocker_oldest_age', None, studio, unit='seconds',
           reason='Current timestamps do not establish a continuous blocker start.')
    return c.finish()
